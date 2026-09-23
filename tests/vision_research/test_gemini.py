"""Gemini REST boundary tests; all HTTP responses use MockTransport."""
from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from yoyo.vision_research.gemini import (
    DEFAULT_MODEL,
    INTERACTIONS_URL,
    MAX_OUTPUT_TOKENS,
    MODELS_URL,
    GeminiClient,
    GeminiError,
)
from yoyo.vision_research.schemas import Decision, ImageInput


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
    assert caught.value.http_status == status_code
    assert f"HTTP {status_code}" in str(caught.value)
    assert "provider-secret-body" not in str(caught.value)
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("status", "provider_code", "expected_code", "hint"),
    [(402, "payment_required", "payment_required", "预付款余额不足"),
     (416, "out_of_range", "out_of_range", "超出允许范围"),
     (422, "invalid_request", "invalid_request", "参数"),
     (400, "failed_precondition", "invalid_request", "结算"),
     (429, "quota_exceeded", "rate_limited", "配额已用完")],
)
def test_provider_diagnostics_distinguish_billing_and_request_errors(status, provider_code, expected_code, hint):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {
            "code": provider_code, "message": "provider-secret-body", "details": "test-key"}})

    with make_client(respond) as client:
        with pytest.raises(GeminiError) as caught:
            client.analyze(image("candidate.png", b"candidate"), [], "只看可见形态。")
    exc = caught.value
    assert exc.diagnostics() == {"code": expected_code, "http_status": status, "provider_code": provider_code}
    assert hint in str(exc) and f"HTTP {status}" in str(exc)
    assert "provider-secret-body" not in str(exc) and "test-key" not in str(exc)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [
    {"error": {"code": "provider-secret-code", "message": "provider-secret-message"}},
    {"error": []}, ["provider-secret-value"], None,
])
def test_unknown_http_errors_keep_status_without_reflecting_untrusted_fields(body):
    with make_client(lambda request: httpx.Response(451, json=body)) as client:
        with pytest.raises(GeminiError) as caught:
            client.check_connection()
    assert caught.value.diagnostics() == {"code": "provider_error", "http_status": 451, "provider_code": None}
    assert "HTTP 451" in str(caught.value)
    assert "provider-secret" not in str(caught.value)


def test_non_json_payment_error_still_reports_http_402():
    with make_client(lambda request: httpx.Response(402, text="private-html")) as client:
        with pytest.raises(GeminiError) as caught:
            client.check_connection()
    assert caught.value.http_status == 402
    assert caught.value.provider_code is None
    assert "预付款余额不足" in str(caught.value)
    assert "private-html" not in str(caught.value)


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
            (lambda: client.analyze(candidate, [candidate] * 3600, "只看可见形态。"), "too_many_references"),
            (lambda: client.analyze(ImageInput("x.gif", "image/gif", b"x", hashlib.sha256(b"x").hexdigest(), 1, 1), [], "只看可见形态。"), "unsupported_image_type"),
        ]
        for call, expected_code in cases:
            with pytest.raises(GeminiError) as caught:
                call()
            assert caught.value.code == expected_code
        assert calls == []
    finally:
        client.close()


def test_six_references_are_sent_in_order_without_the_old_four_image_cap():
    observed = []
    references = [image(f"reference-{i}.png", f"reference {i}".encode()) for i in range(6)]
    def respond(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json=completed_body())
    with make_client(respond) as client:
        client.analyze(image("candidate.png", b"candidate"), references, "只判断可见均线形态。")
    images = [part for part in observed[0]["input"] if part["type"] == "image"]
    assert len(images) == 7
    assert [base64.b64decode(part["data"]) for part in images[1:]] == [ref.data for ref in references]
