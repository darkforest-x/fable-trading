from pathlib import Path

import pandas as pd

from scripts.backtest_owner_short_gold_center_recent import (
    EVENT_GAP_BARS,
    bar_from_x_normalized,
    deduplicate_detections,
    paired_closed_metrics,
    shard_paths,
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


def test_symbol_shards_are_disjoint_and_complete() -> None:
    paths = [Path(f"S{i}.csv") for i in range(11)]
    shards = [shard_paths(paths, shard_index=i, shard_count=4) for i in range(4)]
    assert sum(shards, []) != paths  # interleaved by design, not contiguous slicing
    assert {path for shard in shards for path in shard} == set(paths)
    assert sum(len(shard) for shard in shards) == len(paths)


def test_matched_control_difference_uses_identical_closed_event_ids() -> None:
    events = [
        {"event_id": "a", "status": "closed", "net_taker": 0.02},
        {"event_id": "b", "status": "closed", "net_taker": -0.01},
        {"event_id": "c", "status": "open", "net_taker": None},
    ]
    controls = [
        {"event_id": "a", "status": "closed", "net_taker": 0.01},
        {"event_id": "b", "status": "open", "net_taker": None},
        {"event_id": "c", "status": "closed", "net_taker": 0.50},
    ]

    result = paired_closed_metrics(events, controls)

    assert result["paired_closed"] == 1
    assert result["paired_event_net_taker_mean"] == 0.02
    assert result["paired_control_net_taker_mean"] == 0.01
    assert result["paired_event_minus_control_net_taker"] == 0.01
