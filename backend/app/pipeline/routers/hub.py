"""
SSE Hub — 共享状态单例
所有子路由模块 import 此模块获取 _queues / _sequences / _abort_events / _running_tasks
以及 _publish / _make_publisher 工具函数。
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone

from app.pipeline.domain import new_id

# ── 全局状态 ────────────────────────────────────────────────────────────────────
_queues:        dict[str, list[asyncio.Queue]] = {}
_sequences:     dict[str, int]                 = {}
_abort_events:  dict[str, asyncio.Event]       = {}
_running_tasks: dict[str, asyncio.Task]        = {}


async def _publish(session_id: str, evt_type: str, payload: dict, display: bool = True):
    _sequences[session_id] = _sequences.get(session_id, 0) + 1
    evt = {
        "id":         new_id("evt"),
        "type":       evt_type,
        "session_id": session_id,
        "display":    display,
        "sequence":   _sequences[session_id],
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "payload":    payload,
    }
    data = json.dumps(evt, ensure_ascii=False)
    for q in _queues.get(session_id, []):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


def _make_publisher(session_id: str):
    async def publish(evt_type: str, payload: dict, display: bool = True):
        await _publish(session_id, evt_type, payload, display=display)
    return publish
