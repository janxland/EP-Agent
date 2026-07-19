"""Exception hierarchy for MiniMax API failures."""

from __future__ import annotations

from typing import Any, Mapping, Optional


class MiniMaxError(Exception):
    """Base SDK exception."""


class MiniMaxConfigurationError(MiniMaxError):
    """Invalid or missing client configuration."""


class MiniMaxValidationError(MiniMaxError, ValueError):
    """Locally rejected input that cannot be safely sent."""


class MiniMaxTransportError(MiniMaxError):
    """Network failure after the configured retry budget is exhausted."""

    def __init__(self, message: str, *, request_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class MiniMaxAPIError(MiniMaxError):
    """HTTP failure or non-zero MiniMax ``base_resp.status_code``."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        api_status_code: Optional[int] = None,
        request_id: Optional[str] = None,
        response: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_status_code = api_status_code
        self.request_id = request_id
        self.response = response

    def __str__(self) -> str:
        details = []
        if self.status_code is not None:
            details.append(f"http_status={self.status_code}")
        if self.api_status_code is not None:
            details.append(f"api_status={self.api_status_code}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{super().__str__()}{suffix}"
