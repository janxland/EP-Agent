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

from app.agentcore.llm import complete, complete_stream, complete_with_tools, complete_with_tools_stream, get_current_model_name
from app.agentcore.todo_manager import TodoManager
from app.pipeline import db as _db

Publisher = Callable[[str, dict], Awaitable[None]]

MAX_REACT_ROUNDS = 8

# 文件写入类工具集合（模块级常量，避免每轮循环重建 set）
_FILE_WRITE_TOOLS: frozenset[str] = frozenset({
    "write_workspace_file", "edit_workspace_file",
    "delete_workspace_file", "copy_workspace_file",
    "rename_workspace_file", "move_workspace_file",
    "run_write_tasks_in_parallel",
    "abc_to_midi", "generate_h5_from_midi", "save_h5_output",
    "sovits_tts_and_save", "sovits_clone_and_save", "sovits_save_audio",
})


def _clean_content(text: str) -> str:
    """清理 LLM 流式输出时偶发混入 content 的 tool_call XML 残片。
    如 `}</tool_call>` `<tool_call>{...}</tool_call>` 等。

    修复 R1：原末尾 } 正则会误删合法 JSON 末尾的 }（如 '{"key":"val"}'）。
    改为仅清理「独占一行且该行只有 }」的孤立残留，不触碰多字符行末尾的 }。
    """
    s = re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', text)
    s = re.sub(r'</?tool_call>', '', s)
    # 孤立的 } 行（tool_call JSON 尾部残留）：行首可选空白 + } + 行尾
    s = re.sub(r'(?m)^[ \t]*\}[ \t]*$', '', s)
    # 注意：不再清理末尾孤立 }，避免误删合法 JSON 结尾
    # 清理多余空行（连续超过2个换行压缩为2个）
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


# ── 流式推送工具函数 ──────────────────────────────────────────────────────────

async def stream_text(text: str, publish: Publisher, chunk_size: int = 80):
    """将文本分块推送为 message.delta，模拟流式输出。
    chunk_size=80：每帧约 80 字符，减少帧数同时保持打字机感。
    不再 sleep：依赖事件循环自然调度，消除人为延迟。
    """
    for i in range(0, len(text), chunk_size):
        await publish("message.delta", {"delta": text[i:i + chunk_size]})
        # 让出事件循环一次，避免长文本阻塞其他协程
        if i + chunk_size < len(text):
            await asyncio.sleep(0)


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
        exceeded_rounds = False
        # P1: 连续全失败计数器（工具全部失败时避免无限重试）
        _consecutive_all_fail = 0
        _MAX_CONSECUTIVE_FAIL = 2   # 连续 2 轮工具全失败则强制退出
        # P4: 重复工具调用检测（tool_name + arguments hash → 出现次数）
        _tool_call_seen: dict[str, int] = {}
        _MAX_TOOL_REPEAT = 2        # 同一工具+参数最多重复 2 次

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
                "model":         get_current_model_name("strong"),  # T2: 携带模型名供 TraceCollector 记录
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

            # P3: finish_reason='length' — 上下文超限，content 可能是截断残片
            # 触发一次压缩后继续，而不是当作正常 stop 处理
            if finish_reason == "length":
                import logging as _log
                _log.getLogger("ep_agent.react").warning(
                    "[ReAct] finish_reason=length（上下文超限），触发压缩后继续 round=%d",
                    round_idx,
                )
                await publish("pipeline.step", {
                    "step":   "context_length_exceeded",
                    "status": "warning",
                    "text":   "⚠️ 上下文超限，正在压缩后继续...",
                })
                messages = await compress_messages(messages, session_id=session_id)
                # 不 break，继续下一轮（让 LLM 基于压缩后的 context 重新输出）
                continue

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
                except Exception as _e:
                    import logging as _log
                    _log.getLogger("ep_agent.react").warning(
                        "[ReactExecutor] assistant 消息落库失败 session=%s round=%d: %s",
                        session_id, round_idx, _e
                    )

            # ── Stop：LLM 完成输出 ────────────────────────────────────────────
            if finish_reason == "stop" or not tool_calls:
                final_content = content_clean
                # running TODO → done（已开始执行，LLM 认为完成）
                for rid in todo_manager.get_running_ids():
                    await todo_manager.complete_one(rid, publish)
                # ⚠️ pending TODO 不在此处标 skipped！
                # 原因：EditAgent/CreateAgent 在 run_edit()/run() 返回后，
                # Python 代码层还会继续执行保存、导出等步骤，
                # 这些步骤对应的 TODO 由外层 Agent 的 finish_all(done) 统一收尾。
                # 若此处标 skipped，finish_all 会跳过（skipped 不再处理），
                # 导致前端显示「步骤被跳过」但实际上 Python 已执行，状态不一致。
                # 外层 Agent 负责在所有 Python 层操作完成后调用 finish_all(done)。
                break

            # ── 执行工具调用（Observe 步骤）──────────────────────────────────
            current_running_id = (
                todo_manager.get_running_ids()[0]
                if todo_manager.get_running_ids() else None
            )

            # ── v5 Phase 1：并行工具执行 ──────────────────────────────────────
            # LLM 返回多个 tool_calls 时用 asyncio.gather 并行执行，
            # 汇聚结果后按原顺序注入 messages，降低整体延迟 20-30%。
            # 有数据依赖的工具 LLM 会自动拆成两轮，无需手动处理依赖关系。

            # ── 预处理：解析参数 + 重复检测（串行，无 IO 开销）─────────────────
            valid_tcs: list[tuple] = []
            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                # ── 数组参数自动修复：LLM 有时把 list 参数序列化为 JSON 字符串传入
                # 例如 file_paths='[".sky/a.abc"]'（字符串）而非 [".sky/a.abc"]（数组）
                # 遇到此情况自动 json.loads 还原为真正的列表
                for _k, _v in list(arguments.items()):
                    if isinstance(_v, str) and _v.strip().startswith("["):
                        try:
                            _parsed = json.loads(_v)
                            if isinstance(_parsed, list):
                                arguments[_k] = _parsed
                        except (json.JSONDecodeError, ValueError):
                            pass

                _call_key = f"{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)[:200]}"
                _tool_call_seen[_call_key] = _tool_call_seen.get(_call_key, 0) + 1
                if _tool_call_seen[_call_key] > _MAX_TOOL_REPEAT:
                    import logging as _log
                    _log.getLogger("ep_agent.react").warning(
                        "[ReAct] 检测到重复工具调用 tool=%s（第%d次），跳过",
                        tool_name, _tool_call_seen[_call_key],
                    )
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      f"[跳过] 工具 {tool_name} 已重复调用 {_tool_call_seen[_call_key]} 次（相同参数），请换用其他工具或直接输出结果。",
                    })
                    continue
                valid_tcs.append((tc, tool_name, arguments))

            # ── 并行执行所有合法工具调用（R2 修复：改用静态方法，消除闭包陷阱）────
            tool_succeeded_count = 0
            if valid_tcs:
                # return_exceptions=True：单个工具异常不取消其他工具（异常隔离）
                # 异常对象在后续 zip 处理时被识别为 (False, str(exc), None)
                _raw_results = await asyncio.gather(
                    *[self._run_one_tool(tc, tn, args, publish, session_id, call_tool, round_idx)
                      for tc, tn, args in valid_tcs],
                    return_exceptions=True,
                )
                parallel_results = [
                    r if not isinstance(r, BaseException) else (False, str(r), None)
                    for r in _raw_results
                ]
            else:
                parallel_results = []

            # ── 按原顺序处理结果 ──────────────────────────────────────────────
            # _FILE_WRITE_TOOLS 已提取为模块级常量（R3 修复）
            for (tc, tool_name, arguments), (success, result_str, result) in zip(valid_tcs, parallel_results):
                if success:
                    # 捕获特定工具的额外输出
                    if tool_name == "abc_to_sky_json":
                        extra["sky_json"] = result_str
                    elif tool_name == "abc_to_midi_file":
                        try:
                            _midi_r = result if isinstance(result, dict) else json.loads(result_str)
                            extra["midi_url"] = _midi_r.get("midi_url", "")
                        except Exception:
                            pass
                    elif tool_name == "save_h5_file":
                        try:
                            _h5_r = result if isinstance(result, dict) else json.loads(result_str)
                            if "url_path" in _h5_r:
                                extra["url_path"]  = _h5_r.get("url_path", "")
                                extra["file_path"] = _h5_r.get("file_path", "")
                                extra["size_kb"]   = _h5_r.get("size_kb", 0)
                        except Exception:
                            pass
                    elif tool_name == "generate_h5_from_midi":
                        try:
                            _gen_r = result if isinstance(result, dict) else json.loads(result_str)
                            if "title" in _gen_r:
                                extra["title"]    = _gen_r.get("title", "")
                                extra["template"] = _gen_r.get("template", "apple")
                            if "midi_url" in _gen_r:
                                extra["midi_url"] = _gen_r.get("midi_url", "")
                            if _gen_r.get("url_path") or _gen_r.get("workspace_path"):
                                extra["url_path"]       = _gen_r.get("url_path", "")
                                extra["file_path"]      = _gen_r.get("file_path", "")
                                extra["size_kb"]        = _gen_r.get("size_kb", 0)
                                extra["workspace_path"] = _gen_r.get("workspace_path", "")
                        except Exception:
                            pass

                    # workspace_path 写入 Session.extra 记忆
                    try:
                        _any_r = result if isinstance(result, dict) else json.loads(result_str)
                        _ws_path = _any_r.get("workspace_path", "") if isinstance(_any_r, dict) else ""
                        if _ws_path and session_id:
                            _fname = _any_r.get("filename") or _any_r.get("name", "")
                            from app.agentcore.session_context import remember_workspace_file
                            remember_workspace_file(session_id, _ws_path, _fname)
                    except Exception:
                        pass

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
                        "id": tc["id"], "tool": tool_name,
                        "arguments": {k: v for k, v in arguments.items() if k != "abc"},
                        "result_preview": preview, "status": "succeeded",
                    })
                    await publish("tool.call", {
                        "call_id": tc["id"], "tool": tool_name, "status": "succeeded",
                        "result_preview": result_str[:80] + "..." if len(result_str) > 80 else result_str,
                        "full_result": result_str[:4096],
                    })
                    _result_for_ctx = (
                        result_str[:8192] + "\n...[结果过长已截断，如需完整内容请调用工具重新获取]..."
                        if len(result_str) > 8192 else result_str
                    )
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": _result_for_ctx,
                    })
                    tool_succeeded_count += 1

                    if tool_name in _FILE_WRITE_TOOLS and session_id:
                        try:
                            _changed_path = ""
                            if isinstance(result, dict):
                                _changed_path = (
                                    result.get("workspace_path") or result.get("file_path")
                                    or result.get("path") or ""
                                )
                            await publish("workspace.files.changed", {
                                "tool": tool_name, "path": _changed_path, "trigger": "tool_call",
                            }, display=False)
                        except Exception:
                            pass

                    if session_id:
                        try:
                            # FIX: 用 uuid 保证唯一，避免 call_id 复用时 INSERT OR IGNORE 静默丢弃
                            _db.insert_message(
                                msg_id=f"tool_{tc['id']}_{uuid.uuid4().hex[:8]}",
                                session_id=session_id,
                                role="tool", content=result_str[:4096],
                                tool_call_id=tc["id"], tool_name=tool_name,
                            )
                        except Exception as _e:
                            import logging as _log
                            _log.getLogger("ep_agent.react").warning(
                                "[ReactExecutor] tool 消息落库失败 session=%s tool=%s: %s",
                                session_id, tool_name, _e
                            )

                else:
                    # 工具执行失败
                    e_str = result_str
                    tool_call_records.append({
                        "id": tc["id"], "tool": tool_name,
                        "arguments": arguments, "status": "failed", "error": e_str,
                    })
                    await publish("tool.call", {
                        "call_id": tc["id"], "tool": tool_name,
                        "status": "failed", "error": e_str,
                    })
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": f"工具执行失败: {e_str}",
                    })
                    if session_id:
                        try:
                            _db.insert_message(
                                msg_id=f"tool_{tc['id']}_{uuid.uuid4().hex[:8]}",
                                session_id=session_id,
                                role="tool", content=f"工具执行失败: {e_str}",
                                tool_call_id=tc["id"], tool_name=tool_name,
                            )
                        except Exception as _e:
                            import logging as _log
                            _log.getLogger("ep_agent.react").warning(
                                "[ReactExecutor] tool 失败消息落库失败 session=%s tool=%s: %s",
                                session_id, tool_name, _e
                            )

            # 工具批次全部执行完后，complete 当前 running TODO（若有工具成功）
            if tool_succeeded_count > 0 and current_running_id:
                t = todo_manager.get_by_id(current_running_id)
                if t and t.get("status") == "running":
                    await todo_manager.complete_one(current_running_id, publish)

            # R5 修复：连续全失败计数器（防止工具全部失败时无限重试）
            if valid_tcs and tool_succeeded_count == 0:
                _consecutive_all_fail += 1
                if _consecutive_all_fail >= _MAX_CONSECUTIVE_FAIL:
                    import logging as _log
                    _log.getLogger("ep_agent.react").warning(
                        "[ReAct] 连续 %d 轮工具全部失败，强制退出 round=%d",
                        _consecutive_all_fail, round_idx,
                    )
                    await publish("pipeline.step", {
                        "step":   "react_all_fail_exit",
                        "status": "warning",
                        "text":   f"⚠️ 连续 {_consecutive_all_fail} 轮工具全部失败，已退出重试",
                    })
                    break
            else:
                _consecutive_all_fail = 0  # 有成功则重置计数

        else:
            # for-else：正常耗尽所有轮次（未 break）
            # 用最后一轮的实际 content（若有），而不是硬编码错误文本
            # 这样 LLM 在最后一轮输出的结果仍然能正常返回给用户
            exceeded_rounds = True
            if not final_content:
                final_content = content_clean or "[任务未在最大轮次内完成，请尝试简化请求]"

        return {
            "content":         final_content,
            "tool_calls":      tool_call_records,
            "rounds":          round_idx + 1,
            "extra":           extra,
            "exceeded_rounds": exceeded_rounds,   # R6: 调用方可感知是否超轮退出
            # 标记：ReactExecutor 已落库所有 assistant/tool 消息，调用方无需重复写入
            "_persisted":      bool(session_id),
        }

    # ── R2 修复：_run_one_tool 提取为静态方法，消除闭包陷阱 ──────────────────────
    # 所有外部依赖（publish/session_id/call_tool/round_idx）通过参数显式传入，
    # 不再依赖外层作用域捕获，未来重构为异步迭代时不会出现变量污染。

    @staticmethod
    async def _run_one_tool(
        tc: dict,
        tool_name: str,
        arguments: dict,
        publish,
        session_id: str,
        call_tool,
        round_idx: int,
    ) -> tuple:
        """执行单个工具，返回 (success: bool, result_str: str, result_raw: Any)。"""
        # 敏感字段脱敏（大型 base64 截断，避免日志/SSE 膨胀）
        _SENSITIVE_KEYS = {
            "abc", "audio_b64", "content",
            "ref_audio_b64", "reference_audio_b64",
            "attachment_b64", "b64", "image_b64",
            "midi_b64", "audio_data", "file_b64",
        }
        safe_args = {
            k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
            for k, v in arguments.items()
            if k not in _SENSITIVE_KEYS
        }
        await publish("tool.call", {
            "call_id": tc["id"], "tool": tool_name,
            "arguments": safe_args, "status": "running", "round_idx": round_idx,
        })
        try:
            if session_id:
                try:
                    from app.agentcore.session_context import set_current_session_id
                    set_current_session_id(session_id)
                except Exception:
                    pass

            # ── 工作区工具前置检查：确保 project_id 已绑定 ──────────────────────
            # workspace 类工具依赖 project_id 定位文件目录，若未绑定则提前修复，
            # 避免工具执行时抛 "会话未绑定项目" PermissionError。
            _WS_TOOLS = frozenset({
                "list_workspace_files", "read_workspace_files", "read_workspace_file",
                "write_workspace_file", "edit_workspace_file", "delete_workspace_file",
                "copy_workspace_file", "rename_workspace_file", "move_workspace_file",
                "run_write_tasks_in_parallel", "get_workspace_file_url",
                "save_score_to_workspace", "list_workspace_scores",
                "abc_to_midi", "abc_to_midi_file", "abc_to_sky_json",
                "generate_h5_from_midi", "generate_h5_from_abc",
                "save_h5_file", "save_h5_output",
                "sovits_tts_and_save", "sovits_clone_and_save", "sovits_save_audio",
            })
            if tool_name in _WS_TOOLS:
                try:
                    from app.agentcore.session_context import (
                        get_current_project_id, get_current_workspace_id
                    )
                    _proj = get_current_project_id()
                    _ws   = get_current_workspace_id()
                    if not _proj:
                        import logging as _log
                        _log.getLogger("ep_agent.react").warning(
                            "[ReactExecutor] 工具 %s 前置检查：project_id 为空（ws=%s），"
                            "触发 session_id → DB 确定性查询绑定",
                            tool_name, _ws or "?",
                        )
                        # 通过 session_id 查 DB 取 project_id（确定性，不猜测）
                        from app.agentcore.tools.workspace_tools import _get_project_root
                        _get_project_root()
                        # 验证绑定结果
                        _proj_after = get_current_project_id()
                        if _proj_after:
                            _log.getLogger("ep_agent.react").info(
                                "[ReactExecutor] 前置绑定成功: proj=%s", _proj_after
                            )
                        else:
                            _log.getLogger("ep_agent.react").error(
                                "[ReactExecutor] 前置绑定失败，工具 %s 可能报错", tool_name
                            )
                except Exception as _pre_e:
                    import logging as _log
                    _log.getLogger("ep_agent.react").warning(
                        "[ReactExecutor] 工具前置检查异常（不影响执行）: %s", _pre_e
                    )

            try:
                from app.agentcore.replay_engine import get_mock_registry
                _mock = get_mock_registry()
            except Exception:
                _mock = None
            if _mock is not None:
                result = await _mock.call(tool_name, arguments)
            else:
                result = await call_tool(tool_name, arguments)
            result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            return True, result_str, result
        except Exception as exc:
            return False, str(exc), None
