"""
MiniMax 音色克隆工具集

完整工作流：
  1. upload_voice_sample     - 上传音频样本，获取 file_id（purpose=voice_clone）
  2. upload_prompt_audio     - 上传增强样本，获取 prompt_file_id（purpose=prompt_audio，可选）
  3. clone_voice_minimax     - 基于 file_id 克隆音色，得到 voice_id
  4. list_cloned_voices      - 查询已克隆的音色列表（voice_type=voice_cloning）
  5. synthesize_speech_minimax - 用克隆音色做 TTS，返回试听音频 URL

注意：
  - 克隆音色 7 天内未调用则自动删除
  - voice_id 规则：长度 [8,256]，首字母英文，可含字母/数字/-/_，末位不可为 -/_
  - 上传文件格式：mp3/m4a/wav；源音频 10s-5min ≤20MB；增强样本 <8s ≤20MB
"""
from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from app.agentcore.tools import tool
from app.config import config


# ─── 内部辅助 ─────────────────────────────────────────────────────────────────

def _minimax_api_key() -> str:
    return config.MINIMAX_API_KEY

def _minimax_base_url() -> str:
    return config.MINIMAX_BASE_URL

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_minimax_api_key()}",
    }

def _json_headers() -> dict:
    return {
        "Authorization": f"Bearer {_minimax_api_key()}",
        "Content-Type": "application/json",
    }

def _check_key() -> str | None:
    """返回 None 表示 key 存在；返回错误 dict 字符串表示未配置"""
    if not _minimax_api_key():
        return "MINIMAX_API_KEY 未配置，请设置环境变量 MINIMAX_API_KEY"
    return None

def _validate_voice_id(voice_id: str) -> str | None:
    """校验 voice_id 规则，返回 None 表示合法，否则返回错误说明"""
    if not (8 <= len(voice_id) <= 256):
        return f"voice_id 长度须在 [8, 256]，当前长度 {len(voice_id)}"
    if not re.match(r'^[A-Za-z]', voice_id):
        return "voice_id 首字符必须为英文字母"
    if not re.match(r'^[A-Za-z0-9\-_]+$', voice_id):
        return "voice_id 只允许字母、数字、- 和 _"
    if voice_id[-1] in ('-', '_'):
        return "voice_id 末位不可为 - 或 _"
    return None


# ─── 工具 1：上传音频样本（获取 file_id）────────────────────────────────────

@tool(group="audio")
async def upload_voice_sample(audio_b64: str, filename: str = "sample.mp3") -> dict:
    """上传音色克隆源音频（base64 编码），获取 file_id 供 clone_voice_minimax 使用。
    audio_b64: 音频文件的 base64 编码字符串（mp3/m4a/wav，时长 10s-5min，≤20MB）
    filename: 文件名，需带扩展名，如 sample.mp3 / voice.wav
    返回: {"file_id": str, "bytes": int, "filename": str}
    """
    err = _check_key()
    if err:
        return {"error": err}

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as e:
        return {"error": f"base64 解码失败: {e}"}

    if len(audio_bytes) > 20 * 1024 * 1024:
        return {"error": "文件大小超过 20MB 限制"}

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
    mime_map = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"}
    mime = mime_map.get(ext, "audio/mpeg")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.minimax.io/v1/files/upload",
            headers=_headers(),
            data={"purpose": "voice_clone"},
            files={"file": (filename, audio_bytes, mime)},
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"文件上传失败: {base.get('status_msg')}")

    file_info = data.get("file", {})
    return {
        "file_id":  str(file_info.get("file_id", "")),
        "bytes":    file_info.get("bytes", len(audio_bytes)),
        "filename": file_info.get("filename", filename),
        "purpose":  "voice_clone",
    }


# ─── 工具 2：上传增强样本（可选，提升克隆质量）──────────────────────────────

@tool(group="audio")
async def upload_prompt_audio(audio_b64: str, filename: str = "prompt.mp3") -> dict:
    """上传音色克隆增强样本（可选），获取 prompt_file_id 传给 clone_voice_minimax 提升相似度。
    audio_b64: 短音频的 base64 编码（mp3/m4a/wav，时长 <8s，≤20MB）
    filename: 文件名，需带扩展名
    返回: {"file_id": str, "bytes": int, "filename": str}
    """
    err = _check_key()
    if err:
        return {"error": err}

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as e:
        return {"error": f"base64 解码失败: {e}"}

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
    mime_map = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"}
    mime = mime_map.get(ext, "audio/mpeg")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.minimax.io/v1/files/upload",
            headers=_headers(),
            data={"purpose": "prompt_audio"},
            files={"file": (filename, audio_bytes, mime)},
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"增强样本上传失败: {base.get('status_msg')}")

    file_info = data.get("file", {})
    return {
        "file_id":  str(file_info.get("file_id", "")),
        "bytes":    file_info.get("bytes", len(audio_bytes)),
        "filename": file_info.get("filename", filename),
        "purpose":  "prompt_audio",
    }


# ─── 工具 3：克隆音色 ─────────────────────────────────────────────────────────

@tool(group="audio")
async def clone_voice_minimax(
    file_id: str,
    voice_id: str,
    prompt_file_id: str = "",
    prompt_text: str = "",
    preview_text: str = "",
    need_noise_reduction: bool = False,
    need_volume_normalization: bool = False,
) -> dict:
    """基于已上传的音频样本克隆音色，生成可复用的 voice_id。
    file_id: upload_voice_sample 返回的 file_id（源音频）
    voice_id: 自定义音色 ID（首字母英文，8-256位，只含字母/数字/-/_，末位不可为-/_）
    prompt_file_id: upload_prompt_audio 返回的增强样本 file_id（可选，提升相似度）
    prompt_text: 增强样本对应的文本内容（提供 prompt_file_id 时建议填写）
    preview_text: 克隆后生成试听音频的文本（≤1000字符，留空则不生成试听）
    need_noise_reduction: 是否对源音频降噪（背景噪音较多时开启）
    need_volume_normalization: 是否对源音频音量归一化
    返回: {"voice_id": str, "demo_audio": str, "status": "success"|"failed", "message": str}
    """
    err = _check_key()
    if err:
        return {"error": err}

    # 校验 voice_id
    id_err = _validate_voice_id(voice_id)
    if id_err:
        return {"error": id_err}

    payload: dict[str, Any] = {
        "file_id": int(file_id) if file_id.isdigit() else file_id,
        "voice_id": voice_id,
        "need_noise_reduction": need_noise_reduction,
        "need_volume_normalization": need_volume_normalization,
    }

    # 增强样本（可选）
    if prompt_file_id:
        payload["clone_prompt"] = {
            "prompt_audio": int(prompt_file_id) if prompt_file_id.isdigit() else prompt_file_id,
        }
        if prompt_text:
            payload["clone_prompt"]["prompt_text"] = prompt_text

    # 试听（可选）
    if preview_text:
        payload["text"] = preview_text
        payload["model"] = "speech-2.6-hd"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.minimax.io/v1/voice_clone",
            headers=_json_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    status_code = base.get("status_code", -1)

    if status_code == 1043:
        return {
            "voice_id":   voice_id,
            "demo_audio": "",
            "status":     "failed",
            "message":    "ASR 相似度校验失败，请确认音频内容与 text_validation 一致",
        }
    if status_code != 0:
        return {
            "voice_id":   voice_id,
            "demo_audio": "",
            "status":     "failed",
            "message":    f"克隆失败: {base.get('status_msg', '未知错误')}",
        }

    return {
        "voice_id":   voice_id,
        "demo_audio": data.get("demo_audio", ""),
        "status":     "success",
        "message":    f"音色 {voice_id} 克隆成功，7天内未使用将自动删除",
        "extra_info": data.get("extra_info", {}),
    }


# ─── 工具 4：查询已克隆音色列表 ───────────────────────────────────────────────

@tool(group="audio")
async def list_cloned_voices(voice_type: str = "voice_cloning") -> dict:
    """查询账号下已克隆的音色列表。
    voice_type: 查询范围：voice_cloning（快速克隆音色）| system（系统音色）| all（全部）
    返回: {"voices": [{"voice_id": str, "name": str, "type": str}, ...], "total": int}
    """
    err = _check_key()
    if err:
        return {"error": err}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.minimaxi.com/v1/get_voice",
            headers=_json_headers(),
            json={"voice_type": voice_type},
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"查询音色列表失败: {base.get('status_msg')}")

    voices = data.get("voices", [])
    return {
        "voices": [
            {
                "voice_id": v.get("voice_id", ""),
                "name":     v.get("name", ""),
                "type":     v.get("type", voice_type),
            }
            for v in voices
        ],
        "total": len(voices),
    }


# ─── 工具 5：用克隆音色合成语音（TTS）────────────────────────────────────────

@tool(group="audio")
async def synthesize_speech_minimax(
    text: str,
    voice_id: str,
    model: str = "speech-2.6-hd",
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: int = 0,
    output_format: str = "url",
) -> dict:
    """使用克隆音色（或系统音色）将文本合成为语音。
    text: 要合成的文本（≤10000字符）
    voice_id: 克隆音色 ID（来自 clone_voice_minimax）或系统音色 ID
    model: 语音模型，speech-2.6-hd（高质量）| speech-2.6-turbo（低延迟）| speech-2.8-hd（最新）
    speed: 语速，范围 [0.5, 2.0]，默认 1.0
    vol: 音量，范围 [0.1, 10.0]，默认 1.0
    pitch: 音调，范围 [-12, 12]，默认 0（半音）
    output_format: url（返回链接，有效24h）| hex（返回 base64 编码）
    返回: {"audio_url": str, "audio_b64": str, "duration_ms": int, "usage_characters": int}
    """
    err = _check_key()
    if err:
        return {"error": err}

    if not text.strip():
        return {"error": "text 不能为空"}

    payload: dict[str, Any] = {
        "model": model,
        "text": text,
        "voice_setting": {
            "voice_id": voice_id,
            "speed":    max(0.5, min(2.0, speed)),
            "vol":      max(0.1, min(10.0, vol)),
            "pitch":    max(-12, min(12, pitch)),
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate":     128000,
            "format":      "mp3",
            "channel":     1,
        },
        "output_format": output_format,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.minimax.io/v1/t2a_v2",
            headers=_json_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        raise RuntimeError(f"语音合成失败: {base.get('status_msg')}")

    audio_data = data.get("data", {})
    extra      = data.get("extra_info", {})

    result: dict[str, Any] = {
        "duration_ms":       extra.get("audio_length", 0),
        "usage_characters":  extra.get("usage_characters", 0),
        "voice_id":          voice_id,
        "model":             model,
        "provider":          "minimax",
    }

    if output_format == "url":
        result["audio_url"] = audio_data.get("audio", "")
        result["audio_b64"] = ""
    else:
        hex_str = audio_data.get("audio", "")
        if hex_str:
            raw = bytes.fromhex(hex_str)
            result["audio_b64"] = base64.b64encode(raw).decode()
        else:
            result["audio_b64"] = ""
        result["audio_url"] = ""

    return result
