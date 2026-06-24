"""
ReactExecutor — 通用 ReAct Tool-Calling Loop

职责（单一）：
  - 执行 ReAct Loop（Think → Act → Observe）直到 stop 或超轮
  - 工具执行成功后自动 complete 对应 TODO（complete_one 纪律）
  - 流式文本推送（_stream_text / _stream_llm）

设计原则：
  - 不感知意图域（domain-agnostic）
  - 不直接操作 session/db
  - 通过 on_tool_result 回调允许调用方感知工具结果
  - 所有 SubAgent 共用同一个 ReactExecutor，不再各自实现 ReAct Loop
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Awaitable

from app.agentcore.llm import complete, complete_stream, complete_with_tools
from app.agentcore.todo_manager import TodoManager

Publisher = Callable[[str, dict], Awaitable[None]]

MAX_REACT_ROUNDS = 8  # 单次对话最大 ReAct 轮数（防止死循环）


# ── 流式推送工具函数 ──────────────────────────────────────────────────────────

async def stream_text(text: str, publish: Publisher, chunk_size: int = 24):
    """将文本分块推送为 message.delta，模拟流式输出。"""
    for i in range(0, len(text), chunk_size):
        await publish("message.delta", {"delta": text[i:i + chunk_size]})
        await asyncio.sleep(0.01)


async def stream_llm(messages: list[dict], publish: Publisher) -> str:
    """真正的流式 LLM 调用，每 token 推送 message.delta，降级为分块推送。"""
    try:
        full_text = ""
        async for chunk in complete_stream(messages):
            if chunk:
                await publish("message.delta", {"delta": chunk})
                full_text += chunk
        return full_text
    except (AttributeError, NotImplementedError):
        resp = await complete(messages)
        text = resp if isinstance(resp, str) else resp.get("content", "")
        await stream_text(text, publish)
        return text


# ── ReactExecutor ─────────────────────────────────────────────────────────────

class ReactExecutor:
    """
    通用 ReAct 执行器（所有 SubAgent 共用）。

    接收 messages + tools，执行 ReAct Loop 直到 stop 或超轮。
    通过 todo_manager 统一管理 TODO 状态（complete_one 纪律）。

    返回值：
      {
        "content": str,        # 最终 LLM 文本输出
        "tool_calls": [...],   # 所有工具调用记录
        "rounds": int,         # 实际执行轮数
        "extra": {}            # 工具执行的额外输出（sky_json/midi_b64 等）
      }
    """

    async def run(
        self,
        messages: list[dict],
        tools: list[dict],
        publish: Publisher,
        todo_manager: TodoManager,
        max_rounds: int = MAX_REACT_ROUNDS,
        on_tool_result: Callable | None = None,
        temperature: float = 0.2,
    ) -> dict:
        from app.agentcore.tools import call_tool

        tool_call_records: list[dict] = []
        extra: dict = {}
        final_content = ""
        round_idx = 0

        for round_idx in range(max_rounds):
            # 有工具时使用 tool calling，无工具时直接 complete
            if tools:
                response = await complete_with_tools(messages, tools, temperature=temperature)
            else:
                text = await complete(messages, temperature=temperature)
                response = {
                    "content":       text,
                    "tool_calls":    [],
                    "finish_reason": "stop",
                }

            content      = response.get("content") or ""
            tool_calls   = response.get("tool_calls") or []
            finish_reason = response.get("finish_reason", "stop")

            messages.append({
                "role":       "assistant",
                "content":    content,
                "tool_calls": tool_calls,
            })

            # ── Stop：LLM 完成输出 ────────────────────────────────────────────
            if finish_reason == "stop" or not tool_calls:
                final_content = content
                # running TODO → done（已开始执行，LLM 认为完成）
                for rid in todo_manager.get_running_ids():
                    await todo_manager.complete_one(rid, publish)
                # pending TODO → skipped（未执行就结束，不能标绿！）
                for pid in todo_manager.get_pending_ids():
                    await todo_manager.tick(pid, "skipped", publish)
                break

            # ── 执行工具调用（Observe 步骤）──────────────────────────────────
            current_running_id = (
                todo_manager.get_running_ids()[0]
                if todo_manager.get_running_ids() else None
            )

            tool_succeeded_count = 0
            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                await publish("tool.call", {
                    "call_id":   tc["id"],
                    "tool":      tool_name,
                    "arguments": {k: v for k, v in arguments.items() if k != "abc"},
                    "status":    "running",
                })

                try:
                    result = await call_tool(tool_name, arguments)
                    result_str = (
                        result if isinstance(result, str)
                        else json.dumps(result, ensure_ascii=False)
                    )

                    # 捕获特定工具的额外输出
                    if tool_name == "abc_to_sky_json":
                        extra["sky_json"] = result_str
                    elif tool_name == "abc_to_midi_b64":
                        extra["midi_b64"] = result_str

                    if on_tool_result:
                        await on_tool_result(tool_name, arguments, result, todo_manager)

                    preview = result_str[:120] + "..." if len(result_str) > 120 else result_str
                    tool_call_records.append({
                        "id":             tc["id"],
                        "tool":           tool_name,
                        "arguments":      {k: v for k, v in arguments.items() if k != "abc"},
                        "result_preview": preview,
                        "status":         "succeeded",
                    })
                    await publish("tool.call", {
                        "call_id":        tc["id"],
                        "tool":           tool_name,
                        "status":         "succeeded",
                        "result_preview": result_str[:80] + "..." if len(result_str) > 80 else result_str,
                    })
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      result_str,
                    })
                    tool_succeeded_count += 1

                except Exception as e:
                    tool_call_records.append({
                        "id": tc["id"], "tool": tool_name,
                        "arguments": arguments, "status": "failed", "error": str(e),
                    })
                    await publish("tool.call", {
                        "call_id": tc["id"], "tool": tool_name,
                        "status": "failed", "error": str(e),
                    })
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      f"工具执行失败: {e}",
                    })

            # 工具批次全部执行完后，complete 当前 running TODO（若有工具成功）
            if tool_succeeded_count > 0 and current_running_id:
                t = todo_manager.get_by_id(current_running_id)
                if t and t.get("status") == "running":
                    await todo_manager.complete_one(current_running_id, publish)

        return {
            "content":    final_content,
            "tool_calls": tool_call_records,
            "rounds":     round_idx + 1,
            "extra":      extra,
        }
