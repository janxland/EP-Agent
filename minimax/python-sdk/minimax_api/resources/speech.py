"""Synchronous/streaming T2A, long-text tasks, and WebSocket client."""

from __future__ import annotations

import inspect
import json
from typing import Any, AsyncIterator, Dict, Iterator, Mapping, Optional

from ..errors import MiniMaxAPIError, MiniMaxValidationError
from ..models import GenerationResult, TTSChunk
from ..streaming import parse_json_line, tts_chunk_from_event
from ..transport import AsyncTransport, Transport
from ._base import compact_payload, normalized, poll_async, poll_sync


class SpeechResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def t2a(
        self,
        *,
        model: str,
        text: str,
        voice_setting: Optional[Mapping[str, Any]] = None,
        audio_setting: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        payload = _t2a_payload(
            model=model,
            text=text,
            stream=False,
            voice_setting=voice_setting,
            audio_setting=audio_setting,
            timestamp=timestamp,
            extra=extra,
            **kwargs,
        )
        return normalized(self._transport.request("POST", "/t2a_v2", json=payload))

    def t2a_stream(self, **kwargs: Any) -> Iterator[TTSChunk]:
        payload = _t2a_payload(stream=True, **kwargs)
        with self._transport.stream("POST", "/t2a_v2", json=payload) as response:
            for line in response.iter_lines():
                event = parse_json_line(line)
                if event is not None:
                    yield tts_chunk_from_event(event)

    def create_long_text(
        self,
        *,
        model: str,
        voice_setting: Mapping[str, Any],
        text: Optional[str] = None,
        text_file_id: Optional[str | int] = None,
        audio_setting: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        payload = _long_text_payload(
            model=model,
            voice_setting=voice_setting,
            text=text,
            text_file_id=text_file_id,
            audio_setting=audio_setting,
            timestamp=timestamp,
            extra=extra,
            **kwargs,
        )
        return normalized(self._transport.request("POST", "/t2a_async_v2", json=payload))

    def query_long_text(self, task_id: str | int) -> Dict[str, Any]:
        return self._transport.request(
            "GET", "/query/t2a_async_query_v2", params={"task_id": task_id}
        )

    def poll_long_text(
        self, task_id: str | int, *, interval: float = 2.0, timeout: float = 900.0
    ) -> Dict[str, Any]:
        return poll_sync(
            lambda value: self.query_long_text(value),
            str(task_id),
            interval=interval,
            timeout=timeout,
            success={"success"},
            failure={"failed", "expired", "fail"},
        )

    def download_long_text(self, file_id: str | int) -> bytes:
        """Download completed long-text T2A content through the Files API."""

        response = self._transport.request_raw(
            "GET", "/files/retrieve_content", params={"file_id": file_id}
        )
        return response.content

    def websocket(self, *, url: Optional[str] = None) -> "TTSWebSocketClient":
        return TTSWebSocketClient(self._transport.config, url=url)


class AsyncSpeechResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def t2a(self, **kwargs: Any) -> GenerationResult:
        payload = _t2a_payload(stream=False, **kwargs)
        return normalized(await self._transport.request("POST", "/t2a_v2", json=payload))

    async def t2a_stream(self, **kwargs: Any) -> AsyncIterator[TTSChunk]:
        payload = _t2a_payload(stream=True, **kwargs)
        async with self._transport.stream("POST", "/t2a_v2", json=payload) as response:
            async for line in response.aiter_lines():
                event = parse_json_line(line)
                if event is not None:
                    yield tts_chunk_from_event(event)

    async def create_long_text(self, **kwargs: Any) -> GenerationResult:
        payload = _long_text_payload(**kwargs)
        return normalized(await self._transport.request("POST", "/t2a_async_v2", json=payload))

    async def query_long_text(self, task_id: str | int) -> Dict[str, Any]:
        return await self._transport.request(
            "GET", "/query/t2a_async_query_v2", params={"task_id": task_id}
        )

    async def poll_long_text(
        self, task_id: str | int, *, interval: float = 2.0, timeout: float = 900.0
    ) -> Dict[str, Any]:
        return await poll_async(
            lambda value: self.query_long_text(value),
            str(task_id),
            interval=interval,
            timeout=timeout,
            success={"success"},
            failure={"failed", "expired", "fail"},
        )

    async def download_long_text(self, file_id: str | int) -> bytes:
        """Download completed long-text T2A content through the Files API."""

        response = await self._transport.request_raw(
            "GET", "/files/retrieve_content", params={"file_id": file_id}
        )
        return response.content

    def websocket(self, *, url: Optional[str] = None) -> "TTSWebSocketClient":
        return TTSWebSocketClient(self._transport.config, url=url)


class TTSWebSocketClient:
    """Client-only WebSocket interface for official ``/ws/v1/t2a_v2``.

    The event payload is intentionally generic because MiniMax evolves fields
    independently by region/account. Callers can send documented task events
    verbatim and consume raw dictionaries.
    """

    def __init__(self, config: Any, *, url: Optional[str] = None) -> None:
        self.config = config
        self.url = url or config.websocket_url
        self._socket: Any = None

    async def connect(self, **kwargs: Any) -> "TTSWebSocketClient":
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "WebSocket support requires `pip install minimax-api[websocket]`"
            ) from exc
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        signature = inspect.signature(websockets.connect)
        header_name = "additional_headers" if "additional_headers" in signature.parameters else "extra_headers"
        kwargs.setdefault(header_name, headers)
        self._socket = await websockets.connect(self.url, **kwargs)
        return self

    async def send_event(self, event: Mapping[str, Any]) -> None:
        self._require_connection()
        await self._socket.send(json.dumps(dict(event), ensure_ascii=False))

    async def receive_event(self) -> Dict[str, Any]:
        self._require_connection()
        frame = await self._socket.recv()
        if isinstance(frame, bytes):
            frame = frame.decode("utf-8")
        try:
            value = json.loads(frame)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MiniMaxAPIError("Invalid JSON WebSocket frame") from exc
        if not isinstance(value, dict):
            raise MiniMaxAPIError("WebSocket frame must be a JSON object")
        base = value.get("base_resp")
        if isinstance(base, dict) and base.get("status_code") not in (None, 0, "0"):
            raise MiniMaxAPIError(
                str(base.get("status_msg") or "MiniMax WebSocket error"),
                api_status_code=_optional_int(base.get("status_code")),
                response=value,
            )
        if value.get("event") == "task_failed":
            raise MiniMaxAPIError(str(value.get("message") or "MiniMax TTS task failed"), response=value)
        return value

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        while True:
            event = await self.receive_event()
            yield event
            if event.get("event") in {"task_finished", "task_failed"}:
                return

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None

    async def __aenter__(self) -> "TTSWebSocketClient":
        return await self.connect()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    def _require_connection(self) -> None:
        if self._socket is None:
            raise RuntimeError("WebSocket is not connected")


def _t2a_payload(
    *,
    model: str,
    text: str,
    stream: bool,
    voice_setting: Optional[Mapping[str, Any]] = None,
    audio_setting: Optional[Mapping[str, Any]] = None,
    timestamp: Optional[Mapping[str, Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    if not text:
        raise MiniMaxValidationError("text must not be empty")
    timestamp_fields = dict(timestamp or {})
    payload = compact_payload(
        {
            "model": model,
            "text": text,
            "stream": stream,
            "voice_setting": dict(voice_setting) if voice_setting else None,
            "audio_setting": dict(audio_setting) if audio_setting else None,
            **timestamp_fields,
            **kwargs,
        },
        extra,
    )
    return payload


def _long_text_payload(
    *,
    model: str,
    voice_setting: Mapping[str, Any],
    text: Optional[str] = None,
    text_file_id: Optional[str | int] = None,
    audio_setting: Optional[Mapping[str, Any]] = None,
    timestamp: Optional[Mapping[str, Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    if (text is None) == (text_file_id is None):
        raise MiniMaxValidationError("provide exactly one of text or text_file_id")
    return compact_payload(
        {
            "model": model,
            "voice_setting": dict(voice_setting),
            "text": text,
            "text_file_id": text_file_id,
            "audio_setting": dict(audio_setting) if audio_setting else None,
            **dict(timestamp or {}),
            **kwargs,
        },
        extra,
    )


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
