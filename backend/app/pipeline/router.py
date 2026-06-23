"""
FastAPI 路由层 + SSE Hub
对应原 Go 版 pipeline/interfaces/http/
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from app.pipeline import service
from app.pipeline.domain import new_id

router = APIRouter(prefix="/api")

# ─── SSE Hub ──────────────────────────────────────────────────

_queues: dict[str, list[asyncio.Queue]] = {}
# per-session 递增序号，保证事件有序
_sequences: dict[str, int] = {}

async def _publish(session_id: str, evt_type: str, payload: dict, display: bool = True):
    _sequences[session_id] = _sequences.get(session_id, 0) + 1
    evt = {
        "id": new_id("evt"),
        "type": evt_type,
        "session_id": session_id,
        "display": display,
        "sequence": _sequences[session_id],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
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

# ─── SSE Stream ───────────────────────────────────────────────

@router.get("/sessions/{session_id}/stream")
async def sse_stream(session_id: str, request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    _queues.setdefault(session_id, []).append(q)

    async def event_generator() -> AsyncIterator[str]:
        # 连接心跳
        yield f'data: {{"type":"connected","session_id":"{session_id}"}}\n\n'
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # 发送 keepalive 注释
                    yield ": keepalive\n\n"
        finally:
            _queues.get(session_id, []).remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

# ─── Session ──────────────────────────────────────────────────

@router.post("/sessions", status_code=201)
async def create_session():
    sess = service.create_session()
    return {"session_id": sess.id}

@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        sess = service.get_session(session_id)
        return {"session_id": sess.id, "pipeline_state": sess.pipeline_state}
    except KeyError as e:
        raise HTTPException(404, str(e))

# ─── Convert ──────────────────────────────────────────────────

class ConvertRequest(BaseModel):
    json_content: str
    file_name: str = ""

@router.post("/sessions/{session_id}/convert")
async def convert(session_id: str, req: ConvertRequest):
    try:
        result = await service.convert(
            session_id, req.json_content, req.file_name,
            publish=_make_publisher(session_id),
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))

# ─── Edit ─────────────────────────────────────────────────────

class EditRequest(BaseModel):
    intent: str
    scene: str = "editor"  # editor | player | daw | raw

@router.post("/sessions/{session_id}/edit")
async def edit(session_id: str, req: EditRequest):
    try:
        result = await service.edit(
            session_id, req.intent,
            publish=_make_publisher(session_id),
            scene=req.scene,
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))

# ─── Universal Chat（统一对话接口，替代固定 /edit）────────────

class UniversalChatRequest(BaseModel):
    message: str
    attachment_content: str = ""   # 附件文本内容（JSON/TXT 等）
    attachment_name: str = ""      # 附件文件名
    attachment_b64: str = ""       # 音频附件 base64（音色克隆用）

@router.post("/sessions/{session_id}/chat")
async def universal_chat(session_id: str, req: UniversalChatRequest):
    """
    统一对话接口：LLM 自动识别意图，路由到 convert/edit/audio/voice/query。
    前端不需要区分接口，直接发消息即可。
    """
    try:
        result = await service.universal_chat(
            session_id=session_id,
            message=req.message,
            attachment_content=req.attachment_content,
            attachment_name=req.attachment_name,
            attachment_b64=req.attachment_b64,
            publish=_make_publisher(session_id),
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))

# ─── Export ───────────────────────────────────────────────────

class ExportRequest(BaseModel):
    format: str          # abc | midi | json
    instrument: int = 0  # MIDI 音色

@router.post("/sessions/{session_id}/export")
async def export_score(session_id: str, req: ExportRequest):
    try:
        data, filename, mime = await service.export_score(
            session_id, req.format, req.instrument
        )
        # 对非 ASCII 文件名做 RFC 5987 编码
        try:
            filename.encode("ascii")
            cd = f"attachment; filename={filename}"
        except UnicodeEncodeError:
            from urllib.parse import quote
            cd = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(
            content=data,
            media_type=mime,
            headers={"Content-Disposition": cd},
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
