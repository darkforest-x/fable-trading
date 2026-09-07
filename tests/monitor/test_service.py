"""Regression scenarios for cache recovery, HTF availability and service health.

All prices, clocks and API replies are synthetic. No network call, real
Telegram credential loading, worker thread, or persistent runtime is used.
"""
from copy import deepcopy

import pytest
import requests

from yoyo.monitor import TIMEFRAMES
from yoyo.monitor import okx, service
from yoyo.monitor.okx import MarketError, OKX
from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store


SYMBOL = "TEST-USDT-SWAP"
INSTRUMENT = dict(instId=SYMBOL, settleCcy="USDT", tickSz="0.01")
NOW = 400 * TIMEFRAMES["1Dutc"] + 60_000


class NoTelegram:
    def __init__(self, store):
        self.store = store

    def status(self):
        return dict(configured=False, enabled=False)


class FakeMarket:
    """Always return the same confirmed bars, including after fault recovery."""

    def __init__(self):
        self.fail = set()
        self.offset_ms = 0
        self.requests = 0
        self.history = {}
        for timeframe, period in TIMEFRAMES.items():
            end = NOW // period * period
            self.history[timeframe] = [
                dict(t=end - (400 - i) * period, o=100., h=101., l=99., c=100., v=1.)
                for i in range(400)
            ]

    def clock(self):
        return NOW

    def candles(self, symbol, timeframe, previous=None):
        assert symbol == SYMBOL
        if timeframe in self.fail:
            raise MarketError("synthetic_endpoint_failure")
        return self.history[timeframe], 0


@pytest.fixture(autouse=True)
def no_external_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("service regression test attempted a real network request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(service, "TelegramWorker", NoTelegram)


def market(store, timeframe):
    return next(row for row in store.list_markets()
                if row["symbol"] == SYMBOL and row["timeframe"] == timeframe)


@pytest.mark.parametrize("timeframe", ["1H", "4H"])
def test_fetch_failure_then_same_timestamp_recovery_restores_chart_and_market(tmp_path, timeframe):
    store = Store(tmp_path / "monitor.sqlite3")
    client = FakeMarket()
    monitor = Monitor(store, client=client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    first = deepcopy(monitor.chart(SYMBOL, timeframe))
    assert first["state"]["ready"] and first["state"]["phase"] == "ready"
    assert first["state"]["stale"] is False

    client.fail.add(timeframe)
    assert monitor.scan_symbol(INSTRUMENT)
    failed = monitor.chart(SYMBOL, timeframe)
    assert failed["state"]["phase"] == "loading"
    assert failed["state"]["stale"] is True
    assert failed["state"]["error"] == "market_data_unavailable"
    assert market(store, timeframe)["error"] == "market_data_unavailable"
    # Price history remains reviewable, but it must not look healthy.
    assert failed["candles"] == first["candles"]

    client.fail.clear()
    assert monitor.scan_symbol(INSTRUMENT) == []
    recovered = monitor.chart(SYMBOL, timeframe)
    assert recovered["candles"][-1]["t"] == first["candles"][-1]["t"]
    for restored in (recovered["state"], market(store, timeframe)):
        assert restored["phase"] == "ready"
        assert restored["ready"] is True
        assert restored["stale"] is False
        assert not restored.get("error")


@pytest.mark.parametrize("timeframe,higher", [("1H", "4H"), ("4H", "1Dutc")])
def test_missing_htf_then_recovery_recomputes_same_local_candle(tmp_path, timeframe, higher):
    store = Store(tmp_path / "monitor.sqlite3")
    client = FakeMarket()
    client.fail.add(higher)
    monitor = Monitor(store, client=client)
    monitor.scan_symbol(INSTRUMENT)
    missing = deepcopy(monitor.chart(SYMBOL, timeframe))
    assert missing["state"]["htf_side"] == "unknown"
    assert missing["state"]["htf_known"] is False
    assert missing["state"]["htf_long_allowed"] is None
    assert missing["state"]["htf_short_allowed"] is None

    client.fail.clear()
    monitor.scan_symbol(INSTRUMENT)
    restored = monitor.chart(SYMBOL, timeframe)
    state = restored["state"]
    assert restored["candles"][-1]["t"] == missing["candles"][-1]["t"]
    assert state["htf_known"] is True and state["htf_side"] == "flat"
    assert state["htf_long_allowed"] is True and state["htf_short_allowed"] is True
    assert state["htf_bar_close_ms"] <= state["bar_open_ms"]
    # Losing that endpoint again must also remove the old permission.
    client.fail.add(higher)
    monitor.scan_symbol(INSTRUMENT)
    assert monitor.chart(SYMBOL, timeframe)["state"]["htf_side"] == "unknown"


def test_outbox_recovery_waits_until_start_and_precedes_worker_launch(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    store.upsert_event(dict(protocol="test", symbol=SYMBOL, timeframe="1H", kind="entry",
                            side="long", price=100, bar_close_ms=NOW, detected_at_ms=NOW), notify=True)
    assert store.claim(NOW) is not None
    assert store.telegram_status().get("sending") == 1
    monitor = Monitor(store, client=FakeMarket())
    # Merely constructing a second server cannot mutate the active worker's row.
    assert store.telegram_status().get("sending") == 1
    assert store.telegram_status().get("unknown") == 0
    starts = []

    class InertThread:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]

        def start(self):
            starts.append((self.name, store.telegram_status()))

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(service.threading, "Thread", InertThread)
    monitor.start()
    assert len(starts) == 2
    assert all(status.get("unknown") == 1 and status.get("sending", 0) == 0
               for _, status in starts)
    assert store.claim(NOW) is None  # An uncertain old send must not be resent.
    monitor.close()


@pytest.mark.parametrize("status,errors,age_ms,ready,ok", [
    ("degraded", 2, 1_000, False, False),
    ("degraded", 1, 1_000, True, False),
    ("idle", 0, 1_000, True, True),
    ("error", 0, 1_000, False, False),
    ("idle", 0, 20 * 60_000, False, False),
])
def test_health_distinguishes_all_failed_partial_and_current_scan(tmp_path, monkeypatch,
                                                                status, errors, age_ms, ready, ok):
    from yoyo.monitor.server import create_app

    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    scan = dict(status=status, total=2, errors=errors, finished_at_ms=NOW - age_ms)
    monkeypatch.setattr(monitor, "status", lambda: dict(now_ms=NOW, scan=dict(scan)))
    # Invoke the installed route itself; no optional HTTP test-client dependency.
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/healthz")
    result = endpoint()
    assert result["service_alive"] is True
    assert result["market_ready"] is ready
    assert result["ok"] is ok


def test_rejected_clock_offset_does_not_replace_last_trusted_clock(monkeypatch):
    client = OKX()
    client.offset_ms = 37
    monkeypatch.setattr(okx.time, "time", lambda: 1000.)
    monkeypatch.setattr(client, "get", lambda *args, **kwargs: [{"ts": "1120001"}])
    with pytest.raises(MarketError, match="local_clock_out_of_sync"):
        client.synchronize()
    assert client.offset_ms == 37
    assert client.clock() == 1_000_037


def test_accepted_clock_offset_uses_request_midpoint(monkeypatch):
    client = OKX()
    clock = iter([1000., 1000.2])
    monkeypatch.setattr(okx.time, "time", lambda: next(clock))
    monkeypatch.setattr(client, "get", lambda *args, **kwargs: [{"ts": "1000600"}])
    assert client.synchronize() == 500
    assert client.offset_ms == 500
