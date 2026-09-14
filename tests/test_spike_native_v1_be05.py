"""Causal ordering tests for the native V1 0.5R paired replay."""
from __future__ import annotations

import pandas as pd

from yoyo.evaluation.spike_native_v1_be05 import _frozen_stop, _native_exit, _paired_summary


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


def _replayed(index: pd.DatetimeIndex, protections: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"trend_side": [False] * len(index), "protection": protections}, index=index)


def test_stop_precedes_same_bar_mfe_trigger() -> None:
    bars = _bars([(100, 106, 89, 100), (99, 100, 98, 99)])
    result = _native_exit(bars, _replayed(bars.index, [float("nan")] * 2), 0, 90, use_be=True)
    assert result["exit_reason"] == "protective_stop"
    assert result["exit_price"] == 90
    assert result["be_triggered"] is False


def test_mfe_trigger_is_effective_only_on_following_bar() -> None:
    bars = _bars([(100, 106, 95, 105), (99, 100, 98, 99)])
    result = _native_exit(bars, _replayed(bars.index, [float("nan")] * 2), 0, 90, use_be=True)
    assert result["be_triggered"] is True
    assert result["be_trigger_bar_open"] == bars.index[0]
    assert result["exit_time"] == bars.index[1] + pd.Timedelta(hours=1)
    assert result["exit_price"] == 99  # next-bar gap through entry-level protection


def test_be_never_relaxes_existing_tighter_native_protection() -> None:
    bars = _bars([(100, 106, 96, 105), (104, 105, 103, 104)])
    replayed = _replayed(bars.index, [105, float("nan")])
    replayed.loc[bars.index[0], "trend_side"] = True
    result = _native_exit(bars, replayed, 0, 90, use_be=True)
    assert result["be_triggered"] is True
    assert result["exit_price"] == 104  # 105 native stop remains tighter than entry=100


def test_be_remains_active_after_later_lower_native_reference() -> None:
    bars = _bars([(100, 106, 96, 105), (104, 105, 103, 104), (100, 101, 99, 100)])
    replayed = _replayed(bars.index, [95, 95, 95])
    replayed.loc[:, "trend_side"] = True
    result = _native_exit(bars, replayed, 0, 90, use_be=True)
    assert result["be_triggered"] is True
    assert result["exit_price"] == 100
    assert result["exit_time"] == bars.index[2] + pd.Timedelta(hours=1)


def test_csv_risk_subtraction_recovers_the_original_tick_stop() -> None:
    assert _frozen_stop(0.005751, 0.001, 0.000001) == 0.004751


def test_summary_uses_the_two_frozen_year_blocks() -> None:
    frame = pd.DataFrame({"event_id": ["a", "b"], "entry_time": ["2025-09-09T23:00:00Z", "2025-09-10T00:00:00Z"],
                          "baseline_exit_time": ["2025-09-09T23:30:00Z", "2025-09-10T00:30:00Z"], "be05_exit_time": ["2025-09-09T23:30:00Z", "2025-09-10T00:30:00Z"],
                          "baseline_censored": [False, False], "be05_censored": [False, False], "baseline_net_r": [1., 1.], "be05_net_r": [1., 1.],
                          "baseline_mfe_r": [1., 1.], "be05_mfe_r": [1., 1.], "timeframe_min": [30, 30]})
    summary = _paired_summary(frame)
    yearly = summary.loc[summary.entry_period.notna(), "entry_period"].unique().tolist()
    assert yearly == ["2024-09-10..2025-09-10", "2025-09-10..2026-09-10"]
