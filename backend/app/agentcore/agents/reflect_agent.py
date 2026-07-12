"""
ReflectAgent — 质量反思节点 (v6 LangGraph 原生)

迁移记录 v6：
  - 去掉旧版 @node 装饰器 + GraphState dataclass 依赖
  - 节点函数签名改为 LangGraph 原生：(state: EPState dict) -> dict
  - 只返回变更字段（LangGraph 自动 merge）
"""
from __future__ import annotations

import json
import logging
import re

from app.agentcore.llm import complete
from app.agentcore.supervisor_agent import REFLECTION_THRESHOLD, _find_last_real_node

_logger = logging.getLogger("ep_agent.reflect")

_REFLECT_SYSTEM = """你是 EP-Agent 的质量评审员（Critic）。
评估上一步的执行结果，给出质量评分和改进建议。

评估维度：
1. 完整性：任务是否完整完成？
2. 正确性：输出格式是否正确？（ABC 语法、HTML 结构等）
3. 用户满意度：是否符合用户原始请求？

输出严格 JSON：
{"score": 0.0-1.0, "passed": true/false, "issues": ["问题1"], "suggestion": "改进建议"}

评分标准：
  0.9-1.0：优秀，直接通过
  0.7-0.9：良好，可通过
  0.5-0.7：一般，建议重试
  < 0.5：较差，必须重试
"""


async def reflect_node(state: dict, config: dict | None = None) -> dict:
    """
    反思节点（LangGraph 原生）：评估上一步输出质量，决定是否需要重试。
    只返回变更字段，LangGraph 自动 merge 到 EPState。
    config["configurable"] 含 publish 等运行时回调（BUG-011：不经 checkpointer 序列化）。
    """
    message      = state.get("message", "")
    abc_notation = state.get("abc_notation", "")
    tool_results = state.get("tool_results") or []
    error        = state.get("error", "")
    visited      = state.get("visited") or []
    retry_count  = int(state.get("retry_count", 0))
    _cfg         = (config or {}).get("configurable", {})
    publish      = _cfg.get("publish") or state.get("publish")

    context_parts = [f"用户原始请求：{message}"]
    if abc_notation:
        context_parts.append(f"生成的 ABC 谱（前500字）：\n{abc_notation[:500]}")
    if tool_results:
        last_result = tool_results[-1]
        context_parts.append(f"上一步执行结果：{str(last_result)[:300]}")

    changes: dict = {}

    # 有错误时直接给低分，不消耗 LLM token
    if error:
        reflection_score = 0.3
        reflection_notes = f"上一步发生错误：{error}"
        _logger.warning("[reflect] 上一步有错误，直接低分: %s", error)
    else:
        try:
            resp = await complete([
                {"role": "system", "content": _REFLECT_SYSTEM},
                {"role": "user",   "content": "\n".join(context_parts)},
            ], tier="lite")

            raw = resp if isinstance(resp, str) else resp.get("content", "{}")
            m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if m:
                review = json.loads(m.group())
                reflection_score = float(review.get("score", 0.5))
                reflection_notes = review.get("suggestion", "")
                issues  = review.get("issues", [])
                passed  = review.get("passed", reflection_score >= REFLECTION_THRESHOLD)

                _logger.info(
                    "[reflect] score=%.2f passed=%s issues=%s",
                    reflection_score, passed, issues,
                )

                if publish:
                    try:
                        await publish("graph.reflection", {
                            "score":      reflection_score,
                            "passed":     passed,
                            "issues":     issues,
                            "suggestion": reflection_notes,
                        })
                    except Exception:
                        pass
            else:
                reflection_score = 0.7
                reflection_notes = ""
        except Exception as exc:
            _logger.warning("[reflect] LLM 评估失败，默认通过: %s", exc)
            reflection_score = 0.7
            reflection_notes = ""

    changes["reflection_score"] = reflection_score
    changes["reflection_notes"] = reflection_notes

    # 决策：通过 or 重试
    if reflection_score >= REFLECTION_THRESHOLD:
        changes["next_node"]   = "supervisor"
        changes["retry_count"] = 0
        _logger.info("[reflect] 质量通过 score=%.2f → supervisor", reflection_score)
    else:
        last_real_node = _find_last_real_node(visited)
        if retry_count < 2 and last_real_node:
            changes["retry_count"] = retry_count + 1
            changes["next_node"]   = last_real_node
            # 将反思建议注入消息，下一轮 LLM 能看到
            changes["message"] = (
                f"{message}\n\n"
                f"[质量反思-第{retry_count + 1}次重试] {reflection_notes}"
            )
            _logger.info(
                "[reflect] 质量不通过 score=%.2f，重试 %s（第%d次）",
                reflection_score, last_real_node, retry_count + 1,
            )
        else:
            changes["retry_count"] = 0
            changes["next_node"]   = "supervisor"
            _logger.warning(
                "[reflect] 重试次数耗尽（retry_count=%d），强制通过",
                retry_count,
            )

    return changes
