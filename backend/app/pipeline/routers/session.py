"""
Session 路由：CRUD / chat / abort / history / export / context / role
"""
from __future__ import annotations
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.pipeline import db as _db
from app.pipeline import service
from app.pipeline.domain import new_id
from app.pipeline.routers.hub import _abort_events, _running_tasks, _publish, _make_publisher
from app.config import config
import app.agentcore.llm as _llm

router  = APIRouter()
_logger = logging.getLogger(__name__)


# ── Session CRUD ─────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    workspace_id: str = ""
    project_id:   str = ""
    title:        str = "新对话"


@router.post("/sessions", status_code=201)
async def create_session(req: CreateSessionRequest = CreateSessionRequest()):
    ws_id   = req.workspace_id or None
    proj_id = req.project_id   or None

    if ws_id and not proj_id:
        projects = _db.list_projects(ws_id)
        if projects:
            proj_id = projects[0]["id"]
        else:
            default_proj = _db.create_project(ws_id, "默认项目", "自动创建的默认项目")
            proj_id = default_proj["id"]

    if proj_id and ws_id:
        try:
            if not _db.get_project_info(proj_id):
                _db.ensure_project(proj_id, ws_id)
        except Exception as _ep:
            _logger.warning("[create_session] ensure_project 失败: %s", _ep)

    sess = service.create_session(workspace_id=ws_id, project_id=proj_id, title=req.title)
    return {"session_id": sess.id, "workspace_id": ws_id, "project_id": proj_id, "title": req.title}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    _FIELDS = ["workspace_id", "project_id", "title", "score_title",
               "score_key", "score_bpm", "score_notes", "created_at", "updated_at"]
    try:
        sess = service.get_session(session_id)
        info = _db.get_session_info(session_id) or {}
        return {"id": sess.id, "session_id": sess.id,
                "pipeline_state": sess.pipeline_state,
                **{f: info.get(f) for f in _FIELDS}}
    except KeyError:
        info = _db.get_session_info(session_id)
        if not info:
            raise HTTPException(404, f"session not found: {session_id}")
        return {"id": session_id, "session_id": session_id,
                "pipeline_state": info.get("pipeline_state", "idle"),
                "stale": True,
                **{f: info.get(f) for f in _FIELDS}}


class RenameSessionRequest(BaseModel):
    title: str


@router.patch("/sessions/{session_id}")
async def rename_session_route(session_id: str, req: RenameSessionRequest):
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "title 不能为空")
    ok = _db.rename_session(session_id, title)
    if not ok:
        raise HTTPException(404, f"session not found: {session_id}")
    return {"ok": True, "session_id": session_id, "title": title}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_route(session_id: str):
    _db.delete_session_cascade(session_id)
    try:
        service.remove_session_from_memory(session_id)
    except Exception:
        pass


# ── Convert / Edit ────────────────────────────────────────────────────────────

class ConvertRequest(BaseModel):
    json_content: str
    file_name:    str = ""


@router.post("/sessions/{session_id}/convert")
async def convert(session_id: str, req: ConvertRequest):
    try:
        return await service.convert(session_id, req.json_content, req.file_name,
                                     publish=_make_publisher(session_id))
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


class EditRequest(BaseModel):
    intent: str
    scene:  str = "editor"


@router.post("/sessions/{session_id}/edit")
async def edit(session_id: str, req: EditRequest):
    try:
        return await service.edit(session_id, req.intent,
                                  publish=_make_publisher(session_id), scene=req.scene)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Universal Chat ────────────────────────────────────────────────────────────

class UniversalChatRequest(BaseModel):
    message:                    str
    workspace_id:               str = ""
    project_id:                 str = ""
    attachment_content:         str = ""
    attachment_name:            str = ""
    attachment_workspace_path:  str = ""
    attachment_b64:             str = ""
    # BUG-034 修复：前端可直接传 role_id，优先于 DB session.extra 中存储的值
    role_id:                    str = ""


def _rebuild_session_from_db(session_id: str):
    """session 不在内存时从 DB 重建（刷新/重启后兜底）"""
    info = _db.get_session_info(session_id)
    if not info:
        raise HTTPException(404, f"session not found: {session_id}")
    from app.pipeline.domain import Session, Score, ScoreMeta
    sess = Session()
    sess.id             = session_id
    sess.pipeline_state = info.get("pipeline_state", "idle")
    abc = info.get("abc_notation") or ""
    if abc:
        meta = ScoreMeta(
            title=info.get("score_title") or "",
            key=info.get("score_key") or "C",
            bpm=float(info.get("score_bpm") or 120),
            note_count=int(info.get("score_notes") or 0),
        )
        sess.score = Score(title=meta.title, abc_notation=abc, meta=meta)
    # 恢复 workspace_id / project_id 到内存 Session（工具调用链路依赖）
    sess.workspace_id = (info.get("workspace_id") or "").strip()
    sess.project_id   = (info.get("project_id")   or "").strip()
    _info_extra = info.get("extra") or {}
    if isinstance(_info_extra, dict):
        sess.extra = _info_extra
    service.save_session(sess)
    _db.upsert_session(
        session_id, score=sess.score, pipeline_state=sess.pipeline_state,
        workspace_id=info.get("workspace_id"), project_id=info.get("project_id"),
        title=info.get("title", "新对话"),
        extra=_info_extra if isinstance(_info_extra, dict) else None,
    )
    return info


def _ensure_project_binding(session_id: str, req_ws_id: str, req_proj_id: str):
    """守门：确保 session 的 workspace_id / project_id 写入 DB，返回 (final_ws_id, final_proj_id, role_id)"""
    _chat_info  = _db.get_session_info(session_id)
    _db_proj_id = (_chat_info.get("project_id")   or "").strip() if _chat_info else ""
    _db_ws_id   = (_chat_info.get("workspace_id") or "").strip() if _chat_info else ""
    _req_proj   = (req_proj_id or "").strip()
    _req_ws     = (req_ws_id   or "").strip()

    final_proj = _req_proj or _db_proj_id
    final_ws   = _req_ws   or _db_ws_id

    # ws_id 缺失时从 projects 表反查
    if final_proj and not final_ws:
        try:
            _pi = _db.get_project_info(final_proj)
            if _pi:
                final_ws = (_pi.get("workspace_id") or "").strip()
        except Exception:
            pass

    # 确保 project 行存在（防 FOREIGN KEY constraint failed）
    if final_proj and final_ws:
        try:
            if not _db.get_project_info(final_proj):
                _db.ensure_project(final_proj, final_ws)
                _logger.info("[chat] 守门补建 project row: proj=%s ws=%s", final_proj, final_ws)
        except Exception as _pe:
            _logger.warning("[chat] 守门补建 project 失败: %s", _pe)

    need_update = (
        (final_proj and final_proj != _db_proj_id) or
        (final_ws   and final_ws   != _db_ws_id)
    )
    if need_update:
        _db.upsert_session(session_id, workspace_id=final_ws or None, project_id=final_proj or None)
        _chat_info = _db.get_session_info(session_id)
    elif not final_proj:
        _logger.warning("[chat] session %s 无 project_id，工具调用将失败", session_id)

    # 从 extra 读取 role_id
    role_id = None
    try:
        if _chat_info:
            _extra = _chat_info.get("extra") or {}
            if isinstance(_extra, str):
                try: _extra = json.loads(_extra)
                except Exception: _extra = {}
            role_id = _extra.get("role_id") or None
    except Exception:
        pass

    return final_ws, final_proj, role_id


@router.post("/sessions/{session_id}/chat")
async def universal_chat(session_id: str, req: UniversalChatRequest):
    try:
        service.get_session(session_id)
    except KeyError:
        _rebuild_session_from_db(session_id)

    try:
        final_ws, final_proj, role_id = _ensure_project_binding(
            session_id, req.workspace_id, req.project_id
        )
    except Exception as _ge:
        _logger.warning("[chat] 守门异常 session=%s: %s", session_id, _ge)
        final_proj = req.project_id
        role_id    = None

    # BUG-034 修复：请求体中直接传入的 role_id 优先于 DB session.extra 中的值
    if req.role_id:
        role_id = req.role_id

    async def _run():
        try:
            await service.universal_chat(
                session_id=session_id,
                message=req.message,
                project_id=final_proj,
                attachment_content=req.attachment_content,
                attachment_name=req.attachment_name,
                attachment_workspace_path=req.attachment_workspace_path,
                attachment_b64=req.attachment_b64,
                publish=_make_publisher(session_id),
                role_id=role_id,
            )
        except Exception as e:
            await _publish(session_id, "error", {"message": str(e)})
            await _publish(session_id, "pipeline.state", {"state": "idle", "_error": True})
            try:
                _db.upsert_session(session_id, pipeline_state="idle")
            except Exception:
                pass

    _abort_events[session_id] = asyncio.Event()
    task = asyncio.create_task(_run())
    _running_tasks[session_id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(session_id, None))
    return {"status": "accepted", "session_id": session_id}


# ── Abort ─────────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/abort")
async def abort_session(session_id: str):
    ev = _abort_events.get(session_id)
    if ev:
        ev.set()
    task = _running_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
    await _publish(session_id, "run.aborted", {"message": "用户已中断对话"}, display=False)
    await _publish(session_id, "pipeline.state", {"state": "idle", "_aborted": True})
    try:
        _db.upsert_session(session_id, pipeline_state="idle")
    except Exception:
        pass
    return {"status": "aborted", "session_id": session_id}


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions_route():
    try:
        return {"sessions": _db.list_sessions(limit=50)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    try:
        return {"session_id": session_id, "messages": _db.get_session_messages(session_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/sessions/{session_id}/todos")
async def get_todos(session_id: str):
    try:
        return {"session_id": session_id, "todos": _db.get_session_todos(session_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Export ────────────────────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    format:     str
    instrument: int = 0


@router.post("/sessions/{session_id}/export")
async def export_score(session_id: str, req: ExportRequest):
    try:
        data, filename, mime = await service.export_score(session_id, req.format, req.instrument)
        try:
            filename.encode("ascii")
            cd = f"attachment; filename={filename}"
        except UnicodeEncodeError:
            from urllib.parse import quote
            cd = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(content=data, media_type=mime, headers={"Content-Disposition": cd})
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Context usage ─────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/context")
async def get_context_usage(session_id: str):
    try:
        msgs        = _db.get_session_messages(session_id)
        total_chars = sum(len(m.get("content") or "") for m in msgs)
        for m in msgs:
            tc = m.get("tool_calls")
            if isinstance(tc, list):
                total_chars += len(json.dumps(tc, ensure_ascii=False))
            elif isinstance(tc, str):
                total_chars += len(tc)
        _CPT       = 2.5
        est_tokens = int(total_chars / _CPT)
        ctx_limit  = _llm.get_model_context_limit(config.LLM_MODEL)
        pct        = min(99, round(est_tokens / ctx_limit * 100))
        return {"session_id": session_id, "msg_count": len(msgs),
                "total_chars": total_chars, "est_tokens": est_tokens,
                "ctx_limit": ctx_limit, "model": config.LLM_MODEL, "pct": pct}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Role ──────────────────────────────────────────────────────────────────────

class RoleSwitchRequest(BaseModel):
    role_id: str


@router.post("/sessions/{session_id}/role")
async def switch_role(session_id: str, req: RoleSwitchRequest):
    from app.agentcore.role_config import get_role, DEFAULT_ROLE_ID
    role = get_role(req.role_id)
    if not role:
        raise HTTPException(404, f"角色 '{req.role_id}' 不存在")
    if not role.enabled:
        raise HTTPException(400, f"角色 '{role.name}' 尚未启用")
    try:
        _db.upsert_session(session_id, extra={"role_id": req.role_id})
        try:
            mem_sess = service.get_session(session_id)
            if not isinstance(mem_sess.extra, dict):
                mem_sess.extra = {}
            mem_sess.extra["role_id"] = req.role_id
            service.save_session(mem_sess)
        except KeyError:
            pass
    except Exception as e:
        raise HTTPException(500, f"角色切换失败: {e}")
    return {"session_id": session_id, "role_id": role.id, "role_name": role.name,
            "greeting": role.greeting, "domains": role.domains, "icon": role.icon, "color": role.color}


@router.get("/sessions/{session_id}/role")
async def get_session_role(session_id: str):
    from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID
    try:
        info  = _db.get_session_info(session_id)
        extra = info.get("extra") or {} if info else {}
        if isinstance(extra, str):
            try: extra = json.loads(extra)
            except Exception: extra = {}
        role_id = extra.get("role_id", DEFAULT_ROLE_ID)
    except Exception:
        role_id = DEFAULT_ROLE_ID
    role = get_role_or_default(role_id)
    return {"role_id": role.id, "role_name": role.name, "icon": role.icon,
            "color": role.color, "domains": role.domains, "greeting": role.greeting}
