"""
AgentNodes — 将现有 SubAgent 适配为图引擎 @node 节点 (v5)

桥接层：GraphState ↔ RunContext
  - GraphState 是图引擎的共享状态（跨节点传递）
  - RunContext 是现有 SubAgent 的请求上下文（单次请求）
  - 本文件通过工厂函数消除8个节点的重复代码

优化记录（v5.1）：
  - 用 _make_node() 工厂替代 153 行重复的节点定义（压缩至 ~50 行）
  - Agent 实例缓存：get_agent() 结果按域名缓存，避免每次请求重新实例化
  - ContextVar 注入移至 _state_to_ctx 入口一次完成，不再每个节点重复
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable

from app.agentcore.graph_engine import GraphState, node
from app.agentcore.run_context import RunContext
from app.agentcore.agent_registry import get_agent

_logger = logging.getLogger("ep_agent.agent_nodes")


# ── Agent 类缓存（避免每次请求重新 get_agent + 实例化）────────────────────────

@lru_cache(maxsize=32)
def _get_cached_agent_cls(domain: str):
    """按域名缓存 AgentCls，避免重复查表。"""
    return get_agent(domain)


# ── GraphState → RunContext 转换 ─────────────────────────────────────────────

# P2 修复：_noop_publish 已移至 graph_engine.py 统一导出
from app.agentcore.graph_engine import noop_publish as _noop_publish


def _state_to_ctx(state: GraphState, domain: str) -> RunContext:
    """
    将 GraphState 转换为 RunContext，供现有 SubAgent 使用。
    ContextVar（session_id / trace_id）在此处注入一次，节点内无需重复注入。
    """
    ctx = RunContext.from_request(
        session_id=state.session_id,
        message=state.message,
        publish=state.publish or _noop_publish,
        workspace_id=state.workspace_id,
        role_id=state.role_id,
        attachment_content=state.attachment_content,
        attachment_name=state.attachment_name,
        attachment_workspace_path=state.attachment_workspace_path,
        attachment_b64=state.attachment_b64,
        has_score=state.has_score,
    )
    return ctx.with_domain(domain).with_extra(
        session_getter=state.session_getter,
        session_saver=state.session_saver,
        convert_fn=state.convert_fn,
        edit_fn=state.edit_fn,
        todo_mgr=state.todo_mgr,
        abc_notation=state.abc_notation,
    )


def _merge_result(state: GraphState, result: dict, domain: str) -> GraphState:
    """将 SubAgent 返回的 result 合并回 GraphState。"""
    if not result:
        return state

    new_abc = result.get("abc_notation", "")
    if new_abc:
        state.abc_notation = new_abc
    if new_abc or result.get("has_score"):
        state.has_score = True

    state.tool_results.append({
        "domain":  domain,
        "summary": result.get("content", "")[:200],
        "extra":   result.get("extra", {}),
        "success": not bool(result.get("error")),
    })

    err = result.get("error", "")
    if err:
        state.error = str(err)
    else:
        # 按 domain 分类存储，不互相覆盖（convert 的 abc 不会被 h5 覆盖）
        state.outputs[domain] = result
        # final_output 指向最后一个成功节点（supervisor 汇总时可读取 state.outputs）
        state.final_output = result

    return state


# ── 通用节点工厂（消除8个节点的重复代码）────────────────────────────────────────

def _make_node(
    node_name: str,
    domain: str,
    agent_label: str,
    next_after: str = "supervisor",
) -> Callable:
    """
    工厂函数：生成一个标准的 @node 包装函数。

    node_name   : 注册到图引擎的节点名（如 "convert_node"）
    domain      : agent_registry 中的域名（如 "convert"）
    agent_label : 日志中显示的 Agent 名称（如 "ConvertAgent"）
    next_after  : 执行完成后跳转的节点（默认 supervisor，query 用 END）
    """
    @node(node_name)
    async def _node_fn(state: GraphState) -> GraphState:
        AgentCls = _get_cached_agent_cls(domain)
        if not AgentCls:
            state.error = f"{agent_label} 未注册（域名: {domain}）"
            _logger.error("[%s] %s 未找到", node_name, agent_label)
            state.next_node = next_after
            return state

        ctx = _state_to_ctx(state, domain)
        try:
            result = await AgentCls().run_with_ctx(ctx)
            state = _merge_result(state, result, domain)
            _logger.info(
                "[%s] 完成 abc_len=%d error=%s",
                node_name, len(state.abc_notation), bool(state.error),
            )
        except Exception as exc:
            state.error = str(exc)
            _logger.exception("[%s] 执行异常", node_name)

        state.next_node = next_after
        return state

    # 让函数名可读（调试友好）
    _node_fn.__name__ = node_name
    _node_fn.__qualname__ = node_name
    return _node_fn


# ── 注册所有业务节点（一行一个，清晰可维护）─────────────────────────────────────
#
# 新增节点只需在此处加一行：
#   _make_node("new_node", "new_domain", "NewAgent")  # GRAPH-2: 示例行，替换为实际节点
#
convert_node  = _make_node("convert_node",  "convert",  "ConvertAgent")
edit_node     = _make_node("edit_node",     "edit",     "EditAgent")
create_node   = _make_node("create_node",   "create",   "CreateAgent")
h5_node       = _make_node("h5_node",       "h5_create","H5Agent")
h5_edit_node  = _make_node("h5_edit_node",  "h5_edit",  "H5EditAgent")
audio_node    = _make_node("audio_node",    "audio",    "AudioAgent")
sovits_node   = _make_node("sovits_node",   "sovits",   "SoVITSAgent")
query_node    = _make_node("query_node",    "query",    "QueryAgent",   next_after="END")
