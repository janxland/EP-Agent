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
你的唯一职责：观察当前执行状态，决定下一步调用哪个 Agent 节点。

可用节点：
- convert_node   : 解析用户上传的 Sky JSON / ABC 附件文件 → 加载 ABC 谱（仅用于处理附件）
- create_node    : 创作或改编 ABC 谱（用户描述需求、或消息中粘贴了参考ABC文本时均走此节点）
- edit_node      : 编辑已有 ABC 谱（转调/变速/加花等）
- h5_node        : 生成 H5 乐谱海报页面
- h5_edit_node   : 编辑已有 H5 页面
- audio_node     : 生成/迭代配乐音频
- sovits_node    : GPT-SoVITS 音色克隆
- reflect_node   : 质量反思（当工具结果质量不佳时调用）
- query_node     : 回答问题（无需修改谱子）
- END            : 任务完成，返回最终结果

决策规则：
1. 消息中含有 ABC 谱文本（无论多行还是单行紧凑格式）且无附件 → create_node（改编模式）
2. 用户上传了 .json/.abc/.txt 附件 → convert_node 解析附件
3. 若 reflection_score < {REFLECTION_THRESHOLD} 且 retry_count < 2 → reflect_node 后重试原节点
4. 若用户请求包含多个意图（如"转换后生成H5"）→ 先完成第一个，再决策第二个
5. 已访问节点列表中同一节点超过 2 次 → 强制 END
6. 若上一步有错误且重试无意义 → END

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

def _build_supervisor_context(state: dict) -> tuple[str, bool]:
    """构建 Supervisor 决策上下文，返回 (context_str, consumed_error)。"""
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

    parts = [
        f"用户消息：{message}",
        f"已访问节点：{' → '.join(visited) if visited else '无'}",
        f"当前 ABC 谱：{'有（' + str(len(abc_notation)) + '字符）' if abc_notation else '无'}",
        f"质量评分：{reflection_score:.2f}（阈值 {REFLECTION_THRESHOLD}）",
        f"重试次数：{retry_count}",
    ]
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

    PERF-04：消息中含 ABC 内容（K: 字段）时直接短路 create_node，
    避免 supervisor LLM 决策（节省 2 次 LLM 调用）。
    """
    msg_lower = message.lower()
    # 附件扩展名优先（最明确的信号）
    if attachment_name:
        for ext, node_name in _EXT_NODE_MAP.items():
            if attachment_name.lower().endswith(ext):
                return node_name

    # ABC 内容检测：消息里直接粘贴了 ABC 谱（含 K: 字段）→ 直接 create_node
    # 这是最常见场景（用户粘贴参考谱改编），无需 LLM 判断
    if "K:" in message and ("M:" in message or "L:" in message or "Q:" in message):
        _logger.info("[supervisor] heuristic: 消息含 ABC 内容 → create_node")
        return "create_node"

    matched = _match_keywords(msg_lower)
    if matched:
        if matched == "h5_node" and not abc_notation:
            return "create_node"
        return matched
    return None


def _heuristic_fallback(state: dict) -> str:
    """Supervisor LLM 失败时的启发式兜底决策（全覆盖，不返回 None）。"""
    visited          = state.get("visited") or []
    message          = state.get("message", "")
    abc_notation     = state.get("abc_notation", "")
    reflection_score = float(state.get("reflection_score", 1.0))
    retry_count      = int(state.get("retry_count", 0))
    msg_lower        = message.lower()
    last_node        = visited[-1] if visited else ""

    if last_node == "convert_node" and abc_notation:
        matched = _match_keywords(msg_lower)
        if matched in ("h5_node", "edit_node"):
            return matched
        return "END"

    if last_node == "reflect_node":
        if reflection_score < REFLECTION_THRESHOLD and retry_count < 2:
            real = _find_last_real_node(visited)
            if real:
                return real
        return "END"

    if last_node in ("create_node", "edit_node"):
        if _match_keywords(msg_lower) == "h5_node":
            return "h5_node"
        return "END"

    if not visited:
        matched = _match_keywords(msg_lower)
        if matched:
            if matched == "h5_node" and not abc_notation:
                return "create_node"
            return matched
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
    convert 完成后的路由：
      - ABC 输出为空 → 降级 create_node（让 LLM 根据用户描述创作）
      - 有 ABC 且消息含链式意图 → 直接路由到对应节点
      - 否则 → END（任务完成）

    LG-08 修复：返回 _END 常量而非字符串 'END'，
    确保与 add_conditional_edges path_map 的 key 严格匹配。
    """
    abc = state.get("abc_notation") or ""
    if not abc.strip():
        _logger.info("[edge:convert_node] 无 ABC 输出，降级 → create_node")
        return "create_node"
    msg_lower = state.get("message", "").lower()
    if any(kw in msg_lower for kw in ["h5", "html", "网页", "页面", "海报", "poster"]):
        _logger.info("[edge:convert_node] 链式意图 → h5_node")
        return "h5_node"
    if any(kw in msg_lower for kw in ["编辑", "修改", "转调", "变速", "加花", "edit", "modify"]):
        _logger.info("[edge:convert_node] 链式意图 → edit_node")
        return "edit_node"
    _logger.info("[edge:convert_node] convert 成功，任务完成 → END")
    return _END


def route_after_create(state: dict) -> str:
    """
    create 完成后的路由。

    修复死循环：create_node 不设置 next_node，
    若不加专属路由函数，router 会退化读 state["next_node"]（supervisor 留下的旧值），
    导致 create → router 读到 "convert_node" → 无限循环。

    路由规则：
      - 有链式 h5 意图 → h5_node
      - 其他情况 → END（创作任务天然终止）

    LG-08 修复：返回 _END 常量而非字符串 'END'。
    """
    msg_lower = state.get("message", "").lower()
    if any(kw in msg_lower for kw in ["h5", "html", "网页", "页面", "海报", "poster"]):
        _logger.info("[edge:create_node] 链式意图 → h5_node")
        return "h5_node"
    _logger.info("[edge:create_node] create 完成，任务完成 → END")
    return _END


def route_after_edit(state: dict) -> str:
    """edit 完成后的路由：链式意图 h5 / END（LG-08：返回 _END 常量）。"""
    msg_lower = state.get("message", "").lower()
    if any(kw in msg_lower for kw in ["h5", "html", "网页", "页面", "海报", "poster"]):
        _logger.info("[edge:edit_node] 链式意图 → h5_node")
        return "h5_node"
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
