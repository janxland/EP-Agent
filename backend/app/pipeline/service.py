"""
核心用例层 - 直接 import sky-music-tools（内置工具，与其他工具平等）
"""
from __future__ import annotations
import sys
import json
import asyncio
import tempfile
import os
from pathlib import Path
from threading import Lock
import datetime
import logging
from app.pipeline.domain import Score, Session, ScoreMeta, IntentRecord, new_id
from app.pipeline import db as _db

_logger = logging.getLogger(__name__)

# sky-music-tools 是内置工具，固定位于 backend/sky-music-tools/
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_DIR / "sky-music-tools"))

from tools.parser import parse_game_score
from tools.abc_writer import to_abc_notation
from tools.abc_to_json import abc_to_cuby_json
from tools.midi_writer import to_midi


# ─── 内存 Session Store（带 TTL 自动清理）────────────────────

_sessions: dict[str, Session] = {}
_lock = Lock()

# Session 最大空闲时间：2 小时（可通过环境变量覆盖）
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "7200"))


def get_session(session_id: str) -> Session:
    with _lock:
        sess = _sessions.get(session_id)
    if not sess:
        raise KeyError(f"session not found: {session_id}")
    return sess


def save_session(sess: Session):
    with _lock:
        _sessions[sess.id] = sess
    # 锁外调用，避免 Lock 不可重入导致死锁
    _evict_expired()


def _evict_expired():
    """清理超过 TTL 的 session，释放内存（含 source_json / audio_history base64）。"""
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(seconds=SESSION_TTL_SECONDS)
    with _lock:
        expired = [
            sid for sid, s in _sessions.items()
            if s.updated_at < cutoff
        ]
        for sid in expired:
            del _sessions[sid]


# ─── 用例1: 创建 Session ──────────────────────────────────────

def create_session(
    workspace_id: str | None = None,
    title: str = "新对话",
) -> Session:
    """创建 Session，支持直接关联工作区，避免二次写入。"""
    sess = Session()
    save_session(sess)
    try:
        _db.upsert_session(
            sess.id,
            score=None,
            pipeline_state="idle",
            workspace_id=workspace_id or None,
            title=title,
        )
    except Exception as e:
        _logger.warning("[service] create_session 落库失败 session=%s: %s", sess.id, e)
    return sess


def remove_session_from_memory(session_id: str) -> None:
    """从内存 store 中移除 session（供 router 调用，避免直接访问私有变量）"""
    with _lock:
        _sessions.pop(session_id, None)


# ─── 用例2: JSON → ABC 转换 ───────────────────────────────────

async def convert(session_id: str, json_content: str, file_name: str, publish) -> dict:
    sess = get_session(session_id)

    await publish("pipeline.step", {
        "step": "convert", "status": "running",
        "text": "正在解析 Sky JSON 谱...",
    })
    sess.pipeline_state = "running"
    save_session(sess)

    # 写临时文件（parser 需要文件路径）
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                     delete=False, encoding="utf-8") as f:
        f.write(json_content)
        tmp_path = f.name

    try:
        # 直接调用 Python 函数，无需 subprocess！
        loop = asyncio.get_running_loop()
        score_obj = await loop.run_in_executor(None, parse_game_score, tmp_path)
        abc_str = await loop.run_in_executor(None, to_abc_notation, score_obj)
    finally:
        os.unlink(tmp_path)

    meta = ScoreMeta(
        title=score_obj.title or "",
        composer=getattr(score_obj, "composer", "") or "",
        arranged_by=getattr(score_obj, "arranged_by", "") or "",
        transcribed_by=getattr(score_obj, "transcribed_by", "") or "",
        bpm=float(getattr(score_obj, "bpm", 120)),
        raw_bpm=float(getattr(score_obj, "raw_bpm", 120)),
        key=getattr(score_obj, "key", "C") or "C",
        pitch_level=int(getattr(score_obj, "pitch_level", 0)),
        time_sig_num=int(getattr(score_obj, "time_sig_num", 4)),
        time_sig_den=int(getattr(score_obj, "time_sig_den", 4)),
        note_count=len(score_obj.notes),
        duration_ms=float(score_obj.duration_ms()) if hasattr(score_obj, "duration_ms") else 0.0,
    )

    score = Score(
        title=meta.title,
        source_json=json_content,
        source_file=file_name,
        abc_notation=abc_str,
        meta=meta,
    )
    sess.score = score
    sess.pipeline_state = "succeeded"
    save_session(sess)

    await publish("pipeline.step", {
        "step": "convert", "status": "succeeded",
        "text": f"转换完成：{meta.title}，共 {meta.note_count} 个音符",
        "note_count": meta.note_count, "bpm": meta.bpm, "key": meta.key,
    })
    # SQLite 落库
    try:
        _db.upsert_session(session_id, score=score, pipeline_state="succeeded")
    except Exception as e:
        _logger.warning("[service] convert 落库失败 session=%s: %s", session_id, e)

    # abc.updated 由 ConvertAgent 统一推送（避免双推送导致 scoreStore 重复更新）
    # service.convert() 只负责转换 + 落库，不推送 SSE 事件

    return {
        "session_id": session_id,
        "score_id": score.id,
        "abc_notation": abc_str,
        "meta": {
            "title": meta.title, "composer": meta.composer,
            "bpm": meta.bpm, "key": meta.key,
            "time_sig": {"num": meta.time_sig_num, "den": meta.time_sig_den},
            "note_count": meta.note_count, "pitch_level": meta.pitch_level,
        },
    }


# ─── 用例3: 意图驱动 ABC 编辑 ─────────────────────────────────

async def edit(session_id: str, intent: str, publish, scene: str = "editor") -> dict:
    from app.agentcore.edit_runner import edit_agent_runner

    sess = get_session(session_id)
    if not sess.score:
        raise ValueError("no score in session, please convert first")

    sess.pipeline_state = "running"
    save_session(sess)

    abc_before = sess.score.abc_notation
    agent_result = await edit_agent_runner.run(
        current_abc=abc_before,
        intent=intent,
        meta=sess.score.meta,
        context_summary=sess.context_summary,
        publish=publish,
        scene=scene,
    )

    new_abc = agent_result["abc"]
    summary = agent_result["summary"]
    tool_calls = agent_result["tool_calls"]

    # 推断 intent_type（从工具调用记录中提取）
    intent_type = "custom"
    tool_names = [tc["tool"] for tc in tool_calls]
    if "transpose_abc" in tool_names:
        intent_type = "transpose"
    elif "change_tempo" in tool_names:
        intent_type = "tempo"
    elif "change_style" in tool_names:
        intent_type = "style"
    elif "add_ornament" in tool_names:
        intent_type = "structure"

    sess.score.push_version(new_abc, summary)
    sess.intent_history.append(IntentRecord(
        intent=intent, intent_type=intent_type,
        summary=summary, abc_before=abc_before, abc_after=new_abc,
    ))
    if sess.intent_history:
        last = sess.intent_history[-1]
        sess.context_summary = f"最近一次修改：{last.summary}（{last.intent_type}）"
    sess.pipeline_state = "succeeded"
    save_session(sess)

    # SQLite 落库
    try:
        _db.upsert_session(session_id, score=sess.score, pipeline_state="succeeded")
    except Exception as e:
        _logger.warning("[service] edit 落库失败 session=%s: %s", session_id, e)

    m2 = sess.score.meta
    await publish("abc.updated", {
        "abc":     new_abc,
        "version": sess.score.latest_version(),
        "summary": summary,
        "meta": {
            "title":      m2.title,
            "composer":   m2.composer,
            "bpm":        m2.bpm,
            "key":        m2.key,
            "time_sig":   {"num": m2.time_sig_num, "den": m2.time_sig_den},
            "note_count": m2.note_count,
            "pitch_level": m2.pitch_level,
        },
    })

    result = {
        "session_id": session_id,
        "abc_notation": new_abc,
        "intent_type": intent_type,
        "summary": summary,
        "version": sess.score.latest_version(),
        "tool_calls": tool_calls,
    }
    # 按场景附加额外输出
    if agent_result.get("sky_json"):
        result["sky_json"] = agent_result["sky_json"]
    if agent_result.get("midi_b64"):
        result["midi_b64"] = agent_result["midi_b64"]
    return result


# ─── 用例4: 导出 ──────────────────────────────────────────────

async def export_score(session_id: str, fmt: str, instrument: int = 0) -> tuple[bytes, str, str]:
    """返回 (content_bytes, filename, mime_type)"""
    sess = get_session(session_id)
    if not sess.score:
        raise ValueError("no score in session")

    abc_str = sess.score.abc_notation
    title = sess.score.meta.title or "score"
    safe_title = "".join(c if c not in r'/\:*?"<>|' else "_" for c in title)

    loop = asyncio.get_running_loop()

    if fmt == "abc":
        return abc_str.encode("utf-8"), f"{safe_title}.abc", "text/plain; charset=utf-8"

    elif fmt == "midi":
        # 需要先把 ABC 转回 score 对象，再用 midi_writer 导出
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False, encoding="utf-8") as f:
            cuby = abc_to_cuby_json(abc_str)
            json.dump([cuby], f, ensure_ascii=False)
            tmp_json = f.name
        # 独立生成 MIDI 临时文件路径，避免多进程竞态
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            tmp_mid = f.name
        try:
            score_obj = await loop.run_in_executor(None, parse_game_score, tmp_json)
            await loop.run_in_executor(
                None, lambda: to_midi(score_obj, tmp_mid,
                                      instrument=instrument,
                                      add_expression=True,
                                      humanize_ticks=6)
            )
            with open(tmp_mid, "rb") as f:
                data = f.read()
        finally:
            for p in [tmp_json, tmp_mid]:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        return data, f"{safe_title}.mid", "audio/midi"

    elif fmt == "json":
        cuby = abc_to_cuby_json(abc_str)
        data = json.dumps([cuby], ensure_ascii=False, indent=2).encode("utf-8")
        return data, f"{safe_title}.json", "application/json"

    else:
        raise ValueError(f"unsupported format: {fmt}")


# ─── 用例5: 统一对话（意图路由）─────────────────────────────────────────────

async def universal_chat(
    session_id: str,
    message: str,
    attachment_content: str = "",
    attachment_name: str = "",
    attachment_b64: str = "",
    publish=None,
    role_id: str | None = None,   # ← 角色 ID，透传给 universal_runner
) -> dict:
    """
    统一对话用例：LLM 自动识别意图，路由到 convert/edit/audio/voice/query。
    前端只需调用这一个接口，不需要区分场景。

    落库策略（参考 magic-coding 的 Message 全量落库）：
      1. 执行前：落库用户消息（role=user）
      2. 执行后：落库 AI 回复（role=assistant）
      3. 同步更新 session 的 abc_notation（供刷新后 SSE replay）
    """
    from app.agentcore.universal_runner import universal_runner

    if publish is None:
        async def _noop(evt_type: str, payload: dict):
            pass
        publish = _noop

    # ── 1. 落库用户消息 ──────────────────────────────────────────────────────
    user_msg_id = new_id("msg")
    # 构建完整消息内容（含附件名提示）
    user_content = message
    if attachment_name:
        user_content = f"[附件: {attachment_name}]\n{message}"
    try:
        _db.insert_message(
            msg_id=user_msg_id,
            session_id=session_id,
            role="user",
            content=user_content,
        )
    except Exception as e:
        _logger.warning("[service] 用户消息落库失败 session=%s: %s", session_id, e)

    # ── 2. 执行意图路由（透传 role_id，若未传则 runner 内部从 session extra 恢复）──
    # 优先使用调用方传入的 role_id；若为 None，runner 会从 DB session.extra 读取
    result = await universal_runner.run(
        session_id=session_id,
        message=message,
        attachment_content=attachment_content,
        attachment_name=attachment_name,
        attachment_b64=attachment_b64,
        session_getter=get_session,
        session_saver=save_session,
        publish=publish,
        convert_fn=convert,
        edit_fn=edit,
        audio_chat_fn=audio_chat,
        role_id=role_id,
    )

    # ── 3. 落库 AI 回复（兼容各 SubAgent 返回 key 不一致：message / reply / text）──
    assistant_reply = (
        result.get("message")
        or result.get("reply")
        or result.get("text")
        or ""
    )
    # 非空检查：空字符串或纯空白不落库，避免 SSE replay 时推送空消息气泡
    if assistant_reply and assistant_reply.strip():
        try:
            _db.insert_message(
                msg_id=new_id("msg"),
                session_id=session_id,
                role="assistant",
                content=assistant_reply,
            )
        except Exception as e:
            _logger.warning("[service] AI回复落库失败 session=%s: %s", session_id, e)

    # ── 4. 同步更新 session 的 abc_notation（刷新后 SSE replay 依赖此字段）──
    # universal_runner 内部已调用 upsert_session，但 abc_notation 可能未同步
    # 这里再做一次确保性更新
    try:
        sess = get_session(session_id)
        if sess.score and sess.score.abc_notation:
            _db.upsert_session(
                session_id,
                score=sess.score,
                pipeline_state=sess.pipeline_state,
            )
    except Exception as e:
        _logger.warning("[service] abc_notation 同步失败 session=%s: %s", session_id, e)

    return result


# ─── 用例6: 对话式音频生成 ────────────────────────────────────────────────────

async def audio_chat(
    session_id: str,
    message: str,
    provider: str = "auto",
    audio_b64: str = "",
    publish=None,
) -> dict:
    """
    对话式音频生成用例。
    - 首次调用：audio_history 为空，自动走 generate 流程
    - 后续调用：传入历史记录，走 iterate 流程（"再欢快一点"式迭代）
    返回本轮生成结果 dict，并自动追加到 sess.audio_history。
    """
    from app.agentcore.audio_runner import audio_chat_runner

    sess = get_session(session_id)

    # 无 publish 时使用空实现（静默模式，用于测试）
    if publish is None:
        async def _noop(evt_type: str, payload: dict):
            pass
        publish = _noop

    # 若 provider 偏好写入 message 上下文
    full_message = message
    if provider != "auto":
        full_message = f"[使用 {provider}] {message}"

    result = await audio_chat_runner.run(
        user_message=full_message,
        audio_history=list(sess.audio_history),   # 传副本，避免 runner 内部修改
        score_meta=sess.score.meta if sess.score else None,
        current_abc=sess.score.abc_notation if sess.score else "",
        audio_b64=audio_b64,
        publish=publish,
    )

    # 保存本轮记录到 Session
    sess.audio_history.append({
        "turn":         result.get("turn", len(sess.audio_history) + 1),
        "user_message": message,
        "domain":       result.get("domain", ""),
        "prompt":       result.get("prompt_used", ""),
        "style":        result.get("style_used", ""),
        "lyrics":       result.get("lyrics_used", ""),
        "instrumental": result.get("instrumental", False),
        "provider":     result.get("provider", ""),
        "model":        result.get("model", ""),
        "audio_url":    result.get("audio_url", ""),
        "audio_b64":    result.get("audio_b64", ""),
        "duration_ms":  result.get("duration_ms", 0),
        "summary":      result.get("summary", ""),
        "suggestions":  result.get("suggestions", []),
        "diff_summary": result.get("diff_summary", ""),
        # voice_clone 域专属
        "voice_id":     result.get("voice_id", ""),
        "demo_audio":   result.get("demo_audio", ""),
    })
    sess.updated_at = datetime.datetime.now()
    save_session(sess)

    return result
