"""
SupervisorAgent — 图的"大脑"，LLM 驱动的动态路由决策器 (v5.1)

优化记录：
  - 统一 REFLECTION_THRESHOLD 常量（消除 prompt/代码/reflect 三处不一致）
  - 关键词映射提取为 _KEYWORD_NODE_MAP 配置（消除散落的硬编码字符串）
  - 首次决策且有明确附件/关键词时走 heuristic 短路，跳过 LLM 调用
  - _build_supervisor_context 不再修改 state（副作用消除），改为返回 error_consumed 标志
"""
from __future__ import annotations

import json
import logging
import re

from app.agentcore.graph_engine import GraphState, node
from app.agentcore.llm import complete

_logger = logging.getLogger("ep_agent.supervisor")

# ── 统一阈值常量（与 reflect_agent.REFLECTION_THRESHOLD 保持一致）──────────────
REFLECTION_THRESHOLD = 0.65   # 低于此分数触发重试（supervisor/reflect/heuristic 三处共用）

# ── 关键词 → 节点名映射（单一配置源，消除散落硬编码）─────────────────────────────
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
_NODES_REQUIRING_REFLECTION = {"create_node", "edit_node", "h5_node", "h5_edit_node"}

# ── SupervisorAgent 决策 Prompt ────────────────────────────────────────────────
_SUPERVISOR_SYSTEM = f"""你是 EP-Agent 的编排主管（Supervisor）。
你的唯一职责：观察当前执行状态，决定下一步调用哪个 Agent 节点。

可用节点：
- convert_node   : 解析 Sky JSON / ABC 文件 → 生成 ABC 谱
- edit_node      : 编辑已有 ABC 谱（转调/变速/加花等）
- create_node    : 从零创作 ABC 谱
- h5_node        : 生成 H5 乐谱海报页面
- h5_edit_node   : 编辑已有 H5 页面
- audio_node     : 生成/迭代配乐音频
- sovits_node    : GPT-SoVITS 音色克隆
- reflect_node   : 质量反思（当工具结果质量不佳时调用）
- query_node     : 回答问题（无需修改谱子）
- END            : 任务完成，返回最终结果

决策规则：
1. 若 reflection_score < {REFLECTION_THRESHOLD} 且 retry_count < 2 → 调用 reflect_node 后重试原节点
2. 若用户请求包含多个意图（如"转换后生成H5"）→ 先完成第一个，再决策第二个
3. 若 abc_notation 为空且用户要编辑 → 先 convert_node 或 create_node
4. 已访问节点列表中同一节点超过 2 次 → 强制 END
5. 若上一步有错误且重试无意义 → END

输出严格 JSON，不要任何其他文字：
{{"next_node": "节点名或END", "reasoning": "一句话说明决策理由", "confidence": 0.0-1.0}}
"""


@node("supervisor")
async def supervisor_node(state: GraphState) -> GraphState:
    """
    Supervisor 节点：决定下一个要执行的节点。
    优先级：① 自动插入 reflect → ② heuristic 短路 → ③ LLM 决策 → ④ heuristic 兜底
    """
    # ① 高风险节点执行完毕后，自动插入 reflect_node（不经过 LLM，零延迟）
    if state.visited and state.visited[-1] in _NODES_REQUIRING_REFLECTION:
        recent = state.visited[-3:]
        if "reflect_node" not in recent:
            state.next_node = "reflect_node"
            _logger.info("[supervisor] 自动插入 reflect_node（上一节点: %s）", state.visited[-1])
            return state

    # ② 首次决策且信号明确 → heuristic 短路，跳过 LLM 调用（节省 ~500ms）
    if not state.visited:
        shortcut = _heuristic_first(state)
        if shortcut:
            state.next_node = shortcut
            _logger.info("[supervisor] 首次决策 heuristic 短路 → %s", shortcut)
            return state

    # ③ LLM 决策
    context, consumed_error = _build_supervisor_context(state)
    if consumed_error:
        state.error = ""   # 消费错误，避免重复传递

    # SUPER-1 修复：supervisor 决策改用 standard tier，lite 模型 JSON 解析能力弱
    # 同时增加 JSON 解析重试：若第一次解析失败，再尝试一次（防止 LLM 在 JSON 外包裹文字）
    for _attempt in range(2):
        try:
            resp = await complete([
                {"role": "system", "content": _SUPERVISOR_SYSTEM},
                {"role": "user",   "content": context},
            ], tier="standard")

            raw = resp if isinstance(resp, str) else resp.get("content", "{}")
            m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if m:
                decision = json.loads(m.group())
                next_node  = decision.get("next_node", "END")
                reasoning  = decision.get("reasoning", "")
                confidence = float(decision.get("confidence", 1.0))

                # 安全校验：LLM 返回的节点名必须合法
                from app.agentcore.graph_engine import list_nodes
                valid = set(list_nodes()) | {"END"}
                if next_node not in valid:
                    _logger.warning("[supervisor] LLM 返回非法节点 %r，兜底 END", next_node)
                    next_node = "END"

                state.next_node = next_node
                _logger.info(
                    "[supervisor] LLM决策 next_node=%s confidence=%.2f reason=%s",
                    state.next_node, confidence, reasoning,
                )
                if state.publish:
                    try:
                        await state.publish("graph.supervisor_decision", {
                            "next_node":  state.next_node,
                            "reasoning":  reasoning,
                            "confidence": confidence,
                            "visited":    state.visited,
                        })
                    except Exception:
                        pass
                return state
            # JSON 未找到，重试
            _logger.warning("[supervisor] 第%d次 LLM 响应无 JSON，重试", _attempt + 1)
        except Exception as exc:
            _logger.warning("[supervisor] LLM 调用失败（第%d次）: %s", _attempt + 1, exc)
            break  # 网络/API 错误不重试

    # ④ 启发式兜底
    state.next_node = _heuristic_fallback(state)
    _logger.info("[supervisor] 启发式兜底 next_node=%s", state.next_node)
    return state


def _build_supervisor_context(state: GraphState) -> tuple[str, bool]:
    """
    构建 Supervisor 决策上下文。
    返回 (context_str, consumed_error)，不直接修改 state.error（消除副作用）。
    """
    parts = [
        f"用户消息：{state.message}",
        f"已访问节点：{' → '.join(state.visited) if state.visited else '无'}",
        f"当前 ABC 谱：{'有（' + str(len(state.abc_notation)) + '字符）' if state.abc_notation else '无'}",
        f"质量评分：{state.reflection_score:.2f}（阈值 {REFLECTION_THRESHOLD}）",
        f"重试次数：{state.retry_count}",
    ]
    if state.attachment_name:
        parts.append(f"附件：{state.attachment_name}")
    if state.tool_results:
        last = state.tool_results[-1]
        parts.append(f"上一步结果摘要：{str(last.get('summary', last))[:120]}")
    # 注入长期记忆上下文（用户偏好/历史风格）
    if getattr(state, 'memory_context', ''):
        parts.append(f"用户长期偏好：{state.memory_context[:300]}")
    # 已完成节点的输出摘要（多步场景感知）
    if state.outputs:
        done = ', '.join(f"{k}✓" for k in state.outputs)
        parts.append(f"已完成步骤：{done}")
    consumed_error = False
    if state.error:
        parts.append(f"上一步错误：{state.error}")
        consumed_error = True
    return "\n".join(parts), consumed_error


def _match_keywords(msg_lower: str) -> str | None:
    """在 _KEYWORD_NODE_MAP 中匹配第一个命中的节点名。"""
    for keywords, node_name in _KEYWORD_NODE_MAP:
        if any(kw in msg_lower for kw in keywords):
            return node_name
    return None


def _heuristic_first(state: GraphState) -> str | None:
    """
    首次决策的 heuristic 短路（仅在 visited 为空时调用）。
    信号明确时返回节点名，不确定时返回 None（交给 LLM）。
    """
    msg_lower = state.message.lower()

    # 附件扩展名优先（最确定的信号）
    if state.attachment_name:
        for ext, node_name in _EXT_NODE_MAP.items():
            if state.attachment_name.lower().endswith(ext):
                return node_name

    # 关键词匹配
    matched = _match_keywords(msg_lower)
    if matched:
        # h5 需要先有谱子
        if matched == "h5_node" and not state.abc_notation:
            return "create_node"
        return matched

    # 无明确信号 → 交给 LLM
    return None


def _heuristic_fallback(state: GraphState) -> str:
    """Supervisor LLM 失败时的启发式兜底决策（全覆盖，不返回 None）。"""
    msg_lower = state.message.lower()
    last_node = state.visited[-1] if state.visited else ""

    # convert 完成后
    if last_node == "convert_node" and state.abc_notation:
        matched = _match_keywords(msg_lower)
        if matched in ("h5_node", "edit_node"):
            return matched
        return "END"

    # reflect 完成后
    if last_node == "reflect_node":
        if state.reflection_score < REFLECTION_THRESHOLD and state.retry_count < 2:
            real = _find_last_real_node(state.visited)
            if real:
                return real
        return "END"

    # create/edit 完成后（复用 _KEYWORD_NODE_MAP 中 h5_node 的关键词，消除硬编码）
    if last_node in ("create_node", "edit_node"):
        if _match_keywords(msg_lower) == "h5_node":
            return "h5_node"
        return "END"

    # 首次决策兜底（LLM 和 heuristic_first 都失败）
    if not state.visited:
        matched = _match_keywords(msg_lower)
        if matched:
            if matched == "h5_node" and not state.abc_notation:
                return "create_node"
            return matched
        return "create_node" if not state.abc_notation else "edit_node"

    return "END"


def _find_last_real_node(visited: list[str]) -> str | None:
    """
    找到最近一个非 supervisor/reflect 的实质性节点。
    单一定义：reflect_agent 直接从此处导入，消除重复。
    """
    skip = {"supervisor", "reflect_node"}
    for node_name in reversed(visited):
        if node_name not in skip:
            return node_name
    return None
