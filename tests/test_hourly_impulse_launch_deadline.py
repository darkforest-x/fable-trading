"""Synthetic-only V11 launch deadline: causal closes, clocks and invariants.

No source prices or saved experimental outcomes are loaded. The finite-domain
tests cover every eligible 5m close and both mirrored directions; original-mode
oracle comparisons assert all existing fields, not just the exit label.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


START = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
HOUR = pd.Timedelta(minutes=60)
BASE = {"exit_mode": "transition_colour", "management_minutes": 5, "confirmations": 1}
LAUNCH = {"launch_deadline_minutes": 60, "launch_progress_r": 0.5}


def fixture(direction=1, phase=0, count=28):
    entry = START + pd.Timedelta(minutes=phase)
    raw = pd.DataFrame({
        "open_time": pd.date_range(entry-FIVE, periods=count, freq="5min"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1.0, "segment_id": 17,
    })
    mg = raw[["open_time", "high", "low", "close"]].copy()
    mg["ma"], mg["ma_side"], mg["ma_slope_atr"], mg["segment_id"] = 100.0, direction, np.nan, 3
    entries = pd.DataFrame([{
        "event_id": "synthetic_launch", "decision_time": entry, "direction": direction,
        "initial_stop": 100.0 - direction*10.0, "signal_atr": 2.0,
        "unchanged_feature": 0.61,
    }])
    return raw, mg, entries


def run(raw, mg, entries, *, launch=True, cutoff=None, **policy):
    selected = dict(BASE, max_minutes=120)
    if launch:
        selected.update(LAUNCH)
    selected.update(policy)
    return simulate_events(raw, mg, entries, selected, end_exclusive=cutoff)


def set_close(raw, at, price):
    idx = raw.index[raw.open_time.eq(at)][0]
    raw.loc[idx, "close"] = price
    raw.loc[idx, "high"] = max(raw.loc[idx, "open"], price) + 1.0
    raw.loc[idx, "low"] = min(raw.loc[idx, "open"], price) - 1.0


def original_columns_equal(candidate, baseline):
    assert not any(name.startswith("launch_") for name in baseline.columns)
    pd.testing.assert_frame_equal(candidate.loc[:, baseline.columns], baseline)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_no_progress_exits_at_actual_entry_plus60_open_with_original_risk_cost(direction, phase):
    raw, mg, entries = fixture(direction, phase)
    deadline = entries.iloc[0].decision_time + HOUR
    raw.loc[raw.open_time.eq(deadline), ["open", "high", "low", "close"]] = [102.0, np.nan, np.nan, np.nan]
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "launch_timeout_exit" and row.closed
    assert row.entry_time == entries.iloc[0].decision_time
    assert row.exit_time == row.launch_deadline_at == row.launch_deadline_checked_at == deadline
    assert row.entry_price == 100.0 and row.exit_price == 102.0
    assert row.initial_stop == 100-direction*10.0 and row.risk_atr == 5.0
    assert row.risk_pct == 0.1 and row.hold_minutes == 60.0
    assert row.gross_return == pytest.approx(direction*0.02)
    assert row.net_return == pytest.approx(direction*0.02-0.002)
    assert row.net_r == pytest.approx(row.net_return/0.1)
    assert row.partial_fraction == 0 and row.exit_remaining_fraction == 1
    assert row.realised_partial_gross_return == 0 and not row.funding_modelled
    assert row.launch_enabled and not row.launch_progress_reached
    assert row.launch_completed_close_count == 12
    assert row.launch_max_completed_close_r == 0
    assert row.launch_status == "timeout_exit" and pd.isna(row.launch_progress_first_at)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("phase", [0, 5, 10])
@pytest.mark.parametrize("close_number", range(1, 13))
def test_every_eligible_close_including_boundary_permanently_disables_timeout(direction, phase, close_number):
    raw, mg, entries = fixture(direction, phase)
    entry = entries.iloc[0].decision_time
    set_close(raw, entry+(close_number-1)*FIVE, 100.0+direction*5.0)
    candidate = run(raw, mg, entries)
    baseline = run(raw, mg, entries, launch=False)
    original_columns_equal(candidate, baseline)
    row = candidate.iloc[0]
    assert row.outcome == "time_exit" and row.hold_minutes == 120.0
    assert row.launch_progress_reached and row.launch_status == "progress_confirmed"
    assert row.launch_progress_first_at == entry+close_number*FIVE
    assert row.launch_completed_close_count == 12
    assert row.launch_max_completed_close_r == 0.5
    assert row.launch_deadline_checked_at == entry+HOUR


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("relative_close,expected", [(0.499999, False), (0.5, True), (0.500001, True)])
def test_threshold_is_fixed_initial_risk_and_inclusive_not_rounded(direction, relative_close, expected):
    raw, mg, entries = fixture(direction)
    set_close(raw, START+11*FIVE, 100+direction*relative_close*10)
    row = run(raw, mg, entries).iloc[0]
    assert bool(row.launch_progress_reached) is expected
    assert row.outcome == ("time_exit" if expected else "launch_timeout_exit")


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("source", ["preentry_close", "wick", "open", "after_deadline_close", "management_close"])
def test_no_seed_open_wick_future_or_management_price_can_confirm_progress(direction, source):
    raw, mg, entries = fixture(direction)
    if source == "preentry_close":
        set_close(raw, START-FIVE, 100+direction*6)
    elif source == "wick":
        raw.loc[raw.open_time.ge(START), "high" if direction == 1 else "low"] = 100+direction*6
    elif source == "open":
        row_index = raw.index[raw.open_time.eq(START+FIVE)][0]
        raw.loc[row_index, "open"] = 100+direction*6
        raw.loc[row_index, "high" if direction == 1 else "low"] = 100+direction*7
    elif source == "after_deadline_close":
        set_close(raw, START+HOUR, 100+direction*6)
    else:
        mg["close"] = 100+direction*6
        mg["high" if direction == 1 else "low"] = 100+direction*7
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "launch_timeout_exit"
    assert not row.launch_progress_reached and row.launch_max_completed_close_r == 0
    assert row.launch_completed_close_count == 12


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("invalid", ["missing", "side_nan", "ma_nan", "segment_change"])
@pytest.mark.parametrize("progress", [False, True])
def test_management_failure_only_resets_colour_and_cannot_change_raw_close_progress(direction, invalid, progress):
    raw, mg, entries = fixture(direction)
    if progress:
        set_close(raw, START+FIVE, 100+direction*5)
    if invalid == "missing":
        mg = mg.drop(index=range(2, 14))
    elif invalid == "side_nan":
        mg.loc[2:13, "ma_side"] = np.nan
    elif invalid == "ma_nan":
        mg.loc[2:13, "ma"] = np.nan
    else:
        mg.loc[2:, "segment_id"] = 12
    row = run(raw, mg, entries).iloc[0]
    assert row.transition_reset_count > 0
    assert bool(row.launch_progress_reached) is progress
    assert row.launch_completed_close_count == 12
    assert row.outcome == ("time_exit" if progress else "launch_timeout_exit")
    if progress:
        assert row.launch_progress_first_at == START+2*FIVE
        original_columns_equal(run(raw, mg, entries), run(raw, mg, entries, launch=False))


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("initial", ["opposite", "unknown", "aligned"])
def test_launch_progress_is_not_the_initial_colour_armed_flag(direction, initial):
    raw, mg, entries = fixture(direction)
    mg["ma_side"] = {"aligned": direction, "opposite": -direction, "unknown": np.nan}[initial]
    row = run(raw, mg, entries).iloc[0]
    assert row.transition_initial_state == initial
    assert row.outcome == "launch_timeout_exit" and not row.launch_progress_reached
    assert row.launch_completed_close_count == 12


@pytest.mark.parametrize("direction", [1, -1])
def test_reaching_progress_while_initially_opposite_can_keep_waiting_for_first_true_edge(direction):
    raw, mg, entries = fixture(direction)
    mg["ma_side"] = -direction
    mg.loc[16:17, "ma_side"] = direction
    set_close(raw, START+FIVE, 100+direction*5)
    row = run(raw, mg, entries).iloc[0]
    assert row.transition_initial_state == "opposite"
    assert row.transition_first_armed_at == START+16*FIVE
    assert row.outcome == "transition_colour_exit" and row.exit_time == START+18*FIVE
    assert row.launch_progress_first_at == START+2*FIVE


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("collision", ["previous_intrabar", "gap_open", "colour_exit", "max_duration"])
@pytest.mark.parametrize("qualifying_boundary_close", [False, True])
def test_existing_exits_win_at_deadline_without_double_fill(direction, collision, qualifying_boundary_close):
    raw, mg, entries = fixture(direction)
    if qualifying_boundary_close:
        set_close(raw, START+11*FIVE, 100+direction*5)
    options = {}
    if collision == "previous_intrabar":
        raw.loc[raw.open_time.eq(START+11*FIVE), "low" if direction == 1 else "high"] = 100-direction*11
    elif collision == "gap_open":
        raw.loc[raw.open_time.eq(START+HOUR), "open"] = 100-direction*12
    elif collision == "colour_exit":
        mg.loc[12, "ma_side"] = -direction
    else:
        options["max_minutes"] = 60
    candidate = run(raw, mg, entries, **options)
    original_columns_equal(candidate, run(raw, mg, entries, launch=False, **options))
    row = candidate.iloc[0]
    assert row.outcome == {"previous_intrabar": "hard_stop", "gap_open": "hard_stop_gap",
                           "colour_exit": "transition_colour_exit", "max_duration": "time_exit"}[collision]
    assert row.exit_time == START+HOUR
    assert pd.isna(row.launch_deadline_checked_at)
    assert row.launch_completed_close_count == (11 if collision == "previous_intrabar" else 12)
    assert bool(row.launch_progress_reached) is (qualifying_boundary_close and collision != "previous_intrabar")


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("minutes", [5, 25, 55])
@pytest.mark.parametrize("kind", ["hard_stop", "colour_exit", "time_exit"])
def test_exits_before_deadline_match_every_original_field(direction, minutes, kind):
    raw, mg, entries = fixture(direction)
    options = {}
    if kind == "hard_stop":
        raw.loc[raw.open_time.eq(START+pd.Timedelta(minutes=minutes-5)), "low" if direction == 1 else "high"] = 100-direction*11
    elif kind == "colour_exit":
        mg.loc[minutes//5, "ma_side"] = -direction
    else:
        options["max_minutes"] = minutes
    candidate = run(raw, mg, entries, **options)
    original_columns_equal(candidate, run(raw, mg, entries, launch=False, **options))
    row = candidate.iloc[0]
    assert row.exit_time == START+pd.Timedelta(minutes=minutes)
    assert row.launch_status == "prior_exit"
    assert pd.isna(row.launch_deadline_checked_at)


@pytest.mark.parametrize("kind", ["gap", "segment", "bad_close", "bad_high", "bad_low", "bad_open"])
@pytest.mark.parametrize("minute", [30, 55, 60])
def test_missing_raw_clocks_or_invalid_completed_source_do_not_invent_no_progress(kind, minute):
    raw, mg, entries = fixture()
    when = START+pd.Timedelta(minutes=minute)
    if kind == "gap":
        raw = raw[~raw.open_time.eq(when)].copy()
    elif kind == "segment":
        raw.loc[raw.open_time.ge(when), "segment_id"] = 18
    else:
        raw.loc[raw.open_time.eq(when), kind.removeprefix("bad_")] = np.nan
    row = run(raw, mg, entries).iloc[0]
    # At the timeout open, its still-unfinished HLC are deliberately irrelevant.
    if minute == 60 and kind in {"bad_close", "bad_high", "bad_low"}:
        assert row.outcome == "launch_timeout_exit" and row.closed
    else:
        assert row.outcome == "data_gap_censored" and not row.closed
        assert pd.isna(row.net_return) and pd.isna(row.net_r)
        assert row.launch_status == "unknown_source"
        assert pd.isna(row.launch_deadline_checked_at)


@pytest.mark.parametrize("cutoff_minutes,expected_count", [(57, 11), (60, 12)])
def test_exclusive_cutoff_does_not_supply_timeout_open_or_unfinished_close(cutoff_minutes, expected_count):
    raw, mg, entries = fixture()
    set_close(raw, START+11*FIVE, 106.0)
    row = run(raw, mg, entries, cutoff=START+pd.Timedelta(minutes=cutoff_minutes)).iloc[0]
    assert row.outcome == "right_censored" and not row.closed
    assert pd.isna(row.net_return) and pd.isna(row.launch_deadline_checked_at)
    assert row.launch_completed_close_count == expected_count
    assert bool(row.launch_progress_reached) is (cutoff_minutes == 60)


def test_physical_end_without_actual_deadline_open_is_censored_even_after_twelve_valid_closes():
    raw, mg, entries = fixture(count=13)
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "right_censored" and not row.closed
    assert row.exit_time == START+HOUR and row.launch_completed_close_count == 12
    assert pd.isna(row.net_return) and pd.isna(row.launch_deadline_checked_at)


@pytest.mark.parametrize("direction", [1, -1])
def test_causal_prefix_result_cannot_change_from_exit_bar_extrema_or_later_mutations(direction):
    raw, mg, entries = fixture(direction)
    before = run(raw, mg, entries)
    changed_raw = raw.copy()
    changed_raw.loc[changed_raw.open_time.ge(START+HOUR), ["high", "low", "close"]] = np.nan
    changed_raw.loc[changed_raw.open_time.gt(START+HOUR), "open"] = np.nan
    changed_mg = mg.copy()
    changed_mg.loc[changed_mg.open_time.ge(START+HOUR), ["ma_side", "ma", "high", "low", "close"]] = np.nan
    after = run(changed_raw, changed_mg, entries)
    pd.testing.assert_frame_equal(before, after)
    pd.testing.assert_frame_equal(before, run(raw[raw.open_time.le(START+HOUR)], mg[mg.open_time.lt(START+HOUR)], entries))


@pytest.mark.parametrize("direction", [1, -1])
def test_permanent_progress_keeps_original_72h_clock_and_stop_after_later_giveback(direction):
    raw, mg, entries = fixture(direction, count=870)
    set_close(raw, START, 100+direction*5)
    policy = dict(BASE, **LAUNCH)
    candidate = simulate_events(raw, mg, entries, policy)
    baseline = simulate_events(raw, mg, entries, BASE)
    original_columns_equal(candidate, baseline)
    row = candidate.iloc[0]
    assert row.hold_minutes == 4320 and row.outcome == "time_exit"
    assert row.launch_progress_reached and row.launch_completed_close_count == 12
    assert row.launch_progress_first_at == START+FIVE
    raw.loc[raw.open_time.eq(START+2*HOUR), "low" if direction == 1 else "high"] = 100-direction*11
    stopped = simulate_events(raw, mg, entries, policy).iloc[0]
    assert stopped.outcome == "hard_stop" and stopped.exit_price == entries.iloc[0].initial_stop
    assert stopped.launch_progress_reached


@pytest.mark.parametrize("field,bad", [
    ("initial_stop", 100.0), ("initial_stop", 101.0), ("signal_atr", 0.0),
    ("direction", 0.0), ("initial_stop", np.nan),
])
def test_invalid_entries_remain_rejected_and_do_not_create_progress_observations(field, bad):
    raw, mg, entries = fixture()
    entries.loc[0, field] = bad
    candidate = run(raw, mg, entries)
    original_columns_equal(candidate, run(raw, mg, entries, launch=False))
    row = candidate.iloc[0]
    assert row.outcome.startswith("entry_") and not row.closed
    assert row.launch_status == "entry_not_validated"
    assert row.launch_completed_close_count == 0 and pd.isna(row.launch_max_completed_close_r)


def test_missing_entry_and_empty_inputs_have_stable_opt_in_diagnostics_only():
    raw, mg, entries = fixture()
    row = run(raw[~raw.open_time.eq(START)], mg, entries).iloc[0]
    assert row.outcome == "entry_missing" and row.launch_status == "entry_not_validated"
    assert pd.isna(row.launch_max_completed_close_r)
    empty = run(raw, mg, entries.iloc[:0])
    assert empty.empty
    assert set(name for name in empty if name.startswith("launch_")) == set(name for name in row.index if name.startswith("launch_"))
    baseline_empty = run(raw, mg, entries.iloc[:0], launch=False)
    assert not any(name.startswith("launch_") for name in baseline_empty.columns)


@pytest.mark.parametrize("override", [
    {"launch_deadline_minutes": None}, {"launch_deadline_minutes": True},
    {"launch_deadline_minutes": np.bool_(True)}, {"launch_deadline_minutes": 60.0},
    {"launch_deadline_minutes": "60"}, {"launch_deadline_minutes": 55},
    {"launch_deadline_minutes": 65}, {"launch_deadline_minutes": np.nan},
    {"launch_progress_r": None}, {"launch_progress_r": True},
    {"launch_progress_r": "0.5"}, {"launch_progress_r": np.nan},
    {"launch_progress_r": np.inf}, {"launch_progress_r": 0},
    {"launch_progress_r": 0.6}, {"launch_progress_r": [0.5]},
    {"management_minutes": 15}, {"management_minutes": 60},
    {"confirmations": 2}, {"confirmations": np.bool_(True)}, {"decision_minutes": 15},
    {"exit_mode": "colour"}, {"exit_mode": "fixed_3r"},
    {"exit_mode": "partial_colour"}, {"exit_mode": "slope_colour"},
])
def test_opt_in_frozen_policy_rejects_other_modes_thresholds_and_bad_types(override):
    with pytest.raises(ValueError):
        run(*fixture(), **override)


@pytest.mark.parametrize("partial_policy", [{"launch_deadline_minutes": 60}, {"launch_progress_r": 0.5}])
def test_policy_keys_must_be_explicitly_supplied_together(partial_policy):
    with pytest.raises(ValueError):
        run(*fixture(), launch=False, **partial_policy)


def test_explicit_decision5_and_numpy_exact_numeric_inputs_match_default_candidate_all_fields():
    pd.testing.assert_frame_equal(run(*fixture()), run(*fixture(), decision_minutes=5,
                                  launch_deadline_minutes=np.int64(60), launch_progress_r=np.float64(0.5)))


def test_max_minutes_keeps_existing_precedence_over_inherited_max_hours():
    row = run(*fixture(), max_minutes=30, max_hours=72).iloc[0]
    assert row.outcome == "time_exit" and row.hold_minutes == 30
    assert row.launch_status == "prior_exit"


@pytest.mark.parametrize("direction", [1, -1])
def test_each_event_uses_its_own_frozen_entry_stop_r_not_atr_or_another_events_progress(direction):
    raw, mg, entries = fixture(direction)
    set_close(raw, START, 100+direction*3)
    second = entries.copy()
    second["event_id"] = "wider_risk"
    second["initial_stop"] = 100-direction*10
    second["signal_atr"] = 100.0
    entries["initial_stop"] = 100-direction*4
    entries["signal_atr"] = 0.25
    together = pd.concat([entries, second], ignore_index=True)
    output = run(raw, mg, together).set_index("event_id")
    assert output.loc["synthetic_launch", "outcome"] == "time_exit"
    assert output.loc["synthetic_launch", "launch_max_completed_close_r"] == 0.75
    assert output.loc["wider_risk", "outcome"] == "launch_timeout_exit"
    assert output.loc["wider_risk", "launch_max_completed_close_r"] == 0.3
    reordered = run(raw, mg, together.iloc[::-1]).set_index("event_id")
    pd.testing.assert_frame_equal(output.sort_index(), reordered.sort_index())


@pytest.mark.parametrize("invalid", [np.inf, -np.inf, np.nan])
def test_unknown_raw_source_segment_is_not_no_progress_evidence(invalid):
    raw, mg, entries = fixture()
    raw["segment_id"] = invalid
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "data_gap_censored" and not row.closed
    assert row.launch_status == "unknown_source"
    assert row.launch_completed_close_count == 0
    assert pd.isna(row.launch_max_completed_close_r) and pd.isna(row.net_return)
    # Explicitly retain the old engine's pre-existing inf-segment behaviour.
    if np.isinf(invalid):
        baseline = run(raw, mg, entries, launch=False).iloc[0]
        assert baseline.outcome == "time_exit" and baseline.closed


def test_input_frames_are_not_mutated():
    raw, mg, entries = fixture()
    originals = [frame.copy(deep=True) for frame in (raw, mg, entries)]
    run(raw, mg, entries)
    for actual, original in zip((raw, mg, entries), originals):
        pd.testing.assert_frame_equal(actual, original)
