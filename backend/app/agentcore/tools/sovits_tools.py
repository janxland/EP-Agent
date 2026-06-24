"""
GPT-SoVITS 工具集 — 音色克隆 + 语音合成

接入方式：部署 GPT-SoVITS 服务后，设置以下环境变量：
  SOVITS_BASE_URL=http://your-sovits-server:9880
  SOVITS_API_KEY=（若服务需要鉴权）

工具清单：
  sovits_tts           - 文本转语音（TTS），使用已训练的音色模型
  sovits_clone_voice   - 上传参考音频，零样本克隆音色（GPT-SoVITS zero-shot）
  sovits_list_models   - 列出已部署的音色模型列表

扩展说明：
  - 当前为占位实现（SOVITS_BASE_URL 未配置时返回友好提示）
  - 服务部署后无需修改 Runner，Agent 自动发现此工具组
  - 与 MiniMax voice_clone 工具并存，Agent 可根据用户意图自主选择
"""
from __future__ import annotations

import json
from app.agentcore.tools import tool
from app.config import config


def _sovits_base_url() -> str:
    return getattr(config, "SOVITS_BASE_URL", "")

def _sovits_api_key() -> str:
    return getattr(config, "SOVITS_API_KEY", "")

def _check_sovits() -> str | None:
    """返回 None 表示服务已配置；返回错误字符串表示未配置。"""
    if not _sovits_base_url():
        return (
            "GPT-SoVITS 服务未配置。"
            "请部署 GPT-SoVITS 服务后设置环境变量 SOVITS_BASE_URL。"
            "参考：https://github.com/RVC-Boss/GPT-SoVITS"
        )
    return None


@tool(group="sovits")
async def sovits_tts(
    text: str,
    model_name: str = "default",
    language: str = "zh",
    speed: float = 1.0,
) -> dict:
    """使用 GPT-SoVITS 将文本转换为语音（TTS）。
    text: 要合成的文本内容
    model_name: 音色模型名称（使用 sovits_list_models 查询可用模型）
    language: 语言代码，如 zh（中文）/en（英文）/ja（日文）
    speed: 语速倍率（0.5-2.0，默认 1.0）
    返回: {"audio_url": str, "audio_b64": str, "duration_ms": int, "model": str}
    """
    err = _check_sovits()
    if err:
        return {"error": err, "audio_url": "", "audio_b64": "", "duration_ms": 0}

    try:
        import httpx
        headers = {"Content-Type": "application/json"}
        if _sovits_api_key():
            headers["Authorization"] = f"Bearer {_sovits_api_key()}"

        payload = {
            "text":       text,
            "text_lang":  language,
            "ref_audio_path": model_name,  # GPT-SoVITS API 参数格式
            "speed_factor": speed,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_sovits_base_url()}/tts",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()

            # GPT-SoVITS 返回音频字节流
            import base64
            audio_b64 = base64.b64encode(resp.content).decode("ascii")
            return {
                "audio_url":  "",          # 本地部署无公开 URL，返回 b64
                "audio_b64":  audio_b64,
                "duration_ms": 0,          # GPT-SoVITS 不返回时长，需客户端解析
                "model":      model_name,
                "provider":   "sovits",
            }

    except Exception as e:
        return {
            "error":      str(e),
            "audio_url":  "",
            "audio_b64":  "",
            "duration_ms": 0,
        }


@tool(group="sovits")
async def sovits_clone_voice(
    reference_audio_b64: str,
    reference_text: str,
    target_text: str,
    language: str = "zh",
) -> dict:
    """使用 GPT-SoVITS zero-shot 克隆音色并合成语音。
    reference_audio_b64: 参考音频的 base64 编码（3-10秒清晰人声，mp3/wav）
    reference_text: 参考音频对应的文字内容（用于对齐）
    target_text: 要用克隆音色合成的目标文字
    language: 语言代码，zh/en/ja
    返回: {"audio_url": str, "audio_b64": str, "duration_ms": int}
    """
    err = _check_sovits()
    if err:
        return {"error": err, "audio_url": "", "audio_b64": "", "duration_ms": 0}

    try:
        import httpx, base64

        headers = {"Content-Type": "application/json"}
        if _sovits_api_key():
            headers["Authorization"] = f"Bearer {_sovits_api_key()}"

        payload = {
            "text":             target_text,
            "text_lang":        language,
            "ref_audio_path":   "",          # 使用 b64 上传
            "ref_audio_b64":    reference_audio_b64,
            "prompt_text":      reference_text,
            "prompt_lang":      language,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_sovits_base_url()}/tts",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            audio_b64 = base64.b64encode(resp.content).decode("ascii")
            return {
                "audio_url":   "",
                "audio_b64":   audio_b64,
                "duration_ms": 0,
                "provider":    "sovits",
            }

    except Exception as e:
        return {
            "error":      str(e),
            "audio_url":  "",
            "audio_b64":  "",
            "duration_ms": 0,
        }


@tool(group="sovits")
async def sovits_list_models() -> dict:
    """列出 GPT-SoVITS 服务上已部署的音色模型列表。
    返回: {"models": [{"name": str, "language": str, "description": str}], "total": int}
    """
    err = _check_sovits()
    if err:
        return {"error": err, "models": [], "total": 0}

    try:
        import httpx
        headers = {}
        if _sovits_api_key():
            headers["Authorization"] = f"Bearer {_sovits_api_key()}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_sovits_base_url()}/models",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", data if isinstance(data, list) else [])
            return {"models": models, "total": len(models)}

    except Exception as e:
        return {"error": str(e), "models": [], "total": 0}
