"""Offline contract tests for the bounded Alibaba Qwen-VL adapter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import base64
import hashlib
import json

import httpx
import pytest

from yoyo.vision_research.comparison_input import build_market_packet
from yoyo.vision_research.schemas import ImageInput
from yoyo.vision_research.source import chart_sha256
from yoyo.vision_research.qwen import (
    CONNECTION_TEST_MAX_TOKENS,
    DEFAULT_QWEN_MODEL,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    QWEN_MODELS,
    QwenClient,
    QwenError,
    REGION_ENDPOINTS,
)


def image(name: str, data: bytes) -> ImageInput:
    return ImageInput(name, "image/png", data, hashlib.sha256(data).hexdigest(), 64, 48)


def decision_text(**overrides: object) -> str:
    decision = {
        "assessment_scope": "current_right_edge",
        "current_state": "launching",
        "verdict": "match",
        "side": "long",
        "summary": "当前右端均线收拢后开始向上扩张。",
        "evidence": ["右侧均线束开始离散"],
        "risks": [],
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
        "id": "chatcmpl-qwen-test-1",
        "model": DEFAULT_QWEN_MODEL,
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {"prompt_tokens": 210, "completion_tokens": 70, "total_tokens": 280},
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return payload


def make_client(handler, *, model: str = DEFAULT_QWEN_MODEL,
                region: str = "beijing") -> QwenClient:
    return QwenClient("test-api-key", model=model, region=region,
                      transport=httpx.MockTransport(handler))


def observation() -> dict:
    cursor_ms = 1_750_032_000_000
    duration_ms = 900_000
    candles = []
    for index in range(120):
        close = 100.0 + index / 10
        candles.append({
            "t": cursor_ms - (120 - index) * duration_ms,
            "o": close,
            "h": close + 1,
            "l": close - 1,
            "c": close + 0.1,
            "sma20": close - 0.2,
            "ema20": close - 0.1,
            "sma60": close - 0.4,
            "ema60": close - 0.3,
            "sma120": close - 0.6,
            "ema120": close - 0.5,
        })
    digest = chart_sha256(candles)
    return {
        "id": "frozen-qwen-observation",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "cursor_ms": cursor_ms,
        "chart_sha256": digest,
        "chart": {
            "candles": candles,
            "chart_sha256": digest,
            "provenance": {
                "symbol": "ETH-USDT-SWAP",
                "timeframe": "15m",
                "time_boundary": "historical_replay",
                "observed_at_ms": cursor_ms,
                "visible_end_ms": cursor_ms,
                "visible_start_ms": candles[0]["t"],
                "last_bar_closed": True,
                "bar_count": 120,
            },
        },
    }


def context_for(packet: dict) -> dict:
    zone = timezone(timedelta(hours=8))
    iso = lambda value: datetime.fromtimestamp(value / 1000, zone).isoformat()
    return {
        "assessment_scope": "current_right_edge",
        "time_boundary": "historical_replay",
        "timezone": "Asia/Shanghai",
        "symbol": packet["symbol"],
        "timeframe": packet["timeframe"],
        "last_bar_closed": True,
        "observed_at": iso(packet["cursor_ms"]),
        "rightmost_bar_open_at": iso(packet["rows"][-1][0]),
    }


@pytest.mark.parametrize("region", ["beijing", "singapore"])
def test_analyze_sends_bounded_json_mode_and_png_data_urls_to_region_endpoint(region: str) -> None:
    candidate = image("candidate.png", b"candidate-bytes")
    references = [image("ref-a.png", b"reference-a"), image("ref-b.png", b"reference-b")]
    seen: list[tuple[httpx.Request, dict]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append((request, json.loads(request.content)))
        return httpx.Response(200, json=completion())

    with make_client(respond, region=region) as adapter:
        result = adapter.analyze(candidate, references, "仅判断图片最右端当前形态。")

    assert len(seen) == 1
    request, body = seen[0]
    assert request.method == "POST"
    assert str(request.url) == REGION_ENDPOINTS[region]
    assert request.headers["authorization"] == "Bearer test-api-key"
    assert body["model"] == DEFAULT_QWEN_MODEL
    assert body["stream"] is False
    assert body["max_tokens"] == MAX_OUTPUT_TOKENS == 4096
    assert body["enable_thinking"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert "tools" not in body

    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert "JSON Schema" in parts[0]["text"]
    assert "本次任务是检测待判图最右端的当前盘口" in parts[0]["text"]
    assert "不能把右端变化排除为“事后走势”" in parts[0]["text"]
    image_parts = [part for part in parts if part["type"] == "image_url"]
    assert len(image_parts) == 3
    assert [part["image_url"]["url"] for part in image_parts] == [
        "data:image/png;base64," + base64.b64encode(value.data).decode("ascii")
        for value in [candidate, *references]
    ]
    assert "第 1 张参考图片" in parts[2]["text"]
    assert result["decision"]["verdict"] == "match"
    assert result["decision"]["box_2d"] == [100, 200, 800, 900]
    assert result["usage"] == {"prompt_tokens": 210, "completion_tokens": 70, "total_tokens": 280}
    assert result["response_id"] == "chatcmpl-qwen-test-1"
    assert adapter.provider == "qwen"
    assert adapter.region == region
    assert adapter.prompt_version == PROMPT_VERSION


def test_legacy_vl_max_keeps_json_mode_and_uses_only_supported_model_fields() -> None:
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    with make_client(respond, model="qwen-vl-max") as adapter:
        adapter.analyze(image("candidate.png", b"candidate"), [], "只判断当前右端。")

    assert len(bodies) == 1
    assert bodies[0]["model"] == "qwen-vl-max"
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in bodies[0]
    assert bodies[0]["max_tokens"] <= 8192


def test_comparison_reuses_frozen_input_validation_and_emits_schema_json_mode() -> None:
    packet = build_market_packet(observation())
    image_input = image("frozen.png", b"frozen-chart")
    captured = []

    def respond(request):
        captured.append((request, json.loads(request.content)))
        return httpx.Response(200, json=completion(decision_text(box_2d=None)))

    with make_client(respond, region="singapore") as adapter:
        result = adapter.analyze_comparison(
            input_mode="hybrid", image=image_input, market_packet=packet,
            criteria="只按可见的冻结行情判断。", context=context_for(packet),
        )

    assert len(captured) == 1
    request, body = captured[0]
    assert str(request.url) == REGION_ENDPOINTS["singapore"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["enable_thinking"] is False
    parts = body["messages"][0]["content"]
    assert "冻结行情数值" in parts[-1]["text"]
    assert any(part["type"] == "image_url" for part in parts)
    assert result["decision"]["box_2d"] is None
    assert result["input_mode"] == "hybrid"
    assert result["reference_scope"] == "excluded_for_ablation"


@pytest.mark.parametrize("region", ["beijing", "singapore"])
def test_connection_check_uses_region_host_small_budget_and_no_json_mode(region: str) -> None:
    captured = []

    def respond(request):
        captured.append((request, json.loads(request.content)))
        return httpx.Response(200, json=completion("OK"))

    with make_client(respond, region=region) as adapter:
        result = adapter.check_connection()

    request, body = captured[0]
    assert str(request.url) == REGION_ENDPOINTS[region]
    assert body["max_tokens"] == CONNECTION_TEST_MAX_TOKENS == 128
    assert body["enable_thinking"] is False
    assert "response_format" not in body
    assert "百炼 API Key" in result["message"]
    assert "尚未验证图片审阅" in result["message"]


def test_http_provider_errors_are_allowlisted_redacted_and_never_retried() -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {
            "code": "invalid_api_key",
            "type": "private raw body test-api-key",
            "message": "private raw body test-api-key",
        }})

    with make_client(respond) as adapter:
        with pytest.raises(QwenError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], "只判断当前右端。")

    assert len(calls) == 1
    assert caught.value.code == "authentication_failed"
    assert caught.value.provider_code == "invalid_api_key"
    assert caught.value.diagnostics() == {
        "code": "authentication_failed", "http_status": 401,
        "provider_code": "invalid_api_key",
    }
    assert "private raw body" not in str(caught.value)
    assert "test-api-key" not in str(caught.value)
    assert "private raw body" not in json.dumps(caught.value.diagnostics())
    assert "test-api-key" not in adapter.last_exchange["response"]["body_text"]


def test_unknown_provider_error_code_is_not_exposed() -> None:
    def respond(request):
        return httpx.Response(400, json={"error": {
            "code": "private-future-code", "message": "private raw provider body",
        }})

    with make_client(respond) as adapter:
        with pytest.raises(QwenError) as caught:
            adapter.check_connection()

    assert caught.value.code == "invalid_request"
    assert caught.value.provider_code is None
    assert "private-future-code" not in str(caught.value)
    assert "private raw provider body" not in str(caught.value)


@pytest.mark.parametrize(("status", "provider_code", "expected_code"), [
    (429, "Throttling.RateQuota", "rate_limited"),
    (429, "Throttling.AllocationQuota", "quota_exceeded"),
    (400, "DataInspectionFailed", "safety_blocked"),
    (404, "model_not_found", "model_not_found"),
])
def test_documented_provider_errors_map_to_safe_categories(
        status: int, provider_code: str, expected_code: str) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {
            "code": provider_code, "message": "private provider explanation",
        }})

    with make_client(respond) as adapter:
        with pytest.raises(QwenError) as caught:
            adapter.check_connection()

    assert len(calls) == 1
    assert caught.value.code == expected_code
    assert caught.value.provider_code == provider_code
    assert "private provider explanation" not in str(caught.value)


def test_redirect_to_wrong_host_is_rejected_without_following_or_resending_key() -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://attacker.invalid/collect"})

    with make_client(respond, region="singapore") as adapter:
        with pytest.raises(QwenError) as caught:
            adapter.check_connection()

    assert caught.value.code == "redirect_rejected"
    assert len(calls) == 1
    assert str(calls[0].url) == REGION_ENDPOINTS["singapore"]
    assert calls[0].headers["authorization"] == "Bearer test-api-key"


@pytest.mark.parametrize(("finish_reason", "expected_code"), [
    ("length", "truncated_response"),
    ("private-future-state", "incomplete_response"),
    (None, "incomplete_response"),
])
def test_only_stop_finish_is_accepted_and_truncation_is_not_retried(
        finish_reason: object, expected_code: str) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=completion(finish_reason=finish_reason))

    with make_client(respond) as adapter:
        with pytest.raises(QwenError) as caught:
            adapter.analyze(image("candidate.png", b"candidate"), [], "只判断当前右端。")

    assert len(calls) == 1
    assert caught.value.code == expected_code
    assert "通义千问" in str(caught.value)


def test_invalid_region_or_model_fails_before_transport() -> None:
    with pytest.raises(QwenError) as bad_region:
        QwenClient("test-api-key", region="https://evil.invalid")
    with pytest.raises(QwenError) as bad_model:
        QwenClient("test-api-key", model="qwen3-vl-max")
    assert bad_region.value.code == "invalid_region"
    assert bad_model.value.code == "invalid_model"
    assert QWEN_MODELS == {"qwen3-vl-plus", "qwen-vl-max"}
