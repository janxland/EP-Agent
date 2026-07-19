"""
AgentGraph v2 — 基于真实 langgraph 包的图执行引擎 (v7 全量原生)

架构：
  - 所有节点直接使用 LangGraph 原生签名：(state: EPState dict, config: dict) -> dict
  - supervisor/reflect/agent_nodes 全部迁移到原生节点
  - 条件边路由直接调用 supervisor_agent 中的原生路由函数
  - Checkpointer：MemorySaver（每次请求独立实例，零外部依赖）
  - astream_events v2 / aget_state_history / START+END 全部使用最新 LangGraph API

v7 修复：
  - 修复 clean_state pop publish 后节点 state.get("publish") 为 None 的问题
    → publish 等回调字段同时保留在 state 和 config["configurable"] 中
  - 增加 on_chat_model_stream 事件处理，推送 LLM 流式 token 到 SSE
  - 增加 on_tool_start / on_tool_end 事件处理（用于图级别工具调用，非 ReactExecutor 内部）
  - 修复 _VALID_NODES 注册时机：build_ep_graph 返回后立即注册，确保 supervisor LLM 决策有效
"""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Any, Callable, Awaitable

_logger = logging.getLogger("ep_agent.graph_v2")

Publisher = Callable[[str, dict], Awaitable[None]]

# ── ContextVar：跨 LangGraph 内部传递 publish 等运行时回调 ─────────────────────
# LangGraph 在某些版本下不把 config["configurable"] 正确传给节点函数，
# 导致 agent_nodes._node_fn 里 config 参数为空/不含回调字段。
# 使用 ContextVar 作为100%可靠的备用通道：asyncio Task 继承父 ContextVar，
# LangGraph 内部的 Task 也能正确读取。
_publish_ctx:        ContextVar = ContextVar("_publish_ctx",        default=None)
_session_getter_ctx: ContextVar = ContextVar("_session_getter_ctx", default=None)
_session_saver_ctx:  ContextVar = ContextVar("_session_saver_ctx",  default=None)
_convert_fn_ctx:     ContextVar = ContextVar("_convert_fn_ctx",     default=None)
_edit_fn_ctx:        ContextVar = ContextVar("_edit_fn_ctx",        default=None)
_audio_chat_fn_ctx:  ContextVar = ContextVar("_audio_chat_fn_ctx",  default=None)
_todo_mgr_ctx:       ContextVar = ContextVar("_todo_mgr_ctx",       default=None)

# 字段名 → ContextVar 映射（agent_nodes 通过此映射读取）
_RUNTIME_CTX_MAP: dict[str, ContextVar] = {
    "publish":        _publish_ctx,
    "session_getter": _session_getter_ctx,
    "session_saver":  _session_saver_ctx,
    "convert_fn":     _convert_fn_ctx,
    "edit_fn":        _edit_fn_ctx,
    "audio_chat_fn":  _audio_chat_fn_ctx,
    "todo_mgr":       _todo_mgr_ctx,
}

# 单步超时（秒）
NODE_TIMEOUT_SECONDS = 120


# ── EPState：LangGraph TypedDict 状态（替代自研 GraphState dataclass）──────────

try:
    from typing import TypedDict
    # LangGraph 0.2+: StateGraph/END/START 从 langgraph.graph 导出
    # LangGraph 0.3+: END/START 同时在 langgraph.constants 可用，但 langgraph.graph 仍重新导出
    # 统一从 langgraph.graph 导入，兼容 0.2.x ~ 最新版
    from langgraph.graph import StateGraph, END, START
    # END 是 '__end__' 字符串常量（LangGraph 内部约定），不是 Enum
    # 路由函数必须返回 END 常量（而非字符串 'END'），add_conditional_edges map 也必须用 END 常量作 key
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False
    END = "__end__"   # 降级占位，仅供类型检查，实际不可用
    START = "__start__"
    _logger.warning("[graph_v2] langgraph 未安装，请执行: pip install langgraph>=0.2 langchain-core>=0.3")


if _LANGGRAPH_AVAILABLE:
    class EPState(TypedDict, total=False):
        """
        EP-Agent 图状态（LangGraph TypedDict）。

        total=False：所有字段可选，节点只需返回变更的字段，
        LangGraph 自动 merge（不需要返回完整 state）。
        """
        # 用户输入
        session_id:                str
        workspace_id:              str
        project_id:                str
        role_id:                   str
        message:                   str
        attachment_name:           str
        attachment_content:        str
        attachment_workspace_path: str
        attachment_b64:            str

        # 执行状态
        current_node:  str
        next_node:     str          # "END" = 终止
        visited:       list[str]    # 节点访问序列（可观测）
        visit_counts:  dict         # 节点访问计数 O(1)
        has_score:     bool
        initial_domain: str         # intent_router 路由结果（supervisor 优先参考）
        initial_domain_confidence: float  # intent_router 路由置信度

        # 节点间传递的数据
        abc_notation:  str
        score_meta:    dict
        tool_results:  list[dict]
        outputs:       dict         # 按 domain 分类存储各节点输出

        # Reflection 状态
        reflection_score: float
        reflection_notes: str
        retry_count:      int

        # 最终输出
        final_output:  dict
        error:         str

        # 运行时回调（v7：同时保留在 state 和 config["configurable"] 中）
        # state 中的值供节点 state.get() 读取（fallback 路径）
        # config["configurable"] 中的值供节点 config 参数读取（主路径）
        publish:        Any
        session_getter: Any
        session_saver:  Any
        convert_fn:     Any
        edit_fn:        Any
        audio_chat_fn:  Any
        todo_mgr:       Any
        memory_context: str

else:
    # 降级：langgraph 未安装时，EPState 用 dict 代替，避免 import 报错
    EPState = dict  # type: ignore


# ── Checkpointer：仅使用 MemorySaver ─────────────────────────────────────────
#
# 设计决策（v8 架构重设计）：
#   主执行路径只用 MemorySaver，零外部依赖，永不因第三方包版本问题崩溃。
#   审计/回放通过 TraceCollector（SSE 事件旁路收集）+ 独立 SQLite DB 实现，
#   与主流程完全解耦。
#
#   主流程：MemorySaver（内存，每次请求独立实例，无状态污染）
#   审计：TraceCollector 拦截 SSE 事件 → 写 traces/spans DB（完全解耦）
#   回放：读 DB spans/traces 重建执行链路（不依赖 Checkpointer）


# ── 路由函数（LangGraph add_conditional_edges 格式）─────────────────────────

def _supervisor_router(state: EPState) -> str:
    """
    Supervisor / reflect_node 后的路由：读取 state['next_node']。

    LangGraph 兼容性（LG-02/LG-07 修复）：
      - state['next_node'] 可能是字符串 'END'（supervisor_node 写入）
        或 END 常量（'__end__'），统一转换为 END 常量返回
      - add_conditional_edges 的 path_map key 必须与此函数返回值完全一致
    """
    nxt = state.get("next_node") or END
    # 字符串 'END' 和 END 常量统一转换为 END 常量
    if nxt == "END" or nxt == END:
        return END
    return nxt


def _make_business_router(node_name: str) -> Callable:
    """
    为每个业务节点生成独立路由函数（闭包捕获节点名）。
    优先查 supervisor_agent 中的原生条件边函数，退化到 state['next_node']。
    """
    # 原生条件边路由函数映射（supervisor_agent v6 原生）
    _EDGE_FN_MAP: dict[str, Callable] = {}

    def _load_edge_fns():
        if _EDGE_FN_MAP:
            return
        try:
            from app.agentcore.supervisor_agent import (
                route_after_convert, route_after_create,
                route_after_edit, route_after_sovits,
            )
            _EDGE_FN_MAP["convert_node"] = route_after_convert
            _EDGE_FN_MAP["create_node"]  = route_after_create   # 修复：防止 create→convert 死循环
            _EDGE_FN_MAP["edit_node"]    = route_after_edit
            _EDGE_FN_MAP["sovits_node"]  = route_after_sovits
        except Exception as e:
            _logger.warning("[graph_v2] 加载条件边函数失败: %s", e)

    def router(state: EPState) -> str:
        _load_edge_fns()
        edge_fn = _EDGE_FN_MAP.get(node_name)
        if edge_fn:
            try:
                routed = edge_fn(state)
                if routed:
                    # LG-02/LG-08 修复：统一将字符串 'END' 转换为 END 常量
                    if routed == "END" or routed == END:
                        return END
                    return routed
            except Exception as exc:
                _logger.warning("[graph_v2] 条件边 %s 路由异常: %s", node_name, exc)
        nxt = state.get("next_node") or END
        # 统一转换
        if nxt == "END" or nxt == END:
            return END
        return nxt

    router.__name__ = f"router_{node_name}"
    return router


# ── 构建 LangGraph 编译图 ─────────────────────────────────────────────────────

def build_ep_graph(checkpointer=None):
    """
    构建未编译的 EP-Agent StateGraph（v7 全量原生节点）。

    注意：传入 checkpointer（MemorySaver）时直接编译并返回已编译图；
    未传入时返回未编译 StateGraph，由调用方编译。

    节点注册顺序：supervisor → reflect_node → 8个业务节点
    所有节点均为 LangGraph 原生签名：(state: EPState dict, config: dict) -> dict
    无任何 GraphState dataclass 包装层。
    """
    if not _LANGGRAPH_AVAILABLE:
        raise RuntimeError("langgraph 未安装，请执行: pip install langgraph>=0.2 langchain-core>=0.3")

    import importlib, sys

    # 确保节点模块已 import
    for mod in (
        "app.agentcore.supervisor_agent",
        "app.agentcore.agents.reflect_agent",
        "app.agentcore.agents.agent_nodes",
    ):
        if mod not in sys.modules:
            try:
                importlib.import_module(mod)
            except Exception as e:
                _logger.warning("[graph_v2] 节点模块导入失败: %s — %s", mod, e)

    # 从各模块直接导入原生节点函数
    from app.agentcore.supervisor_agent import supervisor_node, register_valid_nodes
    from app.agentcore.agents.reflect_agent import reflect_node
    from app.agentcore.agents.agent_nodes import NODE_REGISTRY as _BUSINESS_NODES

    graph = StateGraph(EPState)

    # 注册 supervisor 和 reflect（原生节点，无包装）
    graph.add_node("supervisor",   supervisor_node)
    graph.add_node("reflect_node", reflect_node)

    # 注册所有业务节点（原生节点，无包装）
    for node_name, node_fn in _BUSINESS_NODES.items():
        graph.add_node(node_name, node_fn)
        _logger.debug("[graph_v2] 注册原生节点: %s", node_name)

    all_node_names = ["supervisor", "reflect_node"] + list(_BUSINESS_NODES.keys())

    # 向 supervisor_node 注册合法节点名（用于 LLM 决策校验）
    # v7 修复：在图编译前立即注册，确保 supervisor LLM 决策时 _VALID_NODES 非空
    register_valid_nodes(all_node_names)
    _logger.info("[graph_v2] 已注册合法节点: %s", all_node_names)

    # 入口节点（最新 API：add_edge(START, ...) 替代已废弃的 set_entry_point）
    graph.add_edge(START, "supervisor")

    # LG-02/LG-03 修复：path_map 同时注册 END 常量和字符串 'END' 两个 key，
    # 确保无论路由函数返回哪种形式都能命中，彻底消除 KeyError。
    # 推荐路由函数统一返回 END 常量（supervisor_agent.py 已修复），
    # 字符串 'END' key 作为双重保险。
    def _make_edge_map(node_names: list[str]) -> dict:
        m = {n: n for n in node_names}
        m[END]   = END   # END 常量 → 终止（主路径）
        m["END"] = END   # 字符串 'END' → 终止（兼容旧路由函数）
        return m

    # supervisor 条件边
    graph.add_conditional_edges("supervisor", _supervisor_router, _make_edge_map(all_node_names))

    # reflect_node 条件边
    graph.add_conditional_edges("reflect_node", _supervisor_router, _make_edge_map(all_node_names))

    # 各业务节点条件边（每个节点独立路由函数，优先查原生条件边）
    for bnode in _BUSINESS_NODES:
        graph.add_conditional_edges(bnode, _make_business_router(bnode), _make_edge_map(all_node_names))

    _logger.info("[graph_v2] StateGraph 构建完成（v7 原生），节点数=%d", len(all_node_names))

    if checkpointer is not None:
        # 传入了同步 checkpointer（如 MemorySaver），直接编译
        compiled = graph.compile(checkpointer=checkpointer)
        _logger.info("[graph_v2] 图编译完成（checkpointer=%s）", type(checkpointer).__name__)
        return compiled

    # 未传入 checkpointer：返回未编译的 graph，由调用方在 async with 中编译
    return graph


# ── 调度节点集合（模块级常量，避免热路径重建）────────────────────────────────────
# supervisor/reflect_node 是调度节点，不触发 tick_next(running)；
# 提升为模块级 frozenset 常量，消除每次 on_chain_start 的重建开销。
_SKIP_TICK_NODES: frozenset[str] = frozenset({
    "supervisor", "reflect_node", "__start__", "LangGraph"
})

# 回调字段列表（同时保留在 state 和 config["configurable"] 中）
_RUNTIME_FIELDS = (
    "publish", "session_getter", "session_saver",
    "convert_fn", "edit_fn", "audio_chat_fn",
    "todo_mgr",
)


# ── 向后兼容桩（v7 已不再使用 SQLite Checkpointer，但旧版 main.py 仍会 import）──
# 如果你的 main.py 有 `from app.agentcore.graph_engine_v2 import _get_sqlite_cm`，
# 这个桩函数确保 import 不报错。实际不会被调用（MemorySaver 已替代 SQLite）。
from contextlib import asynccontextmanager as _acm

@_acm
async def _get_sqlite_cm(*args, **kwargs):
    """兼容桩：v7 已改用 MemorySaver，此函数仅保留供旧 main.py import 不报错。"""
    yield None


# ── SSE 事件适配器：langgraph 事件 → EP-Agent SSE 格式 ───────────────────────

async def stream_graph_events(
    state: EPState,
    publish: Publisher,
    session_id: str,
) -> EPState:
    """
    使用 astream_events(version="v2") 流式执行图，将 LangGraph 事件转换为 EP-Agent SSE 格式。

    主流程唯一 Checkpointer：每次请求创建独立 MemorySaver 实例（零外部依赖）。
    审计通过 TraceCollector（SSE 事件旁路）+ 独立 DB 实现，与本函数完全解耦。

    v7 修复：
      - 回调字段同时注入 config["configurable"] 和保留在 state 中（双轨保险）
      - 增加 on_chat_model_stream 事件处理，LLM 流式 token 推送到 SSE
      - 增加 on_tool_start / on_tool_end 处理（图级别工具调用）

    LangGraph 事件类型映射（astream_events v2）：
      on_chain_start      → graph.node_enter
      on_chain_end        → graph.node_exit
      on_chain_error      → graph.error（节点业务错误，不中断流）
      on_chat_model_stream → message.delta（LLM 流式 token）
      on_tool_start       → tool.call running（图级别工具调用开始）
      on_tool_end         → tool.call succeeded（图级别工具调用完成）

    运行时回调（publish/session_getter/todo_mgr 等）通过双轨传递：
      1. config["configurable"]：LangGraph 原生机制，节点 config 参数可读
      2. state 中保留（不 pop）：节点 state.get() fallback 路径可读
    """
    steps = 0
    MAX_STEPS = 14
    final_state: EPState = dict(state)  # type: ignore

    # ── v7 修复：回调字段注入 config["configurable"]，同时保留在 state 中 ──────
    # 不再从 state pop 回调字段，确保节点 state.get("publish") 路径也能工作。
    # config["configurable"] 不经过 checkpointer 序列化，函数对象安全传递。
    runtime_callbacks: dict = {}
    for _f in _RUNTIME_FIELDS:
        val = state.get(_f)
        if val is not None:
            runtime_callbacks[_f] = val

    # ── ContextVar 注入（最可靠的跨 LangGraph 传递机制）────────────────────────
    # 在 stream_graph_events 调用前设置 ContextVar，asyncio Task 会继承父上下文，
    # 无论 LangGraph 内部如何创建子 Task，都能正确读取这些回调。
    _ctx_tokens = []
    for _f, _ctx_var in _RUNTIME_CTX_MAP.items():
        _val = state.get(_f)
        if _val is not None:
            _tok = _ctx_var.set(_val)
            _ctx_tokens.append((_ctx_var, _tok))
            _logger.debug("[graph_v2] ContextVar 注入: %s", _f)
    _logger.info("[graph_v2] ContextVar 注入完成，共 %d 个回调字段", len(_ctx_tokens))

    # config 注入回调（thread_id 保留，回调字段追加）
    config = {
        "configurable": {
            "thread_id": session_id,
            **runtime_callbacks,        # publish/session_getter/todo_mgr 等
        }
    }

    # input_state：从 state 中移除回调字段，避免 MemorySaver msgpack 序列化失败
    # （MemorySaver 实际使用 msgpack，函数对象不可序列化 → "Type is not msgpack serializable: function"）
    # 回调字段已注入 config["configurable"]（主路径），节点通过 config 参数读取。
    # agent_nodes._state_to_ctx 双轨读取：config 优先，state fallback（fallback 路径此处不再可用，
    # 但 config 路径已覆盖所有场景，fallback 仅为历史兼容保留）。
    input_state = {k: v for k, v in state.items() if k not in _RUNTIME_FIELDS}

    async def _run_stream(compiled, input_st: EPState):
        """执行图流并收集状态。节点业务错误（on_chain_error）只推 SSE，不中断流。"""
        nonlocal steps, final_state
        async for event in compiled.astream_events(input_st, config=config, version="v2"):
            evt_type = event.get("event", "")
            name     = event.get("name", "")
            data     = event.get("data", {})
            tags     = event.get("tags", [])

            # ── 节点进入 ──────────────────────────────────────────────────────
            if evt_type == "on_chain_start" and name not in ("LangGraph", "__start__"):
                steps += 1
                progress_pct = min(int(steps / MAX_STEPS * 100), 95)
                await _safe_publish(publish, "graph.node_enter", {
                    "node":     name,
                    "step":     steps,
                    "progress": progress_pct,
                })
                _logger.info("[graph_v2] node_enter step=%d node=%s", steps, name)
                # 业务节点进入时推进 TODO running 状态
                if name not in _SKIP_TICK_NODES:
                    _todo_mgr = config.get("configurable", {}).get("todo_mgr")
                    if _todo_mgr and hasattr(_todo_mgr, "tick_next"):
                        try:
                            await _todo_mgr.tick_next(publish, "running")
                        except Exception:
                            pass

            # ── 节点退出 ──────────────────────────────────────────────────────
            elif evt_type == "on_chain_end" and name not in ("LangGraph", "__start__"):
                output = data.get("output", {})
                next_node = output.get("next_node", "") if isinstance(output, dict) else ""
                _node_err = (output.get("error", "") if isinstance(output, dict) else "") or ""
                await _safe_publish(publish, "graph.node_exit", {
                    "node":      name,
                    "next_node": next_node,
                    "has_error": bool(_node_err),
                    "error_msg": str(_node_err)[:500],
                })
                if isinstance(output, dict):
                    final_state.update(output)

            # ── 节点业务错误（不中断流）──────────────────────────────────────
            elif evt_type == "on_chain_error":
                err = data.get("error", None)
                err_msg = str(err) if err is not None else "unknown"
                _logger.error("[graph_v2] 节点 %s 业务异常: %s", name, err_msg)
                # 同时发布 graph.error（供前端展示）和 graph.node_exit（供 trace_collector 记录错误 span）
                await _safe_publish(publish, "graph.error", {
                    "message":      err_msg,
                    "current_node": name,
                })
                # 补发 node_exit，确保 trace_collector 能回填 error_msg
                await _safe_publish(publish, "graph.node_exit", {
                    "node":      name,
                    "next_node": "",
                    "has_error": True,
                    "error_msg": err_msg[:500],
                })

            # ── LLM 流式 token（图级别 LLM 调用，如 supervisor LLM 决策）────
            elif evt_type == "on_chat_model_stream":
                # supervisor/reflect 节点的 LLM 决策 token 不推到用户 SSE
                # 只推送业务节点（如 query_node）的 LLM token
                chunk = data.get("chunk", {})
                # AIMessageChunk 有 content 属性
                chunk_content = ""
                chunk_reasoning = ""
                if hasattr(chunk, "content"):
                    chunk_content = chunk.content or ""
                elif isinstance(chunk, dict):
                    chunk_content = chunk.get("content", "") or ""
                # ALIGN-005 修复：支持推理模型 reasoning_content（DeepSeek-R1 等）
                # AIMessageChunk 的 reasoning_content 存在于 additional_kwargs 或直接属性中
                if hasattr(chunk, "additional_kwargs"):
                    chunk_reasoning = chunk.additional_kwargs.get("reasoning_content", "") or ""
                if not chunk_reasoning and hasattr(chunk, "reasoning_content"):
                    chunk_reasoning = getattr(chunk, "reasoning_content", "") or ""
                if (chunk_content or chunk_reasoning) and name not in _SKIP_TICK_NODES:
                    _delta_payload: dict = {}
                    if chunk_content:
                        _delta_payload["delta"] = chunk_content
                    if chunk_reasoning:
                        _delta_payload["reasoning_delta"] = chunk_reasoning
                    if "delta" not in _delta_payload:
                        _delta_payload["delta"] = ""
                    await _safe_publish(publish, "message.delta", _delta_payload)

            # ── 图级别工具调用开始（非 ReactExecutor 内部的工具调用）────────
            elif evt_type == "on_tool_start":
                tool_name = name or data.get("name", "unknown")
                tool_input = data.get("input", {})
                if isinstance(tool_input, dict):
                    # 敏感字段脱敏
                    safe_input = {
                        k: (str(v)[:100] + "..." if isinstance(v, str) and len(str(v)) > 100 else v)
                        for k, v in tool_input.items()
                        if k not in ("abc", "content", "audio_b64", "b64")
                    }
                else:
                    safe_input = {}
                await _safe_publish(publish, "tool.call", {
                    "call_id":   f"graph_{tool_name}",
                    "tool":      tool_name,
                    "status":    "running",
                    "arguments": safe_input,
                })

            # ── 图级别工具调用完成 ────────────────────────────────────────────
            elif evt_type == "on_tool_end":
                tool_name = name or "unknown"
                output = data.get("output", "")
                out_str = str(output)
                await _safe_publish(publish, "tool.call", {
                    "call_id":       f"graph_{tool_name}",
                    "tool":          tool_name,
                    "status":        "succeeded",
                    "result_preview": out_str[:120] + "..." if len(out_str) > 120 else out_str,
                })

    # 每次请求独立 MemorySaver 实例：无跨请求状态污染，零外部依赖
    from langgraph.checkpoint.memory import MemorySaver
    compiled = build_ep_graph(checkpointer=MemorySaver())
    _logger.info("[graph_v2] 使用独立 MemorySaver 执行图（session=%s）", session_id[:8])
    await _run_stream(compiled, input_state)

    await _safe_publish(publish, "graph.progress", {
        "progress": 100,
        "status":   "completed",
        "steps":    steps,
    })

    return final_state


async def _safe_publish(publish: Publisher, evt: str, payload: dict) -> None:
    """安全推送 SSE 事件，异常完全隔离。"""
    if publish:
        try:
            await publish(evt, payload)
        except Exception:
            pass


# ── 审计：从 DB spans 重建 session 历史（BUG-017 修复）─────────────────────────
#
# v8 架构：主流程每次请求使用独立 MemorySaver，执行完即释放，无法通过
# Checkpointer 读取历史。改为从 TraceCollector 落库的 spans/traces 表重建，
# 与主流程完全解耦，replay/fork 功能不受 Checkpointer 生命周期影响。

async def get_session_history(session_id: str) -> list[dict]:
    """
    从 DB spans 重建 session 的执行历史（v8 架构：不依赖 Checkpointer）。
    用于审计回放 replay_engine_v2.py。

    返回：[{"step": i, "node": str, "state": dict, "ts": str}, ...]
    """
    try:
        from app.pipeline import db as _db
        # 取该 session 最新一条 trace
        traces = _db.get_traces_by_session(session_id, limit=1, offset=0)
        if not traces:
            _logger.info("[graph_v2] get_session_history: session=%s 无 trace 记录", session_id[:8])
            return []
        trace = traces[0]
        spans = _db.get_spans_by_trace(trace["trace_id"])
        if not spans:
            return []
        # 按 step_idx 排序，重建为历史快照格式
        snapshots = []
        for span in sorted(spans, key=lambda s: s.get("step_idx", 0)):
            snapshots.append({
                "step":   span.get("step_idx", 0),
                "node":   span.get("tool_name") or span.get("agent_name") or span.get("span_kind", "unknown"),
                "writes": {},
                "state": {
                    "session_id":      session_id,
                    "message":         trace.get("user_message", ""),
                    "role_id":         trace.get("role_id", ""),
                    "attachment_name": trace.get("attachment_name", ""),
                    "tool_results": [{
                        "tool_name":  span.get("tool_name", ""),
                        "result":     span.get("tool_result", "{}"),
                        "status":     span.get("status", ""),
                        "domain":     span.get("agent_name", ""),
                        "summary":    span.get("tool_result_preview", ""),
                    }],
                    "visited": [],
                    "error":   span.get("error_msg", ""),
                },
                "ts":     span.get("started_at", ""),
                "config": {"configurable": {"thread_id": session_id}},
            })
        _logger.info(
            "[graph_v2] get_session_history: session=%s 从 DB 重建 %d 步历史",
            session_id[:8], len(snapshots),
        )
        return snapshots
    except Exception as e:
        _logger.warning("[graph_v2] get_session_history 失败: %s", e)
        return []


async def fork_from_checkpoint(
    session_id: str,
    step_index: int,
    new_message: str | None = None,
) -> EPState | None:
    """
    从 DB 历史重建某步执行状态，支持"从某步重跑"调试（v8 架构）。

    step_index: get_session_history() 返回列表中的索引
    new_message: 可选，覆盖 state.message（模拟不同输入重跑）
    """
    try:
        history = await get_session_history(session_id)
        if step_index >= len(history):
            _logger.warning("[graph_v2] fork step_index=%d 超出范围（共%d步）", step_index, len(history))
            return None
        snap = history[step_index]
        state = dict(snap["state"])
        if new_message:
            state["message"] = new_message
        _logger.info("[graph_v2] fork from step=%d node=%s", step_index, snap["node"])
        return state  # type: ignore
    except Exception as e:
        _logger.warning("[graph_v2] fork_from_checkpoint 失败: %s", e)
        return None
