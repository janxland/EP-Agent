"""OpenAI- and Anthropic-compatible text generation resources."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, Mapping, Optional, Sequence

from ..streaming import parse_async_sse_lines, parse_sse_lines
from ..transport import AsyncTransport, Transport
from ._base import compact_payload


class TextResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def chat_completions(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        tool_choice: Any = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = _openai_payload(
            model, messages, False, temperature, top_p, max_tokens, tools, tool_choice, extra_body
        )
        return self._transport.request("POST", "/chat/completions", json=payload)

    def chat_completions_stream(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        tool_choice: Any = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        payload = _openai_payload(
            model, messages, True, temperature, top_p, max_tokens, tools, tool_choice, extra_body
        )
        with self._transport.stream("POST", "/chat/completions", json=payload) as response:
            yield from parse_sse_lines(response.iter_lines())

    def anthropic_messages(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int,
        system: Any = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        tool_choice: Any = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = _anthropic_payload(
            model, messages, max_tokens, False, system, temperature, top_p, tools, tool_choice, extra_body
        )
        return self._transport.request(
            "POST", _anthropic_url(self._transport), json=payload, headers=_anthropic_headers()
        )

    def anthropic_messages_stream(self, **kwargs: Any) -> Iterator[Dict[str, Any]]:
        payload = _anthropic_payload(stream=True, **kwargs)
        with self._transport.stream(
            "POST", _anthropic_url(self._transport), json=payload, headers=_anthropic_headers()
        ) as response:
            yield from parse_sse_lines(response.iter_lines())


class AsyncTextResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def chat_completions(self, **kwargs: Any) -> Dict[str, Any]:
        payload = _openai_payload(stream=False, **kwargs)
        return await self._transport.request("POST", "/chat/completions", json=payload)

    async def chat_completions_stream(self, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        payload = _openai_payload(stream=True, **kwargs)
        async with self._transport.stream("POST", "/chat/completions", json=payload) as response:
            async for event in parse_async_sse_lines(response.aiter_lines()):
                yield event

    async def anthropic_messages(self, **kwargs: Any) -> Dict[str, Any]:
        payload = _anthropic_payload(stream=False, **kwargs)
        return await self._transport.request(
            "POST", _anthropic_url(self._transport), json=payload, headers=_anthropic_headers()
        )

    async def anthropic_messages_stream(self, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        payload = _anthropic_payload(stream=True, **kwargs)
        async with self._transport.stream(
            "POST", _anthropic_url(self._transport), json=payload, headers=_anthropic_headers()
        ) as response:
            async for event in parse_async_sse_lines(response.aiter_lines()):
                yield event


def _openai_payload(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    stream: bool,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[Sequence[Mapping[str, Any]]] = None,
    tool_choice: Any = None,
    extra_body: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return compact_payload(
        {
            "model": model,
            "messages": list(messages),
            "stream": stream,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "tools": list(tools) if tools is not None else None,
            "tool_choice": tool_choice,
        },
        extra_body,
    )


def _anthropic_payload(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    max_tokens: int,
    stream: bool,
    system: Any = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    tools: Optional[Sequence[Mapping[str, Any]]] = None,
    tool_choice: Any = None,
    extra_body: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return compact_payload(
        {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": stream,
            "system": system,
            "temperature": temperature,
            "top_p": top_p,
            "tools": list(tools) if tools is not None else None,
            "tool_choice": tool_choice,
        },
        extra_body,
    )


def _anthropic_url(transport: Any) -> str:
    return f"{transport.config.api_root}/anthropic/v1/messages"


def _anthropic_headers() -> Dict[str, str]:
    return {"anthropic-version": "2023-06-01", "content-type": "application/json"}
