"""Voice upload, cloning, design, and catalog APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..models import GenerationResult
from ..transport import AsyncTransport, Transport
from ..validation import validate_audio_file, validate_voice_id
from ._base import compact_payload, normalized


class VoiceResource:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def upload_voice_clone(self, file_path: str | Path) -> GenerationResult:
        return self._upload_audio(file_path, "voice_clone")

    def upload_prompt_audio(self, file_path: str | Path) -> GenerationResult:
        return self._upload_audio(file_path, "prompt_audio")

    def voice_clone(
        self,
        *,
        file_id: str | int,
        voice_id: str,
        clone_prompt: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        validate_voice_id(voice_id)
        payload = compact_payload(
            {
                "file_id": file_id,
                "voice_id": voice_id,
                "clone_prompt": dict(clone_prompt) if clone_prompt else None,
                **kwargs,
            },
            extra,
        )
        return normalized(self._transport.request("POST", "/voice_clone", json=payload))

    def voice_design(
        self,
        *,
        prompt: str,
        preview_text: str,
        voice_id: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        if voice_id is not None:
            validate_voice_id(voice_id)
        payload = compact_payload(
            {"prompt": prompt, "preview_text": preview_text, "voice_id": voice_id, **kwargs}, extra
        )
        return normalized(self._transport.request("POST", "/voice_design", json=payload))

    def list_voices(self, voice_type: str = "all") -> Dict[str, Any]:
        if voice_type not in {"all", "system", "voice_cloning", "voice_generation"}:
            raise ValueError("unsupported voice_type")
        return self._transport.request("POST", "/get_voice", json={"voice_type": voice_type})

    def _upload_audio(self, file_path: str | Path, purpose: str) -> GenerationResult:
        path = validate_audio_file(file_path, purpose=purpose)
        with path.open("rb") as stream:
            response = self._transport.request(
                "POST",
                "/files/upload",
                data={"purpose": purpose},
                files={"file": (path.name, stream, _audio_content_type(path))},
            )
        return normalized(response)


class AsyncVoiceResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def upload_voice_clone(self, file_path: str | Path) -> GenerationResult:
        return await self._upload_audio(file_path, "voice_clone")

    async def upload_prompt_audio(self, file_path: str | Path) -> GenerationResult:
        return await self._upload_audio(file_path, "prompt_audio")

    async def voice_clone(self, *, file_id, voice_id: str, clone_prompt=None, extra=None, **kwargs: Any) -> GenerationResult:
        validate_voice_id(voice_id)
        payload = compact_payload(
            {"file_id": file_id, "voice_id": voice_id, "clone_prompt": clone_prompt, **kwargs}, extra
        )
        return normalized(await self._transport.request("POST", "/voice_clone", json=payload))

    async def voice_design(
        self, *, prompt: str, preview_text: str, voice_id=None, extra=None, **kwargs: Any
    ) -> GenerationResult:
        if voice_id is not None:
            validate_voice_id(voice_id)
        payload = compact_payload(
            {"prompt": prompt, "preview_text": preview_text, "voice_id": voice_id, **kwargs}, extra
        )
        return normalized(await self._transport.request("POST", "/voice_design", json=payload))

    async def list_voices(self, voice_type: str = "all") -> Dict[str, Any]:
        if voice_type not in {"all", "system", "voice_cloning", "voice_generation"}:
            raise ValueError("unsupported voice_type")
        return await self._transport.request("POST", "/get_voice", json={"voice_type": voice_type})

    async def _upload_audio(self, file_path: str | Path, purpose: str) -> GenerationResult:
        path = validate_audio_file(file_path, purpose=purpose)
        with path.open("rb") as stream:
            response = await self._transport.request(
                "POST",
                "/files/upload",
                data={"purpose": purpose},
                files={"file": (path.name, stream, _audio_content_type(path))},
            )
        return normalized(response)


def _audio_content_type(path: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }[path.suffix.lower()]
