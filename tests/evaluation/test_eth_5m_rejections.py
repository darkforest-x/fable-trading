"""Tests for admission feature timing and structural meanings, not native replay."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.eth_5m_rejections import (
    five_features, attach, admissions, gain_inference,
)

CFG = dict(sweep_window=3, structure_window=12, dense_window=12,
           dense_prior_count=2, round_trip_cost=.002, watch_max_minutes=360,
           seed=3, bootstrap_samples=500)


def candles(start="2024-10-20", end="2024-11-12"):
    index = pd.date_range(start, end, freq="5min", tz="UTC", inclusive="right").as_unit("ns")
    value = 100+np.sin(np.arange(len(index))/17)*3+np.arange(len(index))*.001
    return pd.DataFrame(dict(open=value, high=value+1, low=value-1, close=value), index=index)


def test_prefix_features_unchanged_and_missing_bars_fail():
    bars = candles(end="2024-10-23")
    full = five_features(bars, CFG)
    pd.testing.assert_frame_equal(full.iloc[:333], five_features(bars.iloc[:333], CFG))
    with pytest.raises(ValueError, match="Missing"):
        five_features(bars.drop(bars.index[8]), CFG)


def test_sweep_uses_fixed_pre_window_reference_and_mirrors():
    index = pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC")
    b = pd.DataFrame(dict(open=100., high=110., low=90., close=100.), index=index)
    b.loc[index[-2], "low"] = 89
    b.loc[index[-1], "close"] = 91
    f = five_features(b, CFG)
    assert f.iloc[-1].sweep_long and f.iloc[-1].ref_low == 90
    b.loc[index[-1], "close"] = 89.5
    assert not five_features(b, CFG).iloc[-1].sweep_long
    b.loc[index[-1], "close"] = 91
    mirror = pd.DataFrame(dict(open=200-b.open, high=200-b.low,
                               low=200-b.high, close=200-b.close), index=index)
    assert five_features(mirror, CFG).iloc[-1].sweep_short


def test_prior_density_excludes_current_signal_and_watch_refresh_resets_count():
    bars = candles(end="2024-10-23")
    bars["high"], bars["low"] = bars.close+.01, bars.close-.01
    f = five_features(bars, CFG)
    expected = (f.arrow == 1).shift(1).rolling(12).sum()
    pd.testing.assert_series_equal(f.prior_long, expected, check_names=False)
    selected = np.flatnonzero(f.arrow.to_numpy() == 1)[-3:]
    i = selected[-1]
    same = bars.index[i-1].value
    old = bars.index[selected[0]-1].value
    rows = pd.DataFrame(dict(entry_i=[i, i], side=[1, 1], entry=[100., 100.],
                             risk=[1., 1.], h1_watch=[old, same]))
    result = attach(rows, f, CFG)
    assert result.watch_arrow_ordinal.tolist() == [3, 1]
    assert result.watch_age_minutes.iloc[1] == 5


def test_masks_ignore_outcomes_pass_unknown_ranges_and_keep_repeats_below_density():
    rows = pd.DataFrame(dict(cost_in_r=[.5, 1.1], directional_body=[1, -1],
        directional_close=[.5, .49], prior_arrows=[1, 2], watch_age_minutes=[360, 365],
        day_u=[np.nan, -.1], mon_u=[np.nan, -.2], local_reclaim=[True, False],
        k_extreme=[True, False], net_r=[-100, 100], gross_r=[20, -1]))
    mask = admissions(rows, CFG)
    assert len(mask) == 11
    for name, value in mask.items():
        if name != "baseline":
            np.testing.assert_array_equal(value, [True, False])
    for key, value in admissions(rows.assign(net_r=999, gross_r=999), CFG).items():
        np.testing.assert_array_equal(value, mask[key])


def test_gain_compares_same_week_resamples_and_unchanged_mask_is_zero():
    rows = pd.DataFrame(dict(week=np.repeat(np.arange(10), 2),
                             net_notional_bp=np.tile([-30., 10.], 10)))
    same = gain_inference(rows, np.ones(len(rows), bool), CFG)
    assert same["mean"] == 0 and same["ci95"] == [0., 0.]
    better = gain_inference(rows, rows.net_notional_bp > 0, CFG)
    assert better["mean"] == 20 and better["ci95"] == [20., 20.]
    assert better["p"] < .01
