"""Music generation, cover, and lyrics-forwarding helpers."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..models import GenerationResult
from ..transport import AsyncTransport, Transport
from ._base import compact_payload, normalized


class MusicResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def music_generation(
        self,
        *,
        model: str,
        prompt: Optional[str] = None,
        lyrics: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        return self._generate(model=model, prompt=prompt, lyrics=lyrics, extra=extra, **kwargs)

    def lyrics_generation(
        self,
        *,
        mode: str,
        prompt: Optional[str] = None,
        lyrics: Optional[str] = None,
        title: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> GenerationResult:
        if mode not in {"write_full_song", "edit"}:
            raise ValueError("mode must be 'write_full_song' or 'edit'")
        payload = compact_payload(
            {"mode": mode, "prompt": prompt, "lyrics": lyrics, "title": title}, extra
        )
        return normalized(self._transport.request("POST", "/lyrics_generation", json=payload))

    def music_cover_preprocess(
        self,
        *,
        audio_url: Optional[str] = None,
        audio_base64: Optional[str] = None,
        model: str = "music-cover",
        extra: Optional[Mapping[str, Any]] = None,
    ) -> GenerationResult:
        if (audio_url is None) == (audio_base64 is None):
            raise ValueError("provide exactly one of audio_url or audio_base64")
        payload = compact_payload(
            {"model": model, "audio_url": audio_url, "audio_base64": audio_base64}, extra
        )
        return normalized(
            self._transport.request("POST", "/music_cover_preprocess", json=payload)
        )

    def music_cover(
        self,
        *,
        prompt: str,
        model: str = "music-cover",
        audio_url: Optional[str] = None,
        audio_base64: Optional[str] = None,
        cover_feature_id: Optional[str] = None,
        lyrics: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        sources = [audio_url, audio_base64, cover_feature_id]
        if sum(value is not None for value in sources) != 1:
            raise ValueError("music_cover requires exactly one audio_url, audio_base64, or cover_feature_id")
        return self._generate(
            model=model,
            prompt=prompt,
            lyrics=lyrics,
            audio_url=audio_url,
            audio_base64=audio_base64,
            cover_feature_id=cover_feature_id,
            extra=extra,
            **kwargs,
        )

    def _generate(self, *, extra=None, **values: Any) -> GenerationResult:
        payload = compact_payload(values, extra)
        return normalized(self._transport.request("POST", "/music_generation", json=payload))


class AsyncMusicResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def music_generation(self, *, extra=None, **values: Any) -> GenerationResult:
        return await self._generate(extra=extra, **values)

    async def lyrics_generation(
        self, *, mode: str, prompt=None, lyrics=None, title=None, extra=None
    ) -> GenerationResult:
        if mode not in {"write_full_song", "edit"}:
            raise ValueError("mode must be 'write_full_song' or 'edit'")
        payload = compact_payload(
            {"mode": mode, "prompt": prompt, "lyrics": lyrics, "title": title}, extra
        )
        return normalized(
            await self._transport.request("POST", "/lyrics_generation", json=payload)
        )

    async def music_cover_preprocess(
        self, *, audio_url=None, audio_base64=None, model="music-cover", extra=None
    ) -> GenerationResult:
        if (audio_url is None) == (audio_base64 is None):
            raise ValueError("provide exactly one of audio_url or audio_base64")
        payload = compact_payload(
            {"model": model, "audio_url": audio_url, "audio_base64": audio_base64}, extra
        )
        return normalized(
            await self._transport.request("POST", "/music_cover_preprocess", json=payload)
        )

    async def music_cover(
        self,
        *,
        prompt: str,
        model: str = "music-cover",
        audio_url=None,
        audio_base64=None,
        cover_feature_id=None,
        lyrics=None,
        extra=None,
        **kwargs: Any,
    ) -> GenerationResult:
        if sum(value is not None for value in [audio_url, audio_base64, cover_feature_id]) != 1:
            raise ValueError("music_cover requires exactly one audio_url, audio_base64, or cover_feature_id")
        return await self._generate(
            model=model,
            prompt=prompt,
            audio_url=audio_url,
            audio_base64=audio_base64,
            cover_feature_id=cover_feature_id,
            lyrics=lyrics,
            extra=extra,
            **kwargs,
        )

    async def _generate(self, *, extra=None, **values: Any) -> GenerationResult:
        payload = compact_payload(values, extra)
        return normalized(await self._transport.request("POST", "/music_generation", json=payload))
