"""Causal HTF availability and independent raw-exit preservation."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate
from yoyo.evaluation.spike_v9_htf_sma_study import execute
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation import spike_v10_4_study as source


def candles(n=72):
    ix = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    close = np.repeat(np.arange((n + 11) // 12) + 100., 12)[:n]
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 10.}, index=ix)


def test_exact_hour_boundary_uses_only_previously_closed_higher_candle():
    b = candles(); ix = pd.date_range("2025-01-01 01:45", periods=3, freq="15min", tz="UTC")
    f = confirmed_sma(b, ix, 60, (1, 2))
    assert f.sma_1.tolist() == [100., 101., 101.]
    assert np.isnan(f.sma_2.iloc[0])
    assert f.sma_2.iloc[1] == 100.5
    assert (f.htf_close_time <= f.index).all()


def test_direction_equality_and_unknown_fail_closed():
    assert side_gate([101, 99, 100, 101, np.nan, 100], [1, -1, 1, 0, 1, -1], [100]*6).tolist() == [True, True, False, False, False, False]


def test_future_suffix_cannot_change_prefix():
    b = candles(); ix = pd.date_range("2025-01-01", periods=24, freq="15min", tz="UTC")
    before = confirmed_sma(b, ix, 60, (1, 2, 3))
    changed = b.copy(); changed.loc[changed.index >= "2025-01-01 03:00", ["open", "high", "low", "close"]] *= 2
    after = confirmed_sma(changed, ix, 60, (1, 2, 3))
    pd.testing.assert_frame_equal(before.loc[:"2025-01-01 03:00"], after.loc[:"2025-01-01 03:00"])


@pytest.mark.parametrize("kind", ["missing", "invalid", "whole_hour"])
def test_missing_bucket_breaks_sma_and_stale_carry(kind):
    b = candles(); ix = pd.date_range("2025-01-01 01:00", periods=5, freq="h", tz="UTC")
    if kind == "missing": b = b.drop(b.index[15])
    elif kind == "invalid": b.iloc[15, b.columns.get_loc("low")] = 999
    else: b = b.drop(b.index[12:24])
    f = confirmed_sma(b, ix, 60, (1, 2))
    assert np.isnan(f.sma_1.iloc[1])
    assert np.isnan(f.sma_2.iloc[1]) and np.isnan(f.sma_2.iloc[2])
    assert f.sma_2.iloc[3] == 102.5
    later = confirmed_sma(b.iloc[:12], ix, 60, (1,))
    assert later.sma_1.iloc[0] == 100 and later.sma_1.iloc[1:].isna().all()


def test_empty_and_duplicate_clock():
    b = candles(); ix = b.index[:0]
    assert confirmed_sma(b, ix, 60, (20,)).empty
    assert confirmed_sma(b.iloc[:0], b.index, 60, (20,)).sma_20.isna().all()
    with pytest.raises(ValueError): confirmed_sma(pd.concat([b, b]), b.index, 60, (20,))


def test_rejecting_reverse_entry_keeps_raw_opposite_exit():
    ix = pd.date_range("2025-01-06", periods=24, freq="h", tz="UTC")
    bars = pd.DataFrame({"open":100., "high":100.2, "low":99.8, "close":100., "atr":1.}, index=ix)
    raw = np.zeros(len(ix), int); raw[6] = 1; raw[12] = -1
    p = source.prepared_arm(bars, np.zeros(len(ix), bool), raw, "synthetic", {"timeframe":"1h"}, 60, .01)
    allowed = p.allowed.copy(); allowed[6] = True
    trades, _ = execute(p, allowed, "actual", 50)
    assert len(trades) == 1
    assert trades.side.iloc[0] == 1
    # Reference engine must receive the unfiltered short at bar12, even though
    # treatment allows no short entries. Compare with the same path sans raw short.
    assert trades.exit_i.iloc[0] == 13
    assert trades.exit_reason.iloc[0] == "opposite_v6_next_open"
    isolated = p.raw_side.copy(); isolated[12:] = 0
    without, _ = execute(replace(p, raw_side=isolated), allowed, "actual", 50)
    assert p.raw_side[12] == -1
    assert len(without) == 1 and without.censored.iloc[0]
