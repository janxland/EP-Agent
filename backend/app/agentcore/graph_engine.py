"""
AgentGraph — LangGraph 风格的动态图执行引擎 (v5.2)

核心思想：
  - 每个节点（SubAgent）执行后返回更新的 GraphState
  - GraphState 中的 next_node 字段由 LLM（SupervisorAgent）决定
  - 图引擎根据 next_node 调度下一个节点
  - 支持循环、回溯、条件边

v5.2 新增：
  - NODE_TIMEOUT_SECONDS：单步超时保护（节点挂死不再卡住整个图）
  - graph.progress SSE 事件：前端可显示整体进度百分比
  - graph.error SSE 事件：错误实时推送
  - 条件边（conditional_edge）支持：节点可注册路由函数替代 next_node 字段
  - GraphState.outputs dict：按 domain 分类存储各节点输出，不再互相覆盖
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

_logger = logging.getLogger("ep_agent.graph")

Publisher = Callable[[str, dict], Awaitable[None]]


async def noop_publish(evt_type: str, payload: dict, **kwargs):
    """P2 修复：公共空操作 publish，消除 replay_engine/agent_nodes 重复定义。"""
    pass

# 单步超时（秒）：节点执行超过此时间视为挂死，强制中断
NODE_TIMEOUT_SECONDS = 120


# ── GraphState：贯穿全图的共享状态 ──────────────────────────────────────────────
@dataclass
class GraphState:
    """
    图执行过程中的共享状态。
    每个节点读取并更新此状态，图引擎根据 next_node 决定下一跳。
    """
    # 用户输入
    session_id:   str = ""
    workspace_id: str = ""
    role_id:      str = ""
    message:      str = ""
    attachment_name: str = ""
    attachment_content: str = ""
    attachment_workspace_path: str = ""
    attachment_b64: str = ""

    # 执行状态
    current_node: str = "supervisor"
    next_node:    str | None = None       # None / "END" = 终止
    visited:      list[str] = field(default_factory=list)    # 节点访问序列（可观测）
    visit_counts: dict = field(default_factory=dict)         # 节点访问计数 O(1)
    has_score:    bool = False

    # 节点间传递的数据
    abc_notation: str = ""
    score_meta:   dict = field(default_factory=dict)
    tool_results: list[dict] = field(default_factory=list)

    # 各节点输出按 domain 分类存储（不再互相覆盖）
    # 格式：{"convert": {...}, "h5_create": {...}, "audio": {...}}
    outputs:      dict = field(default_factory=dict)

    # Reflection 状态
    reflection_score: float = 1.0
    reflection_notes: str = ""
    retry_count:      int = 0

    # 最终输出（由 supervisor 在决策 END 前汇总，或最后成功节点的输出）
    final_output: dict = field(default_factory=dict)
    error:        str = ""

    # 运行时回调（不序列化）
    publish:        Any = field(default=None, repr=False)
    session_getter: Any = field(default=None, repr=False)
    session_saver:  Any = field(default=None, repr=False)
    convert_fn:     Any = field(default=None, repr=False)
    edit_fn:        Any = field(default=None, repr=False)
    todo_mgr:       Any = field(default=None, repr=False)
    memory_context: str = ""   # 长期记忆上下文（由 _dispatch_v5 注入）


# ── 节点注册表 ─────────────────────────────────────────────────────────────────
_NODE_REGISTRY: dict[str, Callable] = {}
_EDGE_REGISTRY: dict[str, Callable] = {}   # 条件边：node_name → router_fn(state) → next_node


def node(name: str):
    """装饰器：注册节点函数到图引擎。"""
    def decorator(fn: Callable) -> Callable:
        _NODE_REGISTRY[name] = fn
        _logger.debug("[graph] 注册节点: %s → %s", name, fn.__qualname__)
        return fn
    return decorator


def conditional_edge(from_node: str):
    """
    装饰器：为节点注册条件路由函数。
    路由函数签名：(state: GraphState) -> str  （返回下一节点名）
    优先级高于 state.next_node。

    用法：
        @conditional_edge("convert_node")
        def route_after_convert(state: GraphState) -> str:
            if state.abc_notation and "h5" in state.message:
                return "h5_node"
            return "supervisor"
    """
    def decorator(fn: Callable) -> Callable:
        _EDGE_REGISTRY[from_node] = fn
        _logger.debug("[graph] 注册条件边: %s → %s", from_node, fn.__qualname__)
        return fn
    return decorator


def get_node(name: str) -> Callable | None:
    return _NODE_REGISTRY.get(name)


def list_nodes() -> list[str]:
    return list(_NODE_REGISTRY.keys())


# ── AgentGraph：图执行引擎 ─────────────────────────────────────────────────────
class AgentGraph:
    """
    动态图执行引擎（LangGraph 风格）。

    执行流程：
      1. 从 start_node 开始（默认 "supervisor"）
      2. 执行当前节点 → 更新 state（带超时保护）
      3. 检查条件边（_EDGE_REGISTRY）→ 优先于 state.next_node
      4. 若 next_node == END 或超过 max_steps → 停止
      5. 推送进度 SSE 事件（graph.progress）

    健壮性特性：
      - NODE_TIMEOUT_SECONDS：单步超时，节点挂死自动中断
      - visit_counts O(1) 计数替代 list.count()
      - 条件边支持：节点可注册路由函数实现确定性跳转
      - graph.progress / graph.error SSE 实时可观测
    """

    MAX_STEPS = 14
    MAX_NODE_VISITS = 3

    async def run(
        self,
        state: GraphState,
        start_node: str = "supervisor",
    ) -> GraphState:
        state.current_node = start_node
        steps = 0

        while steps < self.MAX_STEPS:
            steps += 1
            node_name = state.current_node

            # ── 防死循环：O(1) 计数检查 ──────────────────────────────────
            node_visit_count = state.visit_counts.get(node_name, 0)
            if node_visit_count >= self.MAX_NODE_VISITS:
                state.error = f"节点 {node_name} 循环超限（{node_visit_count}次），强制终止"
                _logger.warning("[graph] %s", state.error)
                await self._push_error(state, state.error)
                break

            state.visited.append(node_name)
            state.visit_counts[node_name] = node_visit_count + 1

            # ── 推送进度事件 ──────────────────────────────────────────────
            progress_pct = min(int(steps / self.MAX_STEPS * 100), 95)
            # GRAPH-1 修复： visited 只传最近 5 步，避免大图时 SSE 载荷随步数线性增长
            await self._publish(state, "graph.node_enter", {
                "node":     node_name,
                "step":     steps,
                "progress": progress_pct,
                "visited":  state.visited[-5:],
                "total_visited": len(state.visited),
            })

            _logger.info("[graph] step=%d/%d node=%s", steps, self.MAX_STEPS, node_name)

            # ── 获取节点函数 ──────────────────────────────────────────────
            node_fn = get_node(node_name)
            if node_fn is None:
                state.error = f"未知节点: {node_name}，可用节点: {list_nodes()}"
                _logger.error("[graph] %s", state.error)
                await self._push_error(state, state.error)
                break

            # ── 执行节点（带超时保护）────────────────────────────────────
            try:
                state = await asyncio.wait_for(
                    node_fn(state),
                    timeout=NODE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                state.error = f"节点 {node_name} 执行超时（>{NODE_TIMEOUT_SECONDS}s），强制跳过"
                _logger.error("[graph] %s", state.error)
                await self._push_error(state, state.error)
                # 超时不终止整个图，让 supervisor 决定下一步
                state.next_node = "supervisor"
            except Exception as exc:
                state.error = str(exc)
                _logger.exception("[graph] 节点 %s 执行异常", node_name)
                await self._push_error(state, state.error)
                break

            # ── 条件边优先（确定性路由，不走 LLM）────────────────────────
            edge_router = _EDGE_REGISTRY.get(node_name)
            if edge_router is not None:
                try:
                    routed = edge_router(state)
                    if routed:
                        state.next_node = routed
                        _logger.info("[graph] 条件边 %s → %s", node_name, routed)
                except Exception as exc:
                    _logger.warning("[graph] 条件边 %s 路由异常: %s", node_name, exc)

            # ── 推送节点退出事件 ──────────────────────────────────────────
            await self._publish(state, "graph.node_exit", {
                "node":      node_name,
                "next_node": state.next_node,
                "has_error": bool(state.error),
                "progress":  progress_pct,
            })

            # ── 检查终止条件 ──────────────────────────────────────────────
            if state.next_node is None or state.next_node == "END":
                await self._publish(state, "graph.progress", {
                    "progress": 100,
                    "status":   "completed",
                    "steps":    steps,
                })
                _logger.info("[graph] 图执行完成 steps=%d", steps)
                break

            state.current_node = state.next_node

        else:
            state.error = f"图执行超过最大步数 {self.MAX_STEPS}，强制终止"
            _logger.warning("[graph] %s", state.error)
            await self._push_error(state, state.error)

        return state

    # ── 内部工具方法 ──────────────────────────────────────────────────────────

    @staticmethod
    async def _publish(state: GraphState, evt: str, payload: dict) -> None:
        """安全推送 SSE 事件，异常完全隔离。"""
        if state.publish:
            try:
                await state.publish(evt, payload)
            except Exception:
                pass

    @staticmethod
    async def _push_error(state: GraphState, msg: str) -> None:
        """推送错误事件。"""
        if state.publish:
            try:
                await state.publish("graph.error", {
                    "message":      msg,
                    "current_node": state.current_node,
                    "visited":      state.visited,
                })
            except Exception:
                pass


# 全局图实例
agent_graph = AgentGraph()
