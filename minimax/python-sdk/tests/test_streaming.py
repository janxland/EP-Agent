from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from minimax_api import AsyncMiniMax, Config, MiniMaxAPIError
from minimax_api.streaming import parse_json_line, parse_sse_lines, tts_chunk_from_event


def test_sse_parser_handles_comments_done_and_multiple_events() -> None:
    lines = [
        ": ping",
        'data: {"id":"one","base_resp":{"status_code":0}}',
        "",
        'data: {"id":"two"}',
        "",
        "data: [DONE]",
        "",
    ]
    assert [item["id"] for item in parse_sse_lines(lines)] == ["one", "two"]


def test_tts_stream_hex_audio_is_decoded() -> None:
    event = {
        "data": {"audio": "0001ff", "status": 2},
        "trace_id": "trace-1",
        "base_resp": {"status_code": 0},
    }
    chunk = tts_chunk_from_event(event)
    assert chunk.audio == b"\x00\x01\xff"
    assert chunk.is_final is True
    assert chunk.trace_id == "trace-1"


def test_stream_base_resp_error() -> None:
    with pytest.raises(MiniMaxAPIError) as captured:
        parse_json_line('data: {"base_resp":{"status_code":9001,"status_msg":"denied"}}')
    assert captured.value.api_status_code == 9001


def test_async_chat_stream_returns_async_iterator() -> None:
    async def run() -> None:
        body = (
            'data: {"id":"c1","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            'data: {"id":"c2","choices":[{"delta":{"content":"!"}}]}\n\n'
            "data: [DONE]\n\n"
        ).encode()

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["stream"] is True
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
                request=request,
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sdk = AsyncMiniMax(Config(api_key="test-key"), http_client=http_client)
        items = []
        async for chunk in sdk.text.chat_completions_stream(
            model="MiniMax-M3", messages=[{"role": "user", "content": "Hello"}]
        ):
            items.append(chunk)
        assert [item["id"] for item in items] == ["c1", "c2"]
        await http_client.aclose()

    asyncio.run(run())
