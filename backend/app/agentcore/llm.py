"""
LLM 客户端 - 基于 OpenAI SDK
支持：普通完成 / 流式完成 / Tool Calling / 流式 Tool Calling

设计要点：
- 全局单例客户端（_client），httpx 连接池在进程生命周期内复用
- 配置变更时调用 reset_client() 重建客户端
- 所有调用均有超时保护，防止网络抖动永久阻塞
- complete_with_tools_stream：流式 Tool Calling，实时推送 reasoning/content，
  工具调用参数在 stream 结束后汇总返回，解决大 context 下超时问题

fix40 新增：
- M1 模型分层路由：model_tier 参数（'lite' / 'strong'）
  lite  → config.LLM_MODEL_LITE  （意图路由、TODO 规划等轻量调用，降低成本）
  strong→ config.LLM_MODEL        （ReAct 多轮、创作/编辑等复杂推理，保证质量）
  未配置 LLM_MODEL_LITE 时自动回退到 strong 模型，保持向后兼容
- M7 熔断增强：_call_with_retry() 封装重试逻辑（最多 2 次），
  TimeoutError / RateLimitError 自动重试，其余异常立即抛出
"""
from __future__ import annotations
import asyncio
import logging
from typing import AsyncIterator, Literal
from openai import AsyncOpenAI
from app.config import config

_logger = logging.getLogger("ep_agent.llm")

# ── 全局单例客户端（连接池复用） ───────────────────────────────────
_client: AsyncOpenAI | None = None

# LLM 调用超时（秒）：路由/规划用短超时，创作/ReAct 用长超时
_TIMEOUT_FAST   = 30   # 意图路由、TODO 规划等轻量调用
_TIMEOUT_NORMAL = 180  # 普通创作、工具调用（H5 生成含大 context 需要更长时间）
_TIMEOUT_STREAM = 240  # 流式输出（需更长时间）

# M7 熔断：最大重试次数（TimeoutError / RateLimitError 时自动重试）
_MAX_RETRIES = 2

# ── M1 模型分层路由 ────────────────────────────────────────────────
# model_tier 取值：
#   'lite'   → 轻量模型（意图路由、TODO 规划、简单查询），降低成本
#              硅基流动默认：deepseek-ai/DeepSeek-V4-Flash（¥1/¥2 per M tokens）
#   'strong' → 强力模型（ReAct 多轮、创作/编辑/H5 生成），保证质量
#              硅基流动默认：deepseek-ai/DeepSeek-V3.2（¥2/¥3 per M tokens）
# 未配置 LLM_MODEL_LITE 时自动回退到 LLM_MODEL（向后兼容）
# 切换模型只需在 .env 中设置 LLM_MODEL / LLM_MODEL_LITE，无需改代码
ModelTier = Literal["lite", "strong"]


def _resolve_model(tier: ModelTier = "strong") -> str:
    """根据 tier 返回实际模型名称。lite 回退逻辑：无配置则用 strong。"""
    if tier == "lite":
        lite = getattr(config, "LLM_MODEL_LITE", "") or ""
        return lite if lite else config.LLM_MODEL
    return config.LLM_MODEL


def get_llm_client() -> AsyncOpenAI:
    """返回全局单例客户端，首次调用时惰性初始化。"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=_TIMEOUT_NORMAL + 60,  # httpx 层超时兜底（比 asyncio 超时多 60s 余量）
        )
    return _client


def reset_client() -> None:
    """配置变更后调用，强制下次请求重建客户端。"""
    global _client
    _client = None


async def _call_with_retry(coro_factory, timeout: float, label: str = ""):
    """
    M7 熔断增强：带重试的 LLM 调用封装。
    TimeoutError / RateLimitError 自动重试最多 _MAX_RETRIES 次，
    其余异常（AuthenticationError / InvalidRequestError 等）立即抛出。

    coro_factory: 无参可调用，每次调用返回一个新 coroutine
    timeout: asyncio.wait_for 超时秒数
    label: 调用标签，用于日志
    """
    from app.agentcore.session_context import get_current_trace_id
    try:
        from openai import RateLimitError as _RateLimitError
    except ImportError:
        _RateLimitError = Exception  # type: ignore

    last_err = None
    for attempt in range(1, _MAX_RETRIES + 2):  # 1 次正常 + 最多 _MAX_RETRIES 次重试
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout)
        except asyncio.TimeoutError as e:
            last_err = e
            tid = get_current_trace_id()
            _logger.warning(
                "[trace=%s] %s 超时（第 %d/%d 次，timeout=%ds）",
                tid[:8] if tid else "?", label, attempt, _MAX_RETRIES + 1, timeout,
            )
            if attempt > _MAX_RETRIES:
                raise TimeoutError(
                    f"{label} 请求超时（>{timeout}s），已重试 {_MAX_RETRIES} 次，请检查网络或 API 服务状态"
                ) from e
            await asyncio.sleep(2 ** attempt)  # 指数退避：2s, 4s
        except _RateLimitError as e:
            last_err = e
            tid = get_current_trace_id()
            _logger.warning(
                "[trace=%s] %s 触发限流（第 %d/%d 次）: %s",
                tid[:8] if tid else "?", label, attempt, _MAX_RETRIES + 1, e,
            )
            if attempt > _MAX_RETRIES:
                raise
            await asyncio.sleep(5 * attempt)  # 限流退避：5s, 10s
        except Exception:
            raise  # 其余异常（鉴权失败、参数错误等）立即抛出，不重试
    raise last_err  # 理论上不会到这里


async def complete(
    messages: list[dict],
    temperature: float = 0.1,
    tier: ModelTier = "strong",
) -> str:
    """普通完成，返回文本，带超时保护 + M7 重试熔断。
    tier='lite' 使用轻量模型（意图路由、TODO 规划等），tier='strong'（默认）使用强力模型。
    """
    model = _resolve_model(tier)
    resp = await _call_with_retry(
        lambda: get_llm_client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        ),
        timeout=_TIMEOUT_NORMAL,
        label=f"complete[{tier}]",
    )
    return resp.choices[0].message.content or ""


async def complete_stream(
    messages: list[dict],
    temperature: float = 0.2,
    tier: ModelTier = "strong",
) -> AsyncIterator[str]:
    """流式完成，逐 token yield，带超时保护 + M7 重试熔断。"""
    model = _resolve_model(tier)
    stream = await _call_with_retry(
        lambda: get_llm_client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
        ),
        timeout=_TIMEOUT_STREAM,
        label=f"complete_stream[{tier}]",
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def complete_with_tools_stream(
    messages: list[dict],
    tools: list[dict],
    publish,
    temperature: float = 0.1,
) -> dict:
    """
    流式 Tool Calling：实时推送 reasoning/content token，工具调用参数在流结束后汇总。
    解决大 context（含 MIDI base64）下非流式调用超时的问题。

    返回格式与 complete_with_tools 一致：
      {content, tool_calls, finish_reason}
    """
    import json as _json

    model = _resolve_model("strong")  # Tool Calling 始终用 strong 模型
    stream = await _call_with_retry(
        lambda: get_llm_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            stream=True,
        ),
        timeout=_TIMEOUT_STREAM,
        label="complete_with_tools_stream",
    )

    content_parts: list[str] = []
    # tool_calls 累积：{index: {id, name, arguments_parts}}
    tc_accum: dict[int, dict] = {}
    finish_reason = "stop"
    # 流式 delta 实时过滤缓冲区：LLM 偶发混入 tool_call JSON 残片（如 `}`）
    # 用滑动窗口检测 `<tool_call>` 起始标记，一旦出现则停止推送后续 delta
    _delta_buf = ""          # 最近若干字符的滑动缓冲，用于跨 chunk 检测残片
    _in_tool_fragment = False  # 是否已进入 tool_call 残片区域（停止推送直到下一轮）
    _BUF_MAX = 16              # 缓冲区最大长度（足够检测 `<tool_call>` 前缀）

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue

        finish_reason = choice.finish_reason or finish_reason
        delta = choice.delta

        # 推送 reasoning content（思考链，部分模型支持）
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            await publish("message.delta", {"delta": "", "reasoning_delta": reasoning})

        # 推送普通 content（实时过滤 tool_call 残片）
        if delta.content:
            content_parts.append(delta.content)
            raw = delta.content

            # 若已进入残片区域，跳过推送（等待下一个 chunk 判断是否恢复）
            if _in_tool_fragment:
                # 残片区域：只要 delta 里没有正常文字（仅有 `}` 或空白），继续跳过
                import re as _re
                if _re.fullmatch(r'[\s\}]*', raw):
                    continue
                else:
                    # 出现了正常文字，退出残片区域
                    _in_tool_fragment = False

            # 检测是否是孤立的 `}` 残片（最常见的脏 delta）
            import re as _re
            if _re.fullmatch(r'[\s\}]+', raw):
                # 纯 `}` / 空白：先缓冲，不立即推送
                _delta_buf += raw
                if len(_delta_buf) > _BUF_MAX:
                    _delta_buf = _delta_buf[-_BUF_MAX:]
                # 判断是否是 tool_call JSON 尾部残片：
                # 若缓冲区全是 `}` / 空白 且 tc_accum 非空（说明有工具调用在流中），则标记为残片
                if tc_accum and _re.fullmatch(r'[\s\}]+', _delta_buf):
                    _in_tool_fragment = True
                    continue
                # 否则（tc_accum 为空，说明是正常文字的 `}`，如 JSON 输出），正常推送
                await publish("message.delta", {"delta": raw})
            elif "<tool_call>" in raw or "</tool_call>" in raw:
                # 明确的 tool_call XML 标记，直接跳过
                _in_tool_fragment = True
                continue
            else:
                # 正常文字 delta，重置缓冲区并推送
                _delta_buf = raw[-_BUF_MAX:]
                _in_tool_fragment = False
                await publish("message.delta", {"delta": raw})

        # 累积 tool_calls（流式下分片到达）
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tc_accum:
                    tc_accum[idx] = {
                        "id": tc_delta.id or "",
                        "name": "",
                        "arguments_parts": [],
                    }
                if tc_delta.id:
                    tc_accum[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc_accum[idx]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc_accum[idx]["arguments_parts"].append(tc_delta.function.arguments)

    # 汇总 tool_calls
    tool_calls = [
        {
            "id": v["id"],
            "type": "function",
            "function": {
                "name": v["name"],
                "arguments": "".join(v["arguments_parts"]),
            },
        }
        for v in tc_accum.values()
    ]

    return {
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
    }


async def complete_with_tools(
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.1,
) -> dict:
    """
    Tool Calling 完成，带超时保护。
    返回包含以下字段的 dict：
      - content: str | None
      - tool_calls: list[dict]  每项包含 id / function.name / function.arguments
      - finish_reason: "tool_calls" | "stop" | ...
    """
    model = _resolve_model("strong")  # Tool Calling 始终用 strong 模型
    resp = await _call_with_retry(
        lambda: get_llm_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
        ),
        timeout=_TIMEOUT_NORMAL,
        label="complete_with_tools",
    )
    msg = resp.choices[0].message
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
            }
            for tc in (msg.tool_calls or [])
        ],
        "finish_reason": resp.choices[0].finish_reason,
    }
