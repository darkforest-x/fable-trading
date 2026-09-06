"""Synthetic-only frozen hourly MA structure exits and original-mode invariants.

The finite input domains exercise mirrored direction, every complete-close
slot before the fixed horizon, and exact/inclusive timing boundaries. No price
archive, study, historical outcome or runtime installation is used.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


START = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
BASE = {"exit_mode": "transition_colour", "management_minutes": 5, "confirmations": 1}


def fixture(direction=1, count=28):
    raw = pd.DataFrame({
        "open_time": pd.date_range(START-FIVE, periods=count, freq="5min"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1.0, "segment_id": 17,
    })
    mg = raw[["open_time", "high", "low", "close"]].copy()
    mg["ma"], mg["ma_side"], mg["ma_slope_atr"], mg["segment_id"] = 100.0, direction, np.nan, 3
    entries = pd.DataFrame([{
        "event_id": "synthetic_frozen_ma", "signal_time": START-pd.Timedelta(hours=1),
        "decision_time": START, "direction": direction, "ma": 100.0-direction,
        "initial_stop": 100.0-direction*10, "signal_atr": 2.0, "unchanged_feature": 0.61,
    }])
    return raw, mg, entries


def run(raw, mg, entries, *, frozen=True, cutoff=None, **policy):
    selected = dict(BASE, max_minutes=120)
    if frozen:
        selected["frozen_ma_exit"] = True
    selected.update(policy)
    return simulate_events(raw, mg, entries, selected, end_exclusive=cutoff)


def set_close(raw, at, price):
    idx = raw.index[raw.open_time.eq(at)][0]
    raw.loc[idx, "close"] = price
    raw.loc[idx, "high"] = max(raw.loc[idx, "open"], price)+1
    raw.loc[idx, "low"] = min(raw.loc[idx, "open"], price)-1


def wrong_close(raw, entries, at=START):
    price = entries.iloc[0].ma-entries.iloc[0].direction*0.25
    set_close(raw, at, price)
    return price


def original_columns_equal(candidate, baseline):
    assert not any(name.startswith("frozen_ma_") for name in baseline)
    pd.testing.assert_frame_equal(candidate.loc[:, baseline.columns], baseline)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("close_number", range(1, 24))
def test_each_held_wrong_side_close_latches_next_real_open_despite_rebound(direction, close_number):
    raw, mg, entries = fixture(direction)
    trigger_open = START+(close_number-1)*FIVE
    price = wrong_close(raw, entries, trigger_open)
    # The next opening price has already recovered to the original MA side.
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "frozen_ma_exit" and row.closed
    assert row.exit_time == START+close_number*FIVE and row.exit_price == 100.0
    assert row.hold_minutes == close_number*5 and row.hold_minutes > 0
    assert row.frozen_ma_boundary == entries.iloc[0].ma
    assert row.frozen_ma_available_at == row.entry_time == START
    assert row.frozen_ma_entry_distance_atr == 0.5
    assert row.frozen_ma_trigger_open_time == trigger_open
    assert row.frozen_ma_trigger_available_at == row.exit_time
    assert row.frozen_ma_trigger_close == price
    assert row.frozen_ma_completed_close_count == close_number
    assert row.frozen_ma_status == "structure_exit" and row.frozen_ma_enabled
    assert row.initial_stop == 100-direction*10 and row.risk_atr == 5.0 and row.risk_pct == 0.1
    assert row.gross_return == 0 and row.net_return == pytest.approx(-0.002)
    assert row.net_r == pytest.approx(-0.02)
    assert row.partial_fraction == 0 and row.exit_remaining_fraction == 1
    assert row.realised_partial_gross_return == 0 and not row.funding_modelled


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("signed_offset", [-1e-8, 0.0, 1e-8])
def test_close_relation_is_strict_wrong_side_equality_does_not_exit(direction, signed_offset):
    raw, mg, entries = fixture(direction)
    set_close(raw, START, entries.iloc[0].ma+direction*signed_offset)
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == ("frozen_ma_exit" if signed_offset < 0 else "time_exit")
    assert pd.notna(row.frozen_ma_trigger_available_at) == (signed_offset < 0)
    if signed_offset >= 0:
        original_columns_equal(run(raw, mg, entries), run(raw, mg, entries, frozen=False))


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("colour", ["aligned", "opposite", "unknown"])
def test_initial_wrong_ma_side_needs_first_postentry_close_not_an_adjacent_cross(direction, colour):
    raw, mg, entries = fixture(direction)
    entries["ma"] = 100.0+direction
    mg["ma_side"] = {"aligned": direction, "opposite": -direction, "unknown": np.nan}[colour]
    row = run(raw, mg, entries).iloc[0]
    assert row.transition_initial_state == colour
    assert row.frozen_ma_entry_distance_atr == -0.5
    assert row.outcome == "frozen_ma_exit" and row.exit_time == START+FIVE
    assert row.frozen_ma_trigger_open_time == START and row.frozen_ma_trigger_close == 100.0


@pytest.mark.parametrize("direction", [1, -1])
def test_entry_equal_to_boundary_does_not_trigger_without_later_wrong_close(direction):
    raw, mg, entries = fixture(direction)
    entries["ma"] = 100.0
    wrong_close(raw, entries, START+FIVE)
    row = run(raw, mg, entries).iloc[0]
    assert row.frozen_ma_entry_distance_atr == 0
    assert row.exit_time == START+2*FIVE and row.frozen_ma_completed_close_count == 2


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("source", ["preentry_close", "wick", "open", "management_close"])
def test_preentry_seed_wick_open_and_management_close_cannot_trigger(direction, source):
    raw, mg, entries = fixture(direction)
    price = entries.iloc[0].ma-direction*0.25
    if source == "preentry_close":
        set_close(raw, START-FIVE, price)
    elif source == "wick":
        raw["low" if direction == 1 else "high"] = price
    elif source == "open":
        idx = raw.index[raw.open_time.eq(START+FIVE)][0]
        raw.loc[idx, "open"] = price
        raw.loc[idx, "low" if direction == 1 else "high"] = price-direction
    else:
        mg["close"] = price
        mg["low" if direction == 1 else "high"] = price-direction
    candidate = run(raw, mg, entries)
    original_columns_equal(candidate, run(raw, mg, entries, frozen=False))
    row = candidate.iloc[0]
    assert row.outcome == "time_exit"
    assert row.frozen_ma_completed_close_count == 24
    assert pd.isna(row.frozen_ma_trigger_available_at) and pd.isna(row.frozen_ma_trigger_close)


@pytest.mark.parametrize("direction", [1, -1])
def test_later_dynamic_management_ma_does_not_move_frozen_hourly_boundary(direction):
    raw, mg, entries = fixture(direction)
    wrong_close(raw, entries, START+2*FIVE)
    before = run(raw, mg, entries)
    mg["ma"] = np.linspace(1.0, 1000.0, len(mg))
    after = run(raw, mg, entries)
    pd.testing.assert_frame_equal(before, after)
    assert after.iloc[0].frozen_ma_boundary == entries.iloc[0].ma


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("fault", ["missing_all", "missing_trigger", "side_nan", "ma_nan", "segment_change"])
def test_management_colour_reset_does_not_cancel_raw_close_structure_exit(direction, fault):
    raw, mg, entries = fixture(direction)
    wrong_close(raw, entries, START+FIVE)
    if fault == "missing_all":
        mg = mg.iloc[:0]
    elif fault == "missing_trigger":
        mg = mg.drop(index=2)
    elif fault == "side_nan":
        mg.loc[2, "ma_side"] = np.nan
    elif fault == "ma_nan":
        mg.loc[2, "ma"] = np.nan
    else:
        mg.loc[2:, "segment_id"] = 4
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "frozen_ma_exit" and row.exit_time == START+2*FIVE
    assert row.transition_reset_count > 0 and row.frozen_ma_completed_close_count == 2
    assert row.frozen_ma_trigger_open_time == START+FIVE


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("winner", ["intrabar_stop", "gap_stop", "colour", "horizon"])
def test_old_barrier_colour_and_duration_priority_preserve_all_original_fields(direction, winner):
    raw, mg, entries = fixture(direction)
    wrong_close(raw, entries)
    options = {}
    if winner == "intrabar_stop":
        raw.loc[1, "low" if direction == 1 else "high"] = 100-direction*11
    elif winner == "gap_stop":
        raw.loc[2, ["open", "high", "low", "close"]] = [100-direction*12, np.nan, np.nan, np.nan]
    elif winner == "colour":
        mg.loc[1, "ma_side"] = -direction
    else:
        options["max_minutes"] = 5
    candidate = run(raw, mg, entries, **options)
    original_columns_equal(candidate, run(raw, mg, entries, frozen=False, **options))
    row = candidate.iloc[0]
    assert row.outcome == {"intrabar_stop": "hard_stop", "gap_stop": "hard_stop_gap",
                           "colour": "transition_colour_exit", "horizon": "time_exit"}[winner]
    assert row.exit_time == START+FIVE and row.frozen_ma_status == "prior_exit"
    assert pd.notna(row.frozen_ma_trigger_available_at) == (winner != "intrabar_stop")
    assert row.frozen_ma_completed_close_count == (0 if winner == "intrabar_stop" else 1)


@pytest.mark.parametrize("direction", [1, -1])
def test_default_72h_horizon_wins_over_new_trigger_at_exact_deadline(direction):
    raw, mg, entries = fixture(direction, count=870)
    deadline = START+pd.Timedelta(hours=72)
    wrong_close(raw, entries, deadline-FIVE)
    candidate = simulate_events(raw, mg, entries, dict(BASE, frozen_ma_exit=True))
    original_columns_equal(candidate, simulate_events(raw, mg, entries, BASE))
    row = candidate.iloc[0]
    assert row.outcome == "time_exit" and row.exit_time == deadline
    assert row.frozen_ma_trigger_available_at == deadline
    assert row.frozen_ma_completed_close_count == 864


@pytest.mark.parametrize("fault", ["missing_bar", "off_grid", "segment_change", "segment_nan", "segment_inf", "open_nan", "open_zero"])
def test_pending_structure_exit_censors_on_missing_source_clock_or_next_open(fault):
    raw, mg, entries = fixture()
    wrong_close(raw, entries)
    if fault == "missing_bar":
        raw = raw.drop(index=2)
    elif fault == "off_grid":
        raw.loc[2, "open_time"] += pd.Timedelta(minutes=1)
    elif fault.startswith("segment_"):
        raw["segment_id"] = raw.segment_id.astype(float)
        raw.loc[2:, "segment_id"] = {"segment_change": 18, "segment_nan": np.nan, "segment_inf": np.inf}[fault]
    else:
        raw.loc[2, "open"] = np.nan if fault == "open_nan" else 0
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "data_gap_censored" and not row.closed
    assert row.frozen_ma_status == "unknown_source" and row.exit_time == START+FIVE
    assert row.frozen_ma_trigger_available_at == START+FIVE
    assert pd.isna(row.net_return) and pd.isna(row.net_r)
    assert row.frozen_ma_completed_close_count == 1


@pytest.mark.parametrize("column,value", [
    ("high", np.nan), ("low", np.nan), ("close", np.nan),
    ("high", np.inf), ("low", 101.0), ("close", 0.0),
])
def test_invalid_trigger_bar_ohlc_is_unknown_not_a_structure_signal(column, value):
    raw, mg, entries = fixture()
    wrong_close(raw, entries)
    raw.loc[1, column] = value
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "data_gap_censored" and not row.closed
    assert row.frozen_ma_status == "unknown_source"
    assert pd.isna(row.frozen_ma_trigger_available_at) and pd.isna(row.net_return)
    assert row.frozen_ma_completed_close_count == 0


@pytest.mark.parametrize("direction", [1, -1])
def test_next_open_fill_never_reads_unfinished_exit_bar_hlc_or_later_suffix(direction):
    raw, mg, entries = fixture(direction)
    wrong_close(raw, entries, START+2*FIVE)
    before = run(raw, mg, entries)
    at_exit = START+3*FIVE
    raw.loc[raw.open_time.ge(at_exit), ["high", "low", "close"]] = np.nan
    raw.loc[raw.open_time.gt(at_exit), "open"] = np.nan
    mg.loc[mg.open_time.ge(at_exit), ["ma", "ma_side", "high", "low", "close"]] = np.nan
    after = run(raw, mg, entries)
    pd.testing.assert_frame_equal(before, after)
    pd.testing.assert_frame_equal(after, run(raw[raw.open_time.le(at_exit)], mg[mg.open_time.lt(at_exit)], entries))


@pytest.mark.parametrize("cutoff_minutes,triggered,closes", [(3, False, 0), (5, True, 1)])
def test_exclusive_cutoff_does_not_disclose_unfinished_close_or_supply_next_open(cutoff_minutes, triggered, closes):
    raw, mg, entries = fixture()
    wrong_close(raw, entries)
    row = run(raw, mg, entries, cutoff=START+pd.Timedelta(minutes=cutoff_minutes)).iloc[0]
    assert row.outcome == "right_censored" and not row.closed
    assert row.frozen_ma_status == "unknown_source" and pd.isna(row.net_return)
    assert pd.notna(row.frozen_ma_trigger_available_at) is triggered
    assert row.frozen_ma_completed_close_count == closes


def test_physical_end_after_trigger_without_next_open_stays_unknown():
    raw, mg, entries = fixture(count=2)
    wrong_close(raw, entries)
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "right_censored" and not row.closed
    assert row.frozen_ma_trigger_available_at == START+FIVE
    assert row.frozen_ma_completed_close_count == 1 and pd.isna(row.net_return)


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_unknown_initial_source_segment_cannot_support_structure_exit(invalid):
    raw, mg, entries = fixture()
    raw["segment_id"] = invalid
    row = run(raw, mg, entries).iloc[0]
    assert row.outcome == "data_gap_censored" and not row.closed
    assert row.frozen_ma_completed_close_count == 0 and pd.isna(row.frozen_ma_trigger_available_at)
    if np.isinf(invalid):
        old = run(raw, mg, entries, frozen=False).iloc[0]
        assert old.outcome == "time_exit" and old.closed  # Deliberate old-mode parity.


@pytest.mark.parametrize("field,value", [("ma", np.nan), ("ma", np.inf), ("ma", -np.inf),
    ("ma", 0), ("ma", -1), ("ma", True), ("ma", np.bool_(True)),
    ("ma", "99"), ("ma", None), ("ma", 10**400),
    ("signal_time", pd.NaT), ("signal_time", None), ("signal_time", "invalid"),
    ("signal_time", 0), ("signal_time", True),
    ("signal_time", START), ("signal_time", START-pd.Timedelta(minutes=55)),
    ("decision_time", START+pd.Timedelta(minutes=5)), ("decision_time", pd.NaT),
    ("decision_time", START.value), ("decision_time", True)])
def test_invalid_frozen_hourly_metadata_fails_entire_request_set_before_any_entry(field, value):
    raw, mg, entries = fixture()
    together = pd.concat([entries, entries], ignore_index=True)
    together.loc[1, "event_id"] = "invalid_second"
    together[field] = together[field].astype(object)
    together.at[1, field] = value
    with pytest.raises(ValueError):
        run(raw, mg, together)


@pytest.mark.parametrize("missing", ["ma", "signal_time"])
@pytest.mark.parametrize("empty", [False, True])
def test_opt_in_metadata_columns_required_even_for_empty_request_schema(missing, empty):
    raw, mg, entries = fixture()
    if empty:
        entries = entries.iloc[:0]
    with pytest.raises(ValueError, match="missing columns"):
        run(raw, mg, entries.drop(columns=missing))


@pytest.mark.parametrize("value", [False, np.bool_(False), 0, 1, 1.0, "True", "false", None, np.nan, [], {}])
def test_option_presence_requires_actual_boolean_true(value):
    with pytest.raises(ValueError):
        run(*fixture(), frozen_ma_exit=value)


@pytest.mark.parametrize("options", [
    {"management_minutes": 15}, {"management_minutes": 60}, {"confirmations": 2},
    {"confirmations": True}, {"confirmations": np.bool_(True)}, {"decision_minutes": 15},
    {"exit_mode": "colour"}, {"exit_mode": "partial_colour"}, {"exit_mode": "fixed_3r"},
    {"exit_mode": "slope_colour"}, {"exit_mode": "hour_colour"},
    {"launch_deadline_minutes": 60, "launch_progress_r": 0.5},
    {"launch_deadline_minutes": 60}, {"launch_progress_r": 0.5},
])
def test_unsupported_clocks_modes_and_launch_composition_are_rejected(options):
    with pytest.raises(ValueError):
        run(*fixture(), **options)


def test_boolean_numpy_true_and_explicit_decision5_are_exact_candidate_aliases():
    raw, mg, entries = fixture()
    wrong_close(raw, entries)
    pd.testing.assert_frame_equal(run(raw, mg, entries), run(raw, mg, entries,
                                  frozen_ma_exit=np.bool_(True), decision_minutes=5))


def test_timezone_equivalent_completed_hour_metadata_is_accepted():
    raw, mg, entries = fixture()
    entries["signal_time"] = "2024-01-01T08:00:00+08:00"
    entries["decision_time"] = "2024-01-01T09:00:00+08:00"
    wrong_close(raw, entries)
    row = run(raw, mg, entries).iloc[0]
    assert row.frozen_ma_available_at == START and row.exit_time == START+FIVE


@pytest.mark.parametrize("fault", ["missing_entry", "invalid_open", "invalid_risk", "invalid_atr", "invalid_direction"])
def test_existing_entry_failure_semantics_are_preserved_after_valid_frozen_metadata(fault):
    raw, mg, entries = fixture()
    if fault == "missing_entry":
        raw = raw.drop(index=1)
    elif fault == "invalid_open":
        raw.loc[1, "open"] = np.nan
    elif fault == "invalid_risk":
        entries["initial_stop"] = 100.0
    elif fault == "invalid_atr":
        entries["signal_atr"] = 0.0
    else:
        entries["direction"] = 0.0
    candidate = run(raw, mg, entries)
    original_columns_equal(candidate, run(raw, mg, entries, frozen=False))
    row = candidate.iloc[0]
    assert row.outcome.startswith("entry_") and not row.closed
    assert row.frozen_ma_status == "entry_not_validated" and row.frozen_ma_completed_close_count == 0
    assert pd.isna(row.frozen_ma_entry_distance_atr) and pd.isna(row.frozen_ma_trigger_close)


def test_empty_candidate_has_fixed_diagnostic_schema_and_old_empty_has_no_new_fields():
    raw, mg, entries = fixture()
    full = run(raw, mg, entries)
    empty = run(raw, mg, entries.iloc[:0])
    assert empty.empty
    assert {key for key in empty if key.startswith("frozen_ma_")} == {key for key in full if key.startswith("frozen_ma_")}
    original = run(raw, mg, entries.iloc[:0].drop(columns=["ma", "signal_time"]), frozen=False)
    assert not any(key.startswith("frozen_ma_") for key in original)


@pytest.mark.parametrize("mode,minutes", [("colour", 5), ("colour", 15), ("hour_colour", 60),
    ("slope_colour", 15), ("partial_colour", 15), ("fixed_3r", 5),
    ("transition_colour", 5), ("transition_colour", 15)])
def test_old_modes_do_not_validate_unused_frozen_metadata_or_add_new_columns(mode, minutes):
    raw, mg, entries = fixture()
    entries["ma"], entries["signal_time"] = np.nan, "not_a_timestamp"
    row = run(raw, mg, entries, frozen=False, exit_mode=mode, management_minutes=minutes).iloc[0]
    assert row.outcome == "time_exit" and row.closed
    assert not any(key.startswith("frozen_ma_") for key in row.index)


@pytest.mark.parametrize("direction", [1, -1])
def test_frozen_boundary_is_per_event_not_shared_and_result_is_order_invariant(direction):
    raw, mg, entries = fixture(direction)
    wrong_close(raw, entries, START+FIVE)
    other = entries.copy()
    other["event_id"] = "different_hourly_ma"
    other["ma"] = 100-direction*4
    other["signal_atr"] = 4.0
    together = pd.concat([entries, other], ignore_index=True)
    results = run(raw, mg, together).set_index("event_id")
    assert results.loc["synthetic_frozen_ma", "outcome"] == "frozen_ma_exit"
    assert results.loc["different_hourly_ma", "outcome"] == "time_exit"
    assert results.loc["different_hourly_ma", "frozen_ma_entry_distance_atr"] == 1.0
    reversed_results = run(raw, mg, together.iloc[::-1]).set_index("event_id")
    pd.testing.assert_frame_equal(results.sort_index(), reversed_results.sort_index())


def test_input_frames_are_unchanged():
    frames = fixture()
    originals = [frame.copy(deep=True) for frame in frames]
    run(*frames)
    for actual, original in zip(frames, originals):
        pd.testing.assert_frame_equal(actual, original)
