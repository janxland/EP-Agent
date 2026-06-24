"""
SessionContext — 消除 session_getter/session_saver 透传（Phase 3 预备）

当前状态（Phase 1）：
  提供 set_session_context / get_session / save_session 接口，
  但各 SubAgent 暂时保留 session_getter/saver 参数（向后兼容）。
  Phase 3 时统一迁移，彻底消除 26 处透传。

使用方式：
  # pipeline/router.py 请求入口（每次请求调用一次）
  from app.agentcore.session_context import set_session_context
  set_session_context(get_session_fn, save_session_fn)

  # SubAgent 内部（Phase 3 后直接调用，无需透传）
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
