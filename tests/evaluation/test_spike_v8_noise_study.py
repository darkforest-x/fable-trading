"""Causality and accounting contracts for SPIKE V8 discovery features."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

import pytest

from yoyo.evaluation.spike_v8_noise_study import (
    OUTCOME_VALUE_COLUMNS,
    _assert_validation_outcomes_withheld,
    _auc,
    _outcome_scope_metadata,
    join_frozen_outcomes,
    signal_feature_frame,
)


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


def test_join_only_materializes_development_outcomes_and_marks_validation_withheld():
    signal_times = pd.to_datetime(["2025-09-01T00:00:00Z", "2025-10-01T00:00:00Z"])
    features = pd.DataFrame(
        {
            "stream_key": ["s", "s"],
            "signal_bar_open": signal_times,
            "side": [1, -1],
            "period": ["development", "validation"],
            "timeframe_min": [60, 60],
            "cost_share_of_close_r": [0.1, 0.1],
        }
    )
    trades = pd.DataFrame(
        {
            "arm": ["baseline"], "period": ["development"],
            "stream_key": ["s"], "signal_bar_open": signal_times[:1],
            "side": [1], "trade_id": ["development-trade"],
            "entry_time": signal_times[:1],
            "exit_time": signal_times[:1] + pd.Timedelta(hours=1),
            "exit_reason": ["trailing"], "initial_risk_frac": [0.02],
            "mfe_r": [11.0], "net_return": [0.03], "net_r": [1.5],
            "gross_return": [0.032], "gross_r": [1.6],
            "censored": [False], "timeframe_min": [60],
        }
    )

    joined = join_frozen_outcomes(features, trades)
    development = joined.loc[joined.period.eq("development")].iloc[0]
    validation = joined.loc[joined.period.eq("validation")]

    assert development.outcome_available
    assert not development.outcome_withheld_validation
    assert validation.outcome_available.eq(False).all()
    assert validation.outcome_withheld_validation.eq(True).all()
    assert validation.loc[:, OUTCOME_VALUE_COLUMNS].isna().all().all()
    assert _outcome_scope_metadata(joined) == {
        "validation_outcomes_present": False,
        "outcome_scope": "development_only",
    }


def test_join_rejects_a_combined_development_and_validation_outcome_input():
    signal_time = pd.Timestamp("2025-09-01T00:00:00Z")
    features = pd.DataFrame({
        "stream_key": ["s"], "signal_bar_open": [signal_time], "side": [1],
        "period": ["development"], "timeframe_min": [60], "cost_share_of_close_r": [0.1],
    })
    trades = pd.DataFrame({
        "arm": ["baseline"], "period": ["validation"], "stream_key": ["s"],
        "signal_bar_open": [signal_time], "side": [1],
    })
    with pytest.raises(ValueError, match="separate development-only artifact"):
        join_frozen_outcomes(features, trades)


def test_validation_outcome_isolation_assertion_rejects_a_leaked_value():
    frame = pd.DataFrame(
        {
            "period": ["validation"],
            "outcome_available": [False],
            "outcome_withheld_validation": [True],
            **{column: [pd.NA] for column in OUTCOME_VALUE_COLUMNS},
        }
    )
    frame.loc[0, "net_return"] = 0.01

    with pytest.raises(AssertionError, match="validation rows contain withheld outcome values: net_return"):
        _assert_validation_outcomes_withheld(frame)
