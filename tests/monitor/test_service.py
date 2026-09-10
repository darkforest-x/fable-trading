"""V1 monitor service regressions with synthetic public OHLC only.

This file owns the current service boundary: long-only SPIKE Burst V1 on
confirmed 30m/1H/4H bars. Raw/YOLO two-stage identity and replay isolation are
covered in the dedicated V1 policy/API suites rather than reviving IMACD-era
periods, sides, or Telegram behavior here.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
import requests

from yoyo.monitor import MONITORED_TIMEFRAMES, TIMEFRAMES, candle_open_ms
from yoyo.monitor import service
from yoyo.monitor.okx import MarketError
from yoyo.monitor.server import create_app
from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store


SYMBOL = "TEST-USDT-SWAP"
INSTRUMENT = {"instId": SYMBOL, "settleCcy": "USDT", "tickSz": "0.01"}
NOW = 400 * TIMEFRAMES["4H"] + 60_000


class FakeMarket:
    """Stable, confirmed V1-compatible candles; never accesses a real exchange."""

    def __init__(self, bars: int = 400):
        self.fail: set[str] = set()
        self.offset_ms = 0
        self.requests = 0
        self.history: dict[str, list[dict]] = {}
        for timeframe, period in TIMEFRAMES.items():
            end = candle_open_ms(NOW, timeframe)
            self.history[timeframe] = [
                {"t": end - (bars - index) * period, "o": 100.0, "h": 101.0,
                 "l": 99.0, "c": 100.0, "v": 1.0}
                for index in range(bars)
            ]

    def clock(self):
        return NOW

    def synchronize(self):
        return 0

    def instruments(self):
        return [INSTRUMENT]

    def candles(self, symbol, timeframe, previous=None):
        assert symbol == SYMBOL
        if timeframe in self.fail:
            raise MarketError("synthetic_endpoint_failure")
        return self.history[timeframe], 0


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *args, **kwargs: pytest.fail("service test made a network request"))


def market(store, timeframe):
    return next(row for row in store.list_markets()
                if row["symbol"] == SYMBOL and row["timeframe"] == timeframe)


@pytest.mark.parametrize("timeframe", MONITORED_TIMEFRAMES)
def test_failure_then_same_timestamp_recovery_keeps_v1_chart_reviewable(tmp_path, timeframe):
    store, client = Store(tmp_path / "monitor.sqlite3"), FakeMarket()
    monitor = Monitor(store, client=client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    before = deepcopy(monitor.chart(SYMBOL, timeframe))
    assert before["state"]["phase"] == "ready" and before["state"]["ready"] is True

    client.fail.add(timeframe)
    errors = monitor.scan_symbol(INSTRUMENT)
    assert (timeframe, "synthetic_endpoint_failure") in errors
    failed = monitor.chart(SYMBOL, timeframe)
    assert failed["candles"] == before["candles"]
    assert failed["state"]["stale"] is True
    assert failed["state"]["error"] == "market_data_unavailable"
    assert market(store, timeframe)["error"] == "market_data_unavailable"

    client.fail.clear()
    assert monitor.scan_symbol(INSTRUMENT) == []
    recovered = monitor.chart(SYMBOL, timeframe)
    assert recovered["candles"][-1]["t"] == before["candles"][-1]["t"]
    assert recovered["state"]["phase"] == "ready"
    assert recovered["state"]["stale"] is False
    assert not recovered["state"].get("error")


def test_pre_warmup_source_is_explicitly_not_ready_but_not_a_fetch_error(tmp_path):
    store, client = Store(tmp_path / "monitor.sqlite3"), FakeMarket(bars=339)
    monitor = Monitor(store, client=client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    for timeframe in MONITORED_TIMEFRAMES:
        state = monitor.chart(SYMBOL, timeframe)["state"]
        assert state["phase"] == "ready"
        assert state["bars"] == 339
        assert state["ready"] is False
        assert not state.get("error")


def test_partial_scan_is_degraded_and_keeps_other_v1_periods_available(tmp_path):
    store, client = Store(tmp_path / "monitor.sqlite3"), FakeMarket()
    client.fail.add("1H")
    monitor = Monitor(store, client=client)
    monitor.scan()
    scan = store.get_meta("scan")
    assert scan["status"] == "degraded"
    assert scan["completed"] == scan["total"] == len(MONITORED_TIMEFRAMES)
    assert scan["errors"] == 1
    assert monitor.chart(SYMBOL, "30m")["state"]["ready"] is True
    assert market(store, "1H")["error"] == "market_data_unavailable"


def test_scan_persists_only_current_v1_timeframes_and_fresh_cutovers(tmp_path):
    store, client = Store(tmp_path / "monitor.sqlite3"), FakeMarket()
    monitor = Monitor(store, client=client)
    monitor.bark.creds = "synthetic-device"
    monitor.scan()
    status = monitor.status()
    assert status["scan"]["completed"] == status["scan"]["total"] == 3
    assert status["scan"]["errors"] == 0
    assert status["runtime"]["timeframes"] == ["30m", "1H", "4H"]
    assert status["runtime"]["bark_timeframes"] == ["30m", "1H", "4H"]
    assert status["runtime"]["display_only_timeframes"] == []
    assert status["runtime"]["timeframe_notification_since_ms"] == {tf: NOW for tf in MONITORED_TIMEFRAMES}
    assert monitor.notification_ready.is_set()
    # Existing activation is immutable across a later restart/cutover attempt.
    assert Store(store.path).activate_timeframe_policy("30m", NOW + 900_000,
                                                        protocol=service.PROTOCOL) == NOW


def test_chart_api_accepts_only_v1_timeframes(tmp_path):
    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    monitor.client = FakeMarket()
    monitor.scan_symbol(INSTRUMENT)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/chart")
    for timeframe in MONITORED_TIMEFRAMES:
        assert endpoint(SYMBOL, timeframe)["timeframe"] == timeframe
    from fastapi import HTTPException
    for legacy in ("5m", "15m", "1Dutc"):
        with pytest.raises(HTTPException) as error:
            endpoint(SYMBOL, legacy)
        assert error.value.status_code == 400


def test_health_path_is_lightweight_and_reports_lazy_model_not_ready(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    monitor.client = FakeMarket()
    monitor.scan()
    monkeypatch.setattr(monitor, "status", lambda: pytest.fail("health must not read full status"))
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/health")
    result = endpoint()
    assert result["service_alive"] is True
    assert result["market_ready"] is True
    assert result["model_ready"] is False
    assert result["ok"] is False


def test_stale_market_is_not_reported_as_a_current_v1_chart(tmp_path):
    store, client = Store(tmp_path / "monitor.sqlite3"), FakeMarket()
    monitor = Monitor(store, client=client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    client.clock = lambda: NOW + TIMEFRAMES["1H"]
    chart = monitor.chart(SYMBOL, "1H")
    assert chart["state"]["stale"] is True
    assert chart["state"]["error"] == "awaiting_latest_confirmed_bar"


def test_start_recovers_uncertain_telegram_row_without_reenqueuing_it(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    event = {"protocol": "test", "kind": "test", "symbol": SYMBOL, "timeframe": "1H",
             "side": "long", "price": 100.0, "bar_close_ms": NOW, "detected_at_ms": NOW}
    assert store.upsert_event(event, notify=True)
    assert store.claim(NOW) is not None
    starts = []

    class InertThread:
        def __init__(self, *, name, **kwargs):
            self.name = name
        def start(self):
            starts.append(self.name)
        def join(self, timeout=None):
            pass

    monkeypatch.setattr(service.threading, "Thread", InertThread)
    monitor = Monitor(store, client=FakeMarket())
    monitor.start()
    assert set(starts) == {"impulse-scan", "impulse-model", "impulse-bark", "impulse-status"}
    assert store.telegram_status().get("unknown") == 1
    assert store.claim(NOW) is None
    monitor.close()
