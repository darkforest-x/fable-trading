"""Focused formula and event-path contracts for BB × Stoch v2 replay."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import bb_stoch_replay as replay


def _bars(n: int = 208) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = np.linspace(100.0, 101.0, n)
    return pd.DataFrame({"open": close, "high": close + .4, "low": close - .4, "close": close}, index=index)


def _features(side: int = 1, n: int = 208) -> pd.DataFrame:
    bars = _bars(n)
    f = replay.compute_features(bars)
    f.loc[:, "signal"] = 0
    f.iloc[200, f.columns.get_loc("signal")] = side
    f.loc[:, "target_upper"] = 103.0
    f.loc[:, "target_lower"] = 97.0
    return f


def test_whole_wick_is_strict_and_features_are_prefix_causal(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(205)
    arrows = pd.DataFrame({"k": 10., "d": 10., "arrow_long": True, "arrow_short": False}, index=bars.index)
    monkeypatch.setattr(replay, "_original_arrows", lambda _: arrows)
    baseline = replay.compute_features(bars)
    # Body closes below the lower band but a wick that touches it is not wholly out.
    i = 200
    bars.iloc[i, bars.columns.get_loc("close")] = baseline.lower.iloc[i] - .1
    bars.iloc[i, bars.columns.get_loc("open")] = baseline.lower.iloc[i] - .1
    bars.iloc[i, bars.columns.get_loc("high")] = baseline.lower.iloc[i]
    bars.iloc[i, bars.columns.get_loc("low")] = baseline.lower.iloc[i] - .2
    assert replay.compute_features(bars).signal.iloc[i] == 0
    bars.iloc[i, bars.columns.get_loc("high")] = baseline.lower.iloc[i] - .01
    assert replay.compute_features(bars).signal.iloc[i] == 1
    short_bars = _bars(205)
    short_arrows = arrows.assign(arrow_long=False, arrow_short=True)
    monkeypatch.setattr(replay, "_original_arrows", lambda _: short_arrows)
    short_base = replay.compute_features(short_bars)
    short_bars.iloc[i, [short_bars.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [
        short_base.upper.iloc[i] + .1, short_base.upper.iloc[i] + .2,
        short_base.upper.iloc[i], short_base.upper.iloc[i] + .1,
    ]
    # Equality with the upper band is inside, just as equality with lower was.
    boundary = replay.compute_features(short_bars).upper.iloc[i]
    short_bars.iloc[i, short_bars.columns.get_loc("low")] = boundary
    assert replay.compute_features(short_bars).signal.iloc[i] == 0
    short_bars.iloc[i, short_bars.columns.get_loc("low")] = boundary + .01
    assert replay.compute_features(short_bars).signal.iloc[i] == -1
    clean = replay.compute_features(_bars(205))
    poisoned = _bars(205); poisoned.iloc[203:, :] = [1000., 1001., 999., 1000.]
    got = replay.compute_features(poisoned)
    pd.testing.assert_frame_equal(clean.iloc[:203], got.iloc[:203])


def test_targets_use_only_prior_199_closes_and_tick_rounding() -> None:
    bars = _bars(205)
    f = replay.compute_features(bars)
    i = 199
    prior = bars.close.iloc[:199].to_numpy()
    raw = prior.mean() + 2 * prior.std(ddof=0) * math.sqrt(200 / 195)
    assert f.target_upper.iloc[i] == pytest.approx(math.ceil(raw / .01) * .01)
    changed = bars.copy()
    changed.iloc[i, [changed.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [10000., 10001., 9999., 10000.]
    assert replay.compute_features(changed).target_upper.iloc[i] == f.target_upper.iloc[i]


def test_target_then_be_does_not_reuse_pre_target_wick_and_stop_gap_wins() -> None:
    f = _features()
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 103.2, 99., 100.]
    trade = replay.replay_entry(f, 200)
    assert trade["partial"] and trade["exit_reason"] == "break_even" and trade["exit_price"] == 100.
    assert trade["ambiguous_bar_count"] == 1  # target and future BE both lie in this bar's range.
    # The low before the target on O-L-H-C causes the initial stop, never BE.
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 103.2, 96., 100.]
    adverse = replay.replay_entry(f, 200, path_mode="adverse_first")
    assert adverse["exit_reason"] == "initial_stop"
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 99., 100.]
    f.iloc[202, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [96., 97., 95., 96.]
    assert replay.replay_entry(f, 200)["exit_reason"] == "initial_stop_gap"


def test_tv_distance_tie_goes_to_low_before_high_and_target_updates_by_bar() -> None:
    f = _features()
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 103., 97., 100.]
    assert replay.replay_entry(f, 200)["exit_reason"] == "initial_stop"
    f = _features()
    f.loc[:, "target_upper"] = 105.0
    f.iloc[202, f.columns.get_loc("target_upper")] = 103.0
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 99., 100.]
    f.iloc[202, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 103.2, 100., 102.]
    moved = replay.replay_entry(f, 200, end_i=203)
    assert moved["partial"] and moved["tp_i"] == 202 and moved["tp_price"] == 103.0


def test_intrabar_target_moved_through_entry_marks_marketable_be() -> None:
    f = _features()
    f.loc[:, "target_upper"] = 105.0
    f.iloc[202, f.columns.get_loc("target_upper")] = 99.0
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 99., 100.]
    f.iloc[202, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [98.5, 99.2, 98.4, 99.]
    trade = replay.replay_entry(f, 200, end_i=203)
    assert trade["exit_reason"] == "break_even_marketable" and trade["exit_price"] == 99.0
    assert trade["marketable_be_approximation"] and not trade["same_open_be_approximation"]


@pytest.mark.parametrize("side, open_, target", [(1, 104., 103.), (-1, 96., 97.)])
def test_target_gap_fills_once_and_marks_entry_open_beyond_target(side: int, open_: float, target: float) -> None:
    f = _features(side)
    target_column = "target_upper" if side == 1 else "target_lower"
    f.loc[:, target_column] = target
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [open_, open_ + 1, open_ - 1, open_]
    trade = replay.replay_entry(f, 200, end_i=202)
    assert trade["partial"] and trade["entry_open_beyond_target"]
    assert trade["same_open_be_approximation"]
    assert len([fill for fill in trade["fills"] if fill["reason"] == "take_profit_gap"]) == 1
    # A later favourable gap receives that observed open, rather than the old
    # resting limit, and does not carry the entry-open timing caveat.
    f = _features(side)
    f.loc[:, target_column] = target
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 99., 100.]
    later_open = 104. if side == 1 else 96.
    f.iloc[202, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [later_open, later_open + 1, later_open - 1, later_open]
    later = replay.replay_entry(f, 200, end_i=203)
    assert later["tp_i"] == 202 and later["tp_price"] == pytest.approx(later_open)
    assert not later["entry_open_beyond_target"]
    assert later["censored"] and not later["same_open_be_approximation"]


def test_opposite_close_before_and_after_partial_and_weighted_fees() -> None:
    f = _features()
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 102., 99., 101.]
    f.iloc[201, f.columns.get_loc("signal")] = -1
    before = replay.replay_entry(f, 200)
    assert before["exit_reason"] == "opposite_signal_close" and not before["partial"]
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 103., 99., 101.]
    after = replay.replay_entry(f, 200)
    assert after["partial"] and after["gross_pnl"] == pytest.approx(2.0)
    assert after["fees"] == pytest.approx(.1 + .103 * .5 + .101 * .5)
    assert after["gross_r"] == pytest.approx(2 / 3) and after["tail_price_r"] == pytest.approx(1 / 3)


def test_boundary_and_gap_censor_mark_without_invented_exit_fee() -> None:
    f = _features()
    f.iloc[201, [f.columns.get_loc(c) for c in ("open", "high", "low", "close")]] = [100., 101., 99., 101.]
    boundary = replay.replay_entry(f, 200, end_i=202)
    assert boundary["censored"] and boundary["exit_reason"] == "boundary_censor" and boundary["fees"] == pytest.approx(.1)
    f.iloc[202, f.columns.get_loc("_data_gap")] = True
    gap = replay.replay_entry(f, 200)
    assert gap["censored"] and gap["exit_i"] == 201 and gap["exit_reason"] == "data_gap_censor"
