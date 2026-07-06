"""
Workspace / Project CRUD

职责：
  - Workspace 增删改查（create / rename / delete / list）
  - Project 增删改查（create / rename / delete / list / ensure）
  - list_workspaces：嵌套结构 workspace → projects[] → sessions[]
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ._connection import get_db

# 项目文件系统根目录（相对于 backend/data/workspace/）
_WS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "workspace"


def _ensure_proj_dirs(ws_id: str, proj_id: str) -> None:
    """创建项目文件系统目录（幂等）"""
    try:
        proj_dir = _WS_ROOT / ws_id / "projects" / proj_id
        (proj_dir / ".sky").mkdir(parents=True, exist_ok=True)
        (proj_dir / "shared").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# ─── Workspace ────────────────────────────────────────────────────────────────

def create_workspace(name: str = "新工作区", description: str = "") -> dict:
    db = get_db()
    ws_id = f"ws_{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO workspaces (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
        (ws_id, name, description, now, now),
    )
    db.commit()
    return {"id": ws_id, "name": name, "description": description,
            "created_at": now, "updated_at": now}


def rename_workspace(ws_id: str, name: str) -> bool:
    db = get_db()
    now = datetime.now().isoformat()
    cur = db.execute(
        "UPDATE workspaces SET name=?, updated_at=? WHERE id=?",
        (name, now, ws_id),
    )
    db.commit()
    return cur.rowcount > 0


def delete_workspace(ws_id: str) -> bool:
    """级联删除工作区下所有 sessions 及其关联数据。"""
    db = get_db()
    sess_ids = [
        r[0] for r in
        db.execute("SELECT id FROM sessions WHERE workspace_id=?", (ws_id,)).fetchall()
    ]
    for sid in sess_ids:
        db.execute("DELETE FROM messages   WHERE session_id=?", (sid,))
        db.execute("DELETE FROM tool_calls WHERE session_id=?", (sid,))
        db.execute("DELETE FROM todos      WHERE session_id=?", (sid,))
    db.execute("DELETE FROM sessions WHERE workspace_id=?", (ws_id,))
    cur = db.execute("DELETE FROM workspaces WHERE id=?", (ws_id,))
    db.commit()
    return cur.rowcount > 0


def list_workspaces() -> list[dict]:
    """
    列出所有工作区，附带嵌套结构：workspace → projects[] → sessions[]。
    同时保留顶层 sessions 字段（向后兼容旧前端）。
    """
    db = get_db()
    ws_rows = db.execute("SELECT * FROM workspaces ORDER BY updated_at DESC").fetchall()
    if not ws_rows:
        return []

    ws_ids = [r["id"] for r in ws_rows]
    placeholders = ",".join("?" * len(ws_ids))

    proj_rows = db.execute(
        f"SELECT * FROM projects WHERE workspace_id IN ({placeholders}) ORDER BY updated_at DESC",
        ws_ids,
    ).fetchall()

    sess_rows = db.execute(
        "SELECT id, workspace_id, project_id, title, score_title, score_key, score_bpm, "
        "score_notes, pipeline_state, created_at, updated_at "
        "FROM sessions ORDER BY updated_at DESC"
    ).fetchall()

    proj_by_ws:   dict[str, list[dict]] = defaultdict(list)
    sess_by_proj: dict[str, list[dict]] = defaultdict(list)
    sess_by_ws:   dict[str, list[dict]] = defaultdict(list)

    for p in proj_rows:
        pd = dict(p)
        proj_by_ws[pd["workspace_id"]].append(pd)

    for r in sess_rows:
        d = dict(r)
        pid  = d.get("project_id")  or ""
        wsid = d.get("workspace_id") or ""
        if pid:
            sess_by_proj[pid].append(d)
        if wsid:
            sess_by_ws[wsid].append(d)

    for pd in [p for plist in proj_by_ws.values() for p in plist]:
        pd["sessions"] = sess_by_proj.get(pd["id"], [])

    result = []
    for ws in ws_rows:
        wd = dict(ws)
        wd["projects"] = proj_by_ws.get(wd["id"], [])
        wd["sessions"] = sess_by_ws.get(wd["id"], [])
        result.append(wd)
    return result


# ─── Project ──────────────────────────────────────────────────────────────────

def create_project(ws_id: str, name: str = "新项目", description: str = "") -> dict:
    """在指定工作区下创建新项目，自动创建文件系统目录。"""
    db = get_db()
    proj_id = f"proj_{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO projects (id, workspace_id, name, description, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (proj_id, ws_id, name, description, now, now),
    )
    db.commit()
    _ensure_proj_dirs(ws_id, proj_id)
    return {"id": proj_id, "workspace_id": ws_id, "name": name,
            "description": description, "created_at": now, "updated_at": now}


def rename_project(proj_id: str, name: str) -> bool:
    db = get_db()
    now = datetime.now().isoformat()
    cur = db.execute(
        "UPDATE projects SET name=?, updated_at=? WHERE id=?",
        (name, now, proj_id),
    )
    db.commit()
    return cur.rowcount > 0


def delete_project(proj_id: str) -> bool:
    """级联删除项目及其下所有 sessions。"""
    db = get_db()
    sess_ids = [
        r[0] for r in
        db.execute("SELECT id FROM sessions WHERE project_id=?", (proj_id,)).fetchall()
    ]
    for sid in sess_ids:
        db.execute("DELETE FROM messages   WHERE session_id=?", (sid,))
        db.execute("DELETE FROM tool_calls WHERE session_id=?", (sid,))
        db.execute("DELETE FROM todos      WHERE session_id=?", (sid,))
    db.execute("DELETE FROM sessions WHERE project_id=?", (proj_id,))
    cur = db.execute("DELETE FROM projects WHERE id=?", (proj_id,))
    db.commit()
    return cur.rowcount > 0


def list_projects(ws_id: str) -> list[dict]:
    """列出工作区下所有项目（含嵌套 sessions）。"""
    db = get_db()
    proj_rows = db.execute(
        "SELECT * FROM projects WHERE workspace_id=? ORDER BY updated_at DESC",
        (ws_id,),
    ).fetchall()
    if not proj_rows:
        return []
    proj_ids = [r["id"] for r in proj_rows]
    placeholders = ",".join("?" * len(proj_ids))
    sess_rows = db.execute(
        f"SELECT id, workspace_id, project_id, title, score_title, score_key, score_bpm, "
        f"score_notes, pipeline_state, created_at, updated_at "
        f"FROM sessions WHERE project_id IN ({placeholders}) ORDER BY updated_at DESC",
        proj_ids,
    ).fetchall()
    sess_by_proj: dict[str, list[dict]] = defaultdict(list)
    for r in sess_rows:
        d = dict(r)
        pid = d.get("project_id") or ""
        if pid:
            sess_by_proj[pid].append(d)
    result = []
    for p in proj_rows:
        pd = dict(p)
        pd["sessions"] = sess_by_proj.get(pd["id"], [])
        result.append(pd)
    return result


def get_project_info(proj_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id=?", (proj_id,)).fetchone()
    return dict(row) if row else None


def ensure_project(proj_id: str, ws_id: str, name: str = "默认项目") -> dict:
    """
    确保 project 行在 DB 中存在（幂等）。
    同时确保 workspace 行存在（防止二级外键失败）。
    """
    db = get_db()
    now = datetime.now().isoformat()
    db.execute(
        "INSERT OR IGNORE INTO workspaces (id, name, description, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        (ws_id, "默认工作区", "自动创建", now, now),
    )
    db.execute(
        "INSERT OR IGNORE INTO projects (id, workspace_id, name, description, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (proj_id, ws_id, name, "自动创建", now, now),
    )
    db.commit()
    _ensure_proj_dirs(ws_id, proj_id)
    row = db.execute("SELECT * FROM projects WHERE id=?", (proj_id,)).fetchone()
    return dict(row) if row else {"id": proj_id, "workspace_id": ws_id, "name": name}


def get_project_info(proj_id: str) -> dict | None:
    """按 proj_id 查询单条项目记录，不存在返回 None。"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id=?", (proj_id,)).fetchone()
    return dict(row) if row else None
