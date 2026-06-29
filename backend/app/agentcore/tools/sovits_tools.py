"""
GPT-SoVITS 工具集 v2.0 — 音色克隆 + 语音合成（解耦重构版）

核心设计原则（防止 b64/二进制污染 LLM 对话层）：
  ❌ 禁止：工具返回 audio_b64 给 LLM
  ❌ 禁止：LLM 把 audio_b64 当参数在工具间传递
  ✅ 正确：音频合成后立即落盘到项目 audio/ 目录，返回 workspace_path
  ✅ 正确：参考音频通过 workspace_path 传递，b64 仅在底层内部解码，绝不上浮

流程设计（文件路径驱动）：
  1. 用户上传参考音频 → 前端保存到工作区 → 传 attachment_workspace_path
  2. LLM 调用 sovits_tts_and_save(text, ref_audio_workspace_path, filename)
     → 底层：读文件 → b64编码 → POST /tts → 收 bytes → 直接落盘
     → 返回：{workspace_path, url_path, size_bytes}（无 audio_b64！）
  3. LLM 拿到 workspace_path 即可播放/引用，无需感知二进制数据

接入方式：
  SOVITS_BASE_URL=http://localhost:9880   # GPT-SoVITS 服务地址
  SOVITS_API_KEY=（若服务需要鉴权，默认不需要）

工具清单（v2.0）：
  sovits_health_check       — 检查服务是否在线
  sovits_tts_and_save       — TTS + 自动落盘（核心工具，替代旧 sovits_tts）
  sovits_clone_and_save     — 零样本克隆 + 自动落盘（替代旧 sovits_clone_voice）
  sovits_list_models        — 列出已部署的音色模型
  sovits_set_model          — 切换 GPT/SoVITS 模型权重
  sovits_list_audio_files   — 列出工作区 audio/ 目录的音频文件

废弃（保留向后兼容但不暴露给 LLM）：
  _sovits_tts_raw           — 内部调用，返回 bytes，不注册为工具
"""
from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Optional

import logging as _logging

from app.agentcore.tools import tool
from app.config import config

_logger = _logging.getLogger("ep_agent.sovits")

# ── 内部辅助 ─────────────────────────────────────────────────────────────────

def _base_url() -> str:
    """动态读取 SOVITS_BASE_URL，支持运行时修改环境变量。"""
    import os
    url = os.getenv("SOVITS_BASE_URL") or getattr(config, "SOVITS_BASE_URL", "") or ""
    return url.rstrip("/")

def _api_key() -> str:
    import os
    return os.getenv("SOVITS_API_KEY") or getattr(config, "SOVITS_API_KEY", "") or ""

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    k = _api_key()
    if k:
        h["Authorization"] = f"Bearer {k}"
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

def _safe_filename(name: str) -> str:
    """将任意字符串转换为安全文件名（仅保留字母数字和 -_）。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or f"audio_{uuid.uuid4().hex[:6]}"

def _get_project_audio_dir() -> Path | None:
    """通过 ContextVar 推断当前项目 audio/ 目录，不依赖 LLM 传参。"""
    try:
        from app.agentcore.session_context import get_current_project_root
        root = get_current_project_root()
        if root:
            d = Path(root) / "audio"
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        pass
    return None

def _save_audio_bytes(audio_bytes: bytes, filename: str, media_type: str) -> dict:
    """
    将音频字节流落盘到项目 audio/ 目录（底层公共函数，不暴露给 LLM）。
    返回 {workspace_path, url_path, file_path, size_bytes, filename}
    b64 数据不出现在返回值中。
    """
    safe = _safe_filename(filename)
    fname = f"{safe}.{media_type}" if not safe.endswith(f".{media_type}") else safe

    # 优先写入项目 audio/ 目录
    audio_dir = _get_project_audio_dir()
    if audio_dir:
        dest = audio_dir / fname
        dest.write_bytes(audio_bytes)
        ws_path = f"audio/{fname}"
        _logger.info("[sovits] 音频已保存到项目目录: %s (%d bytes)", ws_path, len(audio_bytes))
        return {
            "workspace_path": ws_path,
            "file_path":      str(dest),
            "url_path":       f"/audio/{fname}",
            "size_bytes":     len(audio_bytes),
            "filename":       fname,
            "media_type":     media_type,
        }

    # 降级：写入全局临时目录
    import os
    tmp_dir = Path(os.getenv("H5_OUTPUT_DIR", "/tmp/ep_agent_h5")).parent / "ep_agent_audio"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / fname
    dest.write_bytes(audio_bytes)
    _logger.warning("[sovits] project_root 未绑定，音频写入临时目录: %s", dest)
    return {
        "workspace_path": "",          # 无项目目录时留空
        "file_path":      str(dest),
        "url_path":       f"/audio/{fname}",
        "size_bytes":     len(audio_bytes),
        "filename":       fname,
        "media_type":     media_type,
        "warning":        "project_id 未绑定，文件保存在临时目录，重启后可能丢失",
    }


async def _tts_raw(
    text: str,
    ref_audio_bytes: bytes | None,
    prompt_text: str,
    prompt_lang: str,
    text_lang: str,
    top_k: int,
    top_p: float,
    temperature: float,
    speed_factor: float,
    how_to_cut: str,
    media_type: str,
    ref_audio_filename: str = "ref.wav",
) -> bytes:
    """
    底层 TTS 调用，直接返回音频 bytes（不经过 LLM 层）。

    GPT-SoVITS 官方 Docker API（/tts）接受两种调用方式：
      1. 有参考音频（零样本克隆）：multipart/form-data，ref_audio 作为文件字段上传
      2. 无参考音频（使用已加载模型默认音色）：JSON POST，无 ref_audio 字段

    b64 编码仅在此函数内部完成，绝不传递给 LLM 层。
    """
    import httpx

    if ref_audio_bytes:
        # ── 有参考音频：multipart/form-data 上传 ─────────────────────────────
        # GPT-SoVITS /tts 接口参数（multipart）：
        #   text, text_lang, ref_audio_path（文件字段）, prompt_text,
        #   prompt_lang, top_k, top_p, temperature, speed_factor,
        #   how_to_cut, media_type
        # 注意：不含 Content-Type header（httpx 自动设置 multipart boundary）
        h = {}
        k = _api_key()
        if k:
            h["Authorization"] = f"Bearer {k}"

        files = {
            "ref_audio_path": (ref_audio_filename, ref_audio_bytes, "audio/wav"),
        }
        data = {
            "text":         text,
            "text_lang":    text_lang,
            "prompt_text":  prompt_text,
            "prompt_lang":  prompt_lang,
            "top_k":        str(top_k),
            "top_p":        str(top_p),
            "temperature":  str(temperature),
            "speed_factor": str(speed_factor),
            "how_to_cut":   how_to_cut,
            "media_type":   media_type,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_base_url()}/tts",
                files=files,
                data=data,
                headers=h,
            )
            resp.raise_for_status()
            return resp.content
    else:
        # ── 无参考音频：JSON POST，使用服务默认已加载音色 ──────────────────────
        payload: dict = {
            "text":         text,
            "text_lang":    text_lang,
            "prompt_lang":  prompt_lang,
            "prompt_text":  prompt_text,
            "top_k":        top_k,
            "top_p":        top_p,
            "temperature":  temperature,
            "speed_factor": speed_factor,
            "how_to_cut":   how_to_cut,
            "media_type":   media_type,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_base_url()}/tts",
                json=payload,
                headers=_headers(),
            )
            resp.raise_for_status()
            return resp.content


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 1：健康检查
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_health_check() -> dict:
    """检查 GPT-SoVITS 服务是否在线，返回服务状态。
    返回: {"online": bool, "url": str, "message": str}
    """
    err = _check()
    if err:
        return {"online": False, "url": "", "message": err}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(_base_url() + "/")
            online = resp.status_code < 500
            return {
                "online":  online,
                "url":     _base_url(),
                "message": f"服务在线 ({resp.status_code})" if online else f"服务异常 ({resp.status_code})",
            }
    except Exception as e:
        return {
            "online":  False,
            "url":     _base_url(),
            "message": f"连接失败: {e}。请确认 GPT-SoVITS 已启动（python webui.py）",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 2：TTS + 自动落盘（核心工具）
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_tts_and_save(
    text: str,
    filename: str = "",
    ref_audio_workspace_path: str = "",
    prompt_text: str = "",
    prompt_lang: str = "zh",
    text_lang: str = "zh",
    speed_factor: float = 1.0,
    how_to_cut: str = "凑四句一切",
    media_type: str = "wav",
    top_k: int = 5,
    top_p: float = 1.0,
    temperature: float = 1.0,
) -> dict:
    """使用 GPT-SoVITS 将文本转为语音，并自动保存到项目 audio/ 目录。
    ⚠️ 不返回 base64，只返回文件路径，避免二进制数据污染对话上下文。

    text: 要合成的目标文本（支持中/英/日/韩/粤）
    filename: 保存文件名（不含扩展名，默认自动生成）
    ref_audio_workspace_path: 参考音频的工作区路径（如 audio/ref.wav），用于零样本克隆音色
    prompt_text: 参考音频对应的文字内容（提高克隆质量，可选）
    prompt_lang: 参考音频语言 zh/en/ja/ko/yue（默认 zh）
    text_lang: 目标文本语言 zh/en/ja/ko/yue（默认 zh）
    speed_factor: 语速倍率 0.5-2.0（默认 1.0）
    how_to_cut: 文本切分 凑四句一切/按中文句号切/按英文句号切/按标点符号切
    media_type: 输出格式 wav/mp3/ogg（默认 wav）
    返回: {"workspace_path": str, "url_path": str, "size_bytes": int, "filename": str}
    """
    err = _check()
    if err:
        return {"error": err}

    if not text.strip():
        return {"error": "text 不能为空"}

    # 读取参考音频（通过工作区路径，不接受 b64 参数）
    ref_bytes: bytes | None = None
    if ref_audio_workspace_path:
        try:
            from app.agentcore.session_context import get_current_project_root
            root = get_current_project_root()
            if root:
                ref_file = Path(root) / ref_audio_workspace_path
                if ref_file.exists():
                    ref_bytes = ref_file.read_bytes()
                    _logger.info("[sovits] 读取参考音频: %s (%d bytes)", ref_audio_workspace_path, len(ref_bytes))
                else:
                    return {"error": f"参考音频文件不存在: {ref_audio_workspace_path}"}
            else:
                return {"error": "project_root 未绑定，无法读取参考音频文件"}
        except Exception as e:
            return {"error": f"读取参考音频失败: {e}"}

    try:
        audio_bytes = await _tts_raw(
            text=text,
            ref_audio_bytes=ref_bytes,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            text_lang=text_lang,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            speed_factor=speed_factor,
            how_to_cut=how_to_cut,
            media_type=media_type,
        )
    except Exception as e:
        return {"error": f"TTS 合成失败: {e}"}

    fname = filename or f"tts_{uuid.uuid4().hex[:8]}"
    result = _save_audio_bytes(audio_bytes, fname, media_type)
    result["text_length"] = len(text)
    result["provider"] = "sovits"
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 3：零样本音色克隆 + 自动落盘
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_clone_and_save(
    target_text: str,
    ref_audio_workspace_path: str,
    filename: str = "",
    reference_text: str = "",
    language: str = "zh",
    speed_factor: float = 1.0,
    media_type: str = "wav",
) -> dict:
    """零样本音色克隆：用参考音频克隆音色，合成目标文本，自动保存到项目 audio/ 目录。
    只需 3-10 秒清晰人声参考音频，无需训练即可克隆任意音色。
    ⚠️ 参考音频通过工作区路径传递，不接受 base64 参数。

    target_text: 要用克隆音色朗读的文字
    ref_audio_workspace_path: 参考音频的工作区路径（如 audio/ref.wav 或 shared/voice.mp3）
    filename: 输出文件名（不含扩展名，默认自动生成）
    reference_text: 参考音频对应的文字（提高克隆质量，可选）
    language: 语言 zh/en/ja/ko/yue（默认 zh）
    speed_factor: 语速倍率 0.5-2.0（默认 1.0）
    media_type: 输出格式 wav/mp3/ogg（默认 wav）
    返回: {"workspace_path": str, "url_path": str, "size_bytes": int, "filename": str}
    """
    return await sovits_tts_and_save(
        text=target_text,
        filename=filename or f"clone_{uuid.uuid4().hex[:8]}",
        ref_audio_workspace_path=ref_audio_workspace_path,
        prompt_text=reference_text,
        prompt_lang=language,
        text_lang=language,
        speed_factor=speed_factor,
        media_type=media_type,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 4：列出已加载模型
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_list_models() -> dict:
    """列出 GPT-SoVITS 服务上已加载的 GPT 和 SoVITS 模型。
    返回: {"gpt_models": [...], "sovits_models": [...], "webui_url": str}
    """
    err = _check()
    if err:
        return {"error": err, "gpt_models": [], "sovits_models": []}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(f"{_base_url()}/models", headers=_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "gpt_models":     data.get("gpt_models", []),
                        "sovits_models":  data.get("sovits_models", []),
                        "current_gpt":    data.get("current_gpt", ""),
                        "current_sovits": data.get("current_sovits", ""),
                        "webui_url":      _base_url().replace("9880", "9872"),
                    }
            except Exception:
                pass

            return {
                "gpt_models":    [],
                "sovits_models": [],
                "current_gpt":   "（服务在线，模型列表接口不支持）",
                "current_sovits":"（请在 GPT-SoVITS WebUI 中查看）",
                "webui_url":     _base_url().replace("9880", "9872"),
                "note":          "请访问 WebUI 查看和切换模型",
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
    训练好新音色模型后调用此工具切换。

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
# 工具 6：列出工作区音频文件
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
def sovits_list_audio_files() -> dict:
    """列出当前项目 audio/ 目录下的所有音频文件。
    用于确认已保存的音频、选择参考音频路径。
    返回: {"files": [{"name": str, "workspace_path": str, "size_bytes": int, "url_path": str}]}
    """
    audio_dir = _get_project_audio_dir()
    if not audio_dir:
        return {
            "files": [],
            "message": "project_root 未绑定，无法列出音频文件",
        }

    audio_exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac"}
    files = []
    for f in sorted(audio_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in audio_exts:
            files.append({
                "name":           f.name,
                "workspace_path": f"audio/{f.name}",
                "url_path":       f"/audio/{f.name}",
                "size_bytes":     f.stat().st_size,
            })
    return {
        "files":   files,
        "count":   len(files),
        "dir":     str(audio_dir),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容：旧工具 sovits_save_audio（保留注册但标记废弃）
# LLM 不应再调用此工具，改用 sovits_tts_and_save / sovits_clone_and_save
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
def sovits_save_audio(
    audio_b64: str,
    filename: str = "",
    media_type: str = "wav",
) -> dict:
    """[已废弃，请改用 sovits_tts_and_save] 将音频 base64 保存到工作区。
    ⚠️ 此工具要求传入 audio_b64，会导致大量 base64 数据流入 LLM 上下文，已废弃。
    新工具 sovits_tts_and_save / sovits_clone_and_save 在合成后自动落盘，无需此工具。
    """
    if not audio_b64:
        return {"error": "audio_b64 为空，请改用 sovits_tts_and_save 直接合成并保存"}
    try:
        audio_bytes = base64.b64decode(_strip_dataurl(audio_b64))
    except Exception as e:
        return {"error": f"base64 解码失败: {e}"}

    fname = filename or f"sovits_{uuid.uuid4().hex[:8]}"
    return _save_audio_bytes(audio_bytes, fname, media_type)
