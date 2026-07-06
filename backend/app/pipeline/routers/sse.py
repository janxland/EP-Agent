"""
SSE Stream 路由 — /api/sessions/{session_id}/stream
负责历史 replay + 实时事件推送
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.pipeline.domain import new_id
from app.pipeline import db as _db
from app.pipeline.routers.hub import _queues, _sequences, _publish

router = APIRouter()
_logger = logging.getLogger(__name__)


@router.get("/sessions/{session_id}/stream")
async def sse_stream(session_id: str, request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    _queues.setdefault(session_id, []).append(q)

    async def event_generator() -> AsyncIterator[str]:
        yield f'data: {{"type":"connected","session_id":"{session_id}"}}\n\n'

        try:
            _replay_seq_base = _sequences.get(session_id, 0)

            # 1. abc_notation replay
            session_info = _db.get_session_info(session_id)
            if session_info and session_info.get("abc_notation"):
                _replay_seq_base += 1
                abc_evt = {
                    "id": new_id("evt"), "type": "abc.updated",
                    "session_id": session_id, "display": True,
                    "sequence": _replay_seq_base,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "abc":     session_info["abc_notation"],
                        "version": 1,
                        "summary": session_info.get("score_title", ""),
                        "meta": {
                            "title":      session_info.get("score_title", ""),
                            "key":        session_info.get("score_key", "C"),
                            "bpm":        session_info.get("score_bpm", 120),
                            "note_count": session_info.get("score_notes", 0),
                        },
                        "_replay": True,
                    },
                }
                yield f"data: {json.dumps(abc_evt, ensure_ascii=False)}\n\n"

            # 2. 历史消息 replay
            history_msgs = _db.get_session_messages(session_id)
            seq = _replay_seq_base
            for msg in history_msgs:
                role    = msg.get("role", "")
                content = msg.get("content", "") or ""
                if role not in ("user", "assistant", "tool"):
                    continue
                if role != "assistant" and not content.strip():
                    continue
                payload: dict = {"role": role, "content": content, "msg_id": msg.get("id", "")}
                if role == "assistant":
                    raw_tc = msg.get("tool_calls")
                    if raw_tc:
                        try:
                            tc_list = json.loads(raw_tc) if isinstance(raw_tc, str) else raw_tc
                            if tc_list:
                                payload["tool_calls"] = tc_list
                        except Exception:
                            pass
                    if not content.strip() and not payload.get("tool_calls"):
                        continue
                if role == "tool":
                    payload["tool_call_id"] = msg.get("tool_call_id", "")
                    payload["name"]         = msg.get("tool_name", "")
                seq += 1
                yield f"data: {json.dumps({'id': new_id('evt'), 'type': 'message.history', 'session_id': session_id, 'display': True, 'sequence': seq, 'timestamp': msg.get('created_at', datetime.now(timezone.utc).isoformat()), 'payload': payload}, ensure_ascii=False)}\n\n"

            # 3. TODO replay
            todos = _db.get_session_todos(session_id)
            if todos:
                todo_items = [{"id": t.get("id",""), "title": t.get("title",""), "detail": t.get("detail",""), "status": t.get("status","done")} for t in todos]
                last_todo = todos[-1] if todos else {}
                seq += 1
                yield f"data: {json.dumps({'id': new_id('evt'), 'type': 'todo.list', 'session_id': session_id, 'display': True, 'sequence': seq, 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'todos': todo_items, 'domain': last_todo.get('domain',''), 'summary': last_todo.get('summary',''), '_replay': True}}, ensure_ascii=False)}\n\n"

            _sequences[session_id] = seq

            # 4. role.active replay
            try:
                from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID
                _sess_extra = session_info.get("extra") or {} if session_info else {}
                if isinstance(_sess_extra, str):
                    try: _sess_extra = json.loads(_sess_extra)
                    except Exception: _sess_extra = {}
                _role = get_role_or_default(_sess_extra.get("role_id", DEFAULT_ROLE_ID))
                seq += 1
                yield f"data: {json.dumps({'id': new_id('evt'), 'type': 'role.active', 'session_id': session_id, 'display': False, 'sequence': seq, 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'role_id': _role.id, 'role_name': _role.name, 'icon': _role.icon, 'color': _role.color, '_replay': True}}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

            # 5. pipeline.state replay
            try:
                _ps = (session_info or {}).get("pipeline_state", "idle")
                _eff = "idle" if _ps == "running" else _ps
                seq += 1
                yield f"data: {json.dumps({'id': new_id('evt'), 'type': 'pipeline.state', 'session_id': session_id, 'display': False, 'sequence': seq, 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'state': _eff, '_replay': True}}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

            # 6. workspace.scores replay
            try:
                _ws_id = (session_info or {}).get("workspace_id") or ""
                if _ws_id:
                    from app.agentcore.tools.workspace_tools import list_workspace_scores_impl
                    from app.agentcore.session_context import set_current_session_id as _set_sid
                    _set_sid(session_id)
                    _scores = list_workspace_scores_impl()
                    if _scores:
                        seq += 1
                        yield f"data: {json.dumps({'id': new_id('evt'), 'type': 'workspace.scores', 'session_id': session_id, 'display': False, 'sequence': seq, 'timestamp': datetime.now(timezone.utc).isoformat(), 'payload': {'workspace_id': _ws_id, 'scores': _scores, '_replay': True}}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

        except Exception as e:
            _logger.warning("SSE replay failed for %s: %s", session_id, e)

        # 实时事件循环
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _queues.get(session_id, []).remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
