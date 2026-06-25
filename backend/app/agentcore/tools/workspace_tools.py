"""
工作区文件工具集 — workspace_tools

设计理念（对齐 .magic 体系）：
  工作区 = 文件夹，包含项目所有文件，类似 Cursor 的 workspace folder 概念。
  约定子目录：
    .sky/          Sky 游戏谱子文件（JSON / ABC / MIDI）—— 类比 .magic/skills
    shared/        通用共享文件（图片、H5、音频等）
    shared/images/ 图片（粘贴上传自动路由到此）

提供 Agent 读写工作区文件的能力，注册到 "workspace" 分组。
支持：
  - list_workspace_files    列出工作区文件树（含 MIDI / ABC / JSON / H5 / 图片等）
  - read_workspace_file     读取文件内容（文本，ABC/JSON/HTML 等）
  - get_workspace_file_url  获取二进制文件的可访问 URL（图片/MIDI/音频，不返回 base64）
  - write_workspace_file    写入/创建工作区文件
  - delete_workspace_file   删除工作区文件
  - copy_workspace_file     复制文件
  - rename_workspace_file   重命名文件
  - move_workspace_file     移动文件（跨目录）

核心原则（防止上下文爆炸）：
  - 文本文件（ABC/JSON/HTML/MD）：直接读取文本内容，可进入 LLM context
  - 二进制文件（图片/MIDI/音频）：只传 URL/路径，LLM 不处理二进制/base64
  - 图片处理：通过 get_workspace_file_url 获取 URL，用 visual_understanding 工具分析
  - MIDI 处理：通过 generate_h5_from_midi(midi_workspace_path=...) 直接生成 H5，CDN 库播放

工作区根目录：EP-Agent/backend/data/workspace/
  每个 workspace_id 对应一个子目录，
  .sky/ 放谱子，shared/ 放通用文件，session 临时文件放 session_id/ 下。

安全限制：
  - 所有路径必须在 workspace 根目录内（防路径穿越）
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

# ─── 工作区根目录 ─────────────────────────────────────────────────────────────

_WS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "workspace"
_WS_ROOT.mkdir(parents=True, exist_ok=True)

# 禁止写入的扩展名（安全）
_BLOCKED_EXTS = {".py", ".sh", ".bash", ".zsh", ".exe", ".bat", ".cmd",
                 ".ps1", ".rb", ".pl", ".php"}

# 文件大小限制
_MAX_TEXT_BYTES  = 2 * 1024 * 1024   # 2 MB
_MAX_BINARY_BYTES = 10 * 1024 * 1024  # 10 MB

# 可读取的文本扩展名（其余按二进制处理）
_TEXT_EXTS = {
    ".abc", ".txt", ".md", ".json", ".html", ".htm", ".css", ".js", ".ts",
    ".xml", ".yaml", ".yml", ".csv", ".svg", ".log",
}


def _resolve_safe(workspace_id: str, rel_path: str) -> Path:
    """
    将 (workspace_id, rel_path) 解析为绝对路径，并验证不超出工作区根目录。
    rel_path 可以是 'shared/xxx.mid' 或 'session_xxx/yyy.abc' 等相对路径。
    """
    base = _WS_ROOT / workspace_id
    target = (base / rel_path).resolve()
    # 安全检查：目标必须在 workspace 目录内
    if not str(target).startswith(str(base.resolve())):
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
def list_workspace_files(workspace_id: str, subdir: str = "") -> str:
    """列出工作区内的文件树，返回 JSON 格式的文件列表。
    workspace_id: 工作区 ID（如 ws_abc123）
    subdir: 可选子目录，留空则列出整个工作区（如 'shared' 或 'session_xxx'）
    返回 JSON 数组，每项含 path/name/ext/size/is_text/mime 字段
    """
    import json
    base = _WS_ROOT / workspace_id
    if not base.exists():
        return json.dumps([])

    scan_dir = base / subdir if subdir else base
    if not scan_dir.exists():
        return json.dumps([])

    files = []
    for p in sorted(scan_dir.rglob("*")):
        if p.is_file():
            files.append(_file_info(p, base))

    return json.dumps(files, ensure_ascii=False, indent=2)


@tool(group="workspace")
def read_workspace_file(workspace_id: str, file_path: str) -> str:
    """读取工作区文本文件内容（ABC / JSON / HTML / TXT / MD 等）。
    workspace_id: 工作区 ID
    file_path: 相对于工作区根目录的文件路径（如 'shared/score.abc'）
    返回文件的文本内容字符串
    """
    target = _resolve_safe(workspace_id, file_path)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    if target.stat().st_size > _MAX_TEXT_BYTES:
        raise ValueError(f"文件过大（>{_MAX_TEXT_BYTES // 1024}KB），请使用 read_workspace_file_b64")
    return target.read_text(encoding="utf-8", errors="replace")


@tool(group="workspace")
def get_workspace_file_url(workspace_id: str, file_path: str) -> dict:
    """
    获取工作区文件的可访问 URL 和元数据（适用于图片 / MIDI / 音频等二进制文件）。
    ⚠️ 不返回 base64 — 二进制内容永远不进入 LLM context，防止上下文爆炸。

    用途：
      - 图片：获取 URL 后传给 visual_understanding 工具进行视觉分析
      - MIDI：获取 workspace_path 后传给 generate_h5_from_midi 生成 H5
      - 音频：获取 URL 后传给前端播放器

    workspace_id: 工作区 ID
    file_path: 相对于工作区根目录的文件路径（如 'shared/images/截图.png'）
    返回: {"workspace_path": str, "url": str, "size": int, "mime": str, "ext": str}
    """
    target = _resolve_safe(workspace_id, file_path)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    stat = target.stat()
    if stat.st_size > _MAX_BINARY_BYTES:
        raise ValueError(f"文件过大（>{_MAX_BINARY_BYTES // 1024 // 1024}MB）")
    ext = target.suffix.lower()
    mime, _ = mimetypes.guess_type(str(target))
    # 构造可访问 URL（通过 /workspace/{workspace_id}/{file_path} 静态服务）
    url = f"/workspace/{workspace_id}/{file_path}"
    return {
        "workspace_path": file_path,
        "url":            url,
        "size":           stat.st_size,
        "mime":           mime or "application/octet-stream",
        "ext":            ext.lstrip("."),
        "_note":          "使用 visual_understanding(images=[url]) 分析图片；MIDI 请用 generate_h5_from_midi(midi_workspace_path=workspace_path)",
    }


@tool(group="workspace")
def write_workspace_file(workspace_id: str, file_path: str, content: str,
                         encoding: str = "text") -> str:
    """写入或创建工作区文件。
    workspace_id: 工作区 ID
    file_path: 相对路径（如 'shared/output.html' 或 'shared/score.abc'）
    content: 文件内容（encoding=text 时为文本，encoding=base64 时为 base64 字符串）
    encoding: 'text'（默认）或 'base64'（二进制文件）
    返回写入结果描述
    """
    ext = Path(file_path).suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止写入 {ext} 类型文件（安全限制）")

    target = _resolve_safe(workspace_id, file_path)
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
def delete_workspace_file(workspace_id: str, file_path: str) -> str:
    """删除工作区文件。
    workspace_id: 工作区 ID
    file_path: 相对路径（如 'shared/old.html'）
    返回删除结果描述
    """
    target = _resolve_safe(workspace_id, file_path)
    if not target.exists():
        return f"文件不存在（已删除或从未创建）：{file_path}"
    target.unlink()
    return f"已删除：{file_path}"


@tool(group="workspace")
def copy_workspace_file(workspace_id: str, src_path: str, dst_path: str) -> str:
    """复制工作区文件到新路径（目标不存在则创建，已存在则覆盖）。
    workspace_id: 工作区 ID
    src_path: 源文件相对路径（如 'shared/score.abc'）
    dst_path: 目标文件相对路径（如 'shared/score_backup.abc'）
    返回复制结果描述
    """
    import shutil
    src = _resolve_safe(workspace_id, src_path)
    dst = _resolve_safe(workspace_id, dst_path)
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
def rename_workspace_file(workspace_id: str, src_path: str, new_name: str) -> str:
    """重命名工作区文件（仅改名，保持在同一目录）。
    workspace_id: 工作区 ID
    src_path: 源文件相对路径（如 'shared/old_name.html'）
    new_name: 新文件名（仅文件名部分，如 'new_name.html'，不含路径分隔符）
    返回重命名结果描述
    """
    src = _resolve_safe(workspace_id, src_path)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在：{src_path}")
    if "/" in new_name or "\\" in new_name:
        raise ValueError("new_name 只能是文件名，不能含路径分隔符。如需移动请用 copy + delete")
    ext = Path(new_name).suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止重命名为 {ext} 类型文件（安全限制）")
    dst = src.parent / new_name
    dst_safe = _resolve_safe(workspace_id, str(dst.relative_to(_WS_ROOT / workspace_id)))
    src.rename(dst_safe)
    return f"已重命名：{src_path} → {dst_safe.relative_to(_WS_ROOT / workspace_id)}"


@tool(group="workspace")
def move_workspace_file(workspace_id: str, src_path: str, dst_path: str) -> str:
    """移动工作区文件到新路径（跨目录移动，目标已存在则覆盖）。
    workspace_id: 工作区 ID
    src_path: 源文件相对路径（如 'session_abc/temp.html'）
    dst_path: 目标文件相对路径（如 'shared/output.html'）
    返回移动结果描述
    """
    import shutil
    src = _resolve_safe(workspace_id, src_path)
    dst = _resolve_safe(workspace_id, dst_path)
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
    workspace_id: str,
    abc_notation: str,
    title: str = "",
    overwrite: bool = True,
) -> dict:
    """
    将 ABC 谱子保存到工作区 .sky/ 目录（内部实现，供 Agent 层直接调用）。
    文件名由 title 生成，去掉非法字符，后缀 .abc。
    返回 {"path": ".sky/xxx.abc", "existed": bool}
    """
    import re as _re
    # 清理文件名（去掉特殊字符，保留中英文数字空格）
    safe_title = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title or "score").strip() or "score"
    file_name = f"{safe_title}.abc"
    rel_path  = f".sky/{file_name}"

    sky_dir = _WS_ROOT / workspace_id / ".sky"
    sky_dir.mkdir(parents=True, exist_ok=True)
    target = sky_dir / file_name

    existed = target.exists()
    if not overwrite and existed:
        # 自动加后缀避免覆盖
        import time as _time
        ts = int(_time.time()) % 10000
        file_name = f"{safe_title}_{ts}.abc"
        rel_path  = f".sky/{file_name}"
        target = sky_dir / file_name

    target.write_text(abc_notation, encoding="utf-8")
    return {"path": rel_path, "existed": existed, "name": file_name}


def list_workspace_scores_impl(workspace_id: str) -> list[dict]:
    """
    列出工作区 .sky/ 目录下所有 ABC 谱子文件（内部实现，供 Agent 层直接调用）。
    返回列表，每项含 path/name/size/title（从 ABC 头部解析）。
    """
    sky_dir = _WS_ROOT / workspace_id / ".sky"
    if not sky_dir.exists():
        return []

    results = []
    for p in sorted(sky_dir.glob("*.abc")):
        stat = p.stat()
        # 从 ABC 头部提取标题
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
