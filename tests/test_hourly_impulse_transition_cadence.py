"""Synthetic-only fixed-native5 features with a separate quarter-hour decision clock."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


START = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
QUARTER = pd.Timedelta(minutes=15)
POLICY = {"exit_mode": "transition_colour", "management_minutes": 5,
          "confirmations": 1, "decision_minutes": 15}


def raw_bars(count=32):
    return pd.DataFrame({
        "open_time": pd.date_range(START-FIVE, periods=count, freq="5min"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1.0, "segment_id": 17,
    })


def management(sides):
    # Array index 0 is available exactly at START, not START+5m.
    return pd.DataFrame({
        "open_time": pd.date_range(START-FIVE, periods=len(sides), freq="5min"),
        "ma": 100.0, "ma_side": sides, "ma_slope_atr": np.nan,
        "high": 101.0, "low": 99.0, "close": 100.0, "segment_id": 3,
    })


def entries(direction=1, phase=0, stop=None):
    return pd.DataFrame([{
        "event_id": "synthetic_cadence", "decision_time": START+pd.Timedelta(minutes=phase),
        "direction": direction, "initial_stop": (90.0 if direction == 1 else 110.0) if stop is None else stop,
        "signal_atr": 2.0, "unchanged_feature": .61,
    }])


def replay(sides, *, raw=None, mg=None, direction=1, phase=0, stop=None, cutoff=None, **policy):
    return simulate_events(raw_bars() if raw is None else raw, management(sides) if mg is None else mg,
                           entries(direction, phase, stop), dict(POLICY, **policy),
                           end_exclusive=cutoff).iloc[0]


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_exact_native5_entry_seed_to_first_quarter_sample_all_phases(direction, phase):
    sides = [direction]*20
    sides[3] = -direction
    raw = raw_bars()
    raw.loc[raw.open_time.eq(START+QUARTER), ["open", "high", "low", "close"]] = [103., np.nan, np.nan, np.nan]
    result = replay(sides, raw=raw, direction=direction, phase=phase)
    entry = START+pd.Timedelta(minutes=phase)
    assert result.transition_initial_state == "aligned"
    assert result.transition_initial_open_time == entry-FIVE
    assert result.transition_first_armed_at == entry
    assert result.transition_trigger_previous_open_time == entry-FIVE
    assert result.transition_trigger_previous_available_at == entry
    assert result.transition_trigger_open_time == START+2*FIVE
    assert result.transition_trigger_available_at == START+QUARTER
    assert result.exit_time == START+QUARTER
    assert result.hold_minutes == 15-phase
    assert result.outcome == "transition_colour_exit"
    assert result.transition_sample_count == 1
    assert result.transition_reset_count == 0
    assert result.gross_return == pytest.approx(direction*.03)
    assert result.gross_return-result.net_return == pytest.approx(.002)
    assert result.partial_fraction == 0 and result.exit_remaining_fraction == 1
    assert not result.funding_modelled


@pytest.mark.parametrize("direction", [1, -1])
def test_unsampled_flip_then_flip_back_is_not_latched(direction):
    sides = np.array([1, -1, -1, 1, 1, -1, 1, -1, -1, -1])*direction
    result = replay(sides, direction=direction)
    assert result.exit_time == START+3*QUARTER
    assert result.transition_sample_count == 3
    assert result.transition_reset_count == 0
    assert result.transition_trigger_previous_available_at == START+2*QUARTER
    assert result.transition_trigger_previous_open_time == START+2*QUARTER-FIVE
    assert result.transition_armed_at == START
    immediate = replay(sides, direction=direction, decision_minutes=5)
    assert immediate.exit_time == START+FIVE


@pytest.mark.parametrize("direction", [1, -1])
def test_sustained_opposite_is_seen_only_at_sampling_clock(direction):
    result = replay(np.array([1, -1, -1, -1])*direction, direction=direction)
    assert result.exit_time == START+QUARTER
    assert result.transition_trigger_previous_available_at == START
    assert result.transition_sample_count == 1


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_initial_opposite_ignores_unsampled_alignment(direction, phase):
    sides = np.array([-1, -1, -1, -1, 1, 1, -1, 1, 1, 1, -1, -1, -1])*direction
    result = replay(sides, direction=direction, phase=phase)
    assert result.transition_initial_state == "opposite"
    assert result.transition_first_armed_at == START+3*QUARTER
    assert result.exit_time == START+4*QUARTER
    assert result.transition_sample_count == 4
    assert result.transition_reset_count == 0


@pytest.mark.parametrize("initial_side", [0., np.nan, np.inf, pd.NA, None])
def test_unknown_seed_cannot_arm_from_off_clock_colours(initial_side):
    sides = [initial_side, 1, 1, -1, 1, 1, 1, -1, -1, -1]
    result = replay(sides)
    assert result.transition_initial_state == "unknown"
    assert result.transition_first_armed_at == START+2*QUARTER
    assert result.exit_time == START+3*QUARTER
    assert result.transition_reset_count == 0


@pytest.mark.parametrize("invalid_side", [0., np.nan, np.inf, 2., pd.NA, None])
@pytest.mark.parametrize("offset_index", [1, 2])
def test_invalid_unsampled_colour_resets_but_valid_unsampled_does_not_rearm(invalid_side, offset_index):
    sides = [1, 1, 1, -1, 1, 1, 1, -1, -1, -1]
    sides[offset_index] = invalid_side
    result = replay(sides)
    assert result.exit_time == START+3*QUARTER
    assert result.transition_reset_count == 1
    assert result.transition_first_armed_at == START
    assert result.transition_armed_at == START+2*QUARTER


@pytest.mark.parametrize("column,value", [
    ("ma", np.nan), ("ma", 0.), ("high", np.inf), ("low", 102.),
    ("close", 102.), ("segment_id", np.nan), ("segment_id", np.inf),
])
def test_invalid_unsampled_management_fields_cut_sample_edge(column, value):
    mg = management([1, 1, 1, -1, 1, 1, 1, -1, -1, -1])
    mg[column] = mg[column].astype(float)
    mg.loc[1, column] = value
    result = replay([], mg=mg)
    assert result.exit_time == START+3*QUARTER
    assert result.transition_reset_count == 1
    assert result.transition_armed_at == START+2*QUARTER


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("offset_index", [1, 2])
def test_missing_or_off_grid_unsampled_management_is_not_silently_ignored(stale, offset_index):
    mg = management([1, 1, 1, -1, 1, 1, 1, -1, -1, -1])
    if stale:
        mg.loc[offset_index, "open_time"] += pd.Timedelta(minutes=1)
    else:
        mg = mg.drop(index=offset_index)
    result = replay([], mg=mg)
    assert result.exit_time == START+3*QUARTER
    assert result.transition_reset_count == 1
    assert result.transition_last_reset_reason == "missing_management"


@pytest.mark.parametrize("returns_to_old_counter", [False, True])
def test_unsampled_management_segment_changes_reset_even_when_sample_counter_matches_seed(returns_to_old_counter):
    mg = management([1, 1, 1, -1, 1, 1, 1, -1, -1, -1])
    mg.loc[1:, "segment_id"] = 4
    if returns_to_old_counter:
        mg.loc[2:, "segment_id"] = 3
    result = replay([], mg=mg)
    assert result.transition_initial_state == "aligned"
    assert result.exit_time == START+3*QUARTER
    assert result.transition_reset_count == (2 if returns_to_old_counter else 1)
    assert result.transition_last_reset_reason == "management_sequence_change"
    assert result.transition_armed_at == START+2*QUARTER


@pytest.mark.parametrize("problem", ["missing", "invalid", "segment"])
def test_sampled_observation_failure_resets_then_known_sample_reseeds(problem):
    mg = management([1, 1, 1, -1, 1, 1, -1, 1, 1, 1, -1, -1, -1])
    if problem == "missing":
        mg = mg.drop(index=3)
    elif problem == "invalid":
        mg.loc[3, "ma"] = np.nan
    else:
        mg.loc[3:, "segment_id"] = 8
    result = replay([], mg=mg)
    assert result.exit_time == START+4*QUARTER
    assert result.transition_armed_at == START+3*QUARTER
    assert result.transition_reset_count == 1
    assert result.transition_sample_count == 4


def test_known_aligned_sample_can_seed_at_the_same_management_segment_reset():
    mg = management([-1, -1, -1, 1, 1, 1, -1])
    mg.loc[3:, "segment_id"] = 9
    result = replay([], mg=mg)
    assert result.transition_reset_count == 1
    assert result.transition_first_armed_at == START+QUARTER
    assert result.exit_time == START+2*QUARTER


@pytest.mark.parametrize("problem", ["raw_missing", "raw_segment", "raw_invalid", "mg_missing"])
def test_initial_seed_requires_exact_prior_native5_source(problem):
    raw, mg = raw_bars(), management([1, 1, 1, -1, 1, 1, 1, -1, -1, -1])
    if problem == "raw_missing":
        raw = raw.drop(index=0)
    elif problem == "raw_segment":
        raw.loc[0, "segment_id"] = 13
    elif problem == "raw_invalid":
        raw.loc[0, "high"] = np.nan
    else:
        mg = mg.drop(index=0)
    result = replay([], raw=raw, mg=mg)
    assert result.transition_initial_state == "unknown"
    assert result.exit_time == START+3*QUARTER
    assert result.transition_first_armed_at == START+2*QUARTER


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("offset", [0, 5, 10, 15, 20])
def test_intrabar_hard_stop_runs_every_raw5_bar_not_only_quarters(direction, offset):
    raw = raw_bars()
    raw.loc[raw.open_time.eq(START+pd.Timedelta(minutes=offset)), "low" if direction == 1 else "high"] = 89. if direction == 1 else 111.
    result = replay([direction]*15, raw=raw, direction=direction)
    assert result.outcome == "hard_stop"
    assert result.exit_time == START+pd.Timedelta(minutes=offset+5)
    assert result.exit_price == result.initial_stop
    assert result.net_r == pytest.approx(-1.02)
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("offset", [5, 15])
def test_gap_open_stop_precedes_management_or_sampling_work(direction, offset):
    raw = raw_bars()
    raw.loc[raw.open_time.eq(START+pd.Timedelta(minutes=offset)), "open"] = 88. if direction == 1 else 112.
    result = replay([direction]+[-direction]*12, raw=raw, direction=direction)
    assert result.outcome == "hard_stop_gap"
    assert result.exit_time == START+pd.Timedelta(minutes=offset)
    assert result.exit_price == (88. if direction == 1 else 112.)
    assert result.transition_sample_count == 0
    assert pd.isna(result.transition_trigger_available_at)


@pytest.mark.parametrize("problem", ["missing", "segment", "invalid_ohlc"])
def test_raw_data_gap_is_censored_on_raw_clock_not_delayed_to_next_sample(problem):
    raw = raw_bars()
    if problem == "missing":
        raw = raw.drop(index=2)
    elif problem == "segment":
        raw.loc[2, "segment_id"] = 22
    else:
        raw.loc[2, "high"] = np.nan
    result = replay([1]*15, raw=raw)
    assert result.outcome == "data_gap_censored"
    assert result.exit_time == START+FIVE
    assert not result.closed and pd.isna(result.net_return)
    assert result.transition_sample_count == 0


@pytest.mark.parametrize("phase", [0, 5, 10])
def test_default_72h_horizon_uses_entry_clock_even_between_quarters(phase):
    result = replay([-1]*873, raw=raw_bars(874), phase=phase)
    assert result.outcome == "time_exit"
    assert result.exit_time == START+pd.Timedelta(minutes=phase)+pd.Timedelta(hours=72)
    assert result.hold_minutes == 4320
    assert result.transition_reset_count == 0
    assert pd.isna(result.transition_first_armed_at)


def test_time_exit_between_samples_preserves_seed_without_waiting():
    result = replay([1, -1, -1, -1], max_minutes=10)
    assert result.outcome == "time_exit"
    assert result.exit_time == START+2*FIVE
    assert result.transition_armed_at == START
    assert result.transition_sample_count == 0


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_nonpositive_original_risk_rejects_before_sampling(direction, phase):
    result = replay([direction]*12, direction=direction, phase=phase, stop=100.)
    assert result.outcome == "entry_invalid_risk"
    assert not result.closed and pd.isna(result.net_return)
    assert result.transition_initial_reason == "entry_not_validated"
    assert result.transition_sample_count == 0


@pytest.mark.parametrize("phase", [0, 5, 10])
def test_initialization_does_not_read_unfinished_entry_hlc_or_future_native_colour(phase):
    raw, mg = raw_bars(), management([1]*20)
    entry_time = START+pd.Timedelta(minutes=phase)
    before = replay([], raw=raw, mg=mg, phase=phase, cutoff=entry_time+pd.Timedelta(minutes=1))
    raw.loc[raw.open_time.ge(entry_time), ["high", "low", "close"]] = np.nan
    mg.loc[mg.open_time.ge(entry_time), ["ma_side", "ma", "high", "low", "close"]] = np.nan
    after = replay([], raw=raw, mg=mg, phase=phase, cutoff=entry_time+pd.Timedelta(minutes=1))
    pd.testing.assert_series_equal(before, after)
    assert after.transition_initial_state == "aligned"
    assert after.outcome == "right_censored"


def test_future_suffix_and_exit_bar_extrema_cannot_change_closed_outcome():
    raw, mg = raw_bars(), management([1, -1, -1, -1]+[1]*10)
    before = replay([], raw=raw, mg=mg)
    raw.loc[raw.open_time.gt(START+QUARTER), ["open", "high", "low", "close"]] = 200.
    raw.loc[raw.open_time.eq(START+QUARTER), ["high", "low", "close"]] = np.nan
    mg.loc[mg.open_time.ge(START+QUARTER), ["ma_side", "ma", "high", "low", "close"]] = np.nan
    after = replay([], raw=raw, mg=mg)
    pd.testing.assert_series_equal(before, after)


def test_cutoff_equal_to_sample_close_does_not_create_missing_next_open_fill():
    result = replay([1, -1, -1, -1], cutoff=START+QUARTER)
    assert result.outcome == "right_censored"
    assert not result.closed and pd.isna(result.net_return)
    assert result.transition_sample_count == 0
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
@pytest.mark.parametrize("initial_side", [-1., 0., 1., np.nan])
def test_explicit_decision_five_is_all_field_exactly_equivalent_to_omission(direction, phase, initial_side):
    raw, mg = raw_bars(), management([initial_side]*3+[-1., 1., 1., -1., -1., 1., -1.])
    req = entries(direction, phase)
    policy = {"exit_mode": "transition_colour", "management_minutes": 5, "confirmations": 1}
    default = simulate_events(raw, mg, req, policy)
    explicit = simulate_events(raw, mg, req, dict(policy, decision_minutes=5))
    pd.testing.assert_frame_equal(default, explicit, check_exact=True)
    assert "transition_decision_minutes" not in explicit
    assert "transition_sample_count" not in explicit


@pytest.mark.parametrize("value", [None, True, False, np.bool_(True), np.nan, np.inf, 0, 10, 30, "15", 15.0])
def test_invalid_decision_clock_values_fail_closed(value):
    with pytest.raises(ValueError, match="decision_minutes"):
        replay([1, -1], decision_minutes=value)


@pytest.mark.parametrize("value", [5, 15])
@pytest.mark.parametrize("other_policy", [
    {"exit_mode": "colour"}, {"exit_mode": "slope_colour"},
    {"exit_mode": "partial_colour"}, {"exit_mode": "fixed_3r"},
    {"exit_mode": "hour_colour"}, {"management_minutes": 15},
    {"management_minutes": 60}, {"confirmations": 2},
])
def test_decision_option_is_only_for_native5_transition_one_confirmation(value, other_policy):
    with pytest.raises(ValueError):
        replay([1, -1], decision_minutes=value, **other_policy)
