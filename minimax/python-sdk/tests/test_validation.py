from __future__ import annotations

import wave
from pathlib import Path

import pytest

from minimax_api import MiniMaxValidationError
from minimax_api.validation import validate_audio_file, validate_voice_id


def _write_silent_wav(path: Path, seconds: float, sample_rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x00\x00" * int(seconds * sample_rate))


def test_voice_id_validation() -> None:
    assert validate_voice_id("Voice_123") == "Voice_123"
    for invalid in ["short", "1starts_wrong", "ends-wrong_", "has space 1"]:
        with pytest.raises(MiniMaxValidationError):
            validate_voice_id(invalid)


def test_voice_clone_wav_duration_validation(tmp_path: Path) -> None:
    short = tmp_path / "short.wav"
    valid = tmp_path / "valid.wav"
    _write_silent_wav(short, 1.0)
    _write_silent_wav(valid, 10.0)

    with pytest.raises(MiniMaxValidationError, match="between 10s and 5min"):
        validate_audio_file(short, purpose="voice_clone")
    assert validate_audio_file(valid, purpose="voice_clone") == valid


def test_prompt_audio_must_be_under_eight_seconds(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.wav"
    _write_silent_wav(prompt, 8.0)
    with pytest.raises(MiniMaxValidationError, match="shorter than 8s"):
        validate_audio_file(prompt, purpose="prompt_audio")


def test_audio_extension_and_existence(tmp_path: Path) -> None:
    text = tmp_path / "voice.txt"
    text.write_text("not audio", encoding="utf-8")
    with pytest.raises(MiniMaxValidationError, match=".mp3"):
        validate_audio_file(text, purpose="voice_clone")
    with pytest.raises(MiniMaxValidationError, match="does not exist"):
        validate_audio_file(tmp_path / "missing.wav", purpose="voice_clone")
