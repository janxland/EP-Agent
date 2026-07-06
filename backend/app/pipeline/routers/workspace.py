"""
Workspace & Project CRUD 路由
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.pipeline import db as _db
from app.pipeline import service

router = APIRouter()


# ── Workspace ────────────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str = "新工作区"
    description: str = ""


@router.get("/workspaces")
async def list_workspaces_route():
    """列出所有工作区（含三层结构：projects → sessions）"""
    workspaces = _db.list_workspaces()
    result = []
    for ws in workspaces:
        projects    = _db.list_projects(ws["id"])
        all_sessions = [s for p in projects for s in p.get("sessions", [])]
        result.append({**ws, "projects": projects, "sessions": all_sessions})
    return {"workspaces": result}


@router.post("/workspaces", status_code=201)
async def create_workspace_route(req: CreateWorkspaceRequest):
    return _db.create_workspace(req.name, req.description)


@router.patch("/workspaces/{ws_id}")
async def rename_workspace_route(ws_id: str, req: CreateWorkspaceRequest):
    ok = _db.rename_workspace(ws_id, req.name)
    if not ok:
        raise HTTPException(404, f"workspace not found: {ws_id}")
    return {"ok": True}


@router.delete("/workspaces/{ws_id}", status_code=204)
async def delete_workspace_route(ws_id: str):
    sessions_in_ws = _db.get_workspace_sessions(ws_id)
    ok = _db.delete_workspace(ws_id)
    if not ok:
        raise HTTPException(404, f"workspace not found: {ws_id}")
    for sess_info in sessions_in_ws:
        try:
            service.remove_session_from_memory(sess_info["id"])
        except Exception:
            pass


# ── Project ──────────────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    name: str = "新项目"
    description: str = ""


class RenameProjectRequest(BaseModel):
    name: str


@router.get("/workspaces/{ws_id}/projects")
async def list_projects_route(ws_id: str):
    """列出工作区下所有项目（含嵌套 sessions）"""
    return {"workspace_id": ws_id, "projects": _db.list_projects(ws_id)}


@router.post("/workspaces/{ws_id}/projects", status_code=201)
async def create_project_route(ws_id: str, req: CreateProjectRequest):
    ws_list = _db.list_workspaces()
    if not any(w["id"] == ws_id for w in ws_list):
        raise HTTPException(404, f"workspace not found: {ws_id}")
    return _db.create_project(ws_id, req.name, req.description)


@router.patch("/projects/{proj_id}")
async def rename_project_route(proj_id: str, req: RenameProjectRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name 不能为空")
    ok = _db.rename_project(proj_id, name)
    if not ok:
        raise HTTPException(404, f"project not found: {proj_id}")
    return {"ok": True, "project_id": proj_id, "name": name}


@router.delete("/projects/{proj_id}", status_code=204)
async def delete_project_route(proj_id: str):
    proj = _db.get_project_info(proj_id)
    if not proj:
        raise HTTPException(404, f"project not found: {proj_id}")
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
    proj = _db.get_project_info(proj_id)
    if not proj:
        raise HTTPException(404, f"project not found: {proj_id}")
    return proj
