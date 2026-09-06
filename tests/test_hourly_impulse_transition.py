"""Synthetic-only regression tests for native completed-5m colour edges."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


START = pd.Timestamp("2024-01-01T01:00:00Z")
STEP = pd.Timedelta(minutes=5)
POLICY = {"exit_mode": "transition_colour", "management_minutes": 5, "confirmations": 1}


def raw_bars(count=12):
    """Include the fully completed pre-entry bar and enough next-open fills."""
    return pd.DataFrame({
        "open_time": pd.date_range(START - STEP, periods=count, freq="5min"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1.0, "segment_id": 11,
    })


def management(sides):
    # The management segment counter deliberately differs from raw5's counter.
    return pd.DataFrame({
        "open_time": pd.date_range(START - STEP, periods=len(sides), freq="5min"),
        "ma": 100.0, "ma_side": sides, "ma_slope_atr": np.nan,
        "high": 101.0, "low": 99.0, "close": 100.0, "segment_id": 4,
    })


def entries(direction=1, stop=None):
    return pd.DataFrame([{
        "event_id": "synthetic", "decision_time": START, "direction": direction,
        "initial_stop": (90.0 if direction == 1 else 110.0) if stop is None else stop,
        "signal_atr": 2.0, "unchanged_feature": 0.61,
    }])


def replay(sides, *, raw=None, mg=None, direction=1, stop=None, cutoff=None, **policy):
    return simulate_events(
        raw_bars() if raw is None else raw,
        management(sides) if mg is None else mg,
        entries(direction, stop), dict(POLICY, **policy), end_exclusive=cutoff,
    ).iloc[0]


@pytest.mark.parametrize("minutes,confirmations", [(15, 1), (60, 1), (5, 2), (5, True)])
def test_transition_policy_rejects_other_clocks_or_confirmation_counts(minutes, confirmations):
    with pytest.raises(ValueError):
        replay([1, -1], management_minutes=minutes, confirmations=confirmations)


@pytest.mark.parametrize("direction", [1, -1])
def test_initially_aligned_transition_matches_baseline_fill_cost_and_excursions(direction):
    raw = raw_bars()
    raw.loc[2, ["open", "high", "low", "close"]] = [103.0, np.nan, np.nan, np.nan]
    mg = management([direction, -direction])
    transition = replay([], raw=raw, mg=mg, direction=direction)
    baseline = simulate_events(raw, mg, entries(direction), {"management_minutes": 5}).iloc[0]
    assert transition.outcome == "transition_colour_exit"
    assert transition.exit_time == START + STEP
    assert transition.exit_price == 103.0
    assert transition.transition_initial_state == "aligned"
    assert transition.transition_initial_side == direction
    assert transition.transition_initial_open_time == START - STEP
    assert transition.transition_armed_at == START
    assert transition.transition_first_armed_at == START
    assert transition.transition_trigger_previous_open_time == START - STEP
    assert transition.transition_trigger_open_time == START
    assert transition.transition_trigger_available_at == START + STEP
    assert transition.gross_return - transition.net_return == pytest.approx(0.002)
    assert transition.initial_stop == baseline.initial_stop
    assert transition.partial_fraction == 0
    assert transition.exit_remaining_fraction == 1
    assert not transition.funding_modelled
    for name in baseline.index:
        if name != "outcome":
            if pd.isna(baseline[name]):
                assert pd.isna(transition[name]), name
            else:
                assert baseline[name] == transition[name], name
    assert not any(name.startswith("transition_") for name in baseline.index)


@pytest.mark.parametrize("direction", [1, -1])
def test_initial_opposite_persists_until_an_observed_aligned_then_opposite_edge(direction):
    result = replay([-direction, -direction, -direction, direction, direction, -direction], direction=direction)
    assert result.transition_initial_state == "opposite"
    assert result.transition_armed_at == START + 3 * STEP
    assert result.transition_first_armed_at == START + 3 * STEP
    assert result.exit_time == START + 5 * STEP
    assert result.transition_trigger_previous_open_time == START + 3 * STEP
    assert result.transition_trigger_open_time == START + 4 * STEP
    assert result.outcome == "transition_colour_exit"


def test_an_old_preentry_aligned_colour_is_not_carried_across_opposite_initial_colour():
    mg = management([-1, -1, -1, -1])
    earlier = mg.iloc[[0]].copy()
    earlier["open_time"] = START - 2 * STEP
    earlier["ma_side"] = 1
    mg = pd.concat([earlier, mg], ignore_index=True)
    result = replay([], mg=mg, max_hours=0.25)
    assert result.transition_initial_state == "opposite"
    assert pd.isna(result.transition_armed_at)
    assert pd.isna(result.transition_first_armed_at)
    assert result.outcome == "time_exit"
    assert result.exit_time == START + 3 * STEP


@pytest.mark.parametrize("direction", [1, -1])
def test_never_aligned_still_obeys_fixed_intrabar_stop(direction):
    raw = raw_bars()
    raw.loc[3, "low" if direction == 1 else "high"] = 89.0 if direction == 1 else 111.0
    result = replay([-direction] * 8, raw=raw, direction=direction)
    assert result.outcome == "hard_stop"
    assert result.exit_time == START + 3 * STEP
    assert result.exit_price == result.initial_stop
    assert result.net_r == pytest.approx(-1.02)
    assert pd.isna(result.transition_armed_at)
    assert pd.isna(result.transition_trigger_open_time)


def test_never_aligned_exits_at_exact_default_seventy_two_hour_open():
    raw = raw_bars(867)
    result = replay([-1] * 866, raw=raw)
    assert result.outcome == "time_exit"
    assert result.exit_time == START + pd.Timedelta(hours=72)
    assert result.hold_minutes == 4320
    assert result.closed
    assert result.net_return == pytest.approx(-0.002)
    assert pd.isna(result.transition_armed_at)


@pytest.mark.parametrize("invalid_side", [0.0, np.nan, np.inf, 2.0, pd.NA, None])
def test_invalid_colour_unarms_and_requires_a_new_adjacent_edge(invalid_side):
    result = replay([1, 1, invalid_side, -1, 1, -1])
    assert result.outcome == "transition_colour_exit"
    assert result.exit_time == START + 5 * STEP
    assert result.transition_armed_at == START + 4 * STEP
    assert result.transition_first_armed_at == START
    assert result.transition_reset_count == 1


@pytest.mark.parametrize("column,value", [
    ("ma", np.nan), ("ma", np.inf), ("ma", -1), ("high", np.nan),
    ("low", 110.0), ("close", 102.0), ("segment_id", np.nan), ("segment_id", np.inf),
])
def test_invalid_management_fields_unarm_even_if_colour_is_finite(column, value):
    mg = management([1, 1, -1, -1, 1, -1])
    mg[column] = mg[column].astype(float)
    mg.loc[2, column] = value
    result = replay([], mg=mg)
    assert result.exit_time == START + 5 * STEP
    assert result.transition_armed_at == START + 4 * STEP
    assert result.transition_reset_count == 1


@pytest.mark.parametrize("stale", [False, True])
def test_missing_or_off_grid_management_bar_is_not_forward_filled(stale):
    mg = management([1, 1, -1, -1, 1, -1])
    if stale:
        mg.loc[2, "open_time"] += pd.Timedelta(minutes=1)
    else:
        mg = mg.drop(index=2)
    result = replay([], mg=mg)
    assert result.exit_time == START + 5 * STEP
    assert result.transition_reset_count == 1
    assert result.transition_last_reset_reason == "missing_management"


@pytest.mark.parametrize("initial_side", [0.0, np.nan, -1.0])
def test_unknown_or_opposite_initial_colour_cannot_use_future_bar_to_arm(initial_side):
    mg = management([initial_side, -1, -1, 1, -1])
    result = replay([], mg=mg)
    assert result.exit_time == START + 4 * STEP
    assert result.transition_armed_at == START + 3 * STEP
    assert result.transition_initial_state == ("opposite" if initial_side == -1 else "unknown")


@pytest.mark.parametrize("initial_problem", ["missing_management", "stale_management", "missing_source", "source_segment_change", "invalid_completed_source"])
def test_initial_colour_requires_exact_close_and_source_continuity(initial_problem):
    raw = raw_bars()
    mg = management([1, -1, -1, 1, -1])
    if initial_problem == "missing_management":
        mg = mg.drop(index=0)
    elif initial_problem == "stale_management":
        mg.loc[0, "open_time"] -= STEP
    elif initial_problem == "missing_source":
        raw = raw.drop(index=0)
    elif initial_problem == "source_segment_change":
        raw.loc[0, "segment_id"] = 10
    else:
        raw.loc[0, "high"] = np.nan
    result = replay([], raw=raw, mg=mg)
    assert result.transition_initial_state == "unknown"
    assert result.exit_time == START + 4 * STEP
    assert result.transition_armed_at == START + 3 * STEP
    assert result.transition_initial_reason != "valid"


def test_distinct_raw_and_management_segment_spaces_do_not_discard_valid_edge():
    result = replay([1, -1])
    assert result.transition_initial_state == "aligned"
    assert result.exit_time == START + STEP
    assert result.transition_reset_count == 0


def test_nonfinite_source_segment_does_not_initialize_or_arm_colour():
    raw = raw_bars()
    raw["segment_id"] = np.inf
    result = replay([1, -1, 1, -1], raw=raw, max_hours=0.25)
    assert result.transition_initial_state == "unknown"
    assert result.transition_initial_reason == "source_segment_change"
    assert pd.isna(result.transition_first_armed_at)
    assert pd.isna(result.transition_trigger_open_time)


def test_management_segment_transition_cuts_old_edge_then_can_rearm():
    mg = management([1, -1, -1, 1, -1])
    mg.loc[1:, "segment_id"] = 5
    result = replay([], mg=mg)
    assert result.exit_time == START + 4 * STEP
    assert result.transition_reset_count == 1
    assert result.transition_last_reset_reason == "management_sequence_change"


def test_first_valid_aligned_bar_in_new_management_segment_can_arm():
    mg = management([1, 1, -1])
    mg.loc[1:, "segment_id"] = 5
    result = replay([], mg=mg)
    assert result.transition_armed_at == START + STEP
    assert result.transition_first_armed_at == START
    assert result.exit_time == START + 2 * STEP
    assert result.transition_reset_count == 1


def test_first_armed_time_survives_later_reset_when_trade_finishes_unarmed():
    result = replay([1, 1, np.nan, -1, -1], max_hours=1 / 3)
    assert result.outcome == "time_exit"
    assert result.transition_first_armed_at == START
    assert pd.isna(result.transition_armed_at)


def test_unknown_initial_state_records_only_first_observed_arm_across_resets():
    result = replay([np.nan, 1, np.nan, -1, 1, -1])
    assert result.transition_initial_state == "unknown"
    assert result.transition_first_armed_at == START + STEP
    assert result.transition_armed_at == START + 4 * STEP
    assert result.exit_time == START + 5 * STEP


@pytest.mark.parametrize("gap_type", ["timestamp", "segment"])
def test_execution_gap_censors_before_colour_edge_instead_of_exiting(gap_type):
    raw = raw_bars()
    if gap_type == "timestamp":
        raw = raw.drop(index=2)
    else:
        raw.loc[2:, "segment_id"] = 12
    result = replay([1, -1, -1], raw=raw)
    assert result.outcome == "data_gap_censored"
    assert result.exit_time == START + STEP
    assert not result.closed
    assert pd.isna(result.net_return)
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("direction", [1, -1])
def test_gap_open_stop_has_priority_over_same_timestamp_transition(direction):
    raw = raw_bars()
    raw.loc[2, "open"] = 88.0 if direction == 1 else 112.0
    raw.loc[2, ["high", "low", "close"]] = np.nan
    result = replay([direction, -direction], raw=raw, direction=direction)
    assert result.outcome == "hard_stop_gap"
    assert result.exit_time == START + STEP
    assert result.exit_price == (88.0 if direction == 1 else 112.0)
    assert result.net_return == pytest.approx(-0.122)
    assert pd.isna(result.transition_trigger_open_time)


def test_intrabar_stop_before_management_close_prevents_future_transition_exit():
    raw = raw_bars()
    raw.loc[1, "low"] = 80.0
    result = replay([1, -1], raw=raw)
    assert result.outcome == "hard_stop"
    assert result.exit_time == START + STEP
    assert result.exit_price == 90.0
    assert pd.isna(result.transition_trigger_open_time)


def test_confirmed_edge_exits_before_current_bar_future_stop_extrema():
    raw = raw_bars()
    raw.loc[2, "low"] = 80.0
    result = replay([1, -1], raw=raw)
    assert result.outcome == "transition_colour_exit"
    assert result.exit_time == START + STEP
    assert result.exit_price == 100.0
    assert result.max_adverse_r == pytest.approx(-0.1)


def test_confirmed_edge_precedes_coincident_deadline_without_changing_clock():
    result = replay([1, -1], max_hours=1 / 12)
    assert result.outcome == "transition_colour_exit"
    assert result.exit_time == START + STEP


def test_cutoff_cannot_consume_a_completed_edge_without_its_execution_open():
    result = replay([1, -1], cutoff=START + STEP)
    assert result.outcome == "right_censored"
    assert result.exit_time == START + STEP
    assert not result.closed
    assert pd.isna(result.net_return)
    assert pd.isna(result.transition_trigger_open_time)


def test_incomplete_current_bar_cannot_change_initial_state_or_trigger():
    raw = raw_bars()
    raw.loc[1, ["high", "low", "close"]] = [1000.0, 1.0, 2.0]
    result = replay([1, -1], raw=raw, cutoff=START + pd.Timedelta(minutes=2))
    assert result.transition_initial_state == "aligned"
    assert result.transition_armed_at == START
    assert result.outcome == "right_censored"
    assert result.exit_time == START
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("direction,stop", [(1, 100.0), (1, 101.0), (-1, 99.0), (-1, 100.0)])
def test_invalid_entry_risk_is_not_repaired_or_armed(direction, stop):
    result = replay([direction, -direction], direction=direction, stop=stop)
    assert result.outcome == "entry_invalid_risk"
    assert result.initial_stop == stop
    assert result.transition_initial_reason == "entry_not_validated"
    assert pd.isna(result.transition_armed_at)
    assert not result.closed


def test_future_price_and_colour_mutation_cannot_move_stop_or_completed_edge_exit():
    raw = raw_bars()
    mg = management([-1, -1, 1, -1, 1, -1])
    before = replay([], raw=raw, mg=mg)
    # The filled exit is at +15m. That candle's HLC and all later data are future.
    raw.loc[4, ["high", "low", "close"]] = np.nan
    raw.loc[5:, ["open", "high", "low", "close"]] = [1000, 1100, 1, 900]
    mg.loc[4:, ["ma_side", "ma"]] = [np.nan, 1000]
    after = replay([], raw=raw, mg=mg)
    pd.testing.assert_series_equal(before, after)


def test_copied_event_diagnostics_are_overwritten_for_new_replay():
    original = replay([1, -1]).to_frame().T
    original["transition_armed_at"] = START - pd.Timedelta(days=10)
    original["transition_initial_state"] = "stale"
    result = simulate_events(raw_bars(), management([-1, -1, 1, -1]), original, POLICY).iloc[0]
    assert result.transition_initial_state == "opposite"
    assert result.transition_armed_at == START + 2 * STEP
    assert result.exit_time == START + 3 * STEP
