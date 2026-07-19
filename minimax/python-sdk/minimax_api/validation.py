"""Conservative local validation for IDs and upload files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

try:
    import wave
except ImportError:  # pragma: no cover - 某些精简 Python 运行时可能移除该标准库模块
    wave = None  # type: ignore[assignment]

from .errors import MiniMaxValidationError

PathLike = Union[str, Path]
VOICE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]$")
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav"}
MAX_CLONE_AUDIO_BYTES = 20 * 1024 * 1024
MAX_GENERAL_FILE_BYTES = 512 * 1024 * 1024


def validate_voice_id(voice_id: str) -> str:
    """Validate the documented 8-256 character custom voice ID grammar."""

    if not isinstance(voice_id, str) or not VOICE_ID_RE.fullmatch(voice_id):
        raise MiniMaxValidationError(
            "voice_id must be 8-256 characters, start with an ASCII letter, contain only "
            "letters/digits/_/-, and end with a letter or digit"
        )
    return voice_id


def validate_audio_file(
    file_path: PathLike,
    *,
    purpose: str,
    max_bytes: int = MAX_CLONE_AUDIO_BYTES,
    check_wav_duration: bool = True,
) -> Path:
    """Validate existence, extension, size, and WAV duration when inspectable.

    MP3/M4A duration is intentionally not guessed without an audio dependency;
    the server remains authoritative for duration validation.
    """

    path = Path(file_path).expanduser()
    if not path.is_file():
        raise MiniMaxValidationError(f"audio file does not exist or is not a file: {path}")
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise MiniMaxValidationError("audio file must use .mp3, .m4a, or .wav")
    size = path.stat().st_size
    if size <= 0:
        raise MiniMaxValidationError("audio file must not be empty")
    if size > max_bytes:
        raise MiniMaxValidationError(f"audio file exceeds {max_bytes} bytes")
    if check_wav_duration and path.suffix.lower() == ".wav":
        if wave is None:
            raise MiniMaxValidationError(
                "WAV duration validation requires the Python wave module in this runtime"
            )
        duration = _wav_duration(path)
        if purpose == "voice_clone" and not 10.0 <= duration <= 300.0:
            raise MiniMaxValidationError("voice_clone WAV duration must be between 10s and 5min")
        if purpose == "prompt_audio" and duration >= 8.0:
            raise MiniMaxValidationError("prompt_audio WAV duration must be shorter than 8s")
    return path


def validate_general_file(file_path: PathLike, max_bytes: int = MAX_GENERAL_FILE_BYTES) -> Path:
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise MiniMaxValidationError(f"file does not exist or is not a file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise MiniMaxValidationError("file must not be empty")
    if size > max_bytes:
        raise MiniMaxValidationError(f"file exceeds {max_bytes} bytes")
    return path


def optional_non_empty(value: Optional[str], name: str) -> Optional[str]:
    if value is not None and not value.strip():
        raise MiniMaxValidationError(f"{name} must not be blank")
    return value


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                raise MiniMaxValidationError("WAV file has an invalid frame rate")
            return wav_file.getnframes() / frame_rate
    except (wave.Error, EOFError) as exc:
        raise MiniMaxValidationError(f"invalid WAV audio file: {path}") from exc
