"""
LLM 客户端 - 基于 OpenAI SDK
支持：普通完成 / 流式完成 / Tool Calling / 流式 Tool Calling

设计要点：
- 全局单例客户端（_client），httpx 连接池在进程生命周期内复用
- 配置变更时调用 reset_client() 重建客户端
- 所有调用均有超时保护，防止网络抖动永久阻塞
- complete_with_tools_stream：流式 Tool Calling，实时推送 reasoning/content，
  工具调用参数在 stream 结束后汇总返回，解决大 context 下超时问题
"""
from __future__ import annotations
import asyncio
from typing import AsyncIterator
from openai import AsyncOpenAI
from app.config import config

# ── 全局单例客户端（连接池复用） ───────────────────────────────────
_client: AsyncOpenAI | None = None

# LLM 调用超时（秒）：路由/规划用短超时，创作/ReAct 用长超时
_TIMEOUT_FAST   = 30   # 意图路由、TODO 规划等轻量调用
_TIMEOUT_NORMAL = 180  # 普通创作、工具调用（H5 生成含大 context 需要更长时间）
_TIMEOUT_STREAM = 240  # 流式输出（需更长时间）


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


async def complete(messages: list[dict], temperature: float = 0.1) -> str:
    """普通完成，返回文本，带超时保护"""
    try:
        resp = await asyncio.wait_for(
            get_llm_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                temperature=temperature,
            ),
            timeout=_TIMEOUT_NORMAL,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"LLM 请求超时（>{_TIMEOUT_NORMAL}s），请检查网络或 API 服务状态")
    return resp.choices[0].message.content or ""


async def complete_stream(
    messages: list[dict],
    temperature: float = 0.2,
) -> AsyncIterator[str]:
    """流式完成，逐 token yield，带超时保护"""
    try:
        stream = await asyncio.wait_for(
            get_llm_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                temperature=temperature,
                stream=True,
            ),
            timeout=_TIMEOUT_STREAM,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"LLM 流式请求超时（>{_TIMEOUT_STREAM}s）")
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

    try:
        stream = await asyncio.wait_for(
            get_llm_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                stream=True,
            ),
            timeout=_TIMEOUT_STREAM,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"LLM 流式 Tool Calling 请求超时（>{_TIMEOUT_STREAM}s）")

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
    try:
        resp = await asyncio.wait_for(
            get_llm_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
            ),
            timeout=_TIMEOUT_NORMAL,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"LLM Tool Calling 请求超时（>{_TIMEOUT_NORMAL}s）")
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
