"""Live research timing, MA continuity and immutable inference snapshots."""
import copy

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.live import LiveMarket, MARKET_URL, live_rows
from yoyo.vision_research.server import create_app
from yoyo.vision_research.source import SourceError, SpikeSource
from test_app import FakeProvider, configure, image_url

PERIOD = 900_000


def fixture_market():
    # Deliberately nonlinear prices expose the difference between continuing
    # the original EMA recurrence and reseeding it from a trimmed chart.
    values = [100 + i / 10 + (i % 7) for i in range(243)]
    averages = {}
    prices = pd.Series(values)
    for n in (20, 60, 120):
        averages[f"sma{n}"] = prices.rolling(n).mean()
        averages[f"ema{n}"] = prices.ewm(alpha=2 / (n + 1), adjust=False).mean()
    rows = [dict(t=i * PERIOD, o=p - 1, h=p + 2, l=p - 2, c=p,
                 **{key: float(series[i]) for key, series in averages.items()}) for i, p in enumerate(values)]
    signal = dict(id="live-test", symbol="TEST-USDT-SWAP", timeframe="15m", timeframe_min=15,
                  side="long", price=values[239], bar_close_ms=240 * PERIOD, is_closed=True)
    payload = dict(symbol=signal["symbol"], timeframe="15m", candles=rows[120:240])
    raw = [[str(r["t"]), *[str(r[k]) for k in ("o", "h", "l", "c")], "1", "1", "1",
            "0" if i == 242 else "1"] for i, r in enumerate(rows)]
    return payload, signal, list(reversed(raw)), rows


def source_fixture():
    payload, signal, raw, expected = fixture_market()
    clock = [242 * PERIOD + 45_000]
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": [signal]} if request.url.path == "/api/signals" else payload)

    class Market:
        def candles(self, _signal):
            assert _signal["symbol"] == signal["symbol"]
            return raw

    source = SpikeSource(transport=httpx.MockTransport(handle), live_market=Market(), clock=lambda: clock[0])
    return source, clock, raw, calls, expected


def test_live_tip_and_ma_values_match_full_history_not_reseeded_ema():
    payload, signal, raw, expected = fixture_market()
    observed = 242 * PERIOD + 45_000
    rows, stale = live_rows(payload, signal, raw, observed)
    assert len(rows) == 120 and rows[-1]["t"] == 242 * PERIOD and not stale
    assert rows[-1]["is_closed"] is False and all(r["is_closed"] for r in rows[:-1])
    for row in rows:
        original = expected[row["t"] // PERIOD]
        for n in (20, 60, 120):
            assert row[f"sma{n}"] == pytest.approx(original[f"sma{n}"])
            assert row[f"ema{n}"] == pytest.approx(original[f"ema{n}"])
    # Provisional ticks recompute from the confirmed seed, not the prior tick.
    raw[0][4] = str(float(raw[0][4]) + 1)
    newer, _ = live_rows(payload, signal, raw, observed + 10_000)
    for n in (20, 60, 120):
        assert newer[-1][f"ema{n}"] - rows[-1][f"ema{n}"] == pytest.approx(2 / (n + 1))


@pytest.mark.parametrize("mutation", ["gap", "revision", "nan", "future", "unconfirmed_history", "duplicate"])
def test_bad_live_data_is_not_substituted_or_fabricated(mutation):
    payload, signal, raw, _ = fixture_market()
    if mutation == "gap":
        del raw[1]
    elif mutation == "revision":
        raw[3][4] = str(float(raw[3][4]) + 1)
    elif mutation == "nan":
        raw[0][4] = "nan"
    elif mutation == "future":
        raw[0][0] = str(244 * PERIOD)
    elif mutation == "unconfirmed_history":
        raw[1][8] = "0"
    else:
        row = copy.deepcopy(raw[0]); row[4] = str(float(row[4]) + 1); raw.append(row)
    with pytest.raises(SourceError):
        live_rows(payload, signal, raw, 242 * PERIOD + 45_000)


def test_polling_updates_but_original_signal_chart_and_snapshots_stay_frozen():
    source, clock, raw, calls, _ = source_fixture()
    original = source.signal_chart("live-test")
    first = source.live_chart("live-test")
    raw[0][4] = str(float(raw[0][4]) + 1)
    clock[0] += 10_000
    second = source.live_chart("live-test")
    assert second["chart_sha256"] != first["chart_sha256"]
    assert source.chart_snapshot("live-test", first["snapshot_id"]) == first
    assert source.signal_chart("live-test") == original
    assert first["provenance"]["signal_bar_close_ms"] == 240 * PERIOD
    assert first["provenance"]["visible_end_ms"] == first["provenance"]["observed_at_ms"]
    assert first["provenance"]["visible_end_ms"] < first["provenance"]["last_bar_close_ms"]
    assert first["provenance"]["time_boundary"] == "live_observation"
    assert calls.count("/api/chart") == 3
    first["candles"][-1]["c"] = -1
    assert source.chart_snapshot("live-test", first["snapshot_id"])["candles"][-1]["c"] > 0


def test_window_expiry_and_snapshot_ttl_are_checked_when_submitted():
    source, clock, _, _, _ = source_fixture()
    first = source.live_chart("live-test", 12)
    clock[0] += 90_001
    with pytest.raises(SourceError, match="90 秒"):
        source.chart_snapshot("live-test", first["snapshot_id"])
    source, clock, _, _, _ = source_fixture()
    clock[0] = 243 * PERIOD - 10_000
    first = source.live_chart("live-test", 3)
    assert first["provenance"]["recognition_eligible"]
    clock[0] = first["provenance"]["recognition_expires_ms"]
    with pytest.raises(SourceError, match="超出信号后"):
        source.chart_snapshot("live-test", first["snapshot_id"])
    with pytest.raises(SourceError):
        source.chart_snapshot("different", first["snapshot_id"])
    with pytest.raises(SourceError):
        source.live_chart("live-test", 97)
    # A short observation window expires while the chart remains viewable.
    source, clock, _, _, _ = source_fixture()
    expired = source.live_chart("live-test", 1)
    assert not expired["provenance"]["recognition_eligible"]


def test_stale_source_does_not_become_eligible_because_it_was_just_fetched():
    source, clock, raw, _, _ = source_fixture()
    raw.pop(0)  # Latest confirmed close is 242*PERIOD; current bar missing.
    chart = source.live_chart("live-test")
    assert chart["provenance"]["source_stale"]
    assert chart["provenance"]["visible_end_ms"] == 242 * PERIOD
    assert not chart["provenance"]["recognition_eligible"]
    with pytest.raises(SourceError):
        source.chart_snapshot("live-test", chart["snapshot_id"])


def test_live_api_uses_selected_snapshot_after_newer_poll_and_exports_actual_time(tmp_path):
    source, clock, raw, _, _ = source_fixture()
    FakeProvider.failure = False
    with TestClient(create_app(tmp_path, source=source, provider_factory=FakeProvider), base_url="http://127.0.0.1") as client:
        configure(client)
        first = client.get("/api/signals/live-test/chart?mode=live&post_signal_bars=12").json()
        raw[0][4] = str(float(raw[0][4]) + 1)
        clock[0] += 10_000
        second = client.get("/api/signals/live-test/chart?mode=live").json()
        assert first["chart_sha256"] != second["chart_sha256"]
        body = dict(signal_id="live-test", chart_snapshot_id=first["snapshot_id"],
                    expected_chart_sha256=first["chart_sha256"], chart_capture_data_url=image_url())
        response = client.post("/api/analyze", json=body)
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["status"] == "completed"
        assert run["provenance"]["chart_candles"] == first["candles"]
        assert run["provenance"]["observed_at_ms"] == first["provenance"]["observed_at_ms"]
        assert run["provenance"]["chart_snapshot_id"] == first["snapshot_id"]
        assert not run["production_eligible"] and not run["training_eligible"]
        assert client.get(f"/api/runs/{run['id']}/export").json()["provenance"] == run["provenance"]
        count = len(client.get("/api/runs").json()["items"])
        body["expected_chart_sha256"] = second["chart_sha256"]
        assert client.post("/api/analyze", json=body).status_code == 409
        body["chart_snapshot_id"] = "0" * 32
        assert client.post("/api/analyze", json=body).status_code == 409
        assert len(client.get("/api/runs").json()["items"]) == count
        assert client.get("/api/signals/live-test/chart?mode=live&post_signal_bars=0").status_code == 422
        del body["chart_capture_data_url"]
        assert client.post("/api/analyze", json=body).status_code == 400


def test_public_market_adapter_only_calls_fixed_okx_read_endpoint():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "0", "data": []})
    market = LiveMarket(transport=httpx.MockTransport(handle))
    assert market.candles({"symbol": "BTC-USDT-SWAP", "timeframe": "15m"}) == []
    assert calls[0].method == "GET" and str(calls[0].url).split("?")[0] == MARKET_URL
    assert "authorization" not in calls[0].headers
    bad = LiveMarket(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(SourceError):
        bad.candles({"symbol": "BTC-USDT-SWAP", "timeframe": "15m"})
