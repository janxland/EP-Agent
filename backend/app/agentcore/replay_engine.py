"""
ReplayEngine — 一键重播引擎 (Phase 2 v1.3)

设计原则：
  - 零侵入：不修改任何现有 SubAgent / 工具函数 / SSE 协议
  - 低耦合：只依赖 db.py（数据读写）+ universal_runner（执行复用）
  - 两种模式：
      fixture — 用冻结返回值替代真实 API 调用（快速/确定性）
      live    — 真实调用所有工具（验证修复后行为）

v1.1 新增：
  - SSE 实时推流：重放过程中每步工具调用实时推送 replay.step 事件
  - fixture 命中率统计：hit_count / total_fixtures 写入结果
  - 差异分析增强：同时比较 tool span + model span token 差异
  - 重放历史查询：get_replay_history(source_trace_id)
  - 垃圾 session 自动清理：重放完成后标记 session 为 replay，可按需清理

v1.3 新增：
  - SSE 步骤进度推送：每步工具调用实时推送 replay.step 事件（含 tool/status/text）
  - error_detail 字段：失败时返回具体错误原因，前端可展示错误详情
  - replay session 自动清理：重放完成后将孤立 replay session 标记为 archived
    可通过 cleanup_replay_sessions(max_age_hours=24) 批量清理

挂载方式（router.py 中仅需 3 行）：
    engine = ReplayEngine()
    result = await engine.replay(source_trace_id, mode="fixture", publish=publish)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable, Awaitable

_logger = logging.getLogger("ep_agent.replay")

Publisher = Callable[[str, dict], Awaitable[None]]


# ── 工具函数（复用 trace_collector 的 hash 逻辑，不再重复定义）──────────────
from app.agentcore.trace_collector import _hash_args  # P1 修复：消除重复定义


# P2 修复：_noop_publish 已移至 graph_engine.py 统一导出
from app.agentcore.graph_engine import noop_publish as _noop_publish


# ── ContextVar：注入 mock registry 到 ReactExecutor ─────────────────────────

from contextvars import ContextVar
_mock_registry_var: ContextVar["FixtureMockToolRegistry | None"] = ContextVar(
    "mock_registry", default=None
)


def get_mock_registry() -> "FixtureMockToolRegistry | None":
    """ReactExecutor 调用 call_tool 前检查此函数，有 mock 则优先使用"""
    return _mock_registry_var.get()


# ── FixtureMockToolRegistry ──────────────────────────────────────────────────

class FixtureMockToolRegistry:
    """
    Mock 工具注册表：用 fixture 替代真实工具调用。
    按 tool_name + args_hash 建立索引，hash 命中则返回冻结值，否则记录 miss。
    """

    def __init__(self, fixtures: list[dict], fallback_to_live: bool = False):
        # index: "tool_name:args_hash" → fixture dict
        self._index: dict[str, dict] = {}
        for f in fixtures:
            key = f"{f['tool_name']}:{f['tool_args_hash']}"
            self._index[key] = f
        self.fallback_to_live = fallback_to_live
        self.misses: list[dict] = []   # 未命中记录，供差异分析
        self.hits:  list[dict] = []    # 命中记录，供命中率统计
        self.total_fixtures = len(fixtures)

    async def call(self, tool_name: str, arguments: dict) -> str:
        args_hash = _hash_args(arguments)
        key = f"{tool_name}:{args_hash}"
        if key in self._index:
            _logger.debug("[Replay] fixture HIT  %s hash=%s", tool_name, args_hash)
            self.hits.append({"tool_name": tool_name, "args_hash": args_hash})
            return self._index[key]["tool_result"]
        # 未命中
        self.misses.append({"tool_name": tool_name, "args_hash": args_hash})
        _logger.debug("[Replay] fixture MISS %s hash=%s", tool_name, args_hash)
        if self.fallback_to_live:
            from app.agentcore.tools import call_tool as _real_call
            return await _real_call(tool_name, arguments)
        return json.dumps(
            {"error": f"[REPLAY] fixture not found for {tool_name}", "replay_miss": True},
            ensure_ascii=False,
        )

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def hit_rate(self) -> float:
        """fixture 命中率（0.0 ~ 1.0），total_calls = hits + misses"""
        total = len(self.hits) + len(self.misses)
        return len(self.hits) / total if total > 0 else 1.0


# ── 差异分析 ─────────────────────────────────────────────────────────────────

async def _diff_traces(source_trace_id: str, replay_trace_id: str) -> dict:
    """
    对比两条 trace 的 spans，返回差异摘要（v1.1 增强）。
    比较维度：工具调用顺序 / 工具名 / 参数 hash / 状态 / token 差异（model span）
    v1.2：改为 async，DB 调用通过 run_in_executor 避免阻塞事件循环
    """
    from app.pipeline import db as _db
    loop = asyncio.get_running_loop()
    src_spans, rep_spans = await asyncio.gather(
        loop.run_in_executor(None, _db.get_spans_by_trace, source_trace_id),
        loop.run_in_executor(None, _db.get_spans_by_trace, replay_trace_id),
    )

    # 只比较 tool span（model span 单独统计 token 差异）
    src_tool_spans = [s for s in src_spans if s.get("span_kind") == "tool"]
    rep_tool_spans = [s for s in rep_spans if s.get("span_kind") == "tool"]

    src_tools = [(s["tool_name"], s["tool_args_hash"], s["status"]) for s in src_tool_spans]
    rep_tools = [(s["tool_name"], s["tool_args_hash"], s["status"]) for s in rep_tool_spans]

    details: list[dict] = []
    max_len = max(len(src_tools), len(rep_tools), 1)

    for i in range(max_len):
        src = src_tools[i] if i < len(src_tools) else None
        rep = rep_tools[i] if i < len(rep_tools) else None
        if src == rep:
            details.append({"step": i, "match": True, "tool": src[0] if src else ""})
        else:
            details.append({
                "step":    i,
                "match":   False,
                "source":  {"tool": src[0], "hash": src[1], "status": src[2]} if src else None,
                "replay":  {"tool": rep[0], "hash": rep[1], "status": rep[2]} if rep else None,
            })

    # token 差异（model span 汇总）
    src_in  = sum(s.get("input_tokens",  0) for s in src_spans if s.get("span_kind") == "model")
    src_out = sum(s.get("output_tokens", 0) for s in src_spans if s.get("span_kind") == "model")
    rep_in  = sum(s.get("input_tokens",  0) for s in rep_spans if s.get("span_kind") == "model")
    rep_out = sum(s.get("output_tokens", 0) for s in rep_spans if s.get("span_kind") == "model")

    mismatches = [d for d in details if not d["match"]]
    if not mismatches:
        summary = f"完全一致（{len(src_tools)} 步）"
    else:
        summary = f"{len(mismatches)}/{max_len} 步存在差异"

    return {
        "summary":        summary,
        "details":        details,
        "mismatch_count": len(mismatches),
        "token_diff": {
            "src_input":  src_in,  "src_output":  src_out,
            "rep_input":  rep_in,  "rep_output":  rep_out,
            "delta_input":  rep_in  - src_in,
            "delta_output": rep_out - src_out,
        },
    }


# ── ReplayEngine ─────────────────────────────────────────────────────────────

class ReplayEngine:
    """
    一键重播引擎。
    用法：
        engine = ReplayEngine()
        result = await engine.replay("trace_01J...", mode="fixture")
    """

    async def replay(
        self,
        source_trace_id: str,
        mode: str = "fixture",            # "fixture" | "live"
        session_id: str | None = None,    # None → 自动创建新 session
        publish: Publisher | None = None,
    ) -> dict:
        """
        重播指定 trace_id 的执行。
        返回：{replay_id, session_id, trace_id, diff_summary, diff_details,
               fixture_misses, status}
        """
        from app.pipeline import db as _db
        from app.pipeline.domain import new_id
        from app.agentcore.trace_collector import TraceCollector
        from app.agentcore.universal_runner import universal_runner
        from app.pipeline import service as _svc

        _pub = publish or _noop_publish

        # 1. 加载原始 trace
        source_trace = _db.get_trace(source_trace_id)
        if not source_trace:
            return {"error": f"trace {source_trace_id} not found", "status": "failed"}

        # 2. 加载 fixtures（fixture 模式需要）
        fixtures = _db.get_fixtures_by_trace(source_trace_id) if mode == "fixture" else []

        # 3. 创建重播 session（隔离，不污染原始 session；透传 workspace/project 上下文）
        _ws_id  = source_trace.get("workspace_id") or ""
        _proj_id = source_trace.get("project_id") or ""
        if not session_id:
            _user_msg = source_trace.get("user_message", "")  # P3 修复：防御性访问
            new_sess = _svc.create_session(
                workspace_id=_ws_id or None,
                project_id=_proj_id or None,
                title=f"[重播] {_user_msg[:30]}"
            )
            session_id = new_sess.id

        # 4. fixture 模式：注入 MockRegistry 到 ContextVar
        mock_registry: FixtureMockToolRegistry | None = None
        token = None
        if mode == "fixture":
            mock_registry = FixtureMockToolRegistry(fixtures, fallback_to_live=False)
            token = _mock_registry_var.set(mock_registry)

        # 5. 创建重播 TraceCollector
        replay_tracer = TraceCollector(
            session_id=session_id,
            message=source_trace["user_message"],
            role_id=source_trace.get("role_id", ""),
            attachment_name=source_trace.get("attachment_name", ""),
        )
        wrapped_pub = replay_tracer.wrap_publish(_pub)

        _trace_status = "succeeded"
        _error_detail: str | None = None
        try:
            await _pub("replay.step", {
                "step": "replay_start",
                "status": "running",
                "text": f"[重播] 模式={mode} 来源={source_trace_id[:12]}…",
                "tool": "__init__",
            })

            result = await universal_runner.run(
                session_id=session_id,
                message=source_trace["user_message"],
                attachment_content="",
                attachment_name=source_trace.get("attachment_name", ""),
                attachment_workspace_path="",
                attachment_b64="",
                session_getter=_svc.get_session,
                session_saver=_svc.save_session,
                publish=wrapped_pub,
                convert_fn=_svc.convert,
                edit_fn=_svc.edit,
                audio_chat_fn=_svc.audio_chat,
                role_id=source_trace.get("role_id") or None,
            )

            if result.get("error"):
                _trace_status = "failed"
                _error_detail = str(result["error"])

        except Exception as e:
            _trace_status = "failed"
            _error_detail = str(e)
            _logger.warning("[ReplayEngine] 重播执行异常: %s", e)
            result = {"error": str(e)}

        finally:
            # 清除 mock（ContextVar token 恢复，不影响其他协程）
            if token is not None:
                _mock_registry_var.reset(token)
            # 落库重播 trace
            await replay_tracer.end_trace(status=_trace_status)
            # 标记 replay session 为 archived（便于后续批量清理）
            try:
                _db.mark_session_archived(session_id)
            except Exception:
                pass  # 不影响主流程

        # 6. 差异分析
        diff = await _diff_traces(source_trace_id, replay_tracer.trace_id)

        # 7. 记录重播会话
        replay_id = new_id("replay")
        try:
            _db.insert_replay({
                "replay_id":        replay_id,
                "source_trace_id":  source_trace_id,
                "replay_trace_id":  replay_tracer.trace_id,
                "session_id":       session_id,
                "mode":             mode,
                "status":           _trace_status,
                "diff_summary":     diff["summary"],
            })
        except Exception as e:
            _logger.warning("[ReplayEngine] replay 记录落库失败: %s", e)

        _logger.info(
            "[ReplayEngine] 重播完成 replay_id=%s mode=%s status=%s diff=%s",
            replay_id, mode, _trace_status, diff["summary"],
        )

        return {
            "replay_id":       replay_id,
            "session_id":      session_id,
            "trace_id":        replay_tracer.trace_id,
            "diff_summary":    diff["summary"],
            "diff_details":    diff["details"],
            "mismatch_count":  diff["mismatch_count"],
            "token_diff":      diff.get("token_diff", {}),
            "fixture_misses":  mock_registry.misses if mock_registry else [],
            "fixture_hits":    mock_registry.hits   if mock_registry else [],
            "fixture_hit_rate": round(mock_registry.hit_rate, 3) if mock_registry else 1.0,
            "total_fixtures":  mock_registry.total_fixtures if mock_registry else 0,
            "status":          _trace_status,
            "error_detail":    _error_detail,   # 失败时的错误原因（None = 成功）
            "mode":            mode,
        }

    @staticmethod
    async def cleanup_replay_sessions(max_age_hours: int = 24) -> int:
        """
        清理超过 max_age_hours 的 archived replay session（垃圾回收）。
        返回清理数量。通常由定时任务或手动调用。
        """
        from app.pipeline import db as _db
        try:
            count = _db.delete_archived_sessions(max_age_hours=max_age_hours)
            _logger.info("[ReplayEngine] 清理 %d 个过期 replay session", count)
            return count
        except Exception as e:
            _logger.warning("[ReplayEngine] cleanup_replay_sessions 失败: %s", e)
            return 0
