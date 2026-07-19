from __future__ import annotations

import json

import httpx

from minimax_api import Config, MiniMax


def test_openai_extra_body_and_anthropic_path() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "ok", "base_resp": {"status_code": 0, "status_msg": "success"}},
            request=request,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = MiniMax(Config(api_key="test-key"), http_client=http_client)
    sdk.text.chat_completions(
        model="MiniMax-M3",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
        extra_body={"thinking": {"type": "adaptive"}, "service_tier": "priority"},
    )
    openai_body = json.loads(requests[-1].content)
    assert requests[-1].url.path == "/v1/chat/completions"
    assert openai_body["thinking"] == {"type": "adaptive"}

    sdk.text.anthropic_messages(
        model="MiniMax-M3",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
    )
    assert requests[-1].url.path == "/anthropic/v1/messages"
    assert requests[-1].headers["anthropic-version"] == "2023-06-01"
    http_client.close()


def test_media_results_are_normalized_and_file_id_nested_is_extracted(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/upload"):
            payload = {"file": {"file_id": "f-1"}, "base_resp": {"status_code": 0}}
        else:
            payload = {"task_id": "t-1", "base_resp": {"status_code": 0}}
        return httpx.Response(200, json=payload, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = MiniMax(Config(api_key="test-key"), http_client=http_client)
    video = sdk.video.text_to_video(model="MiniMax-Hailuo-2.3", prompt="A calm lake")
    assert video.task_id == "t-1"
    source = tmp_path / "input.txt"
    source.write_text("long text", encoding="utf-8")
    uploaded = sdk.files.upload(source, purpose="t2a_async_input")
    assert uploaded.file_id == "f-1"
    http_client.close()


def test_video_cancel_requires_explicit_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"base_resp": {"status_code": 0}}, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = MiniMax(Config(api_key="test-key"), http_client=http_client)
    sdk.video.cancel("task-1", path="/account-specific/video/cancel")
    assert seen == ["/v1/account-specific/video/cancel"]
    http_client.close()
