from __future__ import annotations

import pandas as pd

from scripts.backtest_owner_short_gold_center_recent import (
    EVENT_GAP_BARS,
    bar_from_x_normalized,
    deduplicate_detections,
)


def _detection(*, decision: int, mid: float, conf: float, symbol: str = "ETH_USDT_SWAP") -> dict:
    core = pd.Timestamp("2026-08-10T12:00:00Z") + pd.Timedelta(minutes=15 * int(mid))
    return {
        "symbol": symbol,
        "decision_i": decision,
        "decision_time": str(pd.Timestamp("2026-08-10T12:00:00Z") + pd.Timedelta(minutes=15 * decision)),
        "core_mid_i": mid,
        "core_mid_time": str(core),
        "conf": conf,
        "window_len": 12,
    }


def test_normalized_x_maps_to_compact_window_edges() -> None:
    assert bar_from_x_normalized(0.0, 12) == 0
    assert bar_from_x_normalized(1.0, 12) == 11
    assert 5 <= bar_from_x_normalized(0.5, 12) <= 6


def test_event_dedupe_uses_first_crossing_not_later_max_confidence() -> None:
    rows = [
        _detection(decision=20, mid=10.0, conf=0.30),
        _detection(decision=21, mid=10.5, conf=0.90),
        _detection(decision=30, mid=10.0 + EVENT_GAP_BARS + 1, conf=0.80),
    ]
    events = deduplicate_detections(rows)
    assert len(events) == 2
    first = events[0]
    assert first["decision_i"] == 20
    assert first["conf"] == 0.30
    assert first["event_conf_max"] == 0.90
    assert first["raw_detection_count"] == 2


def test_dedupe_never_merges_symbols() -> None:
    rows = [
        _detection(decision=20, mid=10, conf=0.4, symbol="ETH_USDT_SWAP"),
        _detection(decision=20, mid=10, conf=0.4, symbol="BTC_USDT_SWAP"),
    ]
    assert len(deduplicate_detections(rows)) == 2
