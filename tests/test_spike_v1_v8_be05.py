"""Focused causal contracts for the V1-common/V8 0.5R price-BE replay."""
from __future__ import annotations

import pandas as pd
import pytest
from pathlib import Path

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as study


def _context(side: int = 1) -> base.StreamContext:
    index = pd.date_range("2026-05-04", periods=11, freq="30min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.4, "low": 99., "close": 100., "atr": 1.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ropeHigh": 100., "ropeLow": 100.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[4], "long_signal" if side == 1 else "short_signal"] = True  # next open is index 5
    cache = {"bars": bars, "signals": signals.copy(), "v1_signals": signals.copy(),
             "data_gap": pd.Series(False, index=index), "bb_ready": pd.Series(True, index=index),
             "bb": pd.DataFrame({"prior_squeeze_run3": True, "v7_ready": True}, index=index), "tick": .01}
    ledger = pd.DataFrame({"signal_bar_open": [index[4]], "signal_i": [4]})
    return base.StreamContext(path=Path("."), key="binance_30m_synthetic", receipt={"source_sha256": "synthetic", "cache_sha256": "synthetic"}, cache=cache, signals_ledger=ledger, minutes=30, identity={"venue": "binance", "symbol": "SYN", "asset": "SYN", "timeframe_min": 30})


def test_wick_trigger_is_effective_only_next_bar_and_price_be_keeps_cost() -> None:
    context = _context()
    bars = context.cache["bars"]
    bars.iloc[5] = [100., 101.1, 99.1, 100.4, 1., 100., 100., 1., 0., 100., 100.]  # initial R = 2
    bars.iloc[6] = [100.2, 100.3, 99.9, 100., 1., 100., 100., 1., 0., 100., 100.]
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_i == 6
    assert trade.exit_price == pytest.approx(100.)
    assert trade.net_return == pytest.approx(-.002)
    assert events.iloc[0].reason == "be05_price_next_bar"


def test_same_bar_initial_stop_and_trigger_keeps_initial_stop_priority() -> None:
    context = _context()
    context.cache["bars"].iloc[5] = [100., 101.1, 97.9, 100., 1., 100., 100., 1., 0., 100., 100.]
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_i == 5
    assert trade.exit_reason == "initial_stop"
    assert events.empty


def test_gap_through_be_fills_at_open_not_at_entry() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = [100., 101.1, 99.1, 100.5, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [99.5, 100., 99.4, 99.7, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_reason, trade.exit_price, trade.net_return) == ("trailing_stop_gap", pytest.approx(99.5), pytest.approx(-.007))


def test_prefix_cannot_change_an_already_observed_be_exit() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = [100., 101.1, 99.1, 100.5, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [100.2, 100.3, 99.9, 100., 1., 100., 100., 1., 0., 100., 100.]
    full, _, _ = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    prefix = _context()
    for key, value in list(prefix.cache.items()):
        if isinstance(value, (pd.Series, pd.DataFrame)) and value.index.equals(context.cache["bars"].index): prefix.cache[key] = value.iloc[:7].copy()
    prefix.cache["bars"].iloc[5] = bars.iloc[5]; prefix.cache["bars"].iloc[6] = bars.iloc[6]
    got, _, _ = study.replay_serial(prefix, arm="v1_common_execution_long", enable_be=True)
    pd.testing.assert_frame_equal(full.loc[~full.censored, study.KEY].reset_index(drop=True), got.loc[~got.censored, study.KEY].reset_index(drop=True), check_dtype=False)


def test_entry_ratcheting_preserves_a_tighter_stop_and_tiny_prices() -> None:
    long = {"protection": 0.0000007, "entry_price": 0.0000005, "side": 1}
    short = {"protection": 0.0000003, "entry_price": 0.0000005, "side": -1}
    assert not study._raise_to_entry(long) and long["protection"] == pytest.approx(.0000007)
    assert not study._raise_to_entry(short) and short["protection"] == pytest.approx(.0000003)


def test_short_wick_trigger_is_next_bar_only_and_fixed_baseline_matches_serial() -> None:
    context = _context(side=-1)
    bars = context.cache["bars"]
    bars.iloc[5] = [100., 100.9, 98.9, 99.6, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [99.8, 100.1, 99.7, 100., 1., 100., 100., 1., 0., 100., 100.]
    prepared = study.prepare_arm(context, arm="v8")
    serial, _, _ = study.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    fixed = pd.DataFrame([study.replay_fixed_entry(context, row, arm="v8", enable_be=False, prepared=prepared)
                          for _, row in serial.iterrows()], columns=study.FIXED_COLUMNS)
    study.validate_fixed_baseline(serial, fixed)
    be, _, _ = study.replay_serial(context, arm="v8", enable_be=True, prepared=prepared)
    trade = be.loc[~be.censored].iloc[0]
    assert (trade.exit_i, trade.exit_price, trade.net_return) == (6, pytest.approx(100.), pytest.approx(-.002))
    fixed_be = study.replay_fixed_entry(context, serial.iloc[0], arm="v8", enable_be=True, prepared=prepared)
    assert (fixed_be["exit_i"], fixed_be["exit_price"], fixed_be["net_return"]) == (6, pytest.approx(100.), pytest.approx(-.002))


def test_zero_trade_pair_and_realized_tail_are_schema_safe() -> None:
    assert len(study.FIXED_COLUMNS) == len(set(study.FIXED_COLUMNS))
    empty = pd.DataFrame(columns=study.FIXED_COLUMNS)
    assert list(study.paired_decomposition(empty, empty).columns) == study.PAIR_COLUMNS
    baseline = pd.DataFrame({"signal_i": [1, 2], "entry_i": [2, 3], "side": [1, 1], "net_r": [10., 9.],
                             "net_return": [.1, .09], "mfe_r": [12., 15.], "censored": [False, False]})
    be = baseline.copy(); be.loc[0, "net_r"] = 9.; be.loc[1, "net_r"] = 10.
    pairs = study.paired_decomposition(baseline, be)
    assert pairs.baseline_realized_ge_10r.tolist() == [True, False]
    assert pairs.retained_realized_ge_10r.tolist() == [False, True]


def test_fixed_exit_restores_full_source_ordinal_from_prefixed_cache() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = [100., 101.1, 99.1, 100.5, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [100.2, 100.3, 99.9, 100., 1., 100., 100., 1., 0., 100., 100.]
    prepared = study.prepare_arm(context, arm="v1_common_execution_long")
    serial, _, _ = study.replay_serial(context, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    row = serial.iloc[0].copy(); row["signal_i"] += 100; row["entry_i"] += 100
    fixed = study.replay_fixed_entry(context, row, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    assert fixed["exit_i"] == int(serial.iloc[0].exit_i) + 100


def test_reverse_intent_is_consumed_by_gap_close_and_cannot_close_new_entry() -> None:
    """A reverse belongs to its original long even when that long gaps out."""
    context = _context()
    bars, index = context.cache["bars"], context.cache["bars"].index
    context.cache["signals"].loc[:, ["long_signal", "short_signal"]] = False
    context.cache["v1_signals"].loc[:, ["long_signal", "short_signal"]] = False
    context.cache["signals"].loc[index[4], "long_signal"] = True
    context.cache["signals"].loc[index[5], "short_signal"] = True
    context.cache["v1_signals"].loc[:, :] = context.cache["signals"]
    # Long enters at 100.  The opposite signal schedules a reverse, then the
    # next opening gaps through its initial stop.  A new short is admitted at
    # that opening and must not inherit the old long's reverse intent.
    bars.iloc[5] = [100., 100.5, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [97., 98.8, 96., 97.5, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[7] = [97.5, 98., 96.5, 97.5, 1., 100., 100., 1., 0., 100., 100.]
    prepared = study.prepare_arm(context, arm="v8")
    serial, _, _ = study.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    assert serial.loc[0, "exit_reason"] == "initial_stop_gap"
    assert serial.loc[1, ["side", "entry_i", "censored"]].tolist() == [-1, 6, True]
    assert "opposite_v6_next_open" not in serial.exit_reason.tolist()
    fixed = pd.DataFrame([study.replay_fixed_entry(context, row, arm="v8", enable_be=False, prepared=prepared)
                          for _, row in serial.iterrows()], columns=study.FIXED_COLUMNS)
    study.validate_fixed_baseline(serial, fixed)


def test_legacy_clean_audit_has_stable_schema_for_no_difference() -> None:
    context = _context()
    prepared = study.prepare_arm(context, arm="v1_common_execution_long")
    legacy, _, _ = study.replay_legacy_baseline(context, arm="v1_common_execution_long", prepared=prepared)
    clean, _, _ = study.replay_serial(context, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    assert list(study.legacy_clean_events(legacy, clean).columns) == study.LEGACY_CLEAN_COLUMNS
    assert study.legacy_clean_events(legacy, clean).empty
