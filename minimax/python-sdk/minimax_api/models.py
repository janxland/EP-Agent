"""Lightweight public result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class APIResult(Generic[T]):
    """Normalized response with the unmodified payload retained in ``raw``."""

    data: T
    raw: Dict[str, Any]
    request_id: Optional[str] = None


@dataclass(frozen=True)
class GenerationResult:
    """Common shape for media generation responses."""

    raw: Dict[str, Any]
    data: Any = None
    id: Optional[str] = None
    task_id: Optional[str] = None
    file_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None

    @classmethod
    def from_response(cls, payload: Dict[str, Any]) -> "GenerationResult":
        file_id = payload.get("file_id")
        file_object = payload.get("file")
        if file_id is None and isinstance(file_object, dict):
            file_id = file_object.get("file_id")
        return cls(
            raw=payload,
            data=payload.get("data"),
            id=_as_optional_string(payload.get("id")),
            task_id=_as_optional_string(payload.get("task_id")),
            file_id=_as_optional_string(file_id),
            trace_id=_as_optional_string(payload.get("trace_id")),
            request_id=_as_optional_string(payload.get("_request_id")),
        )


@dataclass(frozen=True)
class TTSChunk:
    """One decoded HTTP streaming TTS event."""

    audio: bytes = b""
    raw: Dict[str, Any] = field(default_factory=dict)
    status: Optional[int] = None
    trace_id: Optional[str] = None
    is_final: bool = False


@dataclass(frozen=True)
class FileDownload:
    """Downloaded content plus optional metadata."""

    content: bytes
    filename: Optional[str] = None
    content_type: Optional[str] = None
    source_url: Optional[str] = None
    saved_to: Optional[Path] = None


def _as_optional_string(value: Any) -> Optional[str]:
    return None if value is None else str(value)
