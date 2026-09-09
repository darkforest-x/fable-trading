"""Five-minute stream rollout: independent cutovers and public causal data.

Only synthetic candles, notification journals, and senders are used. No
production runtime, network, credentials, or inference is accessed.
"""
from types import SimpleNamespace

import pytest

from model_fixture import model_event
from test_service import FakeMarket, INSTRUMENT, NOW
from yoyo.monitor import (DIRECT_POLICY, DIRECT_TIMEFRAMES, MODEL_PROTOCOL,
                          MONITORED_TIMEFRAMES, HIGHER_TIMEFRAME, TIMEFRAMES, TV_INTERVALS)
from yoyo.monitor import service, tradingview, yolo_detector
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.okx import MarketError, OKX, parse_rows
from yoyo.monitor.store import Store


def test_five_minute_adapters_share_the_same_timeframe_contract():
    assert TIMEFRAMES["5m"] == 300_000
    assert HIGHER_TIMEFRAME["5m"] == "1H"  # Pine's <= 15 minute auto-HTF branch.
    assert MONITORED_TIMEFRAMES == DIRECT_TIMEFRAMES == ("5m", "15m", "1H", "4H")
    assert set(yolo_detector.TIMEFRAME_MS) == set(TV_INTERVALS) == set(MONITORED_TIMEFRAMES)
    assert yolo_detector.TIMEFRAME_MS["5m"] == 300_000
    assert tradingview.INTERVALS["5m"] == TV_INTERVALS["5m"] == "5"


def raw_candle(opening, confirmed="1"):
    return [str(opening), "100", "101", "99", "100", "1", "1", "100", confirmed]


def test_five_minute_public_candles_require_confirmation_close_and_exchange_grid():
    rows = [raw_candle(0), raw_candle(300_000, "0"), raw_candle(600_000)]
    assert [row["t"] for row in parse_rows(rows, "5m", 600_000)] == [0]
    with pytest.raises(MarketError, match="alignment"):
        parse_rows([raw_candle(1)], "5m", 600_000)


def test_five_minute_fetch_uses_native_public_bar_and_skips_current_cached_close():
    client = OKX()
    client.clock = lambda: 600_001
    calls = []
    def get(path, params):
        calls.append((path, params))
        return [raw_candle(300_000), raw_candle(0)]
    client.get = get
    bars, gaps = client.candles("BTC-USDT-SWAP", "5m")
    assert calls == [("/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "5m", "limit": "300"})]
    assert gaps == 0 and [row["t"] for row in bars] == [0, 300_000]
    assert client.candles("BTC-USDT-SWAP", "5m", bars) == (bars, 0)
    assert len(calls) == 1


def test_five_minute_rollout_preserves_every_existing_stage_and_does_not_replay(tmp_path, monkeypatch):
    def worker(store):
        return SimpleNamespace(creds="synthetic-device", status=lambda: {})
    monkeypatch.setattr(service, "TelegramWorker", lambda store: SimpleNamespace(creds=None, status=lambda: {}))
    monkeypatch.setattr(service, "BarkWorker", worker)
    store = Store(tmp_path / "monitor.sqlite")
    existing = ("15m", "1H", "4H")
    cutovers = {}
    for number, protocol in enumerate((DIRECT_POLICY, MODEL_PROTOCOL)):
        store.activate_bark_policy(NOW - 240_000 + number, protocol=protocol, retire_obsolete=False)
        for index, timeframe in enumerate(existing):
            cutover = NOW - 180_000 + number * 10 + index
            cutovers[protocol, timeframe] = cutover
            store.activate_timeframe_policy(timeframe, cutover, protocol=protocol)
        assert store.timeframe_activation("5m", protocol=protocol) is None
    with store.connect() as db:
        before_meta = {row["key"]: row["payload"] for row in db.execute("SELECT * FROM meta")}
    client = FakeMarket()
    client.synchronize = lambda: None
    client.instruments = lambda: [INSTRUMENT]
    for timeframe in MONITORED_TIMEFRAMES:
        client.history[timeframe][-1].update(o=120., h=121., l=119., c=120.)
    monitor = service.Monitor(store, client=client)
    monitor.scan()
    assert store.get_meta("scan")["completed"] == 4
    assert store.get_meta("scan")["errors"] == 0
    assert monitor.chart(INSTRUMENT["instId"], "5m")["state"]["higher_timeframe"] == "1H"
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert store.timeframe_activation("5m", protocol=protocol) == NOW
        assert store.get_meta("notification_policy:" + protocol) is None
    for (protocol, timeframe), cutover in cutovers.items():
        assert store.timeframe_activation(timeframe, protocol=protocol) == cutover
    with store.connect() as db:
        after_meta = {row["key"]: row["payload"] for row in db.execute("SELECT * FROM meta")}
    assert all(after_meta[key] == value for key, value in before_meta.items())
    assert store.telegram_status()["pending"] == 0
    assert store.bark_status()["pending"] == 3
    old = next(e for e in store.list_events() if e["timeframe"] == "5m")
    assert old["bar_close_ms"] < NOW and old["bark_notification_status"] == "history"
    assert store.list_candidates(timeframe="5m") == []
    assert not monitor.record_arrow(old, [], NOW + 1)
    later = model_event(timeframe="5m", wait=0, close=old["bar_close_ms"] + TIMEFRAMES["5m"])
    assert delivery_error(store, later["indicator"], later["bar_close_ms"], "bark") is None
    assert delivery_error(store, later, later["bar_close_ms"], "bark") is None
    assert monitor.record_arrow(later["indicator"], [], later["bar_close_ms"])
    assert store.upsert_event(later, bark_notify=True)
    assert store.bark_status()["pending"] == 5
    # A restart neither swallows the two pending legs nor advances any cutover.
    restarted = service.Monitor(Store(store.path), client=client)
    restarted.scan()
    assert store.bark_status()["pending"] == 5
    assert store.telegram_status()["pending"] == 0
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert store.timeframe_activation("5m", protocol=protocol) == NOW
    for (protocol, timeframe), cutover in cutovers.items():
        assert store.timeframe_activation(timeframe, protocol=protocol) == cutover
