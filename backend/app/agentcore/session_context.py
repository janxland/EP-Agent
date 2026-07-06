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

import logging as _logging_mod
from contextvars import ContextVar
from typing import Callable, Any

# ── 上下文变量（每个异步任务独立，天然线程安全）──────────────────────────────

_getter_var: ContextVar[Callable] = ContextVar("session_getter", default=None)  # type: ignore
_saver_var:  ContextVar[Callable] = ContextVar("session_saver",  default=None)  # type: ignore

# ── 当前请求的 session_id（由 ReactExecutor 在工具调用前注入）────────────────
# 工具内部可通过 get_current_session_id() 获取，无需 LLM 传参
_current_session_id_var: ContextVar[str] = ContextVar("current_session_id", default="")

# ── 全链路 Trace ID（fix40 M3）────────────────────────────────────────────────
# 每个请求在 universal_runner.run() 入口生成唯一 trace_id，通过 ContextVar 传递。
# 所有 logging 调用可通过 get_current_trace_id() 自动带上 trace_id，无需手动透传。
_current_trace_id_var: ContextVar[str] = ContextVar("current_trace_id", default="")


def set_current_session_id(session_id: str) -> None:
    """由 ReactExecutor 在每轮工具调用前注入当前 session_id。"""
    _current_session_id_var.set(session_id)


def set_current_trace_id(trace_id: str) -> None:
    """在请求入口注入 trace_id，全链路日志追踪用。"""
    _current_trace_id_var.set(trace_id)


def get_current_trace_id() -> str:
    """获取当前请求的 trace_id，供所有模块日志调用。"""
    return _current_trace_id_var.get()


def get_current_session_id() -> str:
    """工具内部调用，获取当前请求的 session_id（无需 LLM 传参）。"""
    return _current_session_id_var.get()


def get_ep_logger(name: str = "ep_agent"):
    """
    返回自动携带 trace_id 的结构化 logger（fix40 M3）。

    用法：
      logger = get_ep_logger(__name__)
      logger.info("[create_agent] 创作完成，abc_lines=%d", lines)
      # 输出：[trace=abc12345] [create_agent] 创作完成，abc_lines=32
    """
    import logging

    class _TraceAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            tid = get_current_trace_id()
            prefix = f"[trace={tid[:8]}] " if tid else ""
            return f"{prefix}{msg}", kwargs

    return _TraceAdapter(logging.getLogger(name), {})


def get_current_workspace_id() -> str:
    """
    工具内部调用，自动推断当前请求的 workspace_id。
    优先级：session DB 查询 → 空字符串（工具自行兜底）
    """
    sid = get_current_session_id()
    if not sid:
        return ""
    try:
        from app.pipeline import db as _db
        info = _db.get_session_info(sid)
        return (info or {}).get("workspace_id") or ""
    except Exception:
        return ""


def get_current_project_id() -> str:
    """
    工具内部调用，自动推断当前请求的 project_id。
    project_id 是文件系统隔离边界：工具只能操作所属 project 的文件。
    优先级：session DB 查询 → 空字符串
    """
    sid = get_current_session_id()
    if not sid:
        return ""
    try:
        from app.pipeline import db as _db
        info = _db.get_session_info(sid)
        return (info or {}).get("project_id") or ""
    except Exception:
        return ""


_logger_ctx = _logging_mod.getLogger("ep_agent.session_context")


def get_current_project_root() -> str:
    """
    返回当前 session 对应的项目文件根目录（绝对路径字符串）。
    路径：data/workspace/{ws_id}/projects/{proj_id}/
    """
    from pathlib import Path as _Path

    sid = get_current_session_id()
    if not sid:
        _logger_ctx.debug("get_current_project_root: session_id 为空，ContextVar 未注入")
        return ""
    try:
        from app.pipeline import db as _db
        info = _db.get_session_info(sid)
        ws_id   = (info or {}).get("workspace_id") or ""
        proj_id = (info or {}).get("project_id")   or ""
        if not ws_id or not proj_id:
            _logger_ctx.debug(
                "get_current_project_root: ws_id=%r proj_id=%r 为空，无法定位项目目录",
                ws_id, proj_id,
            )
            return ""
        _WS_ROOT = _Path(__file__).resolve().parent.parent.parent / "data" / "workspace"
        root = _WS_ROOT / ws_id / "projects" / proj_id
        root.mkdir(parents=True, exist_ok=True)
        _logger_ctx.debug("get_current_project_root: root=%s", root)
        return str(root)
    except Exception as _e:
        _logger_ctx.warning("get_current_project_root: 异常: %s", _e)
        return ""


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
    ".txt":  "sky_json",  # Sky 谱 txt = Sky JSON，不是 ABC
    ".json": "sky_json",  # Sky JSON（.json 统一归入 sky_json）
    ".html": "h5",
    # 音频文件（音色克隆 / TTS 合成输出）
    ".wav":  "audio",
    ".mp3":  "audio",
    ".ogg":  "audio",
    ".flac": "audio",
    ".m4a":  "audio",
    ".aac":  "audio",
    ".opus": "audio",
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
