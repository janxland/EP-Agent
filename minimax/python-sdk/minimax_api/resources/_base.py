"""Internal resource helpers."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional, Set

from ..errors import MiniMaxAPIError, MiniMaxValidationError
from ..models import GenerationResult


def compact_payload(values: Mapping[str, Any], extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Drop ``None`` values and merge explicitly supplied forward-compatible fields."""

    payload = {key: value for key, value in values.items() if value is not None}
    if extra:
        payload.update(extra)
    return payload


def normalized(payload: Dict[str, Any]) -> GenerationResult:
    return GenerationResult.from_response(payload)


def task_status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or "").strip().lower()


def poll_sync(
    query: Callable[[str], Dict[str, Any]],
    task_id: str,
    *,
    interval: float,
    timeout: float,
    success: Iterable[str],
    failure: Iterable[str],
) -> Dict[str, Any]:
    _validate_poll(interval, timeout)
    success_set = {item.lower() for item in success}
    failure_set = {item.lower() for item in failure}
    deadline = time.monotonic() + timeout
    while True:
        result = query(task_id)
        status = task_status(result)
        if status in success_set:
            return result
        if status in failure_set:
            raise MiniMaxAPIError(
                f"MiniMax task {task_id} ended with status {result.get('status')}",
                response=result,
                request_id=result.get("_request_id"),
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"MiniMax task {task_id} did not finish within {timeout}s")
        time.sleep(interval)


async def poll_async(
    query: Callable[[str], Awaitable[Dict[str, Any]]],
    task_id: str,
    *,
    interval: float,
    timeout: float,
    success: Iterable[str],
    failure: Iterable[str],
) -> Dict[str, Any]:
    _validate_poll(interval, timeout)
    success_set: Set[str] = {item.lower() for item in success}
    failure_set: Set[str] = {item.lower() for item in failure}
    deadline = time.monotonic() + timeout
    while True:
        result = await query(task_id)
        status = task_status(result)
        if status in success_set:
            return result
        if status in failure_set:
            raise MiniMaxAPIError(
                f"MiniMax task {task_id} ended with status {result.get('status')}",
                response=result,
                request_id=result.get("_request_id"),
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"MiniMax task {task_id} did not finish within {timeout}s")
        await asyncio.sleep(interval)


def _validate_poll(interval: float, timeout: float) -> None:
    if interval <= 0:
        raise MiniMaxValidationError("poll interval must be > 0")
    if timeout <= 0:
        raise MiniMaxValidationError("poll timeout must be > 0")
