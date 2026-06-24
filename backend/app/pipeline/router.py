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
from app.pipeline import db as _db

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

        # ── Replay 历史消息（参考 magic-coding writeTaskSSE 的 backlog 机制）──
        # 刷新后客户端重新连接，先把已落库的历史推送给前端，恢复对话状态。
        try:
            # 1. 若 session 有 abc_notation，先推送 abc.updated 恢复谱子
            session_info = _db.get_session_info(session_id)
            if session_info and session_info.get("abc_notation"):
                abc_evt = {
                    "id": new_id("evt"),
                    "type": "abc.updated",
                    "session_id": session_id,
                    "display": True,
                    "sequence": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "abc": session_info["abc_notation"],
                        "version": 1,
                        "summary": session_info.get("score_title", ""),
                        "meta": {
                            "title":      session_info.get("score_title", ""),
                            "key":        session_info.get("score_key", "C"),
                            "bpm":        session_info.get("score_bpm", 120),
                            "note_count": session_info.get("score_notes", 0),
                        },
                        "_replay": True,   # 前端可用此字段跳过动画
                    },
                }
                yield f"data: {json.dumps(abc_evt, ensure_ascii=False)}\n\n"

            # 2. 推送历史对话消息（role=user / assistant），恢复右侧对话框
            history_msgs = _db.get_session_messages(session_id)
            seq = 1
            for msg in history_msgs:
                role = msg.get("role", "")
                content = msg.get("content", "") or ""
                if role not in ("user", "assistant") or not content.strip():
                    continue
                evt_type = "message.history"   # 专用类型，前端区分历史与实时
                hist_evt = {
                    "id": new_id("evt"),
                    "type": evt_type,
                    "session_id": session_id,
                    "display": True,
                    "sequence": seq,
                    "timestamp": msg.get("created_at", datetime.now(timezone.utc).isoformat()),
                    "payload": {
                        "role":    role,
                        "content": content,
                        "msg_id":  msg.get("id", ""),
                    },
                }
                yield f"data: {json.dumps(hist_evt, ensure_ascii=False)}\n\n"
                seq += 1

            # 3. 推送历史 TODO（恢复任务规划展示，含 domain 字段）
            todos = _db.get_session_todos(session_id)
            if todos:
                todo_items = [
                    {
                        "id":     t.get("id", ""),
                        "title":  t.get("title", ""),
                        "detail": t.get("detail", ""),
                        "status": t.get("status", "done"),
                    }
                    for t in todos
                ]
                # 从最新的 todo 记录中恢复 domain/summary
                last_todo = todos[-1] if todos else {}
                todo_evt = {
                    "id": new_id("evt"),
                    "type": "todo.list",
                    "session_id": session_id,
                    "display": True,
                    "sequence": seq,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "todos":   todo_items,
                        "domain":  last_todo.get("domain", ""),
                        "summary": last_todo.get("summary", ""),
                        "_replay": True,
                    },
                }
                yield f"data: {json.dumps(todo_evt, ensure_ascii=False)}\n\n"
                seq += 1

            # 4. 推送 role.active（恢复角色状态，前端顶栏角色徽章刷新后不复位）
            try:
                from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID
                _sess_extra = session_info.get("extra") or {} if session_info else {}
                if isinstance(_sess_extra, str):
                    try:
                        import json as _j
                        _sess_extra = _j.loads(_sess_extra)
                    except Exception:
                        _sess_extra = {}
                _role_id_replay = _sess_extra.get("role_id", DEFAULT_ROLE_ID)
                _role_replay = get_role_or_default(_role_id_replay)
                role_evt = {
                    "id": new_id("evt"),
                    "type": "role.active",
                    "session_id": session_id,
                    "display": False,
                    "sequence": seq,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "role_id":   _role_replay.id,
                        "role_name": _role_replay.name,
                        "icon":      _role_replay.icon,
                        "color":     _role_replay.color,
                        "_replay":   True,
                    },
                }
                yield f"data: {json.dumps(role_evt, ensure_ascii=False)}\n\n"
            except Exception:
                pass

        except Exception as e:
            # replay 失败不中断连接，仅记录日志
            import logging
            logging.getLogger(__name__).warning(f"SSE replay failed for {session_id}: {e}")

        # ── 实时事件循环 ──
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

# ─── Workspace CRUD ───────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str = "新工作区"
    description: str = ""

@router.get("/workspaces")
async def list_workspaces_route():
    """列出所有工作区（含每个工作区的 session 列表）"""
    workspaces = _db.list_workspaces()
    result = []
    for ws in workspaces:
        sessions = _db.get_workspace_sessions(ws["id"])
        result.append({**ws, "sessions": sessions})
    return {"workspaces": result}

@router.post("/workspaces", status_code=201)
async def create_workspace_route(req: CreateWorkspaceRequest):
    ws = _db.create_workspace(req.name, req.description)
    return ws

@router.patch("/workspaces/{ws_id}")
async def rename_workspace_route(ws_id: str, req: CreateWorkspaceRequest):
    ok = _db.rename_workspace(ws_id, req.name)
    if not ok:
        raise HTTPException(404, f"workspace not found: {ws_id}")
    return {"ok": True}

@router.delete("/workspaces/{ws_id}", status_code=204)
async def delete_workspace_route(ws_id: str):
    # 先收集该工作区下所有 session id，用于清理内存
    sessions_in_ws = _db.get_workspace_sessions(ws_id)
    ok = _db.delete_workspace(ws_id)
    if not ok:
        raise HTTPException(404, f"workspace not found: {ws_id}")
    # 清理内存中属于该工作区的所有 session（防止内存泄漏 + stale 访问）
    for sess_info in sessions_in_ws:
        try:
            service.remove_session_from_memory(sess_info["id"])
        except Exception:
            pass

# ─── Session ──────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    workspace_id: str = ""
    title: str = "新对话"

@router.post("/sessions", status_code=201)
async def create_session(req: CreateSessionRequest = CreateSessionRequest()):
    # 一次写入：直接将 workspace_id/title 传入 service，避免二次落库
    sess = service.create_session(
        workspace_id=req.workspace_id or None,
        title=req.title,
    )
    return {
        "session_id": sess.id,
        "workspace_id": req.workspace_id or None,
        "title": req.title,
    }

@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        sess = service.get_session(session_id)
        info = _db.get_session_info(session_id) or {}
        return {
            "id":             sess.id,          # 前端 SessionInfoDto 用 id
            "session_id":     sess.id,          # 兼容旧代码
            "pipeline_state": sess.pipeline_state,
            "workspace_id":   info.get("workspace_id"),
            "title":          info.get("title", "新对话"),
            "score_title":    info.get("score_title"),
            "score_key":      info.get("score_key"),
            "score_bpm":      info.get("score_bpm"),
            "score_notes":    info.get("score_notes"),
            "created_at":     info.get("created_at"),
            "updated_at":     info.get("updated_at"),
        }
    except KeyError:
        # session 不在内存中，尝试从 DB 恢复基本信息
        info = _db.get_session_info(session_id)
        if not info:
            raise HTTPException(404, f"session not found: {session_id}")
        return {
            "id":             session_id,       # 前端 SessionInfoDto 用 id
            "session_id":     session_id,       # 兼容旧代码
            "pipeline_state": info.get("pipeline_state", "idle"),
            "workspace_id":   info.get("workspace_id"),
            "title":          info.get("title", "新对话"),
            "score_title":    info.get("score_title"),
            "score_key":      info.get("score_key"),
            "score_bpm":      info.get("score_bpm"),
            "score_notes":    info.get("score_notes"),
            "created_at":     info.get("created_at"),
            "updated_at":     info.get("updated_at"),
            "stale":          True,  # 内存中无此 session，需重建
        }

class RenameSessionRequest(BaseModel):
    title: str

@router.patch("/sessions/{session_id}")
async def rename_session_route(session_id: str, req: RenameSessionRequest):
    """重命名对话标题"""
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "title 不能为空")
    ok = _db.rename_session(session_id, title)
    if not ok:
        raise HTTPException(404, f"session not found: {session_id}")
    return {"ok": True, "session_id": session_id, "title": title}

@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_route(session_id: str):
    """删除 session（DB + 内存），级联清理 messages / todos"""
    _db.delete_session_cascade(session_id)
    # 通过 service 公开方法清理内存，避免直接访问私有变量
    try:
        service.remove_session_from_memory(session_id)
    except Exception:
        pass

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
    结果通过 SSE stream 推送，此接口仅返回 202 Accepted。

    刷新恢复策略：
      内存中无 session 时，先从 SQLite 重建空 session（保留 score 状态），
      而非直接返回 404，确保刷新后仍可继续对话。
    """
    try:
        service.get_session(session_id)
    except KeyError:
        # session 不在内存（刷新/重启后），尝试从 DB 重建
        info = _db.get_session_info(session_id)
        if not info:
            raise HTTPException(404, f"session not found: {session_id}")
        # 重建内存 session（含历史谱子）
        from app.pipeline.domain import Session, Score, ScoreMeta
        sess = Session()
        sess.id = session_id
        sess.pipeline_state = info.get("pipeline_state", "idle")
        abc = info.get("abc_notation") or ""
        if abc:
            meta = ScoreMeta(
                title=info.get("score_title") or "",
                key=info.get("score_key") or "C",
                bpm=float(info.get("score_bpm") or 120),
                note_count=int(info.get("score_notes") or 0),
            )
            sess.score = Score(
                title=meta.title,
                abc_notation=abc,
                meta=meta,
            )
        # 同步 extra 到内存 session（含 role_id），确保 universal_runner 能读到
        _info_extra = info.get("extra") or {}
        if isinstance(_info_extra, dict):
            sess.extra = _info_extra
        service.save_session(sess)
        # 重建后同步一次，确保 workspace_id / extra（含 role_id）不丢失
        _db.upsert_session(
            session_id,
            score=sess.score,
            pipeline_state=sess.pipeline_state,
            workspace_id=info.get("workspace_id"),
            title=info.get("title", "新对话"),
            extra=_info_extra if isinstance(_info_extra, dict) else None,
        )

    # 从 session extra 读取 role_id，透传给 universal_chat → universal_runner
    _role_id: str | None = None
    try:
        _sess_info = _db.get_session_info(session_id)
        if _sess_info:
            _extra = _sess_info.get("extra") or {}
            if isinstance(_extra, str):
                import json as _json
                try:
                    _extra = _json.loads(_extra)
                except Exception:
                    _extra = {}
            _role_id = _extra.get("role_id") or None
    except Exception:
        pass

    # 在后台异步执行，结果全部通过 SSE 推送
    async def _run():
        try:
            await service.universal_chat(
                session_id=session_id,
                message=req.message,
                attachment_content=req.attachment_content,
                attachment_name=req.attachment_name,
                attachment_b64=req.attachment_b64,
                publish=_make_publisher(session_id),
                role_id=_role_id,
            )
        except Exception as e:
            await _publish(session_id, "error", {"message": str(e)})

    asyncio.create_task(_run())
    return {"status": "accepted", "session_id": session_id}

# ─── History（SQLite 持久化查询）────────────────────────────────

@router.get("/sessions")
async def list_sessions_route():
    """列出最近 50 个 session（从 SQLite）"""
    try:
        sessions = _db.list_sessions(limit=50)
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """查询 session 的历史消息（从 SQLite）"""
    try:
        msgs = _db.get_session_messages(session_id)
        return {"session_id": session_id, "messages": msgs}
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/sessions/{session_id}/todos")
async def get_todos(session_id: str):
    """查询 session 的 TODO 列表（从 SQLite）"""
    try:
        todos = _db.get_session_todos(session_id)
        return {"session_id": session_id, "todos": todos}
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


# ─── 健康检查专家系统 ──────────────────────────────────────────

@router.get("/health")
async def health_check():
    """快速心跳（前端 30s 轮询，< 10ms 响应）。"""
    from app.agentcore.tools import get_tool_names
    from app.agentcore.domain_config import list_domains
    try:
        tool_count   = len(get_tool_names())
        domain_count = len(list_domains(enabled_only=True))
        tools_ok     = tool_count > 0
    except Exception:
        tool_count = domain_count = 0
        tools_ok   = False
    return {
        "status":       "ok" if tools_ok else "degraded",
        "tools_ok":     tools_ok,
        "tool_count":   tool_count,
        "domain_count": domain_count,
    }


@router.get("/health/tools")
async def health_tools():
    """
    工具注册表端点。
    前端 ToolRegistry 从此处动态加载工具 icon/label/description，
    消除前端硬编码工具映射漂移。
    """
    from app.agentcore.tools import get_tool_names, get_tool_schemas, get_registered_groups

    def _infer_icon(name: str) -> str:
        if "transpose" in name or "key" in name:  return "🎵"
        if "tempo" in name or "bpm" in name:      return "⏱️"
        if "midi" in name:                        return "🎹"
        if "sky" in name or "convert" in name:    return "🎮"
        if "audio" in name or "suno" in name:     return "🎧"
        if "voice" in name or "sovits" in name:   return "🎤"
        if "router" in name or "intent" in name:  return "🧭"
        if "edit" in name or "abc" in name:       return "✏️"
        return "🔧"

    try:
        groups  = get_registered_groups()
        schemas = get_tool_schemas()
        names   = get_tool_names()
        critical = ["abc_transpose", "abc_to_sky_json", "convert_sky_json"]
        critical_checks = {n: ("ok" if n in names else "not_registered") for n in critical}
        tools_ok = all(v == "ok" for v in critical_checks.values())
        tools_list = [
            {
                "name":        s["function"]["name"],
                "label":       s["function"]["name"].replace("_", " "),
                "icon":        _infer_icon(s["function"]["name"]),
                "description": s["function"].get("description", ""),
                "group":       next(
                    (g for g in groups if s["function"]["name"] in get_tool_names(g)),
                    "default"
                ),
            }
            for s in schemas
        ]
    except Exception as e:
        return {"tools_ok": False, "tool_count": 0, "tools": [], "error": str(e)}

    return {
        "tools_ok":        tools_ok,
        "tool_count":      len(names),
        "groups":          groups,
        "tools":           tools_list,
        "critical_checks": critical_checks,
    }


@router.get("/health/domains")
async def health_domains():
    """
    意图域配置端点。
    前端 ChatPanel/TodoListCard 从此处动态加载 DOMAIN_LABEL，
    消除前后端各自维护意图域映射的漂移。
    """
    from app.agentcore.domain_config import to_frontend_map, build_router_prompt
    try:
        domains = to_frontend_map()
        return {
            "domains":       domains,
            "enabled_count": sum(1 for d in domains if d["enabled"]),
            "total_count":   len(domains),
            "router_prompt_preview": build_router_prompt()[:500],
        }
    except Exception as e:
        raise HTTPException(500, f"域配置加载失败: {e}")


# ─── 角色系统 API ──────────────────────────────────────────────

@router.get("/roles")
async def list_roles():
    """
    角色列表端点。
    前端 RoleSwitcher 从此处获取所有可用角色（包含未启用的，前端可灰显）。
    """
    from app.agentcore.role_config import to_frontend_list
    try:
        return {"roles": to_frontend_list()}
    except Exception as e:
        raise HTTPException(500, f"角色配置加载失败: {e}")


class RoleSwitchRequest(BaseModel):
    role_id: str


@router.post("/sessions/{session_id}/role")
async def switch_role(session_id: str, req: RoleSwitchRequest):
    """
    切换 session 绑定的角色。
    - 角色存储在 session 的 extra 字段中（DB 持久化）
    - 不影响已有对话历史，只改变后续路由行为和 Prompt 风格
    - 返回角色的欢迎语，前端可在对话框中展示
    """
    from app.agentcore.role_config import get_role, DEFAULT_ROLE_ID
    role = get_role(req.role_id)
    if not role:
        raise HTTPException(404, f"角色 '{req.role_id}' 不存在")
    if not role.enabled:
        raise HTTPException(400, f"角色 '{role.name}' 尚未启用")

    # 将角色 ID 写入 session extra（upsert 到 DB + 同步内存）
    try:
        _db.upsert_session(session_id, extra={"role_id": req.role_id})
        # 同步到内存 session.extra，确保下一条消息 universal_runner 立即读到新 role_id
        # （universal_runner 优先从内存 session.extra 读取，若不同步会用到旧角色路由）
        try:
            mem_sess = service.get_session(session_id)
            if not isinstance(mem_sess.extra, dict):
                mem_sess.extra = {}
            mem_sess.extra["role_id"] = req.role_id
            service.save_session(mem_sess)
        except KeyError:
            pass  # session 不在内存（已过期/重启），DB 已更新，下次重建时会读到正确值
    except Exception as e:
        raise HTTPException(500, f"角色切换失败: {e}")

    return {
        "session_id": session_id,
        "role_id":    role.id,
        "role_name":  role.name,
        "greeting":   role.greeting,
        "domains":    role.domains,
        "icon":       role.icon,
        "color":      role.color,
    }


@router.get("/sessions/{session_id}/role")
async def get_session_role(session_id: str):
    """
    获取 session 当前绑定的角色。
    前端初始化时调用，恢复上次选择的角色。
    """
    from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID
    try:
        session_info = _db.get_session_info(session_id)
        extra        = session_info.get("extra") or {} if session_info else {}
        if isinstance(extra, str):
            import json as _json
            try:
                extra = _json.loads(extra)
            except Exception:
                extra = {}
        role_id = extra.get("role_id", DEFAULT_ROLE_ID)
    except Exception:
        role_id = DEFAULT_ROLE_ID

    role = get_role_or_default(role_id)
    return {
        "role_id":   role.id,
        "role_name": role.name,
        "icon":      role.icon,
        "color":     role.color,
        "domains":   role.domains,
        "greeting":  role.greeting,
    }
