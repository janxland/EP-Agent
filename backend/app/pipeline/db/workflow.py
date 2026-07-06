"""
WorkflowTemplate / WorkflowRun / WorkflowStepLog CRUD

职责：
  - insert_workflow_template / get_workflow_template / list_workflow_templates
  - update_workflow_template_status
  - insert_workflow_run / update_workflow_run / get_workflow_run / list_workflow_runs
  - insert_workflow_step_log / get_workflow_step_logs
  - get_workflow_run_counts
"""
from __future__ import annotations

from datetime import datetime

from ._connection import get_db


# ─── 工作流模板 CRUD ───────────────────────────────────────────────────────────

def insert_workflow_template(t: dict) -> None:
    """插入工作流模板（已存在则忽略）。"""
    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO workflow_templates
            (template_id, source_trace_id, name, description, domain,
             trigger_pattern, variables, steps, total_steps, llm_steps,
             pruned_steps, status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        t["template_id"], t["source_trace_id"], t.get("name", ""),
        t.get("description", ""), t.get("domain", ""), t.get("trigger_pattern", ""),
        t.get("variables", "[]"), t.get("steps", "[]"),
        t.get("total_steps", 0), t.get("llm_steps", 0), t.get("pruned_steps", 0),
        t.get("status", "draft"), t.get("created_at", ""), t.get("updated_at", ""),
    ))
    db.commit()


def get_workflow_template(template_id: str) -> dict | None:
    """按 template_id 查询单条模板，不存在返回 None。"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM workflow_templates WHERE template_id=?", (template_id,)
    ).fetchone()
    return dict(row) if row else None


def list_workflow_templates(domain: str = "", limit: int = 50) -> list[dict]:
    """列出未废弃的模板，可按 domain 过滤，按创建时间倒序。"""
    db = get_db()
    if domain:
        rows = db.execute(
            "SELECT * FROM workflow_templates WHERE domain=? AND status!='deprecated' "
            "ORDER BY created_at DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM workflow_templates WHERE status!='deprecated' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_workflow_template_status(template_id: str, status: str) -> None:
    """更新模板状态（draft / active / deprecated）。"""
    db = get_db()
    db.execute(
        "UPDATE workflow_templates SET status=?, updated_at=? WHERE template_id=?",
        (status, datetime.now().isoformat(), template_id),
    )
    db.commit()


# ─── 工作流执行记录 CRUD ───────────────────────────────────────────────────────

def insert_workflow_run(r: dict) -> None:
    """插入工作流执行记录（已存在则忽略）。"""
    db = get_db()
    now = datetime.now().isoformat()
    db.execute("""
        INSERT OR IGNORE INTO workflow_runs
            (run_id, template_id, session_id, variables, status,
             current_step, total_steps, result, error_msg, started_at, ended_at, duration_ms)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        r["run_id"], r["template_id"], r["session_id"],
        r.get("variables", "{}"), r.get("status", "pending"),
        r.get("current_step", 0), r.get("total_steps", 0),
        r.get("result", "{}"), r.get("error_msg", ""),
        r.get("started_at", now), r.get("ended_at", ""), r.get("duration_ms", 0),
    ))
    db.commit()


def update_workflow_run(run_id: str, fields: dict) -> None:
    """动态更新执行记录中的指定字段（fields 为 {列名: 值} 字典）。"""
    if not fields:
        return
    db = get_db()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    db.execute(
        f"UPDATE workflow_runs SET {set_clause} WHERE run_id=?",
        (*fields.values(), run_id),
    )
    db.commit()


def get_workflow_run(run_id: str) -> dict | None:
    """按 run_id 查询单条执行记录，不存在返回 None。"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    return dict(row) if row else None


def list_workflow_runs(
    template_id: str = "",
    session_id: str = "",
    limit: int = 20,
) -> list[dict]:
    """
    列出执行记录，按 started_at 倒序。
    优先按 template_id 过滤，其次按 session_id，都不传则返回全局最新。
    """
    db = get_db()
    if template_id:
        rows = db.execute(
            "SELECT * FROM workflow_runs WHERE template_id=? "
            "ORDER BY started_at DESC LIMIT ?",
            (template_id, limit),
        ).fetchall()
    elif session_id:
        rows = db.execute(
            "SELECT * FROM workflow_runs WHERE session_id=? "
            "ORDER BY started_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── 工作流步骤日志 CRUD ───────────────────────────────────────────────────────

def insert_workflow_step_log(log: dict) -> None:
    """插入单条步骤执行日志（已存在则忽略）。"""
    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO workflow_step_logs
            (log_id, run_id, step_idx, tool_name, args_resolved,
             result, status, duration_ms, started_at, ended_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        log["log_id"], log["run_id"], log["step_idx"],
        log.get("tool_name", ""), log.get("args_resolved", "{}"),
        log.get("result", ""), log.get("status", "ok"),
        log.get("duration_ms", 0), log.get("started_at", ""), log.get("ended_at", ""),
    ))
    db.commit()


def get_workflow_step_logs(run_id: str) -> list[dict]:
    """按 run_id 获取所有步骤日志，按 step_idx 升序排列。"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM workflow_step_logs WHERE run_id=? ORDER BY step_idx ASC",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── 聚合统计 ──────────────────────────────────────────────────────────────────

def get_workflow_run_counts() -> dict:
    """快速统计工作流执行数量（不加载完整记录）。"""
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0]
    succeeded = db.execute(
        "SELECT COUNT(*) FROM workflow_runs WHERE status='succeeded'"
    ).fetchone()[0]
    failed = db.execute(
        "SELECT COUNT(*) FROM workflow_runs WHERE status='failed'"
    ).fetchone()[0]
    avg_dur_row = db.execute(
        "SELECT AVG(duration_ms) FROM workflow_runs "
        "WHERE status='succeeded' AND duration_ms > 0"
    ).fetchone()
    avg_dur = int(avg_dur_row[0]) if avg_dur_row[0] else 0
    return {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "avg_duration_ms": avg_dur,
    }
