"""
审计链路 CRUD：Trace / Span / Fixture / Replay

职责：
  - insert_trace / insert_span / insert_fixture
  - get_trace / get_traces_by_session / search_traces / get_trace_stats
  - get_spans_by_trace / get_fixtures_by_trace
  - insert_replay / update_replay_status / get_replay / get_replays_by_source_trace
  - delete_traces_by_session
"""
from __future__ import annotations

from datetime import datetime

from ._connection import get_db


# ─── Trace ────────────────────────────────────────────────────────────────────

def insert_trace(trace: dict) -> None:
    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO traces
            (trace_id, session_id, workspace_id, project_id, domain, role_id,
             user_message, attachment_name, started_at, ended_at, duration_ms,
             status, total_steps, input_tokens, output_tokens)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        trace["trace_id"], trace["session_id"],
        trace.get("workspace_id", ""), trace.get("project_id", ""),
        trace.get("domain", ""), trace.get("role_id", ""),
        trace.get("user_message", ""), trace.get("attachment_name", ""),
        trace.get("started_at", ""), trace.get("ended_at", ""),
        trace.get("duration_ms", 0), trace.get("status", "succeeded"),
        trace.get("total_steps", 0),
        trace.get("input_tokens", 0), trace.get("output_tokens", 0),
    ))
    db.commit()


def get_trace(trace_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    return dict(row) if row else None


def get_traces_by_session(session_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM traces WHERE session_id=? ORDER BY started_at DESC LIMIT ? OFFSET ?",
        (session_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def search_traces(
    session_id: str = "",
    workspace_id: str = "",
    project_id: str = "",
    domain: str = "",
    status: str = "",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """跨 session 全局搜索 trace（支持 workspace_id / project_id / domain / status / keyword 过滤）。"""
    db = get_db()
    conditions, params = [], []
    if session_id:
        conditions.append("session_id=?"); params.append(session_id)
    if workspace_id:
        conditions.append("workspace_id=?"); params.append(workspace_id)
    if project_id:
        conditions.append("project_id=?"); params.append(project_id)
    if domain:
        conditions.append("domain=?"); params.append(domain)
    if status:
        conditions.append("status=?"); params.append(status)
    if keyword:
        conditions.append("user_message LIKE ?"); params.append(f"%{keyword}%")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])
    rows = db.execute(
        f"SELECT * FROM traces {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_trace_stats(
    session_id: str = "",
    workspace_id: str = "",
    project_id: str = "",
) -> dict:
    """统计摘要：总数、各状态计数、token 消耗、平均耗时。支持按 workspace/project/session 过滤。"""
    db = get_db()
    conditions, params = [], []
    if session_id:
        conditions.append("session_id=?"); params.append(session_id)
    if workspace_id:
        conditions.append("workspace_id=?"); params.append(workspace_id)
    if project_id:
        conditions.append("project_id=?"); params.append(project_id)
    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    row = db.execute(f"""
        SELECT COUNT(*) AS total,
            SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
            SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status='running'   THEN 1 ELSE 0 END) AS running,
            SUM(input_tokens)  AS total_input_tokens,
            SUM(output_tokens) AS total_output_tokens,
            AVG(duration_ms)   AS avg_duration_ms
        FROM traces {where}
    """, params).fetchone()
    if not row:
        return {}
    return {
        "total":               row["total"] or 0,
        "succeeded":           row["succeeded"] or 0,
        "failed":              row["failed"] or 0,
        "running":             row["running"] or 0,
        "total_input_tokens":  row["total_input_tokens"] or 0,
        "total_output_tokens": row["total_output_tokens"] or 0,
        "avg_duration_ms":     round(row["avg_duration_ms"] or 0, 1),
    }


def delete_traces_by_session(session_id: str) -> int:
    """删除某 session 的所有 trace/span/fixture，返回删除条数。"""
    db = get_db()
    rows = db.execute(
        "SELECT trace_id FROM traces WHERE session_id=?", (session_id,)
    ).fetchall()
    trace_ids = [r["trace_id"] for r in rows]
    if not trace_ids:
        return 0
    ph = ",".join("?" * len(trace_ids))
    db.execute(f"DELETE FROM replay_fixtures WHERE trace_id IN ({ph})", trace_ids)
    db.execute(f"DELETE FROM spans          WHERE trace_id IN ({ph})", trace_ids)
    db.execute(f"DELETE FROM traces         WHERE trace_id IN ({ph})", trace_ids)
    db.commit()
    return len(trace_ids)


# ─── Span ─────────────────────────────────────────────────────────────────────

def insert_span(span: dict) -> None:
    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO spans
            (span_id, trace_id, parent_span_id, agent_name, span_kind, round_idx, step_idx,
             tool_name, tool_args, tool_args_hash, tool_result, tool_result_preview, attempt,
             model, temperature, input_tokens, output_tokens, finish_reason,
             started_at, ended_at, duration_ms, status, error_msg, call_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        span["span_id"], span["trace_id"], span.get("parent_span_id", ""),
        span.get("agent_name", ""), span.get("span_kind", "tool"),
        span.get("round_idx", 0), span.get("step_idx", 0),
        span.get("tool_name", ""), span.get("tool_args", "{}"),
        span.get("tool_args_hash", ""), span.get("tool_result", "{}"),
        span.get("tool_result_preview", ""), span.get("attempt", 1),
        span.get("model", ""), span.get("temperature", 0.0),
        span.get("input_tokens", 0), span.get("output_tokens", 0),
        span.get("finish_reason", ""), span.get("started_at", ""),
        span.get("ended_at", ""), span.get("duration_ms", 0),
        span.get("status", "ok"), span.get("error_msg", ""),
        span.get("call_id", ""),
    ))
    db.commit()


def get_spans_by_trace(trace_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM spans WHERE trace_id=? ORDER BY step_idx ASC", (trace_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Fixture ──────────────────────────────────────────────────────────────────

def insert_fixture(fixture: dict) -> None:
    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO replay_fixtures
            (fixture_id, trace_id, span_id, tool_name, tool_args_hash, tool_result)
        VALUES (?,?,?,?,?,?)
    """, (
        fixture["fixture_id"], fixture["trace_id"], fixture["span_id"],
        fixture["tool_name"], fixture["tool_args_hash"],
        fixture.get("tool_result", "{}"),
    ))
    db.commit()


def get_fixtures_by_trace(trace_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM replay_fixtures WHERE trace_id=?", (trace_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Replay ───────────────────────────────────────────────────────────────────

def insert_replay(replay: dict) -> None:
    db = get_db()
    now = datetime.now().isoformat()
    db.execute("""
        INSERT OR IGNORE INTO replays
            (replay_id, source_trace_id, replay_trace_id, session_id, mode,
             status, diff_summary, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        replay["replay_id"], replay["source_trace_id"],
        replay.get("replay_trace_id", ""), replay["session_id"],
        replay.get("mode", "fixture"), replay.get("status", "pending"),
        replay.get("diff_summary", ""), now, now,
    ))
    db.commit()


def update_replay_status(
    replay_id: str,
    status: str,
    replay_trace_id: str = "",
    diff_summary: str = "",
) -> None:
    db = get_db()
    now = datetime.now().isoformat()
    db.execute("""
        UPDATE replays
        SET status=?,
            replay_trace_id=COALESCE(NULLIF(?,""), replay_trace_id),
            diff_summary=COALESCE(NULLIF(?,""), diff_summary),
            updated_at=?
        WHERE replay_id=?
    """, (status, replay_trace_id, diff_summary, now, replay_id))
    db.commit()


def get_replay(replay_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM replays WHERE replay_id=?", (replay_id,)).fetchone()
    return dict(row) if row else None


def get_replays_by_source_trace(source_trace_id: str, limit: int = 10) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT replay_id, source_trace_id, replay_trace_id, session_id,
                  mode, status, diff_summary, created_at, updated_at
           FROM replays WHERE source_trace_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (source_trace_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
