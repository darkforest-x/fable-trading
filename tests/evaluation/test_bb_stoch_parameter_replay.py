"""Parity and causal contracts for parameterized BB × Stoch replay."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import bb_stoch_parameter_replay as parameterized
from yoyo.evaluation import bb_stoch_replay as frozen
from yoyo.evaluation.spike_fanshen_exit import compute_signals as source_stoch


def _random_bars(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.35, n))
    open_ = close + rng.normal(0.0, 0.08, n)
    high = np.maximum(open_, close) + rng.uniform(.01, .55, n)
    low = np.minimum(open_, close) - rng.uniform(.01, .55, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
    )


def _forced_features(side: int = 1) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = _random_bars(240, 91)
    legacy = frozen.compute_features(raw)
    current = parameterized.compute_features(raw)
    for frame in (legacy, current):
        frame.loc[:, "signal"] = 0
        frame.iloc[200, frame.columns.get_loc("signal")] = side
        frame.loc[:, "target_upper"] = 103.0
        frame.loc[:, "target_lower"] = 97.0
    return legacy, current


@pytest.mark.parametrize("n, seed", [(500, 7), (1273, 19), (2000, 41)])
def test_default_features_and_replay_dicts_match_frozen_engine(n: int, seed: int) -> None:
    raw = _random_bars(n, seed)
    legacy = frozen.compute_features(raw)
    current = parameterized.compute_features(raw)
    pd.testing.assert_frame_equal(current, legacy, check_exact=True)

    # Force both sides and changing resting limits, then compare the complete
    # public trade dictionary (including flags, fills, and censoring fields).
    for signal_i, side, target in ((205, 1, 104.0), (401, -1, 96.0), (799, 1, 101.0)):
        if signal_i + 24 >= n:
            continue
        old_case, new_case = legacy.copy(), current.copy()
        for frame in (old_case, new_case):
            frame.loc[:, "signal"] = 0
            frame.iloc[signal_i, frame.columns.get_loc("signal")] = side
            frame.loc[:, "target_upper"] = target if side == 1 else 110.0
            frame.loc[:, "target_lower"] = target if side == -1 else 90.0
        expected = frozen.replay_entry(old_case, signal_i, end_i=signal_i + 24)
        actual = parameterized.replay_entry(parameterized.prepare(new_case), signal_i, end_i=signal_i + 24)
        assert actual == expected


def test_default_stoch_is_source_pine_sma_and_custom_bb_algebra_is_causal() -> None:
    raw = _random_bars(80, 5)
    default = parameterized.compute_features(raw)
    source = source_stoch(raw.loc[:, ["high", "low", "close"]])
    pd.testing.assert_series_equal(default.k, source.k, check_exact=True)
    pd.testing.assert_series_equal(default.d, source.d, check_exact=True)

    spec = parameterized.ParamSpec(bb_length=10, bb_mult=1.5, stop_fraction=.04,
                                   stoch_length=7, k_smooth=2, d_smooth=4, oversold=25)
    got = parameterized.compute_features(raw, spec)
    i = 25
    current_window = raw.close.iloc[i - spec.bb_length + 1:i + 1].to_numpy()
    prior_window = raw.close.iloc[i - (spec.bb_length - 1):i].to_numpy()
    expected_upper = current_window.mean() + spec.bb_mult * current_window.std(ddof=0)
    raw_target = prior_window.mean() + spec.bb_mult * prior_window.std(ddof=0) * math.sqrt(
        spec.bb_length / (spec.bb_length - 1 - spec.bb_mult ** 2)
    )
    assert got.upper.iloc[i] == pytest.approx(expected_upper)
    assert got.target_upper.iloc[i] == pytest.approx(math.ceil(raw_target / .01) * .01)
    assert got.target_lower.iloc[i] == pytest.approx(math.floor((2 * prior_window.mean() - raw_target) / .01) * .01)

    poisoned = raw.copy()
    poisoned.iloc[i + 1:, :] = [1000.0, 1001.0, 999.0, 1000.0]
    prefix = parameterized.compute_features(poisoned, spec)
    pd.testing.assert_frame_equal(got.iloc[:i + 1], prefix.iloc[:i + 1], check_exact=True)


def test_stop_parameter_changes_risk_but_preserves_frozen_event_accounting() -> None:
    legacy, current = _forced_features()
    for frame in (legacy, current):
        frame.loc[:, "target_upper"] = 110.0
        frame.iloc[201, [frame.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 96., 100.]
    three = parameterized.replay_entry(parameterized.prepare(current), 200, end_i=202)
    six = parameterized.replay_entry(
        parameterized.prepare(current, parameterized.ParamSpec(stop_fraction=.06)), 200, end_i=202
    )
    assert three["exit_reason"] == "initial_stop" and three["initial_risk"] == pytest.approx(3.0)
    assert six["exit_reason"] == "boundary_censor" and six["initial_risk"] == pytest.approx(6.0)
    assert three["fees"] == pytest.approx(.1 + .097)
    assert six["fees"] == pytest.approx(.1)  # no invented fee for an unliquidated boundary mark
    assert parameterized.replay_entry(parameterized.prepare(current), 200, end_i=202) == frozen.replay_entry(legacy, 200, end_i=202)


def test_invalid_dynamic_target_censors_with_the_frozen_schema() -> None:
    legacy, current = _forced_features()
    for frame in (legacy, current):
        frame.loc[:, "target_upper"] = 110.0
        frame.iloc[202, frame.columns.get_loc("target_upper")] = np.inf
        frame.iloc[201, [frame.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 99., 100.]
    expected = frozen.replay_entry(legacy, 200, end_i=203)
    actual = parameterized.replay_entry(parameterized.prepare(current), 200, end_i=203)
    assert actual == expected
    assert actual["exit_reason"] == "target_unavailable_censor" and actual["censored"]


@pytest.mark.parametrize("kwargs", [
    {"bb_length": 2}, {"bb_mult": 0}, {"bb_length": 5, "bb_mult": 2},
    {"stop_fraction": 0}, {"stop_fraction": 1}, {"stoch_length": 0},
    {"k_smooth": True}, {"oversold": 0}, {"oversold": 50},
])
def test_parameter_validation_rejects_unusable_or_noncausal_specs(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parameterized.ParamSpec(**kwargs)


def test_prepare_copies_feature_arrays_once_and_replay_rejects_invalid_bounds() -> None:
    _, features = _forced_features()
    prepared = parameterized.prepare(features)
    original = parameterized.replay_entry(prepared, 200, end_i=202)
    features.iloc[201, features.columns.get_loc("open")] = 1_000.0
    assert parameterized.replay_entry(prepared, 200, end_i=202) == original
    with pytest.raises(ValueError, match="exclusive"):
        parameterized.replay_entry(prepared, 200, end_i=len(features) + 1)
    with pytest.raises(TypeError, match="prepare"):
        parameterized.replay_entry(features, 200)  # type: ignore[arg-type]
