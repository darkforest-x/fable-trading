"""Synthetic causality, strictness, and segmentation checks for trend baselines."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.trend_baseline_signals import signal_families


def _bars(close: np.ndarray | list[float], *, start: str = "2026-01-01") -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close},
        index=pd.date_range(start, periods=len(close), freq="15min", tz="UTC"),
    )


def test_donchian_uses_prior_bars_and_requires_a_strict_break() -> None:
    frame = _bars([100.0] * 62)
    # The close clears all prior highs; its own extreme must not enter that
    # prior channel.
    frame.iloc[60, frame.columns.get_loc("close")] = 102.0
    frame.iloc[60, frame.columns.get_loc("high")] = 1000.0
    frame.iloc[61, frame.columns.get_loc("close")] = 100.0
    got = signal_families(frame, 15)
    assert got.common_ready.tolist() == [False] * 60 + [True, True]
    assert got.loc[frame.index[60], ["dc20", "dc55"]].tolist() == [1, 1]

    equal = _bars([100.0] * 61)
    equal.iloc[60, :] = [101.0, 101.0, 99.0, 101.0]
    strict = signal_families(equal, 15)
    assert strict.loc[equal.index[60], ["dc20", "dc55"]].tolist() == [0, 0]


def test_future_suffix_cannot_change_completed_prefix_signals() -> None:
    close = 100.0 + np.linspace(0.0, 20.0, 160) + np.sin(np.arange(160))
    clean = _bars(close)
    changed = clean.copy()
    changed.iloc[120:, :] = [900.0, 901.0, 899.0, 900.0]
    expected = signal_families(clean, 15)
    actual = signal_families(changed, 15)
    pd.testing.assert_frame_equal(expected.iloc[:120], actual.iloc[:120], check_exact=True)


def test_gap_and_invalid_bar_each_require_a_fresh_common_warmup() -> None:
    original = _bars([100.0] * 140)
    gapped = original.drop(original.index[70])
    gap_signals = signal_families(gapped, 15)
    assert gap_signals.iloc[69].common_ready
    assert not gap_signals.iloc[70 + 59].common_ready
    assert gap_signals.iloc[70 + 60].common_ready

    invalid = original.copy()
    invalid.iloc[70, invalid.columns.get_loc("high")] = 99.0
    invalid_signals = signal_families(invalid, 15)
    assert not invalid_signals.iloc[70].common_ready
    assert not invalid_signals.iloc[70 + 59].common_ready
    assert not invalid_signals.iloc[70 + 60].common_ready
    assert invalid_signals.iloc[70 + 61].common_ready


def test_sma_crosses_once_per_direction_and_is_mirrored() -> None:
    frame = _bars([100.0] * 70 + [110.0] * 70 + [90.0] * 70)
    signal = signal_families(frame, 15).sma20_60
    assert signal.loc[signal.ne(0)].tolist() == [1, -1]


def test_rejects_clock_that_is_not_aligned_to_chart_bars() -> None:
    frame = _bars([100.0] * 61)
    frame.index = frame.index + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="align"):
        signal_families(frame, 15)
