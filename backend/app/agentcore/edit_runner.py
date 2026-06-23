"""
Tool-Calling Agent Runner
真正的 Agent 架构：LLM 持有工具 schema，自主决定调用哪些工具、调用几次

流程：
  用户意图
    → LLM（携带 abc_edit 分组工具 schema）
    → 决定调用工具（如 transpose_abc, change_tempo）
    → 工具执行，结果回传给 LLM
    → LLM 决定是否继续调用或结束
    → 返回最终 ABC + 摘要

场景路由（OutputAdapter）：
  scene=editor  → 只返回修改后的 ABC
  scene=player  → Agent 完成后自动追加 abc_to_sky_json
  scene=daw     → Agent 完成后自动追加 abc_to_midi_b64
  scene=raw     → 同时返回 abc + sky_json + midi_b64

工具分组隔离：
  abc_edit 分组 → ABC 编辑 Agent 可见（转调/速度/风格/分析/导出）
  audio    分组 → 音频生成 Agent 可见（Suno/MiniMax，不暴露给编辑 Agent）
"""
from __future__ import annotations
import importlib
import json
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Awaitable, Literal

from app.agentcore.llm import complete_with_tools
from app.pipeline.domain import ScoreMeta
from app.agentcore.tools import get_tool_schemas, call_tool, get_tool_names

Publisher = Callable[[str, dict], Awaitable[None]]
Scene = Literal["editor", "player", "daw", "raw"]

MAX_TOOL_ROUNDS = 6  # 最多循环调用工具次数，防止死循环

# ─── 自动扫描并导入 tools/ 目录下所有模块（触发 @tool 注册）────────────────
_tools_pkg = "app.agentcore.tools"
_tools_dir = Path(__file__).parent / "tools"

for _mod_info in pkgutil.iter_modules([str(_tools_dir)]):
    _full_name = f"{_tools_pkg}.{_mod_info.name}"
    if _full_name not in sys.modules:
        importlib.import_module(_full_name)
# ─────────────────────────────────────────────────────────────────────────────

# ABC 编辑 Agent 可见的工具列表（动态从 abc_edit 分组读取）
_ABC_EDIT_TOOLS = get_tool_names("abc_edit")

AGENT_SYSTEM = f"""你是专业的 Sky 音乐谱子编辑助手，通过调用工具来修改 ABC Notation 谱子。

工作原则：
1. 优先使用确定性工具（transpose_abc、change_tempo）处理转调和速度修改
2. 风格转换使用 change_style 工具
3. 修改前先用 analyze_abc 了解谱子结构（如果意图不明确）
4. 每次调用工具后，用工具返回的新 ABC 作为下一步的输入
5. 完成所有修改后，用纯文本回复一句话的中文摘要（不要输出 JSON）
6. 不要重复调用同一个工具超过 2 次

当前可用工具（abc_edit 分组）：
{chr(10).join(f"- {name}" for name in _ABC_EDIT_TOOLS)}

注意：音频生成工具（generate_audio_*）不在你的职责范围内，请勿尝试调用。
"""

# 返回 ABC 内容的工具名集合（用于更新 latest_abc）
_ABC_RETURNING_TOOLS = frozenset({
    "transpose_abc", "change_tempo", "change_style", "add_ornament"
})


class ToolCallAgentRunner:
    async def run(
        self,
        current_abc: str,
        intent: str,
        meta: ScoreMeta,
        context_summary: str,
        publish: Publisher,
        scene: Scene = "editor",
    ) -> dict:
        """
        执行 Tool-Calling Agent，返回完整结果字典：
          {
            "abc": str,                    # 修改后的 ABC（所有场景都有）
            "summary": str,                # 中文摘要
            "tool_calls": [...],           # 工具调用记录（用于前端展示）
            "sky_json": str | None,        # scene=player/raw 时有值
            "midi_b64": str | None,        # scene=daw/raw 时有值
          }
        """
        # 只向 LLM 暴露 abc_edit 分组的工具，音频工具不参与编辑循环
        tools = get_tool_schemas("abc_edit")
        tool_call_records: list[dict] = []

        # 初始消息
        messages: list[dict] = [
            {"role": "system", "content": AGENT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"用户意图：{intent}\n\n"
                    f"当前谱子信息：标题={meta.title}, 调号={meta.key}, "
                    f"BPM={meta.bpm:.0f}, 拍号={meta.time_sig_num}/{meta.time_sig_den}, "
                    f"音符数={meta.note_count}\n"
                    f"历史上下文：{context_summary or '无'}\n\n"
                    f"当前 ABC 谱：\n{current_abc}"
                )
            }
        ]

        # 追踪最新的 ABC（每次工具调用后更新）
        latest_abc = current_abc
        final_summary = intent

        await publish("pipeline.step", {
            "step": "agent_start",
            "status": "running",
            "text": f"Agent 开始处理：{intent}",
        })

        # ── Tool-Calling Loop ────────────────────────────────────────────────
        for _round in range(MAX_TOOL_ROUNDS):
            response = await complete_with_tools(messages, tools)
            messages.append({
                "role": "assistant",
                "content": response["content"],
                "tool_calls": response["tool_calls"] or [],
            })

            finish_reason = response["finish_reason"]

            # LLM 决定不调用工具了，直接给出文本答复
            if finish_reason == "stop" or not response["tool_calls"]:
                final_summary = response["content"] or final_summary
                await publish("pipeline.step", {
                    "step": "agent_done",
                    "status": "succeeded",
                    "text": final_summary,
                })
                break

            # 执行所有 tool_calls（本轮可能有多个）
            for tc in response["tool_calls"]:
                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                await publish("tool.call", {
                    "call_id": tc["id"],
                    "tool": tool_name,
                    "arguments": {k: v for k, v in arguments.items() if k != "abc"},
                    "status": "running",
                })

                try:
                    result = await call_tool(tool_name, arguments)
                    result_str = (
                        result if isinstance(result, str)
                        else json.dumps(result, ensure_ascii=False)
                    )

                    # 如果工具返回了 ABC 谱，更新 latest_abc
                    if tool_name in _ABC_RETURNING_TOOLS:
                        if result_str.strip().startswith("X:"):
                            latest_abc = result_str

                    tool_call_records.append({
                        "id": tc["id"],
                        "tool": tool_name,
                        "arguments": {k: v for k, v in arguments.items() if k != "abc"},
                        "result_preview": (
                            result_str[:120] + "..."
                            if len(result_str) > 120 else result_str
                        ),
                        "status": "succeeded",
                    })

                    await publish("tool.call", {
                        "call_id": tc["id"],
                        "tool": tool_name,
                        "arguments": {k: v for k, v in arguments.items() if k != "abc"},
                        "status": "succeeded",
                        "result_preview": (
                            result_str[:80] + "..."
                            if len(result_str) > 80 else result_str
                        ),
                    })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })

                except Exception as e:
                    err_msg = f"工具执行失败: {e}"
                    tool_call_records.append({
                        "id": tc["id"],
                        "tool": tool_name,
                        "arguments": arguments,
                        "status": "failed",
                        "error": str(e),
                    })
                    await publish("tool.call", {
                        "call_id": tc["id"],
                        "tool": tool_name,
                        "status": "failed",
                        "error": str(e),
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": err_msg,
                    })
        else:
            # for 循环跑满未被 break 打断 = Agent 超过最大轮数仍未 stop，属于超时
            await publish("pipeline.step", {
                "step": "agent_done",
                "status": "failed",
                "text": f"Agent 超过最大轮数（{MAX_TOOL_ROUNDS}）未收敛，返回当前最优结果",
            })

        # ── OutputAdapter：按场景追加导出 ────────────────────────────────────
        sky_json: str | None = None
        midi_b64: str | None = None

        if scene in ("player", "raw"):
            await publish("pipeline.step", {
                "step": "output_adapt",
                "status": "running",
                "text": "正在生成 Sky JSON（小程序格式）...",
            })
            try:
                sky_json = await call_tool("abc_to_sky_json", {"abc": latest_abc})
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "succeeded",
                    "text": "Sky JSON 生成完成",
                })
            except Exception as e:
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "failed",
                    "text": f"Sky JSON 生成失败: {e}",
                })

        if scene in ("daw", "raw"):
            await publish("pipeline.step", {
                "step": "output_adapt",
                "status": "running",
                "text": "正在生成 MIDI...",
            })
            try:
                midi_b64 = await call_tool("abc_to_midi_b64", {"abc": latest_abc})
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "succeeded",
                    "text": "MIDI 生成完成",
                })
            except Exception as e:
                await publish("pipeline.step", {
                    "step": "output_adapt",
                    "status": "failed",
                    "text": f"MIDI 生成失败: {e}",
                })

        return {
            "abc": latest_abc,
            "summary": final_summary,
            "tool_calls": tool_call_records,
            "sky_json": sky_json,
            "midi_b64": midi_b64,
        }


edit_agent_runner = ToolCallAgentRunner()
