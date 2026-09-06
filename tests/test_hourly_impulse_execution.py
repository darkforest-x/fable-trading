"""Synthetic-only evidence for causal hourly-entry / lower-frame exit replay."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events, single_position_ledger


START = pd.Timestamp("2026-01-01T01:00:00Z")


def raw_bars(count=30, start=START, price=100.0):
    return pd.DataFrame({
        "open_time": pd.date_range(start, periods=count, freq="5min"),
        "open": price, "high": price + 0.2, "low": price - 0.2,
        "close": price, "volume": 1.0, "segment_id": 0,
    })


def management(sides, minutes=15, start=START, slopes=None):
    if slopes is None:
        slopes = sides
    return pd.DataFrame({
        "open_time": pd.date_range(start, periods=len(sides), freq="{}min".format(minutes)),
        "ma": 100.0, "ma_side": sides, "ma_slope_atr": slopes,
        "low": 99.0, "high": 101.0, "close": 100.0, "segment_id": 0,
    })


def event(**overrides):
    row = {"event_id": "signal_1", "decision_time": START, "direction": 1,
           "initial_stop": 90.0, "signal_atr": 2.0, "preserved_feature": 0.7}
    row.update(overrides)
    return pd.DataFrame([row])


def replay(raw=None, mg=None, entries=None, **policy):
    return simulate_events(
        raw_bars() if raw is None else raw,
        management([-1] * 9) if mg is None else mg,
        event() if entries is None else entries,
        policy,
    ).iloc[0]


def test_exit_uses_first_fully_post_entry_bar_and_its_next_five_minute_open():
    raw = raw_bars()
    raw.loc[3, ["open", "high", "low", "close"]] = [103, 104, 102, 103]
    mg = management([-1, -1, -1], start=START - pd.Timedelta(minutes=15))
    result = replay(raw=raw, mg=mg)
    assert result.exit_time == START + pd.Timedelta(minutes=15)
    assert result.exit_price == 103
    assert result.net_return == pytest.approx(0.028)
    assert result.preserved_feature == 0.7
    assert result.initial_stop == 90.0


def test_preentry_partial_management_bar_is_not_eligible():
    mg = management([-1, 1, -1], start=START - pd.Timedelta(minutes=5))
    result = replay(mg=mg)
    assert result.exit_time == START + pd.Timedelta(minutes=40)


def test_confirmations_count_completed_bars_and_reset_after_temporary_colour():
    result = replay(mg=management([-1, 1, -1, -1]), confirmations=2)
    assert result.exit_time == START + pd.Timedelta(minutes=60)
    assert result.outcome == "colour_exit"


def test_first_postentry_opposite_state_does_not_require_a_fresh_flip():
    result = replay(mg=management([-1, -1], start=START - pd.Timedelta(minutes=15)))
    assert result.exit_time == START + pd.Timedelta(minutes=15)


def test_gap_stop_precedes_same_timestamp_colour_exit():
    raw = raw_bars()
    raw.loc[3, ["open", "high", "low", "close"]] = [88, 90, 87, 89]
    result = replay(raw=raw)
    assert result.outcome == "hard_stop_gap"
    assert result.exit_time == START + pd.Timedelta(minutes=15)
    assert result.exit_price == 88
    assert result.net_return == pytest.approx(-0.122)


def test_intrabar_hard_stop_does_not_wait_for_management_exit():
    raw = raw_bars()
    raw.loc[1, "low"] = 89
    result = replay(raw=raw)
    assert result.outcome == "hard_stop"
    assert result.exit_time == START + pd.Timedelta(minutes=10)
    assert result.exit_price == 90
    assert result.net_r == pytest.approx(-1.02)


def test_partial_weighting_and_single_roundtrip_cost_are_conserved():
    raw = raw_bars()
    raw.loc[3, ["open", "high", "low", "close"]] = [110, 111, 109, 110]
    raw.loc[6, ["open", "high", "low", "close"]] = [120, 121, 119, 120]
    result = replay(raw=raw, exit_mode="partial_colour")
    assert result.partial_fraction == 0.5
    assert result.exit_remaining_fraction == 0.5
    assert result.partial_exit_time == START + pd.Timedelta(minutes=15)
    assert result.exit_time == START + pd.Timedelta(minutes=30)
    assert result.gross_return == pytest.approx(0.15)
    assert result.net_return == pytest.approx(0.148)
    assert result.net_r == pytest.approx(1.48)


def test_partial_is_not_repeated_on_later_isolated_opposite_bars():
    result = replay(mg=management([-1, 1, -1, 1, -1, -1]), exit_mode="partial_colour")
    assert result.exit_time == START + pd.Timedelta(minutes=90)
    assert result.partial_fraction == 0.5
    assert result.exit_remaining_fraction == 0.5
    assert result.partial_exit_time == START + pd.Timedelta(minutes=15)


def test_right_censor_is_not_a_completed_profit():
    raw = raw_bars(3)
    result = replay(raw=raw, mg=management([1]))
    assert not result.closed
    assert result.outcome == "right_censored"
    assert pd.isna(result.net_return)
    assert pd.isna(result.net_r)
    assert result.marked_net_return == pytest.approx(-0.002)


def test_source_gap_censors_before_missing_bar_not_at_later_open():
    raw = raw_bars().drop(index=2).reset_index(drop=True)
    result = replay(raw=raw)
    assert not result.closed
    assert result.outcome == "data_gap_censored"
    assert result.exit_time == START + pd.Timedelta(minutes=10)
    assert pd.isna(result.net_return)


def test_segment_boundary_is_a_gap_even_when_timestamp_is_adjacent():
    raw = raw_bars()
    raw.loc[2:, "segment_id"] = 1
    result = replay(raw=raw)
    assert result.outcome == "data_gap_censored"
    assert result.exit_time == START + pd.Timedelta(minutes=10)


def test_deadline_requires_an_exact_observable_next_open():
    mg = management([1, 1, 1])
    complete = replay(raw=raw_bars(7), mg=mg, max_hours=0.5)
    truncated = replay(raw=raw_bars(6), mg=mg, max_hours=0.5)
    assert complete.outcome == "time_exit"
    assert complete.closed
    assert complete.exit_time == START + pd.Timedelta(minutes=30)
    assert truncated.outcome == "right_censored"
    assert not truncated.closed


def test_end_exclusive_cannot_use_the_following_open():
    result = simulate_events(raw_bars(), management([-1] * 9), event(), {}, end_exclusive=START + pd.Timedelta(minutes=15)).iloc[0]
    assert result.outcome == "right_censored"
    assert result.exit_time == START + pd.Timedelta(minutes=15)
    assert pd.isna(result.net_return)


def test_future_mutation_cannot_move_stop_or_change_closed_trade():
    raw = raw_bars()
    raw.loc[1, "low"] = 89
    before = replay(raw=raw)
    mutated = raw.copy()
    mutated.loc[2:, ["open", "high", "low", "close"]] = [1000, 1100, 999, 1050]
    after = replay(raw=mutated)
    for key in ("initial_stop", "entry_price", "exit_price", "exit_time", "net_r", "max_favourable_r", "max_adverse_r"):
        assert before[key] == after[key]


def test_same_bar_stop_and_target_collision_is_stop_first():
    raw = raw_bars()
    raw.loc[0, ["high", "low"]] = [140, 80]
    result = replay(raw=raw, exit_mode="fixed_3r")
    assert result.outcome == "hard_stop"
    assert result.exit_price == 90


def test_resting_target_gap_fills_at_open_before_later_intrabar_stop():
    raw = raw_bars()
    raw.loc[1, ["open", "high", "low", "close"]] = [135, 140, 80, 100]
    result = replay(raw=raw, exit_mode="fixed_3r")
    assert result.outcome == "target_3r"
    assert result.exit_time == START + pd.Timedelta(minutes=5)
    assert result.exit_price == 135


def test_incomplete_bar_extrema_cannot_create_a_stop_before_cutoff():
    raw = raw_bars()
    raw.loc[0, "low"] = 80
    result = simulate_events(raw, management([-1]), event(), {}, end_exclusive=START + pd.Timedelta(minutes=2)).iloc[0]
    assert result.outcome == "right_censored"
    assert not result.closed
    assert result.exit_time == START
    assert result.exit_price == 100


def test_postexit_candle_values_are_not_needed_for_confirmed_open_exit():
    raw = raw_bars()
    raw.loc[3, ["high", "low", "close"]] = np.nan
    result = replay(raw=raw)
    assert result.closed
    assert result.outcome == "colour_exit"
    assert result.exit_time == START + pd.Timedelta(minutes=15)


def test_missing_management_bar_does_not_preserve_consecutive_streak():
    mg = management([-1, 1, -1, -1]).drop(index=1)
    result = replay(mg=mg, confirmations=2)
    assert result.exit_time == START + pd.Timedelta(minutes=60)


def test_short_direction_profit_and_colour_are_mirrored():
    raw = raw_bars()
    raw.loc[3, ["open", "high", "low", "close"]] = [95, 96, 94, 95]
    result = replay(raw=raw, mg=management([1] * 9), entries=event(direction=-1, initial_stop=110))
    assert result.outcome == "colour_exit"
    assert result.net_return == pytest.approx(0.048)
    assert result.net_r == pytest.approx(0.48)


def test_slope_confirmation_requires_colour_and_signed_slope():
    result = replay(mg=management([-1, -1, -1], slopes=[1, 0, -1]), exit_mode="slope_colour")
    assert result.exit_time == START + pd.Timedelta(minutes=45)


def test_hour_colour_uses_completed_hour_not_fifteen_minutes():
    result = replay(mg=management([-1, -1], minutes=60), exit_mode="hour_colour")
    assert result.exit_time == START + pd.Timedelta(minutes=60)


def test_single_position_ties_allow_exit_then_entry_and_censor_blocks_future():
    trades = pd.DataFrame([
        {"event_id": "a", "entry_time": START, "exit_time": START + pd.Timedelta(minutes=15), "closed": True, "outcome": "colour_exit"},
        {"event_id": "b", "entry_time": START + pd.Timedelta(minutes=5), "exit_time": START + pd.Timedelta(minutes=10), "closed": True, "outcome": "hard_stop"},
        {"event_id": "c", "entry_time": START + pd.Timedelta(minutes=15), "exit_time": START + pd.Timedelta(minutes=20), "closed": False, "outcome": "right_censored"},
        {"event_id": "d", "entry_time": START + pd.Timedelta(minutes=25), "exit_time": START + pd.Timedelta(minutes=30), "closed": True, "outcome": "colour_exit"},
    ])
    selected = single_position_ledger(trades)
    assert selected.portfolio_selected.tolist() == [True, False, True, False]
    assert selected.portfolio_skip_reason.tolist() == ["", "position_open", "", "position_open"]


def test_invalid_risk_and_missing_entry_are_rejected():
    invalid = replay(entries=event(initial_stop=101))
    missing = replay(entries=event(decision_time=START + pd.Timedelta(minutes=1)))
    assert invalid.outcome == "entry_invalid_risk"
    assert missing.outcome == "entry_missing"
    assert not invalid.closed and not missing.closed


def test_unordered_or_duplicate_execution_times_raise():
    raw = raw_bars()
    raw.loc[1, "open_time"] = raw.loc[0, "open_time"]
    with pytest.raises(ValueError, match="unique"):
        replay(raw=raw)
