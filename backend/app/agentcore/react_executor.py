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
import re
import uuid
from typing import Callable, Awaitable

from app.agentcore.llm import complete, complete_stream, complete_with_tools, complete_with_tools_stream
from app.agentcore.todo_manager import TodoManager
from app.pipeline import db as _db

Publisher = Callable[[str, dict], Awaitable[None]]

MAX_REACT_ROUNDS = 8


def _clean_content(text: str) -> str:
    """清理 LLM 流式输出时偶发混入 content 的 tool_call XML 残片。
    如 `}</tool_call>` `<tool_call>{...}</tool_call>` 等。
    """
    s = re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', text)
    s = re.sub(r'</?tool_call>', '', s)
    # 孤立的 } 行（tool_call JSON 尾部残留）：行首可选空白 + } + 行尾
    s = re.sub(r'(?m)^[ \t]*\}[ \t]*$', '', s)
    # 末尾孤立的 }（不在行首时也要清理，如 "content\n}"）
    s = re.sub(r'\}\s*$', '', s)
    # 清理多余空行（连续超过2个换行压缩为2个）
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


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
        "extra": {}            # 工具执行的额外输出（sky_json/midi_url 等）
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
        stream_tool_calling: bool = True,   # 默认开启流式 Tool Calling，实时推送 thinking
        session_id: str = "",              # 传入后自动落库 tool message（刷新恢复）
    ) -> dict:
        from app.agentcore.tools import call_tool
        from app.agentcore.memory_manager import (
            should_compress, compress_messages, estimate_tokens,
            extract_memory_from_result,
        )

        tool_call_records: list[dict] = []
        extra: dict = {}
        final_content = ""
        round_idx = 0

        for round_idx in range(max_rounds):
            # ── 上下文压力检测：超过 80% 触发 LLM 自主压缩 ────────────────────
            if round_idx > 0 and should_compress(messages):
                await publish("pipeline.step", {
                    "step":   "memory_compress",
                    "status": "running",
                    "text":   "🧠 上下文过长，正在压缩记忆...",
                })
                messages = await compress_messages(messages, session_id=session_id)
                await publish("pipeline.step", {
                    "step":   "memory_compress",
                    "status": "done",
                    "text":   f"✅ 记忆压缩完成，当前约 {estimate_tokens(messages)} tokens",
                })

            # 每轮开始时生成唯一 stream_turn_id，前端用此隔离多轮流式输出
            stream_turn_id = f"turn_{round_idx}_{uuid.uuid4().hex[:8]}"
            # 每轮开始时推送进度（多轮 ReAct 时前端可感知第 N 轮）
            # 携带 stream_turn_id：前端收到后先 commit 上一轮 streaming，再 reset 新轮次
            await publish("pipeline.step", {
                "step":          f"react_round_{round_idx}",
                "status":        "running",
                "text":          f"第 {round_idx + 1} 轮推理中..." if round_idx > 0 else "推理中...",
                "round_idx":     round_idx,
                "stream_turn_id": stream_turn_id,
            })

            # 有工具时使用 tool calling（优先流式），无工具时直接 complete
            if tools:
                if stream_tool_calling:
                    # 流式 Tool Calling：实时推送 thinking/content，工具调用在流结束后汇总
                    try:
                        response = await complete_with_tools_stream(
                            messages, tools, publish, temperature=temperature
                        )
                    except Exception:
                        # 流式失败则降级为非流式
                        response = await complete_with_tools(messages, tools, temperature=temperature)
                else:
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

            # 清理 content 中的 tool_call XML 残片（LLM 流式输出偶发混入）
            # 同时清理 messages 里的内容，避免污染后续 LLM context
            content_clean = _clean_content(content)

            messages.append({
                "role":       "assistant",
                "content":    content_clean,
                "tool_calls": tool_calls,
            })

            # ── 落库本轮 assistant 消息（含 tool_calls，刷新后 SSE replay 恢复工具卡片）──
            # 落库清理后的 content_clean，避免 XML 残片写入 DB
            # msg_id 使用 uuid 保证全局唯一，避免多 session / 多轮时 INSERT OR IGNORE 碰撞
            if session_id:
                try:
                    _asst_msg_id = f"asst_{uuid.uuid4().hex[:12]}"
                    _db.insert_message(
                        msg_id=_asst_msg_id,
                        session_id=session_id,
                        role="assistant",
                        content=content_clean,
                        tool_calls=tool_calls if tool_calls else None,
                    )
                except Exception:
                    pass  # 落库失败不影响主流程

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

                # 推送工具调用开始（过滤超长字段，防止 SSE 消息过大）
                safe_args = {
                    k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
                    for k, v in arguments.items()
                    if k not in ("abc", "audio_b64", "content")
                }
                await publish("tool.call", {
                    "call_id":   tc["id"],
                    "tool":      tool_name,
                    "arguments": safe_args,
                    "status":    "running",
                    "round_idx": round_idx,
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
                    elif tool_name == "abc_to_midi_file":
                        # 捕获 MIDI 文件 URL（不再使用 base64，改为静态路径）
                        try:
                            _midi_result = result if isinstance(result, dict) else json.loads(result_str)
                            extra["midi_url"] = _midi_result.get("midi_url", "")
                        except Exception:
                            pass
                    # H5 工具链：捕获 save_h5_file 的路径输出（供 H5Agent 推送 h5.ready 事件）
                    elif tool_name == "save_h5_file":
                        try:
                            _h5_result = result if isinstance(result, dict) else json.loads(result_str)
                            if "url_path" in _h5_result:
                                extra["url_path"]  = _h5_result.get("url_path", "")
                                extra["file_path"] = _h5_result.get("file_path", "")
                                extra["size_kb"]   = _h5_result.get("size_kb", 0)
                            # 写入重要记忆：H5 工作区路径
                            _ws_path = _h5_result.get("workspace_path", "") if isinstance(_h5_result, dict) else ""
                            if _ws_path and session_id:
                                from app.agentcore.session_context import remember_workspace_file
                                remember_workspace_file(session_id, _ws_path)
                        except Exception:
                            pass
                    # H5 生成工具：捕获 title/template/midi_url 供 h5.ready 事件使用
                    elif tool_name in ("generate_h5_poster", "generate_h5_from_abc", "generate_h5_from_midi"):
                        try:
                            _gen_result = result if isinstance(result, dict) else json.loads(result_str)
                            if "title" in _gen_result:
                                extra["title"]    = _gen_result.get("title", "")
                                extra["template"] = _gen_result.get("template", "apple")
                            if "midi_url" in _gen_result:
                                extra["midi_url"] = _gen_result.get("midi_url", "")
                            # 写入重要记忆：H5 工作区路径
                            _ws_path = _gen_result.get("workspace_path", "") if isinstance(_gen_result, dict) else ""
                            if _ws_path and session_id:
                                from app.agentcore.session_context import remember_workspace_file
                                remember_workspace_file(session_id, _ws_path)
                        except Exception:
                            pass

                    # ── 重要记忆自动写入：工作区文件上传/保存工具 ──────────────────
                    # 凡是工具返回 workspace_path 字段，自动记入 Session.extra 记忆中枢
                    # 覆盖范围：save_workspace_file / upload_to_workspace 等所有工作区写入工具
                    if tool_name not in ("save_h5_file", "generate_h5_poster",
                                         "generate_h5_from_abc", "generate_h5_from_midi"):
                        try:
                            _any_result = result if isinstance(result, dict) else json.loads(result_str)
                            _ws_path = _any_result.get("workspace_path", "") if isinstance(_any_result, dict) else ""
                            if _ws_path and session_id:
                                _fname = _any_result.get("filename") or _any_result.get("name", "")
                                from app.agentcore.session_context import remember_workspace_file
                                remember_workspace_file(session_id, _ws_path, _fname)
                        except Exception:
                            pass

                    # ── 记忆提取：工具执行后自动更新 memory.key_files ──────────────
                    # 高价值信息（文件路径）无需 LLM 判断，直接写入携带体
                    # 注意：extract_memory_from_result 是同步写入 _sessions dict，
                    # 直接 await 即可，无需 create_task（避免 ContextVar 副本问题）
                    if session_id:
                        try:
                            _r = result if isinstance(result, dict) else {}
                            await extract_memory_from_result(session_id, tool_name, _r)
                        except Exception:
                            pass

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

                    # ── 落库 tool message（刷新后 SSE replay 恢复）──────────────
                    if session_id:
                        try:
                            _db.insert_message(
                                msg_id=f"tool_{tc['id']}",
                                session_id=session_id,
                                role="tool",
                                content=result_str[:4096],  # 限制存储大小，防止 DB 膨胀
                                tool_call_id=tc["id"],
                                tool_name=tool_name,
                            )
                        except Exception:
                            pass  # 落库失败不影响主流程

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
                    # ── 落库失败的 tool message ─────────────────────────────────
                    if session_id:
                        try:
                            _db.insert_message(
                                msg_id=f"tool_{tc['id']}",
                                session_id=session_id,
                                role="tool",
                                content=f"工具执行失败: {e}",
                                tool_call_id=tc["id"],
                                tool_name=tool_name,
                            )
                        except Exception:
                            pass

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
            # 标记：ReactExecutor 已落库所有 assistant/tool 消息，调用方无需重复写入
            "_persisted": bool(session_id),
        }
