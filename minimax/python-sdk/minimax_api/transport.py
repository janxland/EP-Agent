"""Shared httpx transport, retry policy, error parsing, and safe diagnostics."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Iterator, Mapping, Optional

import httpx

from .config import Config
from .errors import MiniMaxAPIError, MiniMaxTransportError

logger = logging.getLogger("minimax_api")
_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "audio",
    "audio_base64",
    "file",
}


class Transport:
    """Synchronous shared ``httpx.Client`` wrapper."""

    def __init__(self, config: Config, client: Optional[httpx.Client] = None) -> None:
        self.config = config
        self._owns_client = client is None
        headers = _default_headers(config)
        self.client = client or httpx.Client(timeout=config.timeout, headers=headers)
        if client is not None:
            _apply_missing_headers(self.client.headers, headers)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        response = self.request_raw(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=headers,
        )
        return _decode_json_response(response)

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        request_id = _request_id(headers)
        request_headers = _merge_headers(headers, request_id)
        url = _resolve_url(self.config, path)
        _log_request(method, url, request_id, json if json is not None else data)
        for attempt in range(self.config.retry.max_retries + 1):
            try:
                response = self.client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    files=files,
                    headers=request_headers,
                )
            except httpx.TransportError as exc:
                if attempt >= self.config.retry.max_retries:
                    raise MiniMaxTransportError(
                        f"MiniMax request failed after {attempt + 1} attempt(s): {exc}",
                        request_id=request_id,
                    ) from exc
                time.sleep(_retry_delay(self.config, attempt, None))
                continue
            if _should_retry(self.config, response.status_code, attempt):
                delay = _retry_delay(self.config, attempt, response.headers.get("Retry-After"))
                response.close()
                time.sleep(delay)
                continue
            _raise_for_http_response(response, request_id=request_id)
            return response
        raise AssertionError("unreachable")

    @contextmanager
    def stream(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Iterator[httpx.Response]:
        request_id = _request_id(headers)
        request_headers = _merge_headers(headers, request_id)
        url = _resolve_url(self.config, path)
        _log_request(method, url, request_id, json)
        for attempt in range(self.config.retry.max_retries + 1):
            try:
                with self.client.stream(
                    method, url, params=params, json=json, headers=request_headers
                ) as response:
                    if _should_retry(self.config, response.status_code, attempt):
                        response.read()
                        time.sleep(
                            _retry_delay(
                                self.config, attempt, response.headers.get("Retry-After")
                            )
                        )
                        continue
                    _raise_for_http_response(response, request_id=request_id, read_stream=True)
                    yield response
                    return
            except httpx.TransportError as exc:
                if attempt >= self.config.retry.max_retries:
                    raise MiniMaxTransportError(
                        f"MiniMax stream failed after {attempt + 1} attempt(s): {exc}",
                        request_id=request_id,
                    ) from exc
                time.sleep(_retry_delay(self.config, attempt, None))
        raise AssertionError("unreachable")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class AsyncTransport:
    """Asynchronous shared ``httpx.AsyncClient`` wrapper."""

    def __init__(self, config: Config, client: Optional[httpx.AsyncClient] = None) -> None:
        self.config = config
        self._owns_client = client is None
        headers = _default_headers(config)
        self.client = client or httpx.AsyncClient(timeout=config.timeout, headers=headers)
        if client is not None:
            _apply_missing_headers(self.client.headers, headers)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        response = await self.request_raw(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=headers,
        )
        return _decode_json_response(response)

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        request_id = _request_id(headers)
        request_headers = _merge_headers(headers, request_id)
        url = _resolve_url(self.config, path)
        _log_request(method, url, request_id, json if json is not None else data)
        for attempt in range(self.config.retry.max_retries + 1):
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    files=files,
                    headers=request_headers,
                )
            except httpx.TransportError as exc:
                if attempt >= self.config.retry.max_retries:
                    raise MiniMaxTransportError(
                        f"MiniMax request failed after {attempt + 1} attempt(s): {exc}",
                        request_id=request_id,
                    ) from exc
                await asyncio.sleep(_retry_delay(self.config, attempt, None))
                continue
            if _should_retry(self.config, response.status_code, attempt):
                delay = _retry_delay(self.config, attempt, response.headers.get("Retry-After"))
                await response.aclose()
                await asyncio.sleep(delay)
                continue
            await _raise_for_async_http_response(response, request_id=request_id)
            return response
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> AsyncIterator[httpx.Response]:
        request_id = _request_id(headers)
        request_headers = _merge_headers(headers, request_id)
        url = _resolve_url(self.config, path)
        _log_request(method, url, request_id, json)
        for attempt in range(self.config.retry.max_retries + 1):
            try:
                async with self.client.stream(
                    method, url, params=params, json=json, headers=request_headers
                ) as response:
                    if _should_retry(self.config, response.status_code, attempt):
                        await response.aread()
                        await asyncio.sleep(
                            _retry_delay(
                                self.config, attempt, response.headers.get("Retry-After")
                            )
                        )
                        continue
                    await _raise_for_async_http_response(response, request_id=request_id)
                    yield response
                    return
            except httpx.TransportError as exc:
                if attempt >= self.config.retry.max_retries:
                    raise MiniMaxTransportError(
                        f"MiniMax stream failed after {attempt + 1} attempt(s): {exc}",
                        request_id=request_id,
                    ) from exc
                await asyncio.sleep(_retry_delay(self.config, attempt, None))
        raise AssertionError("unreachable")

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _apply_missing_headers(target: httpx.Headers, defaults: Mapping[str, str]) -> None:
    for key, value in defaults.items():
        if key not in target:
            target[key] = value


def _default_headers(config: Config) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Accept": "application/json",
        "User-Agent": "minimax-api-python/0.1.0",
    }
    headers.update(config.default_headers)
    return headers


def _resolve_url(config: Config, path: str) -> str:
    if path.startswith(("https://", "http://")):
        return path
    assert config.base_url is not None
    return f"{config.base_url}/{path.lstrip('/')}"


def _request_id(headers: Optional[Mapping[str, str]]) -> str:
    for key, value in (headers or {}).items():
        if key.lower() in {"x-request-id", "request-id"}:
            return value
    return str(uuid.uuid4())


def _merge_headers(headers: Optional[Mapping[str, str]], request_id: str) -> Dict[str, str]:
    merged = dict(headers or {})
    if not any(key.lower() == "x-request-id" for key in merged):
        merged["X-Request-ID"] = request_id
    return merged


def _should_retry(config: Config, status_code: int, attempt: int) -> bool:
    return attempt < config.retry.max_retries and status_code in config.retry.retry_statuses


def _retry_delay(config: Config, attempt: int, retry_after: Optional[str]) -> float:
    parsed = _parse_retry_after(retry_after)
    if parsed is not None:
        return min(parsed, config.retry.max_delay)
    base = min(
        config.retry.initial_delay * (config.retry.backoff_factor**attempt),
        config.retry.max_delay,
    )
    return base * random.uniform(0.8, 1.2) if base else 0.0


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _decode_json_response(response: httpx.Response) -> Dict[str, Any]:
    request_id = _response_request_id(response)
    _raise_for_http_response(response, request_id=request_id)
    try:
        payload = response.json()
    except ValueError as exc:
        raise MiniMaxAPIError(
            "MiniMax returned a non-JSON response",
            status_code=response.status_code,
            request_id=request_id,
        ) from exc
    if not isinstance(payload, dict):
        raise MiniMaxAPIError(
            "MiniMax JSON response must be an object",
            status_code=response.status_code,
            request_id=request_id,
        )
    _raise_for_base_resp(payload, response.status_code, request_id)
    payload.setdefault("_request_id", request_id)
    return payload


def _raise_for_http_response(
    response: httpx.Response, *, request_id: str, read_stream: bool = False
) -> None:
    if response.status_code < 400:
        return
    if read_stream and not response.is_closed:
        response.read()
    payload = _safe_json(response)
    message, api_code = _error_details(payload, response.reason_phrase)
    raise MiniMaxAPIError(
        message,
        status_code=response.status_code,
        api_status_code=api_code,
        request_id=request_id,
        response=payload,
    )


async def _raise_for_async_http_response(response: httpx.Response, *, request_id: str) -> None:
    if response.status_code < 400:
        return
    await response.aread()
    payload = _safe_json(response)
    message, api_code = _error_details(payload, response.reason_phrase)
    raise MiniMaxAPIError(
        message,
        status_code=response.status_code,
        api_status_code=api_code,
        request_id=request_id,
        response=payload,
    )


def _raise_for_base_resp(
    payload: Dict[str, Any], http_status: int, request_id: Optional[str]
) -> None:
    base = payload.get("base_resp")
    if not isinstance(base, dict):
        return
    code = base.get("status_code", 0)
    if code in (None, 0, "0"):
        return
    raise MiniMaxAPIError(
        str(base.get("status_msg") or "MiniMax API error"),
        status_code=http_status,
        api_status_code=_to_int(code),
        request_id=request_id,
        response=payload,
    )


def _safe_json(response: httpx.Response) -> Optional[Dict[str, Any]]:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _error_details(
    payload: Optional[Dict[str, Any]], fallback: str
) -> tuple[str, Optional[int]]:
    if payload:
        base = payload.get("base_resp")
        if isinstance(base, dict):
            return str(base.get("status_msg") or fallback), _to_int(base.get("status_code"))
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or fallback), _to_int(error.get("code"))
        if isinstance(error, str):
            return error, None
        if payload.get("message"):
            return str(payload["message"]), None
    return fallback or "MiniMax HTTP error", None


def _response_request_id(response: httpx.Response) -> str:
    return (
        response.headers.get("x-request-id")
        or response.headers.get("request-id")
        or response.headers.get("trace-id")
        or response.request.headers.get("x-request-id")
        or str(uuid.uuid4())
    )


def _log_request(method: str, url: str, request_id: str, payload: Any) -> None:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "MiniMax request method=%s url=%s request_id=%s payload=%r",
            method.upper(),
            url.split("?", 1)[0],
            request_id,
            _redact(payload),
        )


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in _SECRET_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
