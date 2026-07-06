"""
WorkflowRunner — 执行工作流模板，实时 SSE 进度推送 (v2.0)

设计原则：
  - 零大模型：默认直接执行工具链，不调用 LLM
  - 实时进度：每步执行前后都 publish SSE，前端可实时显示进度条
  - 变量注入：执行前将用户提供的变量值替换到 args_template 中的 {var} 槽位
  - 完全隔离：不修改任何现有 Agent / ReplayEngine，独立落库 workflow_runs 表

SSE 事件序列：
  workflow.start     → { run_id, template_id, total_steps, variables }
  workflow.step      → { run_id, step_idx, tool_name, status: running, total_steps }
  workflow.step      → { run_id, step_idx, tool_name, status: ok|error, result_preview, duration_ms }
  workflow.complete  → { run_id, status, total_steps, duration_ms, result }
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Awaitable

_logger = logging.getLogger("ep_agent.workflow")

Publisher = Callable[[str, dict], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started: str, ended: str) -> int:
    try:
        s = datetime.fromisoformat(started)
        e = datetime.fromisoformat(ended)
        return max(0, int((e - s).total_seconds() * 1000))
    except Exception:
        return 0


def _resolve_args(args_template: dict, variables: dict) -> dict:
    """
    将 args_template 中的 {var} 槽位替换为 variables 中的实际值。
    支持嵌套 dict / list / str。
    """
    def _replace(obj):
        if isinstance(obj, str):
            def sub(m):
                key = m.group(1)
                return str(variables.get(key, m.group(0)))
            return re.sub(r'\{(\w+)\}', sub, obj)
        if isinstance(obj, dict):
            return {k: _replace(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_replace(i) for i in obj]
        return obj
    return _replace(args_template)


async def _noop_publish(evt_type: str, payload: dict):
    pass


# ── WorkflowRunner ───────────────────────────────────────────────────────────

class WorkflowRunner:
    """
    工作流执行引擎。

    用法：
        runner = WorkflowRunner()
        result = await runner.run(
            template_id="wf_xxx",
            session_id="sess_xxx",
            variables={"ref_audio": "furina.wav", "text": "你好世界"},
            publish=publish_fn,
        )
    """

    async def run(
        self,
        template_id: str,
        session_id: str,
        variables: dict | None = None,
        publish: Publisher | None = None,
        dry_run: bool = False,
    ) -> dict:
        from app.pipeline import db as _db
        from app.pipeline.domain import new_id
        from app.agentcore.tools import call_tool as _call_tool

        _pub = publish or _noop_publish
        _vars = variables or {}

        # 1. 加载模板
        template = _db.get_workflow_template(template_id)
        if not template:
            return {"error": f"workflow template {template_id} not found"}

        steps_raw = template.get("steps", "[]")
        try:
            steps = json.loads(steps_raw) if isinstance(steps_raw, str) else steps_raw
        except Exception:
            return {"error": "模板 steps 解析失败"}

        total = len(steps)
        now   = _now_iso()
        run_id = new_id("wfrun")

        # 2. 创建 run 记录
        _db.insert_workflow_run({
            "run_id":      run_id,
            "template_id": template_id,
            "session_id":  session_id,
            "variables":   json.dumps(_vars, ensure_ascii=False),
            "status":      "running",
            "current_step": 0,
            "total_steps": total,
            "started_at":  now,
        })

        # 3. 推送 workflow.start
        await _pub("workflow.start", {
            "run_id":       run_id,
            "template_id":  template_id,
            "name":         template.get("name", ""),
            "total_steps":  total,
            "variables":    _vars,
        })

        run_status  = "succeeded"
        final_result: dict = {}
        step_results: list[dict] = []
        all_errors: list[str] = []

        for step in steps:
            step_idx  = step.get("step_idx", 0)
            tool_name = step.get("tool_name", "")
            args_tpl  = step.get("args_template", {})
            if isinstance(args_tpl, str):
                try:
                    args_tpl = json.loads(args_tpl)
                except Exception:
                    args_tpl = {}

            # 变量替换
            args_resolved = _resolve_args(args_tpl, _vars)

            step_started = _now_iso()
            log_id = new_id("wflog")

            # 推送 step running
            await _pub("workflow.step", {
                "run_id":      run_id,
                "step_idx":    step_idx,
                "tool_name":   tool_name,
                "status":      "running",
                "total_steps": total,
                "args":        args_resolved,
            })

            # 更新 run 当前步骤
            _db.update_workflow_run(run_id, {"current_step": step_idx, "status": "running"})

            # 跳过无意义的固定节点
            SKIP_TOOLS = {"finish_task", "health_check", "sovits_health_check"}

            # 执行工具
            step_status = "ok"
            result_str  = ""
            error_msg   = ""
            if tool_name in SKIP_TOOLS:
                result_str = f"[skipped: {tool_name}]"
            elif dry_run:
                dry_result = {"dry_run": True, "tool": tool_name, "args": args_resolved}
                result_str = json.dumps(dry_result, ensure_ascii=False)
            else:
                try:
                    raw_result = await _call_tool(tool_name, args_resolved)
                    result_str = raw_result if isinstance(raw_result, str) else json.dumps(raw_result, ensure_ascii=False)
                    # 尝试解析 result 为 dict，供后续步骤引用
                    try:
                        parsed = json.loads(result_str)
                        if isinstance(parsed, dict):
                            # 将工具结果的顶层 key 注入变量（供后续步骤引用）
                            _vars.update({f"_step{step_idx}_{k}": str(v) for k, v in parsed.items()})
                            final_result = parsed
                    except Exception:
                        pass
                except Exception as e:
                    step_status = "error"
                    error_msg   = str(e)
                    run_status  = "failed"
                    _logger.warning("[WorkflowRunner] step %d %s 失败: %s", step_idx, tool_name, e)

            step_ended   = _now_iso()
            step_dur     = _duration_ms(step_started, step_ended)
            result_preview = result_str[:200] if result_str else error_msg[:200]

            # 落库步骤日志
            _db.insert_workflow_step_log({
                "log_id":       log_id,
                "run_id":       run_id,
                "step_idx":     step_idx,
                "tool_name":    tool_name,
                "args_resolved": json.dumps(args_resolved, ensure_ascii=False),
                "result":       result_str[:4096],
                "status":       step_status,
                "duration_ms":  step_dur,
                "started_at":   step_started,
                "ended_at":     step_ended,
            })

            step_results.append({
                "step_idx":      step_idx,
                "tool_name":     tool_name,
                "status":        step_status,
                "result_preview": result_preview,
                "duration_ms":   step_dur,
            })

            # 推送 step 完成
            await _pub("workflow.step", {
                "run_id":        run_id,
                "step_idx":      step_idx,
                "tool_name":     tool_name,
                "status":        step_status,
                "result_preview": result_preview,
                "duration_ms":   step_dur,
                "total_steps":   total,
            })

            # 收集错误信息
            if step_status == "error" and error_msg:
                all_errors.append(f"step{step_idx}({tool_name}): {error_msg}")

            # 失败时中止
            if step_status == "error":
                break

        # 4. 更新 run 状态
        ended_at = _now_iso()
        total_dur = _duration_ms(now, ended_at)
        run_update: dict = {
            "status":      run_status,
            "current_step": total,
            "result":      json.dumps(final_result, ensure_ascii=False),
            "ended_at":    ended_at,
            "duration_ms": total_dur,
        }
        if all_errors:
            run_update["error_msg"] = "; ".join(all_errors)
        _db.update_workflow_run(run_id, run_update)

        # 5. 推送 workflow.complete
        await _pub("workflow.complete", {
            "run_id":      run_id,
            "status":      run_status,
            "total_steps": total,
            "duration_ms": total_dur,
            "steps":       step_results,
            "result":      final_result,
        })

        _logger.info(
            "[WorkflowRunner] 执行完成 run_id=%s template=%s status=%s steps=%d dur=%dms",
            run_id, template_id, run_status, total, total_dur,
        )

        return {
            "run_id":      run_id,
            "template_id": template_id,
            "status":      run_status,
            "total_steps": total,
            "duration_ms": total_dur,
            "steps":       step_results,
            "result":      final_result,
        }
