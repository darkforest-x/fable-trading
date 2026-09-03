"""Contract tests for the four-timeframe latest-market research scanner."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts import scan_4h_ma_launch_yolo_latest as base
from scripts.scan_crypto_grade_a_yolo_mtf_latest import (
    SPEC_BY_KEY,
    TimeframeSpec,
    deduplicate_events,
    earliest_endpoint_open,
    enrich_model_frames,
    latest_closed_open,
    rank_events,
)
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features


def _candidate(
    *,
    symbol: str = "TEST_USDT_SWAP",
    endpoint: str,
    core_end_i: int = 140,
    confidence: float = 0.7,
    class_id: int = 0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "core_end_i": core_end_i,
        "class_name": "dense_long" if class_id == 0 else "dense_short",
        "class_id": class_id,
        "confidence": confidence,
        "window_end_time": endpoint,
        "window_start_i": 123,
        "window_end_i": 140,
        "window_len": 18,
        "core_start_i": core_end_i - 3,
        "core_length_bars": 4,
        "confirmation_bars": 2,
        "core_start_time": endpoint,
        "core_end_time": endpoint,
        "input_pixel_sha256": "abc",
    }


def _frame(*, end: str, freq: str) -> pd.DataFrame:
    times = pd.date_range(end=pd.Timestamp(end), periods=160, freq=freq, tz="UTC")
    return pd.DataFrame({"open_time": times})


def test_latest_closed_open_uses_only_fully_closed_bars() -> None:
    frozen = pd.Timestamp("2026-09-03T03:17:00Z")
    assert latest_closed_open(frozen, SPEC_BY_KEY["15m"]) == pd.Timestamp(
        "2026-09-03T03:00:00Z"
    )
    assert latest_closed_open(frozen, SPEC_BY_KEY["1h"]) == pd.Timestamp(
        "2026-09-03T02:00:00Z"
    )
    assert latest_closed_open(frozen, SPEC_BY_KEY["4h"]) == pd.Timestamp(
        "2026-09-02T20:00:00Z"
    )
    assert latest_closed_open(frozen, SPEC_BY_KEY["1d"]) == pd.Timestamp(
        "2026-09-02T00:00:00Z"
    )


def test_four_hour_and_daily_windows_are_bounded_to_fifteen_days() -> None:
    frozen = pd.Timestamp("2026-09-03T03:17:00Z")
    four_hour = SPEC_BY_KEY["4h"]
    daily = SPEC_BY_KEY["1d"]
    assert four_hour.lookback_endpoints == 90
    assert daily.lookback_endpoints == 15
    assert latest_closed_open(frozen, four_hour) - earliest_endpoint_open(
        frozen, four_hour
    ) == pd.Timedelta(hours=4 * 89)
    assert latest_closed_open(frozen, daily) - earliest_endpoint_open(
        frozen, daily
    ) == pd.Timedelta(days=14)


def test_dedup_availability_uses_each_timeframe_clock() -> None:
    frozen = pd.Timestamp("2026-09-03T03:17:00Z")
    spec = TimeframeSpec("1h", "1h", "1H", 60, 1, 14, 2)
    frame = _frame(end="2026-09-03T02:00:00Z", freq="1h")
    event = deduplicate_events(
        [
            _candidate(endpoint="2026-09-03T02:00:00Z", confidence=0.91),
            _candidate(endpoint="2026-09-03T02:00:00Z", confidence=0.62),
        ],
        spec=spec,
        frames={"TEST_USDT_SWAP": frame},
        frozen_at=frozen,
    )[0]
    assert event["first_available_at"] == "2026-09-03T03:00:00+00:00"
    assert event["confidence"] == 0.91
    assert event["is_current_latest_bar"] is True


def _event(
    *,
    event_id: str,
    symbol: str,
    timeframe: str,
    confidence: float,
    class_id: int = 0,
    available: str = "2026-09-03T02:00:00Z",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "confidence": confidence,
        "class_id": class_id,
        "class_name": "dense_long" if class_id == 0 else "dense_short",
        "first_available_at": available,
    }


def test_review_rank_prefers_same_side_multitimeframe_overlap() -> None:
    ranked = rank_events(
        [
            _event(event_id="solo", symbol="SOLO_USDT_SWAP", timeframe="1d", confidence=0.99),
            _event(event_id="pair15", symbol="PAIR_USDT_SWAP", timeframe="15m", confidence=0.55),
            _event(event_id="pair4", symbol="PAIR_USDT_SWAP", timeframe="4h", confidence=0.45),
        ]
    )
    assert [row["event_id"] for row in ranked[:2]] == ["pair4", "pair15"]
    assert ranked[0]["same_side_timeframe_count"] == 2
    assert ranked[0]["review_rank"] == 1


def test_confidence_is_ranked_within_timeframe_not_combined() -> None:
    ranked = rank_events(
        [
            _event(event_id="a", symbol="A_USDT_SWAP", timeframe="15m", confidence=0.60),
            _event(event_id="b", symbol="B_USDT_SWAP", timeframe="15m", confidence=0.80),
            _event(event_id="c", symbol="C_USDT_SWAP", timeframe="1d", confidence=0.40),
        ]
    )
    by_id = {row["event_id"]: row for row in ranked}
    assert by_id["b"]["confidence_rank_within_timeframe"] == 1
    assert by_id["a"]["confidence_rank_within_timeframe"] == 2
    assert by_id["c"]["confidence_rank_within_timeframe"] == 1
    assert all("combined" not in row for row in ranked)


def test_model_frames_preserve_parent_gate_atr_through_task_building() -> None:
    rows = 160
    close = np.linspace(100.0, 132.0, rows) + np.sin(np.arange(rows) / 4.0)
    raw = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": close - 0.15,
            "high": close + 0.8,
            "low": close - 0.9,
            "close": close,
            "volume": np.linspace(1_000.0, 2_000.0, rows),
        }
    )
    expected = add_candidate_features(raw)
    semantic_ready = enrich_model_frames({"TEST_USDT_SWAP": raw})
    enriched, tasks = base.build_tasks(semantic_ready, lookback_endpoints=1)
    actual = enriched["TEST_USDT_SWAP"]
    np.testing.assert_allclose(actual["atr"], expected["atr"], equal_nan=True)
    assert len(tasks) == 2
    assert np.isfinite(float(actual.iloc[-1]["atr"]))
