"""State-machine coverage for the V7 early-failure exit study."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_early_failure_exit_study as study
from yoyo.evaluation import spike_exit_policy_study as base


def _context(side: int = 1) -> base.StreamContext:
    index = pd.date_range("2024-09-10", periods=12, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "atr": 1., "s20": 99., "e20": 99.,
                         "md": 2., "sb": 1., "ropeHigh": 99., "ropeLow": 101.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.iloc[4, 0 if side == 1 else 1] = True
    bb = pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index)
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(), "data_gap": pd.Series(False, index=index), "bb": bb, "tick": .01}
    ledger = pd.DataFrame({"signal_bar_open": [index[4]], "signal_i": [4]})
    return base.StreamContext(Path("."), "synthetic", {}, cache, ledger, 60, {"venue": "test", "symbol": "X", "asset": "X", "timeframe_min": 60})


@pytest.mark.parametrize("side", [1, -1])
def test_no_new_extreme_exits_at_next_open_for_both_sides(side: int) -> None:
    context = _context(side); bars = context.cache["bars"]
    if side == -1:
        bars[["open", "high", "low", "close", "ropeHigh", "ropeLow"]] = 200 - bars[["open", "low", "high", "close", "ropeLow", "ropeHigh"]].to_numpy()
    trades, fills, events = study.replay_policy(context, policy="no_new_extreme_2")
    early = fills.loc[fills.reason.eq("no_new_extreme_2_next_open")].iloc[0]
    assert early.execution_phase == "open"
    assert early.bar_open == bars.index[7]
    assert events.loc[events.reason.eq("no_new_extreme_2_next_open"), "bar_open"].iloc[0] == bars.index[6]
    assert not trades.censored.iloc[0]


@pytest.mark.parametrize("side", [1, -1])
def test_rope_reentry_is_close_confirmed_and_next_open(side: int) -> None:
    context = _context(side); bars = context.cache["bars"]
    bars.iloc[5, bars.columns.get_loc("close")] = 98.5 if side == 1 else 101.5
    trades, fills, events = study.replay_policy(context, policy="back_inside_rope_2")
    exit_fill = fills.loc[fills.reason.eq("back_inside_rope_2_next_open")].iloc[0]
    assert exit_fill.bar_open == bars.index[6]
    assert events.loc[events.reason.eq("back_inside_rope_2_next_open"), "bar_open"].iloc[0] == bars.index[5]
    assert not trades.censored.iloc[0]


def test_stop_at_next_open_has_priority_over_scheduled_early_exit() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[7, bars.columns.get_loc("open")] = 97.0
    trades, fills, _ = study.replay_policy(context, policy="no_new_extreme_2")
    assert fills.loc[fills.kind.eq("exit"), "reason"].iloc[0] == "initial_stop_gap"
    assert trades.exit_reason.iloc[0] == "initial_stop_gap"


def test_progress_strictly_exceeds_signal_extreme() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[6, bars.columns.get_loc("high")] = 101.01
    _, fills, _ = study.replay_policy(context, policy="no_new_extreme_2")
    assert fills.loc[fills.reason.eq("no_new_extreme_2_next_open")].empty


def test_gap_censors_and_never_invents_a_next_open_exit() -> None:
    context = _context(); context.cache["data_gap"].iloc[7] = True
    trades, fills, _ = study.replay_policy(context, policy="no_new_extreme_2")
    assert trades.censored.iloc[0]
    assert fills.loc[fills.kind.eq("censor"), "reason"].iloc[0] == "data_gap_censored"


def test_baseline_delegates_to_frozen_engine_parity() -> None:
    context = _context()
    actual = study.replay_policy(context, policy="baseline")
    expected = base.replay_policy(context, cohort="v7_both", policy="baseline")
    for got, want in zip(actual, expected):
        pd.testing.assert_frame_equal(got, want)
