"""Fifteen-minute admission must remain causal through boundaries and gaps."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v9_5m_15m_ma import confirmed_ma, execute
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate
from yoyo.evaluation import spike_v10_4_study as source


def candles(n=180):
    ix = pd.date_range("2025-01-06", periods=n, freq="5min", tz="UTC")
    price = np.repeat(100. + np.arange((n + 2) // 3), 3)[:n]
    return pd.DataFrame(dict(open=price, high=price + .2, low=price - .2,
                             close=price, volume=np.ones(n)), index=ix)


def test_exact_boundary_and_signal_ending_on_boundary_does_not_peek():
    b = candles()
    ix = pd.date_range("2025-01-06 00:10", periods=3, freq="5min", tz="UTC")
    f = confirmed_ma(b, ix, (1,), 1)
    assert np.isnan(f.sma_1.iloc[0])  # This signal closes00:15, still uses00:10's knowledge.
    assert f.sma_1.iloc[1:].tolist() == [100., 100.]
    assert f.ema_1.iloc[1:].tolist() == [100., 100.]
    assert (f.htf_close_time.dropna() <= f.index[1:]).all()


def test_sma_matches_existing_helper_and_ema_matches_scalar_recurrence():
    b = candles()
    f = confirmed_ma(b, b.index, (2, 3), 10)
    pd.testing.assert_series_equal(f.sma_3, confirmed_sma(b, b.index, 15, (3,)).sma_3)
    close = b.close.resample("15min").last()
    value = None
    for i, (stamp, price) in enumerate(close.items()):
        value = price if value is None else .5 * price + .5 * value
        clock = stamp + pd.Timedelta(minutes=15)
        if clock not in f.index:
            continue
        if i + 1 < 30:
            assert np.isnan(f.loc[clock, "ema_3"])
        else:
            assert f.loc[clock, "ema_3"] == pytest.approx(value)


@pytest.mark.parametrize("kind", ["missing", "whole_bucket", "invalid"])
def test_gap_resets_both_ma_windows_and_ema_seed(kind):
    b = candles()
    if kind == "missing":
        b = b.drop(b.index[61])
    elif kind == "whole_bucket":
        b = b.drop(b.index[60:63])
    else:
        b.iloc[61, b.columns.get_loc("low")] = 9999.
    f = confirmed_ma(b, pd.date_range("2025-01-06", periods=181, freq="5min", tz="UTC"), (2,), 2)
    assert np.isnan(f.loc["2025-01-06 05:15", "sma_2"])
    assert np.isnan(f.loc["2025-01-06 05:30", "sma_2"])
    assert f.loc["2025-01-06 05:45", "sma_2"] == 121.5
    assert np.isnan(f.loc["2025-01-06 06:00", "ema_2"])
    assert f.loc["2025-01-06 06:15", "ema_2"] == pytest.approx(123.5185185185)


def test_mutating_future_suffix_cannot_change_prior_values():
    b = candles()
    before = confirmed_ma(b, b.index, (2, 3), 2)
    changed = b.copy()
    changed.loc[changed.index >= "2025-01-06 04:00", ["open", "high", "low", "close"]] *= 3
    after = confirmed_ma(changed, changed.index, (2, 3), 2)
    pd.testing.assert_frame_equal(before.loc[:"2025-01-06 04:00"], after.loc[:"2025-01-06 04:00"])


def test_no_stale_carry_equality_unknown_empty_and_duplicate():
    b = candles(6)
    ix = pd.date_range("2025-01-06 00:30", periods=4, freq="5min", tz="UTC")
    f = confirmed_ma(b, ix, (1,), 1)
    assert f.sma_1.iloc[:3].eq(101.).all() and np.isnan(f.sma_1.iloc[3])
    assert f.ema_1.iloc[:3].eq(101.).all() and np.isnan(f.ema_1.iloc[3])
    assert side_gate([101, 99, 100, 101], [1, -1, -1, 1], [100, 100, 100, np.nan]).tolist() == [True, True, False, False]
    assert confirmed_ma(b.iloc[:0], ix, (1,)).ema_1.isna().all()
    with pytest.raises(ValueError):
        confirmed_ma(pd.concat([b, b]), ix, (1,))


def test_blocked_short_entry_still_exits_long_next_open():
    ix = pd.date_range("2025-01-06", periods=24, freq="5min", tz="UTC")
    b = pd.DataFrame(dict(open=100., high=100.2, low=99.8, close=100., atr=1.), index=ix)
    raw = np.zeros(len(ix), int)
    raw[6], raw[12] = 1, -1
    p = source.prepared_arm(b, np.zeros(len(ix), bool), raw, "synthetic", {"timeframe": "5m"}, 5, .01)
    allowed = np.zeros(len(ix), bool)
    allowed[6] = True
    trades, _ = execute(p, allowed, "common", "ema20")
    assert len(trades) == 1 and trades.exit_i.iloc[0] == 13
    assert trades.exit_reason.iloc[0] == "opposite_v6_next_open"
    raw2 = raw.copy()
    raw2[12] = 0
    without, _ = execute(replace(p, raw_side=raw2), allowed, "common", "ema20")
    assert len(without) == 1 and without.censored.iloc[0]
