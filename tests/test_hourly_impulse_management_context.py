"""Synthetic independent 5m/15m entry context checks; no price/outcome files."""
import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features, resample_complete
from yoyo.data.hourly_impulse_management_context import CONTEXT_COLUMNS, attach_management_context


START = pd.Timestamp("2024-01-01T01:00:00Z")


def frames(minutes=15, phase=0, direction=1):
    raw = pd.DataFrame({
        "open_time": pd.date_range(START-pd.Timedelta(minutes=30), periods=16, freq="5min"),
        "open": 100., "high": 101., "low": 99., "close": 100., "segment_id": "raw_segment",
    })
    mg = pd.DataFrame({
        "open_time": pd.date_range(START-pd.Timedelta(minutes=30), periods=8, freq=f"{minutes}min"),
        "ma": 100., "ma_side": 1., "high": 101., "low": 99., "close": 100.,
        "segment_id": "separate_management_counter", "ma_slope_atr": np.nan,
    })
    mg.attrs["bar_minutes"] = minutes
    entries = pd.DataFrame({"event_id": ["a"], "decision_time": [START+pd.Timedelta(minutes=phase)],
                            "direction": [direction], "initial_stop": [90.],
                            "signal_atr": [2.], "ltf_entry_state": ["old_5m_state"],
                            "known_5m_colour": [-1]})
    return raw, mg, entries


def result(*, minutes=15, phase=0, direction=1, raw=None, mg=None, entries=None):
    default_raw, default_mg, default_entries = frames(minutes, phase, direction)
    return attach_management_context(default_raw if raw is None else raw,
                                     default_mg if mg is None else mg,
                                     default_entries if entries is None else entries, minutes)


@pytest.mark.parametrize("minutes,phase", [(5, 0), (5, 5), (5, 10), (15, 0), (15, 5), (15, 10)])
@pytest.mark.parametrize("direction", [1, -1])
def test_exact_native_close_floor_at_each_phase_and_direction(minutes, phase, direction):
    row = result(minutes=minutes, phase=phase, direction=direction).iloc[0]
    boundary = (START+pd.Timedelta(minutes=phase)).floor(f"{minutes}min")
    assert row.mg_entry_bar_open == boundary-pd.Timedelta(minutes=minutes)
    assert row.mg_entry_available_at == boundary
    assert row.mg_entry_available_at <= row.decision_time
    assert row.mg_entry_side == 1
    assert row.mg_entry_aligned == (direction == 1)
    assert row.mg_entry_state == ("aligned" if direction == 1 else "opposite")
    assert row.mg_entry_reason == "valid"


@pytest.mark.parametrize("phase", [0, 5, 10])
def test_incomplete_management_colour_does_not_replace_latest_completed_colour(phase):
    raw, mg, entries = frames(phase=phase)
    mg.loc[mg.open_time.eq(START), "ma_side"] = -1
    assert result(phase=phase, raw=raw, mg=mg, entries=entries).iloc[0].mg_entry_side == 1


@pytest.mark.parametrize("problem", ["absent", "invalid", "off_grid"])
def test_missing_or_invalid_newest_native_bar_never_falls_back_to_older_colour(problem):
    raw, mg, entries = frames()
    position = mg.index[mg.open_time.eq(START-pd.Timedelta(minutes=15))][0]
    if problem == "absent":
        mg = mg.drop(index=position)
    elif problem == "off_grid":
        mg.loc[position, "open_time"] += pd.Timedelta(minutes=5)
    else:
        mg.loc[position, "ma_side"] = 0
    row = result(raw=raw, mg=mg, entries=entries).iloc[0]
    assert row.mg_entry_state == "unknown"
    assert pd.isna(row.mg_entry_side) and pd.isna(row.mg_entry_aligned)


@pytest.mark.parametrize("source_phase", [-15, -10, -5, 0, 5, 10])
def test_every_source_subbar_through_phase_offset_entry_is_required(source_phase):
    raw, mg, entries = frames(phase=10)
    raw = raw.loc[raw.open_time.ne(START+pd.Timedelta(minutes=source_phase))]
    row = result(phase=10, raw=raw, mg=mg, entries=entries).iloc[0]
    assert row.mg_entry_state == "unknown"
    assert row.mg_entry_reason == "missing_source"


@pytest.mark.parametrize("source_phase", [-15, -10, -5, 0, 5, 10])
def test_intermediate_segment_switch_cannot_be_hidden_by_equal_endpoints(source_phase):
    raw, mg, entries = frames(phase=10)
    raw.loc[raw.open_time.eq(START+pd.Timedelta(minutes=source_phase)), "segment_id"] = "gap"
    row = result(phase=10, raw=raw, mg=mg, entries=entries).iloc[0]
    assert row.mg_entry_reason == "source_segment_change"


@pytest.mark.parametrize("source_phase", [-15, -10, -5, 0, 5])
@pytest.mark.parametrize("field,value", [("high", np.nan), ("low", 102.), ("open", 0.)])
def test_completed_source_ohlc_is_validated_including_since_native_close(source_phase, field, value):
    raw, mg, entries = frames(phase=10)
    raw.loc[raw.open_time.eq(START+pd.Timedelta(minutes=source_phase)), field] = value
    row = result(phase=10, raw=raw, mg=mg, entries=entries).iloc[0]
    assert row.mg_entry_reason == "invalid_completed_source"


@pytest.mark.parametrize("phase", [0, 5, 10])
def test_entry_open_is_required_but_entry_hlc_and_future_are_not_observable(phase):
    raw, mg, entries = frames(phase=phase)
    before = result(phase=phase, raw=raw, mg=mg, entries=entries)
    boundary = entries.decision_time.iloc[0]
    raw.loc[raw.open_time.ge(boundary), ["high", "low", "close"]] = np.nan
    raw.loc[raw.open_time.gt(boundary), "open"] = np.inf
    mg.loc[mg.open_time.ge(boundary.floor("15min")), ["ma", "ma_side", "high", "low", "close"]] = np.nan
    after = result(phase=phase, raw=raw, mg=mg, entries=entries)
    pd.testing.assert_frame_equal(before, after)
    raw.loc[raw.open_time.eq(boundary), "open"] = np.nan
    assert result(phase=phase, raw=raw, mg=mg, entries=entries).iloc[0].mg_entry_reason == "invalid_source_open"


@pytest.mark.parametrize("minutes,phase", [(5, 0), (5, 5), (15, 0), (15, 5), (15, 10)])
def test_complete_prefix_with_only_entry_open_matches_full_suffix(minutes, phase):
    raw, mg, entries = frames(minutes, phase)
    before = result(minutes=minutes, raw=raw, mg=mg, entries=entries)
    boundary = entries.decision_time.iloc[0]
    after = result(minutes=minutes, raw=raw.loc[raw.open_time.le(boundary)],
                   mg=mg.loc[(mg.open_time+pd.Timedelta(minutes=minutes)).le(boundary)], entries=entries)
    pd.testing.assert_frame_equal(before, after)


@pytest.mark.parametrize("field,value,reason", [
    ("ma", np.nan, "nonfinite_management"), ("ma", -1., "invalid_management"),
    ("ma_side", 0., "invalid_management"), ("ma_side", np.inf, "nonfinite_management"),
    ("high", np.nan, "nonfinite_management"), ("low", 102., "invalid_management"),
    ("close", 103., "invalid_management"), ("segment_id", None, "unknown_management_segment"),
    ("segment_id", np.inf, "unknown_management_segment"),
])
def test_bad_native_colour_geometry_or_segment_is_unknown(field, value, reason):
    raw, mg, entries = frames()
    mg.loc[mg.open_time.eq(START-pd.Timedelta(minutes=15)), field] = value
    assert result(raw=raw, mg=mg, entries=entries).iloc[0].mg_entry_reason == reason


def test_ma_equality_uses_positive_precomputed_native_colour_not_body_direction():
    raw = pd.DataFrame({"open_time": pd.date_range(START-pd.Timedelta(hours=12), periods=145, freq="5min"),
                        "open": 100.2, "high": 101., "low": 99., "close": 99.8, "volume": 10.})
    five = resample_complete(raw, 5)
    mg = add_features(resample_complete(raw, 15), "SMA", 40)
    _, _, entries = frames()
    row = attach_management_context(five, mg, entries, 15).iloc[0]
    assert row.mg_entry_state == "aligned"
    selected = mg.loc[mg.open_time.eq(row.mg_entry_bar_open)].iloc[0]
    assert selected.hl2 == selected.ma and selected.close < selected.open


def test_opaque_segment_spaces_and_old_5m_fields_attrs_order_survive_unchanged():
    raw, mg, entries = frames()
    entries = pd.concat([entries, entries.assign(direction=-1), entries], ignore_index=True)
    entries.index = pd.Index([8, 3, 3], name="owner_index")
    entries.attrs["origin"] = "frozen_v7_requests"
    before = entries.copy(deep=True)
    actual = result(raw=raw, mg=mg, entries=entries)
    pd.testing.assert_frame_equal(actual[entries.columns], before)
    pd.testing.assert_frame_equal(entries, before)
    assert actual.attrs == entries.attrs
    assert actual.mg_entry_state.tolist() == ["aligned", "opposite", "aligned"]
    assert actual.ltf_entry_state.tolist() == ["old_5m_state"] * 3
    assert actual.known_5m_colour.tolist() == [-1] * 3


def test_timezone_equivalence_and_nullable_empty_schema():
    raw, mg, entries = frames(phase=5)
    before = result(raw=raw, mg=mg, entries=entries)
    for frame, name in ((raw, "open_time"), (mg, "open_time"), (entries, "decision_time")):
        frame[name] = frame[name].dt.tz_convert("Asia/Shanghai")
    actual = result(raw=raw, mg=mg, entries=entries)
    pd.testing.assert_frame_equal(before[CONTEXT_COLUMNS], actual[CONTEXT_COLUMNS])
    empty = result(raw=raw, mg=mg, entries=entries.iloc[:0])
    assert empty.columns.tolist() == entries.columns.tolist() + CONTEXT_COLUMNS
    assert str(empty.mg_entry_side.dtype) == "Int64"
    assert str(empty.mg_entry_aligned.dtype) == "boolean"
    assert str(empty.mg_entry_available_at.dtype) == "datetime64[ns, UTC]"


@pytest.mark.parametrize("which", ["raw", "management"])
@pytest.mark.parametrize("problem", ["duplicate", "unsorted", "numeric", "missing_column"])
def test_malformed_source_schema_and_timestamps_are_rejected(which, problem):
    raw, mg, entries = frames()
    target = raw if which == "raw" else mg
    if problem == "duplicate":
        target.loc[1, "open_time"] = target.loc[0, "open_time"]
    elif problem == "unsorted":
        target.loc[0, "open_time"] = target.loc[2, "open_time"]+pd.Timedelta(minutes=1)
    elif problem == "numeric":
        target["open_time"] = range(len(target))
    else:
        target.drop(columns="high", inplace=True)
    with pytest.raises(ValueError):
        result(raw=raw, mg=mg, entries=entries)


@pytest.mark.parametrize("minutes", [True, np.bool_(True), 1, 10, 60, 15.0, "15"])
def test_unsupported_or_ambiguous_management_interval_is_rejected(minutes):
    raw, mg, entries = frames()
    with pytest.raises(ValueError):
        attach_management_context(raw, mg, entries, minutes)


def test_conflicting_frame_interval_and_existing_context_are_rejected():
    raw, mg, entries = frames()
    mg.attrs["bar_minutes"] = 5
    with pytest.raises(ValueError):
        result(raw=raw, mg=mg, entries=entries)
    mg.attrs["bar_minutes"] = 15
    entries["mg_entry_state"] = "do not overwrite"
    with pytest.raises(ValueError):
        result(raw=raw, mg=mg, entries=entries)


@pytest.mark.parametrize("time,reason", [(None, "invalid_entry_time"), (START+pd.Timedelta(minutes=1), "unaligned_entry_time")])
def test_invalid_request_time_keeps_original_row_with_unknown(time, reason):
    raw, mg, entries = frames()
    entries.loc[0, "decision_time"] = time
    actual = result(raw=raw, mg=mg, entries=entries)
    assert len(actual) == 1 and actual.iloc[0].mg_entry_reason == reason
    assert pd.isna(actual.iloc[0].mg_entry_aligned)


def test_no_initial_risk_filter_changes_frozen_request_membership():
    raw, mg, entries = frames()
    entries["initial_stop"] = 101.  # Deliberately invalid long risk, for L3 to reject.
    actual = result(raw=raw, mg=mg, entries=entries)
    assert len(actual) == 1 and actual.iloc[0].initial_stop == 101.
    assert actual.iloc[0].mg_entry_state == "aligned"


@pytest.mark.parametrize("minutes,phase", [(5, 0), (5, 5), (15, 0), (15, 5), (15, 10)])
@pytest.mark.parametrize("side", [1., -1., 0., np.nan])
def test_independent_context_matches_l3_initial_state_on_all_valid_entry_phases(minutes, phase, side):
    # Independent implementations meet only here, on synthetic input. No layer
    # is imported by the data helper and no real study/source loader is called.
    from yoyo.layers.l3_backtest.hourly_impulse import simulate_events

    raw, mg, entries = frames(minutes, phase)
    native_open = entries.decision_time.iloc[0].floor(f"{minutes}min")-pd.Timedelta(minutes=minutes)
    mg.loc[mg.open_time.eq(native_open), "ma_side"] = side
    context = attach_management_context(raw, mg, entries, minutes).iloc[0]
    replay = simulate_events(raw, mg, entries,
                             {"exit_mode": "transition_colour", "management_minutes": minutes},
                             end_exclusive=entries.decision_time.iloc[0]+pd.Timedelta(minutes=1)).iloc[0]
    assert context.mg_entry_state == replay.transition_initial_state
    assert context.mg_entry_reason == replay.transition_initial_reason
    if context.mg_entry_state != "unknown":
        assert context.mg_entry_side == replay.transition_initial_side
        assert context.mg_entry_bar_open == replay.transition_initial_open_time


@pytest.mark.parametrize("problem", ["missing_inner", "segment_inner", "bad_inner_ohlc"])
def test_l3_initial_unknown_parity_includes_completed_bars_since_native_close(problem):
    from yoyo.layers.l3_backtest.hourly_impulse import simulate_events

    raw, mg, entries = frames(phase=10)
    affected = raw.open_time.eq(START+pd.Timedelta(minutes=5))
    if problem == "missing_inner":
        raw = raw.loc[~affected]
    elif problem == "segment_inner":
        raw.loc[affected, "segment_id"] = "intervening_gap"
    else:
        raw.loc[affected, "high"] = np.nan
    context = attach_management_context(raw, mg, entries, 15).iloc[0]
    replay = simulate_events(raw, mg, entries,
                             {"exit_mode": "transition_colour", "management_minutes": 15},
                             end_exclusive=entries.decision_time.iloc[0]+pd.Timedelta(minutes=1)).iloc[0]
    assert context.mg_entry_state == replay.transition_initial_state == "unknown"
    assert context.mg_entry_reason == replay.transition_initial_reason


def test_missing_native_bucket_restarts_sma_warmup_not_old_colour_forward_fill():
    raw = pd.DataFrame({"open_time": pd.date_range(START-pd.Timedelta(hours=12), periods=145, freq="5min"),
                        "open": 100.2, "high": 101., "low": 99., "close": 99.8, "volume": 10.})
    raw = raw.loc[raw.open_time.ne(START-pd.Timedelta(minutes=40))]
    five = resample_complete(raw, 5)
    mg = add_features(resample_complete(raw, 15), "SMA", 40)
    _, _, entries = frames()
    row = attach_management_context(five, mg, entries, 15).iloc[0]
    assert row.mg_entry_state == "unknown"
    assert row.mg_entry_reason == "nonfinite_management"


@pytest.mark.parametrize("empty", ["raw", "management"])
def test_empty_source_has_explicit_unknown_not_dropped_request(empty):
    raw, mg, entries = frames()
    actual = result(raw=raw.iloc[:0] if empty == "raw" else raw,
                     mg=mg.iloc[:0] if empty == "management" else mg, entries=entries)
    assert len(actual) == 1 and actual.iloc[0].mg_entry_state == "unknown"
    assert actual.iloc[0].mg_entry_reason == ("missing_source" if empty == "raw" else "missing_management")
