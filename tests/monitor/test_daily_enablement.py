"""Daily rollout, UTC/Monday causality and independent Bark delivery cutovers.

Synthetic OHLCV and fake senders only. Features use confirmed daily bars and
weekly bars closed by the local opening time, never future outcomes.
"""
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from model_fixture import model_event
from test_signals import candles
from yoyo.monitor import (DIRECT_POLICY, DIRECT_TIMEFRAMES, FRESH_MS,
                          HIGHER_TIMEFRAME, MODEL_PROTOCOL, MONITORED_TIMEFRAMES,
                          TIMEFRAMES, TV_INTERVALS, candle_open_ms)
from yoyo.monitor import signals
from yoyo.monitor.bark import BarkWorker
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.okx import MarketError, OKX, parse_rows
from yoyo.monitor.server import DesktopChartRequest
from yoyo.monitor.store import Store
from yoyo.monitor.tradingview import chart_url

DAY, WEEK = 86_400_000, 604_800_000
MONDAY = 4 * DAY


def raw(t, confirmed="1"):
    return [str(t), "100", "101", "99", "100", "1", "1", "100", confirmed]


def test_daily_contract_reuses_native_daily_and_accepts_exact_desktop_interval():
    assert MONITORED_TIMEFRAMES == DIRECT_TIMEFRAMES == ("15m", "30m", "1H", "4H", "1Dutc")
    assert HIGHER_TIMEFRAME["4H"] == "1Dutc"
    assert HIGHER_TIMEFRAME["1Dutc"] == "1Wutc"
    assert TIMEFRAMES["1Dutc"] == DAY and TIMEFRAMES["1Wutc"] == WEEK
    payload = DesktopChartRequest(symbol="BTC-USDT-SWAP", timeframe="1Dutc")
    assert payload.timeframe == "1Dutc"
    assert parse_qs(urlsplit(chart_url(payload.symbol, payload.timeframe)).query) == {
        "symbol": ["OKX:BTCUSDT.P"], "interval": [TV_INTERVALS["1Dutc"]]}
    assert TV_INTERVALS["1Dutc"] == "1D"


def test_daily_only_confirms_at_utc_midnight_and_weekly_uses_monday():
    assert parse_rows([raw(DAY)], "1Dutc", 2 * DAY - 1) == []
    assert [r["t"] for r in parse_rows([raw(DAY), raw(2 * DAY, "0")], "1Dutc", 2 * DAY)] == [DAY]
    # A Hong Kong-midnight native 1D bar must not masquerade as 1Dutc.
    with pytest.raises(MarketError, match="alignment"):
        parse_rows([raw(DAY - 8 * 3_600_000)], "1Dutc", 2 * DAY)
    assert candle_open_ms(MONDAY + WEEK - 1, "1Wutc") == MONDAY
    assert candle_open_ms(MONDAY + WEEK, "1Wutc") == MONDAY + WEEK
    assert parse_rows([raw(MONDAY)], "1Wutc", MONDAY + WEEK - 1) == []
    assert [r["t"] for r in parse_rows([raw(MONDAY)], "1Wutc", MONDAY + WEEK)] == [MONDAY]
    with pytest.raises(MarketError, match="alignment"):
        parse_rows([raw(WEEK)], "1Wutc", 3 * WEEK)


@pytest.mark.parametrize("timeframe,start,step", [("1Dutc", DAY, DAY), ("1Wutc", MONDAY, WEEK)])
def test_native_daily_weekly_fetch_and_cached_close(timeframe, start, step):
    client = OKX()
    client.clock = lambda: start + step + 1
    calls = []
    def get(path, params):
        calls.append((path, params))
        return [raw(start + step, "0"), raw(start)]
    client.get = get
    bars, gaps = client.candles("BTC-USDT-SWAP", timeframe)
    assert len(bars) == 1 and gaps == 0 and bars[0]["t"] == start
    assert calls[0][1]["bar"] == timeframe
    assert client.candles("BTC-USDT-SWAP", timeframe, bars) == (bars, 0)
    assert len(calls) == 1


def test_daily_weekly_background_is_closed_at_local_open_and_not_thursday():
    high = candles(350, WEEK, MONDAY)
    first_known = MONDAY + 341 * WEEK
    low = candles(342, DAY, first_known - 341 * DAY)
    before = signals.analyze(low[:-1], high, "1Dutc")
    after = signals.analyze(low, high, "1Dutc")
    assert before["state"]["htf_side"] == "unknown"
    assert after["state"]["htf_side"] == "flat"
    assert after["state"]["htf_bar_close_ms"] == first_known
    modified = deepcopy(high)
    modified[341].update(o=100, h=2001, l=99, c=2000)
    assert signals.analyze(low, modified, "1Dutc")["state"] == after["state"]
    assert signals.analyze(low, high[:340], "1Dutc")["state"]["htf_side"] == "unknown"


def test_daily_real_computation_keeps_visible_start_without_weekly_history():
    assert not signals.analyze(candles(340, DAY), [], "1Dutc")["state"]["ready"]
    assert signals.analyze(candles(341, DAY), [], "1Dutc")["state"]["ready"]
    bars = candles(370, DAY)
    for row in bars[355:]:
        row.update(o=120, h=121, l=119, c=120)
    result = signals.analyze(bars, [], "1Dutc")
    starts = [e for e in result["events"] if e["kind"] == "tv_start"]
    assert len(starts) == 1
    assert starts[0]["near_zero_bars"] >= 12
    assert starts[0]["higher_timeframe"] == "1Wutc" and starts[0]["htf_allowed"] is None
    prefix = signals.analyze(bars[:356], [], "1Dutc")
    assert [e for e in prefix["events"] if e["kind"] == "tv_start"] == starts


def test_daily_cutover_preserves_old_periods_and_two_stage_bark_does_not_replay(tmp_path):
    store = Store(tmp_path / "monitor.sqlite")
    activation = 400 * DAY + 60_000
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        store.activate_bark_policy(0, protocol=protocol, retire_obsolete=False)
        for tf in ("15m", "30m", "1H", "4H"):
            store.activate_timeframe_policy(tf, activation - DAY, protocol=protocol)
    with store.connect() as db:
        before = {r[0]: r[1] for r in db.execute("SELECT key,payload FROM meta")}
    old = model_event(timeframe="1Dutc", wait=0, close=400 * DAY)
    assert delivery_error(store, old["indicator"], activation, "bark") == "before_timeframe_activation"
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert store.activate_timeframe_policy("1Dutc", activation, protocol=protocol) == activation
    assert delivery_error(store, old, activation, "bark") == "before_timeframe_activation"
    newer = model_event(timeframe="1Dutc", wait=0, close=401 * DAY)
    calls = []
    def sender(*args, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(status_code=200, json=lambda: {"code": 200, "timestamp": 123})
    worker = BarkWorker(store, creds="synthetic-device", sender=sender)
    # The raw leg sends before a model event exists.
    assert store.upsert_event(newer["indicator"], bark_notify=True)
    assert worker.deliver_once(401 * DAY + 1000)
    assert len(calls) == 1 and "未经 YOLO" in calls[0]["subtitle"]
    assert "日线" in calls[0]["title"] and "interval=1D" in calls[0]["body"]
    assert store.upsert_event(newer, bark_notify=True)
    assert worker.deliver_once(401 * DAY + 2000)
    assert len(calls) == 2 and "YOLO 确认" in calls[1]["subtitle"]
    assert not worker.deliver_once(401 * DAY + 3000)
    # The daily period does not extend the existing 30-minute freshness gate.
    assert delivery_error(store, newer, 401 * DAY + FRESH_MS + 1, "bark") == "signal_expired"
    restarted = Store(store.path)
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert restarted.activate_timeframe_policy("1Dutc", activation + DAY, protocol=protocol) == activation
    with restarted.connect() as db:
        after = {r[0]: r[1] for r in db.execute("SELECT key,payload FROM meta")}
    assert all(after[k] == v for k, v in before.items())
    assert restarted.bark_status()["sent"] == 2
    assert restarted.telegram_status()["pending"] == 0
