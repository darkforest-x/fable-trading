"""Causal rendering and read-only SPIKE reuse regressions."""
import copy

import httpx
import pytest

from yoyo.vision_research.source import SpikeSource, SourceError, causal_candles, render_chart


def market():
    bars = [{"t": i * 900000, "o": 100 + i / 10, "c": 101 + i / 10,
             "h": 102 + i / 10, "l": 99 + i / 10, "ema20": 100 + i / 10}
            for i in range(80)]
    signal = {"id": "signal-a", "symbol": "TEST-USDT", "timeframe": "15m",
              "bar_close_ms": 60 * 900000, "timeframe_min": 15, "price": 107,
              "side": "long", "is_closed": True, "performance": {"future_return": 9}}
    return {"symbol": "TEST-USDT", "timeframe": "15m", "candles": bars}, signal


def test_future_price_and_axis_mutations_do_not_change_input_bytes():
    payload, signal = market()
    before = causal_candles(payload, signal)
    changed = copy.deepcopy(payload)
    for row in changed["candles"][60:]:
        row.update(o=1e9, h=2e9, l=1e8, c=1e9, ema20=1e12)
    after = causal_candles(changed, signal)
    assert len(before) == 60 and before[-1]["t"] == 59 * 900000
    assert before == after
    assert render_chart(before, "TEST", "15m", signal["bar_close_ms"]) == render_chart(after, "TEST", "15m", signal["bar_close_ms"])


@pytest.mark.parametrize("mutation", ["gap", "missing_tip", "unsorted", "short"])
def test_incomplete_decision_window_is_rejected(mutation):
    payload, signal = market()
    if mutation == "gap":
        del payload["candles"][40]
    elif mutation == "missing_tip":
        del payload["candles"][59]
    elif mutation == "unsorted":
        payload["candles"].reverse()
    else:
        payload["candles"] = payload["candles"][40:]
    with pytest.raises(SourceError):
        causal_candles(payload, signal)


def test_source_only_gets_whitelisted_data_and_freezes_image():
    payload, signal = market()
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"items": [signal]} if request.url.path == "/api/signals" else payload)

    source = SpikeSource(transport=httpx.MockTransport(handle))
    item = source.list_signals()["items"][0]
    assert "performance" not in item and "is_closed" not in item
    image, provenance = source.signal_image(item["id"])
    payload["candles"][0]["h"] = 99999
    assert source.signal_image(item["id"])[0].sha256 == image.sha256
    assert provenance["visible_end_ms"] == signal["bar_close_ms"]
    assert provenance["overlay"] == "none"
    assert calls == [("GET", "/api/signals"), ("GET", "/api/chart")]


def test_source_failure_does_not_substitute_a_different_source():
    source = SpikeSource(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    assert source.list_signals()["items"] == []
    assert source.status()["available"] is False
    with pytest.raises(SourceError):
        source.signal_image("missing")
    with pytest.raises(ValueError):
        SpikeSource("https://example.com")
