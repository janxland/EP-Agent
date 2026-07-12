"""
AgentNodes — 将现有 SubAgent 适配为 LangGraph 原生节点 (v7)

迁移记录 v7：
  - 去掉旧版 @node 装饰器 + GraphState dataclass 依赖
  - 节点函数签名改为 LangGraph 原生：(state: EPState dict, config: dict) -> dict
  - _state_to_ctx：从 EPState dict 构造 RunContext
  - _merge_result：返回变更 dict（LangGraph 自动 merge）
  - Agent 实例缓存保留（lru_cache）

v7 修复：
  - 移除 next_after 硬编码参数：业务节点不再强制设置 next_node。
    路由完全由 graph_engine_v2 的条件边函数决定（route_after_convert 等）。
    supervisor 通用节点返回 "supervisor" 让 supervisor 再次决策。
    query_node 特殊：任务天然终止，返回 "END"。
  - _state_to_ctx 优先从 config["configurable"] 读取回调（主路径），
    再从 state 读取（fallback 路径），双轨保险。
  - 节点异常时也正确追加 visited/visit_counts，防止 supervisor 无限重试。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable

from app.agentcore.run_context import RunContext
from app.agentcore.agent_registry import get_agent, ensure_all_agents_loaded

_logger = logging.getLogger("ep_agent.agent_nodes")

# ── 启动时强制 import 所有 agent 模块，触发 @register 装饰器 ──────────────────
# 必须在模块级别调用，确保 agent_nodes 被 import 时 _REGISTRY 已填充完毕。
ensure_all_agents_loaded()


# ── Agent 类缓存（避免每次请求重新 get_agent + 实例化）────────────────────────

@lru_cache(maxsize=32)
def _get_cached_agent_cls(domain: str):
    return get_agent(domain)


def _get_agent_cls_with_reload(domain: str):
    """
    获取 AgentClass，带懒加载兜底。

    lru_cache 在进程启动时可能缓存了 None（因为当时某个 agent 文件有语法错误），
    即使文件修复后缓存不会自动更新。
    兜底逻辑：get_agent 返回 None 时，清除 lru_cache 并重新调用
    ensure_all_agents_loaded()（强制 reload 所有模块），再查一次注册表。
    """
    cls = _get_cached_agent_cls(domain)
    if cls is not None:
        return cls
    # 兜底：清除 lru_cache，强制 reload 所有 agent 模块，再查一次
    _logger.warning(
        "[agent_nodes] domain='%s' 未在注册表中，清除缓存并强制重新加载 agents/", domain
    )
    _get_cached_agent_cls.cache_clear()
    # 强制 reload：清除 sys.modules 中的 agent 模块缓存，确保重新执行 @register
    import sys as _sys, importlib as _importlib
    _agents_pkg = "app.agentcore.agents"
    for _key in list(_sys.modules.keys()):
        if _key.startswith(_agents_pkg) and _key != f"{_agents_pkg}.agent_nodes":
            del _sys.modules[_key]
    ensure_all_agents_loaded()
    cls = get_agent(domain)
    if cls is not None:
        _logger.info("[agent_nodes] 强制 reload 后 domain='%s' 注册成功: %s", domain, cls.__name__)
    else:
        _logger.error("[agent_nodes] 强制 reload 后 domain='%s' 仍未注册，已注册: %s",
                      domain, sorted(get_agent.__module__ and [] or []))
    return cls


# ── EPState dict → RunContext 转换 ────────────────────────────────────────────

async def _noop_publish(evt_type: str, payload: dict, **kwargs):
    pass


def _state_to_ctx(state: dict, domain: str, config: dict | None = None) -> RunContext:
    """
    将 EPState dict 转换为 RunContext，供现有 SubAgent 使用。

    v7 修复（双轨读取）：
      1. 优先从 config["configurable"] 读取回调字段（LangGraph 原生传递路径）
      2. 回退到 state 读取（v7 新增：回调字段保留在 state 中，不再 pop）
    这样无论 LangGraph 版本如何传递 config，回调都能正确获取。
    """
    cfg = (config or {}).get("configurable", {})

    def _get(key: str, default=None):
        """优先从 config.configurable 读取，回退到 state。"""
        v = cfg.get(key)
        if v is not None:
            return v
        v = state.get(key)
        if v is not None:
            return v
        return default

    ctx = RunContext.from_request(
        session_id=state.get("session_id", ""),
        message=state.get("message", ""),
        publish=_get("publish") or _noop_publish,
        workspace_id=state.get("workspace_id", ""),
        role_id=state.get("role_id", ""),
        attachment_content=state.get("attachment_content", ""),
        attachment_name=state.get("attachment_name", ""),
        attachment_workspace_path=state.get("attachment_workspace_path", ""),
        attachment_b64=state.get("attachment_b64", ""),
        has_score=state.get("has_score", False),
    )
    return ctx.with_domain(domain).with_extra(
        session_getter=_get("session_getter"),
        session_saver=_get("session_saver"),
        convert_fn=_get("convert_fn"),
        edit_fn=_get("edit_fn"),
        audio_chat_fn=_get("audio_chat_fn"),
        todo_mgr=_get("todo_mgr"),
        abc_notation=state.get("abc_notation", ""),
        project_id=state.get("project_id", ""),
    )


def _merge_result(state: dict, result: dict, domain: str, node_name: str = "") -> dict:
    """
    将 SubAgent 返回的 result 转为 EPState 变更 dict（LangGraph 自动 merge）。

    关键：必须追加 visited + visit_counts，否则 supervisor 看不到已执行节点，
    无限循环保护（同一节点超过 2 次 → 强制 END）将完全失效。

    v7 修复：不再设置 next_node（路由由条件边函数决定），
    只有 query_node 例外（任务天然终止，设置 next_node="END"）。
    """
    changes: dict = {}

    # ── visited 追加（CRITICAL：supervisor 无限循环保护依赖此字段）────────────
    _nname = node_name or f"{domain}_node"
    existing_visited = list(state.get("visited") or [])
    existing_visited.append(_nname)
    changes["visited"] = existing_visited

    # visit_counts O(1) 计数（避免每次遍历 visited 列表）
    existing_counts = dict(state.get("visit_counts") or {})
    existing_counts[_nname] = existing_counts.get(_nname, 0) + 1
    changes["visit_counts"] = existing_counts

    # ── 关键修复：业务节点完成后必须清除 next_node ──────────────────────────────
    # supervisor 首次决策会把 next_node 写入 state（如 "convert_node"），
    # LangGraph 自动 merge 不会自动清除旧值。
    # 若业务节点不主动清除，router 读到的仍是 supervisor 留下的旧值，
    # 导致 create_node 完成后 router 读到 next_node="convert_node" 形成死循环。
    changes["next_node"] = ""  # 清除旧路由，让条件边函数或 supervisor 重新决策

    if not result:
        return changes

    new_abc = result.get("abc_notation", "")
    if new_abc:
        changes["abc_notation"] = new_abc
    if new_abc or result.get("has_score"):
        changes["has_score"] = True

    # tool_results 追加（LangGraph 不做 list append，需手动合并）
    existing_tr = list(state.get("tool_results") or [])
    existing_tr.append({
        "domain":  domain,
        "summary": result.get("content", result.get("message", ""))[:200],
        "extra":   result.get("extra", {}),
        "success": not bool(result.get("error")),
    })
    changes["tool_results"] = existing_tr

    err = result.get("error", "")
    if err:
        changes["error"] = str(err)
    else:
        existing_outputs = dict(state.get("outputs") or {})
        existing_outputs[domain] = result
        changes["outputs"]      = existing_outputs
        changes["final_output"] = result

    return changes


# ── 通用节点工厂（LangGraph 原生，消除重复代码）────────────────────────────────

def _make_node(
    node_name: str,
    domain: str,
    agent_label: str,
    is_terminal: bool = False,
) -> Callable:
    """
    工厂函数：生成 LangGraph 原生节点函数 (state: dict, config: dict) -> dict。

    node_name   : 注册到图中的节点名（如 "convert_node"）
    domain      : agent_registry 中的域名（如 "convert"）
    agent_label : 日志中显示的 Agent 名称
    is_terminal : True 时节点完成后设置 next_node="END"（如 query_node）
                  False 时不设置 next_node，由条件边路由函数决定路由

    v7 设计：
      - 移除 next_after 硬编码参数，避免与条件边路由冲突。
      - 非终止节点（convert/edit/create/h5/audio/sovits）不设置 next_node，
        条件边路由函数（route_after_convert 等）或 _supervisor_router 接管路由。
      - 终止节点（query）设置 next_node="END"。
    """
    async def _node_fn(state: dict, config: dict | None = None) -> dict:
        """
        LangGraph 原生节点函数，支持 (state, config) 双参数签名。
        config["configurable"] 中含 publish/todo_mgr 等运行时回调，
        不经过 checkpointer 序列化，避免 msgpack 不可序列化崩溃（BUG-011）。
        v7：回调字段同时在 state 和 config["configurable"] 中，双轨保险。
        """
        AgentCls = _get_agent_cls_with_reload(domain)
        if not AgentCls:
            _logger.error("[%s] %s 未找到（域名: %s）", node_name, agent_label, domain)
            # Agent 未注册时也要追加 visited，防止 supervisor 无限重试此节点
            _v = list(state.get("visited") or [])
            _v.append(node_name)
            _vc = dict(state.get("visit_counts") or {})
            _vc[node_name] = _vc.get(node_name, 0) + 1
            result = {
                "error": f"{agent_label} 未注册（域名: {domain}）",
                "visited": _v,
                "visit_counts": _vc,
            }
            if is_terminal:
                result["next_node"] = "END"
            return result

        # AUDIT-FIX-01 v2: 三轨道 publish 注入（config → state → ContextVar）
        # LangGraph 在某些版本下不把 config["configurable"] 正确传给节点，
        # ContextVar 是最可靠的备用通道：asyncio Task 继承父 ContextVar，
        # 无论 LangGraph 内部如何创建子 Task，都能正确读取。
        _cfg_cb = (config or {}).get("configurable", {})
        _state_with_callbacks = dict(state)

        # 导入 ContextVar 映射（graph_engine_v2 在 stream_graph_events 前已设置）
        try:
            from app.agentcore.graph_engine_v2 import _RUNTIME_CTX_MAP as _ctx_map
        except Exception:
            _ctx_map = {}

        for _cb_key in ("publish", "session_getter", "session_saver",
                        "convert_fn", "edit_fn", "audio_chat_fn", "todo_mgr"):
            if _state_with_callbacks.get(_cb_key) is not None:
                continue  # state 已有值，跳过
            # 轨道1：从 config["configurable"] 读取
            _cb_val = _cfg_cb.get(_cb_key)
            if _cb_val is None:
                # 轨道2：从 ContextVar 读取（最可靠）
                _ctx_var = _ctx_map.get(_cb_key)
                if _ctx_var is not None:
                    _cb_val = _ctx_var.get()
            if _cb_val is not None:
                _state_with_callbacks[_cb_key] = _cb_val

        if _state_with_callbacks.get("publish") is not None:
            _logger.debug("[%s] publish 注入成功（三轨道保险）", node_name)
        else:
            _logger.warning("[%s] publish 为 None，三轨道均未找到，tool.call 事件将丢失", node_name)

        ctx = _state_to_ctx(_state_with_callbacks, domain, config)
        try:
            result = await AgentCls().run_with_ctx(ctx)
            changes = _merge_result(state, result, domain, node_name)
            _logger.info(
                "[%s] 完成 abc_len=%d error=%s",
                node_name,
                len(changes.get("abc_notation", state.get("abc_notation", ""))),
                bool(changes.get("error")),
            )
        except Exception as exc:
            _logger.exception("[%s] 执行异常", node_name)
            # 异常路径也必须追加 visited，防止 supervisor 无限重试
            _v = list(state.get("visited") or [])
            _v.append(node_name)
            _vc = dict(state.get("visit_counts") or {})
            _vc[node_name] = _vc.get(node_name, 0) + 1
            changes = {"error": str(exc), "visited": _v, "visit_counts": _vc}

        # 终止节点强制设置 next_node="END"
        # 非终止节点不设置 next_node，由条件边路由函数接管
        if is_terminal:
            changes["next_node"] = "END"

        return changes

    _node_fn.__name__     = node_name
    _node_fn.__qualname__ = node_name
    return _node_fn


# ── 注册所有业务节点（一行一个，新增节点只需在此处加一行）─────────────────────
# v7 修复：移除 next_after 参数，改用 is_terminal 标记终止节点
convert_node  = _make_node("convert_node",  "convert",   "ConvertAgent")
edit_node     = _make_node("edit_node",     "edit",      "EditAgent")
create_node   = _make_node("create_node",   "create",    "CreateAgent")
h5_node       = _make_node("h5_node",       "h5_create", "H5Agent")
h5_edit_node  = _make_node("h5_edit_node",  "h5_edit",   "H5EditAgent")
audio_node    = _make_node("audio_node",    "audio",     "AudioAgent")
sovits_node   = _make_node("sovits_node",   "sovits",    "SoVITSAgent")
query_node    = _make_node("query_node",    "query",     "QueryAgent",  is_terminal=True)

# 节点名 → 函数映射（供 graph_engine_v2 直接注册，无需旧版 _NODE_REGISTRY）
NODE_REGISTRY: dict[str, Callable] = {
    "convert_node":  convert_node,
    "edit_node":     edit_node,
    "create_node":   create_node,
    "h5_node":       h5_node,
    "h5_edit_node":  h5_edit_node,
    "audio_node":    audio_node,
    "sovits_node":   sovits_node,
    "query_node":    query_node,
}
