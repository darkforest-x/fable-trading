"""Current V1 Bark cutover rules; all rows are synthetic and causal."""
from __future__ import annotations

import pytest

from yoyo.monitor import (BARK_TIMEFRAMES, DIRECT_POLICY, FRESH_MS, SIGNAL_KIND,
                          SIGNAL_PROTOCOL, TIMEFRAMES)
from yoyo.monitor.notification_policy import arm_v1_bark, delivery_error, is_direct_start
from yoyo.monitor.store import Store


def raw_event(timeframe="1H", **changes):
    step = TIMEFRAMES[timeframe]
    close = 100 * step
    event = dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, source="live", confirmation="raw",
                 direction="long", side="long", venue="okx", symbol="TEST-USDT-SWAP",
                 timeframe=timeframe, timeframe_min={"30m": 30, "1H": 60, "4H": 240}[timeframe],
                 bar_open_ms=close - step, bar_close_ms=close, signal_close_time=close,
                 is_closed=True, confirmed=True, price=100.0, risk=1.0, initial_stop=99.0,
                 source_sha256="a" * 64, entry_reference="next_open", executable_entry_time=None)
    event.update(changes)
    return event


@pytest.mark.parametrize("timeframe", BARK_TIMEFRAMES)
def test_raw_v1_bark_is_forward_only_and_telegram_remains_disabled(tmp_path, timeframe):
    store = Store(tmp_path / "monitor.sqlite3")
    event = raw_event(timeframe)
    arm_v1_bark(store, event["bar_close_ms"] - 1)
    assert delivery_error(store, event, event["bar_close_ms"], "bark") is None
    assert delivery_error(store, event, event["bar_close_ms"], "telegram") == "not_model_confirmed_signal"
    assert delivery_error(store, event, event["bar_close_ms"] + FRESH_MS + 1, "bark") == "signal_expired"


@pytest.mark.parametrize("changes", [
    {"source": "replay"}, {"confirmation": "yolo"}, {"direction": "short"},
    {"side": "short"}, {"is_closed": False}, {"confirmed": False}, {"risk": 0},
])
def test_raw_stage_rejects_non_v1_or_nonclosed_rows(changes):
    assert not is_direct_start(raw_event(**changes))


def test_cutover_never_backfills_a_preexisting_raw_signal(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    event = raw_event()
    arm_v1_bark(store, event["bar_close_ms"])
    assert delivery_error(store, event, event["bar_close_ms"], "bark") == "before_bark_activation"
    assert store.get_meta("notification_policy:v1_bark_arm")["timeframes"] == list(BARK_TIMEFRAMES)
    assert store.timeframe_activation("1H", protocol=DIRECT_POLICY) == event["bar_close_ms"]
