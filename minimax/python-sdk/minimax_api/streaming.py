"""SSE and newline-delimited JSON parsers."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Iterable, Iterator, Optional

from .errors import MiniMaxAPIError
from .models import TTSChunk


def parse_sse_lines(lines: Iterable[str]) -> Iterator[Dict[str, Any]]:
    """Parse SSE lines into JSON dictionaries, ignoring comments and metadata."""

    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                event = _decode_sse_data("\n".join(data_lines))
                data_lines.clear()
                if event is not None:
                    yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        event = _decode_sse_data("\n".join(data_lines))
        if event is not None:
            yield event


def parse_json_line(line: str) -> Optional[Dict[str, Any]]:
    """Decode an SSE data line or a plain NDJSON line."""

    value = line.strip()
    if not value or value.startswith(":"):
        return None
    if value.startswith("data:"):
        value = value[5:].lstrip()
    if value == "[DONE]":
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MiniMaxAPIError(f"Invalid streaming JSON frame: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise MiniMaxAPIError("Streaming frame must decode to a JSON object")
    _raise_for_base_resp(decoded)
    return decoded


def tts_chunk_from_event(event: Dict[str, Any]) -> TTSChunk:
    """Normalize a MiniMax T2A JSON event and decode hexadecimal audio."""

    _raise_for_base_resp(event)
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    audio_value = data.get("audio") or event.get("audio") or ""
    audio = b""
    if isinstance(audio_value, str) and audio_value:
        try:
            audio = bytes.fromhex(audio_value)
        except ValueError as exc:
            raise MiniMaxAPIError("TTS stream contained non-hex audio data") from exc
    status = data.get("status", event.get("status"))
    return TTSChunk(
        audio=audio,
        raw=event,
        status=status if isinstance(status, int) else None,
        trace_id=_optional_str(event.get("trace_id")),
        is_final=status == 2 or event.get("event") in {"task_finished", "finished"},
    )


async def parse_async_sse_lines(lines: AsyncIterator[str]) -> AsyncIterator[Dict[str, Any]]:
    """Incrementally parse SSE from an asynchronous line iterator."""

    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                event = _decode_sse_data("\n".join(data_lines))
                data_lines.clear()
                if event is not None:
                    yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        event = _decode_sse_data("\n".join(data_lines))
        if event is not None:
            yield event


def _decode_sse_data(value: str) -> Optional[Dict[str, Any]]:
    if value.strip() == "[DONE]":
        return None
    return parse_json_line(value)


def _raise_for_base_resp(payload: Dict[str, Any]) -> None:
    base = payload.get("base_resp")
    if isinstance(base, dict):
        code = base.get("status_code", 0)
        if code not in (None, 0, "0"):
            raise MiniMaxAPIError(
                str(base.get("status_msg") or "MiniMax streaming API error"),
                api_status_code=_to_int(code),
                response=payload,
            )


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)
