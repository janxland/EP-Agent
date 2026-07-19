"""
SupervisorAgent — 图的"大脑"，LLM 驱动的动态路由决策器 (v7 LangGraph 原生)

迁移记录 v6：
  - 去掉旧版 @node 装饰器 + GraphState dataclass 依赖
  - 节点函数签名改为 LangGraph 原生：(state: EPState) -> dict
  - 条件边函数同步改为接受 EPState dict
  - 向后兼容：保留 REFLECTION_THRESHOLD / _find_last_real_node 供 reflect_agent 导入

v7 修复：
  - _VALID_NODES 默认预填充所有已知节点名，不再依赖 register_valid_nodes 时机
  - route_after_convert：仅当 abc_notation 真正为空时才降级 create_node
    （v6 的 not state.get("abc_notation") 在 abc_notation="" 时触发，
     v7 改为严格检查空字符串，避免误降级）
  - supervisor_node：LLM 决策失败时的 heuristic 兜底更智能，
    考虑 visited 和 abc_notation 状态
  - publish 双轨读取：config["configurable"] 优先，state fallback
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from app.agentcore.llm import complete

# LG-08 修复：路由函数统一返回 END 常量而非字符串 'END'
# LangGraph 0.2+ add_conditional_edges path_map 严格匹配 key，
# 路由函数返回字符串 'END' 而 map 里只有 END 常量会导致 KeyError
try:
    from langgraph.graph import END as _END
except ImportError:
    _END = "__end__"  # 降级占位，langgraph 未安装时不影响模块 import

if TYPE_CHECKING:
    pass

_logger = logging.getLogger("ep_agent.supervisor")

# ── 统一阈值常量（reflect_agent 直接从此处导入，单一来源）──────────────────────
REFLECTION_THRESHOLD = 0.65

# ── 关键词 → 节点名映射 ────────────────────────────────────────────────────────
_KEYWORD_NODE_MAP: list[tuple[list[str], str]] = [
    (["创作", "写一首", "谱一首", "create", "compose"],          "create_node"),
    (["转换", "解析", "导入", "convert", "parse", "import"],     "convert_node"),
    (["编辑", "修改", "转调", "变速", "加花", "edit", "modify"], "edit_node"),
    (["h5", "html", "网页", "页面", "海报", "poster"],           "h5_node"),
    (["音频", "配乐", "伴奏", "audio", "bgm", "music"],          "audio_node"),
    (["克隆", "声音", "音色", "sovits", "voice", "clone"],       "sovits_node"),
    (["问", "查", "解释", "什么是", "query", "what", "how"],     "query_node"),
]

# 附件扩展名 → 节点名映射
_EXT_NODE_MAP: dict[str, str] = {
    ".mid":  "h5_node",
    ".midi": "h5_node",
    ".txt":  "convert_node",
    ".json": "convert_node",
    ".abc":  "convert_node",
}

# 高风险节点：执行后自动插入 reflect_node
# PERF-02：create_node 已移除，主创作 LLM 一次性输出正确结果，无需事后质量反思
_NODES_REQUIRING_REFLECTION = {"edit_node", "h5_node", "h5_edit_node"}

# ── SupervisorAgent 决策 Prompt ────────────────────────────────────────────────
_SUPERVISOR_SYSTEM = f"""你是 EP-Agent 的编排主管（Supervisor）。
你的唯一职责：根据用户消息语义 + 当前角色能力范围，决定下一步调用哪个 Agent 节点。

可用节点及适用场景：
- convert_node : 解析附件（仅当有 .sky/.json/.abc/.txt 附件时，不用于处理消息内的ABC文本）
- create_node  : 创作/改编 ABC 谱（描述音乐需求，或粘贴参考ABC谱要求改编续写）
- edit_node    : 编辑已有 ABC 谱（转调/变速/加花等，需已有谱子）
- h5_node      : 生成 H5 乐谱海报/播放页面
- h5_edit_node : 编辑已有 H5 页面
- audio_node   : 生成真实可播放音频文件（调用 Suno/MiniMax，用户要的是音频而非谱子）
- sovits_node  : GPT-SoVITS 音色克隆/语音合成
- reflect_node : 质量反思（工具结果质量不佳时调用）
- query_node   : 回答问题/输出文字（无需生成文件）
- END          : 任务完成

关键区分（最容易混淆）：
⚠️ 用户要「生成音乐/生成一首歌/生成配乐」→ audio_node（要的是音频文件）
⚠️ 用户要「写谱子/创作ABC/改编旋律/续写」→ create_node（要的是乐谱）
⚠️ 消息中粘贴了ABC谱，但用户说「生成一首歌」→ audio_node（ABC只是旋律参考）
⚠️ 消息中粘贴了ABC谱，用户说「改编/续写/写X分钟版本」→ create_node

当前角色 allowed_nodes 会在上下文中告知，只能从中选择节点。

决策规则：
1. 优先根据用户消息核心意图（要音频？要谱子？要问答？）选节点
2. 若 reflection_score < {REFLECTION_THRESHOLD} 且 retry_count < 2 → reflect_node
3. 同一节点已执行 ≥ 2 次 → 强制 END
4. 上一步有错误且重试无意义 → END

输出严格 JSON，不要任何其他文字：
{{"next_node": "节点名或END", "reasoning": "一句话说明决策理由", "confidence": 0.0-1.0}}
"""

# ── 已注册的合法节点名（v7：预填充所有已知节点，不再依赖注册时机）──────────────
# graph_engine_v2.build_ep_graph() 调用 register_valid_nodes() 追加动态节点
_VALID_NODES: set[str] = {
    "supervisor", "reflect_node",
    "convert_node", "edit_node", "create_node",
    "h5_node", "h5_edit_node", "audio_node", "sovits_node", "query_node",
    "END",
}


def register_valid_nodes(node_names: list[str]) -> None:
    """由 graph_engine_v2.build_ep_graph() 调用，追加/更新合法节点名。
    v7：_VALID_NODES 已预填充，此函数作为扩展点保留。"""
    _VALID_NODES.update(node_names)
    _VALID_NODES.add("END")
    _logger.debug("[supervisor] 更新合法节点集合: %s", sorted(_VALID_NODES))


# ── LangGraph 原生节点函数（EPState dict → dict）──────────────────────────────

async def supervisor_node(state: dict, config: dict | None = None) -> dict:
    """
    Supervisor 节点（LangGraph 原生）：决定下一个要执行的节点。
    优先级：① 硬拦截超限 → ② 自动插入 reflect → ③ heuristic 短路 → ④ LLM 决策 → ⑤ heuristic 兜底
    config["configurable"] 含 publish 等运行时回调（BUG-011：不经 checkpointer 序列化）。

    v7 修复：
      - publish 双轨读取：config["configurable"] 优先，state fallback
      - _VALID_NODES 已预填充，LLM 决策不再因节点集合为空而全部被拒
    """
    visited      = state.get("visited") or []
    visit_counts = state.get("visit_counts") or {}
    message      = state.get("message", "")
    abc_notation = state.get("abc_notation", "")
    _cfg         = (config or {}).get("configurable", {})

    # v7：双轨读取 publish（config 优先，state fallback）
    publish = _cfg.get("publish") or state.get("publish")

    # ① 代码层硬拦截：同一节点访问次数 ≥ 2 → 强制 END（不依赖 LLM 遵守 Prompt 规则）
    # 这是防死循环的最后一道防线，必须在所有其他逻辑之前执行
    _LOOP_LIMIT = 2
    for _node, _cnt in visit_counts.items():
        if _node not in ("supervisor", "reflect_node") and _cnt >= _LOOP_LIMIT:
            _logger.warning(
                "[supervisor] 硬拦截：节点 %s 已执行 %d 次（上限 %d），强制 END",
                _node, _cnt, _LOOP_LIMIT,
            )
            if publish:
                try:
                    await publish("graph.supervisor_decision", {
                        "next_node":  "END",
                        "reasoning":  f"节点 {_node} 已执行 {_cnt} 次，超出上限，强制结束",
                        "confidence": 1.0,
                        "visited":    visited,
                        "forced":     True,
                    })
                except Exception:
                    pass
            return {"next_node": "END"}

    # ② 高风险节点执行完毕后，自动插入 reflect_node（零延迟，不调用 LLM）
    if visited and visited[-1] in _NODES_REQUIRING_REFLECTION:
        recent = visited[-3:]
        if "reflect_node" not in recent:
            _logger.info("[supervisor] 自动插入 reflect_node（上一节点: %s）", visited[-1])
            return {"next_node": "reflect_node"}

    # ③ 首次决策且信号明确 → heuristic 短路（避免消耗 LLM token）
    if not visited:
        if publish:
            try:
                await publish("pipeline.step", {
                    "step": "supervisor_decide", "status": "running",
                    "text": f"Supervisor 决策中（heuristic）msg={message[:40].replace(chr(10),' ')}",
                })
            except Exception:
                pass

        # ③-a intent_router 已有高置信度结论 → 直接采纳，跳过 LLM（最高优先级）
        initial_domain     = state.get("initial_domain", "")
        initial_confidence = float(state.get("initial_domain_confidence", 0.0))
        if initial_domain and initial_confidence >= 0.75:
            router_node = _DOMAIN_TO_NODE.get(initial_domain)
            if router_node and router_node in _VALID_NODES:
                _logger.info(
                    "[supervisor] intent_router 短路 → %s（domain=%s confidence=%.2f）",
                    router_node, initial_domain, initial_confidence,
                )
                if publish:
                    try:
                        await publish("pipeline.step", {
                            "step": "supervisor_decide", "status": "succeeded",
                            "text": f"intent_router 短路 → {router_node}（domain={initial_domain} conf={initial_confidence:.2f}）",
                        })
                        await publish("graph.supervisor_decision", {
                            "next_node":  router_node,
                            "reasoning":  f"intent_router 已判定 domain={initial_domain}，直接采纳",
                            "confidence": initial_confidence,
                            "visited":    visited,
                            "source":     "intent_router",
                        })
                    except Exception:
                        pass
                return {"next_node": router_node}

        # ③-b 附件扩展名等结构信号 → heuristic 短路
        shortcut = _heuristic_first(message, abc_notation, state.get("attachment_name", ""))
        if shortcut:
            _logger.info("[supervisor] heuristic 短路 → %s（跳过 LLM 决策）", shortcut)
            if publish:
                try:
                    await publish("pipeline.step", {
                        "step": "supervisor_decide", "status": "succeeded",
                        "text": f"heuristic 短路 → {shortcut}（跳过 LLM）",
                    })
                except Exception:
                    pass
            return {"next_node": shortcut}
        _logger.info("[supervisor] heuristic 未命中，走 LLM 决策 msg=%s", message[:60].replace('\n',' '))

    # ④ LLM 决策
    context, consumed_error = _build_supervisor_context(state)
    changes: dict = {}
    if consumed_error:
        changes["error"] = ""  # 消费错误

    _logger.info("[supervisor] LLM 决策开始 visited=%s", visited)
    if publish:
        try:
            await publish("pipeline.step", {
                "step": "supervisor_llm", "status": "running",
                "text": f"Supervisor LLM 决策中 visited={visited}",
            })
        except Exception:
            pass
    for _attempt in range(2):
        try:
            resp = await complete([
                {"role": "system", "content": _SUPERVISOR_SYSTEM},
                {"role": "user",   "content": context},
            ], tier="strong")

            raw = resp if isinstance(resp, str) else resp.get("content", "{}")
            m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if m:
                decision   = json.loads(m.group())
                next_node  = decision.get("next_node", "END")
                reasoning  = decision.get("reasoning", "")
                confidence = float(decision.get("confidence", 1.0))

                # 安全校验：LLM 返回的节点名必须合法
                # v7：_VALID_NODES 已预填充，不再出现全部拒绝的情况
                if next_node not in _VALID_NODES:
                    _logger.warning("[supervisor] LLM 返回非法节点 %r，兜底 END", next_node)
                    next_node = "END"

                _logger.info(
                    "[supervisor] LLM决策 next_node=%s confidence=%.2f reason=%s",
                    next_node, confidence, reasoning,
                )
                if publish:
                    try:
                        await publish("graph.supervisor_decision", {
                            "next_node":  next_node,
                            "reasoning":  reasoning,
                            "confidence": confidence,
                            "visited":    visited,
                        })
                        await publish("pipeline.step", {
                            "step": "supervisor_llm", "status": "succeeded",
                            "text": f"LLM 决策 → {next_node}（confidence={confidence:.2f}）",
                        })
                    except Exception:
                        pass
                changes["next_node"] = next_node
                return changes
            _logger.warning("[supervisor] 第%d次 LLM 响应无 JSON，重试", _attempt + 1)
        except Exception as exc:
            _logger.warning("[supervisor] LLM 调用失败（第%d次）: %s", _attempt + 1, exc)
            break

    # ⑤ 启发式兜底（LLM 失败时）
    fallback = _heuristic_fallback(state)
    _logger.info("[supervisor] 启发式兜底 next_node=%s", fallback)
    if publish:
        try:
            await publish("pipeline.step", {
                "step": "supervisor_llm", "status": "failed",
                "text": f"LLM 决策失败，启发式兜底 → {fallback}",
            })
        except Exception:
            pass
    changes["next_node"] = fallback
    return changes


# ── 内部辅助函数 ──────────────────────────────────────────────────────────────

_DOMAIN_TO_NODE: dict[str, str] = {
    "convert":   "convert_node",
    "create":    "create_node",
    "edit":      "edit_node",
    "audio":     "audio_node",
    "voice":     "audio_node",
    "sovits":    "sovits_node",
    "query":     "query_node",
    "h5_create": "h5_node",
    "h5_edit":   "h5_edit_node",
}


def _build_supervisor_context(state: dict) -> tuple[str, bool]:
    """构建 Supervisor 决策上下文，返回 (context_str, consumed_error)。

    注入 role_id 对应的 allowed_nodes，让 LLM 感知当前角色能力范围，
    避免选出角色不支持的节点（如乐谱专家选 audio_node）。
    """
    visited          = state.get("visited") or []
    message          = state.get("message", "")
    abc_notation     = state.get("abc_notation", "")
    reflection_score = float(state.get("reflection_score", 1.0))
    retry_count      = int(state.get("retry_count", 0))
    attachment_name  = state.get("attachment_name", "")
    tool_results     = state.get("tool_results") or []
    memory_context   = state.get("memory_context", "")
    outputs          = state.get("outputs") or {}
    error            = state.get("error", "")
    role_id          = state.get("role_id", "")

    # 从 role_config 读取当前角色允许的节点白名单
    allowed_nodes_str = ""
    if role_id:
        try:
            from app.agentcore.role_config import get_role_domains
            allowed_domains = get_role_domains(role_id)
            allowed_nodes = list({_DOMAIN_TO_NODE[d] for d in allowed_domains if d in _DOMAIN_TO_NODE})
            allowed_nodes += ["reflect_node", "END"]
            allowed_nodes_str = ", ".join(sorted(set(allowed_nodes)))
        except Exception:
            pass

    initial_domain     = state.get("initial_domain", "")
    initial_confidence = float(state.get("initial_domain_confidence", 0.0))

    parts = [
        f"用户消息：{message}",
        f"已访问节点：{' → '.join(visited) if visited else '无'}",
        f"当前 ABC 谱：{'有（' + str(len(abc_notation)) + '字符）' if abc_notation else '无'}",
        f"质量评分：{reflection_score:.2f}（阈值 {REFLECTION_THRESHOLD}）",
        f"重试次数：{retry_count}",
    ]
    if initial_domain:
        router_node = _DOMAIN_TO_NODE.get(initial_domain, "")
        parts.append(
            f"⚠️ intent_router 已判定 domain={initial_domain}（置信度={initial_confidence:.2f}）"
            + (f"，对应节点={router_node}，请优先选择此节点" if router_node else "")
        )
    if allowed_nodes_str:
        parts.append(f"当前角色 allowed_nodes（只能从这些节点中选择）：{allowed_nodes_str}")
    if attachment_name:
        parts.append(f"附件：{attachment_name}")
    if tool_results:
        last = tool_results[-1]
        parts.append(f"上一步结果摘要：{str(last.get('summary', last))[:120]}")
    if memory_context:
        parts.append(f"用户长期偏好：{memory_context[:300]}")
    if outputs:
        done = ', '.join(f"{k}✓" for k in outputs)
        parts.append(f"已完成步骤：{done}")
    consumed_error = False
    if error:
        parts.append(f"上一步错误：{error}")
        consumed_error = True
    return "\n".join(parts), consumed_error


def _match_keywords(msg_lower: str) -> str | None:
    for keywords, node_name in _KEYWORD_NODE_MAP:
        if any(kw in msg_lower for kw in keywords):
            return node_name
    return None


def _heuristic_first(message: str, abc_notation: str, attachment_name: str) -> str | None:
    """首次决策 heuristic 短路（仅在 visited 为空时调用）。

    设计原则（解耦版）：
      只处理「文件格式」这一零歧义结构信号，不做语义判断。
      语义判断（用户想要音频/谱子/问答）全部交给 LLM supervisor。

    保留唯一的结构信号：附件扩展名（文件格式是客观事实，无歧义）。
    移除的关键词匹配：
      - "K:" in message → create_node  （消息含ABC但用户可能要audio，需LLM判断）
      - _match_keywords（语义词）        （语义判断是LLM的职责）
    """
    if attachment_name:
        for ext, node_name in _EXT_NODE_MAP.items():
            if attachment_name.lower().endswith(ext):
                _logger.info("[supervisor] heuristic: 附件扩展名 %s → %s", ext, node_name)
                return node_name
    return None


def _heuristic_fallback(state: dict) -> str:
    """Supervisor LLM 失败时的启发式兜底决策（全覆盖，不返回 None）。

    设计原则（解耦版）：
      只基于「执行状态」做结构性兜底，不做语义关键词匹配。
      LLM 失败时保守处理：已有结果就 END，未开始就用角色默认域。
    """
    visited          = state.get("visited") or []
    abc_notation     = state.get("abc_notation", "")
    reflection_score = float(state.get("reflection_score", 1.0))
    retry_count      = int(state.get("retry_count", 0))
    last_node        = visited[-1] if visited else ""
    role_id          = state.get("role_id", "")

    # reflect 后：若质量不够且可重试 → 重试上一个实质节点，否则 END
    if last_node == "reflect_node":
        if reflection_score < REFLECTION_THRESHOLD and retry_count < 2:
            real = _find_last_real_node(visited)
            if real:
                return real
        return "END"

    # 业务节点执行完毕 → END（链式意图由 LLM 决策，兜底不猜）
    if last_node in ("convert_node", "create_node", "edit_node",
                     "audio_node", "h5_node", "h5_edit_node",
                     "sovits_node", "query_node"):
        return "END"

    # 首次决策 LLM 失败 → 用角色第一个允许的域对应节点，再兜底 create_node
    if not visited:
        if role_id:
            try:
                from app.agentcore.role_config import get_role_domains
                domains = get_role_domains(role_id)
                for d in domains:
                    if d in _DOMAIN_TO_NODE:
                        node = _DOMAIN_TO_NODE[d]
                        _logger.info("[supervisor] fallback: 角色首选域 %s → %s", d, node)
                        return node
            except Exception:
                pass
        return "create_node" if not abc_notation else "edit_node"

    return "END"


def _find_last_real_node(visited: list[str]) -> str | None:
    """找到最近一个非 supervisor/reflect 的实质性节点（reflect_agent 导入此函数）。"""
    skip = {"supervisor", "reflect_node"}
    for node_name in reversed(visited):
        if node_name not in skip:
            return node_name
    return None


# ── 条件边路由函数（LangGraph 原生，接受 EPState dict）────────────────────────

def route_after_convert(state: dict) -> str:
    """
    convert 完成后的路由（结构性，不做语义判断）：
      - ABC 输出为空 → create_node（让 LLM 根据用户描述创作）
      - 有 ABC → supervisor 重新决策（链式意图由 LLM 判断，不硬编码关键词）
    """
    abc = state.get("abc_notation") or ""
    if not abc.strip():
        _logger.info("[edge:convert_node] 无 ABC 输出，降级 → create_node")
        return "create_node"
    _logger.info("[edge:convert_node] convert 成功，回 supervisor 决策链式意图")
    return "supervisor"


def route_after_create(state: dict) -> str:
    """
    create 完成后的路由（结构性，不做语义判断）。

    修复死循环：create_node 不设置 next_node，
    若不加专属路由函数，router 会退化读 state["next_node"]（supervisor 留下的旧值），
    导致 create → router 读到 "convert_node" → 无限循环。

    路由规则：create 任务天然终止 → END。
    链式意图（如「创作完再生成H5」）由 supervisor LLM 在下一轮决策，
    不在此处硬编码关键词。
    """
    _logger.info("[edge:create_node] create 完成 → END")
    return _END


def route_after_edit(state: dict) -> str:
    """edit 完成后的路由 → END。链式意图由 supervisor LLM 决策，不硬编码关键词。"""
    _logger.info("[edge:edit_node] edit 完成 → END")
    return _END


def route_after_sovits(state: dict) -> str:
    """sovits 完成后的路由：链式音频意图 / 有错误回 supervisor / END（LG-08：返回 _END 常量）。"""
    if state.get("error"):
        return "supervisor"
    msg_lower = state.get("message", "").lower()
    audio_kws = [
        "生成歌曲", "生成一首", "唱一首", "生成音乐", "生成配乐",
        "克隆后生成", "用这个声音唱", "用这个声音生成", "克隆完再生成",
        "克隆声音后", "克隆好后", "然后生成", "再生成",
    ]
    if any(kw in msg_lower for kw in audio_kws):
        _logger.info("[edge:sovits_node] 链式意图 → audio_node")
        return "audio_node"
    return _END
