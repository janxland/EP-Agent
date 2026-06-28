"""
工作区文件工具集 — workspace_tools v2.0

三层架构设计（Workspace → Project → Session/Topic）：
  - 工具完全不感知 workspace_id / project_id
  - 通过 ContextVar（session_context.py）自动推断当前 session 所属的项目根目录
  - 所有文件操作限制在 project_root 内，不能跨项目操作
  - Agent/LLM 只传文件的相对路径，系统自动拼接绝对路径

工具清单（v2.0）：
  ── 读取 ──
  list_workspace_files      — 列出文件树（含元数据）
  read_workspace_files      — 批量并行读多个文本文件（核心！）
  get_workspace_file_url    — 获取文件的可访问 URL（适用于二进制文件）

  ── 写入 ──
  write_workspace_file      — 整文件写入（新建 or 覆盖）
  edit_workspace_file       — 精准字符串替换（带文件锁，同文件串行安全）
  run_write_tasks_in_parallel — 并行写多个新文件（无依赖时高效批量创建）

  ── 文件管理 ──
  delete_workspace_file     — 删除
  copy_workspace_file       — 复制
  rename_workspace_file     — 重命名
  move_workspace_file       — 移动

安全限制：
  - 所有路径必须在 project_root 内（防路径穿越）
  - 文件大小限制 10MB（二进制）/ 2MB（文本）
  - 禁止写入 .py / .sh / .exe 等可执行文件
  - edit_workspace_file 使用文件级锁，保证同一文件不被并发修改
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import threading
from pathlib import Path
from typing import Optional

import logging as _logging
from app.agentcore.tools import tool

_audit_logger = _logging.getLogger("ep_agent.audit")

# ─── 全局工作区根目录 ─────────────────────────────────────────────────────────

_WS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "workspace"
_WS_ROOT.mkdir(parents=True, exist_ok=True)

# 禁止写入的扩展名（安全）
_BLOCKED_EXTS = {".py", ".sh", ".bash", ".zsh", ".exe", ".bat", ".cmd",
                 ".ps1", ".rb", ".pl", ".php"}

# 文件大小限制
_MAX_TEXT_BYTES   = 2 * 1024 * 1024   # 2 MB
_MAX_BINARY_BYTES = 10 * 1024 * 1024  # 10 MB

# 可读取的文本扩展名
_TEXT_EXTS = {
    ".abc", ".txt", ".md", ".json", ".html", ".htm", ".css", ".js", ".ts",
    ".xml", ".yaml", ".yml", ".csv", ".svg", ".log",
}

# ─── 文件锁注册表（edit_workspace_file 使用，保证同文件串行安全）────────────
_file_locks: dict[str, threading.Lock] = {}
_file_locks_meta = threading.Lock()


def _get_file_lock(abs_path: str) -> threading.Lock:
    """获取指定文件的锁（不存在则创建）。"""
    with _file_locks_meta:
        if abs_path not in _file_locks:
            _file_locks[abs_path] = threading.Lock()
        return _file_locks[abs_path]


# ─── 内部工具函数 ─────────────────────────────────────────────────────────────

def _get_project_root() -> Path | None:
    """
    通过 ContextVar 推断当前 session 的项目根目录。
    v4.0 修复：project_id 为空时降级到 workspace 级目录，而非返回 None 导致工具宕机。
    优先级：session.project_id → session.workspace_id 目录 → None
    """
    try:
        from app.agentcore.session_context import (
            get_current_project_root, get_current_project_id, get_current_workspace_id
        )
        # 优先：有 project_id → 返回精确项目目录
        proj_id = get_current_project_id()
        if proj_id:
            root_str = get_current_project_root()
            if root_str:
                root = Path(root_str)
                try:
                    root.resolve().relative_to(_WS_ROOT.resolve())
                except ValueError:
                    pass
                else:
                    root.mkdir(parents=True, exist_ok=True)
                    return root

        # 降级：无 project_id 但有 workspace_id → 使用 workspace 根目录
        ws_id = get_current_workspace_id()
        if ws_id:
            import logging as _log
            _log.getLogger("ep_agent").warning(
                "[workspace_tools] project_id 未绑定，降级到 workspace 目录: ws=%s", ws_id[:8]
            )
            ws_root = _WS_ROOT / ws_id
            ws_root.mkdir(parents=True, exist_ok=True)
            return ws_root
    except Exception as _e:
        import logging as _log
        _log.getLogger("ep_agent").warning("[workspace_tools] _get_project_root 异常: %s", _e)
    return None


def _resolve_safe(rel_path: str, project_root: Path | None = None) -> Path:
    """
    将相对路径解析为项目内绝对路径，并验证不超出项目根目录（防路径穿越）。
    rel_path: 相对于项目根目录的路径，如 'src/main.py' 或 'assets/img.png'
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
        "path":    rel,
        "name":    path.name,
        "ext":     ext.lstrip("."),
        "size":    stat.st_size,
        "is_text": is_text,
        "mime":    mime or "application/octet-stream",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 读取工具
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="workspace")
def list_workspace_files(subdir: str = "") -> str:
    """列出当前项目内的文件树，返回 JSON 格式的文件列表。
    subdir: 可选子目录，留空则列出整个项目目录（如 '.sky' 或 'shared'）
    返回 JSON 数组，每项含 path/name/ext/size/is_text/mime 字段
    """
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
def read_workspace_files(file_paths: list, offset_lines: int = 0, limit_lines: int = 0) -> dict:
    """
    【核心读取工具】批量并行读取多个项目内文本文件。
    一次调用读多个文件，比多次单文件调用高效得多。

    file_paths: 相对路径列表，如 ["src/main.js", "docs/readme.md", "config/settings.json"]
    offset_lines: 从第几行开始读（0 = 从头，支持负数从尾部计算）
    limit_lines: 最多读多少行（0 = 全部）
    返回: {
      "files": [
        {"path": str, "content": str, "lines": int, "size_kb": float},  ← 成功
        {"path": str, "error": str},                                     ← 失败
      ],
      "summary": "成功 N/M 个文件"
    }
    """
    root = _get_project_root()
    if root is None:
        return {"error": "会话未绑定项目，无法读取文件。", "files": []}

    results = []
    for rel_path in file_paths:
        try:
            target = _resolve_safe(rel_path, root)
            if not target.exists():
                results.append({"path": rel_path, "error": f"文件不存在：{rel_path}"})
                continue
            if target.stat().st_size > _MAX_TEXT_BYTES:
                results.append({"path": rel_path, "error": f"文件过大（>{_MAX_TEXT_BYTES // 1024}KB）"})
                continue

            text = target.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total = len(lines)

            # 支持负数 offset（从尾部）
            if offset_lines < 0:
                start = max(0, total + offset_lines)
            else:
                start = offset_lines

            if limit_lines > 0:
                selected = lines[start:start + limit_lines]
            else:
                selected = lines[start:]

            content = "\n".join(selected)
            results.append({
                "path":     rel_path,
                "content":  content,
                "lines":    total,
                "shown":    len(selected),
                "size_kb":  round(target.stat().st_size / 1024, 1),
            })
        except Exception as e:
            results.append({"path": rel_path, "error": str(e)})

    ok = sum(1 for r in results if "content" in r)
    return {
        "files":   results,
        "summary": f"成功读取 {ok}/{len(file_paths)} 个文件",
    }


# 保留旧接口向后兼容（单文件读取）
@tool(group="workspace")
def read_workspace_file(file_path: str) -> str:
    """读取项目内单个文本文件内容（向后兼容接口，推荐使用 read_workspace_files 批量读取）。
    file_path: 相对于项目根目录的文件路径（如 'src/score.abc'）
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
    获取项目内文件的可访问 URL 和元数据（适用于二进制文件，不返回文件内容本身）。
    ⚠️ 不返回 base64 — 二进制内容永远不进入 LLM context，防止上下文爆炸。

    file_path: 相对于项目根目录的文件路径（如 'assets/image.png' 或 'output/audio.mp3'）
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
    rel_to_ws = str(target.relative_to(_WS_ROOT))
    url = f"/workspace/{rel_to_ws.replace(os.sep, '/')}"
    return {
        "workspace_path": file_path,
        "url":            url,
        "size":           stat.st_size,
        "mime":           mime or "application/octet-stream",
        "ext":            ext.lstrip("."),
        "_note": "二进制文件请通过 url 字段访问；文本文件请用 read_workspace_files 直接读取内容",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 写入工具
# ═══════════════════════════════════════════════════════════════════════════════

@tool(group="workspace")
def write_workspace_file(file_path: str, content: str, encoding: str = "text") -> dict:
    """
    整文件写入（新建 or 覆盖）。适合新文件创建或整文件重写。
    对已有文件的局部修改请优先用 edit_workspace_file（更安全，带锁）。

    file_path: 相对路径（如 'output/result.html' 或 'data/score.abc'）
    content: 文件内容（encoding=text 时为文本，encoding=base64 时为 base64 字符串）
    encoding: 'text'（默认）或 'base64'（二进制文件）
    返回: {"workspace_path": str, "message": str, "size": int}
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
        # M6 操作审计日志
        try:
            from app.agentcore.session_context import get_current_trace_id, get_current_session_id
            _audit_logger.info(
                "[audit] WRITE_BINARY path=%s size=%d session=%s trace=%s",
                file_path, len(data),
                get_current_session_id()[:8] or "?",
                get_current_trace_id()[:8] or "?",
            )
        except Exception:
            pass
        return {
            "workspace_path": file_path,
            "message": f"已写入二进制文件：{file_path}（{len(data)} 字节）",
            "size": len(data),
        }
    else:
        if len(content.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ValueError("文件过大")
        size = len(content.encode("utf-8"))
        target.write_text(content, encoding="utf-8")
        # M6 操作审计日志
        try:
            from app.agentcore.session_context import get_current_trace_id, get_current_session_id
            _audit_logger.info(
                "[audit] WRITE_TEXT path=%s lines=%d size=%d session=%s trace=%s",
                file_path, len(content.splitlines()), size,
                get_current_session_id()[:8] or "?",
                get_current_trace_id()[:8] or "?",
            )
        except Exception:
            pass
        return {
            "workspace_path": file_path,
            "message": f"已写入：{file_path}（{len(content)} 字符，{len(content.splitlines())} 行）",
            "size": size,
        }


@tool(group="workspace")
def edit_workspace_file(
    file_path: str,
    old_string: str,
    new_string: str,
    expected_replacements: int = 1,
) -> dict:
    """
    【精准编辑】在已有文件中做精确字符串替换，带文件锁保证并发安全。

    规则（与 Claude Code / Cursor 一致）：
    - 同一文件同时只能有一个 edit 操作（文件锁保证）
    - 不同文件可以并行 edit（锁粒度是文件级）
    - old_string 必须与文件内容完全匹配（含空格/缩进/换行）
    - 若匹配次数 ≠ expected_replacements，操作失败（不修改文件）

    file_path: 相对路径（如 'src/index.html' 或 'config/app.json'）
    old_string: 要替换的原始字符串（必须与文件内容精确匹配）
    new_string: 替换后的新字符串
    expected_replacements: 期望替换次数（默认 1，防止意外多处替换）
    返回: {"workspace_path": str, "replacements": int, "message": str}
    """
    target = _resolve_safe(file_path)
    if not target.exists():
        return {"error": f"文件不存在：{file_path}，请先用 write_workspace_file 创建"}
    if old_string == new_string:
        return {"error": "old_string 与 new_string 相同，无需修改"}
    if not old_string:
        return {"error": "old_string 不能为空"}

    lock = _get_file_lock(str(target.resolve()))
    with lock:
        content = target.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)

        if count == 0:
            # 给出有用的上下文帮助调试
            lines = content.splitlines()
            preview = "\n".join(lines[:20]) + ("\n..." if len(lines) > 20 else "")
            return {
                "error": f"old_string 在文件中未找到（文件共 {len(lines)} 行）",
                "file_preview": preview,
                "tip": "请用 read_workspace_files 重新读取文件确认精确内容后再编辑",
            }

        if count != expected_replacements:
            return {
                "error": (
                    f"old_string 匹配到 {count} 处，期望 {expected_replacements} 处。"
                    f"请增加上下文使 old_string 唯一，或设置 expected_replacements={count}"
                ),
                "actual_count": count,
            }

        new_content = content.replace(old_string, new_string, count)
        target.write_text(new_content, encoding="utf-8")

    old_lines = len(old_string.splitlines())
    new_lines = len(new_string.splitlines())
    # M6 操作审计日志
    try:
        from app.agentcore.session_context import get_current_trace_id, get_current_session_id
        _audit_logger.info(
            "[audit] EDIT path=%s replacements=%d lines_delta=%d session=%s trace=%s",
            file_path, count, new_lines - old_lines,
            get_current_session_id()[:8] or "?",
            get_current_trace_id()[:8] or "?",
        )
    except Exception:
        pass
    return {
        "workspace_path":  file_path,
        "replacements":    count,
        "lines_delta":     new_lines - old_lines,
        "message": (
            f"已替换 {count} 处：{file_path}"
            f"（行数变化：{'+' if new_lines >= old_lines else ''}{new_lines - old_lines}）"
        ),
    }


@tool(group="workspace")
def run_write_tasks_in_parallel(tasks: list) -> dict:
    """
    【并行写文件】同时写入多个新文件，适合无依赖关系的批量创建场景。
    ⚠️ 只适用于新文件整文件生成；已有文件的修改必须用 edit_workspace_file（串行）。
    ⚠️ 确保任务间文件互不依赖、互不 import，再使用此工具。

    tasks: 写入任务列表，每项格式：
      {
        "file_path": "相对路径",       ← 必填
        "content": "文件完整内容",     ← 必填
        "description": "简短说明",     ← 可选，用于结果摘要
      }
    返回: {
      "results": [{"file_path", "ok", "message"/"error"}, ...],
      "summary": "成功 N/M 个文件",
      "written_files": ["成功写入的路径列表"],
    }
    """
    root = _get_project_root()
    if root is None:
        return {"error": "会话未绑定项目，无法写入文件。", "results": []}

    results = []
    written = []

    # 验证所有任务（写前检查，快速失败）
    validated = []
    for task in tasks:
        fp   = task.get("file_path", "")
        cont = task.get("content", "")
        desc = task.get("description", fp)

        if not fp:
            results.append({"file_path": fp, "ok": False, "error": "file_path 不能为空"})
            continue

        ext = Path(fp).suffix.lower()
        if ext in _BLOCKED_EXTS:
            results.append({"file_path": fp, "ok": False, "error": f"禁止写入 {ext} 类型文件"})
            continue

        if len(cont.encode("utf-8")) > _MAX_TEXT_BYTES:
            results.append({"file_path": fp, "ok": False, "error": "文件内容超过 2MB 限制"})
            continue

        try:
            target = _resolve_safe(fp, root)
            validated.append((fp, cont, desc, target))
        except Exception as e:
            results.append({"file_path": fp, "ok": False, "error": str(e)})

    # 并行写入（线程池，每个文件独立锁）
    def _write_one(fp: str, content: str, desc: str, target: Path):
        lock = _get_file_lock(str(target.resolve()))
        with lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        size_kb = round(len(content.encode("utf-8")) / 1024, 1)
        lines   = len(content.splitlines())
        return {
            "file_path":   fp,
            "ok":          True,
            "description": desc,
            "message":     f"已写入：{fp}（{lines} 行，{size_kb}KB）",
            "lines":       lines,
            "size_kb":     size_kb,
        }

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(validated))) as executor:
        futures = {
            executor.submit(_write_one, fp, cont, desc, target): fp
            for fp, cont, desc, target in validated
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                r = future.result()
                results.append(r)
                if r["ok"]:
                    written.append(r["file_path"])
            except Exception as e:
                fp = futures[future]
                results.append({"file_path": fp, "ok": False, "error": str(e)})

    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "results":       results,
        "summary":       f"成功写入 {ok_count}/{len(tasks)} 个文件",
        "written_files": written,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 文件管理工具
# ═══════════════════════════════════════════════════════════════════════════════

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
    src_path: 源文件相对路径（如 'src/score.abc'）
    dst_path: 目标文件相对路径（如 'backup/score_backup.abc'）
    返回复制结果描述
    """
    import shutil
    root = _get_project_root()
    if root is None:
        raise PermissionError("当前会话未绑定项目，无法操作文件。")
    src = _resolve_safe(src_path, root)
    dst = _resolve_safe(dst_path, root)
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src_path}")
    ext = dst.suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止复制为 {ext} 类型文件（安全限制）")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return f"已复制：{src_path} → {dst_path}（{dst.stat().st_size} 字节）"


@tool(group="workspace")
def rename_workspace_file(src_path: str, new_name: str) -> str:
    """重命名项目内文件（仅改名，保持在同一目录）。
    src_path: 源文件相对路径（如 'shared/old_name.html'）
    new_name: 新文件名（仅文件名部分，如 'new_name.html'，不含路径分隔符）
    返回重命名结果描述
    """
    root = _get_project_root()
    if root is None:
        raise PermissionError("当前会话未绑定项目，无法操作文件。")
    src = _resolve_safe(src_path, root)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在：{src_path}")
    if "/" in new_name or "\\" in new_name:
        raise ValueError("new_name 只能是文件名，不能含路径分隔符。如需移动请用 move_workspace_file")
    ext = Path(new_name).suffix.lower()
    if ext in _BLOCKED_EXTS:
        raise PermissionError(f"禁止重命名为 {ext} 类型文件（安全限制）")
    dst_safe = _resolve_safe(str(src.parent.relative_to(root) / new_name), root)
    src.rename(dst_safe)
    return f"已重命名：{src_path} → {dst_safe.relative_to(root)}"


@tool(group="workspace")
def move_workspace_file(src_path: str, dst_path: str) -> str:
    """移动项目内文件到新路径（跨目录移动，目标已存在则覆盖）。
    src_path: 源文件相对路径（如 'tmp/temp.html'）
    dst_path: 目标文件相对路径（如 'shared/output.html'）
    返回移动结果描述
    """
    import shutil
    root = _get_project_root()
    if root is None:
        raise PermissionError("当前会话未绑定项目，无法操作文件。")
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


# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容 re-export（业务逻辑已迁移至 abc_tools.py）
# 调用方（create_agent / convert_agent / edit_agent / universal_runner / router）
# 仍可通过 `from app.agentcore.tools.workspace_tools import xxx` 正常导入，
# 无需修改任何调用代码。
# ═══════════════════════════════════════════════════════════════════════════════

from app.agentcore.tools.abc_tools import (  # noqa: E402
    save_score_to_workspace_impl,
    list_workspace_scores_impl,
)
