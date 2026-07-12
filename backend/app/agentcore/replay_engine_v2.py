"""
ReplayEngine v2 — 基于 DB traces/spans 的审计回放引擎（v8 架构，唯一回放引擎）

架构说明（v8 架构，REPLAY-001 注释修正）：
  - 主流程使用独立 MemorySaver（每次请求独立实例，执行完即释放）。
  - 审计/回放完全不依赖 LangGraph Checkpointer，改为：
      TraceCollector 拦截 SSE 事件 → 写 traces/spans/fixtures DB（解耦）
      replay_snapshot/rerun/fork 从 DB 读取历史，重建执行链路
  - 合并了 replay_engine.py v1.3 的 fixture mock 能力（FixtureMockToolRegistry）
  - 不依赖 SSE 事件重放（更可靠，不受事件丢失影响）
  - 支持"从任意步骤 fork 重跑"（调试神器）
  - 差异分析：对比两次执行的 GraphState 快照序列（从 DB spans 重建）

三种回放模式：
  snapshot — 从 DB spans 重建历史快照，按时间轴推送 SSE（只读，零副作用）
  rerun    — 从 DB traces 读取原始输入，重新执行并对比差异（live 模式）
  fork     — 从历史快照某步 fork 重跑，支持覆盖 message

Fixture Mock 机制（合并自 v1.3）：
  - FixtureMockToolRegistry：按 tool_name + args_hash 冻结返回值
  - get_mock_registry()：ReactExecutor 调用工具前检查此函数
  - _mock_registry_var：ContextVar，隔离不同协程的 mock 注入

与主流程解耦保证：
  - TraceCollector.wrap_publish 零侵入挂载（先原始推送，再审计，异常完全隔离）
  - end_trace 在 finally 块中调用，不影响主流程成功/失败状态
  - replay session 使用独立 session_id，不污染原始 session
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from typing import Callable, Awaitable

_logger = logging.getLogger("ep_agent.replay_v2")

Publisher = Callable[[str, dict], Awaitable[None]]


# ── 空发布函数（公共导出）────────────────────────────────────────────────────────
async def _noop_publish(evt_type: str, payload: dict) -> None:
    """空发布函数，用于不需要 SSE 推送的场景。"""
    pass

# 公开别名（向后兼容）
noop_publish = _noop_publish


# ── ContextVar：注入 mock registry 到 ReactExecutor ──────────────────────────
_mock_registry_var: ContextVar["FixtureMockToolRegistry | None"] = ContextVar(
    "mock_registry", default=None
)


def get_mock_registry() -> "FixtureMockToolRegistry | None":
    """ReactExecutor 调用 call_tool 前检查此函数，有 mock 则优先使用。"""
    return _mock_registry_var.get()


# ── FixtureMockToolRegistry ───────────────────────────────────────────────────

class FixtureMockToolRegistry:
    """
    Mock 工具注册表：用 fixture 替代真实工具调用。
    按 tool_name + args_hash 建立索引，hash 命中则返回冻结值，否则记录 miss。
    """

    def __init__(self, fixtures: list[dict], fallback_to_live: bool = False):
        self._index: dict[str, dict] = {}
        for f in fixtures:
            key = f"{f['tool_name']}:{f['tool_args_hash']}"
            self._index[key] = f
        self.fallback_to_live = fallback_to_live
        self.misses: list[dict] = []
        self.hits:   list[dict] = []
        self.total_fixtures = len(fixtures)

    async def call(self, tool_name: str, arguments: dict) -> str:
        from app.agentcore.trace_collector import _hash_args
        args_hash = _hash_args(arguments)
        key = f"{tool_name}:{args_hash}"
        if key in self._index:
            _logger.debug("[Replay] fixture HIT  %s hash=%s", tool_name, args_hash)
            self.hits.append({"tool_name": tool_name, "args_hash": args_hash})
            return self._index[key]["tool_result"]
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
        total = len(self.hits) + len(self.misses)
        return len(self.hits) / total if total > 0 else 1.0


# ── 快照差异分析 ──────────────────────────────────────────────────────────────

def _diff_snapshots(src_snaps: list[dict], rep_snaps: list[dict]) -> dict:
    """
    对比两次执行的 GraphState 快照序列，返回差异摘要。

    比较维度：
      - 节点访问序列（visited）
      - 最终 abc_notation 长度
      - 最终 error 状态
      - reflection_score 变化
    """
    src_visited = src_snaps[-1]["state"].get("visited", []) if src_snaps else []
    rep_visited = rep_snaps[-1]["state"].get("visited", []) if rep_snaps else []

    src_abc_len = len(src_snaps[-1]["state"].get("abc_notation", "")) if src_snaps else 0
    rep_abc_len = len(rep_snaps[-1]["state"].get("abc_notation", "")) if rep_snaps else 0

    src_error = src_snaps[-1]["state"].get("error", "") if src_snaps else ""
    rep_error = rep_snaps[-1]["state"].get("error", "") if rep_snaps else ""

    src_score = src_snaps[-1]["state"].get("reflection_score", 1.0) if src_snaps else 1.0
    rep_score = rep_snaps[-1]["state"].get("reflection_score", 1.0) if rep_snaps else 1.0

    details: list[dict] = []
    max_len = max(len(src_snaps), len(rep_snaps), 1)
    for i in range(max_len):
        s = src_snaps[i] if i < len(src_snaps) else None
        r = rep_snaps[i] if i < len(rep_snaps) else None
        s_node = s["node"] if s else None
        r_node = r["node"] if r else None
        details.append({
            "step":  i,
            "match": s_node == r_node,
            "src_node": s_node,
            "rep_node": r_node,
        })

    mismatches = [d for d in details if not d["match"]]
    node_seq_match = src_visited == rep_visited

    if not mismatches and node_seq_match:
        summary = f"完全一致（{len(src_snaps)} 步，节点序列: {' → '.join(src_visited)}）"
    else:
        summary = (
            f"{len(mismatches)}/{max_len} 步节点不同；"
            f"ABC长度 {src_abc_len}→{rep_abc_len}；"
            f"score {src_score:.2f}→{rep_score:.2f}"
        )

    return {
        "summary":        summary,
        "details":        details,
        "mismatch_count": len(mismatches),
        "src_visited":    src_visited,
        "rep_visited":    rep_visited,
        "abc_diff":       rep_abc_len - src_abc_len,
        "score_diff":     round(rep_score - src_score, 3),
        "src_error":      src_error,
        "rep_error":      rep_error,
    }


# ── ReplayEngineV2 ────────────────────────────────────────────────────────────

class ReplayEngineV2:
    """
    基于 LangGraph Checkpointer 的审计回放引擎。

    用法：
        engine = ReplayEngineV2()

        # 模式1：只读快照回放（零副作用）
        result = await engine.replay_snapshot(session_id, publish=publish)

        # 模式2：重新执行并对比差异
        result = await engine.replay_rerun(session_id, publish=publish)

        # 模式3：从某步 fork 重跑
        result = await engine.replay_fork(session_id, step_index=2, new_message="...")
    """

    # ── 模式1：快照回放（只读）────────────────────────────────────────────────

    async def replay_snapshot(
        self,
        session_id: str,
        publish: Publisher | None = None,
    ) -> dict:
        """
        只读回放：从 Checkpointer 读取历史快照，按时间轴推送 SSE 事件。
        不重新执行任何 Agent，零副作用，适合审计查看。
        """
        from app.agentcore.graph_engine_v2 import get_session_history

        _pub = publish or _noop_publish

        await _pub("replay.step", {
            "step": "snapshot_start", "status": "running",
            "text": f"[快照回放] session={session_id[:12]}…",
            "tool": "__init__",
        })

        snapshots = await get_session_history(session_id)
        if not snapshots:
            return {
                "status":  "failed",
                "error":   f"session {session_id} 无 Checkpointer 快照（可能使用旧版引擎）",
                "mode":    "snapshot",
            }

        # 按快照序列推送 SSE 事件（模拟原始执行过程）
        for snap in snapshots:
            node_name = snap.get("node", "unknown")
            step_i    = snap.get("step", 0)
            state     = snap.get("state", {})

            await _pub("graph.node_enter", {
                "node":     node_name,
                "step":     step_i,
                "progress": min(int(step_i / max(len(snapshots), 1) * 100), 95),
                "visited":  state.get("visited", [])[-5:],
                "replay":   True,
            })

            # 推送该步的工具调用结果（从 tool_results 中提取）
            tool_results = state.get("tool_results", [])
            if tool_results:
                last_tool = tool_results[-1]
                await _pub("replay.step", {
                    "step":   f"node_{node_name}",
                    "status": "succeeded" if not last_tool.get("error") else "failed",
                    "text":   last_tool.get("summary", ""),
                    "tool":   last_tool.get("domain", node_name),
                })

            await _pub("graph.node_exit", {
                "node":      node_name,
                "next_node": state.get("next_node", ""),
                "has_error": bool(state.get("error")),
                "replay":    True,
            })

            # 如有 ABC 谱，推送更新事件（统一使用 abc.updated，与主流程保持一致）
            abc = state.get("abc_notation", "")
            if abc:
                await _pub("abc.updated", {
                    "abc":     abc,
                    "version": 1,
                    "summary": f"[回放] {node_name}",
                    "source":  f"replay:{node_name}",
                    "_replay": True,
                })

        final_state = snapshots[-1]["state"] if snapshots else {}
        await _pub("graph.progress", {"progress": 100, "status": "completed", "replay": True})

        _logger.info("[ReplayV2] 快照回放完成 session=%s steps=%d", session_id[:8], len(snapshots))

        return {
            "status":       "succeeded",
            "mode":         "snapshot",
            "session_id":   session_id,
            "steps":        len(snapshots),
            "visited":      final_state.get("visited", []),
            "abc_notation": final_state.get("abc_notation", ""),
            "error":        final_state.get("error", ""),
            "snapshots":    snapshots,
        }

    # ── 模式2：重新执行并对比 ──────────────────────────────────────────────────

    async def replay_rerun(
        self,
        session_id: str,
        publish: Publisher | None = None,
        new_session_id: str | None = None,
    ) -> dict:
        """
        重新执行原始请求，对比新旧执行差异（v8 架构：从 DB traces 读取原始输入）。
        适合验证修复后行为是否符合预期。
        BUG-018 修复：不再依赖 Checkpointer 快照，改从 DB traces/spans 读取原始输入。
        """
        from app.pipeline import db as _db
        from app.pipeline.domain import new_id
        from app.agentcore.trace_collector import TraceCollector
        from app.agentcore.universal_runner import universal_runner
        from app.pipeline import service as _svc

        _pub = publish or _noop_publish

        # 1. 从 DB traces 读取原始输入（BUG-018 修复：不依赖 Checkpointer）
        traces = _db.get_traces_by_session(session_id, limit=1, offset=0)
        if not traces:
            _logger.warning("[ReplayV2] session=%s 无 trace 记录，无法 rerun", session_id)
            return {
                "status": "failed",
                "mode": "rerun",
                "error": f"session {session_id} 无审计记录，请先执行一次对话再重播",
            }
        orig_trace = traces[0]
        orig_message    = orig_trace.get("user_message", "")
        orig_attachment = orig_trace.get("attachment_name", "")
        orig_role_id    = orig_trace.get("role_id", "")

        # 获取原始快照用于对比（可能为空，不影响重跑）
        from app.agentcore.graph_engine_v2 import get_session_history
        src_snaps = await get_session_history(session_id)

        # 2. 创建新的重播 session
        # BUG-025 修复：init_state 未定义，改为从 orig_trace 读取 workspace_id/project_id
        # service.create_session() 内部自动生成 Session.id，不接受外部传入 session_id。
        orig_workspace_id = orig_trace.get("workspace_id") or ""
        orig_project_id   = orig_trace.get("project_id")   or ""
        try:
            _new_sess = _svc.create_session(
                workspace_id=orig_workspace_id or None,
                project_id=orig_project_id or None,
                title=f"[重播v2] {orig_message[:30]}",
            )
            replay_session_id = _new_sess.id  # 使用系统分配的 ID
        except Exception:
            replay_session_id = new_session_id or new_id("sess")

        await _pub("replay.step", {
            "step": "rerun_start", "status": "running",
            "text": f"[重跑] session={session_id[:12]}… → 新session={replay_session_id[:12]}",
            "tool": "__init__",
        })

        # 4. 重新执行
        tracer = TraceCollector(
            session_id=replay_session_id,
            message=orig_message,
            role_id=orig_role_id,
            attachment_name=orig_attachment,
        )
        wrapped_pub = tracer.wrap_publish(_pub)

        _status = "succeeded"
        _error_detail: str | None = None
        try:
            await universal_runner.run(
                session_id=replay_session_id,
                message=orig_message,
                attachment_content="",
                attachment_name=orig_attachment,
                attachment_workspace_path="",
                attachment_b64="",
                session_getter=_svc.get_session,
                session_saver=_svc.save_session,
                publish=wrapped_pub,
                convert_fn=_svc.convert,
                edit_fn=_svc.edit,
                audio_chat_fn=_svc.audio_chat,
                role_id=orig_role_id or None,
            )
        except Exception as e:
            _status = "failed"
            _error_detail = str(e)
            _logger.warning("[ReplayV2] 重跑执行异常: %s", e)
        finally:
            await tracer.end_trace(status=_status)
            try:
                _db.mark_session_archived(replay_session_id)
            except Exception:
                pass

        # 5. 读取新快照并对比
        rep_snaps = await get_session_history(replay_session_id)
        diff = _diff_snapshots(src_snaps, rep_snaps)

        _logger.info(
            "[ReplayV2] 重跑完成 status=%s diff=%s",
            _status, diff["summary"],
        )

        return {
            "status":          _status,
            "mode":            "rerun",
            "src_session_id":  session_id,
            "rep_session_id":  replay_session_id,
            "diff_summary":    diff["summary"],
            "diff_details":    diff["details"],
            "mismatch_count":  diff["mismatch_count"],
            "src_visited":     diff["src_visited"],
            "rep_visited":     diff["rep_visited"],
            "error_detail":    _error_detail,
        }

    # ── 模式3：从某步 fork 重跑 ───────────────────────────────────────────────

    async def replay_fork(
        self,
        session_id: str,
        step_index: int,
        new_message: str | None = None,
        publish: Publisher | None = None,
    ) -> dict:
        """
        从历史快照的某一步 fork，用于调试特定节点。
        支持覆盖 message（模拟不同输入）。
        """
        # BUG-026 修复：get_compiled_graph 已在 v8 删除，不再导入
        from app.agentcore.graph_engine_v2 import (
            fork_from_checkpoint, stream_graph_events
        )
        from app.pipeline.domain import new_id

        _pub = publish or _noop_publish

        fork_state = await fork_from_checkpoint(session_id, step_index, new_message)
        if fork_state is None:
            return {
                "status": "failed",
                "error":  f"无法从 step={step_index} fork（快照不存在或 langgraph 未安装）",
                "mode":   "fork",
            }

        fork_session_id = new_id("sess")
        fork_state["session_id"] = fork_session_id  # type: ignore
        # BUG-023 修复：不在 fork_state 中设置 publish，
        # stream_graph_events 的第二参数 _pub 已统一处理，避免双重推送

        await _pub("replay.step", {
            "step": "fork_start", "status": "running",
            "text": f"[Fork] 从 step={step_index} 重跑，新session={fork_session_id[:12]}",
            "tool": "__init__",
        })

        try:
            final_state = await stream_graph_events(fork_state, _pub, fork_session_id)  # type: ignore
            _logger.info("[ReplayV2] fork 完成 step=%d session=%s", step_index, fork_session_id[:8])
            return {
                "status":         "succeeded",
                "mode":           "fork",
                "fork_session_id": fork_session_id,
                "from_step":      step_index,
                "new_message":    new_message,
                "visited":        final_state.get("visited", []),
                "abc_notation":   final_state.get("abc_notation", ""),
                "error":          final_state.get("error", ""),
            }
        except Exception as e:
            _logger.warning("[ReplayV2] fork 执行异常: %s", e)
            return {
                "status":   "failed",
                "mode":     "fork",
                "error":    str(e),
            }

    @staticmethod
    async def cleanup_replay_sessions(max_age_hours: int = 24) -> int:
        """清理超过 max_age_hours 的 archived replay session（垃圾回收）。"""
        from app.pipeline import db as _db
        try:
            count = _db.delete_archived_sessions(max_age_hours=max_age_hours)
            _logger.info("[ReplayV2] 清理 %d 个过期 replay session", count)
            return count
        except Exception as e:
            _logger.warning("[ReplayV2] cleanup_replay_sessions 失败: %s", e)
            return 0
