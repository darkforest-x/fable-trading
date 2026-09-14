"""Synthetic contracts for net-target V8 recovery exits."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from yoyo.evaluation import spike_net_recovery_exit as net
from yoyo.evaluation import spike_recovery_exit as frozen


def _inputs(side: int = 1, count: int = 10):
    index = pd.date_range("2025-01-01", periods=count, freq="3min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.5, "low": 99.5, "close": 100., "atr": 1.}, index=index)
    raw = pd.DataFrame({"long_signal": False, "short_signal": False, "_data_gap": False}, index=index)
    raw.iloc[4, 0 if side == 1 else 1] = True
    bars.attrs["minutes"] = 3
    return bars, raw


@pytest.mark.parametrize("side,bar", [(1, [100., 102.2, 99., 101., 1.]), (-1, [100., 101., 97.8, 99., 1.])])
def test_favorable_net_target_delivers_one_net_r_after_single_cost(side: int, bar: list[float]) -> None:
    bars, raw = _inputs(side)
    bars.iloc[5] = bar
    trade = net.replay_net_entry(frozen.prepare(bars, raw), 4)
    assert trade["exit_reason"] == "take_profit"
    assert trade["gross_r"] == pytest.approx(1.1)
    assert trade["net_r"] == pytest.approx(1.)
    assert trade["target_price"] == pytest.approx(102.2 if side == 1 else 97.8)


def test_low_risk_cost_r_above_one_moves_target_beyond_three_gross_r() -> None:
    bars, raw = _inputs()
    bars.loc[:, "high"] = 100.05
    bars.loc[:, "low"] = 99.95
    bars.loc[:, "atr"] = .05
    bars.iloc[5] = [100., 100.3, 99.95, 100.2, .05]
    trade = net.replay_net_entry(frozen.prepare(bars, raw), 4)
    assert trade["cost_r"] == pytest.approx(2.)
    assert trade["target_price"] == pytest.approx(100.3)
    assert trade["net_r"] == pytest.approx(1.)


def test_next_bar_arms_after_close_while_ohlc_can_retrace_to_cost_be_same_bar() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 100.21, 100.1, 100.1, 1.]
    bars.iloc[6] = [100.3, 100.35, 100.1, 100.1, 1.]
    prepared = frozen.prepare(bars, raw)
    next_bar = net.replay_net_entry(prepared, 4, timing="next_bar")
    ohlc = net.replay_net_entry(prepared, 4, timing="ohlc")
    assert (next_bar["exit_i"], next_bar["exit_reason"], next_bar["exit_price"]) == (6, "cost_be", 100.2)
    assert (ohlc["exit_i"], ohlc["exit_reason"], ohlc["exit_price"]) == (5, "cost_be", 100.2)
    assert next_bar["trigger_i"] == ohlc["trigger_i"] == 5


def test_path_order_can_hit_old_stop_before_or_after_cost_activation() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 100.21, 97., 98., 1.]
    prepared = frozen.prepare(bars, raw)
    high_first = net.replay_net_entry(prepared, 4, timing="ohlc")
    low_first = net.replay_net_entry(prepared, 4, timing="olhc")
    assert high_first["exit_reason"] == "cost_be" and high_first["exit_price"] == pytest.approx(100.2)
    assert low_first["exit_reason"] == "initial_stop" and low_first["exit_price"] == pytest.approx(98.)


def test_cost_be_gap_uses_observed_open_and_may_be_negative_net_r() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 100.21, 100.1, 100.1, 1.]
    bars.iloc[6] = [100.1, 100.2, 100., 100.1, 1.]
    trade = net.replay_net_entry(frozen.prepare(bars, raw), 4)
    assert trade["exit_reason"] == "cost_be_gap" and trade["exit_at_open"]
    assert trade["exit_price"] == pytest.approx(100.1)
    assert trade["net_r"] < 0


def test_raw_reverse_still_exits_next_open_before_a_new_cost_protection() -> None:
    bars, raw = _inputs()
    raw.iloc[5, raw.columns.get_loc("short_signal")] = True
    bars.iloc[6] = [101., 101.1, 100.9, 101., 1.]
    trade = net.replay_net_entry(frozen.prepare(bars, raw), 4)
    assert trade["exit_reason"] == "opposite_v6_next_open"
    assert trade["exit_at_open"] and trade["exit_price"] == pytest.approx(101.)


def test_data_gap_censors_without_making_up_a_price() -> None:
    bars, raw = _inputs()
    raw.iloc[5, raw.columns.get_loc("_data_gap")] = True
    trade = net.replay_net_entry(frozen.prepare(bars, raw), 4)
    assert trade["censored"] and trade["exit_reason"] == "data_gap_censored"
    assert math.isnan(trade["exit_price"]) and math.isnan(trade["net_r"])


def test_end_i_is_exclusive_and_future_bar_cannot_repaint_boundary() -> None:
    bars, raw = _inputs()
    clean = net.replay_net_entry(frozen.prepare(bars, raw), 4, end_i=6)
    bars.iloc[6] = [1., 1000., .01, 1., 1000.]
    poison = net.replay_net_entry(frozen.prepare(bars, raw), 4, end_i=6)
    assert clean["exit_reason"] == poison["exit_reason"] == "boundary_mark"
    assert clean["exit_price"] == poison["exit_price"]


def test_short_ohlc_path_arms_below_entry_then_retraces_to_cost_be() -> None:
    bars, raw = _inputs(-1)
    bars.iloc[5] = [100., 100.5, 99.7, 99.9, 1.]
    trade = net.replay_net_entry(frozen.prepare(bars, raw), 4, timing="ohlc")
    assert trade["exit_reason"] == "cost_be"
    assert trade["cost_protection"] == pytest.approx(99.8)
    assert trade["activation_price"] == pytest.approx(99.79)
    assert trade["exit_price"] == pytest.approx(99.8)
