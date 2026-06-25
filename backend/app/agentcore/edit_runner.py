"""
ABC Edit Runner — 纯 ABC 编辑逻辑层（v3.1 整合版）

架构变更（v3.1）：
  v3.0 问题：DirectEditRunner 内嵌独立 ReAct Loop，绕过了 TodoManager 和 finish_gate。
  v3.1 修复：
    - 移除独立 ReAct Loop，改用 ReactExecutor（统一 ReAct 执行器）
    - EditAgent（agents/edit_agent.py）在外层统一管理 TODO 状态
    - 此文件只保留 ABC 编辑专用逻辑：
        * _build_system_prompt()：热加载 score-editor.agent
        * _build_user_prompt()：构造用户消息（注入谱子上下文）
        * run_edit()：对外暴露的唯一接口，返回 {abc, summary, ...}

职责（单一）：
  - 构造 ABC 编辑专用 system/user prompt
  - 调用 ReactExecutor 执行 ReAct Loop
  - 提取 ABC 和 SUMMARY（委托给 abc_utils）
  - OutputAdapter：按 scene 追加格式转换（sky_json / midi_url）

不负责：
  - TODO 状态管理（由 EditAgent 负责）
  - session 读写（由 EditAgent 负责）
  - finish_gate 检查（由 EditAgent 负责）
"""
from __future__ import annotations

from typing import Callable, Awaitable, Literal

from app.agentcore.agent_loader import load_agent_prompt
from app.agentcore.abc_utils import extract_abc_and_summary
from app.agentcore.react_executor import ReactExecutor
from app.agentcore.todo_manager import TodoManager
from app.agentcore.tools import get_tool_schemas, call_tool
from app.pipeline.domain import ScoreMeta

Publisher = Callable[[str, dict], Awaitable[None]]
Scene = Literal["editor", "player", "daw", "raw"]

MAX_EDIT_ROUNDS = 5  # edit 域最多 5 轮 ReAct
                     # 简单修改(1轮) / 分析+修改(2轮) / 转调+验证+修正(3轮) / 复杂风格改编(4-5轮)

# ── tools/ 扫描已由 universal_runner.py 在模块加载时统一完成，此处无需重复扫描 ──
# （edit_runner 始终由 EditAgent 调用，而 EditAgent 由 universal_runner 调度，
#   此时 tools/ 已全部注册到 sys.modules，重复扫描是冗余 IO）

# ── ReAct 补充 Prompt ─────────────────────────────────────────────────────────

_REACT_SUPPLEMENT = """
## ReAct 工作模式补充

你在 ReAct 循环中工作（最多 5 轮）：
1. **Thought**：理解意图，思考修改策略（乐理分析）
2. **Action（可选）**：必要时调用 analyze_abc 分析结构，或 validate_abc 验证范围
3. **Observation**：查看工具结果，确认修改方向
4. **Output（必须）**：无论是否调用工具，最终都必须输出完整 ABC + SUMMARY

可用工具：
- `analyze_abc`：分析 ABC 结构（调号/速度/音符数/音域）→ 复杂意图时先分析
- `validate_abc`：验证是否在 Sky C4-C6 范围内 → 转调后验证

**简单修改（改速度/简单转调）可直接输出，无需调工具。**

## 🔴 工具调用后的铁律

调用工具获得 Observation 后，你 **必须** 继续输出修改后的完整 ABC 谱子。
绝对禁止：工具调用完成后只输出文字总结而不输出 ABC。
绝对禁止：输出「已完成」「修改如下」等文字而不附上完整 ABC。

正确流程示例：
  → 调用 analyze_abc → 看到分析结果 → 输出完整修改后 ABC + SUMMARY
  → 调用 validate_abc → 看到验证结果 → 输出完整修改后 ABC + SUMMARY
"""


def _build_system_prompt() -> str:
    """从 score-editor.agent 热加载 system prompt（支持热更新）。
    加载 role + static_context + workflow，让 LLM 感知完整工作流（含 MIDI 导出指引）。
    """
    try:
        base = load_agent_prompt(
            "score-editor",
            sections=["role", "static_context", "workflow", "output_contract"],
        )
        return base + _REACT_SUPPLEMENT
    except FileNotFoundError:
        return (
            "你是精通 ABC Notation 的音乐编辑助手，服务于 Sky: Children of the Light。\n"
            "直接输出修改后的完整 ABC + SUMMARY 行，不输出 JSON 或代码块。"
            + _REACT_SUPPLEMENT
        )


def _build_user_prompt(
    intent: str,
    meta: ScoreMeta,
    current_abc: str,
    context_summary: str = "",
    workspace_id: str = "",
) -> str:
    """构造编辑任务的用户消息（注入谱子上下文）。"""
    parts = [
        f"用户意图：{intent}",
        f"谱子信息：标题={meta.title}，调号={meta.key}，"
        f"BPM={meta.bpm:.0f}，拍号={meta.time_sig_num}/{meta.time_sig_den}，"
        f"音符数={meta.note_count}",
    ]
    if workspace_id:
        parts.append(f"工作区 ID：{workspace_id}（调用 abc_to_midi 时请传入此值）")
    if context_summary:
        parts.append(f"历史上下文：{context_summary}")
    parts.append(f"\n当前 ABC 谱：\n{current_abc}")
    return "\n".join(parts)


async def run_edit(
    current_abc: str,
    intent: str,
    meta: ScoreMeta,
    context_summary: str,
    publish: Publisher,
    todo_mgr: TodoManager,
    scene: Scene = "editor",
    session_id: str = "",   # 传入后 ReactExecutor 自动落库 tool message
    workspace_id: str = "", # 注入到 user prompt，供 abc_to_midi 等工具使用
) -> dict:
    """
    执行 ABC 编辑（v3.1：使用 ReactExecutor，不再内嵌 ReAct Loop）。

    参数：
      current_abc    — 当前 ABC 谱子
      intent         — 用户编辑意图
      meta           — 谱子元数据
      context_summary — 历史操作摘要
      publish        — 事件推送函数
      todo_mgr       — 外层 TodoManager（由 EditAgent 传入，统一管理 TODO）
      scene          — 输出场景（editor/player/daw/raw）

    返回：
      {
        "abc":          str,       # 修改后的 ABC
        "summary":      str,       # 中文摘要
        "tool_calls":   [...],     # 所有工具调用记录
        "sky_json":     str|None,
        "midi_url":     str|None,
        "react_rounds": int,
      }
    """
    await publish("pipeline.step", {
        "step":   "edit_start",
        "status": "running",
        "text":   f"正在理解意图：{intent}",
    })

    # ── 构造 messages ─────────────────────────────────────────────────────────
    tools = get_tool_schemas("abc_edit") + get_tool_schemas("output")
    # 同时注册 analyze_abc 工具（复杂编辑时 LLM 可选择先分析）
    try:
        analyze_tools = get_tool_schemas("analyze")
        tools = analyze_tools + tools
    except Exception:
        pass  # analyze 工具组不存在时不影响主流程
    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user",   "content": _build_user_prompt(
            intent, meta, current_abc, context_summary, workspace_id
        )},
    ]

    await publish("pipeline.step", {
        "step":   "react_loop",
        "status": "running",
        "text":   "ReAct 编辑循环启动...",
    })

    # ── 委托 ReactExecutor 执行 ReAct Loop ───────────────────────────────────
    # todo_mgr 由外层 EditAgent 传入，ReactExecutor 负责 complete_one 纪律
    # 创作型意图（重写/改编/风格转换/时长扩展）用更高 temperature 激发创意
    # 参数型意图（转调/变速）保持低 temperature 确保准确性
    # 创作/风格改编类意图用高 temperature 激发创意；参数型修改用低 temperature 确保精确
    _CREATIVE_INTENTS = ["重写", "改编", "风格", "流行", "爵士", "中国风",
                         "古典", "延长", "分钟", "扩展", "新旋律", "创作",
                         "加花", "华丽", "丰富", "装饰", "变奏"]
    temperature = 0.82 if any(kw in intent for kw in _CREATIVE_INTENTS) else 0.25

    exec_result = await ReactExecutor().run(
        messages=messages,
        tools=tools,
        publish=publish,
        todo_manager=todo_mgr,
        max_rounds=MAX_EDIT_ROUNDS,
        temperature=temperature,
        session_id=session_id,  # 落库 tool message
    )

    raw = exec_result.get("content", "")
    react_rounds = exec_result.get("rounds", 1)
    sky_json = exec_result.get("extra", {}).get("sky_json")
    midi_url = exec_result.get("extra", {}).get("midi_url")

    # ── 提取 ABC + SUMMARY ───────────────────────────────────────────────────
    _FALLBACK = current_abc
    new_abc, summary = extract_abc_and_summary(raw, _FALLBACK)

    await publish("pipeline.step", {
        "step":   "react_loop",
        "status": "succeeded",
        "text":   f"编辑完成（{react_rounds} 轮 ReAct）：{summary}",
    })

    # ── OutputAdapter：按 scene 追加格式转换（若 ReAct 中未调用）────────────
    if scene in ("player", "raw") and not sky_json:
        await publish("pipeline.step", {
            "step": "output_adapt", "status": "running",
            "text": "正在生成 Sky JSON...",
        })
        try:
            sky_json = await call_tool("abc_to_sky_json", {"abc": new_abc})
            await publish("pipeline.step", {
                "step": "output_adapt", "status": "succeeded",
                "text": "Sky JSON 生成完成",
            })
        except Exception as e:
            await publish("pipeline.step", {
                "step": "output_adapt", "status": "failed",
                "text": f"Sky JSON 生成失败: {e}",
            })

    if scene in ("daw", "raw") and not midi_url:
        await publish("pipeline.step", {
            "step": "output_adapt", "status": "running",
            "text": "正在生成 MIDI...",
        })
        try:
            result = await call_tool("abc_to_midi_file", {"abc": new_abc})
            if isinstance(result, dict):
                midi_url = result.get("midi_url")
            else:
                midi_url = result
            await publish("pipeline.step", {
                "step": "output_adapt", "status": "succeeded",
                "text": "MIDI 生成完成",
            })
        except Exception as e:
            await publish("pipeline.step", {
                "step": "output_adapt", "status": "failed",
                "text": f"MIDI 生成失败: {e}",
            })

    return {
        "abc":          new_abc,
        "summary":      summary,
        "tool_calls":   exec_result.get("tool_calls", []),
        "sky_json":     sky_json,
        "midi_url":     midi_url,
        "react_rounds": react_rounds,
    }


# ── 向后兼容：保留 edit_agent_runner 引用（service.py 可能直接调用）────────────
# 新代码请直接调用 run_edit()，此别名将在后续版本移除

class _LegacyEditRunner:
    """向后兼容包装器，将旧的 .run() 接口映射到新的 run_edit()。"""

    async def run(
        self,
        current_abc: str,
        intent: str,
        meta: ScoreMeta,
        context_summary: str,
        publish: Publisher,
        scene: Scene = "editor",
    ) -> dict:
        """兼容旧接口（无 todo_mgr 参数），创建临时 TodoManager。"""
        import logging
        logging.getLogger("ep_agent").warning(
            "[edit_runner] edit_agent_runner.run() 已废弃，请使用 run_edit() + EditAgent"
        )
        # 创建临时 TodoManager（兼容旧调用路径）
        tmp_todo_mgr = TodoManager()
        return await run_edit(
            current_abc=current_abc,
            intent=intent,
            meta=meta,
            context_summary=context_summary,
            publish=publish,
            todo_mgr=tmp_todo_mgr,
            scene=scene,
        )


edit_agent_runner = _LegacyEditRunner()
