"""Synchronous image generation endpoints."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from ..models import GenerationResult
from ..transport import AsyncTransport, Transport
from ._base import compact_payload, normalized


class ImageResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def text_to_image(
        self, *, model: str, prompt: str, extra: Optional[Mapping[str, Any]] = None, **kwargs: Any
    ) -> GenerationResult:
        payload = compact_payload({"model": model, "prompt": prompt, **kwargs}, extra)
        return normalized(self._transport.request("POST", "/image_generation", json=payload))

    def image_to_image(
        self,
        *,
        model: str,
        prompt: str,
        subject_reference: Sequence[Mapping[str, Any]],
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        payload = compact_payload(
            {"model": model, "prompt": prompt, "subject_reference": list(subject_reference), **kwargs},
            extra,
        )
        return normalized(self._transport.request("POST", "/image_generation", json=payload))


class AsyncImageResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def text_to_image(self, *, model: str, prompt: str, extra=None, **kwargs: Any) -> GenerationResult:
        payload = compact_payload({"model": model, "prompt": prompt, **kwargs}, extra)
        return normalized(await self._transport.request("POST", "/image_generation", json=payload))

    async def image_to_image(
        self, *, model: str, prompt: str, subject_reference, extra=None, **kwargs: Any
    ) -> GenerationResult:
        payload = compact_payload(
            {"model": model, "prompt": prompt, "subject_reference": list(subject_reference), **kwargs},
            extra,
        )
        return normalized(await self._transport.request("POST", "/image_generation", json=payload))
