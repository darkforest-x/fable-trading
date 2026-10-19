"""Contracts for delayed V12F/Grade-A YOLO economic replay."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.backtest_pine_eth_15m_v12f_yolo_fusion import (
    BacktestError,
    blocked_signflip,
    holm_adjust,
    materialize_delayed_events,
)


def _frame() -> pd.DataFrame:
    times = pd.date_range("2025-01-01", periods=6, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "atr": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            "atr_percent": [1.0, 1.09, 1.18, 1.26, 1.35, 1.43],
            "entry_allowed": [True, True, True, False, True, True],
        }
    )


def _fused(*, direction: str = "long", available: str = "2025-01-01T01:00:00Z") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fusion_id": "fusion-1",
                "pine_signal_id": "pine-1",
                "model_detection_id": "model-1",
                "symbol": "ETH_USDT_SWAP",
                "direction": direction,
                "pine_signal_i": 1,
                "combined_available_at": pd.Timestamp(available),
                "availability_delay_bars_from_pine": 2.0,
                "overlap_seconds": 0.0,
                "model_confidence": 0.75,
                "model_confirmation_bars": 2,
                "model_window_len": 18,
            }
        ]
    )


def test_delayed_event_uses_completed_bar_before_available_open() -> None:
    events = materialize_delayed_events(
        _frame(),
        _fused(),
        arm_name="model",
    )
    row = events.iloc[0]
    assert row["decision_i"] == 3
    assert row["entry_i"] == 4
    assert row["decision_bar_open"] == pd.Timestamp("2025-01-01T00:45:00Z")
    assert row["entry_time"] == pd.Timestamp("2025-01-01T01:00:00Z")
    assert row["decision_atr"] == pytest.approx(1.3)
    assert row["decision_close"] == pytest.approx(103.0)
    assert not bool(row["delayed_entry_allowed"])
    assert row["overlap_kind"] == "boundary_touch"


def test_delayed_event_rejects_time_outside_exact_bar_grid() -> None:
    with pytest.raises(BacktestError, match="absent from the 15m grid"):
        materialize_delayed_events(
            _frame(),
            _fused(available="2025-01-01T01:01:00Z"),
            arm_name="model",
        )


def test_delayed_event_rejects_opposite_directions_on_same_decision_bar() -> None:
    fused = pd.concat([_fused(), _fused(direction="short")], ignore_index=True)
    fused.loc[1, "fusion_id"] = "fusion-2"
    fused.loc[1, "pine_signal_id"] = "pine-2"
    fused.loc[1, "model_detection_id"] = "model-2"
    with pytest.raises(BacktestError, match="opposite fused directions"):
        materialize_delayed_events(_frame(), fused, arm_name="model")


def test_holm_adjustment_is_monotone_in_sorted_p_values() -> None:
    assert holm_adjust([0.01, 0.04, None, 0.03]) == pytest.approx(
        [0.03, 0.06, None, 0.06]
    )


def test_exact_week_signflip_preserves_whole_week_blocks() -> None:
    pairs = pd.DataFrame(
        {
            "candidate_entry_time": pd.to_datetime(
                ["2025-01-01", "2025-01-08", "2025-01-15"], utc=True
            ),
            "excess_return": [0.01, 0.02, 0.03],
        }
    )
    result = blocked_signflip(pairs, seed=1)
    assert result["method"] == "exact"
    assert result["utc_week_blocks"] == 3
    assert result["assignments_or_resamples"] == 8
    assert result["p_value_one_sided"] == pytest.approx(1 / 8)
