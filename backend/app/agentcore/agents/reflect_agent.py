"""
ReflectAgent — 质量反思节点 (v5)

在关键步骤后插入，强制 LLM 对输出进行质量评估：
  - ABC 谱创作/编辑完成后：检查音符范围、节奏合理性、ABC 语法
  - H5 页面生成后：检查模板变量是否全部替换
  - 音频生成后：检查是否符合用户风格要求

评分低于阈值时：
  - 将反思内容注入下一轮 LLM 上下文（state.message）
  - 触发原节点重试（最多 2 次）

这解决了 v4 的核心问题：工具结果质量无自动评估。
"""
from __future__ import annotations

import json
import logging
import re

from app.agentcore.graph_engine import GraphState, node
from app.agentcore.llm import complete
from app.agentcore.supervisor_agent import REFLECTION_THRESHOLD, _find_last_real_node  # 单一来源

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


@node("reflect_node")
async def reflect_node(state: GraphState) -> GraphState:
    """
    反思节点：评估上一步输出质量，决定是否需要重试。
    通过 graph_engine 的 @node 装饰器注册到图中。
    """
    context_parts = [f"用户原始请求：{state.message}"]

    if state.abc_notation:
        context_parts.append(
            f"生成的 ABC 谱（前500字）：\n{state.abc_notation[:500]}"
        )

    if state.tool_results:
        last_result = state.tool_results[-1]
        context_parts.append(
            f"上一步执行结果：{str(last_result)[:300]}"
        )

    # 有错误时直接给低分，不消耗 LLM token
    if state.error:
        state.reflection_score = 0.3
        state.reflection_notes = f"上一步发生错误：{state.error}"
        _logger.warning("[reflect] 上一步有错误，直接低分: %s", state.error)
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
                state.reflection_score = float(review.get("score", 0.5))
                state.reflection_notes = review.get("suggestion", "")
                issues = review.get("issues", [])
                passed = review.get("passed", state.reflection_score >= REFLECTION_THRESHOLD)

                _logger.info(
                    "[reflect] score=%.2f passed=%s issues=%s",
                    state.reflection_score, passed, issues,
                )

                if state.publish:
                    try:
                        await state.publish("graph.reflection", {
                            "score":      state.reflection_score,
                            "passed":     passed,
                            "issues":     issues,
                            "suggestion": state.reflection_notes,
                        })
                    except Exception:
                        pass
            else:
                # LLM 没有返回合法 JSON，给一个中等分数继续
                state.reflection_score = 0.7
        except Exception as exc:
            _logger.warning("[reflect] LLM 评估失败，默认通过: %s", exc)
            state.reflection_score = 0.7

    # 决策：通过 or 重试
    if state.reflection_score >= REFLECTION_THRESHOLD:
        # 质量通过 → 回到 supervisor 决定下一步
        state.next_node = "supervisor"
        state.retry_count = 0
        _logger.info("[reflect] 质量通过 score=%.2f → supervisor", state.reflection_score)
    else:
        # 质量不通过 → 重试上一个实质性节点
        last_real_node = _find_last_real_node(state.visited)
        if state.retry_count < 2 and last_real_node:
            state.retry_count += 1
            state.next_node = last_real_node
            # 将反思建议注入消息，下一轮 LLM 能看到
            state.message = (
                f"{state.message}\n\n"
                f"[质量反思-第{state.retry_count}次重试] {state.reflection_notes}"
            )
            _logger.info(
                "[reflect] 质量不通过 score=%.2f，重试 %s（第%d次）",
                state.reflection_score, last_real_node, state.retry_count,
            )
        else:
            # 重试次数耗尽，强制通过
            _exhausted = state.retry_count  # 记录耗尽时的值，再重置
            state.retry_count = 0
            state.next_node = "supervisor"
            _logger.warning(
                "[reflect] 重试次数耗尽（retry_count=%d），强制通过",
                _exhausted,
            )

    return state


# _find_last_real_node 已从 supervisor_agent 导入，此处不再重复定义（CROSS-1 修复）
