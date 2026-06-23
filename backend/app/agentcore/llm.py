"""
LLM 客户端 - 基于 OpenAI SDK
支持：普通完成 / 流式完成 / Tool Calling

设计要点：
- 全局单例客户端（_client），httpx 连接池在进程生命周期内复用
- 配置变更时调用 reset_client() 重建客户端
"""
from __future__ import annotations
from typing import AsyncIterator
from openai import AsyncOpenAI
from app.config import config

# ── 全局单例客户端（连接池复用） ───────────────────────────────────
_client: AsyncOpenAI | None = None


def get_llm_client() -> AsyncOpenAI:
    """返回全局单例客户端，首次调用时惰性初始化。"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
        )
    return _client


def reset_client() -> None:
    """配置变更后调用，强制下次请求重建客户端。"""
    global _client
    _client = None


async def complete(messages: list[dict], temperature: float = 0.1) -> str:
    """普通完成，返回文本"""
    resp = await get_llm_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def complete_stream(
    messages: list[dict],
    temperature: float = 0.2,
) -> AsyncIterator[str]:
    """流式完成，逐 token yield"""
    stream = await get_llm_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def complete_with_tools(
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.1,
) -> dict:
    """
    Tool Calling 完成。
    返回包含以下字段的 dict：
      - content: str | None
      - tool_calls: list[dict]  每项包含 id / function.name / function.arguments
      - finish_reason: "tool_calls" | "stop" | ...
    """
    resp = await get_llm_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=temperature,
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
