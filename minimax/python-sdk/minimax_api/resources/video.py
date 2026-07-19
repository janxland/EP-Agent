"""Video tasks, deprecated video agent wrappers, polling, and explicit cancellation."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..models import GenerationResult
from ..transport import AsyncTransport, Transport
from ._base import compact_payload, normalized, poll_async, poll_sync

_SUCCESS = {"success"}
_FAILURE = {"fail", "failed", "expired", "cancelled", "canceled"}


class VideoResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def text_to_video(
        self, *, model: str, prompt: str, extra: Optional[Mapping[str, Any]] = None, **kwargs: Any
    ) -> GenerationResult:
        return self._create(model=model, prompt=prompt, extra=extra, **kwargs)

    def image_to_video(
        self,
        *,
        model: str,
        first_frame_image: str,
        prompt: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        return self._create(
            model=model,
            first_frame_image=first_frame_image,
            prompt=prompt,
            extra=extra,
            **kwargs,
        )

    def video_agent(
        self,
        *,
        template_id: str,
        text_inputs: list[Mapping[str, Any]],
        media_inputs: list[Mapping[str, Any]],
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        """Call the official but deprecated video template/agent endpoint."""

        payload = compact_payload(
            {
                "template_id": template_id,
                "text_inputs": list(text_inputs),
                "media_inputs": list(media_inputs),
                **kwargs,
            },
            extra,
        )
        return normalized(
            self._transport.request("POST", "/video_template_generation", json=payload)
        )

    def query(self, task_id: str | int) -> Dict[str, Any]:
        return self._transport.request(
            "GET", "/query/video_generation", params={"task_id": task_id}
        )

    def query_agent(self, task_id: str | int) -> Dict[str, Any]:
        return self._transport.request(
            "GET", "/query/video_template_generation", params={"task_id": task_id}
        )

    def poll(
        self, task_id: str | int, *, interval: float = 5.0, timeout: float = 1800.0
    ) -> Dict[str, Any]:
        return poll_sync(
            lambda value: self.query(value),
            str(task_id),
            interval=interval,
            timeout=timeout,
            success=_SUCCESS,
            failure=_FAILURE,
        )

    def cancel(
        self,
        task_id: str | int,
        *,
        path: str,
        method: str = "POST",
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call an account/gateway-specific cancellation endpoint.

        MiniMax public documentation does not currently define a stable cancel
        path. The caller must explicitly provide a confirmed path.
        """

        payload = compact_payload({"task_id": task_id}, extra)
        return self._transport.request(method, path, json=payload)

    def _create(self, *, extra=None, **values: Any) -> GenerationResult:
        payload = compact_payload(values, extra)
        return normalized(self._transport.request("POST", "/video_generation", json=payload))


class AsyncVideoResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def text_to_video(self, *, extra=None, **values: Any) -> GenerationResult:
        return await self._create(extra=extra, **values)

    async def image_to_video(self, *, extra=None, **values: Any) -> GenerationResult:
        return await self._create(extra=extra, **values)

    async def video_agent(
        self, *, template_id: str, text_inputs, media_inputs, extra=None, **kwargs: Any
    ) -> GenerationResult:
        payload = compact_payload(
            {
                "template_id": template_id,
                "text_inputs": list(text_inputs),
                "media_inputs": list(media_inputs),
                **kwargs,
            },
            extra,
        )
        return normalized(
            await self._transport.request("POST", "/video_template_generation", json=payload)
        )

    async def query(self, task_id: str | int) -> Dict[str, Any]:
        return await self._transport.request(
            "GET", "/query/video_generation", params={"task_id": task_id}
        )

    async def query_agent(self, task_id: str | int) -> Dict[str, Any]:
        return await self._transport.request(
            "GET", "/query/video_template_generation", params={"task_id": task_id}
        )

    async def poll(
        self, task_id: str | int, *, interval: float = 5.0, timeout: float = 1800.0
    ) -> Dict[str, Any]:
        return await poll_async(
            lambda value: self.query(value),
            str(task_id),
            interval=interval,
            timeout=timeout,
            success=_SUCCESS,
            failure=_FAILURE,
        )

    async def cancel(
        self, task_id: str | int, *, path: str, method: str = "POST", extra=None
    ) -> Dict[str, Any]:
        return await self._transport.request(
            method, path, json=compact_payload({"task_id": task_id}, extra)
        )

    async def _create(self, *, extra=None, **values: Any) -> GenerationResult:
        return normalized(
            await self._transport.request(
                "POST", "/video_generation", json=compact_payload(values, extra)
            )
        )
