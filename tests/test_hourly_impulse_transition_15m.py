"""Synthetic-only native-15m transition clock, source support and 5m risk tests."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


START = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
FIFTEEN = pd.Timedelta(minutes=15)
POLICY = {"exit_mode": "transition_colour", "management_minutes": 15, "confirmations": 1}


def raw_bars(count=40):
    return pd.DataFrame({
        "open_time": pd.date_range(START-FIFTEEN, periods=count, freq="5min"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1.0, "segment_id": 17,
    })


def management(sides):
    return pd.DataFrame({
        "open_time": pd.date_range(START-FIFTEEN, periods=len(sides), freq="15min"),
        "ma": 100.0, "ma_side": sides, "ma_slope_atr": np.nan,
        "high": 101.0, "low": 99.0, "close": 100.0, "segment_id": 3,
    })


def entries(direction=1, phase=0, stop=None):
    return pd.DataFrame([{
        "event_id": "synthetic15", "decision_time": START+pd.Timedelta(minutes=phase),
        "direction": direction, "initial_stop": (90.0 if direction == 1 else 110.0) if stop is None else stop,
        "signal_atr": 2.0, "unchanged_feature": 0.61,
    }])


def replay(sides, *, raw=None, mg=None, direction=1, phase=0, stop=None, cutoff=None, **policy):
    return simulate_events(raw_bars() if raw is None else raw, management(sides) if mg is None else mg,
                           entries(direction, phase, stop), dict(POLICY, **policy),
                           end_exclusive=cutoff).iloc[0]


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_valid_15m_policy_seed_and_first_completed_edge_all_entry_phases(direction, phase):
    raw = raw_bars()
    raw.loc[raw.open_time.eq(START+FIFTEEN), ["open", "high", "low", "close"]] = [103.0, np.nan, np.nan, np.nan]
    result = replay([direction, -direction], raw=raw, direction=direction, phase=phase)
    assert result.transition_initial_state == "aligned"
    assert result.transition_initial_open_time == START-FIFTEEN
    assert result.transition_first_armed_at == START+pd.Timedelta(minutes=phase)
    assert result.transition_armed_at == result.transition_first_armed_at
    assert result.transition_reset_count == 0
    assert result.transition_trigger_previous_open_time == START-FIFTEEN
    assert result.transition_trigger_open_time == START
    assert result.transition_trigger_available_at == START+FIFTEEN
    assert result.outcome == "transition_colour_exit"
    assert result.exit_time == START+FIFTEEN
    assert result.hold_minutes == 15-phase
    assert result.exit_price == 103.0
    assert result.gross_return == pytest.approx(direction*.03)
    assert result.gross_return-result.net_return == pytest.approx(.002)
    assert result.initial_stop == (90.0 if direction == 1 else 110.0)
    assert result.partial_fraction == 0.0 and result.exit_remaining_fraction == 1.0
    assert not result.funding_modelled


@pytest.mark.parametrize("direction", [1, -1])
def test_aligned_hour_boundary_matches_native_15m_state_exit_essential_outcomes(direction):
    raw, mg, request = raw_bars(), management([direction, direction, -direction]), entries(direction)
    transition = simulate_events(raw, mg, request, POLICY).iloc[0]
    baseline = simulate_events(raw, mg, request, {"management_minutes": 15, "exit_mode": "colour"}).iloc[0]
    for name in baseline.index:
        if name == "outcome":
            continue
        if pd.isna(baseline[name]):
            assert pd.isna(transition[name]), name
        else:
            assert transition[name] == baseline[name], name


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_initial_opposite_requires_new_aligned_then_opposite_not_a_fresh_entry_exit(direction, phase):
    result = replay([-direction, -direction, -direction, direction, -direction], direction=direction, phase=phase)
    assert result.transition_initial_state == "opposite"
    assert result.transition_first_armed_at == START+3*FIFTEEN
    assert result.exit_time == START+4*FIFTEEN
    assert result.transition_reset_count == 0


def test_no_management_close_at_plus_five_or_ten_preserves_armed_state():
    result = replay([1, 1, -1])
    assert result.exit_time == START+2*FIFTEEN
    assert result.transition_reset_count == 0
    assert result.transition_armed_at == START


@pytest.mark.parametrize("invalid_side", [0.0, np.nan, np.inf, 2.0, pd.NA, None])
def test_invalid_expected_management_colour_resets_once_and_requires_new_edge(invalid_side):
    result = replay([1, invalid_side, -1, 1, -1])
    assert result.exit_time == START+4*FIFTEEN
    assert result.transition_reset_count == 1
    assert result.transition_first_armed_at == START
    assert result.transition_armed_at == START+3*FIFTEEN


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_off_grid_expected_15m_is_not_forward_filled(missing):
    mg = management([1, -1, -1, 1, -1])
    if missing:
        mg = mg.drop(index=1)
    else:
        mg.loc[1, "open_time"] += FIVE
    result = replay([], mg=mg)
    assert result.exit_time == START+4*FIFTEEN
    assert result.transition_reset_count == 1
    assert result.transition_last_reset_reason == "missing_management"


def test_management_segment_change_cuts_edge_but_never_compares_to_raw_counter():
    mg = management([1, -1, -1, 1, -1])
    mg.loc[1:, "segment_id"] = 6
    result = replay([], mg=mg)
    assert result.transition_initial_state == "aligned"
    assert result.exit_time == START+4*FIFTEEN
    assert result.transition_reset_count == 1
    assert result.transition_last_reset_reason == "management_sequence_change"


@pytest.mark.parametrize("column,value", [
    ("ma", np.nan), ("ma", 0), ("high", np.inf), ("low", 102.0),
    ("close", 102.0), ("segment_id", np.nan), ("segment_id", np.inf),
])
def test_invalid_expected_management_hlc_ma_segment_unarms(column, value):
    mg = management([1, -1, -1, 1, -1])
    mg[column] = mg[column].astype(float)
    mg.loc[1, column] = value
    result = replay([], mg=mg)
    assert result.exit_time == START+4*FIFTEEN
    assert result.transition_reset_count == 1


@pytest.mark.parametrize("source_offset", [-15, -10, -5])
@pytest.mark.parametrize("problem", ["missing", "segment", "invalid_ohlc"])
def test_initial_15m_requires_all_three_complete_raw_source_bars(source_offset, problem):
    raw = raw_bars()
    mask = raw.open_time.eq(START+pd.Timedelta(minutes=source_offset))
    if problem == "missing":
        raw = raw.loc[~mask].copy()
    elif problem == "segment":
        raw.loc[mask, "segment_id"] = 15
    else:
        raw.loc[mask, "high"] = np.nan
    result = replay([1, -1, 1, -1], raw=raw)
    assert result.transition_initial_state == "unknown"
    assert result.transition_initial_reason == {
        "missing": "missing_source", "segment": "source_segment_change", "invalid_ohlc": "invalid_completed_source",
    }[problem]
    assert result.exit_time == START+3*FIFTEEN
    assert result.transition_first_armed_at == START+2*FIFTEEN


@pytest.mark.parametrize("problem", ["missing", "segment", "invalid_ohlc"])
def test_phase_entry_also_requires_source_continuity_after_last_native_close(problem):
    raw = raw_bars()
    mask = raw.open_time.eq(START+FIVE)
    if problem == "missing":
        raw = raw.loc[~mask].copy()
    elif problem == "segment":
        raw.loc[mask, "segment_id"] = 12
    else:
        raw.loc[mask, "high"] = np.nan
    result = replay([1, -1, 1, -1], raw=raw, phase=10)
    assert result.transition_initial_state == "unknown"
    assert result.exit_time == START+3*FIFTEEN
    assert result.transition_first_armed_at == START+2*FIFTEEN
    assert result.transition_reset_count == 1


@pytest.mark.parametrize("phase", [0, 5, 10])
def test_initial_missing_native_bar_never_falls_back_to_older_aligned_bar(phase):
    mg = management([1, -1, 1, -1])
    old = mg.iloc[[0]].copy()
    old.open_time -= FIFTEEN
    mg = pd.concat([old, mg.iloc[1:]], ignore_index=True)
    result = replay([], mg=mg, phase=phase)
    assert result.transition_initial_state == "unknown"
    assert result.transition_initial_reason == "missing_management"
    assert result.exit_time == START+3*FIFTEEN


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("offset", [0, 5, 10])
def test_every_intervening_5m_bar_keeps_fixed_intrabar_hard_stop(direction, offset):
    raw = raw_bars()
    raw.loc[raw.open_time.eq(START+pd.Timedelta(minutes=offset)), "low" if direction == 1 else "high"] = 89.0 if direction == 1 else 111.0
    result = replay([direction, -direction], raw=raw, direction=direction)
    assert result.outcome == "hard_stop"
    assert result.exit_time == START+pd.Timedelta(minutes=offset+5)
    assert result.exit_price == result.initial_stop
    assert result.net_r == pytest.approx(-1.02)
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("direction", [1, -1])
def test_gap_open_stop_precedes_same_timestamp_native_colour_exit(direction):
    raw = raw_bars()
    fill = 88.0 if direction == 1 else 112.0
    raw.loc[raw.open_time.eq(START+FIFTEEN), "open"] = fill
    result = replay([direction, -direction], raw=raw, direction=direction)
    assert result.outcome == "hard_stop_gap"
    assert result.exit_time == START+FIFTEEN
    assert result.exit_price == fill
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("problem", ["missing", "segment"])
def test_open_trade_source_gap_censors_before_colour_update(problem):
    raw = raw_bars()
    mask = raw.open_time.eq(START+FIVE)
    if problem == "missing":
        raw = raw.loc[~mask].copy()
    else:
        raw.loc[mask, "segment_id"] = 18
    result = replay([1, -1], raw=raw)
    assert result.outcome == "data_gap_censored"
    assert result.exit_time == START+FIVE
    assert not result.closed and pd.isna(result.net_return)
    assert pd.isna(result.transition_trigger_open_time)


def test_never_aligned_obeys_exact_72h_deadline_not_next_native_close():
    result = replay([-1]*291, raw=raw_bars(870), phase=5)
    assert result.outcome == "time_exit"
    assert result.exit_time == START+FIVE+pd.Timedelta(hours=72)
    assert result.hold_minutes == 4320
    assert result.transition_reset_count == 0
    assert pd.isna(result.transition_first_armed_at)


def test_short_deadline_between_native_closes_does_not_reset_or_wait_for_colour():
    result = replay([1, -1], max_minutes=10)
    assert result.outcome == "time_exit"
    assert result.exit_time == START+2*FIVE
    assert result.transition_reset_count == 0
    assert result.transition_armed_at == START


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_invalid_original_risk_is_rejected_before_seed(direction, phase):
    result = replay([direction, -direction], direction=direction, phase=phase, stop=100.0)
    assert result.outcome == "entry_invalid_risk"
    assert not result.closed
    assert result.transition_initial_reason == "entry_not_validated"


@pytest.mark.parametrize("phase", [0, 5, 10])
def test_unfinished_entry_hlc_and_future_management_cannot_change_initial_seed(phase):
    entry_time = START+pd.Timedelta(minutes=phase)
    raw = raw_bars()
    mg = management([1, -1, 1, -1])
    before = replay([], raw=raw, mg=mg, phase=phase, cutoff=entry_time+pd.Timedelta(minutes=1))
    raw.loc[raw.open_time.ge(entry_time), ["high", "low", "close"]] = np.nan
    mg.loc[mg.open_time.ge(START), ["ma_side", "ma", "high", "low", "close"]] = np.nan
    after = replay([], raw=raw, mg=mg, phase=phase, cutoff=entry_time+pd.Timedelta(minutes=1))
    pd.testing.assert_series_equal(before, after)
    assert after.transition_initial_state == "aligned"
    assert after.outcome == "right_censored"


def test_suffix_mutation_after_closed_exit_cannot_change_any_result_or_fixed_stop():
    raw, mg = raw_bars(), management([1, -1, 1, -1, 1])
    before = replay([], raw=raw, mg=mg)
    raw.loc[raw.open_time.gt(START+FIFTEEN), ["open", "high", "low", "close"]] = 200.0
    raw.loc[raw.open_time.eq(START+FIFTEEN), ["high", "low", "close"]] = np.nan
    mg.loc[mg.open_time.ge(START+FIFTEEN), ["ma_side", "ma", "high", "low", "close"]] = np.nan
    after = replay([], raw=raw, mg=mg)
    pd.testing.assert_series_equal(before, after)


def test_observation_cutoff_at_close_does_not_invent_unavailable_next_open():
    result = replay([1, -1], cutoff=START+FIFTEEN)
    assert result.outcome == "right_censored"
    assert not result.closed and pd.isna(result.net_return)
    assert pd.isna(result.transition_trigger_open_time)


@pytest.mark.parametrize("minutes,confirmations", [(60, 1), (15, 2), (15, True)])
def test_transition_15m_keeps_unsupported_clock_and_confirmation_rejections(minutes, confirmations):
    with pytest.raises(ValueError):
        replay([1, -1], management_minutes=minutes, confirmations=confirmations)
