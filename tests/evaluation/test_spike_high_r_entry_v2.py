"""Causal contracts for the frozen V9 high-breakout entry gate."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_high_r_entry_v2 as study
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9_full_replay import prepare_v9


def _context(*, periods: int = 48) -> base.StreamContext:
    """Return a V9-admissible hourly cache with caller-controlled raw signals."""
    index = pd.date_range("2025-01-04", periods=periods, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "atr": 1.,
                         "rv": 50., "s20": 100., "e20": 100., "md": 1., "sb": 0.,
                         "ready": True, "ropeHigh": 100., "ropeLow": 100.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    cache = {"bars": bars, "signals": signals.copy(), "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "bb_ready": pd.Series(True, index=index), "data_gap": pd.Series(False, index=index), "tick": .01}
    return base.StreamContext(Path("."), "high-r-entry-v2-synthetic", {}, cache,
                              pd.DataFrame(columns=["signal_bar_open", "signal_i"]), 60,
                              {"asset": "ETH", "symbol": "ETHUSDC", "venue": "synthetic", "timeframe_min": 60})


def _prepared(context: base.StreamContext, entries: dict[int, int]):
    """Create a real V9 prepared arm with the specified original raw candidates."""
    signals, index = context.cache["signals"], context.cache["bars"].index
    for i, side in entries.items():
        signals.loc[index[i], "long_signal" if side == 1 else "short_signal"] = True
    context.cache["v1_signals"] = signals.copy()
    context = replace(context, signals_ledger=pd.DataFrame({"signal_bar_open": [index[i] for i in entries],
                                                             "signal_i": list(entries)}))
    return context, prepare_v9(engine.prepare_arm(context, arm="v8"))[0]


def _make_breakout(context: base.StreamContext, i: int, *, close: float = 102., prior_high: float = 101.) -> None:
    """Set valid prices so exactly the preceding 20 highs determine the gate."""
    bars = context.cache["bars"]
    bars.iloc[i - study.LOOKBACK:i, bars.columns.get_indexer(["open", "high", "low", "close", "atr"])] = [100., prior_high, 99., 100., 1.]
    bars.iloc[i, bars.columns.get_indexer(["open", "high", "low", "close", "atr"])] = [100., 500., 99., close, 1.]


def test_history_boundary_excludes_current_and_future_high_and_equality_fails() -> None:
    context = _context(); context, prepared = _prepared(context, {20: 1})
    _make_breakout(context, 20, close=102.)
    # The enormous current high and a future high cannot affect the t=20 rule.
    context.cache["bars"].iloc[21, context.cache["bars"].columns.get_loc("high")] = 999.
    prepared = prepare_v9(engine.prepare_arm(context, arm="v8"))[0]
    gated, decisions = study.prepare_entry_v2(prepared)
    row = decisions.iloc[0]
    assert (row.prior_high, row.score, bool(row.gate_known), bool(row.gate_passed)) == (pytest.approx(101.), pytest.approx(1.), True, True)
    assert gated.allowed[20]
    context.cache["bars"].iloc[20, context.cache["bars"].columns.get_loc("close")] = 101.
    equality, equality_decisions = study.prepare_entry_v2(prepare_v9(engine.prepare_arm(context, arm="v8"))[0])
    assert not equality.allowed[20]
    assert equality_decisions.iloc[0].reason == "close_not_above_prior_high"


@pytest.mark.parametrize("mutator", ["gap", "nan_high", "nan_atr"])
def test_unknown_long_inputs_fail_closed(mutator: str) -> None:
    context = _context(); context, prepared = _prepared(context, {20: 1})
    _make_breakout(context, 20)
    prepared = prepare_v9(engine.prepare_arm(context, arm="v8"))[0]
    if mutator == "gap":
        gap = prepared.gap.copy(); gap[12] = True
        prepared = replace(prepared, gap=gap)
    elif mutator == "nan_high":
        high = prepared.high.copy(); high[12] = np.nan
        prepared = replace(prepared, high=high)
    else:
        atr = prepared.atr.copy(); atr[20] = np.nan
        prepared = replace(prepared, atr=atr)
    gated, decisions = study.prepare_entry_v2(prepared)
    assert not gated.allowed[20]
    assert not bool(decisions.iloc[0].gate_known)


def test_gap_before_history_boundary_is_recovered_but_internal_gap_rejects() -> None:
    context = _context(); context, prepared = _prepared(context, {20: 1})
    _make_breakout(context, 20)
    prepared = prepare_v9(engine.prepare_arm(context, arm="v8"))[0]
    boundary_gap = prepared.gap.copy(); boundary_gap[0] = True
    recovered, recovered_decisions = study.prepare_entry_v2(replace(prepared, gap=boundary_gap))
    assert bool(recovered.allowed[20]) and bool(recovered_decisions.iloc[0].gate_passed)
    internal_gap = prepared.gap.copy(); internal_gap[1] = True
    rejected, rejected_decisions = study.prepare_entry_v2(replace(prepared, gap=internal_gap))
    assert not rejected.allowed[20]
    assert rejected_decisions.iloc[0].reason == "history_gap"


def test_first_twenty_bars_are_unknown_and_shorts_are_unchanged() -> None:
    context = _context(); context, prepared = _prepared(context, {19: 1, 21: -1})
    _make_breakout(context, 21)
    gated, decisions = study.prepare_entry_v2(prepare_v9(engine.prepare_arm(context, arm="v8"))[0])
    early = decisions.loc[decisions.local_i.eq(19)].iloc[0]
    short = decisions.loc[decisions.local_i.eq(21)].iloc[0]
    assert (bool(gated.allowed[19]), bool(early.gate_known), early.reason) == (False, False, "insufficient_history")
    assert (bool(gated.allowed[21]), bool(short.gate_known), bool(short.gate_passed), short.reason) == (True, True, True, "short_unchanged")


def test_input_and_cache_remain_immutable_and_future_changes_do_not_rewrite_past_decisions() -> None:
    context = _context(); context, prepared = _prepared(context, {20: 1, 25: 1})
    _make_breakout(context, 20); _make_breakout(context, 25)
    prepared = prepare_v9(engine.prepare_arm(context, arm="v8"))[0]
    arrays_before = {name: getattr(prepared, name).copy() for name in ("allowed", "raw_side", "gap", "open", "high", "low", "close", "atr")}
    bars_before = context.cache["bars"].copy(deep=True)
    _, original = study.prepare_entry_v2(prepared)
    for name, before in arrays_before.items():
        np.testing.assert_equal(getattr(prepared, name), before)
    pd.testing.assert_frame_equal(context.cache["bars"], bars_before)
    future_cache = {key: value.copy(deep=True) if isinstance(value, (pd.Series, pd.DataFrame)) else value
                    for key, value in context.cache.items()}
    future = replace(context, cache=future_cache)
    future.cache["bars"].iloc[30:, future.cache["bars"].columns.get_indexer(["open", "high", "low", "close"])] = [500., 600., 400., 550.]
    future_prepared = prepare_v9(engine.prepare_arm(future, arm="v8"))[0]
    _, future = study.prepare_entry_v2(future_prepared)
    pd.testing.assert_frame_equal(original.loc[original.local_i.le(25)].reset_index(drop=True),
                                  future.loc[future.local_i.le(25)].reset_index(drop=True))


def test_raw_blocked_opposite_still_exits_current_long_and_same_mask_has_parent_parity() -> None:
    context = _context(); context, prepared = _prepared(context, {20: 1, 22: -1})
    _make_breakout(context, 20)
    # Preserve the raw short but remove its V9 entry permission: it must still schedule the long exit.
    allowed = prepared.allowed.copy(); allowed[22] = False
    prepared = replace(prepared, allowed=allowed)
    trades, _, _, decisions = study.replay_entry_v2(prepared)
    assert decisions.local_i.tolist() == [20]
    assert (trades.iloc[0].exit_i, trades.iloc[0].exit_reason) == (23, "opposite_v6_next_open")

    all_pass_context = _context(); all_pass_context, all_pass = _prepared(all_pass_context, {20: 1, 21: -1})
    _make_breakout(all_pass_context, 20)
    all_pass = prepare_v9(engine.prepare_arm(all_pass_context, arm="v8"))[0]
    expected = engine.replay_serial(all_pass.context, arm="v8", enable_be=False, prepared=all_pass)
    actual = study.replay_entry_v2(all_pass)[:3]
    for got, want in zip(actual, expected):
        pd.testing.assert_frame_equal(got, want)
