"""
审计与重播 API（Audit & Replay）
"""
from __future__ import annotations
import asyncio
import json as _json
import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from app.pipeline import db as _db

router = APIRouter()


# ── 导出辅助函数 ───────────────────────────────────────────────────────────────

def _build_llm_friendly_trace(trace: dict, spans: list[dict], fixtures: list[dict]) -> dict:
    """
    构建「大模型友好」的 trace 导出格式。
    设计原则：
    - 完整保留所有工具调用入参/出参（不截断）
    - 按 round_idx 分组，呈现每轮 ReAct 的思考→工具调用链
    - 附加 analysis_guide 字段，告知 LLM 如何理解这份 JSON
    """
    # 按 round_idx 分组 spans
    rounds: dict[int, dict] = {}
    for s in spans:
        ri = s.get("round_idx", 0)
        if ri not in rounds:
            rounds[ri] = {"round_idx": ri, "model_call": None, "tool_calls": []}

        tool_name = s.get("tool_name", "")
        span_kind = s.get("span_kind", "")

        # AUDIT-FIX-02: llm: 前缀的 model span（来自 SubAgent 内部 LLM 调用，如 llm:create_main）
        # 同时放入 model_call（token 统计）和 tool_calls（让 PipelineTimeline 可见）。
        # 普通 model span（supervisor/pipeline 级别）只放 model_call，不进 tool_calls。
        is_llm_tool_span = span_kind == "model" and tool_name.startswith("llm:")
        is_plain_model_span = span_kind == "model" and not tool_name.startswith("llm:")

        if is_plain_model_span:
            # 普通模型调用（supervisor LLM 决策等）：只更新 model_call
            rounds[ri]["model_call"] = {
                "model":         s.get("model", ""),
                "input_tokens":  s.get("input_tokens", 0),
                "output_tokens": s.get("output_tokens", 0),
                "finish_reason": s.get("finish_reason", ""),
                "duration_ms":   s.get("duration_ms", 0),
                "status":        s.get("status", ""),
            }
        elif span_kind == "tool" or is_llm_tool_span:
            # 工具调用 span + llm: SubAgent LLM 调用 span：都放入 tool_calls 供 Timeline 展示
            raw_args = s.get("tool_args") or "{}"
            try:
                args_obj = _json.loads(raw_args)
            except Exception:
                args_obj = {"_raw": raw_args}
            raw_result = s.get("tool_result") or "{}"
            try:
                result_obj = _json.loads(raw_result)
            except Exception:
                result_obj = {"_raw": raw_result}

            rounds[ri]["tool_calls"].append({
                "step_idx":       s.get("step_idx", 0),
                "tool_name":      tool_name,
                "status":         s.get("status", ""),
                "duration_ms":    s.get("duration_ms", 0),
                "args":           args_obj,
                "result":         result_obj,
                "result_preview": s.get("tool_result_preview", ""),
                "error_msg":      s.get("error_msg", "") or "",
                "call_id":        s.get("call_id", ""),
                # llm: span 额外携带 model 信息，方便前端展示耗时和模型名
                "model":          s.get("model", "") if is_llm_tool_span else "",
                "agent_name":     s.get("agent_name", "") if is_llm_tool_span else "",
            })
            # llm: span 同时也更新 model_call（token 统计）
            if is_llm_tool_span:
                rounds[ri]["model_call"] = {
                    "model":         s.get("model", ""),
                    "input_tokens":  s.get("input_tokens", 0),
                    "output_tokens": s.get("output_tokens", 0),
                    "finish_reason": s.get("finish_reason", ""),
                    "duration_ms":   s.get("duration_ms", 0),
                    "status":        s.get("status", ""),
                }

    # 按 round_idx 排序，每个 round 内 tool_calls 按 step_idx 排序
    react_rounds = [rounds[k] for k in sorted(rounds.keys())]
    for rnd in react_rounds:
        rnd["tool_calls"].sort(key=lambda x: x.get("step_idx", 0))

    # fixture 摘要（用于理解哪些工具调用有缓存快照）
    fixture_summary = [
        {
            "tool_name":      f.get("tool_name", ""),
            "tool_args_hash": f.get("tool_args_hash", ""),
            "has_result":     bool(f.get("tool_result")),
        }
        for f in fixtures
    ]

    # 统计：tool span + llm: span 都计入 tool_calls 统计
    total_tool_calls  = sum(
        1 for s in spans
        if s.get("span_kind") == "tool"
        or (s.get("span_kind") == "model" and s.get("tool_name", "").startswith("llm:"))
    )
    failed_tool_calls = sum(
        1 for s in spans
        if (s.get("span_kind") == "tool"
            or (s.get("span_kind") == "model" and s.get("tool_name", "").startswith("llm:")))
        and s.get("status") == "error"
    )
    total_in_tokens   = sum(s.get("input_tokens", 0)  for s in spans if s.get("span_kind") == "model")
    total_out_tokens  = sum(s.get("output_tokens", 0) for s in spans if s.get("span_kind") == "model")

    return {
        # ── 元数据 ──────────────────────────────────────────────────────────
        "trace_id":       trace.get("trace_id", ""),
        "session_id":     trace.get("session_id", ""),
        "domain":         trace.get("domain", ""),
        "role_id":        trace.get("role_id", ""),
        "user_message":   trace.get("user_message", ""),   # 用户原始请求（最重要）
        "attachment":     trace.get("attachment_name", ""),
        "status":         trace.get("status", ""),
        "started_at":     trace.get("started_at", ""),
        "ended_at":       trace.get("ended_at", ""),
        "duration_ms":    trace.get("duration_ms", 0),
        # ── 统计摘要 ────────────────────────────────────────────────────────
        "summary": {
            "total_react_rounds": len(react_rounds),
            "total_tool_calls":   total_tool_calls,
            "failed_tool_calls":  failed_tool_calls,
            "total_input_tokens": total_in_tokens,
            "total_output_tokens": total_out_tokens,
            "fixture_count":      len(fixtures),
        },
        # ── ReAct 调用链（核心数据，按轮次展开）────────────────────────────
        "react_chain": react_rounds,
        # ── Fixture 快照摘要 ─────────────────────────────────────────────
        "fixtures": fixture_summary,
        # ── 给 LLM 的分析指引 ───────────────────────────────────────────
        "analysis_guide": (
            "这是一条 EP-Agent ReAct 执行链路的完整审计记录。\n"
            "• user_message: 用户原始请求\n"
            "• domain: 意图路由结果（edit/create/convert/query/audio/voice）\n"
            "• react_chain: 按轮次展开的 ReAct 循环，每轮包含 model_call（LLM推理）和 tool_calls（工具调用）\n"
            "• tool_calls[].args: 工具完整入参（JSON对象）\n"
            "• tool_calls[].result: 工具完整出参（JSON对象）\n"
            "• tool_calls[].status: ok=成功, error=失败, skipped=被跳过\n"
            "• summary.failed_tool_calls > 0 说明有工具调用失败，重点检查对应 error_msg\n"
            "分析建议：先看 user_message 理解意图，再看 react_chain 逐轮追踪执行路径，"
            "重点关注 status=error 或 status=skipped 的步骤。"
        ),
    }


@router.get("/sessions/{session_id}/traces")
async def list_session_traces(session_id: str, limit: int = 20, offset: int = 0):
    try:
        return {"ok": True, "traces": _db.get_traces_by_session(session_id, limit=min(limit, 50), offset=offset)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/traces/{trace_id}")
async def get_trace_detail(trace_id: str):
    try:
        trace = _db.get_trace(trace_id)
        if not trace:
            raise HTTPException(404, f"trace not found: {trace_id}")
        return {"ok": True, "trace": trace, "spans": _db.get_spans_by_trace(trace_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/traces/{trace_id}/spans")
async def list_trace_spans(trace_id: str):
    try:
        return {"ok": True, "spans": _db.get_spans_by_trace(trace_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/traces/search")
async def search_traces(session_id: str = "", domain: str = "", status: str = "",
                        keyword: str = "", limit: int = 50, offset: int = 0):
    try:
        traces = _db.search_traces(session_id=session_id, domain=domain, status=status,
                                   keyword=keyword, limit=min(limit, 100), offset=offset)
        return {"ok": True, "traces": traces, "total": len(traces)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/sessions/{session_id}/traces/stats")
async def get_session_trace_stats(session_id: str):
    try:
        return {"ok": True, "stats": _db.get_trace_stats(session_id=session_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/sessions/{session_id}/traces")
async def delete_session_traces(session_id: str):
    try:
        return {"ok": True, "deleted": _db.delete_traces_by_session(session_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/traces/{trace_id}/fixtures")
async def list_trace_fixtures(trace_id: str):
    try:
        return {"ok": True, "fixtures": _db.get_fixtures_by_trace(trace_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/traces/{trace_id}/replays")
async def list_trace_replays(trace_id: str, limit: int = 10):
    try:
        return {"ok": True, "replays": _db.get_replays_by_source_trace(trace_id, limit=limit)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/traces/{trace_id}/replay")
async def replay_trace(trace_id: str, mode: str = "fixture"):
    """
    基于 LangGraph Checkpointer 快照的 trace 回放（v2）。
    优先用 ReplayEngineV2（快照回放），无快照时自动 rerun。
    """
    if mode not in ("fixture", "live"):
        raise HTTPException(400, "mode 必须为 fixture 或 live")
    try:
        # 先查 trace 对应的 session_id
        trace = _db.get_trace(trace_id)
        if not trace:
            raise HTTPException(404, f"trace not found: {trace_id}")
        session_id = trace.get("session_id", "")
        if not session_id:
            raise HTTPException(400, "trace 缺少 session_id，无法回放")

        from app.agentcore.replay_engine_v2 import ReplayEngineV2
        if mode == "live":
            result = await ReplayEngineV2().replay_rerun(session_id=session_id)
        else:
            # fixture 模式：快照只读回放
            result = await ReplayEngineV2().replay_snapshot(session_id=session_id)
        if result.get("error"):
            raise HTTPException(500, result["error"])
        return {"ok": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/traces/{trace_id}/replay/stream")
async def replay_trace_stream(trace_id: str, mode: str = "fixture"):
    """
    基于 LangGraph Checkpointer 快照的流式 trace 回放（v2，SSE）。
    优先用 ReplayEngineV2（快照回放），无快照时自动 rerun。
    """
    if mode not in ("fixture", "live"):
        raise HTTPException(400, "mode 必须为 fixture 或 live")

    # 先查 trace 对应的 session_id（在生成器外查，避免异常被 SSE 吞掉）
    trace = _db.get_trace(trace_id)
    if not trace:
        raise HTTPException(404, f"trace not found: {trace_id}")
    session_id = trace.get("session_id", "")
    if not session_id:
        raise HTTPException(400, "trace 缺少 session_id，无法回放")

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def publish(evt_type: str, payload: dict):
            await queue.put((evt_type, payload))

        from app.agentcore.replay_engine_v2 import ReplayEngineV2
        engine = ReplayEngineV2()

        async def run_replay():
            try:
                if mode == "live":
                    # BUG-019 修复：live 模式必须传 publish，否则 SSE 事件进入黑洞
                    result = await engine.replay_rerun(session_id=session_id, publish=publish)
                else:
                    result = await engine.replay_snapshot(session_id=session_id, publish=publish)
                await queue.put(("replay.done", result))
            except Exception as exc:
                await queue.put(("replay.error", {"error": str(exc)}))
            finally:
                await queue.put(("__done__", {}))

        task = asyncio.create_task(run_replay())
        try:
            while True:
                try:
                    evt_type, payload = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield 'data: {"type": "replay.error", "error": "重播超时"}\n\n'
                    break
                if evt_type == "__done__":
                    yield 'data: {"type": "done"}\n\n'
                    break
                elif evt_type == "replay.done":
                    # BUG-029/035 修复：只推送摘要，不含 snapshots 大数组（防超大帧）
                    slim = {
                        "type":       "replay.done",
                        "status":     payload.get("status", ""),
                        "mode":       payload.get("mode", ""),
                        "session_id": payload.get("session_id", ""),
                        "steps":      payload.get("steps", 0),
                        "visited":    payload.get("visited", []),
                        "error":      payload.get("error", ""),
                    }
                    yield f"data: {_json.dumps(slim, ensure_ascii=False)}\n\n"
                elif evt_type == "replay.error":
                    yield f"data: {_json.dumps({'type': 'replay.error', **payload}, ensure_ascii=False)}\n\n"
                elif evt_type in ("pipeline.step", "replay.step"):
                    p = {**payload, "type": "replay.step"}
                    yield f"data: {_json.dumps(p, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try: await task
                except (asyncio.CancelledError, Exception): pass

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/traces/{trace_id}/export")
async def export_trace_json(trace_id: str):
    """
    一键导出单条 trace 完整审计链路 JSON（大模型友好格式）。
    包含：trace 元数据 + 按 ReAct 轮次展开的完整工具调用链（完整入参/出参，不截断）
    + analysis_guide 字段（告知 LLM 如何理解这份 JSON）。
    """
    try:
        trace = _db.get_trace(trace_id)
        if not trace:
            raise HTTPException(404, f"trace not found: {trace_id}")
        spans    = _db.get_spans_by_trace(trace_id)
        fixtures = _db.get_fixtures_by_trace(trace_id)

        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"trace_{trace_id[:8]}_{ts}.json"

        export_data = {
            "export_version": "2.0",
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "trace": _build_llm_friendly_trace(trace, spans, fixtures),
            # 保留原始 spans 方便程序化处理
            "raw_spans": spans,
        }

        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Trace-Id": trace_id,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/sessions/{session_id}/traces/export")
async def export_session_traces_json(session_id: str, limit: int = 10):
    """
    导出 session 最近 N 条 trace 的完整审计链路 JSON（大模型友好格式，默认最近10条）。
    每条 trace 包含：完整 ReAct 调用链（完整入参/出参，不截断）+ analysis_guide。
    直接把这个 JSON 丢给大模型即可分析链路问题。
    """
    try:
        traces = _db.get_traces_by_session(session_id, limit=min(limit, 20), offset=0)
        result = []
        for trace in traces:
            tid      = trace.get("trace_id", "")
            spans    = _db.get_spans_by_trace(tid) if tid else []
            fixtures = _db.get_fixtures_by_trace(tid) if tid else []
            result.append(_build_llm_friendly_trace(trace, spans, fixtures))

        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"session_{session_id[:8]}_traces_{ts}.json"

        return JSONResponse(
            content={
                "export_version": "2.0",
                "exported_at":    datetime.datetime.utcnow().isoformat() + "Z",
                "session_id":     session_id,
                "trace_count":    len(result),
                "how_to_use":     (
                    "将此 JSON 直接发给大模型，询问："
                    "'请分析这些 EP-Agent 执行链路，找出失败步骤、意图路由问题、工具调用异常，"
                    "并给出优化建议。' 每条 trace 的 react_chain 字段包含完整的 ReAct 执行过程。"
                ),
                "traces":         result,
            },
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── LangGraph Checkpointer 审计回放 v2 端点 ──────────────────────────────────

@router.get("/sessions/{session_id}/snapshots")
async def list_session_snapshots(session_id: str):
    """获取 session 的所有 LangGraph Checkpointer 快照序列。"""
    try:
        from app.agentcore.graph_engine_v2 import get_session_history
        snapshots = await get_session_history(session_id)
        return {"ok": True, "session_id": session_id, "steps": len(snapshots), "snapshots": snapshots}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/sessions/{session_id}/replay/snapshot")
async def replay_session_snapshot(session_id: str):
    """快照回放（只读）：从 Checkpointer 读取历史快照按时间轴推送 SSE 事件，零副作用。
    BUG-036 修复：SSE replay.done 帧只推送摘要字段（不含 snapshots 大数组）。
    HTTP 端点（此函数）也只返回摘要，不将 snapshots 序列化到响应体。
    """
    async def _gen():
        q: asyncio.Queue = asyncio.Queue()
        async def pub(t, p): await q.put((t, p))
        from app.agentcore.replay_engine_v2 import ReplayEngineV2
        async def run():
            try:
                r = await ReplayEngineV2().replay_snapshot(session_id=session_id, publish=pub)
                await q.put(("replay.done", r))
            except Exception as exc:
                await q.put(("replay.error", {"error": str(exc)}))
            finally:
                await q.put(("__done__", {}))
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    t, p = await asyncio.wait_for(q.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield 'data: {"type":"replay.error","error":"超时"}\n\n'; break
                if t == "__done__":
                    yield 'data: {"type":"done"}\n\n'; break
                # BUG-035 修复：replay.done 只推送摘要，不含 snapshots 大数组（防超大帧）
                if t == "replay.done":
                    slim = {
                        "type":       "replay.done",
                        "status":     p.get("status", ""),
                        "mode":       p.get("mode", ""),
                        "session_id": p.get("session_id", ""),
                        "steps":      p.get("steps", 0),
                        "visited":    p.get("visited", []),
                        "error":      p.get("error", ""),
                    }
                    yield f"data: {_json.dumps(slim, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {_json.dumps({**p, 'type': t}, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try: await task
                except Exception: pass
    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/sessions/{session_id}/replay/rerun")
async def replay_session_rerun(session_id: str):
    """重跑回放：从原始输入重新执行，对比新旧快照差异。"""
    try:
        from app.agentcore.replay_engine_v2 import ReplayEngineV2
        result = await ReplayEngineV2().replay_rerun(session_id=session_id)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/sessions/{session_id}/replay/fork")
async def replay_session_fork(session_id: str, step_index: int = 0, new_message: str = ""):
    """Fork 重跑：从历史快照某步 fork，支持覆盖 message 模拟不同输入。"""
    try:
        from app.agentcore.replay_engine_v2 import ReplayEngineV2
        result = await ReplayEngineV2().replay_fork(
            session_id=session_id,
            step_index=step_index,
            new_message=new_message or None,
        )
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(500, str(e))
