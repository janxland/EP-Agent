"""
数据库连接池 + Schema DDL + 自动迁移

职责：
  - 管理 SQLite 连接（thread-local，每线程独立）
  - 定义全量 DDL（建表 + 索引）
  - _migrate()：为旧 DB 幂等补列/补表/补索引
  - get_db() / init_db()：对外唯一连接入口
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

# ─── 数据库路径 ────────────────────────────────────────────────────────────────

_DB_DIR  = Path(__file__).resolve().parent.parent.parent.parent / "data"
_DB_PATH = Path(os.getenv("EP_AGENT_DB", str(_DB_DIR / "ep_agent.db")))


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Schema DDL ───────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '新工作区',
    description  TEXT DEFAULT '',
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '新项目',
    description  TEXT DEFAULT '',
    created_at   TEXT,
    updated_at   TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    workspace_id   TEXT,
    project_id     TEXT,
    title          TEXT DEFAULT '新对话',
    score_title    TEXT,
    score_key      TEXT,
    score_bpm      REAL,
    score_notes    INTEGER,
    abc_notation   TEXT,
    pipeline_state TEXT DEFAULT 'idle',
    extra          TEXT DEFAULT '{}',
    created_at     TEXT,
    updated_at     TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id)   REFERENCES projects(id)   ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT,
    tool_calls   TEXT,
    tool_call_id TEXT,
    tool_name    TEXT,
    reasoning    TEXT,
    created_at   TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    tool_name    TEXT,
    arguments    TEXT,
    result       TEXT,
    status       TEXT DEFAULT 'running',
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

CREATE TABLE IF NOT EXISTS traces (
    trace_id         TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    workspace_id     TEXT DEFAULT '',
    project_id       TEXT DEFAULT '',
    domain           TEXT DEFAULT '',
    role_id          TEXT DEFAULT '',
    user_message     TEXT DEFAULT '',
    attachment_name  TEXT DEFAULT '',
    started_at       TEXT,
    ended_at         TEXT,
    duration_ms      INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'running',
    total_steps      INTEGER DEFAULT 0,
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS spans (
    span_id              TEXT PRIMARY KEY,
    trace_id             TEXT NOT NULL,
    parent_span_id       TEXT DEFAULT '',
    agent_name           TEXT DEFAULT '',
    span_kind            TEXT DEFAULT 'tool',
    round_idx            INTEGER DEFAULT 0,
    step_idx             INTEGER DEFAULT 0,
    tool_name            TEXT DEFAULT '',
    tool_args            TEXT DEFAULT '{}',
    tool_args_hash       TEXT DEFAULT '',
    tool_result          TEXT DEFAULT '{}',
    tool_result_preview  TEXT DEFAULT '',
    attempt              INTEGER DEFAULT 1,
    model                TEXT DEFAULT '',
    temperature          REAL DEFAULT 0.0,
    input_tokens         INTEGER DEFAULT 0,
    output_tokens        INTEGER DEFAULT 0,
    finish_reason        TEXT DEFAULT '',
    started_at           TEXT,
    ended_at             TEXT,
    duration_ms          INTEGER DEFAULT 0,
    status               TEXT DEFAULT 'running',
    error_msg            TEXT DEFAULT '',
    call_id              TEXT DEFAULT '',
    FOREIGN KEY (trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS replay_fixtures (
    fixture_id       TEXT PRIMARY KEY,
    trace_id         TEXT NOT NULL,
    span_id          TEXT NOT NULL,
    tool_name        TEXT NOT NULL,
    tool_args_hash   TEXT NOT NULL,
    tool_result      TEXT DEFAULT '{}',
    FOREIGN KEY (trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS replays (
    replay_id        TEXT PRIMARY KEY,
    source_trace_id  TEXT NOT NULL,
    replay_trace_id  TEXT DEFAULT '',
    session_id       TEXT NOT NULL,
    mode             TEXT DEFAULT 'fixture',
    status           TEXT DEFAULT 'pending',
    diff_summary     TEXT DEFAULT '',
    created_at       TEXT,
    updated_at       TEXT,
    FOREIGN KEY (source_trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_traces_session       ON traces(session_id);
CREATE INDEX IF NOT EXISTS idx_spans_trace          ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_trace       ON replay_fixtures(trace_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_tool_hash   ON replay_fixtures(tool_name, tool_args_hash);
CREATE INDEX IF NOT EXISTS idx_replays_source_trace ON replays(source_trace_id);

CREATE TABLE IF NOT EXISTS workflow_templates (
    template_id      TEXT PRIMARY KEY,
    source_trace_id  TEXT NOT NULL,
    name             TEXT DEFAULT '',
    description      TEXT DEFAULT '',
    domain           TEXT DEFAULT '',
    trigger_pattern  TEXT DEFAULT '',
    variables        TEXT DEFAULT '[]',
    steps            TEXT DEFAULT '[]',
    total_steps      INTEGER DEFAULT 0,
    llm_steps        INTEGER DEFAULT 0,
    pruned_steps     INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'draft',
    created_at       TEXT,
    updated_at       TEXT,
    FOREIGN KEY (source_trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id           TEXT PRIMARY KEY,
    template_id      TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    variables        TEXT DEFAULT '{}',
    status           TEXT DEFAULT 'pending',
    current_step     INTEGER DEFAULT 0,
    total_steps      INTEGER DEFAULT 0,
    result           TEXT DEFAULT '{}',
    error_msg        TEXT DEFAULT '',
    started_at       TEXT,
    ended_at         TEXT,
    duration_ms      INTEGER DEFAULT 0,
    FOREIGN KEY (template_id) REFERENCES workflow_templates(template_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workflow_step_logs (
    log_id           TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    step_idx         INTEGER NOT NULL,
    tool_name        TEXT DEFAULT '',
    args_resolved    TEXT DEFAULT '{}',
    result           TEXT DEFAULT '',
    status           TEXT DEFAULT 'pending',
    duration_ms      INTEGER DEFAULT 0,
    started_at       TEXT,
    ended_at         TEXT,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wf_templates_trace  ON workflow_templates(source_trace_id);
CREATE INDEX IF NOT EXISTS idx_wf_templates_domain ON workflow_templates(domain);
CREATE INDEX IF NOT EXISTS idx_wf_runs_template    ON workflow_runs(template_id);
CREATE INDEX IF NOT EXISTS idx_wf_runs_session     ON workflow_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_wf_step_logs_run    ON workflow_step_logs(run_id);
"""

# ─── 线程安全连接池 ────────────────────────────────────────────────────────────

_local = threading.local()


def _migrate(conn: sqlite3.Connection) -> None:
    """自动迁移：为旧 DB 幂等补列/补表/补索引"""
    now = datetime.now().isoformat()

    # 1. 确保 workspaces / projects 表存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '新工作区',
            description TEXT DEFAULT '', created_at TEXT, updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '新项目', description TEXT DEFAULT '',
            created_at TEXT, updated_at TEXT,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
        )
    """)

    # 2. 为 sessions 补充缺失列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "workspace_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN workspace_id TEXT")
    if "project_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
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

    # 4. 为无 project_id 的旧 session 自动创建默认项目
    ws_without_proj = conn.execute(
        "SELECT DISTINCT workspace_id FROM sessions "
        "WHERE (project_id IS NULL OR project_id='') AND workspace_id IS NOT NULL AND workspace_id!=''"
    ).fetchall()
    for (ws_id,) in ws_without_proj:
        existing_proj = conn.execute(
            "SELECT id FROM projects WHERE workspace_id=? LIMIT 1", (ws_id,)
        ).fetchone()
        if existing_proj:
            proj_id = existing_proj[0]
        else:
            proj_id = f"proj_{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT OR IGNORE INTO projects (id, workspace_id, name, description, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (proj_id, ws_id, "默认项目", "迁移自旧数据", now, now),
            )
        conn.execute(
            "UPDATE sessions SET project_id=? WHERE workspace_id=? AND (project_id IS NULL OR project_id='')",
            (proj_id, ws_id),
        )

    # 5. 为 traces 表补充 workspace_id / project_id 列
    trace_cols = {r[1] for r in conn.execute("PRAGMA table_info(traces)").fetchall()}
    if "workspace_id" not in trace_cols:
        conn.execute("ALTER TABLE traces ADD COLUMN workspace_id TEXT DEFAULT ''")
    if "project_id" not in trace_cols:
        conn.execute("ALTER TABLE traces ADD COLUMN project_id TEXT DEFAULT ''")

    # 6. 确保工作流模板表存在（v2.0 新增）
    for ddl in [
        """CREATE TABLE IF NOT EXISTS workflow_templates (
            template_id TEXT PRIMARY KEY, source_trace_id TEXT NOT NULL,
            name TEXT DEFAULT '', description TEXT DEFAULT '', domain TEXT DEFAULT '',
            trigger_pattern TEXT DEFAULT '', variables TEXT DEFAULT '[]',
            steps TEXT DEFAULT '[]', total_steps INTEGER DEFAULT 0,
            llm_steps INTEGER DEFAULT 0, pruned_steps INTEGER DEFAULT 0,
            status TEXT DEFAULT 'draft', created_at TEXT, updated_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY, template_id TEXT NOT NULL, session_id TEXT NOT NULL,
            variables TEXT DEFAULT '{}', status TEXT DEFAULT 'pending',
            current_step INTEGER DEFAULT 0, total_steps INTEGER DEFAULT 0,
            result TEXT DEFAULT '{}', error_msg TEXT DEFAULT '',
            started_at TEXT, ended_at TEXT, duration_ms INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS workflow_step_logs (
            log_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_idx INTEGER NOT NULL,
            tool_name TEXT DEFAULT '', args_resolved TEXT DEFAULT '{}',
            result TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            duration_ms INTEGER DEFAULT 0, started_at TEXT, ended_at TEXT
        )""",
    ]:
        conn.execute(ddl)

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_wf_templates_trace  ON workflow_templates(source_trace_id)",
        "CREATE INDEX IF NOT EXISTS idx_wf_templates_domain ON workflow_templates(domain)",
        "CREATE INDEX IF NOT EXISTS idx_wf_runs_template    ON workflow_runs(template_id)",
        "CREATE INDEX IF NOT EXISTS idx_wf_runs_session     ON workflow_runs(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_wf_step_logs_run    ON workflow_step_logs(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_workspace  ON sessions(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_project    ON sessions(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_projects_workspace  ON projects(workspace_id)",
    ]:
        conn.execute(idx_sql)

    conn.commit()


def get_db() -> sqlite3.Connection:
    """每个线程获取独立的 SQLite 连接（thread-local），避免多线程并发锁死。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _get_conn()
        conn.executescript(_DDL)
        _migrate(conn)
        conn.commit()
        _local.conn = conn
    return conn


def init_db() -> None:
    """应用启动时调用，确保 schema 存在（在主线程初始化一次）"""
    get_db()
