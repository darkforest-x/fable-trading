from __future__ import annotations

import pandas as pd
import pytest

from src.detection.direction_dataset import (
    HOLDOUT_START,
    HoldoutLeakError,
    assign_temporal_splits,
    causal_window,
    classify_direction,
    dedupe_candidate_indices,
)
from src.judgment.labeling import BarrierOutcome


def _outcome(label: int, realized_ret: float) -> BarrierOutcome:
    return BarrierOutcome(
        label=label,
        outcome="tp" if label else "sl",
        exit_offset=1,
        entry_price=100.0,
        realized_ret=realized_ret,
    )


@pytest.mark.parametrize(
    ("long_label", "short_label", "expected"),
    [
        (1, 0, "long"),
        (0, 1, "short"),
        (0, 0, "no_trade"),
        (1, 1, "no_trade"),
    ],
)
def test_classify_direction_uses_mutually_exclusive_barrier_winner(
    long_label: int, short_label: int, expected: str
) -> None:
    result = classify_direction(
        _outcome(long_label, 0.01 if long_label else -0.01),
        _outcome(short_label, 0.01 if short_label else -0.01),
    )

    assert result == expected


def test_dedupe_candidate_indices_keeps_earliest_signal_inside_gap() -> None:
    result = dedupe_candidate_indices(
        long_indices=[100, 130],
        short_indices=[105, 160],
        min_gap_bars=18,
    )

    assert result == [100, 130, 160]


def test_causal_window_is_unchanged_when_future_rows_change() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=260, freq="15min", tz="UTC"),
            "close": range(260),
        }
    )
    changed = frame.copy()
    changed.loc[220:, "close"] = 999_999

    baseline = causal_window(frame, signal_i=219, lookback_bars=200)
    mutated = causal_window(changed, signal_i=219, lookback_bars=200)

    pd.testing.assert_frame_equal(baseline, mutated)
    assert len(baseline) == 200
    assert baseline.iloc[-1]["open_time"] == frame.iloc[219]["open_time"]


def test_assign_temporal_splits_is_chronological_and_purged() -> None:
    manifest = pd.DataFrame(
        {
            "signal_time": pd.date_range(
                "2026-01-01", "2026-04-30", periods=30, tz="UTC"
            )
        }
    )

    split = assign_temporal_splits(manifest, horizon_bars=72, bar="15m")
    train = split[split["split"] == "train"]
    val = split[split["split"] == "val"]

    assert not train.empty
    assert not val.empty
    assert train["signal_time"].max() < val["signal_time"].min() - pd.Timedelta(hours=18)
    assert val["signal_time"].max() < HOLDOUT_START - pd.Timedelta(hours=18)


def test_assign_temporal_splits_rejects_holdout_signal() -> None:
    manifest = pd.DataFrame(
        {
            "signal_time": [
                pd.Timestamp("2026-01-01", tz="UTC"),
                HOLDOUT_START,
            ]
        }
    )

    with pytest.raises(HoldoutLeakError, match="holdout"):
        assign_temporal_splits(manifest, horizon_bars=72, bar="15m")
