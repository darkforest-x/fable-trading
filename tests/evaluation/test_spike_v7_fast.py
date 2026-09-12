"""Parity and bounded timing evidence for the array-backed V7 replay helper."""
from __future__ import annotations

from time import perf_counter

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v6_wvf_study as reference
from yoyo.evaluation import spike_v7_fast as fast
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v7_v1_compare import reference_v7_diagnostics


def _market(n: int = 140, seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .55, n))
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, .1, n)
    high = np.maximum(open_, close) + rng.uniform(.1, 1.0, n)
    low = np.minimum(open_, close) - rng.uniform(.1, 1.0, n)
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "atr": 1.3},
                         index=pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"))
    frame.attrs["minutes"] = 60
    return frame


def _signals(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"long_signal": False, "short_signal": False}, index=frame.index)
    long_i = [i for i in (5, 20, 40, 80, 112) if i < len(frame)]
    short_i = [i for i in (13, 32, 65, 101) if i < len(frame)]
    out.iloc[long_i, 0] = True
    out.iloc[short_i, 1] = True
    return out


def _assert_replay_equal(frame: pd.DataFrame, signals: pd.DataFrame, admission: pd.Series, gap: pd.Series) -> None:
    kwargs = dict(admission=admission, variant="v7_test", data_gap=gap, spec=reference.ExecutionSpec(tick=.1))
    ref_ledger, ref_trades = reference.simulate_v6_variant(frame, signals, **kwargs)
    fast_ledger, fast_trades = fast.simulate_v6_variant(frame, signals, **kwargs)
    pd.testing.assert_frame_equal(fast_ledger, ref_ledger, check_exact=True)
    pd.testing.assert_frame_equal(fast_trades, ref_trades, check_exact=True)


def test_reference_source_hashes_fail_closed_and_are_current():
    fast.assert_reference_sources_unchanged()
    assert len(fast.REFERENCE_SOURCE_SHA256) == 4


def test_execution_parity_seeded_paths_with_reverse_stop_gap_and_boundary():
    frame = _market()
    signals = _signals(frame)
    # Force both stop priority paths while retaining a reverse and final mark.
    frame.loc[frame.index[6], ["open", "high", "low", "close"]] = [100, 104, 94, 99]
    frame.loc[frame.index[21], ["open", "high", "low", "close"]] = [92, 95, 88, 90]
    frame.loc[frame.index[33], ["open", "high", "low", "close"]] = [120, 122, 115, 116]
    admission = pd.Series(True, index=frame.index)
    admission.iloc[20] = False  # raw reverse remains an exit while its entry is denied.
    gap = pd.Series(False, index=frame.index)
    gap.iloc[90] = True
    _assert_replay_equal(frame, signals, admission, gap)


def test_sparse_signal_ledger_matches_reference_including_empty_schema():
    frame = _market(20)
    signals = _signals(frame)
    reclaim = pd.DataFrame({"wvf_admitted": np.arange(len(frame)) % 2 == 0,
                            "wvf_filter_reason": ["admitted"] * len(frame),
                            "wvf_current_extreme": False, "wvf_last_extreme_i": np.nan}, index=frame.index)
    expected = reference.make_signal_ledger(signals, reclaim, variant="ledger", minutes=60)
    actual = fast._make_signal_ledger_fast(signals, reclaim, variant="ledger", minutes=60)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    empty = signals.assign(long_signal=False, short_signal=False)
    pd.testing.assert_frame_equal(fast._make_signal_ledger_fast(empty, None, variant="ledger", minutes=60),
                                  reference.make_signal_ledger(empty, None, variant="ledger", minutes=60), check_exact=True)


def test_execution_parity_preserves_short_stop_and_censored_boundary():
    frame = _market(80, seed=31)
    signals = _signals(frame).iloc[:80].copy()
    signals.loc[:, :] = False
    signals.iloc[10, signals.columns.get_loc("short_signal")] = True
    frame.loc[frame.index[11], ["open", "high", "low", "close"]] = [100, 108, 96, 101]
    signals.iloc[-3, signals.columns.get_loc("long_signal")] = True
    _assert_replay_equal(frame, signals, pd.Series(True, index=frame.index), pd.Series(False, index=frame.index))


def test_v6_signal_parity_on_seeded_feature_path_and_bounded_timing():
    raw = _market(3_000, seed=71).drop(columns="atr")
    raw["volume"] = 500.0 + np.arange(len(raw))
    raw["quote_volume"] = raw.volume * raw.close
    featured = features(raw)
    featured.attrs["minutes"] = 60
    before = perf_counter()
    reference_out = reference.v6_signals(featured, 60)
    reference_seconds = perf_counter() - before
    before = perf_counter()
    fast_out = fast.v6_signals(featured, 60)
    fast_seconds = perf_counter() - before
    pd.testing.assert_frame_equal(fast_out, reference_out, check_exact=True)
    # Timing is recorded as evidence without a brittle machine-speed ratio gate.
    assert reference_seconds > 0 and fast_seconds > 0


@pytest.mark.parametrize("constant", [False, True])
def test_v7_diagnostic_parity_on_prefix_gaps_and_constant_prices(constant: bool):
    bars = _market(1_600, seed=91)
    if constant:
        bars.loc[:, ["open", "high", "low", "close"]] = [100., 101., 99., 100.]
    gap = pd.Series(False, index=bars.index)
    gap.iloc[745] = True
    # The invalid row shares the reference segment behavior and must not leak a
    # prior threshold or squeeze run into the later prefix.
    bars.loc[bars.index[1_070], "close"] = np.nan
    expected = reference_v7_diagnostics(bars, data_gap=gap)
    actual = fast.v7_diagnostics(bars, data_gap=gap)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("side", [1, -1])
def test_legacy_v4_parity_on_seeded_feature_path(side: int):
    raw = _market(500, seed=83).drop(columns="atr")
    raw["volume"] = 200.0 + np.arange(len(raw))
    raw["quote_volume"] = raw.volume * raw.close
    featured = features(raw)
    pd.testing.assert_frame_equal(fast._legacy_v4(featured, 60, side), reference._legacy_v4(featured, 60, side), check_exact=True)
