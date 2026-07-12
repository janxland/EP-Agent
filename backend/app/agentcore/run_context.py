"""
RunContext — 单次请求的统一上下文对象（v4.0 解耦架构）

设计原则：
  - 一次构建，全链路传递（Runner → Agent → Executor）
  - 构造时自动注入 ContextVar，消灭手动注入的时机依赖
  - 不可变字段用 dataclass，可变状态通过 with_domain() 返回新实例
  - 替代 _dispatch() 的 13 个参数爆炸问题

使用方式：
    ctx = RunContext.from_request(
        session_id=session_id,
        message=message,
        publish=publish,
        ...
    )
    # ContextVar 在构造时自动注入，无需手动调用 set_current_session_id()
"""
from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field, replace
from typing import Callable, Awaitable, Any

Publisher = Callable[[str, dict], Awaitable[None]]

_logger = logging.getLogger("ep_agent.run_context")


@dataclass
class RunContext:
    """
    单次请求的全局上下文，贯穿 Runner → Agent → Executor。

    字段分组：
      - 身份信息：session_id / workspace_id / project_id / trace_id / role_id
      - 请求内容：message / attachment_*
      - 运行时状态：domain / has_score
      - 基础设施：publish
      - 扩展字段：extra（Agent 间传递中间结果）
    """

    # ── 身份信息（构造时确定，不可变）──────────────────────────────
    session_id:   str = ""
    workspace_id: str = ""
    project_id:   str = ""
    trace_id:     str = ""
    role_id:      str = ""

    # ── 请求内容（构造时确定，不可变）──────────────────────────────
    message:                   str = ""
    attachment_content:        str = ""
    attachment_name:           str = ""
    attachment_workspace_path: str = ""
    attachment_b64:            str = ""

    # ── 运行时状态（可通过 with_* 方法更新）────────────────────────
    domain:    str  = ""
    has_score: bool = False

    # ── 基础设施（注入）────────────────────────────────────────────
    publish: Publisher | None = None

    # ── 扩展字段（Agent 间传递结果，如链式意图的中间 ABC）──────────
    extra: dict = field(default_factory=dict)

    # ── 内部标记：标识此实例是否为 replace() 产生的副本，副本跳过 ContextVar 注入 ──
    _is_copy: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self):
        """构造完成后自动注入 ContextVar，生命周期与请求绑定。
        BUG-040 修复：with_domain/with_extra 等方法通过 dataclasses.replace() 产生副本，
        每次 replace() 都会触发 __post_init__，导致 ContextVar 重复注入和日志刷屏。
        修复：_is_copy=True 的副本跳过注入，只有首次构造（_is_copy=False）才注入。
        """
        # 自动生成 trace_id（若未传入）
        if not self.trace_id:
            object.__setattr__(self, "trace_id", uuid.uuid4().hex)

        # 副本（replace() 产生）跳过 ContextVar 注入，避免重复日志和无谓开销
        if self._is_copy:
            return

        # 自动注入 ContextVar（消灭手动注入的时机依赖）
        if self.session_id:
            try:
                from app.agentcore.session_context import (
                    set_current_session_id,
                    set_current_trace_id,
                )
                set_current_session_id(self.session_id)
                set_current_trace_id(self.trace_id)
                _logger.info(
                    "[trace=%s] RunContext 构造 session=%s msg=%s",
                    self.trace_id[:8],
                    self.session_id[:8],
                    self.message[:50].replace("\n", " "),
                )
            except Exception as e:
                _logger.warning("ContextVar 注入失败: %s", e)

        # v4.0 fix46：workspace_id/project_id 已通过 ContextVar 注入，
        # 工具调用时会自动从 DB 查询，RunContext.extra 无需额外缓存。

    # ── 不可变更新方法（返回新实例，保持原实例不变）────────────────

    def with_domain(self, domain: str) -> "RunContext":
        """返回 domain 已更新的新 RunContext（不可变语义）。
        BUG-040: _is_copy=True 标记副本，__post_init__ 跳过 ContextVar 重复注入。"""
        return replace(self, domain=domain, _is_copy=True)

    def with_has_score(self, has_score: bool) -> "RunContext":
        """返回 has_score 已更新的新 RunContext。"""
        return replace(self, has_score=has_score, _is_copy=True)

    def with_attachment_path(self, path: str) -> "RunContext":
        """返回 attachment_workspace_path 已更新的新 RunContext。"""
        return replace(self, attachment_workspace_path=path, _is_copy=True)

    def with_extra(self, **kwargs) -> "RunContext":
        """返回 extra 字段合并更新的新 RunContext。"""
        new_extra = {**self.extra, **kwargs}
        return replace(self, extra=new_extra, _is_copy=True)

    # ── AGENT-2 修复：便捷属性，消除各 Agent run_with_ctx 中重复的解包样板 ──────

    @property
    def session_getter(self):
        """从 extra 取 session_getter，未注入时 fallback 到内存 store → DB 重建 Session。

        BUG-036 修复：原 fallback 直接返回 _db.get_session_info（返回 dict），
        导致 edit_agent.py 等访问 sess.score 时 AttributeError。
        修复策略：
          1. 优先返回 extra 中注入的 getter（正常路径，返回 Session 对象）
          2. fallback 构造一个安全 getter：先查内存 store，再查 DB 并将 dict 重建为 Session
        """
        getter = self.extra.get("session_getter")
        if getter is not None:
            return getter
        # BUG-036 修复：fallback getter 将 DB dict 重建为 Session 对象
        def _safe_session_getter(session_id: str):
            # 优先从内存 store 获取（内存版 getter 返回 Session 对象）
            try:
                from app.pipeline.service import get_session as _mem_get
                return _mem_get(session_id)
            except KeyError:
                pass
            except Exception:
                pass
            # 内存不存在时，从 DB 查询并重建 Session 对象
            try:
                from app.pipeline import db as _db
                from app.pipeline.domain import Session, Score, ScoreMeta
                row = _db.get_session_info(session_id)
                if not row:
                    return None
                sess = Session(
                    id=row.get("id", session_id),
                    workspace_id=row.get("workspace_id", "") or "",
                    project_id=row.get("project_id", "") or "",
                    pipeline_state=row.get("pipeline_state", "idle") or "idle",
                    extra=row.get("extra", {}) if isinstance(row.get("extra"), dict) else {},
                )
                # 从 DB 列重建 Score（若有 abc_notation）
                abc = row.get("abc_notation") or ""
                if abc:
                    sess.score = Score(
                        title=row.get("score_title", "") or "",
                        abc_notation=abc,
                        meta=ScoreMeta(
                            title=row.get("score_title", "") or "",
                            key=row.get("score_key", "C") or "C",
                            bpm=float(row.get("score_bpm") or 120),
                            note_count=int(row.get("score_notes") or 0),
                        ),
                    )
                return sess
            except Exception as _e:
                _logger.warning("[RunContext.session_getter] DB fallback 失败: %s", _e)
                return None
        return _safe_session_getter

    @property
    def session_saver(self):
        """从 extra 取 session_saver，未注入时 fallback 到 db.upsert_session。"""
        saver = self.extra.get("session_saver")
        if saver is not None:
            return saver
        from app.pipeline import db as _db
        return _db.upsert_session

    # ── 工厂方法 ────────────────────────────────────────────────────

    @classmethod
    def from_request(
        cls,
        session_id:                str,
        message:                   str,
        publish:                   Publisher,
        workspace_id:              str = "",
        project_id:                str = "",
        role_id:                   str = "",
        attachment_content:        str = "",
        attachment_name:           str = "",
        attachment_workspace_path: str = "",
        attachment_b64:            str = "",
        has_score:                 bool = False,
        trace_id:                  str = "",
    ) -> "RunContext":
        """
        标准工厂方法，从 HTTP 请求参数构造 RunContext。
        自动填充 trace_id（若未传）并注入 ContextVar。
        """
        return cls(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            trace_id=trace_id or uuid.uuid4().hex,
            role_id=role_id,
            message=message,
            attachment_content=attachment_content,
            attachment_name=attachment_name,
            attachment_workspace_path=attachment_workspace_path,
            attachment_b64=attachment_b64,
            has_score=has_score,
            publish=publish,
        )
