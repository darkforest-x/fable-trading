"""Parameterized SPIKE thresholds equal the frozen literals at the default; neighbour smoothing."""
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v129_param_opt as study
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v126_htf_recheck import _legacy
from yoyo.evaluation.spike_v7_fast import _data_gap, _detect_confirmed_fast


def _bars(n=3000, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .004, n) + np.where(np.arange(n) % 400 < 40, .002, 0)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, .002, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, .002, n))
    vol = rng.lognormal(3, .6, n) * np.where(np.arange(n) % 400 < 40, 4, 1)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def test_default_thresholds_reproduce_frozen_functions():
    frame = features(_bars())
    gap = _data_gap(frame, 15).to_numpy(bool)
    med = frame.volume.rolling(20, min_periods=20).median()
    advance = (frame.close - frame.close.shift(3)) / frame.atr.shift(3)
    volume_ratio = (frame.volume.rolling(3).sum() / (3 * med.shift(3))).where(med.shift(3).gt(0))
    hits = 0
    for side in (1, -1):
        ref = _legacy(frame, gap, advance, volume_ratio, side)
        ours = study.legacy_param(frame, gap, advance, volume_ratio, side, 3.0, 1.5, 1.5)
        for a, b in zip(ref, ours):
            np.testing.assert_array_equal(a, b)
        supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
        supplied["legacy_confirmed"], supplied["legacy_parent_high"], supplied["legacy_parent_low"] = ref
        supplied["advance3"], supplied["volume_ratio3"], supplied["data_gap"], supplied["confirmed"] = advance, volume_ratio, gap, True
        np.testing.assert_array_equal(_detect_confirmed_fast(supplied, side), study.detect_param(supplied, side, 1.5, 1.5))
        hits += int(ref[0].sum())
    assert hits > 0  # the synthetic bursts exercise the confirmation path


def test_neighbour_smoothing_uses_eligible_axis_neighbours():
    cfg = {"grid": {"dense_width_atr": [2.0, 3.0], "advance_atr": [1.0, 1.5], "volume_ratio": [1.0, 1.5]}}
    rows = []
    for w in (2.0, 3.0):
        for a in (1.0, 1.5):
            for v in (1.0, 1.5):
                rows.append({"dense_width_atr": w, "advance_atr": a, "volume_ratio": v, "select_n": 100 if (w, a, v) != (2.0, 1.0, 1.0) else 10,
                             "select_mean_r": 1.0 if (w, a, v) == (3.0, 1.5, 1.5) else 0.0})
    g = study.smoothed(pd.DataFrame(rows), cfg)
    best = g.loc[(g.dense_width_atr == 3.0) & (g.advance_atr == 1.5) & (g.volume_ratio == 1.5)].select_smoothed.iloc[0]
    assert abs(best - 0.25) < 1e-12  # itself (1.0) plus three eligible neighbours at 0.0
    assert np.isnan(g.loc[(g.dense_width_atr == 2.0) & (g.advance_atr == 1.0) & (g.volume_ratio == 1.0)].select_smoothed.iloc[0])


def test_frozen_config():
    cfg = study.config()
    assert len(cfg["grid"]["dense_width_atr"]) * len(cfg["grid"]["advance_atr"]) * len(cfg["grid"]["volume_ratio"]) == 125
    assert cfg["production_eligible"] is False
