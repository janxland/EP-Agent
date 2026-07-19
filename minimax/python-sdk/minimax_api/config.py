"""SDK configuration with environment-only credential discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional

import httpx


class Region(str, Enum):
    """Supported MiniMax public API regions."""

    MAINLAND = "mainland"
    GLOBAL = "global"


REGION_BASE_URLS = {
    Region.MAINLAND: "https://api.minimaxi.com/v1",
    Region.GLOBAL: "https://api.minimax.io/v1",
}


@dataclass(frozen=True)
class RetryConfig:
    """Bounded retry policy for transient transport and gateway failures."""

    max_retries: int = 3
    initial_delay: float = 0.5
    max_delay: float = 8.0
    backoff_factor: float = 2.0
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 409, 429, 500, 502, 503, 504})
    )

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.initial_delay < 0 or self.max_delay < 0:
            raise ValueError("retry delays must be >= 0")
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor must be >= 1")


@dataclass(frozen=True)
class Config:
    """Connection configuration.

    API keys are accepted only explicitly or from ``MINIMAX_API_KEY``. This
    class deliberately does not load dotenv files.
    """

    api_key: Optional[str] = None
    region: Region | str | None = None
    base_url: Optional[str] = None
    timeout: float | httpx.Timeout = 60.0
    connect_timeout: float = 10.0
    retry: RetryConfig = field(default_factory=RetryConfig)
    default_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = self.api_key or os.getenv("MINIMAX_API_KEY")
        if not key or not key.strip():
            raise ValueError(
                "MiniMax API key is required: pass Config(api_key=...) or set MINIMAX_API_KEY"
            )
        object.__setattr__(self, "api_key", key.strip())

        raw_region = self.region or os.getenv("MINIMAX_REGION", Region.MAINLAND.value)
        try:
            region = raw_region if isinstance(raw_region, Region) else Region(raw_region.lower())
        except ValueError as exc:
            raise ValueError("region must be 'mainland' or 'global'") from exc
        object.__setattr__(self, "region", region)

        url = self.base_url or os.getenv("MINIMAX_BASE_URL") or REGION_BASE_URLS[region]
        object.__setattr__(self, "base_url", url.rstrip("/"))

        if isinstance(self.timeout, (int, float)):
            timeout = httpx.Timeout(float(self.timeout), connect=self.connect_timeout)
            object.__setattr__(self, "timeout", timeout)

    @property
    def api_root(self) -> str:
        """Origin without the trailing ``/v1`` segment."""

        assert self.base_url is not None
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    @property
    def websocket_url(self) -> str:
        """Official synchronous T2A WebSocket URL for the selected origin."""

        scheme_root = self.api_root.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        return f"{scheme_root}/ws/v1/t2a_v2"
