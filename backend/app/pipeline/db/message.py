"""
Message / ToolCall / Todo 落库

职责：
  - _ensure_session_exists：落库保底，防止外键约束静默丢消息
  - insert_message / async_insert_message
  - upsert_tool_call / async_upsert_tool_call
  - upsert_todos / async_upsert_todos
  - get_session_messages / get_session_todos
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from ._connection import get_db

_logger = logging.getLogger("ep_agent.db")


# ─── 落库保底 ─────────────────────────────────────────────────────────────────

def _ensure_session_exists(
    db,
    session_id: str,
    workspace_id: str = "",
    project_id: str = "",
) -> None:
    """
    落库保底：若 sessions 表中不存在该 session_id，则插入最简骨架记录。

    设计原则：
      - messages 表有 FOREIGN KEY (session_id) REFERENCES sessions(id)
      - 当 sessions 记录缺失时，INSERT OR IGNORE 会静默丢弃消息
      - 骨架记录携带已知的 workspace_id / project_id，避免写入 NULL
      - 若 session 已存在但 ws/proj 为空，顺手补全
    """
    existing = db.execute(
        "SELECT id, workspace_id, project_id FROM sessions WHERE id=?", (session_id,)
    ).fetchone()

    if existing:
        ex_ws   = (existing["workspace_id"] or "").strip()
        ex_proj = (existing["project_id"]   or "").strip()
        need_patch = (workspace_id and not ex_ws) or (project_id and not ex_proj)
        if need_patch:
            now = datetime.now().isoformat()
            db.execute(
                "UPDATE sessions SET "
                "workspace_id = CASE WHEN (workspace_id IS NULL OR workspace_id='') AND ? != '' THEN ? ELSE workspace_id END, "
                "project_id   = CASE WHEN (project_id  IS NULL OR project_id ='') AND ? != '' THEN ? ELSE project_id  END, "
                "updated_at   = ? WHERE id=?",
                (workspace_id, workspace_id, project_id, project_id,
                 datetime.now().isoformat(), session_id),
            )
            db.commit()
            _logger.info(
                "[DB] session %s 补全 ws=%s proj=%s",
                session_id, workspace_id or ex_ws, project_id or ex_proj,
            )
        return

    # session 不存在：插入骨架记录
    now = datetime.now().isoformat()
    _proj = project_id or None
    _ws   = workspace_id or ""
    try:
        db.execute("""
            INSERT OR IGNORE INTO sessions
                (id, workspace_id, project_id, title, pipeline_state, extra, created_at, updated_at)
            VALUES (?, ?, ?, '恢复对话', 'idle', '{}', ?, ?)
        """, (session_id, _ws, _proj, now, now))
        db.commit()
        _logger.warning(
            "[DB] session %s 不存在，已自动创建骨架记录 ws=%s proj=%s（消息落库保底）",
            session_id, _ws, _proj,
        )
    except Exception as e:
        _logger.error("[DB] session %s 骨架记录创建失败: %s", session_id, e)


# ─── Message ──────────────────────────────────────────────────────────────────

def insert_message(
    msg_id: str,
    session_id: str,
    role: str,
    content: str = "",
    tool_calls: list | None = None,
    tool_call_id: str = "",
    tool_name: str = "",
    reasoning: str = "",
    workspace_id: str = "",
    project_id: str = "",
) -> None:
    db = get_db()
    now = datetime.now().isoformat()

    # 若调用方未传 ws/proj，从 ContextVar 推断
    _ws   = workspace_id
    _proj = project_id
    if not _ws or not _proj:
        try:
            from app.agentcore.session_context import (
                get_current_workspace_id, get_current_project_id,
            )
            _ws   = _ws   or get_current_workspace_id()
            _proj = _proj or get_current_project_id()
        except Exception:
            pass

    _ensure_session_exists(db, session_id, workspace_id=_ws, project_id=_proj)
    db.execute("""
        INSERT OR IGNORE INTO messages
            (id, session_id, role, content, tool_calls, tool_call_id, tool_name, reasoning, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        msg_id, session_id, role, content,
        json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
        tool_call_id, tool_name, reasoning, now,
    ))
    db.commit()


async def async_insert_message(**kwargs) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: insert_message(**kwargs))


def get_session_messages(session_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── ToolCall ─────────────────────────────────────────────────────────────────

def upsert_tool_call(
    call_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict | None = None,
    result: str = "",
    status: str = "running",
    error: str = "",
) -> None:
    db = get_db()
    now = datetime.now().isoformat()
    db.execute("""
        INSERT INTO tool_calls
            (id, session_id, tool_name, arguments, result, status, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            result=excluded.result, status=excluded.status,
            error=excluded.error, updated_at=excluded.updated_at
    """, (
        call_id, session_id, tool_name,
        json.dumps(arguments, ensure_ascii=False) if arguments else None,
        result, status, error, now, now,
    ))
    db.commit()


async def async_upsert_tool_call(**kwargs) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: upsert_tool_call(**kwargs))


# ─── Todo ─────────────────────────────────────────────────────────────────────

def upsert_todos(
    session_id: str,
    todos: list[dict],
    turn_id: str = "",
    domain: str = "",
    summary: str = "",
) -> None:
    db = get_db()
    now = datetime.now().isoformat()
    for todo in todos:
        db.execute("""
            INSERT INTO todos
                (id, session_id, turn_id, title, detail, status, domain, summary, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id, session_id) DO UPDATE SET
                status=excluded.status, updated_at=excluded.updated_at
        """, (
            todo.get("id", ""), session_id, turn_id,
            todo.get("title", ""), todo.get("detail", ""),
            todo.get("status", "pending"),
            domain, summary, now, now,
        ))
    db.commit()


async def async_upsert_todos(**kwargs) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: upsert_todos(**kwargs))


def get_session_todos(session_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM todos WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]
