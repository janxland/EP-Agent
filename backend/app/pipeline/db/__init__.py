"""
db 包入口 —— re-export 所有公共符号，保持向后兼容。

原 db.py（1559行单文件）已拆分为以下子模块：
  _connection.py  —— 连接池 + Schema DDL + 自动迁移
  workspace.py    —— Workspace / Project CRUD
  session.py      —— Session upsert / query / delete / 存档清理
  message.py      —— Message / ToolCall / Todo 落库
  audit.py        —— Trace / Span / Fixture / Replay CRUD
  workflow.py     —— WorkflowTemplate / Run / StepLog CRUD

所有调用方的 import 路径无需修改：
  from app.pipeline.db import get_db, init_db, upsert_session, ...
"""
from __future__ import annotations

# ── 连接 & 初始化 ──────────────────────────────────────────────────────────────
from ._connection import (
    get_db,
    init_db,
)

# ── Workspace / Project ────────────────────────────────────────────────────────
from .workspace import (
    create_workspace,
    rename_workspace,
    delete_workspace,
    list_workspaces,
    create_project,
    rename_project,
    delete_project,
    list_projects,
    ensure_project,
    get_project_info,
)

# ── Session ────────────────────────────────────────────────────────────────────
from .session import (
    upsert_session,
    async_upsert_session,
    get_session_info,
    list_sessions,
    rename_session,
    delete_session,
    delete_session_cascade,
    mark_session_archived,
    delete_archived_sessions,
    get_workspace_sessions,
)

# ── Message / ToolCall / Todo ──────────────────────────────────────────────────
from .message import (
    insert_message,
    async_insert_message,
    upsert_tool_call,
    async_upsert_tool_call,
    upsert_todos,
    async_upsert_todos,
    get_session_messages,
    get_session_todos,
)

# ── Audit: Trace / Span / Fixture / Replay ────────────────────────────────────
from .audit import (
    insert_trace,
    insert_span,
    insert_fixture,
    get_trace,
    get_traces_by_session,
    search_traces,
    get_trace_stats,
    get_spans_by_trace,
    get_fixtures_by_trace,
    insert_replay,
    update_replay_status,
    get_replay,
    get_replays_by_source_trace,
    delete_traces_by_session,
)

# ── Workflow: Template / Run / StepLog ────────────────────────────────────────
from .workflow import (
    insert_workflow_template,
    get_workflow_template,
    list_workflow_templates,
    update_workflow_template_status,
    insert_workflow_run,
    update_workflow_run,
    get_workflow_run,
    list_workflow_runs,
    insert_workflow_step_log,
    get_workflow_step_logs,
    get_workflow_run_counts,
)

__all__ = [
    # connection
    "get_db",
    "init_db",
    # workspace
    "create_workspace",
    "rename_workspace",
    "delete_workspace",
    "list_workspaces",
    "create_project",
    "rename_project",
    "delete_project",
    "list_projects",
    "ensure_project",
    "get_project_info",
    # session
    "upsert_session",
    "async_upsert_session",
    "get_session_info",
    "list_sessions",
    "rename_session",
    "delete_session",
    "delete_session_cascade",
    "mark_session_archived",
    "delete_archived_sessions",
    "get_workspace_sessions",
    # message
    "insert_message",
    "async_insert_message",
    "upsert_tool_call",
    "async_upsert_tool_call",
    "upsert_todos",
    "async_upsert_todos",
    "get_session_messages",
    "get_session_todos",
    # audit
    "insert_trace",
    "insert_span",
    "insert_fixture",
    "get_trace",
    "get_traces_by_session",
    "search_traces",
    "get_trace_stats",
    "get_spans_by_trace",
    "get_fixtures_by_trace",
    "insert_replay",
    "update_replay_status",
    "get_replay",
    "get_replays_by_source_trace",
    "delete_traces_by_session",
    # workflow
    "insert_workflow_template",
    "get_workflow_template",
    "list_workflow_templates",
    "update_workflow_template_status",
    "insert_workflow_run",
    "update_workflow_run",
    "get_workflow_run",
    "list_workflow_runs",
    "insert_workflow_step_log",
    "get_workflow_step_logs",
    "get_workflow_run_counts",
]
