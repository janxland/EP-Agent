"""
SQLite 持久化层
负责落库：sessions / messages / tool_calls / todos

设计原则：
  - 单文件 SQLite，默认路径 backend/data/ep_agent.db
  - 同步写入（asyncio.run_in_executor 包装），不阻塞 event loop
  - 内存 Session Store 仍是主路径（速度优先），SQLite 仅用于持久化/历史查询
  - 每次写入均 upsert，幂等安全
"""
from __future__ import annotations
import json
import sqlite3
import asyncio
import os
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

# ─── 数据库路径 ────────────────────────────────────────────────────────────────

_DB_DIR  = Path(__file__).resolve().parent.parent.parent / "data"
_DB_PATH = Path(os.getenv("EP_AGENT_DB", str(_DB_DIR / "ep_agent.db")))


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Schema 初始化 ─────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '新工作区',
    description  TEXT DEFAULT '',
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    workspace_id   TEXT,                   -- 所属工作区（可为空，兼容旧数据）
    title          TEXT DEFAULT '新对话',  -- 对话标题（自动从谱子名更新）
    score_title    TEXT,
    score_key      TEXT,
    score_bpm      REAL,
    score_notes    INTEGER,
    abc_notation   TEXT,
    pipeline_state TEXT DEFAULT 'idle',
    extra          TEXT DEFAULT '{}',      -- JSON 扩展字段（存储 role_id 等）
    created_at     TEXT,
    updated_at     TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,          -- user | assistant | tool
    content      TEXT,
    tool_calls   TEXT,                   -- JSON array
    tool_call_id TEXT,                   -- for role=tool
    tool_name    TEXT,                   -- for role=tool
    reasoning    TEXT,
    created_at   TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           TEXT PRIMARY KEY,       -- call_id
    session_id   TEXT NOT NULL,
    tool_name    TEXT,
    arguments    TEXT,                   -- JSON
    result       TEXT,
    status       TEXT DEFAULT 'running', -- running | succeeded | failed
    error        TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS todos (
    id           TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    turn_id      TEXT,
    title        TEXT,
    detail       TEXT,
    status       TEXT DEFAULT 'pending',
    domain       TEXT,
    summary      TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    PRIMARY KEY (id, session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session    ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session  ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_todos_session       ON todos(session_id);
"""
# 注意：idx_sessions_workspace 索引在 _migrate() 中创建，
# 因为旧数据库可能没有 workspace_id 列，放在 DDL 里会报错

# ─── 线程安全连接池（每线程独立连接，避免 SQLite database is locked）──────────
import threading
_local = threading.local()


def _migrate(conn: sqlite3.Connection):
    """自动迁移：为旧 DB 补充缺失的列和表，幂等安全"""
    now = datetime.now().isoformat()

    # 1. 确保 workspaces 表存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '新工作区',
            description  TEXT DEFAULT '',
            created_at   TEXT,
            updated_at   TEXT
        )
    """)

    # 2. 为 sessions 补充缺失列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "workspace_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN workspace_id TEXT")
    if "title" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT '新对话'")
    if "extra" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN extra TEXT DEFAULT '{}'")

    # 3. 将无 workspace_id 的旧 session 关联到默认工作区
    orphans = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE workspace_id IS NULL OR workspace_id=''"
    ).fetchone()[0]
    if orphans > 0:
        default_ws_id = f"ws_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            (default_ws_id, "默认工作区", "迁移自旧数据", now, now),
        )
        conn.execute(
            "UPDATE sessions SET workspace_id=?, title=COALESCE(NULLIF(score_title,''), '新对话') "
            "WHERE workspace_id IS NULL OR workspace_id=''",
            (default_ws_id,),
        )

    # 4. 补充索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_id)")
    conn.commit()


def get_db() -> sqlite3.Connection:
    """每个线程获取独立的 SQLite 连接（thread-local），避免多线程并发锁死。"""
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = _get_conn()
        conn.executescript(_DDL)
        _migrate(conn)
        conn.commit()
        _local.conn = conn
    return conn


def init_db():
    """应用启动时调用，确保 schema 存在（在主线程初始化一次）"""
    get_db()


# ─── Session 落库 ──────────────────────────────────────────────────────────────

# ─── Workspace CRUD ──────────────────────────────────────────────────────────

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
    """
    级联删除该工作区下所有 sessions 及其关联数据。
    手动级联确保兼容未启用 FK 约束的旧 DB。
    """
    db = get_db()
    # 找出所有属于该工作区的 session id
    sess_ids = [
        r[0] for r in
        db.execute("SELECT id FROM sessions WHERE workspace_id=?", (ws_id,)).fetchall()
    ]
    # 按依赖顺序级联删除子表
    for sid in sess_ids:
        db.execute("DELETE FROM messages   WHERE session_id=?", (sid,))
        db.execute("DELETE FROM tool_calls WHERE session_id=?", (sid,))
        db.execute("DELETE FROM todos      WHERE session_id=?", (sid,))
    db.execute("DELETE FROM sessions WHERE workspace_id=?", (ws_id,))
    cur = db.execute("DELETE FROM workspaces WHERE id=?", (ws_id,))
    db.commit()
    return cur.rowcount > 0


def rename_session(session_id: str, title: str) -> bool:
    """重命名对话标题"""
    db = get_db()
    now = datetime.now().isoformat()
    cur = db.execute(
        "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
        (title, now, session_id),
    )
    db.commit()
    return cur.rowcount > 0


def list_workspaces() -> list[dict]:
    """
    列出所有工作区并附带 sessions，通过一次额外查询消除 N+1 问题。
    """
    db = get_db()
    ws_rows = db.execute(
        "SELECT * FROM workspaces ORDER BY updated_at DESC"
    ).fetchall()
    if not ws_rows:
        return []

    # 一次性取出所有 sessions，按 workspace_id 分组
    sess_rows = db.execute(
        "SELECT id, workspace_id, title, score_title, score_key, score_bpm, "
        "score_notes, pipeline_state, created_at, updated_at "
        "FROM sessions ORDER BY updated_at DESC"
    ).fetchall()

    from collections import defaultdict
    sess_by_ws: dict[str, list[dict]] = defaultdict(list)
    for r in sess_rows:
        d = dict(r)
        wsid = d.get("workspace_id") or ""
        if wsid:
            sess_by_ws[wsid].append(d)

    result = []
    for ws in ws_rows:
        wd = dict(ws)
        wd["sessions"] = sess_by_ws.get(wd["id"], [])
        result.append(wd)
    return result


def get_workspace_sessions(ws_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, workspace_id, title, score_title, score_key, score_bpm, "
        "score_notes, pipeline_state, created_at, updated_at "
        "FROM sessions WHERE workspace_id=? ORDER BY updated_at DESC",
        (ws_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Session 落库 ──────────────────────────────────────────────────────────────

def upsert_session(
    session_id: str,
    score=None,
    pipeline_state: str = "idle",
    workspace_id: str | None = None,
    title: str | None = None,
    extra: dict | None = None,
):
    """
    upsert session 记录。
    extra: 扩展 JSON 字段（如 {"role_id": "abc_expert"}）。
           传 None 表示不修改 extra；传 {} 表示清空。
           采用合并策略：只更新传入的 key，不覆盖已有 key。
    """
    db = get_db()
    now = datetime.now().isoformat()
    _title = title or (score.meta.title if score and score.meta.title else "新对话")

    # ── extra 合并策略：读取现有 extra，合并新值 ──────────────────────────────
    extra_json = "{}"
    if extra is not None:
        try:
            existing_row = db.execute(
                "SELECT extra FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            existing_extra: dict = {}
            if existing_row and existing_row[0]:
                try:
                    existing_extra = json.loads(existing_row[0]) or {}
                except Exception:
                    existing_extra = {}
            merged = {**existing_extra, **extra}   # 新值覆盖旧值（合并不清空）
            extra_json = json.dumps(merged, ensure_ascii=False)
        except Exception:
            extra_json = json.dumps(extra, ensure_ascii=False)

    if extra is None:
        # extra 未传：不修改 extra 字段
        db.execute("""
            INSERT INTO sessions (id, workspace_id, title, score_title, score_key, score_bpm,
                                  score_notes, abc_notation, pipeline_state, extra, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id   = COALESCE(NULLIF(excluded.workspace_id,''), workspace_id),
                title          = excluded.title,
                score_title    = excluded.score_title,
                score_key      = excluded.score_key,
                score_bpm      = excluded.score_bpm,
                score_notes    = excluded.score_notes,
                abc_notation   = excluded.abc_notation,
                pipeline_state = excluded.pipeline_state,
                updated_at     = excluded.updated_at
        """, (
            session_id, workspace_id, _title,
            score.meta.title if score else None,
            score.meta.key   if score else None,
            score.meta.bpm   if score else None,
            score.meta.note_count if score else None,
            score.abc_notation if score else None,
            pipeline_state, now, now,
        ))
    else:
        # extra 已传：同时更新 extra 字段（合并后的值）
        db.execute("""
            INSERT INTO sessions (id, workspace_id, title, score_title, score_key, score_bpm,
                                  score_notes, abc_notation, pipeline_state, extra, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id   = COALESCE(NULLIF(excluded.workspace_id,''), workspace_id),
                title          = excluded.title,
                score_title    = excluded.score_title,
                score_key      = excluded.score_key,
                score_bpm      = excluded.score_bpm,
                score_notes    = excluded.score_notes,
                abc_notation   = excluded.abc_notation,
                pipeline_state = excluded.pipeline_state,
                extra          = excluded.extra,
                updated_at     = excluded.updated_at
        """, (
            session_id, workspace_id, _title,
            score.meta.title if score else None,
            score.meta.key   if score else None,
            score.meta.bpm   if score else None,
            score.meta.note_count if score else None,
            score.abc_notation if score else None,
            pipeline_state, extra_json, now, now,
        ))
    db.commit()


async def async_upsert_session(
    session_id: str,
    score=None,
    pipeline_state: str = "idle",
    workspace_id: str | None = None,
    title: str | None = None,
    extra: dict | None = None,
):
    """异步版 upsert_session，签名与同步版保持一致。"""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: upsert_session(session_id, score, pipeline_state, workspace_id, title, extra)
    )


# ─── Message 落库 ──────────────────────────────────────────────────────────────

def insert_message(
    msg_id: str,
    session_id: str,
    role: str,
    content: str = "",
    tool_calls: list | None = None,
    tool_call_id: str = "",
    tool_name: str = "",
    reasoning: str = "",
):
    db = get_db()
    now = datetime.now().isoformat()
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


async def async_insert_message(**kwargs):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: insert_message(**kwargs))


# ─── Tool Call 落库 ────────────────────────────────────────────────────────────

def upsert_tool_call(
    call_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict | None = None,
    result: str = "",
    status: str = "running",
    error: str = "",
):
    db = get_db()
    now = datetime.now().isoformat()
    db.execute("""
        INSERT INTO tool_calls (id, session_id, tool_name, arguments, result, status, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            result     = excluded.result,
            status     = excluded.status,
            error      = excluded.error,
            updated_at = excluded.updated_at
    """, (
        call_id, session_id, tool_name,
        json.dumps(arguments, ensure_ascii=False) if arguments else None,
        result, status, error, now, now,
    ))
    db.commit()


async def async_upsert_tool_call(**kwargs):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: upsert_tool_call(**kwargs))


# ─── TODO 落库 ────────────────────────────────────────────────────────────────

def upsert_todos(
    session_id: str,
    todos: list[dict],
    turn_id: str = "",
    domain: str = "",
    summary: str = "",
):
    db = get_db()
    now = datetime.now().isoformat()
    for todo in todos:
        db.execute("""
            INSERT INTO todos (id, session_id, turn_id, title, detail, status, domain, summary, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id, session_id) DO UPDATE SET
                status     = excluded.status,
                updated_at = excluded.updated_at
        """, (
            todo.get("id", ""), session_id, turn_id,
            todo.get("title", ""), todo.get("detail", ""),
            todo.get("status", "pending"),
            domain, summary, now, now,
        ))
    db.commit()


async def async_upsert_todos(**kwargs):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: upsert_todos(**kwargs))


# ─── 查询接口 ─────────────────────────────────────────────────────────────────

def get_session_messages(session_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_session_todos(session_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM todos WHERE session_id=? ORDER BY created_at ASC",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_sessions(limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, workspace_id, title, score_title, score_key, score_bpm, "
        "score_notes, pipeline_state, created_at, updated_at "
        "FROM sessions ORDER BY updated_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: str) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    db.commit()
    return cur.rowcount > 0


def delete_session_cascade(session_id: str) -> bool:
    """
    级联删除 session 及其所有关联数据（messages / tool_calls / todos）。
    比 delete_session 更彻底，用于前端触发的删除操作。
    SQLite FOREIGN KEY ON DELETE CASCADE 需要 PRAGMA foreign_keys=ON，
    此处手动级联确保兼容旧数据库。
    """
    db = get_db()
    # 按依赖顺序删除：子表先删，父表后删
    db.execute("DELETE FROM messages    WHERE session_id=?", (session_id,))
    db.execute("DELETE FROM tool_calls  WHERE session_id=?", (session_id,))
    db.execute("DELETE FROM todos       WHERE session_id=?", (session_id,))
    cur = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    db.commit()
    return cur.rowcount > 0


def get_session_info(session_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, workspace_id, title, score_title, score_key, score_bpm, "
        "score_notes, abc_notation, pipeline_state, extra, created_at, updated_at "
        "FROM sessions WHERE id=?",
        (session_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    # 确保 extra 始终是 dict（防止 JSON 解析失败导致上层 crash）
    raw_extra = d.get("extra") or "{}"
    if isinstance(raw_extra, str):
        try:
            d["extra"] = json.loads(raw_extra)
        except Exception:
            d["extra"] = {}
    elif not isinstance(raw_extra, dict):
        d["extra"] = {}
    return d
