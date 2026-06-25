"""
SessionContext — session_getter/session_saver 上下文封装

使用方式：
  # 请求入口注入（pipeline/router.py）
  from app.agentcore.session_context import set_session_context
  set_session_context(get_session_fn, save_session_fn)

  # SubAgent 内部直接调用
  from app.agentcore.session_context import ctx_get_session, ctx_save_session
  sess = ctx_get_session(session_id)
  ctx_save_session(sess)
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable, Any

# ── 上下文变量（每个异步任务独立，天然线程安全）──────────────────────────────

_getter_var: ContextVar[Callable] = ContextVar("session_getter", default=None)  # type: ignore
_saver_var:  ContextVar[Callable] = ContextVar("session_saver",  default=None)  # type: ignore


def set_session_context(getter: Callable, saver: Callable) -> None:
    """
    在请求入口注入 session 操作函数。
    pipeline/router.py 的 /chat 处理函数调用此函数，
    后续所有 SubAgent 无需再透传 session_getter/saver。
    """
    _getter_var.set(getter)
    _saver_var.set(saver)


def ctx_get_session(session_id: str) -> Any:
    """
    从上下文中取 session（Phase 3 SubAgent 直接调用）。
    若上下文未注入（如单元测试），抛出明确错误。
    """
    getter = _getter_var.get()
    if getter is None:
        raise RuntimeError(
            "session_context 未初始化，请在请求入口调用 set_session_context()。"
            "如果是单元测试，请手动调用 set_session_context(mock_getter, mock_saver)。"
        )
    return getter(session_id)


def ctx_save_session(sess: Any) -> None:
    """
    通过上下文保存 session（Phase 3 SubAgent 直接调用）。
    """
    saver = _saver_var.get()
    if saver is None:
        raise RuntimeError(
            "session_context 未初始化，请在请求入口调用 set_session_context()。"
        )
    saver(sess)


def is_context_set() -> bool:
    """检查当前上下文是否已注入（用于健康检查/单元测试断言）。"""
    return _getter_var.get() is not None


# ── 重要记忆（Important Memory）接口 ─────────────────────────────────────────
#
# Session.extra["workspace_files"] 作为跨轮次文件路径记忆中枢：
#   {
#     "midi":  [{"path": ".sky/song.mid", "name": "song.mid", "ts": 1234567890}],
#     "abc":   [...],
#     "json":  [...],
#     "h5":    [...],
#   }
#
# 设计原则：
#   1. 工具执行后（react_executor.py）自动写入，无需 LLM 手动记录
#   2. SubAgent 启动时主动读取，直接获得可用文件路径
#   3. 同类型文件按时间戳倒序，最新的排在前面
#   4. 每类最多保留 10 条，防止 extra 无限膨胀

import time as _time

_FILE_TYPE_MAP = {
    ".mid":  "midi",
    ".midi": "midi",
    ".abc":  "abc",
    ".txt":  "abc",   # Sky 谱 txt 也归入 abc 类
    ".json": "json",
    ".html": "h5",
}
_MAX_PER_TYPE = 10


def remember_workspace_file(session_id: str, path: str, name: str = "") -> None:
    """
    将工作区文件路径写入 Session.extra 重要记忆。
    由 react_executor.py 在工具执行后自动调用，无需 LLM 介入。

    path: 工作区内相对路径，如 ".sky/song.mid"
    name: 文件显示名（可选，默认从 path 推断）
    """
    if not is_context_set():
        return
    try:
        sess = ctx_get_session(session_id)
        if sess is None:
            return

        import os
        ext = os.path.splitext(path)[1].lower()
        ftype = _FILE_TYPE_MAP.get(ext, "other")
        fname = name or os.path.basename(path)

        extra = sess.extra if isinstance(sess.extra, dict) else {}
        files: list = extra.get("workspace_files", {}).get(ftype, [])

        # 去重（同路径只保留最新记录）
        files = [f for f in files if f.get("path") != path]
        files.insert(0, {"path": path, "name": fname, "ts": int(_time.time())})
        files = files[:_MAX_PER_TYPE]

        if "workspace_files" not in extra:
            extra["workspace_files"] = {}
        extra["workspace_files"][ftype] = files
        sess.extra = extra
        ctx_save_session(sess)
    except Exception:
        pass  # 记忆写入失败不影响主流程


def recall_workspace_files(session_id: str, ftype: str = "midi") -> list[dict]:
    """
    从 Session.extra 读取指定类型的工作区文件列表（最新在前）。
    SubAgent 启动时调用，主动获取可用文件路径。

    ftype: "midi" | "abc" | "json" | "h5" | "other"
    返回: [{"path": str, "name": str, "ts": int}, ...]
    """
    if not is_context_set():
        return []
    try:
        sess = ctx_get_session(session_id)
        if sess is None:
            return []
        extra = sess.extra if isinstance(sess.extra, dict) else {}
        return extra.get("workspace_files", {}).get(ftype, [])
    except Exception:
        return []


def recall_latest_file(session_id: str, ftype: str = "midi") -> str:
    """
    快捷方法：返回最新上传的指定类型文件路径（无则返回空字符串）。
    H5Agent 用此方法直接获取 MIDI 路径，无需依赖前端传参。
    """
    files = recall_workspace_files(session_id, ftype)
    return files[0]["path"] if files else ""
