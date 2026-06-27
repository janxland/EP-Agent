"""
工作区文件工具集 — workspace_tools

三层架构设计（Workspace → Project → Session/Topic）：
  - 工具完全不感知 workspace_id / project_id
  - 通过 ContextVar（session_context.py）自动推断当前 session 所属的项目根目录
  - 所有文件操作限制在 project_root 内，不能跨项目操作
  - Agent/LLM 只传文件的相对路径，系统自动拼接绝对路径

项目目录结构：
  workspace/{ws_id}/projects/{proj_id}/
    .sky/          Sky 游戏谱子文件（JSON / ABC / MIDI）
    shared/        通用共享文件（图片、H5、音频等）
    h5/            H5 海报文件

安全限制：
  - 所有路径必须在 project_root 内（防路径穿越）
  - 文件大小限制 10MB（二进制）/ 2MB（文本）
  - 禁止写入 .py / .sh / .exe 等可执行文件
"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Optional

from app.agentcore.tools import tool

# ─── 全局工作区根目录（机械路径，工具不感知）─────────────────────────────────

_WS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "workspace"
_WS_ROOT.mkdir(parents=True, exist_ok=True)

# 禁止写入的扩展名（安全）
_BLOCKED_EXTS = {".py", ".sh", ".bash", ".zsh", ".exe", ".bat", ".cmd",
                 ".ps1", ".rb", ".pl", ".php"}

# 文件大小限制
_MAX_TEXT_BYTES   = 2 * 1024 * 1024   # 2 MB
_MAX_BINARY_BYTES = 10 * 1024 * 1024  # 10 MB

# 可读取的文本扩展名（其余按二进制处理）
_TEXT_EXTS = {
    ".abc", ".txt", ".md", ".json", ".html", ".htm", ".css", ".js", ".ts",
    ".xml", ".yaml", ".yml", ".csv", ".svg", ".log",
}


def _get_project_root() -> Path:
    """通过 ContextVar 推断当前 session 的项目根目录，无 project_id 返回 None。"""
    try:
        from app.agentcore.session_context import get_current_project_root, get_current_project_id
        proj_id = get_current_project_id()
        if not proj_id:
            return None  # type: ignore
        root_str = get_current_project_root()
        if root_str:
            root = Path(root_str)
            # 安全校验：路径必须在 _WS_ROOT 内
            try:
                root.resolve().relative_to(_WS_ROOT.resolve())
            except ValueError:
                return None  # type: ignore
            root.mkdir(parents=True, exist_ok=True)
            return root
    except Exception:
        pass
    return None  # type: ignore


def _resolve_safe(rel_path: str, project_root: Path | None = None) -> Path:
    """
    将相对路径解析为项目内绝对路径，并验证不超出项目根目录（防路径穿越）。
    rel_path: 相对于项目根目录的路径，如 '.sky/song.mid' 或 'shared/img.png'
    """
    root = project_root or _get_project_root()
    if root is None:
        raise PermissionError("会话未绑定项目，无法操作文件。")
    norm = os.path.normpath(rel_path)
    if norm.startswith("..") or norm.startswith("/") or norm.startswith("\\"):
        raise PermissionError(f"路径越界：{rel_path}")
    target = (root / norm).resolve()
    root_abs = root.resolve()
    if not str(target).startswith(str(root_abs) + os.sep) and str(target) != str(root_abs):
        raise PermissionError(f"路径越界：{rel_path}")
    if not str(target).startswith(str(_WS_ROOT.resolve())):
        raise PermissionError(f"路径越界：{rel_path}")
    return target


def _file_info(path: Path, base: Path) -> dict:
    """生成单个文件的描述信息"""
    rel = str(path.relative_to(base))
    stat = path.stat()
    ext = path.suffix.lower()
    is_text = ext in _TEXT_EXTS
    mime, _ = mimetypes.guess_type(str(path))
    return {
        "path": rel,
        "name": path.name,
        "ext":  ext.lstrip("."),
        "size": stat.st_size,
        "is_text": is_text,
        "mime": mime or "application/octet-stream",
    }


# ─── Tools ───────────────────────────────────────────────────────────────────

@tool(group="workspace")
def list_workspace_files(subdir: str = "") -> str:
    """列出当前项目内的文件树，返回 JSON 格式的文件列表。
    subdir: 可选子目录，留空则列出整个项目目录（如 '.sky' 或 'shared'）
    返回 JSON 数组，每项含 path/name/ext/size/is_text/mime 字段
    """
    import json
    root = _get_project_root()
    if root is None:
        return json.dumps({"error": "会话未绑定项目，无法列出文件。"})
    if not root.exists():
        return json.dumps([])

    scan_dir = root / subdir if subdir else root
    if not scan_dir.exists():
        return json.dumps([])

    files = []
    for p in sorted(scan_dir.rglob("*")):
        if p.is_file():
            files.append(_file_info(p, root))

    return json.dumps(files, ensure_ascii=False, indent=2)


@tool(group="workspace")
def read_workspace_file(file_path: str) -> str:
    """读取项目内文本文件内容（ABC / JSON / HTML / TXT / MD 等）。
    file_path: 相对于项目根目录的文件路径（如 '.sky/score.abc' 或 'shared/readme.md'）
    返回文件的文本内容字符串
    """
    target = _resolve_safe(file_path)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    if target.stat().st_size > _MAX_TEXT_BYTES:
        raise ValueError(f"文件过大（>{_MAX_TEXT_BYTES // 1024}KB），请拆分读取")
    return target.read_text(encoding="utf-8", errors="replace")


@tool(group="workspace")
def get_workspace_file_url(file_path: str) -> dict:
    """
    获取项目内文件的可访问 URL 和元数据（适用于图片 / MIDI / 音频等二进制文件）。
    ⚠️ 不返回 base64 — 二进制内容永远不进入 LLM context，防止上下文爆炸。

    用途：
      - 图片：获取 URL 后传给 visual_understanding 工具进行视觉分析
      - MIDI：获取 workspace_path 后传给 generate_h5_from_midi 生成 H5
      - 音频：获取 URL 后传给前端播放器

    file_path: 相对于项目根目录的文件路径（如 '.sky/song.mid' 或 'shared/images/img.png'）
    返回: {"workspace_path": str, "url": str, "size": int, "mime": str, "ext": str}
    """
    root = _get_project_root()
    if root is None:
        return {"error": "会话未绑定项目，无法操作文件。"}
    target = _resolve_safe(file_path, root)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    stat = target.stat()
    if stat.st_size > _MAX_BINARY_BYTES:
        raise ValueError(f"文件过大（>{_MAX_BINARY_BYTES // 1024 // 1024}MB）")
    ext = target.suffix.lower()
    mime, _ = mimetypes.guess_type(str(target))
    # 构造可访问 URL（通过工作区静态服务）
    rel_to_ws = str(target.relative_to(_WS_ROOT))
    url = f"/workspace/{rel_to_ws.replace(os.sep, '/')}"
    return {
        "workspace_path": file_path,
        "url":            url,
        "size":           stat.st_size,
        "mime":           mime or "application/octet-stream",
        "ext":            ext.lstrip("."),
        "_note": "使用 visual_understanding(images=[url]) 分析图片；MIDI 请用 generate_h5_from_midi(midi_workspace_path=file_path)",
    }


@tool(group="workspace")
def write_workspace_file(file_path: str, content: str, encoding: str = "text") -> dict:
    """写入或创建项目内文件。
    file_path: 相对路径（如 'shared/output.html' 或 '.sky/score.abc'）
    content: 文件内容（encoding=text 时为文本，encoding=base64 时为 base64 字符串）
    encoding: 'text'（默认）或 'base64'（二进制文件）
    返回写入结果描述
    """
    ext = Path(file_path).suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止写入 {ext} 类型文件（安全限制）")

    target = _resolve_safe(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if encoding == "base64":
        data = base64.b64decode(content)
        if len(data) > _MAX_BINARY_BYTES:
            raise ValueError("文件过大")
        target.write_bytes(data)
        return {
            "workspace_path": file_path,
            "message": f"已写入二进制文件：{file_path}（{len(data)} 字节）",
            "size": len(data),
        }
    else:
        if len(content.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ValueError("文件过大")
        target.write_text(content, encoding="utf-8")
        return {
            "workspace_path": file_path,
            "message": f"已写入文本文件：{file_path}（{len(content)} 字符）",
            "size": len(content.encode("utf-8")),
        }


@tool(group="workspace")
def delete_workspace_file(file_path: str) -> str:
    """删除项目内文件。
    file_path: 相对路径（如 'shared/old.html'）
    返回删除结果描述
    """
    target = _resolve_safe(file_path)
    if not target.exists():
        return f"文件不存在（已删除或从未创建）：{file_path}"
    target.unlink()
    return f"已删除：{file_path}"


@tool(group="workspace")
def copy_workspace_file(src_path: str, dst_path: str) -> str:
    """复制项目内文件到新路径（目标不存在则创建，已存在则覆盖）。
    src_path: 源文件相对路径（如 '.sky/score.abc'）
    dst_path: 目标文件相对路径（如 '.sky/score_backup.abc'）
    返回复制结果描述
    """
    import shutil
    root = _get_project_root()
    if root is None:
        raise PermissionError("当前会话未绑定项目（project_id 为空），无法操作文件。")
    src = _resolve_safe(src_path, root)
    dst = _resolve_safe(dst_path, root)
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src_path}")
    ext = dst.suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止复制为 {ext} 类型文件（安全限制）")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    size = dst.stat().st_size
    return f"已复制：{src_path} → {dst_path}（{size} 字节）"


@tool(group="workspace")
def rename_workspace_file(src_path: str, new_name: str) -> str:
    """重命名项目内文件（仅改名，保持在同一目录）。
    src_path: 源文件相对路径（如 'shared/old_name.html'）
    new_name: 新文件名（仅文件名部分，如 'new_name.html'，不含路径分隔符）
    返回重命名结果描述
    """
    root = _get_project_root()
    if root is None:
        raise PermissionError("当前会话未绑定项目（project_id 为空），无法操作文件。")
    src = _resolve_safe(src_path, root)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在：{src_path}")
    if "/" in new_name or "\\" in new_name:
        raise ValueError("new_name 只能是文件名，不能含路径分隔符。如需移动请用 move_workspace_file")
    ext = Path(new_name).suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止重命名为 {ext} 类型文件（安全限制）")
    dst = src.parent / new_name
    dst_safe = _resolve_safe(str(dst.relative_to(root)), root)
    src.rename(dst_safe)
    return f"已重命名：{src_path} → {dst_safe.relative_to(root)}"


@tool(group="workspace")
def move_workspace_file(src_path: str, dst_path: str) -> str:
    """移动项目内文件到新路径（跨目录移动，目标已存在则覆盖）。
    src_path: 源文件相对路径（如 '.sky/temp.html'）
    dst_path: 目标文件相对路径（如 'shared/output.html'）
    返回移动结果描述
    """
    import shutil
    root = _get_project_root()
    if root is None:
        raise PermissionError("当前会话未绑定项目（project_id 为空），无法操作文件。")
    src = _resolve_safe(src_path, root)
    dst = _resolve_safe(dst_path, root)
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src_path}")
    ext = dst.suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止移动为 {ext} 类型文件（安全限制）")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"已移动：{src_path} → {dst_path}"


# ─── 谱子库专用工具（.sky/ 目录）─────────────────────────────────────────────

def save_score_to_workspace_impl(
    abc_notation: str,
    title: str = "",
    overwrite: bool = True,
    workspace_id: str = "",
) -> dict:
    """
    将 ABC 谱子保存到项目 .sky/ 目录（内部实现，供 Agent 层直接调用）。
    文件名由 title 生成，去掉非法字符，后缀 .abc。
    返回 {"path": ".sky/xxx.abc", "existed": bool}
    """
    import re as _re
    root = _get_project_root()
    if root is None:
        return {"error": "会话未绑定项目，无法保存谱子。"}

    safe_title = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title or "score").strip() or "score"
    file_name = f"{safe_title}.abc"
    rel_path  = f".sky/{file_name}"

    sky_dir = root / ".sky"
    sky_dir.mkdir(parents=True, exist_ok=True)
    target = sky_dir / file_name

    existed = target.exists()
    if not overwrite and existed:
        import time as _time
        ts = int(_time.time()) % 10000
        file_name = f"{safe_title}_{ts}.abc"
        rel_path  = f".sky/{file_name}"
        target = sky_dir / file_name

    target.write_text(abc_notation, encoding="utf-8")
    return {"path": rel_path, "existed": existed, "name": file_name}


def list_workspace_scores_impl(workspace_id: str = "") -> list[dict]:
    """
    列出项目 .sky/ 目录下所有 ABC 谱子文件（内部实现，供 Agent 层直接调用）。
    通过 ContextVar 推断项目根目录，必须有 project_id 才能列出文件。
    返回列表，每项含 path/name/size/title（从 ABC 头部解析）。
    """
    root = _get_project_root()
    if root is None:
        return []  # 静默返回空列表，不影响意图路由

    sky_dir = root / ".sky"
    if not sky_dir.exists():
        return []

    results = []
    for p in sorted(sky_dir.glob("*.abc")):
        stat = p.stat()
        title = p.stem
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if line.startswith("T:"):
                    title = line[2:].strip()
                    break
        except Exception:
            pass
        results.append({
            "path":  f".sky/{p.name}",
            "name":  p.name,
            "title": title,
            "size":  stat.st_size,
        })
    return results
