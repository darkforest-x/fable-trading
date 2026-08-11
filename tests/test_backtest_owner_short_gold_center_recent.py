from pathlib import Path

import pandas as pd

from scripts.backtest_owner_short_gold_center_recent import (
    EVENT_GAP_BARS,
    bar_from_x_normalized,
    deduplicate_detections,
    historical_target_index,
    paired_closed_metrics,
    parser,
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


def test_historical_target_index_uses_frozen_15m_series_coordinates() -> None:
    assert historical_target_index(
        100, "2026-05-02T00:00:00Z", "2026-05-03T00:00:00Z"
    ) == 196


def test_historical_target_index_refuses_holdout() -> None:
    try:
        historical_target_index(
            100, "2026-05-02T00:00:00Z", "2026-05-04T00:00:00Z"
        )
    except ValueError as exc:
        assert "touches holdout" in str(exc)
    else:
        raise AssertionError("holdout target must be rejected")


def test_train_hardneg_scope_is_explicit_for_snapshot_and_scan() -> None:
    historical = parser().parse_args(
        [
            "historical",
            "--out-dir",
            "/tmp/example",
            "--end",
            "2026-03-01T00:00:00Z",
            "--evaluation-scope",
            "train_hardneg_mining",
        ]
    )
    scan = parser().parse_args(
        [
            "scan",
            "--snapshot-dir",
            "/tmp/example",
            "--out-dir",
            "/tmp/output",
            "--evaluation-scope",
            "train_hardneg_mining",
        ]
    )

    assert historical.evaluation_scope == "train_hardneg_mining"
    assert scan.evaluation_scope == "train_hardneg_mining"
