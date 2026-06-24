"""
数据库迁移脚本 v2
- 为旧 sessions 表添加 workspace_id / title 字段
- 创建 workspaces 表（如不存在）
- 将无 workspace_id 的旧 session 关联到默认工作区

用法（在 backend/ 目录下执行）：
    python scripts/migrate_db.py
"""
import sqlite3
import os
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ep_agent.db"

def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None

def migrate():
    if not DB_PATH.exists():
        print(f"[migrate] DB 不存在: {DB_PATH}，无需迁移（首次启动会自动创建）")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=OFF")   # 迁移期间关闭外键约束
    now = datetime.now().isoformat()

    # ── 1. 创建 workspaces 表 ────────────────────────────────────────────────
    if not table_exists(conn, "workspaces"):
        print("[migrate] 创建 workspaces 表...")
        conn.execute("""
            CREATE TABLE workspaces (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL DEFAULT '新工作区',
                description  TEXT DEFAULT '',
                created_at   TEXT,
                updated_at   TEXT
            )
        """)
        conn.commit()
        print("[migrate] workspaces 表创建完成")
    else:
        print("[migrate] workspaces 表已存在，跳过")

    # ── 2. 为 sessions 表添加 workspace_id 列 ───────────────────────────────
    if not column_exists(conn, "sessions", "workspace_id"):
        print("[migrate] 为 sessions 添加 workspace_id 列...")
        conn.execute("ALTER TABLE sessions ADD COLUMN workspace_id TEXT")
        conn.commit()
        print("[migrate] workspace_id 列添加完成")
    else:
        print("[migrate] workspace_id 列已存在，跳过")

    # ── 3. 为 sessions 表添加 title 列 ──────────────────────────────────────
    if not column_exists(conn, "sessions", "title"):
        print("[migrate] 为 sessions 添加 title 列...")
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT '新对话'")
        conn.commit()
        print("[migrate] title 列添加完成")
    else:
        print("[migrate] title 列已存在，跳过")

    # ── 4. 创建默认工作区，关联旧 session ───────────────────────────────────
    orphan_count = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE workspace_id IS NULL OR workspace_id = ''"
    ).fetchone()[0]

    if orphan_count > 0:
        print(f"[migrate] 发现 {orphan_count} 个未关联工作区的 session，创建默认工作区...")
        default_ws_id = f"ws_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO workspaces (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            (default_ws_id, "默认工作区", "迁移自旧数据", now, now),
        )
        conn.execute(
            "UPDATE sessions SET workspace_id=?, title=COALESCE(NULLIF(score_title,''), '新对话') WHERE workspace_id IS NULL OR workspace_id=''",
            (default_ws_id,),
        )
        conn.commit()
        print(f"[migrate] 已创建默认工作区 {default_ws_id}，关联 {orphan_count} 个 session")
    else:
        print("[migrate] 所有 session 已有 workspace_id，跳过")

    # ── 5. 创建索引（幂等）────────────────────────────────────────────────────
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_id)")
    conn.commit()

    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()
    print("[migrate] 迁移完成 ✓")

if __name__ == "__main__":
    migrate()
