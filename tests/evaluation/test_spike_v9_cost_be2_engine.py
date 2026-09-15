"""Focused causal contracts for the V9 fixed-cost net-2R protection replay."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as old
from yoyo.evaluation import spike_v9_cost_be2_engine as study


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
    return base.StreamContext(path=Path("."), key="binance_30m_v9_cost_be2_synthetic",
                              receipt={"source_sha256": "synthetic", "cache_sha256": "synthetic"}, cache=cache,
                              signals_ledger=ledger, minutes=30,
                              identity={"venue": "binance", "symbol": "SYN", "asset": "SYN", "timeframe_min": 30})


def _long_trigger_bar(*, high: float = 104.3, low: float = 99.0, close: float = 100.) -> list[float]:
    """Return an entry bar with initial risk 2 and a configurable surviving wick."""
    return [100., high, low, close, 1., 100., 100., 1., 0., 100., 100.]


def test_gross_two_r_is_insufficient_until_fixed_cost_is_paid() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.1)  # gross 2.05R, net 1.95R after 20bp / 2R risk
    bars.iloc[6] = [100.2, 100.3, 100.1, 100.2, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    assert events.empty
    assert not bool(trades.iloc[0].be_armed)
    assert bool(trades.iloc[0].censored)


def test_surviving_high_can_arm_even_when_close_is_below_two_r() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.3, close=100.)
    bars.iloc[6] = [100.2, 100.3, 100.1, 100.2, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_i, trade.exit_price, trade.be_lock_price) == (6, pytest.approx(100.2), pytest.approx(100.2))
    assert trade.be_gross_favourable_r > 2 and trade.be_net_favourable_r >= 2
    assert events.iloc[0].reason == "v9_cost_be2_next_bar"


def test_cost_be_cannot_retroactively_stop_the_trigger_bar() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.3, low=100.1)
    bars.iloc[6] = [100.2, 100.3, 100.1, 100.2, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert trade.exit_i == 6


def test_entry_bar_old_stop_wins_before_any_net_be_update() -> None:
    context = _context(); context.cache["bars"].iloc[5] = _long_trigger_bar(high=104.3, low=97.9)
    trades, _, events = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    assert (trades.iloc[0].exit_reason, bool(trades.iloc[0].be_armed), len(events)) == ("initial_stop", False, 0)


def test_short_lock_is_floored_to_tick() -> None:
    context = _context(side=-1, tick=.1); bars = context.cache["bars"]
    bars.iloc[5] = [100.03, 100.5, 95.7, 99.8, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [99.0, 99.81, 98.8, 99.2, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v8", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.be_lock_price, trade.exit_price) == (pytest.approx(99.8), pytest.approx(99.8))


def test_cost_lock_never_loosens_an_existing_protection() -> None:
    position = {"side": 1, "entry_price": 100., "protection": 105.}
    assert not study._raise_to_cost_be(position, .01)
    assert position["protection"] == pytest.approx(105.)


def test_gap_below_armed_lock_fills_at_open() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.3, low=99.)
    bars.iloc[6] = [99.5, 100., 99.4, 99.7, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v1_common_execution_long", enable_be=True)
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_reason, trade.exit_price) == ("trailing_stop_gap", pytest.approx(99.5))


def test_raw_filtered_reverse_closes_old_position_but_does_not_open_new_one() -> None:
    context = _context(); bars, index = context.cache["bars"], context.cache["bars"].index
    context.cache["signals"].loc[index[5], "short_signal"] = True
    context.cache["v1_signals"].loc[index[5], "short_signal"] = True
    context.cache["bb"].loc[index[5], "v7_ready"] = False
    bars.iloc[5] = [100., 100.5, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [100., 100.2, 99.8, 100., 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v8", enable_be=True)
    assert trades.loc[0, "exit_reason"] == "opposite_v6_next_open"
    assert len(trades) == 1


def test_gap_close_consumes_old_pending_reverse_before_new_entry() -> None:
    context = _context(); bars, index = context.cache["bars"], context.cache["bars"].index
    context.cache["signals"].loc[index[5], "short_signal"] = True
    context.cache["v1_signals"].loc[index[5], "short_signal"] = True
    bars.iloc[5] = [100., 100.5, 99., 100., 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [97., 98.8, 96., 97.5, 1., 100., 100., 1., 0., 100., 100.]
    trades, _, _ = study.replay_serial(context, arm="v8", enable_be=True)
    assert trades.loc[0, "exit_reason"] == "initial_stop_gap"
    assert trades.loc[1, ["side", "entry_i", "censored"]].tolist() == [-1, 6, True]
    assert "opposite_v6_next_open" not in trades.exit_reason.tolist()


def test_disabled_engine_matches_old_serial_and_fixed_semantics() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.3, low=99.)
    bars.iloc[6] = [97., 98., 96., 97., 1., 100., 100., 1., 0., 100., 100.]
    prepared = old.prepare_arm(context, arm="v8")  # original PreparedArm remains accepted
    expected, _, _ = old.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    actual, _, events = study.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    pd.testing.assert_frame_equal(expected.loc[:, base.TRADE_COLUMNS].reset_index(drop=True),
                                  actual.loc[:, base.TRADE_COLUMNS].reset_index(drop=True), check_dtype=False)
    assert events.empty
    fixed = study.replay_fixed_entry(context, actual.iloc[0], arm="v8", enable_be=False, prepared=prepared)
    old_fixed = old.replay_fixed_entry(context, expected.iloc[0], arm="v8", enable_be=False, prepared=prepared)
    for field in base.TRADE_COLUMNS:
        assert fixed[field] == pytest.approx(old_fixed[field]) if isinstance(fixed[field], float) else fixed[field] == old_fixed[field]


@pytest.mark.parametrize("enable_be", [False, True])
def test_boundary_held_fixed_entry_returns_censored_row_for_both_policies(enable_be: bool) -> None:
    """A held trade must return a complete fixed row instead of Python None."""
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.3, low=100.3)
    for i in range(6, len(bars)):
        bars.iloc[i] = [100.4, 100.5, 100.3, 100.4, 1., 100., 100., 1., 0., 100., 100.]
    prepared = study.prepare_arm(context, arm="v8")
    serial, _, _ = study.replay_serial(context, arm="v8", enable_be=enable_be, prepared=prepared)
    assert len(serial) == 1 and bool(serial.iloc[0].censored)
    fixed = study.replay_fixed_entry(context, serial.iloc[0], arm="v8", enable_be=enable_be, prepared=prepared)
    assert fixed is not None
    assert (fixed["censored"], fixed["exit_reason"], fixed["exit_i"]) == (True, "boundary_mark", serial.iloc[0].exit_i)
    assert bool(fixed["be_armed"]) is enable_be


def test_prefix_and_fixed_entry_match_serial_cost_be_outcome() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.iloc[5] = _long_trigger_bar(high=104.3, low=99.)
    bars.iloc[6] = [100.2, 100.3, 100.1, 100.2, 1., 100., 100., 1., 0., 100., 100.]
    prepared = study.prepare_arm(context, arm="v8")
    serial, _, _ = study.replay_serial(context, arm="v8", enable_be=True, prepared=prepared)
    fixed = study.replay_fixed_entry(context, serial.iloc[0], arm="v8", enable_be=True, prepared=prepared)
    assert tuple(fixed[key] for key in ("exit_i", "exit_price", "be_armed", "be_lock_price")) == (6, pytest.approx(100.2), True, pytest.approx(100.2))
    prefix = _context()
    for key, value in list(prefix.cache.items()):
        if isinstance(value, (pd.Series, pd.DataFrame)) and value.index.equals(context.cache["bars"].index):
            prefix.cache[key] = value.iloc[:7].copy()
    prefix.cache["bars"].iloc[5] = bars.iloc[5]; prefix.cache["bars"].iloc[6] = bars.iloc[6]
    got, _, _ = study.replay_serial(prefix, arm="v1_common_execution_long", enable_be=True)
    assert got.loc[~got.censored, study.KEY].reset_index(drop=True).equals(serial.loc[~serial.censored, study.KEY].reset_index(drop=True))
