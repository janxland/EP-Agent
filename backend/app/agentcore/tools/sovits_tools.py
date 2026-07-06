"""
GPT-SoVITS 工具集 v5.0 — 音色克隆 + 语音合成

架构原则（v5.0 彻底重写）：
  ✅ 唯一传参方式：JSON + ref_audio_path 路径字符串（GPT-SoVITS /tts 接口规范）
  ✅ 无 multipart：GPT-SoVITS 从不支持 form-data 上传，已彻底删除
  ✅ Docker 路径映射：SOVITS_IN_DOCKER=true 时自动把音频复制到共享 volume，
     传容器内路径字符串；false 时直接传宿主机绝对路径
  ✅ 路径解析：复用 workspace_tools._resolve_safe()
  ✅ 文件写入：复用 workspace_tools.write_workspace_file(encoding="base64")

.env 配置说明：
  SOVITS_BASE_URL=http://localhost:9880     # TTS API 地址
  SOVITS_IN_DOCKER=true                    # GPT-SoVITS 跑在 Docker 容器里
  SOVITS_HOST_DIR=/path/to/sovits-installer/data   # 宿主机共享目录（volume 左侧）
  SOVITS_CONTAINER_DIR=/workspace          # 容器内对应路径（volume 右侧）
  SOVITS_API_KEY=                          # 鉴权 key，默认不需要

Docker volume 映射示例（docker-compose.yml）：
  volumes:
    - ./data:/workspace
  则：
    SOVITS_HOST_DIR=/absolute/path/to/sovits-installer/data
    SOVITS_CONTAINER_DIR=/workspace
"""
from __future__ import annotations

import base64
import json
import shutil
import uuid
from pathlib import Path
import os as _os
import logging as _logging

from app.agentcore.tools import tool
from app.config import config

_logger = _logging.getLogger("ep_agent.sovits")


# ══════════════════════════════════════════════════════════════════════════════
# 基础配置读取
# ══════════════════════════════════════════════════════════════════════════════

def _base_url() -> str:
    url = _os.getenv("SOVITS_BASE_URL") or getattr(config, "SOVITS_BASE_URL", "") or ""
    return url.rstrip("/")

def _api_key() -> str:
    return _os.getenv("SOVITS_API_KEY") or getattr(config, "SOVITS_API_KEY", "") or ""

def _json_headers() -> dict:
    """JSON 请求头（/tts 唯一支持的格式）。"""
    h = {"Content-Type": "application/json"}
    k = _api_key()
    if k:
        h["Authorization"] = f"Bearer {k}"
    return h

def _in_docker() -> bool:
    """是否 GPT-SoVITS 运行在 Docker 容器内。"""
    return _os.getenv("SOVITS_IN_DOCKER", "").strip().lower() == "true"

def _host_dir() -> str:
    return (_os.getenv("SOVITS_HOST_DIR") or "").rstrip("/")

def _container_dir() -> str:
    return (_os.getenv("SOVITS_CONTAINER_DIR") or "").rstrip("/")

def _check() -> str | None:
    """检查必要配置，未配置时返回错误提示。"""
    if not _base_url():
        return (
            "GPT-SoVITS 服务未配置。"
            "请在 .env 设置 SOVITS_BASE_URL=http://localhost:9880。"
        )
    if _in_docker() and (not _host_dir() or not _container_dir()):
        return (
            "SOVITS_IN_DOCKER=true 但未配置路径映射。\n"
            "请在 .env 设置：\n"
            "  SOVITS_HOST_DIR=/absolute/path/to/sovits-installer/data\n"
            "  SOVITS_CONTAINER_DIR=/workspace"
        )
    return None

def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or f"audio_{uuid.uuid4().hex[:6]}"


# ══════════════════════════════════════════════════════════════════════════════
# 路径处理：宿主机路径 → 容器内路径
# ══════════════════════════════════════════════════════════════════════════════

def _copy_to_shared_volume(host_abs_path: str) -> str:
    """
    将音频文件复制到 Docker 共享目录（SOVITS_HOST_DIR/ep_agent_refs/）。
    若文件已在共享目录内则跳过复制。
    返回文件在共享目录内的宿主机绝对路径。

    调用场景：SOVITS_IN_DOCKER=true，且参考音频在 EP-Agent workspace 内
    （workspace 目录不在 Docker volume 挂载范围内，容器看不到）。
    """
    hd = _host_dir()
    norm = host_abs_path.replace("\\", "/")

    # 已在共享目录内，无需复制
    if norm.startswith(hd + "/") or norm == hd:
        _logger.info("[sovits] 文件已在共享目录，跳过复制: %s", host_abs_path)
        return host_abs_path

    src = Path(host_abs_path)
    if not src.exists():
        raise FileNotFoundError(
            f"参考音频文件不存在（宿主机路径）: {host_abs_path}\n"
            f"请确认文件已正确上传到工作区。"
        )

    dest_dir = Path(hd) / "ep_agent_refs"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    # 源文件更新时才重新复制
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dest)
        _logger.info("[sovits] 音频已复制到共享目录: %s -> %s", src, dest)
    else:
        _logger.info("[sovits] 共享目录已有最新版本，跳过复制: %s", dest)

    return str(dest)


def _to_container_path(shared_host_path: str) -> str:
    """
    将共享目录内的宿主机路径转换为 Docker 容器内路径。

    示例：
      SOVITS_HOST_DIR      = /Users/Admin1/.../sovits-installer/data
      SOVITS_CONTAINER_DIR = /workspace
      输入: /Users/Admin1/.../sovits-installer/data/ep_agent_refs/ref.wav
      输出: /workspace/ep_agent_refs/ref.wav
    """
    hd = _host_dir()
    cd = _container_dir()
    norm = shared_host_path.replace("\\", "/")
    if norm.startswith(hd + "/") or norm == hd:
        rel = norm[len(hd):]          # 保留前导 /
        container_path = cd + rel
        _logger.info("[sovits] 路径映射: %s -> %s", shared_host_path, container_path)
        return container_path
    # 不在映射范围内（理论上不应发生）
    _logger.warning(
        "[sovits] 路径 %r 不在 SOVITS_HOST_DIR=%r 下，直接传原路径（可能失败）",
        shared_host_path, hd,
    )
    return shared_host_path


def _resolve_ref_audio_to_api_path(ref_audio_workspace_path: str) -> tuple[str | None, str | None]:
    """
    将工作区相对路径解析为最终传给 GPT-SoVITS /tts API 的路径字符串。

    返回 (api_path, None) 或 (None, 错误信息)。

    路径处理流程：
      workspace 相对路径
        → _resolve_safe()           → 宿主机绝对路径（文件存在性校验）
        → _copy_to_shared_volume()  → 共享目录内的宿主机路径（仅 SOVITS_IN_DOCKER=true）
        → _to_container_path()      → 容器内路径字符串（仅 SOVITS_IN_DOCKER=true）
      SOVITS_IN_DOCKER=false 时：
        直接返回宿主机绝对路径字符串
    """
    from app.agentcore.tools.workspace_tools import _resolve_safe, _get_project_root, list_workspace_files

    proj_root = _get_project_root()
    _logger.info("[sovits] 解析参考音频路径 | input=%r | proj_root=%s",
                 ref_audio_workspace_path, proj_root)

    if proj_root is None:
        return None, "当前会话未绑定项目（proj_id 为空），无法操作工作区文件。"

    # 步骤 1：workspace 相对路径 → 宿主机绝对路径
    try:
        target = _resolve_safe(ref_audio_workspace_path, proj_root)
    except PermissionError as e:
        return None, str(e)
    except Exception as e:
        return None, f"路径解析失败: {e}"

    if not target.exists() or not target.is_file():
        # 兜底：按文件名在项目内搜索
        audio_exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
        filename = Path(ref_audio_workspace_path).name
        fallback: Path | None = None
        try:
            for p in proj_root.rglob(filename):
                if p.is_file() and p.suffix.lower() in audio_exts:
                    fallback = p
                    break
        except Exception:
            pass

        if fallback is not None:
            target = fallback
            _logger.warning("[sovits] 兜底找到: %r -> %r", ref_audio_workspace_path, str(target))
        else:
            try:
                entries = json.loads(list_workspace_files())
                available = [e["path"] for e in entries if isinstance(e, dict)
                             and Path(e.get("name", "")).suffix.lower() in audio_exts]
            except Exception:
                available = []
            hint = f"项目内可用音频: {available}" if available else "项目目录内暂无音频文件"
            return None, (
                f"参考音频文件不存在: {ref_audio_workspace_path}\n"
                f"  解析绝对路径: {target}\n  {hint}\n"
                f"请先调用 sovits_list_audio_files 查看真实路径。"
            )

    host_abs_path = str(target.resolve())
    _logger.info("[sovits] 宿主机绝对路径: %s", host_abs_path)

    # 步骤 2：根据部署模式确定最终 API 路径
    if _in_docker():
        # Docker 模式：复制到共享目录 → 转换为容器路径
        try:
            shared_path = _copy_to_shared_volume(host_abs_path)
        except FileNotFoundError as e:
            return None, str(e)
        api_path = _to_container_path(shared_path)
        _logger.info("[sovits] Docker 模式 | host=%s -> container=%s", host_abs_path, api_path)
    else:
        # 同机模式：直接传宿主机绝对路径
        api_path = host_abs_path
        _logger.info("[sovits] 同机模式 | api_path=%s", api_path)

    return api_path, None


# ══════════════════════════════════════════════════════════════════════════════
# 音频落盘
# ══════════════════════════════════════════════════════════════════════════════

def _save_audio(audio_bytes: bytes, output_workspace_path: str) -> dict:
    """将音频字节流落盘到工作区，复用 workspace_tools.write_workspace_file。"""
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


# ══════════════════════════════════════════════════════════════════════════════
# 底层 TTS 调用（唯一入口，只走 JSON + 路径字符串）
# ══════════════════════════════════════════════════════════════════════════════

async def _tts_raw(
    text: str,
    ref_audio_api_path: str | None,
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
    底层 TTS 调用。

    唯一传参方式：JSON body + ref_audio_path 路径字符串。
    GPT-SoVITS /tts 接口从不支持 multipart/form-data，此处不做任何文件读取。

    ref_audio_api_path：
      - SOVITS_IN_DOCKER=true  → 容器内路径，如 /workspace/ep_agent_refs/ref.wav
      - SOVITS_IN_DOCKER=false → 宿主机绝对路径，如 /Users/.../ref.wav
    """
    import httpx

    payload: dict = {
        "text":              text,
        "text_lang":         text_lang,
        "prompt_text":       prompt_text,
        "prompt_lang":       prompt_lang,
        "top_k":             top_k,
        "top_p":             top_p,
        "temperature":       temperature,
        "speed_factor":      speed_factor,
        "text_split_method": text_split_method,
        "media_type":        media_type,
        "batch_size":        1,
        "streaming_mode":    False,
    }

    if ref_audio_api_path:
        payload["ref_audio_path"] = ref_audio_api_path

    _logger.info(
        "[sovits] POST /tts | ref_audio_path=%s | text=%r | lang=%s",
        ref_audio_api_path or "(无)",
        text[:60],
        text_lang,
    )

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{_base_url()}/tts",
            json=payload,
            headers=_json_headers(),
        )

    if not resp.is_success:
        try:
            err_body = resp.text[:2000]
        except Exception:
            err_body = "(无法读取响应体)"
        _logger.error("[sovits] /tts 返回 %d: %s", resp.status_code, err_body)
        raise RuntimeError(f"{resp.status_code} — GPT-SoVITS 错误: {err_body}")

    return resp.content


# ══════════════════════════════════════════════════════════════════════════════
# 工具 1：健康检查
# ══════════════════════════════════════════════════════════════════════════════

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
        async with httpx.AsyncClient(timeout=5) as client:
            # 故意传不完整参数，期望返回 400（参数错误）= 服务正常在线
            resp = await client.post(
                f"{_base_url()}/tts",
                json={"text": "test", "text_lang": "zh"},
                headers=_json_headers(),
            )
            online = resp.status_code < 500
            msg = (
                f"服务在线（HTTP {resp.status_code}）"
                if online else
                f"服务异常（HTTP {resp.status_code}），请检查 GPT-SoVITS 进程"
            )
            return {"online": online, "url": _base_url(), "message": msg}
    except Exception as e:
        return {
            "online":  False,
            "url":     _base_url(),
            "message": f"连接失败: {e}。请确认 GPT-SoVITS 已启动（python api_v2.py）",
        }


# ══════════════════════════════════════════════════════════════════════════════
# 工具 2：TTS + 自动落盘（核心工具）
# ══════════════════════════════════════════════════════════════════════════════

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

    ⚠️ ref_audio_workspace_path 为【必填】参数。
    调用前请先用 sovits_list_audio_files() 获取可用音频的 workspace_path，原样传入。

    text: 要合成的目标文本（支持中/英/日/韩/粤）
    ref_audio_workspace_path: 【必填】参考音频的工作区路径（sovits_list_audio_files 返回的 workspace_path 字段）
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

    if not ref_audio_workspace_path or not ref_audio_workspace_path.strip():
        return {"error": "ref_audio_workspace_path 为必填参数，请先调用 sovits_list_audio_files() 获取可用音频路径后传入"}

    # workspace 路径 → API 路径（宿主机绝对路径 或 容器内路径）
    api_path, ref_err = _resolve_ref_audio_to_api_path(ref_audio_workspace_path)
    if ref_err:
        return {"error": ref_err}

    # 调用 GPT-SoVITS（纯 JSON + 路径字符串）
    try:
        audio_bytes = await _tts_raw(
            text=text,
            ref_audio_api_path=api_path,
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

    # 确定输出路径
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


# ══════════════════════════════════════════════════════════════════════════════
# 工具 3：零样本音色克隆 + 自动落盘
# ══════════════════════════════════════════════════════════════════════════════

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
    ref_audio_workspace_path: 参考音频的工作区路径（sovits_list_audio_files 返回的 workspace_path 字段）
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


# ══════════════════════════════════════════════════════════════════════════════
# 工具 4：列出已加载模型
# ══════════════════════════════════════════════════════════════════════════════

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
                resp = await client.get(f"{_base_url()}/models", headers=_json_headers())
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
                "current_sovits": "（请在 GPT-SoVITS WebUI 中查看）",
                "webui_url":     _base_url().replace("9880", "9872"),
                "note":          "请访问 WebUI 查看和切换模型",
            }
    except Exception as e:
        return {"error": str(e), "gpt_models": [], "sovits_models": []}


# ══════════════════════════════════════════════════════════════════════════════
# 工具 5：切换模型权重
# ══════════════════════════════════════════════════════════════════════════════

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
                    headers=_json_headers(),
                )
                results.append(f"GPT 模型: {'切换成功' if resp.status_code == 200 else f'失败({resp.status_code})'}")
            if sovits_model_path:
                resp = await client.post(
                    f"{_base_url()}/set_sovits_weights",
                    json={"weights_path": sovits_model_path},
                    headers=_json_headers(),
                )
                results.append(f"SoVITS 模型: {'切换成功' if resp.status_code == 200 else f'失败({resp.status_code})'}")
        return {"success": True, "message": " | ".join(results)}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# 工具 6：列出项目内所有音频文件
# ══════════════════════════════════════════════════════════════════════════════

@tool(group="sovits")
def sovits_list_audio_files() -> dict:
    """列出当前项目内所有音频文件，返回真实的工作区相对路径。
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
            "workspace_path": e["path"],
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
