"""Synthetic contracts for bounded partial-exit replay."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from yoyo.evaluation import spike_partial_exit as partial
from yoyo.evaluation import spike_recovery_exit as exits


def _inputs(side: int = 1, count: int = 11):
    index = pd.date_range("2025-01-01", periods=count, freq="3min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.5, "low": 99.5, "close": 100., "atr": 1.}, index=index)
    raw = pd.DataFrame({"long_signal": False, "short_signal": False, "_data_gap": False}, index=index)
    raw.iloc[4, 0 if side == 1 else 1] = True
    bars.attrs["minutes"] = 3
    return bars, raw


@pytest.mark.parametrize("side, first, final", [(1, 102., 106.), (-1, 98., 94.)])
def test_partial_and_final_targets_make_side_correct_weighted_r(side: int, first: float, final: float) -> None:
    bars, raw = _inputs(side)
    if side == 1:
        bars.iloc[5] = [100., first + .1, 99., 101., 1.]
        bars.iloc[6] = [101., final + .1, 100., 105., 1.]
    else:
        bars.iloc[5] = [100., 101., first - .1, 99., 1.]
        bars.iloc[6] = [99., 100., final - .1, 95., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4)
    assert trade["partial_executed"] and trade["exit_reason"] == "take_profit"
    assert [fill["fill_r"] for fill in trade["fills"]] == pytest.approx([1., 3.])
    assert [fill["fraction"] for fill in trade["fills"]] == pytest.approx([.5, .5])
    assert trade["gross_r"] == pytest.approx(2.)
    assert sum(fill["fraction"] for fill in trade["fills"]) == pytest.approx(1.)


def test_old_stop_wins_unknown_intrabar_targets_without_a_partial_fill() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 106.1, 97.9, 100., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4)
    assert trade["exit_reason"] == "initial_stop"
    assert not trade["partial_executed"] and trade["full_initial_stop"]
    assert len(trade["fills"]) == 1 and trade["fills"][0]["fraction"] == pytest.approx(1.)
    assert trade["ambiguous_stop_tp"]


def test_opening_partial_limit_can_fill_before_later_old_stop() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 100.5, 99.5, 100., 1.]
    bars.iloc[6] = [103., 103.5, 97., 100., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4)
    assert [fill["reason"] for fill in trade["fills"]] == ["partial_take_profit_gap", "initial_stop"]
    assert [fill["fill_r"] for fill in trade["fills"]] == pytest.approx([1.5, -1.])
    assert trade["gross_r"] == pytest.approx(.25)
    assert trade["exit_at_open"] is False


def test_after_partial_fixed_stop_is_effective_on_following_bar_only() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 99., 101., 1.]
    bars.iloc[6] = [100.5, 101., 99., 100., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4, after_partial_stop_r=0)
    assert trade["exit_i"] == 6 and trade["exit_reason"] == "trailing_stop"
    assert trade["exit_price"] == pytest.approx(100.)
    assert trade["after_partial_armed"] and trade["be_armed"]


def test_pending_raw_reverse_exits_the_remaining_fraction_at_next_open() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 99., 101., 1.]
    raw.iloc[5, raw.columns.get_loc("short_signal")] = True
    bars.iloc[6] = [101., 101.5, 100., 101., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4)
    assert trade["exit_reason"] == "opposite_v6_next_open" and trade["exit_at_open"]
    assert [fill["fraction"] for fill in trade["fills"]] == pytest.approx([.5, .5])
    assert [fill["fill_r"] for fill in trade["fills"]] == pytest.approx([1., .5])


def test_no_partial_default_trail_delegates_exactly_to_frozen_partial_free_replay() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 103., 99., 102., 1.]
    bars.iloc[6] = [102., 106., 101., 105., 1.]
    prepared = exits.prepare(bars, raw)
    expected = exits.replay_entry(prepared, 4, take_profit_r=3.)
    got = partial.replay_partial(prepared, 4, partial_fraction=0, final_tp_r=3.)
    for key in ("entry_i", "exit_i", "exit_reason", "entry_price", "exit_price", "gross_r", "net_r", "censored"):
        assert got[key] == expected[key]
    assert got["fills"][0]["fraction"] == pytest.approx(1.)


def test_aggregate_cost_is_charged_once_not_once_per_slice() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 99., 101., 1.]
    bars.iloc[6] = [101., 106.1, 100., 105., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4)
    assert trade["cost_r"] == pytest.approx(.1)
    assert trade["gross_r"] == pytest.approx(2.) and trade["net_r"] == pytest.approx(1.9)
    assert sum(fill["allocated_cost_r"] for fill in trade["fills"]) == pytest.approx(.1)


def test_favorable_limit_rounding_and_censored_boundary_keep_all_units() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.04, 99., 101., 1.]
    miss = partial.replay_partial(exits.prepare(bars, raw), 4, partial_r=1.01, tick=.05, end_i=6)
    assert miss["partial_target_price"] == pytest.approx(102.05)
    assert not miss["partial_executed"] and miss["censored"]
    bars.iloc[5] = [100., 102.05, 99., 101., 1.]
    hit = partial.replay_partial(exits.prepare(bars, raw), 4, partial_r=1.01, tick=.05, end_i=6)
    assert hit["partial_executed"] and hit["censored"]
    assert sum(fill["fraction"] for fill in hit["fills"]) == pytest.approx(1.)
    assert hit["realized_partial_net_r"] + hit["residual_mark_net_r"] == pytest.approx(hit["net_r"])


def test_data_gap_censors_residual_without_dropping_the_realized_partial() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 99., 101., 1.]
    raw.iloc[6, raw.columns.get_loc("_data_gap")] = True
    trade = partial.replay_partial(exits.prepare(bars, raw), 4)
    assert trade["censored"] and trade["exit_reason"] == "data_gap_censored"
    assert trade["partial_executed"] and math.isnan(trade["net_r"])
    assert sum(fill["fraction"] for fill in trade["fills"]) == pytest.approx(1.)


def test_nondefault_no_partial_does_not_evaluate_phantom_partial_target() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 97.9, 100., 1.]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4, partial_fraction=0, trail_atr=1)
    assert trade["exit_reason"] == "initial_stop" and not trade["ambiguous_stop_tp"]


def test_positive_partial_requires_final_target_above_first_target() -> None:
    bars, raw = _inputs()
    with pytest.raises(ValueError, match="exceed partial_r"):
        partial.replay_partial(exits.prepare(bars, raw), 4, partial_r=2, final_tp_r=2)


def test_same_open_stop_gap_beats_final_limit_after_costy_net_be() -> None:
    bars, raw = _inputs()
    bars.loc[:, "high"] = 100.001
    bars.loc[:, "low"] = 99.999
    bars.loc[:, "atr"] = .005
    bars.iloc[5] = [100., 100.011, 100., 100.01, .005]
    bars.iloc[6] = [100.035, 100.04, 100.03, 100.035, .005]
    trade = partial.replay_partial(exits.prepare(bars, raw), 4, after_partial_stop_r="net_be")
    assert trade["exit_reason"] == "trailing_stop_gap" and trade["exit_at_open"]
    assert trade["ambiguous_stop_tp"]
    assert [fill["reason"] for fill in trade["fills"]] == ["partial_take_profit", "trailing_stop_gap"]


def test_final_limit_uses_the_same_favorable_tick_rounding() -> None:
    bars, raw = _inputs()
    bars.iloc[5] = [100., 102.1, 99., 101., 1.]
    bars.iloc[6] = [101., 106.04, 100., 105., 1.]
    miss = partial.replay_partial(exits.prepare(bars, raw), 4, final_tp_r=3.01, tick=.05, end_i=7)
    assert miss["target_price"] == pytest.approx(106.05) and miss["censored"]
    bars.iloc[6] = [101., 106.05, 100., 105., 1.]
    hit = partial.replay_partial(exits.prepare(bars, raw), 4, final_tp_r=3.01, tick=.05)
    assert hit["exit_reason"] == "take_profit" and hit["exit_price"] == pytest.approx(106.05)
