"""
音频生成工具集 - Suno AI & MiniMax 音乐生成

设计原则：
- 每个工具是独立的 @tool 注册函数，Agent 可自主决定调用
- 支持异步轮询（Suno 异步任务）和同步响应（MiniMax）
- 工具返回 dict，包含 audio_url / audio_b64 / duration 等字段
- API Key 从环境变量读取，未配置时工具返回友好错误

工具清单：
  generate_audio_suno     - Suno AI 生成原创歌曲（支持歌词/纯音乐/风格）
  generate_audio_minimax  - MiniMax music-2.6 生成原创歌曲
  generate_cover_minimax  - MiniMax music-cover 翻唱已有参考音频
  generate_lyrics_minimax - MiniMax 生成歌词（供后续 generate_audio_minimax 使用）
  abc_to_prompt           - 将 ABC 谱元信息转换为音频生成 prompt（辅助工具）
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx

from app.agentcore.tools import tool
from app.config import config


# ─── 常量（运行时从 config 读取，支持热更新）─────────────────────────────────
# 注意：不在模块级固化，每次调用时通过 config 对象动态获取

def _suno_base_url() -> str:
    return config.SUNO_BASE_URL

def _suno_api_key() -> str:
    return config.SUNO_API_KEY

def _minimax_base_url() -> str:
    return config.MINIMAX_BASE_URL

def _minimax_api_key() -> str:
    return config.MINIMAX_API_KEY

SUNO_POLL_INTERVAL = 5   # 秒
SUNO_POLL_TIMEOUT  = 300 # 秒（最多等 5 分钟）


# ─── 内部工具函数 ─────────────────────────────────────────────────────────────

SUNO_POLL_INTERVAL = 5
SUNO_POLL_TIMEOUT  = 300


def _suno_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "TT-API-KEY": _suno_api_key(),
    }


def _minimax_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_minimax_api_key()}",
    }


async def _suno_poll(job_id: str) -> dict:
    """轮询 Suno 任务直到完成或超时（每次轮询独立建立连接，避免长连接泄漏）"""
    url = f"{_suno_base_url()}/suno/v2/fetch"
    deadline = time.time() + SUNO_POLL_TIMEOUT

    while time.time() < deadline:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params={"jobId": job_id}, headers=_suno_headers())
            resp.raise_for_status()
            data = resp.json()

        status = data.get("status", "")
        if status == "SUCCESS":
            musics = data.get("data", {}).get("musics", [])
            if musics:
                return musics[0]
            raise RuntimeError("Suno 返回成功但 musics 为空")
        if status in ("FAILED", "ERROR"):
            raise RuntimeError(f"Suno 任务失败: {data.get('message', status)}")
        # ON_QUEUE / PROCESSING → 等待后继续
        await asyncio.sleep(SUNO_POLL_INTERVAL)

    raise TimeoutError(f"Suno 任务超时（>{SUNO_POLL_TIMEOUT}s），jobId={job_id}")


def _minimax_hex_to_b64(hex_str: str) -> str:
    """将 MiniMax 返回的 hex 音频数据转为 base64"""
    raw = bytes.fromhex(hex_str)
    return base64.b64encode(raw).decode()


# ─── Suno 工具 ────────────────────────────────────────────────────────────────

@tool(group="audio")
async def generate_audio_suno(
    prompt: str,
    style: str = "",
    title: str = "",
    lyrics: str = "",
    instrumental: bool = False,
    model: str = "chirp-v5",
) -> dict:
    """使用 Suno AI 生成原创歌曲（异步，自动轮询直到完成）。
    prompt: 歌曲描述或歌词内容。自定义歌词模式下填写带 [Verse]/[Chorus] 标签的歌词
    style: 音乐风格标签，多个用逗号分隔，如 "synthwave, 女声, 电子"
    title: 歌曲标题
    lyrics: 歌词文本（提供时自动启用自定义歌词模式）
    instrumental: true 生成纯器乐，不含人声
    model: Suno 模型版本，推荐 chirp-v5 或 chirp-v5-5
    返回: {"audio_url": str, "video_url": str, "duration": float, "music_id": str, "title": str}
    """
    if not _suno_api_key():
        return {"error": "SUNO_API_KEY 未配置，请设置环境变量 SUNO_API_KEY"}

    # 有 lyrics 参数时启用自定义歌词模式
    custom_mode = bool(lyrics)
    actual_prompt = lyrics if custom_mode else prompt

    payload: dict[str, Any] = {
        "mv": model,
        "prompt": actual_prompt,
        "custom": custom_mode,
        "instrumental": instrumental,
    }
    if style:
        payload["tags"] = style
    if title:
        payload["title"] = title

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_suno_base_url()}/suno/v1/music",
            json=payload,
            headers=_suno_headers(),
        )
        resp.raise_for_status()
        result = resp.json()

    job_id = result.get("data", {}).get("jobId") or result.get("jobId")
    if not job_id:
        raise RuntimeError(f"Suno 未返回 jobId: {result}")

    # 轮询等待完成
    music = await _suno_poll(job_id)

    return {
        "audio_url":  music.get("audioUrl", ""),
        "video_url":  music.get("videoUrl", ""),
        "duration":   music.get("duration", 0),
        "music_id":   music.get("musicId", ""),
        "title":      title or prompt[:40],
        "job_id":     job_id,
        "provider":   "suno",
    }


@tool(group="audio")
async def get_suno_job_status(job_id: str) -> dict:
    """查询 Suno 音频生成任务的当前状态（不等待，立即返回）。
    job_id: generate_audio_suno 返回的 job_id
    返回: {"status": "ON_QUEUE|SUCCESS|FAILED", "progress": str, "audio_url": str}
    """
    if not _suno_api_key():
        return {"error": "SUNO_API_KEY 未配置"}

    url = f"{_suno_base_url()}/suno/v2/fetch"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params={"jobId": job_id}, headers=_suno_headers())
        resp.raise_for_status()
        data = resp.json()

    status = data.get("status", "UNKNOWN")
    result: dict[str, Any] = {
        "status":   status,
        "progress": data.get("data", {}).get("progress", ""),
        "job_id":   job_id,
    }
    if status == "SUCCESS":
        musics = data.get("data", {}).get("musics", [])
        if musics:
            result["audio_url"] = musics[0].get("audioUrl", "")
            result["duration"]  = musics[0].get("duration", 0)
    return result


# ─── MiniMax 工具 ─────────────────────────────────────────────────────────────

@tool(group="audio")
async def generate_lyrics_minimax(prompt: str) -> dict:
    """使用 MiniMax 根据主题自动生成歌词（含 Verse/Chorus 结构）。
    prompt: 歌曲主题描述，如 "一首关于夏夜星空的流行歌曲"
    返回: {"lyrics": str, "title": str}  lyrics 可直接传给 generate_audio_minimax
    """
    if not _minimax_api_key():
        return {"error": "MINIMAX_API_KEY 未配置，请设置环境变量 MINIMAX_API_KEY"}

    payload = {
        "mode": "write_full_song",
        "prompt": prompt,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{_minimax_base_url()}/lyrics_generation",
            json=payload,
            headers=_minimax_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"MiniMax 歌词生成失败: {base.get('status_msg')}")

    lyrics = data.get("data", {}).get("lyrics", "")
    title  = data.get("data", {}).get("title", prompt[:20])
    return {"lyrics": lyrics, "title": title}


@tool(group="audio")
async def generate_audio_minimax(
    prompt: str,
    lyrics: str = "",
    instrumental: bool = False,
    model: str = "music-2.6",
    output_format: str = "url",
) -> dict:
    """使用 MiniMax music-2.6 生成原创歌曲（同步，直接返回音频）。
    prompt: 音乐风格描述，如 "Indie folk, melancholic, rainy night"
    lyrics: 歌词文本，使用 \\n 分行，支持 [Verse]/[Chorus] 等结构标签。留空且 instrumental=false 时自动生成
    instrumental: true 生成纯音乐（无人声），此时 prompt 必填
    model: 模型名，music-2.6（付费）或 music-2.6-free（免费，RPM 较低）
    output_format: url（返回下载链接，有效 24h）或 hex（返回 base64 编码）
    返回: {"audio_url": str, "audio_b64": str, "duration_ms": int, "provider": "minimax"}
    """
    if not _minimax_api_key():
        return {"error": "MINIMAX_API_KEY 未配置，请设置环境变量 MINIMAX_API_KEY"}

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"},
        "output_format": output_format,
    }

    if instrumental:
        payload["is_instrumental"] = True
    elif lyrics:
        payload["lyrics"] = lyrics
    else:
        # 无歌词 + 非纯音乐 → 让 MiniMax 自动从 prompt 生成歌词
        payload["lyrics_optimizer"] = True

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{_minimax_base_url()}/music_generation",
            json=payload,
            headers=_minimax_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"MiniMax 音乐生成失败: {base.get('status_msg')}")

    audio_data = data.get("data", {})
    extra      = data.get("extra_info", {})

    result: dict[str, Any] = {
        "duration_ms": extra.get("music_duration", 0),
        "provider":    "minimax",
        "model":       model,
    }

    if output_format == "url":
        result["audio_url"] = audio_data.get("audio", "")
        result["audio_b64"] = ""
    else:
        hex_str = audio_data.get("audio", "")
        result["audio_b64"] = _minimax_hex_to_b64(hex_str) if hex_str else ""
        result["audio_url"] = ""

    return result


@tool(group="audio")
async def generate_cover_minimax(
    audio_url: str,
    prompt: str,
    lyrics: str = "",
    model: str = "music-cover",
) -> dict:
    """使用 MiniMax music-cover 对已有音频进行 AI 翻唱/风格转换。
    audio_url: 原始音频的公开 URL（mp3/wav/flac，时长 6s-6min，≤50MB）
    prompt: 翻唱风格描述，如 "Jazz, smooth, late night lounge"（10-300字符）
    lyrics: 可选，替换原曲歌词（留空则保留原歌词）
    model: music-cover（付费）或 music-cover-free（免费）
    返回: {"audio_url": str, "audio_b64": str, "duration_ms": int, "provider": "minimax"}
    """
    if not _minimax_api_key():
        return {"error": "MINIMAX_API_KEY 未配置，请设置环境变量 MINIMAX_API_KEY"}

    payload: dict[str, Any] = {
        "model": model,
        "audio_url": audio_url,
        "prompt": prompt,
        "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"},
        "output_format": "url",
    }
    if lyrics:
        payload["lyrics"] = lyrics

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{_minimax_base_url()}/music_generation",
            json=payload,
            headers=_minimax_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"MiniMax 翻唱失败: {base.get('status_msg')}")

    audio_data = data.get("data", {})
    extra      = data.get("extra_info", {})

    return {
        "audio_url":   audio_data.get("audio", ""),
        "audio_b64":   "",
        "duration_ms": extra.get("music_duration", 0),
        "provider":    "minimax",
        "model":       model,
    }


# ─── 辅助工具 ─────────────────────────────────────────────────────────────────

@tool(group="audio")
def abc_to_audio_prompt(abc: str, style_hint: str = "") -> dict:
    """将 ABC 谱的元信息提取为音频生成 prompt，方便传给 generate_audio_suno / generate_audio_minimax。
    abc: ABC Notation 谱子文本
    style_hint: 额外的风格提示，如 "中国风" "电子" "爵士"
    返回: {"prompt": str, "title": str, "key": str, "bpm": int}
    """
    import re

    title = ""
    key   = "C"
    bpm   = 120
    meter = "4/4"

    for line in abc.splitlines():
        line = line.strip()
        if line.startswith("T:"):
            title = line[2:].strip()
        elif line.startswith("K:"):
            key = line[2:].strip()
        elif line.startswith("Q:"):
            # Q: 120 或 Q: 1/4=120
            m = re.search(r"(\d+)\s*$", line)
            if m:
                bpm = int(m.group(1))
        elif line.startswith("M:"):
            meter = line[2:].strip()

    # 调号到情绪的简单映射
    key_mood = {
        "C": "bright, clear",
        "G": "pastoral, warm",
        "D": "energetic, bright",
        "A": "joyful, uplifting",
        "E": "intense, passionate",
        "F": "gentle, lyrical",
        "Am": "melancholic, introspective",
        "Dm": "dark, mysterious",
        "Em": "reflective, sad",
    }
    mood = key_mood.get(key.replace("maj", "").replace("min", "m").strip(), "expressive")

    tempo_desc = "slow" if bpm < 80 else ("moderate" if bpm < 120 else "upbeat")

    parts = [f"{tempo_desc}, {mood}"]
    if style_hint:
        parts.insert(0, style_hint)
    if title:
        parts.append(f'inspired by "{title}"')

    return {
        "prompt": ", ".join(parts),
        "title":  title,
        "key":    key,
        "bpm":    bpm,
        "meter":  meter,
    }
