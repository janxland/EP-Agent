"""
FastAPI 路由层 + SSE Hub
对应原 Go 版 pipeline/interfaces/http/
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
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

            # 2. 推送历史对话消息（role=user / assistant / tool），恢复右侧对话框
            history_msgs = _db.get_session_messages(session_id)
            seq = 1
            for msg in history_msgs:
                role = msg.get("role", "")
                content = msg.get("content", "") or ""
                # 过滤：只处理 user / assistant / tool 三种角色
                # assistant 消息允许 content 为空（纯工具调用轮次 content=""，但有 tool_calls）
                if role not in ("user", "assistant", "tool"):
                    continue
                # user / tool 消息：空内容跳过，避免空气泡
                if role != "assistant" and not content.strip():
                    continue

                evt_type = "message.history"   # 专用类型，前端区分历史与实时
                payload: dict = {
                    "role":    role,
                    "content": content,
                    "msg_id":  msg.get("id", ""),
                }
                # assistant 消息：还原 tool_calls 字段（前端渲染工具卡片的关键）
                # tool message 通过 tool_call_id 匹配 assistant.tool_calls[].id，
                # 若 assistant 消息没有 tool_calls，工具卡片无法关联，刷新后工具结果体消失。
                if role == "assistant":
                    raw_tc = msg.get("tool_calls")
                    if raw_tc:
                        try:
                            tc_list = json.loads(raw_tc) if isinstance(raw_tc, str) else raw_tc
                            if tc_list:
                                payload["tool_calls"] = tc_list
                        except Exception:
                            pass
                    # 纯工具调用轮次（content="" 但有 tool_calls）：有 tool_calls 才推送
                    if not content.strip() and not payload.get("tool_calls"):
                        continue
                # tool 消息：附带 tool_call_id 和 name，前端用于与 assistant tool_calls 匹配
                if role == "tool":
                    payload["tool_call_id"] = msg.get("tool_call_id", "")
                    payload["name"]         = msg.get("tool_name", "")
                hist_evt = {
                    "id": new_id("evt"),
                    "type": evt_type,
                    "session_id": session_id,
                    "display": True,
                    "sequence": seq,
                    "timestamp": msg.get("created_at", datetime.now(timezone.utc).isoformat()),
                    "payload": payload,
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
                seq += 1
            except Exception:
                pass

            # 5. 推送 pipeline.state（恢复前端 running/idle 状态，防止刷新后永久 loading）
            # 若 pipeline_state == "running"，说明上次任务未完成（后端重启/崩溃），
            # 此时推送 idle 让前端解除 loading，避免用户无法再发消息
            try:
                _pipeline_state = (session_info or {}).get("pipeline_state", "idle")
                # 后端重启后内存中无此 session，running 状态应重置为 idle
                _effective_state = "idle" if _pipeline_state == "running" else _pipeline_state
                state_evt = {
                    "id": new_id("evt"),
                    "type": "pipeline.state",
                    "session_id": session_id,
                    "display": False,
                    "sequence": seq,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "state":    _effective_state,
                        "_replay":  True,
                    },
                }
                yield f"data: {json.dumps(state_evt, ensure_ascii=False)}\n\n"
                seq += 1
            except Exception:
                pass

            # 6. 推送工作区谱子文件列表（workspace.scores）
            # 工作区文件是跨会话的持久资产，新建会话后谱子依然存在
            try:
                _ws_id_replay = (session_info or {}).get("workspace_id") or ""
                if _ws_id_replay:
                    from app.agentcore.tools.workspace_tools import list_workspace_scores_impl
                    from app.agentcore.session_context import set_current_session_id as _set_sid
                    # 注入 session_id，让 list_workspace_scores_impl 通过 ContextVar 推断项目根目录
                    _set_sid(session_id)
                    _scores = list_workspace_scores_impl()
                    if _scores:
                        scores_evt = {
                            "id": new_id("evt"),
                            "type": "workspace.scores",
                            "session_id": session_id,
                            "display": False,
                            "sequence": seq,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "payload": {
                                "workspace_id": _ws_id_replay,
                                "scores":       _scores,
                                "_replay":      True,
                            },
                        }
                        yield f"data: {json.dumps(scores_evt, ensure_ascii=False)}\n\n"
                        seq += 1
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
    """列出所有工作区（含三层结构：projects → sessions）"""
    workspaces = _db.list_workspaces()
    result = []
    for ws in workspaces:
        projects = _db.list_projects(ws["id"])          # 含嵌套 sessions
        all_sessions = [s for p in projects for s in p.get("sessions", [])]
        result.append({**ws, "projects": projects, "sessions": all_sessions})
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

# ─── Project CRUD ─────────────────────────────────────────────
# 三层架构：Workspace → Project → Session(Topic)
# Project 是文件系统隔离边界，Session 只能操作所属 Project 的文件

class CreateProjectRequest(BaseModel):
    name: str = "新项目"
    description: str = ""

class RenameProjectRequest(BaseModel):
    name: str

@router.get("/workspaces/{ws_id}/projects")
async def list_projects_route(ws_id: str):
    """列出工作区下所有项目（含嵌套 sessions）"""
    projects = _db.list_projects(ws_id)
    return {"workspace_id": ws_id, "projects": projects}

@router.post("/workspaces/{ws_id}/projects", status_code=201)
async def create_project_route(ws_id: str, req: CreateProjectRequest):
    """在指定工作区下创建新项目，自动创建文件系统目录"""
    # 确认工作区存在
    ws_list = _db.list_workspaces()
    if not any(w["id"] == ws_id for w in ws_list):
        raise HTTPException(404, f"workspace not found: {ws_id}")
    proj = _db.create_project(ws_id, req.name, req.description)
    return proj

@router.patch("/projects/{proj_id}")
async def rename_project_route(proj_id: str, req: RenameProjectRequest):
    """重命名项目"""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name 不能为空")
    ok = _db.rename_project(proj_id, name)
    if not ok:
        raise HTTPException(404, f"project not found: {proj_id}")
    return {"ok": True, "project_id": proj_id, "name": name}

@router.delete("/projects/{proj_id}", status_code=204)
async def delete_project_route(proj_id: str):
    """删除项目及其下所有 sessions（级联）"""
    proj = _db.get_project_info(proj_id)
    if not proj:
        raise HTTPException(404, f"project not found: {proj_id}")
    # 收集该项目下所有 session_id，用于清理内存
    sessions_in_proj = _db.list_projects(proj["workspace_id"])
    for p in sessions_in_proj:
        if p["id"] == proj_id:
            for sess in p.get("sessions", []):
                try:
                    service.remove_session_from_memory(sess["id"])
                except Exception:
                    pass
            break
    _db.delete_project(proj_id)

@router.get("/projects/{proj_id}")
async def get_project_route(proj_id: str):
    """获取项目详情"""
    proj = _db.get_project_info(proj_id)
    if not proj:
        raise HTTPException(404, f"project not found: {proj_id}")
    return proj

# ─── Session ──────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    workspace_id: str = ""
    project_id: str = ""      # 所属项目 ID（空 = 使用工作区默认项目）
    title: str = "新对话"

@router.post("/sessions", status_code=201)
async def create_session(req: CreateSessionRequest = CreateSessionRequest()):
    """
    创建新对话（Session/Topic）。
    project_id 为空时：若工作区有项目则使用第一个项目；否则自动创建默认项目。
    """
    ws_id = req.workspace_id or None
    proj_id = req.project_id or None

    # 自动关联项目：若未指定 project_id，但指定了 workspace_id，则使用/创建默认项目
    if ws_id and not proj_id:
        projects = _db.list_projects(ws_id)
        if projects:
            proj_id = projects[0]["id"]   # 使用最近更新的项目
        else:
            # 工作区下没有项目，自动创建默认项目
            default_proj = _db.create_project(ws_id, "默认项目", "自动创建的默认项目")
            proj_id = default_proj["id"]

    # 一次性写入 workspace_id + project_id，消灭两步写入竞态
    sess = service.create_session(
        workspace_id=ws_id,
        project_id=proj_id,
        title=req.title,
    )
    return {
        "session_id":  sess.id,
        "workspace_id": ws_id,
        "project_id":  proj_id,
        "title":       req.title,
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
            "project_id":     info.get("project_id"),   # ← fix22: 补上 project_id，工具层 ContextVar 依赖此字段
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
            "project_id":     info.get("project_id"),   # ← fix22: 补上 project_id
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
    workspace_id: str = ""   # 工作区 ID（必带，与 project_id 共同定位文件系统路径）
    project_id: str = ""    # 项目 ID（必带，工具层文件隔离边界）
    attachment_content: str = ""
    attachment_name: str = ""
    attachment_workspace_path: str = ""
    attachment_b64: str = ""

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
        # 重建后同步一次，确保 workspace_id / project_id / extra（含 role_id）不丢失
        _db.upsert_session(
            session_id,
            score=sess.score,
            pipeline_state=sess.pipeline_state,
            workspace_id=info.get("workspace_id"),
            project_id=info.get("project_id"),   # ← 必须带上，防止重建后 project_id 丢失
            title=info.get("title", "新对话"),
            extra=_info_extra if isinstance(_info_extra, dict) else None,
        )

    # ── workspace_id / project_id 守门 + 自动修复 ────────────────────────────
    # 三重保障：
    #   1. 前端传来的值优先写入（fix12 新前端）
    #   2. DB 已有值直接复用（session 创建时已写入）
    #   3. 若 ws_id 缺失但 proj_id 有值，从 projects 表反查 ws_id（兼容旧 session）
    import logging as _logging
    _chat_logger = _logging.getLogger(__name__)
    try:
        _chat_info   = _db.get_session_info(session_id)
        _db_proj_id  = (_chat_info.get("project_id")   or "").strip() if _chat_info else ""
        _db_ws_id    = (_chat_info.get("workspace_id") or "").strip() if _chat_info else ""
        _req_proj_id = (req.project_id   or "").strip()
        _req_ws_id   = (req.workspace_id or "").strip()

        # 最终使用的 proj_id / ws_id：前端传值 > DB 已有值
        _final_proj_id = _req_proj_id or _db_proj_id
        _final_ws_id   = _req_ws_id   or _db_ws_id

        # 自动修复：ws_id 缺失但 proj_id 有值 → 从 projects 表反查
        if _final_proj_id and not _final_ws_id:
            try:
                _proj_info = _db.get_project_info(_final_proj_id)
                if _proj_info:
                    _final_ws_id = (_proj_info.get("workspace_id") or "").strip()
                    print(f"[EP-Agent] /chat 守门: 从 projects 反查 ws_id={_final_ws_id!r} (proj={_final_proj_id!r})", flush=True)
            except Exception:
                pass

        # 有任何字段需要更新时写入 DB
        _need_update = (
            (_final_proj_id and _final_proj_id != _db_proj_id) or
            (_final_ws_id   and _final_ws_id   != _db_ws_id)
        )
        if _need_update:
            _db.upsert_session(session_id,
                               workspace_id=_final_ws_id or None,
                               project_id=_final_proj_id or None)
            print(f"[EP-Agent] /chat 守门: session={session_id} ws={_final_ws_id!r} proj={_final_proj_id!r} 已写入DB", flush=True)
            _chat_logger.info("[chat] session %s 绑定 ws=%s proj=%s", session_id, _final_ws_id, _final_proj_id)
            _chat_info = _db.get_session_info(session_id)
        elif not _final_proj_id:
            _chat_logger.warning("[chat] session %s 无 project_id，工具调用将失败", session_id)
        else:
            print(f"[EP-Agent] /chat 守门: session={session_id} ws={_final_ws_id!r} proj={_final_proj_id!r} DB已是最新", flush=True)
    except Exception as _ge:
        _chat_logger.warning("[chat] project_id 守门异常 session=%s: %s", session_id, _ge)
        _chat_info = None

    # 从 session extra 读取 role_id（复用上方已查询的 _chat_info，避免重复查 DB）
    _role_id: str | None = None
    try:
        if _chat_info:
            _extra = _chat_info.get("extra") or {}
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
                project_id=_final_proj_id,   # 使用守门修复后的最终值，而非 req 原始值
                attachment_content=req.attachment_content,
                attachment_name=req.attachment_name,
                attachment_workspace_path=req.attachment_workspace_path,
                attachment_b64=req.attachment_b64,
                publish=_make_publisher(session_id),
                role_id=_role_id,
            )
        except Exception as e:
            # 异常时：推送错误事件 + 重置 pipeline_state 为 idle（防止刷新后永久 loading）
            await _publish(session_id, "error", {"message": str(e)})
            await _publish(session_id, "pipeline.state", {"state": "idle", "_error": True})
            try:
                _db.upsert_session(session_id, pipeline_state="idle")
            except Exception:
                pass

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


# ─── 工作区文件系统 API ────────────────────────────────────────────────────────
# 前端文件树和 Agent 工具共用同一套存储。
# 路径规则：data/workspace/{ws_id}/projects/{proj_id}/

from pathlib import Path as _Path
import base64 as _base64
import mimetypes as _mimetypes

_WS_FILE_ROOT = _Path(__file__).resolve().parent.parent.parent / "data" / "workspace"
_WS_FILE_ROOT.mkdir(parents=True, exist_ok=True)

_BLOCKED_EXTS = {".py", ".sh", ".bash", ".exe", ".bat", ".cmd", ".ps1"}
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


def _ws_safe_path(workspace_id: str, rel: str, project_id: str = "") -> _Path:
    """安全路径解析，防止路径遍历，支持中文/特殊字符文件名。"""
    import os as _os
    if project_id:
        base = (_WS_FILE_ROOT / workspace_id / "projects" / project_id).resolve()
    else:
        base = (_WS_FILE_ROOT / workspace_id).resolve()
    norm_rel = _os.path.normpath(rel)
    if norm_rel.startswith("..") or norm_rel.startswith("/") or norm_rel.startswith("\\"):
        raise HTTPException(400, "路径越界")
    target = base / norm_rel
    target_abs = _os.path.abspath(str(target))
    base_abs   = _os.path.abspath(str(base))
    if not target_abs.startswith(base_abs + _os.sep) and target_abs != base_abs:
        raise HTTPException(400, "路径越界")
    return _Path(target_abs)


@router.get("/workspaces/{workspace_id}/files")
async def list_ws_files(workspace_id: str, project_id: str = "", subdir: str = ""):
    """列出项目文件树（有 project_id 时限定在项目目录内，同时合并 ws 级 shared/ 目录）"""
    def _file_entry(p: _Path, base: _Path):
        rel = str(p.relative_to(base))
        ext = p.suffix.lower().lstrip(".")
        mime, _ = _mimetypes.guess_type(str(p))
        return {
            "path": rel,
            "name": p.name,
            "ext":  ext,
            "size": p.stat().st_size,
            "mime": mime or "application/octet-stream",
            "is_text": p.suffix.lower() in {
                ".abc", ".txt", ".md", ".json", ".html", ".htm",
                ".css", ".js", ".ts", ".xml", ".yaml", ".yml",
                ".csv", ".svg", ".log",
            },
        }

    if project_id:
        base = _WS_FILE_ROOT / workspace_id / "projects" / project_id
    else:
        base = _WS_FILE_ROOT / workspace_id
    scan = (base / subdir) if subdir else base

    files = []

    # 扫描项目目录，如实返回真实文件树结构，不做任何目录合并
    if scan.exists():
        for p in sorted(scan.rglob("*")):
            if p.is_file():
                files.append(_file_entry(p, base))

    return {"workspace_id": workspace_id, "project_id": project_id, "files": files}


@router.get("/workspaces/{workspace_id}/files/content")
async def get_ws_file(workspace_id: str, path: str, encoding: str = "text", project_id: str = ""):
    """读取项目文件内容（encoding=text 返回文本，encoding=base64 返回 b64，encoding=raw 直接返回二进制）"""
    import mimetypes as _mimetypes
    from fastapi.responses import Response as _Response
    target = _ws_safe_path(workspace_id, path, project_id)
    if not target.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    if encoding == "raw":
        data = target.read_bytes()
        mime, _ = _mimetypes.guess_type(str(target))
        return _Response(content=data, media_type=mime or "application/octet-stream")
    if encoding == "base64":
        data = target.read_bytes()
        return {"path": path, "content": _base64.b64encode(data).decode("ascii"), "encoding": "base64"}
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace"), "encoding": "text"}


class WsFileWriteRequest(BaseModel):
    path: str
    content: str
    encoding: str = "text"   # "text" | "base64"


@router.put("/workspaces/{workspace_id}/files")
async def put_ws_file(workspace_id: str, req: WsFileWriteRequest, project_id: str = ""):
    """写入/创建项目文件（支持中文/特殊字符文件名）"""
    import logging as _logging
    _log = _logging.getLogger("ep_agent.ws_files")
    try:
        ext = _Path(req.path).suffix.lower()
        if ext in _BLOCKED_EXTS:
            raise HTTPException(400, f"禁止写入 {ext} 类型文件")
        target = _ws_safe_path(workspace_id, req.path, project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if req.encoding == "base64":
            try:
                data = _base64.b64decode(req.content)
            except Exception as b64_err:
                raise HTTPException(400, f"base64 解码失败：{b64_err}")
            if len(data) > _MAX_UPLOAD_BYTES:
                raise HTTPException(400, "文件超过 20MB 限制")
            target.write_bytes(data)
            return {"ok": True, "path": req.path, "size": len(data)}
        else:
            raw = req.content.encode("utf-8")
            if len(raw) > _MAX_UPLOAD_BYTES:
                raise HTTPException(400, "文件超过 20MB 限制")
            target.write_text(req.content, encoding="utf-8")
            return {"ok": True, "path": req.path, "size": len(raw)}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("[put_ws_file] 写入失败 workspace=%s path=%r", workspace_id, req.path)
        raise HTTPException(500, f"文件写入失败：{e}")


@router.post("/workspaces/{workspace_id}/files/upload")
async def upload_ws_file(
    workspace_id: str,
    file: UploadFile = File(...),
    path: str = Form(...),
    project_id: str = Form(""),
):
    """
    multipart/form-data 上传二进制文件（音频、图片等大文件走此接口）。
    支持最大 200MB，避免 base64 JSON 请求体超限问题。
    """
    import logging as _logging
    _log = _logging.getLogger("ep_agent.ws_files")
    try:
        ext = _Path(path).suffix.lower()
        if ext in _BLOCKED_EXTS:
            raise HTTPException(400, f"禁止上传 {ext} 类型文件")
        target = _ws_safe_path(workspace_id, path, project_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        # 流式读取，避免大文件全量加载到内存
        _MAX_MULTIPART_BYTES = 200 * 1024 * 1024  # 200 MB
        written = 0
        with target.open("wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                written += len(chunk)
                if written > _MAX_MULTIPART_BYTES:
                    target.unlink(missing_ok=True)
                    raise HTTPException(400, "文件超过 200MB 限制")
                f.write(chunk)

        _log.info("[upload_ws_file] 上传成功 workspace=%s path=%r size=%d", workspace_id, path, written)
        return {"ok": True, "path": path, "size": written}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("[upload_ws_file] 上传失败 workspace=%s path=%r", workspace_id, path)
        raise HTTPException(500, f"文件上传失败：{e}")


@router.delete("/workspaces/{workspace_id}/files")
async def delete_ws_file(workspace_id: str, path: str, project_id: str = ""):
    """删除项目文件或目录（目录递归删除）"""
    import shutil as _shutil
    target = _ws_safe_path(workspace_id, path, project_id)
    if not target.exists():
        return {"ok": True, "message": "文件不存在（已删除）"}
    if target.is_dir():
        _shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": path}


class WsFileCopyRequest(BaseModel):
    src_path: str
    dst_path: str


@router.post("/workspaces/{workspace_id}/files/copy")
async def copy_ws_file(workspace_id: str, req: WsFileCopyRequest, project_id: str = ""):
    """复制项目文件到新路径"""
    import shutil as _shutil
    src = _ws_safe_path(workspace_id, req.src_path, project_id)
    dst = _ws_safe_path(workspace_id, req.dst_path, project_id)
    if not src.exists():
        raise HTTPException(404, f"源文件不存在：{req.src_path}")
    _BLOCKED = {".py", ".sh", ".bash", ".exe", ".bat", ".cmd", ".ps1"}
    if dst.suffix.lower() in _BLOCKED:
        raise HTTPException(400, f"禁止复制为 {dst.suffix} 类型文件")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(src, dst)
    return {"ok": True, "src": req.src_path, "dst": req.dst_path, "size": dst.stat().st_size}


# ─── 模型列表 ─────────────────────────────────────────────────────────────────

# 预设模型列表（兼容 OpenAI-compatible API，按能力分组）
_MODELS = [
    # ── 旗舰推理模型 ──
    {"id": "claude-opus-4-5",          "name": "Claude Opus 4.5",      "group": "旗舰",   "desc": "Anthropic 最强模型，复杂推理/长文本"},
    {"id": "claude-sonnet-4-5",        "name": "Claude Sonnet 4.5",    "group": "旗舰",   "desc": "性能与速度均衡，推荐日常使用"},
    {"id": "gpt-4o",                   "name": "GPT-4o",               "group": "旗舰",   "desc": "OpenAI 多模态旗舰，视觉+文本"},
    {"id": "gpt-4o-mini",              "name": "GPT-4o Mini",          "group": "快速",   "desc": "低延迟，适合简单任务"},
    {"id": "o3-mini",                  "name": "o3-mini",              "group": "推理",   "desc": "OpenAI 推理模型，数学/代码"},
    {"id": "deepseek-v3",              "name": "DeepSeek V3",          "group": "旗舰",   "desc": "深度求索旗舰，中文优化"},
    {"id": "deepseek-r1",              "name": "DeepSeek R1",          "group": "推理",   "desc": "深度思考，复杂推理任务"},
    # ── 国产模型 ──
    {"id": "Qwen/Qwen2.5-72B-Instruct","name": "Qwen2.5-72B",         "group": "国产",   "desc": "阿里通义千问，中文能力强"},
    {"id": "Qwen/QwQ-32B",             "name": "QwQ-32B",              "group": "推理",   "desc": "通义推理模型"},
    {"id": "THUDM/glm-4-9b-chat",      "name": "GLM-4-9B",             "group": "国产",   "desc": "清华智谱，轻量快速"},
    # ── 当前配置 ──
    {"id": "__current__",              "name": "当前配置",              "group": "默认",   "desc": f"使用 .env 配置的模型"},
]

@router.get("/models")
async def list_models():
    """返回可用模型列表，__current__ 替换为实际配置值"""
    from app.config import config as _cfg
    models = []
    for m in _MODELS:
        item = dict(m)
        if item["id"] == "__current__":
            item["id"]   = _cfg.LLM_MODEL
            item["name"] = f"{_cfg.LLM_MODEL.split('/')[-1]}"
            item["desc"] = f"当前配置：{_cfg.LLM_MODEL}"
            item["current"] = True
        models.append(item)
    return {"models": models, "active": _cfg.LLM_MODEL}


@router.patch("/models/active")
async def set_active_model(body: dict):
    """运行时切换模型（写入 config + 重建 LLM 客户端，重启后失效）"""
    from app.config import config as _cfg
    from app.agentcore.llm import reset_client
    model_id = body.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(400, "model_id is required")
    _cfg.LLM_MODEL = model_id
    reset_client()
    return {"ok": True, "active": model_id}


# ─── 上下文占用 ────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/context")
async def get_context_usage(session_id: str):
    """
    估算当前会话的上下文占用情况。
    基于 messages 表的字符数，按 1 token ≈ 2.5 字符估算（中文保守值），上限 128k token。
    与 memory_manager.py 的 _CHARS_PER_TOKEN = 2.5 保持一致。
    """
    try:
        msgs = _db.get_session_messages(session_id)
        total_chars = sum(len(m.get("content") or "") for m in msgs)
        # tool_calls JSON 也占 context（可能是 list 或 str）
        for m in msgs:
            tc = m.get("tool_calls")
            if isinstance(tc, list):
                import json as _json
                total_chars += len(_json.dumps(tc, ensure_ascii=False))
            elif isinstance(tc, str):
                total_chars += len(tc)
        _CHARS_PER_TOKEN = 2.5          # 中文约 1.5~2 字/token，保守取 2.5
        est_tokens  = int(total_chars / _CHARS_PER_TOKEN)
        ctx_limit   = 128_000           # 128k token 上限
        pct         = min(99, round(est_tokens / ctx_limit * 100))
        return {
            "session_id":   session_id,
            "msg_count":    len(msgs),
            "total_chars":  total_chars,
            "est_tokens":   est_tokens,
            "ctx_limit":    ctx_limit,
            "pct":          pct,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


class WsFileRenameRequest(BaseModel):
    src_path: str
    new_name: str   # 仅文件名，不含路径分隔符


@router.post("/workspaces/{workspace_id}/files/rename")
async def rename_ws_file(workspace_id: str, req: WsFileRenameRequest, project_id: str = ""):
    """重命名项目文件（保持同目录）"""
    if "/" in req.new_name or "\\" in req.new_name:
        raise HTTPException(400, "new_name 不能含路径分隔符")
    _BLOCKED = {".py", ".sh", ".bash", ".exe", ".bat", ".cmd", ".ps1"}
    if _Path(req.new_name).suffix.lower() in _BLOCKED:
        raise HTTPException(400, f"禁止重命名为 {_Path(req.new_name).suffix} 类型文件")
    base = (_WS_FILE_ROOT / workspace_id / "projects" / project_id) if project_id else (_WS_FILE_ROOT / workspace_id)
    src = _ws_safe_path(workspace_id, req.src_path, project_id)
    if not src.exists():
        raise HTTPException(404, f"文件不存在：{req.src_path}")
    dst = src.parent / req.new_name
    _ws_safe_path(workspace_id, str(dst.relative_to(base)), project_id)
    src.rename(dst)
    new_path = str(dst.relative_to(base))
    return {"ok": True, "src": req.src_path, "dst": new_path}


class WsFileMoveRequest(BaseModel):
    src_path: str
    dst_path: str   # 目标完整相对路径（含文件名），可跨目录


@router.post("/workspaces/{workspace_id}/files/move")
async def move_ws_file(workspace_id: str, req: WsFileMoveRequest, project_id: str = ""):
    """移动项目文件到新路径（可跨目录，相当于 rename + mkdir）"""
    import shutil as _shutil
    _BLOCKED = {".py", ".sh", ".bash", ".exe", ".bat", ".cmd", ".ps1"}
    if _Path(req.dst_path).suffix.lower() in _BLOCKED:
        raise HTTPException(400, f"禁止移动为 {_Path(req.dst_path).suffix} 类型文件")
    src = _ws_safe_path(workspace_id, req.src_path, project_id)
    dst = _ws_safe_path(workspace_id, req.dst_path, project_id)
    if not src.exists():
        raise HTTPException(404, f"源文件不存在：{req.src_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _shutil.move(str(src), str(dst))
    return {"ok": True, "src": req.src_path, "dst": req.dst_path, "size": dst.stat().st_size}
