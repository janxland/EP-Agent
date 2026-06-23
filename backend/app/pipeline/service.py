"""
核心用例层 - 直接 import sky-music-tools，无需 subprocess
"""
from __future__ import annotations
import sys
import json
import asyncio
import tempfile
import os
from threading import Lock
import datetime
from app.config import config
from app.pipeline.domain import Score, Session, ScoreMeta, IntentRecord, new_id

# 注入 sky-music-tools 路径
sys.path.insert(0, config.SKILL_DIR)
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

def create_session() -> Session:
    sess = Session()
    save_session(sess)
    return sess


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
        loop = asyncio.get_event_loop()
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
    await publish("abc.updated", {
        "abc": abc_str, "version": score.latest_version(),
    })

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

    await publish("abc.updated", {
        "abc": new_abc, "version": sess.score.latest_version(), "summary": summary,
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

    loop = asyncio.get_event_loop()

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
                try: os.unlink(p)
                except: pass
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
) -> dict:
    """
    统一对话用例：LLM 自动识别意图，路由到 convert/edit/audio/voice/query。
    前端只需调用这一个接口，不需要区分场景。
    """
    from app.agentcore.universal_runner import universal_runner

    if publish is None:
        async def _noop(evt_type: str, payload: dict):
            pass
        publish = _noop

    return await universal_runner.run(
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
    )


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
