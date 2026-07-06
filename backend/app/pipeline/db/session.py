"""
Session CRUD

职责：
  - upsert_session / async_upsert_session：写入/更新 session 记录
  - get_session_info：读取单条 session（含 extra JSON 解析）
  - list_sessions / rename_session / delete_session / delete_session_cascade
  - mark_session_archived / delete_archived_sessions：replay 生命周期管理
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from ._connection import get_db


def upsert_session(
    session_id: str,
    score=None,
    pipeline_state: str = "idle",
    workspace_id: str | None = None,
    project_id: str | None = None,
    title: str | None = None,
    extra: dict | None = None,
) -> None:
    """
    upsert session 记录。
    - workspace_id / project_id 传 None 表示不修改（CASE WHEN 保护）
    - extra 传 None 表示不修改；传 dict 表示合并更新（新值覆盖旧值）
    """
    db = get_db()
    now = datetime.now().isoformat()
    _title = title or (score.meta.title if score and score.meta.title else "新对话")

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
            merged = {**existing_extra, **extra}
            extra_json = json.dumps(merged, ensure_ascii=False)
        except Exception:
            extra_json = json.dumps(extra, ensure_ascii=False)

    _base_params = (
        session_id, workspace_id, project_id, _title,
        score.meta.title     if score else None,
        score.meta.key       if score else None,
        score.meta.bpm       if score else None,
        score.meta.note_count if score else None,
        score.abc_notation   if score else None,
        pipeline_state,
    )

    if extra is None:
        db.execute("""
            INSERT INTO sessions
                (id, workspace_id, project_id, title, score_title, score_key, score_bpm,
                 score_notes, abc_notation, pipeline_state, extra, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id   = CASE
                    WHEN excluded.workspace_id IS NOT NULL AND excluded.workspace_id != ''
                    THEN excluded.workspace_id ELSE workspace_id END,
                project_id     = CASE
                    WHEN excluded.project_id IS NOT NULL AND excluded.project_id != ''
                    THEN excluded.project_id ELSE project_id END,
                title          = excluded.title,
                score_title    = excluded.score_title,
                score_key      = excluded.score_key,
                score_bpm      = excluded.score_bpm,
                score_notes    = excluded.score_notes,
                abc_notation   = excluded.abc_notation,
                pipeline_state = excluded.pipeline_state,
                updated_at     = excluded.updated_at
        """, (*_base_params, now, now))
    else:
        db.execute("""
            INSERT INTO sessions
                (id, workspace_id, project_id, title, score_title, score_key, score_bpm,
                 score_notes, abc_notation, pipeline_state, extra, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workspace_id   = CASE
                    WHEN excluded.workspace_id IS NOT NULL AND excluded.workspace_id != ''
                    THEN excluded.workspace_id ELSE workspace_id END,
                project_id     = CASE
                    WHEN excluded.project_id IS NOT NULL AND excluded.project_id != ''
                    THEN excluded.project_id ELSE project_id END,
                title          = excluded.title,
                score_title    = excluded.score_title,
                score_key      = excluded.score_key,
                score_bpm      = excluded.score_bpm,
                score_notes    = excluded.score_notes,
                abc_notation   = excluded.abc_notation,
                pipeline_state = excluded.pipeline_state,
                extra          = excluded.extra,
                updated_at     = excluded.updated_at
        """, (*_base_params, extra_json, now, now))
    db.commit()


async def async_upsert_session(
    session_id: str,
    score=None,
    pipeline_state: str = "idle",
    workspace_id: str | None = None,
    project_id: str | None = None,
    title: str | None = None,
    extra: dict | None = None,
) -> None:
    """异步版 upsert_session，签名与同步版保持一致。"""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: upsert_session(session_id, score, pipeline_state,
                               workspace_id, project_id, title, extra),
    )


def get_session_info(session_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, workspace_id, project_id, title, score_title, score_key, score_bpm, "
        "score_notes, abc_notation, pipeline_state, extra, created_at, updated_at "
        "FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    raw_extra = d.get("extra") or "{}"
    if isinstance(raw_extra, str):
        try:
            d["extra"] = json.loads(raw_extra)
        except Exception:
            d["extra"] = {}
    elif not isinstance(raw_extra, dict):
        d["extra"] = {}
    return d


def list_sessions(limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, workspace_id, project_id, title, score_title, score_key, score_bpm, "
        "score_notes, pipeline_state, created_at, updated_at "
        "FROM sessions ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def rename_session(session_id: str, title: str) -> bool:
    db = get_db()
    now = datetime.now().isoformat()
    cur = db.execute(
        "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
        (title, now, session_id),
    )
    db.commit()
    return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    db.commit()
    return cur.rowcount > 0


def delete_session_cascade(session_id: str) -> bool:
    """级联删除 session 及其所有关联数据（messages / tool_calls / todos）。"""
    db = get_db()
    db.execute("DELETE FROM messages   WHERE session_id=?", (session_id,))
    db.execute("DELETE FROM tool_calls WHERE session_id=?", (session_id,))
    db.execute("DELETE FROM todos      WHERE session_id=?", (session_id,))
    cur = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    db.commit()
    return cur.rowcount > 0


# ─── Replay Session 生命周期 ──────────────────────────────────────────────────

def mark_session_archived(session_id: str) -> None:
    """将 replay session 标记为 archived，不展示在前端列表但保留审计数据。"""
    db = get_db()
    try:
        db.execute(
            "UPDATE sessions SET pipeline_state='archived', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), session_id),
        )
        db.commit()
    except Exception:
        pass


def delete_archived_sessions(max_age_hours: int = 24) -> int:
    """
    删除超过 max_age_hours 的 archived replay session 及其关联数据（垃圾回收）。
    返回实际删除的 session 数量。
    """
    db = get_db()
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()

    rows = db.execute(
        "SELECT id FROM sessions WHERE pipeline_state='archived' AND updated_at < ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return 0

    session_ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(session_ids))

    trace_rows = db.execute(
        f"SELECT trace_id FROM traces WHERE session_id IN ({ph})", session_ids
    ).fetchall()
    trace_ids = [r["trace_id"] for r in trace_rows]

    if trace_ids:
        tph = ",".join("?" * len(trace_ids))
        db.execute(f"DELETE FROM replay_fixtures WHERE trace_id IN ({tph})", trace_ids)
        db.execute(f"DELETE FROM spans          WHERE trace_id IN ({tph})", trace_ids)
        db.execute(f"DELETE FROM traces         WHERE trace_id IN ({tph})", trace_ids)

    db.execute(f"DELETE FROM replays  WHERE session_id IN ({ph})", session_ids)
    db.execute(f"DELETE FROM sessions WHERE id          IN ({ph})", session_ids)
    db.commit()
    return len(session_ids)


def get_workspace_sessions(ws_id: str) -> list[dict]:
    """返回指定 workspace 下所有 session 的摘要列表，按 updated_at 倒序。"""
    db = get_db()
    rows = db.execute(
        "SELECT id, workspace_id, project_id, title, score_title, score_key, score_bpm, "
        "score_notes, pipeline_state, created_at, updated_at "
        "FROM sessions WHERE workspace_id=? ORDER BY updated_at DESC",
        (ws_id,),
    ).fetchall()
    return [dict(r) for r in rows]
