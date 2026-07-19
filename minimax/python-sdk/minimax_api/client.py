"""Public synchronous and asynchronous MiniMax clients."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import httpx

from .config import Config
from .resources.files import AsyncFilesResource, FilesResource
from .resources.image import AsyncImageResource, ImageResource
from .resources.music import AsyncMusicResource, MusicResource
from .resources.speech import AsyncSpeechResource, SpeechResource
from .resources.text import AsyncTextResource, TextResource
from .resources.video import AsyncVideoResource, VideoResource
from .resources.voice import AsyncVoiceResource, VoiceResource
from .transport import AsyncTransport, Transport


class MiniMax:
    """Synchronous SDK client sharing one ``httpx.Client`` across resources."""

    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        api_key: Optional[str] = None,
        region: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float | httpx.Timeout = 60.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.config = config or Config(
            api_key=api_key,
            region=region,
            base_url=base_url,
            timeout=timeout,
        )
        self._transport = Transport(self.config, http_client)
        self.text = TextResource(self._transport)
        self.speech = SpeechResource(self._transport)
        self.voice = VoiceResource(self._transport)
        self.music = MusicResource(self._transport)
        self.image = ImageResource(self._transport)
        self.video = VideoResource(self._transport)
        self.files = FilesResource(self._transport)

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
        """Low-level JSON request for evolving account-specific fields/endpoints."""

        return self._transport.request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=headers,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "MiniMax":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class AsyncMiniMax:
    """Asynchronous SDK client sharing one ``httpx.AsyncClient`` across resources."""

    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        api_key: Optional[str] = None,
        region: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float | httpx.Timeout = 60.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.config = config or Config(
            api_key=api_key,
            region=region,
            base_url=base_url,
            timeout=timeout,
        )
        self._transport = AsyncTransport(self.config, http_client)
        self.text = AsyncTextResource(self._transport)
        self.speech = AsyncSpeechResource(self._transport)
        self.voice = AsyncVoiceResource(self._transport)
        self.music = AsyncMusicResource(self._transport)
        self.image = AsyncImageResource(self._transport)
        self.video = AsyncVideoResource(self._transport)
        self.files = AsyncFilesResource(self._transport)

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
        """Low-level async JSON request for evolving account-specific APIs."""

        return await self._transport.request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=headers,
        )

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> "AsyncMiniMax":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()
