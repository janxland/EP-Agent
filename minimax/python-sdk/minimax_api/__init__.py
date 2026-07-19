"""A lightweight, safe, sync/async MiniMax Python SDK."""

from .client import AsyncMiniMax, MiniMax
from .config import Config, Region, RetryConfig
from .errors import (
    MiniMaxAPIError,
    MiniMaxConfigurationError,
    MiniMaxError,
    MiniMaxTransportError,
    MiniMaxValidationError,
)
from .models import APIResult, FileDownload, GenerationResult, TTSChunk

__all__ = [
    "APIResult",
    "AsyncMiniMax",
    "Config",
    "FileDownload",
    "GenerationResult",
    "MiniMax",
    "MiniMaxAPIError",
    "MiniMaxConfigurationError",
    "MiniMaxError",
    "MiniMaxTransportError",
    "MiniMaxValidationError",
    "Region",
    "RetryConfig",
    "TTSChunk",
]

__version__ = "0.1.0"
