"""Contracts for bounded independent SPIKE recovery-exit replays."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.evaluation import spike_recovery_exit as exits
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import simulate_v6_variant


def _inputs(side: int = 1, count: int = 11):
    index = pd.date_range("2025-01-01", periods=count, freq="3min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.5, "low": 99.5, "close": 100., "atr": 1.}, index=index)
    raw = pd.DataFrame({"long_signal": False, "short_signal": False, "_data_gap": False}, index=index)
    raw.iloc[4, 0 if side == 1 else 1] = True
    bars.attrs["minutes"] = 3
    return bars, raw


@pytest.mark.parametrize("side", [1, -1])
def test_fixed_take_profit_is_side_correct_and_gap_uses_favorable_open(side: int) -> None:
    bars, raw = _inputs(side)
    bars.iloc[5] = ([100., 106., 99., 103., 1.] if side == 1 else [100., 101., 94., 97., 1.])
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3)
    assert trade["exit_reason"] == "take_profit"
    assert trade["exit_price"] == pytest.approx(106 if side == 1 else 94)
    assert not trade["exit_at_open"]
    bars.iloc[5] = ([100., 101., 99., 100., 1.] if side == 1 else [100., 101., 99., 100., 1.])
    bars.iloc[6] = ([107., 108., 106., 107., 1.] if side == 1 else [93., 94., 92., 93., 1.])
    gap = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3)
    assert gap["exit_reason"] == "take_profit_gap"
    assert gap["exit_at_open"] and gap["exit_price"] == pytest.approx(107 if side == 1 else 93)


def test_old_stop_wins_same_bar_target_and_does_not_record_exit_bar_mfe() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 106., 97.9, 100., 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3)
    assert trade["exit_reason"] == "initial_stop"
    assert trade["ambiguous_stop_tp"] and trade["ambiguous_stop_tp_count"] == 1
    assert trade["mfe_r"] == 0


def test_tp_opening_gap_wins_over_later_intrabar_stop_but_not_raw_reverse_open() -> None:
    bars, raw = _inputs()
    bars.iloc[6] = [107., 108., 97., 100., 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3)
    assert trade["exit_reason"] == "take_profit_gap" and trade["exit_price"] == 107
    raw.iloc[5, raw.columns.get_loc("short_signal")] = True
    reverse = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3)
    assert reverse["exit_reason"] == "opposite_v6_next_open" and reverse["exit_price"] == 107


@pytest.mark.parametrize("side, reached, missed, target", [(1, 106.05, 106.04, 106.05), (-1, 93.95, 93.96, 93.95)])
def test_take_profit_rounds_to_a_tick_valid_favorable_limit(side: int, reached: float, missed: float, target: float) -> None:
    bars, raw = _inputs(side)
    if side == 1:
        bars.iloc[5] = [100., missed, 99., 100., 1.]
    else:
        bars.iloc[5] = [100., 101., missed, 100., 1.]
    pending = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3.01, tick=.05, end_i=6)
    assert pending["exit_reason"] == "boundary_mark" and pending["target_price"] == pytest.approx(target)
    if side == 1:
        bars.iloc[5] = [100., reached, 99., 100., 1.]
    else:
        bars.iloc[5] = [100., 101., reached, 100., 1.]
    filled = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=3.01, tick=.05)
    assert filled["exit_reason"] == "take_profit" and filled["exit_price"] == pytest.approx(target)


def test_entry_protection_arms_next_bar_not_same_bar() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 99.5, 100.4, 1.]
    bars.iloc[6] = [100.2, 100.4, 99.9, 100., 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None,
                               protection_mode="entry", trigger_r=1)
    assert trade["exit_i"] == 6 and trade["exit_price"] == 100
    assert trade["protection_trigger_i"] == 5
    assert trade["be_armed"] is trade["protection_armed"] is True


def test_raw_reverse_closes_next_open_even_when_it_would_not_be_admitted() -> None:
    bars, raw = _inputs()
    raw.iloc[5, raw.columns.get_loc("short_signal")] = True
    bars.iloc[6] = [101.5, 102., 101., 101.7, 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None)
    assert trade["exit_reason"] == "opposite_v6_next_open"
    assert trade["exit_at_open"] and trade["exit_price"] == 101.5


def test_side_override_allows_matched_non_signal_control_but_raw_reverse_remains_live() -> None:
    bars, raw = _inputs()
    raw.iloc[4, raw.columns.get_loc("long_signal")] = False
    raw.iloc[5, raw.columns.get_loc("short_signal")] = True
    bars.iloc[6] = [101.5, 102., 101., 101.7, 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None, side_override=1)
    assert trade["side"] == 1 and trade["entry_side_source"] == "side_override"
    assert trade["exit_reason"] == "opposite_v6_next_open"


def test_old_stop_at_raw_reverse_open_precedes_reverse_close() -> None:
    bars, raw = _inputs()
    raw.iloc[5, raw.columns.get_loc("short_signal")] = True
    bars.iloc[6] = [97., 98., 96., 97., 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None)
    assert trade["exit_reason"] == "initial_stop_gap"
    assert trade["exit_at_open"] and trade["exit_price"] == 97


def test_cost_protection_rounds_favorably_and_never_loosens_old_trail() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 100.3, 99.5, 100.1, 1.]
    trade = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None,
                               protection_mode="cost", trigger_r=0, tick=.03, end_i=6)
    assert trade["protection_price"] == pytest.approx(100.2)
    assert trade["protection_armed"]


def test_end_i_is_exclusive_and_future_poison_cannot_change_boundary() -> None:
    bars, raw = _inputs()
    clean = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None, end_i=6)
    bars.iloc[6] = [1., 1000., .01, 1., 1000.]
    poison = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None, end_i=6)
    assert clean["exit_reason"] == poison["exit_reason"] == "boundary_mark"
    assert clean["exit_price"] == poison["exit_price"]


def test_no_tp_no_protection_matches_same_entry_frozen_v6_on_synthetic_frame() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 103., 99.5, 102., .1]
    bars.iloc[6] = [102., 103., 101.5, 102.5, .1]
    bars.iloc[7] = [102., 102.2, 101., 101.5, .1]
    prepared = exits.prepare(bars, raw)
    got = exits.replay_entry(prepared, 4, take_profit_r=None)
    _, frozen = simulate_v6_variant(bars, raw[["long_signal", "short_signal"]],
                                    admission=raw.long_signal, variant="synthetic",
                                    data_gap=raw._data_gap, spec=ExecutionSpec(tick=.01))
    want = frozen.iloc[0]
    for key in ("signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"):
        assert got[key] == pytest.approx(want[key]) if isinstance(got[key], float) else got[key] == want[key]


def test_non_grid_frozen_trail_rounding_keeps_baseline_parity() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100.05, 104.2, 100., 104.15, .01]
    bars.iloc[6] = [104.15, 104.2, 104.15, 104.17, .01]
    bars.iloc[7] = [104.17, 104.2, 104.05, 104.1, .01]
    got = exits.replay_entry(exits.prepare(bars, raw), 4, take_profit_r=None, tick=.1)
    _, frozen = simulate_v6_variant(bars, raw[["long_signal", "short_signal"]], admission=raw.long_signal,
                                    variant="non_grid", data_gap=raw._data_gap, spec=ExecutionSpec(tick=.1))
    want = frozen.iloc[0]
    assert (got["exit_i"], got["exit_price"], got["exit_reason"]) == (want.exit_i, want.exit_price, want.exit_reason)
