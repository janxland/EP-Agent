from __future__ import annotations

import asyncio

import httpx
import pytest

from minimax_api import AsyncMiniMax, Config, MiniMax, MiniMaxAPIError, RetryConfig


def test_base_resp_error_is_raised_with_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers.get("x-request-id")
        return httpx.Response(
            200,
            json={"base_resp": {"status_code": 1004, "status_msg": "invalid parameter"}},
            headers={"x-request-id": "gateway-request-1"},
            request=request,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = MiniMax(Config(api_key="test-key"), http_client=http_client)
    with pytest.raises(MiniMaxAPIError) as captured:
        sdk.request("POST", "/test", json={"secret": "not logged"})
    assert captured.value.api_status_code == 1004
    assert captured.value.request_id == "gateway-request-1"
    assert "invalid parameter" in str(captured.value)
    http_client.close()


def test_retry_honors_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"}, request=request)
        return httpx.Response(
            200,
            json={"ok": True, "base_resp": {"status_code": 0, "status_msg": "success"}},
            request=request,
        )

    monkeypatch.setattr("minimax_api.transport.time.sleep", sleeps.append)
    config = Config(
        api_key="test-key",
        retry=RetryConfig(max_retries=1, initial_delay=10, max_delay=2),
    )
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = MiniMax(config, http_client=http_client)
    result = sdk.request("GET", "/retry")
    assert result["ok"] is True
    assert attempts == 2
    assert sleeps == [0.25]
    http_client.close()


def test_async_client_uses_bearer_and_returns_request_id() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer async-key"
            return httpx.Response(
                200,
                json={"value": 1, "base_resp": {"status_code": 0}},
                request=request,
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sdk = AsyncMiniMax(Config(api_key="async-key"), http_client=http_client)
        result = await sdk.request("GET", "/value")
        assert result["value"] == 1
        assert result["_request_id"]
        await http_client.aclose()

    asyncio.run(run())


def test_region_base_urls() -> None:
    assert Config(api_key="x", region="mainland").base_url == "https://api.minimaxi.com/v1"
    assert Config(api_key="x", region="global").base_url == "https://api.minimax.io/v1"
