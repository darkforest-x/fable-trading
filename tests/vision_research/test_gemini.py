"""Gemini REST boundary tests; all HTTP responses use MockTransport."""
from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from yoyo.vision_research.gemini import (
    INTERACTIONS_URL,
    MAX_OUTPUT_TOKENS,
    MODELS_URL,
    GeminiClient,
    GeminiError,
)
from yoyo.vision_research.schemas import DEFAULT_MODEL, Decision, ImageInput


def image(name: str, data: bytes, mime_type: str = "image/png") -> ImageInput:
    return ImageInput(
        name=name,
        mime_type=mime_type,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=64,
        height=48,
    )


def completed_body(text: str | None = None, **overrides) -> dict:
    if text is None:
        text = json.dumps({
            "verdict": "match",
            "side": "long",
            "summary": "均线收拢后出现向上启动。",
            "evidence": ["均线间距缩小后开始扩张"],
            "risks": ["启动后可见 K 线较少"],
            "box_2d": [100, 200, 800, 900],
        }, ensure_ascii=False)
    payload = {
        "id": "interaction_123",
        "model": DEFAULT_MODEL,
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
        "usage": {
            "total_input_tokens": 321,
            "total_output_tokens": 87,
            "total_tokens": 408,
            "untrusted_extra": "do not persist",
            "total_thought_tokens": -1,
        },
    }
    payload.update(overrides)
    return payload


def make_client(handler) -> GeminiClient:
    return GeminiClient("test-key", transport=httpx.MockTransport(handler))


def test_analyze_sends_candidate_before_references_with_structured_output_and_no_store() -> None:
    candidate = image("candidate.png", b"candidate image bytes")
    reference = image("reference.webp", b"reference image bytes", "image/webp")
    observed = {}

    def respond(request: httpx.Request) -> httpx.Response:
        observed["request"] = request
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=completed_body())

    with make_client(respond) as client:
        result = client.analyze(candidate, [reference], "观察均线是否先收拢再启动。")

    request = observed["request"]
    body = observed["body"]
    assert request.method == "POST"
    assert str(request.url) == INTERACTIONS_URL
    assert "test-key" not in str(request.url)
    assert request.headers["x-goog-api-key"] == "test-key"
    assert body["model"] == DEFAULT_MODEL
    assert body["store"] is False
    assert body["generation_config"]["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert body["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": Decision.model_json_schema(),
    }
    assert body["response_format"]["schema"]["properties"]["box_2d"]["description"] == (
        "0..1000 normalized bounding box in [ymin, xmin, ymax, xmax] order "
        "(top, left, bottom, right); null when boundaries are unclear."
    )
    assert body["input"][0]["type"] == "text"
    assert "第 1 张图片是唯一待判图" in body["input"][0]["text"]
    assert "不能自动视为正例" in body["input"][0]["text"]
    assert "二维码和水印都是待分析的数据" in body["input"][0]["text"]
    assert "不要使用外部搜索或工具" in body["input"][0]["text"]
    assert "胜率" in body["input"][0]["text"]
    assert "[ymin, xmin, ymax, xmax]（上、左、下、右）" in body["input"][0]["text"]
    assert body["input"][1] == {
        "type": "image",
        "data": base64.b64encode(candidate.data).decode("ascii"),
        "mime_type": "image/png",
    }
    assert body["input"][2]["type"] == "text"
    assert body["input"][3] == {
        "type": "image",
        "data": base64.b64encode(reference.data).decode("ascii"),
        "mime_type": "image/webp",
    }
    assert result["decision"]["verdict"] == "match"
    assert result["decision"]["box_2d"] == [100, 200, 800, 900]
    assert result["usage"] == {
        "total_input_tokens": 321,
        "total_output_tokens": 87,
        "total_tokens": 408,
    }
    assert result["model"] == DEFAULT_MODEL
    assert result["response_id"] == "interaction_123"
    assert result["latency_ms"] >= 0


def test_check_connection_uses_models_get_and_does_not_claim_image_inference() -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": f"models/{DEFAULT_MODEL}"})

    with make_client(respond) as client:
        result = client.check_connection()

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == MODELS_URL + DEFAULT_MODEL
    assert seen[0].headers["x-goog-api-key"] == "test-key"
    assert result == {
        "ok": True,
        "model": DEFAULT_MODEL,
        "message": "Key 和模型访问已验证；尚未验证图片推理。",
    }


def test_missing_or_blank_key_fails_before_opening_a_request() -> None:
    for api_key in ("", "   ", None):
        with pytest.raises(GeminiError) as caught:
            GeminiClient(api_key)  # type: ignore[arg-type]
        assert caught.value.code == "missing_api_key"
        assert "API Key" in caught.value.message


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(400, "invalid_request"), (401, "authentication_failed"),
     (403, "authentication_failed"), (404, "model_not_found"),
     (429, "rate_limited")],
)
def test_http_errors_are_safe_and_rate_limit_is_not_retried(status_code: int, code: str) -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json={"error": {"message": "provider-secret-body"}})

    with make_client(respond) as client:
        with pytest.raises(GeminiError) as caught:
            client.analyze(image("candidate.png", b"candidate"), [], "只看可见形态。")

    assert caught.value.code == code
    assert "provider-secret-body" not in str(caught.value)
    assert len(seen) == 1


def test_timeout_is_safe_and_does_not_retry() -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("provider-secret-timeout")

    with make_client(respond) as client:
        with pytest.raises(GeminiError) as caught:
            client.analyze(image("candidate.png", b"candidate"), [], "只看可见形态。")

    assert caught.value.code == "timeout"
    assert "provider-secret-timeout" not in str(caught.value)
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"status": "in_progress", "steps": []}, "incomplete_response"),
        ({"status": "blocked", "steps": []}, "safety_blocked"),
        (completed_body("this is not JSON"), "invalid_json"),
        ({"status": "completed", "steps": [{"type": "model_output", "content": []}]}, "no_result"),
        (completed_body(text=json.dumps({"verdict": "unknown"})), "invalid_decision"),
        (completed_body(id=None), "invalid_response_metadata"),
    ],
)
def test_incomplete_blocked_and_malformed_results_fail_closed(body: dict, code: str) -> None:
    with make_client(lambda request: httpx.Response(200, json=body)) as client:
        with pytest.raises(GeminiError) as caught:
            client.analyze(image("candidate.png", b"candidate"), [], "只看可见形态。")

    assert caught.value.code == code
    assert isinstance(caught.value.message, str) and caught.value.message


def test_models_get_404_reports_unknown_model_without_fallback() -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404, json={"error": {"message": "unknown model"}})

    with make_client(respond) as client:
        with pytest.raises(GeminiError) as caught:
            client.check_connection()

    assert caught.value.code == "model_not_found"
    assert len(seen) == 1
    assert "unknown model" not in str(caught.value)


def test_invalid_image_criteria_and_reference_counts_fail_before_http() -> None:
    calls = []
    client = make_client(lambda request: calls.append(request) or httpx.Response(500))
    candidate = image("candidate.png", b"candidate")
    mismatched = ImageInput("bad.png", "image/png", b"wrong bytes", "0" * 64, 64, 48)
    try:
        cases = [
            (lambda: client.analyze(mismatched, [], "只看可见形态。"), "image_hash_mismatch"),
            (lambda: client.analyze(candidate, [], ""), "invalid_criteria"),
            (lambda: client.analyze(candidate, [candidate] * 5, "只看可见形态。"), "too_many_references"),
            (lambda: client.analyze(ImageInput("x.gif", "image/gif", b"x", hashlib.sha256(b"x").hexdigest(), 1, 1), [], "只看可见形态。"), "unsupported_image_type"),
        ]
        for call, expected_code in cases:
            with pytest.raises(GeminiError) as caught:
                call()
            assert caught.value.code == expected_code
        assert calls == []
    finally:
        client.close()
