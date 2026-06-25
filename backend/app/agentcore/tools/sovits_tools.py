"""
GPT-SoVITS 工具集 — 音色克隆 + 语音合成（完整接入版）

接入方式：部署 GPT-SoVITS 服务后，设置以下环境变量：
  SOVITS_BASE_URL=http://localhost:9880   # GPT-SoVITS 服务地址
  SOVITS_API_KEY=（若服务需要鉴权，默认不需要）

GPT-SoVITS API 端点（v2/v3 兼容）：
  POST /tts                  — 文本转语音
  GET  /models               — 列出已加载模型（部分版本支持）
  POST /set_gpt_weights      — 切换 GPT 模型
  POST /set_sovits_weights   — 切换 SoVITS 模型
  GET  /                     — 健康检查

工具清单：
  sovits_health_check    — 检查服务是否在线
  sovits_tts             — 文本转语音（TTS），支持参考音频零样本克隆
  sovits_clone_voice     — 上传参考音频 + 目标文本，零样本克隆并合成
  sovits_list_models     — 列出已部署的音色模型
  sovits_set_model       — 切换 GPT/SoVITS 模型权重
  sovits_save_audio      — 将合成音频保存到工作区

设计原则：
  - SOVITS_BASE_URL 未配置时返回友好提示，不抛异常
  - 与 MiniMax voice_clone 工具并存，Agent 可根据用户意图自主选择
  - 参考音频支持 base64 上传或本地路径两种方式
"""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Optional

from app.agentcore.tools import tool
from app.config import config


# ── 内部辅助 ────────────────────────────────────────────────────────────────

def _base_url() -> str:
    return (getattr(config, "SOVITS_BASE_URL", "") or "").rstrip("/")

def _api_key() -> str:
    return getattr(config, "SOVITS_API_KEY", "") or ""

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _api_key():
        h["Authorization"] = f"Bearer {_api_key()}"
    return h

def _check() -> str | None:
    """返回 None 表示服务已配置；返回错误字符串表示未配置。"""
    if not _base_url():
        return (
            "GPT-SoVITS 服务未配置。"
            "请先部署 GPT-SoVITS（参考 EP-Agent/sovits-installer/），"
            "然后设置环境变量 SOVITS_BASE_URL=http://localhost:9880。"
        )
    return None

def _strip_dataurl(s: str) -> str:
    """剥离 DataURL 前缀，返回纯 base64。"""
    if s.startswith("data:") and ";base64," in s:
        return s.split(";base64,", 1)[1]
    return s


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 1：健康检查
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_health_check() -> dict:
    """检查 GPT-SoVITS 服务是否在线，返回服务状态和版本信息。
    返回: {"online": bool, "url": str, "version": str, "message": str}
    """
    err = _check()
    if err:
        return {"online": False, "url": "", "version": "", "message": err}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(_base_url() + "/")
            online = resp.status_code < 500
            text = resp.text[:200] if online else ""
            return {
                "online":  online,
                "url":     _base_url(),
                "version": "GPT-SoVITS",
                "message": f"服务在线 ({resp.status_code})" if online else f"服务异常 ({resp.status_code})",
                "detail":  text,
            }
    except Exception as e:
        return {
            "online":  False,
            "url":     _base_url(),
            "version": "",
            "message": f"连接失败: {e}。请确认 GPT-SoVITS 服务已启动（python webui.py）",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 2：文本转语音（TTS）
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_tts(
    text: str,
    ref_audio_path: str = "",
    ref_audio_b64: str = "",
    prompt_text: str = "",
    prompt_lang: str = "zh",
    text_lang: str = "zh",
    top_k: int = 5,
    top_p: float = 1.0,
    temperature: float = 1.0,
    speed_factor: float = 1.0,
    how_to_cut: str = "凑四句一切",
    media_type: str = "wav",
) -> dict:
    """使用 GPT-SoVITS 将文本转换为语音（TTS）。
    支持零样本音色克隆：提供 3-10 秒参考音频即可克隆音色。

    text: 要合成的目标文本
    ref_audio_path: 参考音频的本地路径（与 ref_audio_b64 二选一）
    ref_audio_b64: 参考音频的 base64 编码（mp3/wav，3-10秒，与 ref_audio_path 二选一）
    prompt_text: 参考音频对应的文字（提高克隆质量，可选）
    prompt_lang: 参考音频语言 zh/en/ja/ko/yue（默认 zh）
    text_lang: 目标文本语言 zh/en/ja/ko/yue（默认 zh）
    top_k: 采样 top-k（默认 5）
    top_p: 采样 top-p（默认 1.0）
    temperature: 采样温度（默认 1.0）
    speed_factor: 语速倍率（0.5-2.0，默认 1.0）
    how_to_cut: 文本切分方式（凑四句一切/按中文句号切/按英文句号切/按标点符号切）
    media_type: 输出格式 wav/mp3/ogg（默认 wav）
    返回: {"audio_b64": str, "media_type": str, "size_bytes": int, "provider": "sovits"}
    """
    err = _check()
    if err:
        return {"error": err, "audio_b64": "", "audio_url": ""}

    if not text.strip():
        return {"error": "text 不能为空", "audio_b64": ""}

    try:
        import httpx

        payload: dict = {
            "text":          text,
            "text_lang":     text_lang,
            "prompt_lang":   prompt_lang,
            "prompt_text":   prompt_text,
            "top_k":         top_k,
            "top_p":         top_p,
            "temperature":   temperature,
            "speed_factor":  speed_factor,
            "how_to_cut":    how_to_cut,
            "media_type":    media_type,
        }

        # 参考音频：base64 优先，其次本地路径
        if ref_audio_b64:
            payload["ref_audio_b64"] = _strip_dataurl(ref_audio_b64)
        elif ref_audio_path:
            payload["ref_audio_path"] = ref_audio_path

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_base_url()}/tts",
                json=payload,
                headers=_headers(),
            )
            resp.raise_for_status()

        audio_bytes = resp.content
        audio_b64   = base64.b64encode(audio_bytes).decode("ascii")

        return {
            "audio_b64":   audio_b64,
            "audio_url":   "",          # 本地部署无公开 URL
            "media_type":  media_type,
            "size_bytes":  len(audio_bytes),
            "provider":    "sovits",
            "text_length": len(text),
        }

    except Exception as e:
        return {"error": str(e), "audio_b64": "", "audio_url": ""}


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 3：零样本音色克隆（上传参考音频 + 合成）
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_clone_voice(
    reference_audio_b64: str,
    target_text: str,
    reference_text: str = "",
    language: str = "zh",
    speed_factor: float = 1.0,
) -> dict:
    """使用 GPT-SoVITS 零样本克隆音色并合成语音（一步完成）。
    只需 3-10 秒参考音频，无需训练，即可克隆音色合成任意文本。

    reference_audio_b64: 参考音频 base64（mp3/wav，3-10秒清晰人声）
    target_text: 要用克隆音色合成的目标文字
    reference_text: 参考音频对应的文字（提高克隆质量，可选）
    language: 语言代码 zh/en/ja/ko/yue（默认 zh）
    speed_factor: 语速倍率 0.5-2.0（默认 1.0）
    返回: {"audio_b64": str, "size_bytes": int, "provider": "sovits"}
    """
    return await sovits_tts(
        text=target_text,
        ref_audio_b64=reference_audio_b64,
        prompt_text=reference_text,
        prompt_lang=language,
        text_lang=language,
        speed_factor=speed_factor,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 4：列出已加载模型
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_list_models() -> dict:
    """列出 GPT-SoVITS 服务上已加载的 GPT 和 SoVITS 模型。
    返回: {"gpt_models": [...], "sovits_models": [...], "current_gpt": str, "current_sovits": str}
    """
    err = _check()
    if err:
        return {"error": err, "gpt_models": [], "sovits_models": []}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            # 尝试获取模型列表（部分版本支持）
            try:
                resp = await client.get(
                    f"{_base_url()}/models",
                    headers=_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "gpt_models":    data.get("gpt_models", []),
                        "sovits_models": data.get("sovits_models", []),
                        "current_gpt":   data.get("current_gpt", ""),
                        "current_sovits":data.get("current_sovits", ""),
                    }
            except Exception:
                pass

            # 降级：返回服务在线状态
            health = await client.get(f"{_base_url()}/")
            return {
                "gpt_models":    [],
                "sovits_models": [],
                "current_gpt":   "（服务在线，模型列表接口不支持）",
                "current_sovits":"（请在 GPT-SoVITS WebUI 中查看）",
                "webui_url":     _base_url().replace("9880", "9872"),
                "note":          "请访问 GPT-SoVITS WebUI 查看和切换模型",
            }
    except Exception as e:
        return {"error": str(e), "gpt_models": [], "sovits_models": []}


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 5：切换模型权重
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_set_model(
    gpt_model_path: str = "",
    sovits_model_path: str = "",
) -> dict:
    """切换 GPT-SoVITS 的 GPT 模型或 SoVITS 模型权重。
    训练好新音色模型后，调用此工具切换到新模型。

    gpt_model_path: GPT 模型文件路径（.ckpt），留空则不切换
    sovits_model_path: SoVITS 模型文件路径（.pth），留空则不切换
    返回: {"success": bool, "message": str}
    """
    err = _check()
    if err:
        return {"success": False, "message": err}

    if not gpt_model_path and not sovits_model_path:
        return {"success": False, "message": "请至少提供 gpt_model_path 或 sovits_model_path 之一"}

    try:
        import httpx
        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            if gpt_model_path:
                resp = await client.post(
                    f"{_base_url()}/set_gpt_weights",
                    json={"weights_path": gpt_model_path},
                    headers=_headers(),
                )
                results.append(f"GPT 模型: {'切换成功' if resp.status_code == 200 else f'失败({resp.status_code})'}")

            if sovits_model_path:
                resp = await client.post(
                    f"{_base_url()}/set_sovits_weights",
                    json={"weights_path": sovits_model_path},
                    headers=_headers(),
                )
                results.append(f"SoVITS 模型: {'切换成功' if resp.status_code == 200 else f'失败({resp.status_code})'}")

        return {"success": True, "message": " | ".join(results)}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 6：保存合成音频到工作区
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
def sovits_save_audio(
    audio_b64: str,
    filename: str = "",
    workspace_id: str = "",
    media_type: str = "wav",
) -> dict:
    """将 GPT-SoVITS 合成的音频保存到工作区，返回文件路径。

    audio_b64: 音频的 base64 编码字符串（sovits_tts 的返回值）
    filename: 文件名（不含扩展名，默认自动生成）
    workspace_id: 工作区 ID（提供时写入工作区 audio/ 目录）
    media_type: 音频格式 wav/mp3/ogg（默认 wav）
    返回: {"file_path": str, "workspace_path": str, "size_bytes": int, "url_path": str}
    """
    if not audio_b64:
        return {"error": "audio_b64 为空"}

    try:
        audio_bytes = base64.b64decode(_strip_dataurl(audio_b64))
    except Exception as e:
        return {"error": f"base64 解码失败: {e}"}

    safe_name = filename or f"sovits_{uuid.uuid4().hex[:8]}"
    # 清理文件名中的非法字符
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in safe_name)
    fname = f"{safe_name}.{media_type}"

    # 写入临时输出目录
    out_dir = Path(getattr(config, "H5_OUTPUT_DIR", "/tmp/ep_agent_h5")).parent / "ep_agent_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / fname
    out_path.write_bytes(audio_bytes)

    # 写入工作区 audio/ 目录（可选）
    ws_path = ""
    if workspace_id:
        try:
            from app.agentcore.tools.workspace_tools import _WS_ROOT
            ws_audio_dir = _WS_ROOT / workspace_id / "audio"
            ws_audio_dir.mkdir(parents=True, exist_ok=True)
            ws_file = ws_audio_dir / fname
            ws_file.write_bytes(audio_bytes)
            ws_path = f"audio/{fname}"
        except Exception:
            pass

    return {
        "file_path":      str(out_path),
        "workspace_path": ws_path,
        "url_path":       f"/audio/{fname}",
        "size_bytes":     len(audio_bytes),
        "filename":       fname,
        "media_type":     media_type,
    }
