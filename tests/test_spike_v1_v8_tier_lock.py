"""Focused causal contracts for common V1/V8 two-tier profit locks."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as be05
from yoyo.evaluation import spike_v1_v8_tier_lock as study


def _context(side: int = 1, tick: float = .01) -> base.StreamContext:
    index = pd.date_range("2026-05-04", periods=11, freq="30min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.4, "low": 99., "close": 100., "atr": 1.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ropeHigh": 100., "ropeLow": 100.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[4], "long_signal" if side == 1 else "short_signal"] = True
    cache = {"bars": bars, "signals": signals.copy(), "v1_signals": signals.copy(),
             "data_gap": pd.Series(False, index=index), "bb_ready": pd.Series(True, index=index),
             "bb": pd.DataFrame({"prior_squeeze_run3": True, "v7_ready": True}, index=index), "tick": tick}
    ledger = pd.DataFrame({"signal_bar_open": [index[4]], "signal_i": [4]})
    return base.StreamContext(path=Path("."), key="binance_30m_tier_synthetic", receipt={"source_sha256": "synthetic", "cache_sha256": "synthetic"}, cache=cache, signals_ledger=ledger, minutes=30, identity={"venue": "binance", "symbol": "SYN", "asset": "SYN", "timeframe_min": 30})


def test_one_bar_can_arm_both_tiers_and_stage2_is_next_bar_only() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = [100., 103.1, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [101.2, 101.3, 100.8, 101., 1., 100., 100., 1., 0., 100., 100.]
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long")
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_i, trade.exit_price, trade.stage1_armed, trade.stage2_armed) == (6, pytest.approx(101.), True, True)
    assert (trade.stage1_trigger_count, trade.stage2_trigger_count, trade.exit_protection_source) == (1, 1, "tier2")
    assert events.tier_stage.tolist() == [1, 2]
    assert pd.to_datetime(events.event_time, utc=True).tolist() == [bars.index[5] + pd.Timedelta(minutes=30)] * 2


def test_same_bar_initial_stop_beats_both_tier_updates() -> None:
    context = _context(); context.cache["bars"].iloc[5] = [100., 103.1, 97.9, 100., 1., 100., 100., 1., 0., 100., 100.]
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long")
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_i, trade.exit_reason, trade.stage1_armed, trade.stage2_armed) == (5, "initial_stop", False, False)
    assert events.empty


def test_gap_through_tier2_uses_open_and_records_tier2_protection() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = [100., 103.1, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [99.5, 100., 99., 99.7, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v1_common_execution_long")
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_reason, trade.exit_price, trade.net_return, trade.exit_protection_source) == ("trailing_stop_gap", pytest.approx(99.5), pytest.approx(-.007), "tier2")


def test_short_stage2_lock_is_conservatively_ceiled_to_tick() -> None:
    context = _context(side=-1, tick=.1); bars = context.cache["bars"]
    bars.iloc[5] = [100.03, 100.5, 96.9, 99.5, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [99.0, 99.11, 98.8, 99., 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v8")
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.stage2_lock_price, trade.exit_price, trade.exit_protection_source) == (pytest.approx(99.1), pytest.approx(99.1), "tier2")


def test_stage1_uses_exact_actual_entry_not_binary_floor_of_tick_grid() -> None:
    """Stage 1 must remain receipt-identical to BE05 even at binary tick edges."""
    context = _context(tick=.1); bars = context.cache["bars"]
    # 100.3 / 0.1 is represented slightly below an integer in binary floats.
    # A floor-based stage-1 implementation turns this into 100.2 and misses
    # the next bar's 100.25 low; frozen BE05 exits exactly at 100.3.
    bars.iloc[5] = [100.3, 101.5, 100.1, 100.3, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [100.35, 100.4, 100.25, 100.3, 1., 100., 100., 1., 0., 100., 100.]
    prepared = be05.prepare_arm(context, arm="v1_common_execution_long")
    old_be, _, _ = be05.replay_serial(context, arm="v1_common_execution_long", enable_be=True, prepared=prepared)
    tier, _, _ = study.replay_serial(context, arm="v1_common_execution_long", prepared=prepared)
    old_trade, tier_trade = old_be.loc[~old_be.censored].iloc[0], tier.loc[~tier.censored].iloc[0]
    assert tier_trade.stage1_armed and not tier_trade.stage2_armed
    assert (tier_trade.exit_i, tier_trade.exit_price, tier_trade.exit_reason, tier_trade.net_return, tier_trade.net_r) == (
        old_trade.exit_i, pytest.approx(old_trade.exit_price), old_trade.exit_reason,
        pytest.approx(old_trade.net_return), pytest.approx(old_trade.net_r),
    )
    assert tier_trade.exit_price == pytest.approx(100.3)


def test_fixed_original_entry_matches_serial_without_reentry_and_keeps_fields() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = [100., 103.1, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [101.2, 101.3, 100.8, 101., 1., 100., 100., 1., 0., 100., 100.]
    prepared = be05.prepare_arm(context, arm="v1_common_execution_long")
    old, _, _ = be05.replay_serial(context, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    fixed = study.replay_fixed_entry(context, old.iloc[0], arm="v1_common_execution_long", prepared=prepared)
    serial, _, _ = study.replay_serial(context, arm="v1_common_execution_long", prepared=prepared)
    row = serial.loc[~serial.censored].iloc[0]
    assert (fixed["exit_i"], fixed["exit_price"], fixed["stage1_armed"], fixed["stage2_armed"]) == (row.exit_i, pytest.approx(row.exit_price), True, True)
    assert set(("stage1_trigger_time", "stage2_trigger_time", "stage2_lock_price", "exit_protection_source")) <= set(fixed)
    study.validate_fixed_identity(old, pd.DataFrame([fixed]))


def test_reverse_gap_clears_old_intent_before_new_short_entry() -> None:
    context = _context(); bars, index = context.cache["bars"], context.cache["bars"].index
    context.cache["signals"].loc[:, ["long_signal", "short_signal"]] = False
    context.cache["v1_signals"].loc[:, ["long_signal", "short_signal"]] = False
    context.cache["signals"].loc[index[4], "long_signal"] = True
    context.cache["signals"].loc[index[5], "short_signal"] = True
    context.cache["v1_signals"].loc[:, :] = context.cache["signals"]
    bars.iloc[5] = [100., 100.5, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [97., 98.8, 96., 97.5, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v8")
    assert trades.loc[0, "exit_reason"] == "initial_stop_gap"
    assert trades.loc[1, ["side", "entry_i", "censored"]].tolist() == [-1, 6, True]
    assert "opposite_v6_next_open" not in trades.exit_reason.tolist()


def test_fixed_reverse_gap_attributes_exit_to_active_protection_not_opposite() -> None:
    context = _context(); bars, index = context.cache["bars"], context.cache["bars"].index
    context.cache["signals"].loc[:, ["long_signal", "short_signal"]] = False
    context.cache["v1_signals"].loc[:, ["long_signal", "short_signal"]] = False
    context.cache["signals"].loc[index[4], "long_signal"] = True
    context.cache["signals"].loc[index[5], "short_signal"] = True
    context.cache["v1_signals"].loc[:, :] = context.cache["signals"]
    bars.iloc[5] = [100., 100.5, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [97., 98.8, 96., 97.5, 1., 100., 100., 1., 0., 100., 100.]
    prepared = be05.prepare_arm(context, arm="v8")
    old, _, _ = be05.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    fixed = study.replay_fixed_entry(context, old.iloc[0], arm="v8", prepared=prepared)
    assert (fixed["exit_reason"], fixed["exit_protection_source"]) == ("initial_stop_gap", "initial")
