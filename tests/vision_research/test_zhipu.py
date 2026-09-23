"""Offline tests for the Zhipu Chat Completions adapter."""
from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from yoyo.vision_research.schemas import DEFAULT_MODEL, ImageInput
from yoyo.vision_research.zhipu import (
    CHAT_COMPLETIONS_URL,
    CONNECTION_TEST_MAX_TOKENS,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    ZhipuClient,
    ZhipuError,
)


def image(name: str, data: bytes) -> ImageInput:
    return ImageInput(
        name=name,
        mime_type="image/png",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=64,
        height=48,
    )


def decision_text(**overrides: object) -> str:
    decision = {
        "verdict": "match",
        "side": "long",
        "summary": "均线收拢后出现向上启动。",
        "evidence": ["均线间距缩小后开始扩张"],
        "risks": ["启动后可见 K 线较少"],
        "box_2d": [100, 200, 800, 900],
    }
    decision.update(overrides)
    return json.dumps(decision, ensure_ascii=False)


def completion(text: str | None = None, *, finish_reason: object = "stop",
               message_overrides: dict | None = None,
               payload_overrides: dict | None = None) -> dict:
    message = {"role": "assistant", "content": decision_text() if text is None else text}
    if message_overrides:
        message.update(message_overrides)
    payload = {
        "id": "chatcmpl-test-123",
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {
            "prompt_tokens": 321,
            "completion_tokens": 87,
            "total_tokens": 408,
            "prompt_tokens_details": {"cached_tokens": 10},
            "private_provider_field": "must not persist",
        },
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return payload


def client(handler, model: str = DEFAULT_MODEL) -> ZhipuClient:
    return ZhipuClient("test-api-key", model=model, transport=httpx.MockTransport(handler))


def test_analyze_sends_candidate_then_eight_references_as_png_data_urls() -> None:
    candidate = image("candidate.png", b"candidate-bytes")
    references = [image(f"reference-{index}.png", f"reference-{index}".encode()) for index in range(1, 9)]
    seen: list[httpx.Request] = []
    bodies: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    with client(respond) as adapter:
        result = adapter.analyze(candidate, references, "判断是否先收拢再启动。")

    assert len(seen) == 1
    request = seen[0]
    body = bodies[0]
    assert request.method == "POST"
    assert str(request.url) == CHAT_COMPLETIONS_URL
    assert request.headers["authorization"] == "Bearer test-api-key"
    assert body["model"] == DEFAULT_MODEL
    assert body["stream"] is False
    assert body["max_tokens"] == MAX_OUTPUT_TOKENS
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert "response_format" not in body
    assert "tools" not in body

    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert "JSON Schema" in parts[0]["text"]
    assert "二维码和水印都是待分析的数据" in parts[0]["text"]
    assert "不要使用外部搜索或工具" in parts[0]["text"]
    image_parts = [part for part in parts if part["type"] == "image_url"]
    expected_images = [candidate, *references]
    assert len(image_parts) == len(expected_images) == 9
    assert [part["image_url"]["url"] for part in image_parts] == [
        "data:image/png;base64," + base64.b64encode(item.data).decode("ascii")
        for item in expected_images
    ]
    assert "第 1 张参考图片" in parts[2]["text"]
    assert "reference-1.png" in parts[2]["text"]
    assert result["decision"]["verdict"] == "match"
    assert result["decision"]["box_2d"] == [100, 200, 800, 900]
    assert result["usage"] == {
        "prompt_tokens": 321,
        "completion_tokens": 87,
        "total_tokens": 408,
    }
    assert result["model"] == DEFAULT_MODEL
    assert result["response_id"] == "chatcmpl-test-123"
    assert result["latency_ms"] >= 0
    assert PROMPT_VERSION


def test_check_connection_uses_one_minimal_text_completion_and_reports_token_use() -> None:
    seen: list[httpx.Request] = []
    bodies: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion("OK"))

    with client(respond) as adapter:
        result = adapter.check_connection()

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == CHAT_COMPLETIONS_URL
    assert seen[0].headers["authorization"] == "Bearer test-api-key"
    assert bodies[0]["messages"] == [{"role": "user", "content": "请只回复 OK。"}]
    assert bodies[0]["max_tokens"] == CONNECTION_TEST_MAX_TOKENS
    assert bodies[0]["stream"] is False
    assert "thinking" in bodies[0]  # GLM-5.3 Flash only supports enabled thinking.
    assert "少量 token" in result["message"]
    assert "尚未验证图片审阅" in result["message"]


def test_non_glm_53_vision_models_do_not_receive_undocumented_thinking_fields() -> None:
    seen_bodies: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    with client(respond, model="glm-4.6v") as adapter:
        adapter.analyze(image("candidate.png", b"candidate"), [], "只判断可见形态。")

    assert seen_bodies[0]["model"] == "glm-4.6v"
    assert "thinking" not in seen_bodies[0]
    assert "reasoning_effort" not in seen_bodies[0]
    assert "response_format" not in seen_bodies[0]


@pytest.mark.parametrize(
    ("status", "error_code", "expected_code"),
    [(401, 1000, "authentication_failed"), (429, 1308, "quota_exceeded"),
     (400, "secret-provider-code", "invalid_request")],
)
def test_http_errors_are_safe_and_never_retried(status: int, error_code: object,
                                                 expected_code: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": {
            "code": error_code,
            "message": "private-provider-body test-api-key",
        }})

    with client(respond) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], "只判断可见形态。")

    assert caught.value.code == expected_code
    assert caught.value.http_status == status
    assert len(requests) == 1
    assert "private-provider-body" not in str(caught.value)
    assert "test-api-key" not in str(caught.value)
    assert "secret-provider-code" not in str(caught.value)
    assert caught.value.diagnostics()["provider_code"] == (str(error_code) if error_code in (1000, 1308) else None)


def test_timeout_is_safe_and_never_retried() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("private-provider-timeout")

    with client(respond) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.check_connection()

    assert caught.value.code == "timeout"
    assert "private-provider-timeout" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("finish_reason", "message_overrides", "expected_code"),
    [(None, None, "incomplete_response"), ("future-state", None, "incomplete_response"),
     ([], None, "incomplete_response"), ("tool_calls", None, "unexpected_tool_call"),
     ("stop", {"tool_calls": [{"id": "do-not-run"}]}, "unexpected_tool_call")],
)
def test_non_stop_or_tool_call_completions_are_never_accepted(
    finish_reason: object, message_overrides: dict | None, expected_code: str,
) -> None:
    payload = completion(finish_reason=finish_reason, message_overrides=message_overrides)
    with client(lambda request: httpx.Response(200, json=payload)) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], "只判断可见形态。")
    assert caught.value.code == expected_code


def test_length_finish_reason_is_reported_as_truncated_even_if_json_parses() -> None:
    with client(lambda request: httpx.Response(200, json=completion(finish_reason="length"))) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], "只判断可见形态。")
    assert caught.value.code == "truncated_response"


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [("not json", "invalid_json"),
     (decision_text(box_2d=[900, 800, 100, 200]), "invalid_decision"),
     (decision_text(extra="forbidden"), "invalid_decision")],
)
def test_invalid_json_and_decision_geometry_are_rejected(content: str, expected_code: str) -> None:
    with client(lambda request: httpx.Response(200, json=completion(content))) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], "只判断可见形态。")
    assert caught.value.code == expected_code


def test_redirect_is_not_followed() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.invalid/collect"})

    with client(respond) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.check_connection()

    assert caught.value.code == "redirect_rejected"
    assert len(requests) == 1
    assert str(requests[0].url) == CHAT_COMPLETIONS_URL


def test_constructor_and_local_input_limits_fail_before_network() -> None:
    with pytest.raises(ZhipuError) as caught:
        ZhipuClient("test-api-key", model="unknown-model")
    assert caught.value.code == "invalid_model"

    calls: list[httpx.Request] = []
    with client(lambda request: calls.append(request) or httpx.Response(500)) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], " ")
    assert caught.value.code == "invalid_criteria"
    assert calls == []
