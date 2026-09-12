"""Causality and accounting contracts for SPIKE V8 discovery features."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation.spike_v8_noise_study import _auc, signal_feature_frame


def inputs(n: int = 760):
    index = pd.date_range("2024-09-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(100 + np.sin(np.arange(n) / 13) + np.arange(n) * 0.002, index=index)
    bars = pd.DataFrame(index=index)
    bars["open"] = close.shift(1).fillna(close.iloc[0])
    bars["close"] = close
    bars["high"] = bars[["open", "close"]].max(axis=1) + 0.5
    bars["low"] = bars[["open", "close"]].min(axis=1) - 0.5
    bars["volume"] = 1000.0
    bars["tr"] = bars.high - bars.low
    bars["atr"] = 1.0
    bars["rv"] = 1.0
    bars["expansion"] = 1.0
    bars["ropeHigh"] = close - 0.25
    bars["ropeLow"] = close + 0.25
    bars["width"] = 1.0
    bars["md"] = close.diff().fillna(0)
    bars["sb"] = bars.md.rolling(3, min_periods=1).mean()
    bars["middle"] = close
    bars["pastWidth"] = 2.0
    bars["pastCrosses"] = 3.0
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[[710, 720]], "long_signal"] = True
    bb = pd.DataFrame(index=index)
    bb["bb_width"] = 0.04
    bb["bb_width_p10_prior500"] = 0.05
    bb["bb_compressed"] = False
    bb.loc[index[700:706], "bb_compressed"] = True
    bb["prior_squeeze_run3"] = False
    bb.loc[index[706:730], "prior_squeeze_run3"] = True
    bb["v7_ready"] = True
    return bars, signals, bb


def test_future_prices_cannot_change_existing_signal_features():
    bars, signals, bb = inputs()
    full = signal_feature_frame(bars, signals, bb, tick=0.01, minutes=60)
    cut = 715
    prefix = signal_feature_frame(bars.iloc[:cut], signals.iloc[:cut], bb.iloc[:cut], tick=0.01, minutes=60)
    assert_frame_equal(full.iloc[:1].reset_index(drop=True), prefix.reset_index(drop=True))


def test_directional_features_mirror_short_semantics():
    bars, signals, bb = inputs()
    signals.loc[:, :] = False
    i = 710
    signals.loc[bars.index[i], "short_signal"] = True
    bars.loc[bars.index[i], ["open", "close", "high", "low", "ropeLow", "md", "sb"]] = [101, 99, 101.2, 98.8, 100, -2, -1]
    result = signal_feature_frame(bars, signals, bb, tick=0.01, minutes=60).iloc[0]
    assert result.side == -1
    assert result.rope_distance_atr > 0
    assert result.md_gap_atr > 0
    assert 0 <= result.directional_end_position <= 1


def test_candidate_gates_are_explicit_and_v1_hard_is_subset():
    bars, signals, bb = inputs()
    bars.loc[bars.index[710], ["rv", "expansion"]] = [5.0, 3.5]
    result = signal_feature_frame(bars, signals, bb, tick=0.01, minutes=60)
    gates = result.filter(regex="^gate_")
    assert not gates.isna().any().any()
    assert gates.dtypes.eq(bool).all()
    assert (~gates.gate_v1_hard_impulse | (result.current_volume_ratio.ge(4) & result.current_tr_expansion.ge(3))).all()


def test_auc_is_tie_aware_and_directional():
    label = pd.Series([False, False, True, True])
    assert _auc(pd.Series([0, 1, 2, 3]), label) == 1.0
    assert _auc(pd.Series([3, 2, 1, 0]), label) == 0.0
    assert _auc(pd.Series([1, 1, 1, 1]), label) == 0.5
