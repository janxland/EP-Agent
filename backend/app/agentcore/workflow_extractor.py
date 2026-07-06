"""
WorkflowExtractor — 从 Trace 提炼确定性工作流模板 (v2.0)

设计原则：
  - 完全隔离：不修改任何现有 Agent / ReplayEngine / TraceCollector
  - LLM 一次性分析：只在"提炼"阶段调用一次 LLM，后续执行零 LLM
  - 大模型剪枝：识别哪些步骤参数可从用户输入直接提取，标记 llm_required=false
  - 变量槽位：将用户输入中的可变部分抽象为 {ref_audio} {text} 等变量

工作流步骤结构：
{
    "step_idx":      0,
    "type":          "tool",           # tool | llm_decision（未来扩展）
    "tool_name":     "sovits_list_audio_files",
    "args_template": {},               # 变量替换模板，{var} 为槽位
    "args_fixed":    {},               # 固定参数（不随用户输入变化）
    "llm_required":  false,            # 是否需要大模型参与
    "prune_reason":  "参数固定无需决策",  # 剪枝原因
    "result_preview": "...",           # 历史执行结果预览（供参考）
    "duration_ms":   120,             # 历史执行耗时
}
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

_logger = logging.getLogger("ep_agent.workflow")

# ── LLM 系统提示 ────────────────────────────────────────────────────────────

_EXTRACTOR_SYSTEM = """你是 EP-Agent 工作流分析专家。
你的任务是分析一次 Agent 执行的 Trace（工具调用序列），提炼出可复用的确定性工作流模板。

## 分析目标
1. 识别工作流的"变量槽位"：用户输入中哪些值会随请求变化（如音频文件名、合成文本）
2. 判断每个工具调用步骤是否需要大模型参与（llm_required）
3. 为工作流命名，提炼触发模式描述

## 判断 llm_required=false 的条件（满足任一即可剪枝）
- 工具参数完全固定（每次执行都一样）
- 工具参数可从用户输入直接提取（如文件名、文本内容）
- 工具是固定流程节点（如 finish_task、health_check、list_files）

## 输出格式（严格 JSON，不要 markdown）
{
  "name": "工作流名称（简短，如：音色克隆语音合成）",
  "description": "一句话描述工作流功能",
  "trigger_pattern": "触发模式描述（如：用{ref_audio}音色合成{text}语音）",
  "variables": [
    {"name": "ref_audio", "description": "参考音频文件名", "extract_from": "attachment或用户消息中的文件名"},
    {"name": "text", "description": "要合成的文本内容", "extract_from": "用户消息中的引号内容或明确文本"}
  ],
  "steps": [
    {
      "step_idx": 0,
      "tool_name": "sovits_list_audio_files",
      "args_template": {},
      "llm_required": false,
      "prune_reason": "固定查询步骤，无需决策"
    }
  ]
}"""


def _build_extractor_prompt(trace: dict, spans: list[dict], user_message: str) -> str:
    """构建给 LLM 的分析 prompt"""
    tool_spans = [s for s in spans if s.get("span_kind") == "tool" and s.get("status") == "ok"]

    steps_desc = []
    for i, span in enumerate(tool_spans):
        args_raw = span.get("tool_args", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception:
            args = {}
        steps_desc.append({
            "step_idx":      i,
            "tool_name":     span.get("tool_name", ""),
            "args":          args,
            "result_preview": span.get("tool_result_preview", ""),
            "duration_ms":   span.get("duration_ms", 0),
            "status":        span.get("status", ""),
        })

    return f"""## 用户原始请求
{user_message}

## 工具调用序列（共 {len(tool_spans)} 步）
{json.dumps(steps_desc, ensure_ascii=False, indent=2)}

## 请分析并输出工作流模板 JSON："""


async def _call_llm_extract(prompt: str) -> dict:
    """调用 LLM 提炼工作流（使用轻量模型，节省 token）"""
    from app.agentcore.llm import call_llm_raw
    try:
        response = await call_llm_raw(
            messages=[
                {"role": "system", "content": _EXTRACTOR_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            model="auto",
            temperature=0.1,
            max_tokens=2000,
        )
        content = response.get("content", "")
        # 提取 JSON（去掉可能的 markdown 包裹）
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
        return {}
    except Exception as e:
        _logger.warning("[WorkflowExtractor] LLM 调用失败: %s", e)
        return {}


def _fallback_extract(trace: dict, spans: list[dict], user_message: str) -> dict:
    """
    LLM 不可用时的规则兜底提炼：
    - 所有 tool span 直接转为步骤
    - 参数中包含用户消息片段的标记为变量
    - finish_task / health_check / list_* 标记为 llm_required=false
    """
    NO_LLM_TOOLS = {
        "finish_task", "sovits_health_check", "sovits_list_audio_files",
        "sovits_list_models", "list_workspace_files", "list_h5_templates",
        "list_cloned_voices", "get_suno_job_status",
        "health_check", "get_session_info", "list_sessions",
        "abc_to_sky_json", "abc_to_midi_b64", "get_abc_header",
    }

    tool_spans = [s for s in spans if s.get("span_kind") == "tool" and s.get("status") == "ok"]
    steps = []
    for i, span in enumerate(tool_spans):
        tool = span.get("tool_name", "")
        args_raw = span.get("tool_args", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception:
            args = {}

        llm_required = tool not in NO_LLM_TOOLS
        prune_reason = "固定流程节点" if not llm_required else "需要决策"

        steps.append({
            "step_idx":      i,
            "tool_name":     tool,
            "args_template": args,
            "args_fixed":    {},
            "llm_required":  llm_required,
            "prune_reason":  prune_reason,
            "result_preview": span.get("tool_result_preview", ""),
            "duration_ms":   span.get("duration_ms", 0),
        })

    domain = trace.get("domain", "unknown")
    return {
        "name":            f"{domain} 工作流",
        "description":     f"从 trace {trace.get('trace_id', '')[:8]} 自动提炼",
        "trigger_pattern": user_message[:80],
        "variables":       [],
        "steps":           steps,
    }


def _merge_llm_result(llm_result: dict, fallback: dict, spans: list[dict]) -> dict:
    """
    合并 LLM 分析结果与规则兜底结果：
    - LLM 提供 name/description/trigger_pattern/variables/llm_required 判断
    - 规则兜底提供 result_preview/duration_ms 等运行时数据
    """
    tool_spans = [s for s in spans if s.get("span_kind") == "tool" and s.get("status") == "ok"]
    llm_steps  = {s.get("step_idx", i): s for i, s in enumerate(llm_result.get("steps", []))}
    fb_steps   = {s["step_idx"]: s for s in fallback["steps"]}

    merged_steps = []
    for idx, fb_step in fb_steps.items():
        llm_step = llm_steps.get(idx, {})
        span     = tool_spans[idx] if idx < len(tool_spans) else {}

        args_raw = span.get("tool_args", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception:
            args = {}

        merged_steps.append({
            "step_idx":      idx,
            "tool_name":     fb_step["tool_name"],
            "args_template": llm_step.get("args_template", args),
            "args_fixed":    llm_step.get("args_fixed", {}),
            "llm_required":  llm_step.get("llm_required", fb_step["llm_required"]),
            "prune_reason":  llm_step.get("prune_reason", fb_step["prune_reason"]),
            "result_preview": span.get("tool_result_preview", ""),
            "duration_ms":   span.get("duration_ms", 0),
        })

    return {
        "name":            llm_result.get("name")            or fallback["name"],
        "description":     llm_result.get("description")     or fallback["description"],
        "trigger_pattern": llm_result.get("trigger_pattern") or fallback["trigger_pattern"],
        "variables":       llm_result.get("variables")       or fallback["variables"],
        "steps":           merged_steps,
    }


# ── WorkflowExtractor ────────────────────────────────────────────────────────

class WorkflowExtractor:
    """
    从一条 Trace 提炼出工作流模板，持久化到 workflow_templates 表。

    用法：
        extractor = WorkflowExtractor()
        template = await extractor.extract(trace_id="trace_xxx", use_llm=True)
    """

    async def extract(
        self,
        trace_id: str,
        use_llm: bool = True,
    ) -> dict:
        """
        提炼工作流模板。
        返回：{ template_id, name, steps, variables, llm_steps, pruned_steps, ... }
        """
        from app.pipeline import db as _db
        from app.pipeline.domain import new_id

        # 1. 加载 trace + spans
        trace = _db.get_trace(trace_id)
        if not trace:
            return {"error": f"trace {trace_id} not found"}

        spans = _db.get_spans_by_trace(trace_id)
        tool_spans = [s for s in spans if s.get("span_kind") == "tool" and s.get("status") == "ok"]
        if not tool_spans:
            return {"error": "trace 中没有成功的工具调用，无法提炼工作流"}

        user_message = trace.get("user_message", "")

        # 2. 规则兜底（始终执行，作为 LLM 失败时的保底）
        fallback = _fallback_extract(trace, spans, user_message)

        # 3. LLM 分析（可选）
        llm_result = {}
        if use_llm:
            prompt = _build_extractor_prompt(trace, spans, user_message)
            llm_result = await _call_llm_extract(prompt)

        # 4. 合并结果
        merged = _merge_llm_result(llm_result, fallback, spans) if llm_result else fallback

        # 5. 统计 LLM 步骤数
        steps       = merged["steps"]
        llm_count   = sum(1 for s in steps if s.get("llm_required", True))
        pruned      = len(steps) - llm_count

        # 6. 落库
        now         = datetime.now(timezone.utc).isoformat()
        template_id = new_id("wf")
        _db.insert_workflow_template({
            "template_id":     template_id,
            "source_trace_id": trace_id,
            "name":            merged["name"],
            "description":     merged["description"],
            "domain":          trace.get("domain", ""),
            "trigger_pattern": merged["trigger_pattern"],
            "variables":       json.dumps(merged["variables"], ensure_ascii=False),
            "steps":           json.dumps(steps,               ensure_ascii=False),
            "total_steps":     len(steps),
            "llm_steps":       llm_count,
            "pruned_steps":    pruned,
            "status":          "ready",
            "created_at":      now,
            "updated_at":      now,
        })

        _logger.info(
            "[WorkflowExtractor] 提炼完成 template_id=%s name=%s steps=%d llm=%d pruned=%d",
            template_id, merged["name"], len(steps), llm_count, pruned,
        )

        return {
            "template_id":  template_id,
            "name":         merged["name"],
            "description":  merged["description"],
            "trigger_pattern": merged["trigger_pattern"],
            "variables":    merged["variables"],
            "steps":        steps,
            "total_steps":  len(steps),
            "llm_steps":    llm_count,
            "pruned_steps": pruned,
            "status":       "ready",
            "source_user_message": user_message[:100],
        }
