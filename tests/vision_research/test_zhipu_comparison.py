"""Offline request and decision checks for replay modality ablations."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import base64
import copy
import hashlib
import json

import httpx
import pytest

from yoyo.vision_research.comparison_input import (
    COLUMNS,
    build_market_packet,
    market_data_json,
    packet_sha256,
)
from yoyo.vision_research.schemas import DEFAULT_MODEL, ImageInput
from yoyo.vision_research.source import chart_sha256
from yoyo.vision_research.zhipu import (
    MAX_OUTPUT_TOKENS,
    ZhipuClient,
    ZhipuError,
)


CURSOR_MS = 1_750_032_000_000
DURATION_MS = 900_000
CRITERIA = "只按当前可见形态分类；不要依据未来结果。"


def image(name="frozen.png", data=b"frozen-chart-pixels") -> ImageInput:
    return ImageInput(name, "image/png", data, hashlib.sha256(data).hexdigest(), 1440, 800)


def observation() -> dict:
    candles = []
    for index in range(120):
        close = 100.0 + index / 10
        candles.append({
            "t": CURSOR_MS - (120 - index) * DURATION_MS,
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
        "id": "frozen-observation",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "cursor_ms": CURSOR_MS,
        "chart_sha256": digest,
        "chart": {
            "candles": candles,
            "chart_sha256": digest,
            "provenance": {
                "symbol": "ETH-USDT-SWAP",
                "timeframe": "15m",
                "time_boundary": "historical_replay",
                "observed_at_ms": CURSOR_MS,
                "visible_end_ms": CURSOR_MS,
                "visible_start_ms": candles[0]["t"],
                "last_bar_closed": True,
                "bar_count": 120,
            },
        },
    }


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).isoformat()


def context_for(packet=None) -> dict:
    packet = packet or build_market_packet(observation())
    return {
        "assessment_scope": "current_right_edge",
        "time_boundary": "historical_replay",
        "timezone": "Asia/Shanghai",
        "symbol": packet["symbol"],
        "timeframe": packet["timeframe"],
        "last_bar_closed": True,
        "observed_at": iso_ms(packet["cursor_ms"]),
        "rightmost_bar_open_at": iso_ms(packet["rows"][-1][0]),
    }


def decision_text(**overrides) -> str:
    decision = {
        "assessment_scope": "current_right_edge",
        "current_state": "launching",
        "verdict": "match",
        "side": "long",
        "summary": "当前右端出现与密集核心相连的启动。",
        "evidence": ["右端均线束开始离散"],
        "risks": [],
        "box_2d": None,
    }
    decision.update(overrides)
    return json.dumps(decision, ensure_ascii=False)


def completion(text=None, finish_reason="stop"):
    return {
        "id": "chatcmpl-comparison-1",
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": decision_text() if text is None else text}}],
        "usage": {"prompt_tokens": 210, "completion_tokens": 45, "total_tokens": 255},
    }


def make_client(handler):
    return ZhipuClient("comparison-test-key", model=DEFAULT_MODEL, transport=httpx.MockTransport(handler))


def test_three_modes_send_only_their_target_encoding_and_share_the_same_rules_packet():
    packet = build_market_packet(observation())
    candidate = image()
    captured = []

    def respond(request):
        captured.append((request, json.loads(request.content)))
        return httpx.Response(200, json=completion())

    with make_client(respond) as adapter:
        vision = adapter.analyze_comparison(input_mode="vision", image=candidate,
                                            market_packet=packet, criteria=CRITERIA,
                                            context=context_for(packet))
        vision_trace = copy.deepcopy(adapter.last_exchange)
        text = adapter.analyze_comparison(input_mode="text", image=None,
                                          market_packet=packet, criteria=CRITERIA,
                                          context=context_for(packet))
        text_trace = copy.deepcopy(adapter.last_exchange)
        hybrid = adapter.analyze_comparison(input_mode="hybrid", image=candidate,
                                            market_packet=packet, criteria=CRITERIA,
                                            context=context_for(packet))
        hybrid_trace = copy.deepcopy(adapter.last_exchange)

    assert len(captured) == 3
    request_bodies = [body for _, body in captured]
    parts = [body["messages"][0]["content"] for body in request_bodies]
    expected_data = market_data_json(packet)
    assert parts[0][0]["text"] == parts[1][0]["text"] == parts[2][0]["text"]
    assert json.dumps(CRITERIA, ensure_ascii=False) in parts[0][0]["text"]
    assert "box_2d" in parts[0][0]["text"]

    image_parts = [[part for part in content if part["type"] == "image_url"] for content in parts]
    assert [len(items) for items in image_parts] == [1, 0, 1]
    assert image_parts[0][0]["image_url"]["url"] == image_parts[2][0]["image_url"]["url"]
    assert image_parts[0][0]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(candidate.data).decode("ascii")
    )

    text_data = [content[-1]["text"] if content[-1]["type"] == "text" else ""
                 for content in parts]
    assert "冻结行情数值" not in text_data[0]
    assert expected_data not in text_data[0]
    assert text_data[1].endswith(expected_data)
    assert text_data[2].endswith(expected_data)
    assert json.loads(text_data[1].split("\n", 1)[1]) == json.loads(expected_data)
    assert json.loads(text_data[2].split("\n", 1)[1]) == json.loads(expected_data)
    assert set(json.loads(expected_data)) == {"columns", "rows"}
    assert json.loads(expected_data)["columns"] == list(COLUMNS)
    assert len(json.loads(expected_data)["rows"]) == 120
    assert "human" not in expected_data and "outcome" not in expected_data
    assert "references" not in str(request_bodies)
    assert [trace["image_count"] for trace in (vision_trace, text_trace, hybrid_trace)] == [1, 0, 1]
    assert [body["max_tokens"] for body in request_bodies] == [MAX_OUTPUT_TOKENS] * 3
    assert all(body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "max"
               for body in request_bodies)
    assert vision["decision"]["box_2d"] is text["decision"]["box_2d"] is hybrid["decision"]["box_2d"] is None
    assert [vision["input_mode"], text["input_mode"], hybrid["input_mode"]] == ["vision", "text", "hybrid"]
    assert all(result["market_packet_sha256"] == packet_sha256(packet)
               for result in (vision, text, hybrid))
    assert all(result["reference_scope"] == "excluded_for_ablation"
               for result in (vision, text, hybrid))


@pytest.mark.parametrize(("input_mode", "image_value"), [
    ("text", image()),
    ("vision", None),
    ("hybrid", None),
    ("vision+text", None),
])
def test_invalid_mode_or_image_combination_fails_before_network(input_mode, image_value):
    calls = []
    with make_client(lambda request: calls.append(request) or httpx.Response(200, json=completion())) as adapter:
        with pytest.raises(ZhipuError):
            adapter.analyze_comparison(input_mode=input_mode, image=image_value,
                                       market_packet=build_market_packet(observation()),
                                       criteria=CRITERIA, context=context_for())
    assert calls == []


def test_packet_and_context_cannot_smuggle_outcomes_or_future_fields():
    packet = build_market_packet(observation())
    invalid_packet = {**packet, "net_r": 99}
    invalid_context = {**context_for(packet), "human": {"current_state": "launching"}}
    calls = []
    with make_client(lambda request: calls.append(request) or httpx.Response(200, json=completion())) as adapter:
        for candidate_packet, candidate_context in ((invalid_packet, context_for(packet)),
                                                     (packet, invalid_context)):
            with pytest.raises(ZhipuError) as caught:
                adapter.analyze_comparison(input_mode="text", image=None,
                                           market_packet=candidate_packet, criteria=CRITERIA,
                                           context=candidate_context)
            assert caught.value.code == "invalid_comparison_input"
    assert calls == []


@pytest.mark.parametrize(("raw_decision", "finish_reason", "expected_code"), [
    (decision_text(box_2d=[100, 100, 200, 200]), "stop", "invalid_decision"),
    ("{\"assessment_scope\":", "stop", "invalid_json"),
    (decision_text(), "length", "truncated_response"),
])
def test_invalid_box_and_truncated_response_are_saved_and_never_retried(
        raw_decision, finish_reason, expected_code):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=completion(raw_decision, finish_reason=finish_reason))

    packet = build_market_packet(observation())
    with make_client(respond) as adapter:
        with pytest.raises(ZhipuError) as caught:
            adapter.analyze_comparison(input_mode="text", image=None,
                                       market_packet=packet, criteria=CRITERIA,
                                       context=context_for(packet))
        assert caught.value.code == expected_code
        trace = adapter.last_exchange
        assert trace["response"]["body_text"]
        assert "comparison-test-key" not in trace["request"]["body_text"]
    assert len(seen) == 1
