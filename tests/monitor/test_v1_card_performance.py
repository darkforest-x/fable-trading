"""Closed-bar SPIKE V1 card outcome projection; no exchange or order access."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.signals import _path_performance
from yoyo.monitor.store import Store


def _path(**changes):
    values = {
        "exit": [False, False, False, False],
        "exit_price": [np.nan] * 4,
        "current_r": [0.0, 0.8, 2.35, 1.9],
        "peak_r": [0.0, 1.0, 3.1, 3.1],
        "protection": [90.0, 90.0, 101.0, 103.0],
        "active_protection": [np.nan, 90.0, 90.0, 101.0],
        "trail_armed": [False, False, True, True],
    }
    values.update(changes)
    return pd.DataFrame(values)


def test_active_path_reports_current_peak_and_next_bar_protection():
    result = _path_performance(_path(), np.array([0, 60_000, 120_000, 180_000]), 60_000, 0, 4)
    assert result == {
        "status": "active", "stop_triggered": False,
        "current_r": 1.9, "peak_r": 3.1, "exit_r": None,
        "exit_price": None, "stop_price": 103.0,
        "trailing_active": True, "bars_held": 3,
        "updated_at_ms": 240_000, "exit_time_ms": None,
        "basis": "v1_signal_close_reference",
    }


def test_first_protective_exit_freezes_realized_r_and_ignores_later_bars():
    frame = _path(
        exit=[False, False, True, False],
        exit_price=[np.nan, np.nan, 90.0, np.nan],
        current_r=[0.0, 0.2, -1.0, 8.0],
        peak_r=[0.0, 0.4, 0.4, 9.0],
        active_protection=[np.nan, 90.0, 90.0, 105.0],
        trail_armed=[False, False, False, True],
    )
    result = _path_performance(frame, np.array([0, 60_000, 120_000, 180_000]), 60_000, 0, 4)
    assert result["status"] == "loss"
    assert result["current_r"] == result["exit_r"] == -1.0
    assert result["exit_price"] == result["stop_price"] == 90.0
    assert result["bars_held"] == 2
    assert result["exit_time_ms"] == 180_000


def test_store_backfills_card_performance_without_creating_an_outbox(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    event = {
        "protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND,
        "source": "live", "confirmation": "raw", "direction": "long", "side": "long",
        "symbol": "PEPE-USDT-SWAP", "timeframe": "1H", "timeframe_min": 60,
        "bar_open_ms": 1_000, "bar_close_ms": 2_000, "price": 100.0,
        "risk": 10.0, "initial_stop": 90.0, "is_closed": True,
    }
    assert store.upsert_event(event) is True
    event_id = store.event_id(event)
    assert store.event_pairs_missing_performance(SIGNAL_PROTOCOL) == {("PEPE-USDT-SWAP", "1H")}
    performance = {"status": "active", "current_r": 2.0, "peak_r": 3.0, "stop_price": 101.0}
    assert store.update_event_payload(event_id, {"performance": performance}) is True
    assert store.update_event_payload(event_id, {"performance": performance}) is False
    assert store.event_pairs_missing_performance(SIGNAL_PROTOCOL) == set()
    card = store.list_events(summary=True)[0]
    assert card["performance"] == performance
    assert store.bark_status()["pending"] == 0
