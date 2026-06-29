"""
GPT-SoVITS 工具集 v4.0 — 音色克隆 + 语音合成

设计原则：
  ✅ 路径解析：完全复用 workspace_tools._resolve_safe()，不自行拼接路径
  ✅ 文件列表：完全复用 workspace_tools.list_workspace_files()，不自行 rglob
  ✅ 文件写入：完全复用 workspace_tools.write_workspace_file(encoding="base64")
  ✅ 无 proj_id：直接拒绝，不兜底
  ✅ 绝对路径：_resolve_safe() 返回的 Path 已经是绝对路径，直接 str() 传给 GPT-SoVITS

接入方式：
  SOVITS_BASE_URL=http://localhost:9880
  SOVITS_API_KEY=（若服务需要鉴权，默认不需要）
"""
from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import logging as _logging

from app.agentcore.tools import tool
from app.config import config

_logger = _logging.getLogger("ep_agent.sovits")


# ── 内部辅助 ─────────────────────────────────────────────────────────────────

def _base_url() -> str:
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
    if not _base_url():
        return (
            "GPT-SoVITS 服务未配置。"
            "请先部署 GPT-SoVITS（参考 EP-Agent/sovits-installer/），"
            "然后设置环境变量 SOVITS_BASE_URL=http://localhost:9880。"
        )
    return None

def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or f"audio_{uuid.uuid4().hex[:6]}"


def _resolve_ref_audio(ref_audio_workspace_path: str) -> tuple[str | None, str | None]:
    """
    将 workspace_path 解析为服务器绝对路径（GPT-SoVITS ref_audio_path 必须是绝对路径）。
    直接复用 workspace_tools._resolve_safe()，它已处理 proj_root 推断、路径安全校验。
    返回 (绝对路径字符串, None) 或 (None, 错误信息)。
    """
    from app.agentcore.tools.workspace_tools import _resolve_safe, _get_project_root, list_workspace_files

    # ── 诊断：先打印 proj_root，方便排查 ──────────────────────────────────────
    proj_root = _get_project_root()
    _logger.info("[sovits] _resolve_ref_audio 开始 | input=%r | proj_root=%s",
                 ref_audio_workspace_path, proj_root)

    if proj_root is None:
        return None, "当前会话未绑定项目（proj_id 为空），无法操作工作区文件。请确认 session 已关联 project。"

    try:
        target = _resolve_safe(ref_audio_workspace_path, proj_root)  # 传入 proj_root，不依赖 ContextVar 二次推断
    except PermissionError as e:
        _logger.error("[sovits] 路径越界: %s", e)
        return None, str(e)
    except Exception as e:
        _logger.error("[sovits] 路径解析异常: %s", e)
        return None, f"路径解析失败: {e}"

    _logger.info("[sovits] 解析结果: input=%r -> abs=%s | exists=%s",
                 ref_audio_workspace_path, target, target.exists())

    if not target.exists() or not target.is_file():
        # 用基础工具列出项目内可用音频辅助诊断
        audio_exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
        try:
            entries = json.loads(list_workspace_files())
            available = [e["path"] for e in entries if isinstance(e, dict)
                         and Path(e.get("name", "")).suffix.lower() in audio_exts]
        except Exception:
            available = []
        hint = f"项目内可用音频: {available}" if available else "项目目录内暂无音频文件"
        _logger.error("[sovits] 文件不存在: abs=%s | proj_root=%s | %s", target, proj_root, hint)
        return None, (
            f"参考音频文件不存在: {ref_audio_workspace_path}\n"
            f"  解析绝对路径: {target}\n"
            f"  项目根目录: {proj_root}\n"
            f"  {hint}\n"
            f"请先调用 sovits_list_audio_files 查看真实路径，将 workspace_path 字段原值传入。"
        )

    abs_path = str(target.resolve())  # 强制 resolve() 得到真实绝对路径（消除符号链接）
    _logger.info("[sovits] ✅ 参考音频绝对路径确认: %r -> %r", ref_audio_workspace_path, abs_path)
    return abs_path, None


def _save_audio(audio_bytes: bytes, output_workspace_path: str) -> dict:
    """
    将音频字节流落盘，完全复用 workspace_tools.write_workspace_file(encoding='base64')。
    output_workspace_path 由调用方指定，不做任何目录假设。
    """
    from app.agentcore.tools.workspace_tools import write_workspace_file
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    result = write_workspace_file(output_workspace_path, b64, encoding="base64")
    if "error" in result:
        return result
    _logger.info("[sovits] 音频已保存: %s (%d bytes)", output_workspace_path, len(audio_bytes))
    return {
        "workspace_path": output_workspace_path,
        "size_bytes":     len(audio_bytes),
        "filename":       Path(output_workspace_path).name,
    }


async def _tts_raw(
    text: str,
    ref_audio_path: str | None,
    prompt_text: str,
    prompt_lang: str,
    text_lang: str,
    top_k: int,
    top_p: float,
    temperature: float,
    speed_factor: float,
    text_split_method: str,
    media_type: str,
) -> bytes:
    """
    底层 TTS 调用，返回音频 bytes。

    GPT-SoVITS 官方 api_v2.py /tts 接口字段名（必须完全一致）：
      ref_audio_path   — 参考音频服务器【绝对路径】字符串（必填，字段名含下划线）
      prompt_text      — 参考音频对应文字（可选）
      prompt_lang      — 参考音频语言
      text             — 目标文本
      text_lang        — 目标语言
      text_split_method — 文本切分方法（含下划线）
      top_k / top_p / temperature / speed_factor / media_type
    """
    import httpx

    payload: dict = {
        "text":             text,
        "text_lang":        text_lang,
        "prompt_text":      prompt_text,
        "prompt_lang":      prompt_lang,
        "top_k":            top_k,
        "top_p":            top_p,
        "temperature":      temperature,
        "speed_factor":     speed_factor,
        "text_split_method": text_split_method,
        "media_type":       media_type,
    }
    if ref_audio_path:
        payload["ref_audio_path"] = ref_audio_path  # 绝对路径，GPT-SoVITS 直接读取（字段名必须含下划线）

    _logger.info(
        "[sovits] POST /tts | ref_audio_path=%s | text=%r | text_lang=%s",
        ref_audio_path or "(无参考音频)", text[:60], text_lang,
    )

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{_base_url()}/tts",
            json=payload,
            headers=_headers(),
        )
        if not resp.is_success:
            try:
                err_body = resp.text[:2000]
            except Exception:
                err_body = "(无法读取响应体)"
            _logger.error("[sovits] /tts 返回 %d: %s", resp.status_code, err_body)
            raise httpx.HTTPStatusError(
                f"{resp.status_code} — GPT-SoVITS 错误: {err_body}",
                request=resp.request,
                response=resp,
            )
        return resp.content


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 1：健康检查
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_health_check() -> dict:
    """检查 GPT-SoVITS 服务是否在线。
    返回: {"online": bool, "url": str, "message": str}
    """
    err = _check()
    if err:
        return {"online": False, "url": "", "message": err}

    try:
        import httpx
        # 用 POST /tts 做真实探测（GET / 返回 404 会被误判为在线）
        # 故意传不完整参数，期望返回 400（参数错误）= 服务正常
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{_base_url()}/tts",
                json={"text": "test", "text_lang": "zh"},
                headers={"Content-Type": "application/json"},
            )
            # 2xx / 4xx 都说明服务在线（400=参数不全但服务正常，200=成功）
            # 只有 5xx 或连接失败才是真正的服务异常
            online = resp.status_code < 500
            if online:
                msg = f"服务在线（HTTP {resp.status_code}）"
            else:
                msg = f"服务异常（HTTP {resp.status_code}），请检查 GPT-SoVITS 进程"
            return {"online": online, "url": _base_url(), "message": msg}
    except Exception as e:
        return {
            "online":  False,
            "url":     _base_url(),
            "message": f"连接失败: {e}。请确认 GPT-SoVITS 已启动（python webui.py 或 api_v2.py）",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 2：TTS + 自动落盘（核心工具）
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
async def sovits_tts_and_save(
    text: str,
    ref_audio_workspace_path: str,
    filename: str = "",
    output_workspace_path: str = "",
    prompt_text: str = "",
    prompt_lang: str = "zh",
    text_lang: str = "zh",
    speed_factor: float = 1.0,
    text_split_method: str = "cut5",
    media_type: str = "wav",
    top_k: int = 15,
    top_p: float = 1.0,
    temperature: float = 1.0,
) -> dict:
    """使用 GPT-SoVITS 将文本转为语音，自动保存到项目工作区。

    ⚠️ ref_audio_workspace_path 为【必填】参数，GPT-SoVITS 强制要求参考音频，缺少此参数将报 ref_audio_path is required 错误。
    调用前请先用 sovits_list_audio_files() 获取可用音频的 workspace_path，原样传入。

    text: 要合成的目标文本（支持中/英/日/韩/粤）
    ref_audio_workspace_path: 【必填】参考音频的工作区路径（由 sovits_list_audio_files 返回的 workspace_path 字段，原样传入，不加任何前缀）
    filename: 输出文件名（不含扩展名，默认自动生成）
    output_workspace_path: 输出文件的工作区路径（含扩展名）；留空则自动生成
    prompt_text: 参考音频对应的文字内容（提高克隆质量，可选）
    prompt_lang: 参考音频语言 zh/en/ja/ko/yue（默认 zh）
    text_lang: 目标文本语言 zh/en/ja/ko/yue（默认 zh）
    speed_factor: 语速倍率 0.5-2.0（默认 1.0）
    text_split_method: 文本切分方法 cut5/cut0/cut1/cut2/cut3/cut4（默认 cut5）
    media_type: 输出格式 wav/mp3/ogg（默认 wav）
    返回: {"workspace_path": str, "size_bytes": int, "filename": str}
    """
    err = _check()
    if err:
        return {"error": err}

    if not text.strip():
        return {"error": "text 不能为空"}

    # 解析参考音频为绝对路径（必填，GPT-SoVITS 强制要求）
    if not ref_audio_workspace_path or not ref_audio_workspace_path.strip():
        return {"error": "ref_audio_workspace_path 为必填参数，请先调用 sovits_list_audio_files() 获取可用音频路径后传入"}
    ref_abs_path, ref_err = _resolve_ref_audio(ref_audio_workspace_path)
    if ref_err:
        return {"error": ref_err}

    # 调用 GPT-SoVITS
    try:
        audio_bytes = await _tts_raw(
            text=text,
            ref_audio_path=ref_abs_path,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            text_lang=text_lang,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            speed_factor=speed_factor,
            text_split_method=text_split_method,
            media_type=media_type,
        )
    except Exception as e:
        return {"error": f"TTS 合成失败: {e}"}

    # 确定输出路径（无前缀，直接在项目根）
    if not output_workspace_path:
        fname = _safe_filename(filename or f"tts_{uuid.uuid4().hex[:8]}")
        if not fname.endswith(f".{media_type}"):
            fname = f"{fname}.{media_type}"
        output_workspace_path = fname

    result = _save_audio(audio_bytes, output_workspace_path)
    if "error" not in result:
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
    output_workspace_path: str = "",
    reference_text: str = "",
    language: str = "zh",
    speed_factor: float = 1.0,
    media_type: str = "wav",
) -> dict:
    """零样本音色克隆：用参考音频克隆音色，合成目标文本，自动保存到项目工作区。

    target_text: 要用克隆音色朗读的文字
    ref_audio_workspace_path: 参考音频的工作区路径（由 sovits_list_audio_files 返回的 workspace_path 字段，原样传入）
    filename: 输出文件名（不含扩展名，默认自动生成）
    output_workspace_path: 输出文件的工作区路径（含扩展名）；留空则自动生成
    reference_text: 参考音频对应的文字（提高克隆质量，可选）
    language: 语言 zh/en/ja/ko/yue（默认 zh）
    speed_factor: 语速倍率 0.5-2.0（默认 1.0）
    media_type: 输出格式 wav/mp3/ogg（默认 wav）
    返回: {"workspace_path": str, "size_bytes": int, "filename": str}
    """
    return await sovits_tts_and_save(
        text=target_text,
        filename=filename or f"clone_{uuid.uuid4().hex[:8]}",
        ref_audio_workspace_path=ref_audio_workspace_path,
        output_workspace_path=output_workspace_path,
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
# 工具 6：列出项目内所有音频文件
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
def sovits_list_audio_files() -> dict:
    """列出当前项目内所有音频文件，复用 list_workspace_files 基础工具，返回真实的工作区相对路径。
    ⚠️ 将返回的 workspace_path 字段直接传给 ref_audio_workspace_path 参数，原样传入，不加任何前缀。
    返回: {"files": [{"name": str, "workspace_path": str, "size_bytes": int}], "count": int}
    """
    from app.agentcore.tools.workspace_tools import list_workspace_files

    raw = list_workspace_files()
    try:
        entries = json.loads(raw)
    except Exception:
        return {"files": [], "count": 0, "error": f"list_workspace_files 返回异常: {raw[:200]}"}

    if isinstance(entries, dict) and "error" in entries:
        return {"files": [], "count": 0, "error": entries["error"]}

    audio_exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
    files = [
        {
            "name":           e["name"],
            "workspace_path": e["path"],   # 已是相对于 proj_root 的真实路径，原样传入即可
            "size_bytes":     e.get("size", 0),
        }
        for e in entries
        if isinstance(e, dict) and Path(e.get("name", "")).suffix.lower() in audio_exts
    ]

    return {
        "files": files,
        "count": len(files),
        "hint":  "将 workspace_path 字段的值直接传给 ref_audio_workspace_path 参数，不加任何前缀",
    }
