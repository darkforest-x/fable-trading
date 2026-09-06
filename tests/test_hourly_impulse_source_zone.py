"""Synthetic, fixed-source-family geometry and causal episode invariants."""

from itertools import product

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features
from yoyo.data.hourly_impulse_source_zone import (
    SOURCE_ENTRY_COLUMNS, ZONE_COLUMNS, build_source_zone_requests,
)


START = pd.Timestamp("2024-01-01", tz="UTC")
HOUR = pd.Timedelta(hours=1)


def raw_fixture(hours=50):
    times = pd.date_range(START-50*HOUR, periods=50+hours, freq="1h")
    raw = pd.DataFrame({"open_time": times, "open": 100.0, "high": 101.0,
                        "low": 99.0, "close": 100.0, "volume": 10.0, "segment_id": 7})
    raw.attrs["bar_minutes"] = 60
    return raw


def bar(raw, hour, values):
    raw.loc[raw.open_time.eq(START+hour*HOUR), ["open", "high", "low", "close"]] = values


def contract(raw, hour=0):
    for offset in range(4): bar(raw, hour+offset, [100, 105, 95, 100])
    for offset in range(4, 8): bar(raw, hour+offset, [100, 102, 98, 100])


def featured(*, direction=1, release=8, hours=50):
    raw = raw_fixture(hours)
    contract(raw)
    if release is not None: bar(raw, release, [100, 106, 99.8, 105.8])
    if direction == -1:
        raw["open"], raw["close"] = 200-raw.open, 200-raw.close
        old_h = raw.high.copy()
        raw["high"], raw["low"] = 200-raw.low, 200-old_h
    return add_features(raw)


def run(frame, cutoff=START+30*HOUR, *, start=START, end=START+240*HOUR):
    return build_source_zone_requests(frame, fold="2024H1", start=start,
                                     end_exclusive=end, observed_through=cutoff)


@pytest.mark.parametrize("direction", [1, -1])
def test_first_release_body_crosses_fixed_bounds_and_mirror(direction):
    frame = featured(direction=direction)
    entries, zones = run(frame, START+9*HOUR)
    assert len(entries) == len(zones) == 1
    entry, zone = entries.iloc[0], zones.iloc[0]
    assert entry.direction == direction
    assert entry.signal_time == START+8*HOUR
    assert entry.decision_time == START+9*HOUR
    assert entry.initial_stop == (99.8 if direction == 1 else 100.2)
    assert entry.event_id == (START+8*HOUR).isoformat()+("_L" if direction == 1 else "_S")
    assert zone.zone_id == (START+8*HOUR).isoformat()+"_ZONE"
    assert zone.status == "request_emitted" and zone.event_id == entry.event_id
    assert zone.zone_lower == 98 and zone.zone_upper == 102
    assert zone.source_start == START and zone.source_end == START+7*HOUR
    assert zone.zone_arm_time == START+8*HOUR and zone.zone_deadline == START+16*HOUR
    assert zone.release_time == START+8*HOUR and zone.terminal_time == START+9*HOUR
    assert entry.release_wait_hours == 1
    assert entries.columns.tolist() == SOURCE_ENTRY_COLUMNS
    assert zones.columns.tolist() == ZONE_COLUMNS


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("ma", [np.nan, 1000.0, 1.0])
def test_hourly_ma_colour_crossing_availability_and_slope_are_not_gates(direction, ma):
    frame = featured(direction=direction)
    frame["ma"], frame["ma_side"], frame["ma_slope_atr"] = ma, -direction, -direction*20.0
    entries, _ = run(frame, START+9*HOUR)
    assert len(entries) == 1
    assert entries.ma_side.iloc[0] == -direction
    assert entries.ma_slope_atr.iloc[0] == -20
    if np.isnan(ma): assert pd.isna(entries.extension_atr.iloc[0])
    else: assert entries.ma.iloc[0] == ma


@pytest.mark.parametrize("equal_edge", ["low", "high", "both"])
def test_source_requires_strict_containment_of_both_envelope_edges(equal_edge):
    raw = raw_fixture(); contract(raw)
    if equal_edge in ("low", "both"): raw.loc[raw.open_time.eq(START+6*HOUR), "low"] = 95
    if equal_edge in ("high", "both"): raw.loc[raw.open_time.eq(START+7*HOUR), "high"] = 105
    entries, zones = run(add_features(raw), START+8*HOUR)
    assert entries.empty and zones.empty


def test_source_is_four_bar_envelopes_not_individual_candle_nesting():
    raw = raw_fixture(); contract(raw)
    for hour in (1, 2, 3): bar(raw, hour, [100, 100.5, 99.5, 100])
    entries, zones = run(add_features(raw), START+8*HOUR)
    assert entries.empty and len(zones) == 1
    assert zones.status.iloc[0] == "censored_source_end"


@pytest.mark.parametrize("first", [[104, 108, 103, 107.9], [101.9, 103, 101.8, 102.1]])
def test_false_first_release_consumes_cannot_select_later_strong_release(first):
    raw = raw_fixture(); contract(raw)
    bar(raw, 8, first); bar(raw, 9, [100, 120, 99, 119])
    entries, zones = run(add_features(raw), START+10*HOUR)
    assert entries.empty and len(zones) == 1
    assert zones.status.iloc[0] == "first_release_unqualified"
    assert zones.release_time.iloc[0] == START+8*HOUR
    assert zones.direction.iloc[0] == 1
    assert pd.isna(zones.event_id.iloc[0])


@pytest.mark.parametrize("open_price", [102, 103])
def test_strict_body_cross_rejects_open_equal_to_or_outside_boundary(open_price):
    raw = raw_fixture(); contract(raw)
    bar(raw, 8, [open_price, 110, 101.5, 109.9])
    entries, zones = run(add_features(raw), START+9*HOUR)
    assert entries.empty and zones.status.iloc[0] == "first_release_unqualified"
    assert zones.reason.iloc[0] == "release_body_does_not_strictly_cross_boundary"


@pytest.mark.parametrize("first_close", [100, 102, 98])
def test_equal_close_or_wick_only_is_not_release(first_close):
    raw = raw_fixture(); contract(raw)
    bar(raw, 8, [100, 105, 95, first_close]); bar(raw, 9, [100, 106, 99.8, 105.8])
    entries, zones = run(add_features(raw), START+10*HOUR)
    assert entries.release_wait_hours.tolist() == [2]
    assert zones.release_time.tolist() == [START+9*HOUR]
    assert zones.zone_lower.iloc[0] == 98 and zones.zone_upper.iloc[0] == 102


@pytest.mark.parametrize("direction", [1, -1])
def test_true_engulf_branch_uses_real_previous_body_and_smaller_atr_minimum(direction):
    raw = raw_fixture(); contract(raw)
    bar(raw, 7, [101, 102, 98, 99.5])
    bar(raw, 8, [99, 105, 97, 103])
    if direction == -1:
        raw["open"], raw["close"] = 200-raw.open, 200-raw.close
        old_h = raw.high.copy(); raw["high"], raw["low"] = 200-raw.low, 200-old_h
    frame = add_features(raw)
    frame.loc[frame.open_time.eq(START+8*HOUR), "atr"] = 10.0
    frame["ma_side"] = -direction
    entries, _ = run(frame, START+9*HOUR)
    assert len(entries) == 1 and entries.is_engulf.iloc[0]
    assert entries.body_ratio.iloc[0] == .5 and entries.range_atr.iloc[0] == .8


def test_fake_engulf_feature_does_not_replace_real_body_semantics():
    raw = raw_fixture(); contract(raw)
    bar(raw, 8, [100.5, 105, 96.5, 103])
    frame = add_features(raw); frame["bullish_engulf"] = True
    frame.loc[frame.open_time.eq(START+8*HOUR), "atr"] = 10
    entries, zones = run(frame, START+9*HOUR)
    assert entries.empty and zones.status.iloc[0] == "first_release_unqualified"


def test_large_shape_accepts_exact_body_range_and_location_thresholds():
    raw = raw_fixture(); contract(raw)
    bar(raw, 8, [99.5, 109, 99, 106])
    frame = add_features(raw)
    frame.loc[frame.open_time.eq(START+8*HOUR), "atr"] = 10
    entries, _ = run(frame, START+9*HOUR)
    assert len(entries) == 1
    assert entries.iloc[0][["body_ratio", "range_atr", "close_location"]].tolist() == [.65, 1.0, .70]


@pytest.mark.parametrize("atr", [np.nan, np.inf, 0, -1, 1000])
def test_unavailable_atr_or_insufficient_range_consumes_known_first_release(atr):
    frame = featured()
    frame.loc[frame.open_time.eq(START+8*HOUR), "atr"] = atr
    entries, zones = run(frame, START+9*HOUR)
    assert entries.empty and zones.status.iloc[0] == "first_release_unqualified"


@pytest.mark.parametrize("direction", [1, -1])
def test_eighth_hour_checks_release_before_expiry(direction):
    entries, zones = run(featured(direction=direction, release=15), START+16*HOUR)
    assert len(entries) == 1 and entries.release_wait_hours.iloc[0] == 8
    assert zones.status.iloc[0] == "request_emitted"
    assert zones.terminal_time.iloc[0] == zones.zone_deadline.iloc[0]


def test_expiration_cannot_be_rescued_by_ninth_hour_or_rewritten_by_later_gap():
    frame = featured(release=16)
    entries, zones = run(frame, START+20*HOUR)
    assert entries.empty and zones.status.iloc[0] == "expired_no_release"
    assert zones.terminal_time.iloc[0] == START+16*HOUR
    broken = frame.loc[~frame.open_time.eq(START+17*HOUR)]
    actual = run(broken, START+20*HOUR)
    pd.testing.assert_frame_equal(zones, actual[1])


@pytest.mark.parametrize("terminal_kind", ["emitted", "unqualified", "expired"])
def test_rearming_requires_eight_new_hours_no_source_or_pending_reuse(terminal_kind):
    raw = raw_fixture(); contract(raw)
    terminal = 16 if terminal_kind == "expired" else 9
    if terminal_kind == "emitted": bar(raw, 8, [100, 106, 99.8, 105.8])
    elif terminal_kind == "unqualified": bar(raw, 8, [104, 108, 103, 107.9])
    contract(raw, terminal)
    bar(raw, terminal+8, [100, 106, 99.8, 105.8])
    _, prefix = run(add_features(raw), START+(terminal+7)*HOUR)
    assert len(prefix) == 1
    _, zones = run(add_features(raw), START+(terminal+9)*HOUR)
    assert len(zones) == 2
    assert zones.source_start.iloc[1] == zones.terminal_time.iloc[0]
    assert zones.zone_arm_time.iloc[1] == zones.terminal_time.iloc[0]+8*HOUR


@pytest.mark.parametrize("problem", ["missing", "segment", "segment_unknown", "invalid_prices"])
def test_pending_missing_hour_or_segment_boundary_is_censored_and_rewarms(problem):
    frame = featured(release=None)
    bad = START+9*HOUR
    if problem == "missing": frame = frame.loc[~frame.open_time.eq(bad)]
    elif problem == "segment": frame.loc[frame.open_time.ge(bad), "segment_id"] = 8
    elif problem == "segment_unknown": frame.loc[frame.open_time.eq(bad), "segment_id"] = pd.NA
    else: frame.loc[frame.open_time.eq(bad), "low"] = np.nan
    entries, zones = run(frame, START+13*HOUR)
    assert entries.empty and len(zones) == 1
    assert zones.status.iloc[0] == "censored_source_gap"
    assert zones.terminal_time.iloc[0] == START+10*HOUR


def test_missing_source_hour_does_not_compress_eight_hour_window():
    frame = featured(release=None)
    frame = frame.loc[~frame.open_time.eq(START+2*HOUR)]
    entries, zones = run(frame, START+8*HOUR)
    assert entries.empty and zones.empty


def test_gap_then_real_new_history_can_rearm_without_rescuing_old_zone():
    raw = raw_fixture(); contract(raw)
    contract(raw, 10); bar(raw, 18, [100, 106, 99.8, 105.8])
    frame = add_features(raw).loc[lambda f: ~f.open_time.eq(START+9*HOUR)]
    entries, zones = run(frame, START+19*HOUR)
    assert zones.status.tolist() == ["censored_source_gap", "request_emitted"]
    assert zones.source_start.iloc[1] == START+10*HOUR
    assert entries.signal_time.tolist() == [START+18*HOUR]


@pytest.mark.parametrize("hours", [8, 8.5, 10, 15.99])
def test_pending_prefix_is_unknown_end_not_known_expiration(hours):
    frame = featured(release=None)
    cutoff = START+pd.Timedelta(hours=hours)
    entries, zones = run(frame, cutoff)
    assert entries.empty and zones.status.tolist() == ["censored_source_end"]
    assert zones.terminal_time.iloc[0] == cutoff


def test_fold_start_excludes_pre_fold_zone_history_but_allows_atr_warmup():
    frame = featured(release=None)
    assert frame.loc[frame.open_time.eq(START), "atr"].notna().all()
    entries, zones = run(frame, START+8*HOUR, start=START+HOUR)
    assert entries.empty and zones.empty


@pytest.mark.parametrize("end_hours,expected", [(88, 0), (89, 1), (80, 0)])
def test_strict_eighty_hour_fold_embargo(end_hours, expected):
    entries, zones = run(featured(), START+9*HOUR, end=START+end_hours*HOUR)
    assert len(entries) == len(zones) == expected
    if expected: assert (entries.decision_time+72*HOUR < START+end_hours*HOUR).all()


def test_empty_input_schema_and_no_mutation():
    frame = featured(); original = frame.copy(deep=True)
    run(frame)
    pd.testing.assert_frame_equal(frame, original)
    entries, zones = run(frame.iloc[:0])
    assert entries.empty and zones.empty
    assert entries.columns.tolist() == SOURCE_ENTRY_COLUMNS and zones.columns.tolist() == ZONE_COLUMNS
    assert str(zones.direction.dtype) == "Int64"
    assert str(zones.terminal_time.dtype) == "datetime64[ns, UTC]"


def test_timezone_normalization_is_equivalent():
    frame = featured(); expected = run(frame, START+9*HOUR)
    frame["open_time"] = frame.open_time.dt.tz_convert("Asia/Shanghai")
    actual = run(frame, (START+9*HOUR).tz_convert("Asia/Shanghai"), start=START.tz_convert("Asia/Shanghai"))
    for left, right in zip(expected, actual): pd.testing.assert_frame_equal(left, right)


@pytest.mark.parametrize("problem", ["duplicates", "unsorted", "off_grid", "numeric", "numeric_object", "missing_time",
                                    "missing_feature", "wrong_minutes", "wrong_ma", "bad_fold", "bad_end", "numeric_start", "bad_start", "numeric_cutoff"])
def test_invalid_contracts_raise(problem):
    frame = featured(); kwargs = dict(fold="2024H1", start=START, end_exclusive=START+240*HOUR, observed_through=START+9*HOUR)
    idx = frame.index[frame.open_time.eq(START)][0]
    if problem == "duplicates": frame.loc[idx+1, "open_time"] = frame.loc[idx, "open_time"]
    elif problem == "unsorted": frame = frame.iloc[::-1]
    elif problem == "off_grid": frame.loc[idx, "open_time"] += pd.Timedelta(seconds=1)
    elif problem == "numeric": frame["open_time"] = frame.open_time.astype("int64")
    elif problem == "numeric_object": frame["open_time"] = frame.open_time.astype("int64").astype(object)
    elif problem == "missing_time": frame.loc[idx, "open_time"] = pd.NaT
    elif problem == "missing_feature": frame = frame.drop(columns="atr")
    elif problem == "wrong_minutes": frame.attrs["bar_minutes"] = 15
    elif problem == "wrong_ma": frame.attrs["ma_length"] = 20
    elif problem == "bad_fold": kwargs["fold"] = ""
    elif problem == "bad_end": kwargs["end_exclusive"] = START
    elif problem == "numeric_start": kwargs["start"] = 1700000000
    elif problem == "bad_start": kwargs["start"] = START+pd.Timedelta(minutes=1)
    else: kwargs["observed_through"] = 1700000000
    with pytest.raises(ValueError): build_source_zone_requests(frame, **kwargs)


def test_completed_terminal_invariant_to_future_suffix_mutation_and_prefix():
    for first_qualifies, later_up in product((False, True), repeat=2):
        raw = raw_fixture(); contract(raw)
        bar(raw, 8, [100, 106, 99.8, 105.8] if first_qualifies else [104, 108, 103, 107.9])
        frame = add_features(raw)
        expected = run(frame.loc[frame.open_time.lt(START+9*HOUR)], START+9*HOUR)
        future = frame.open_time.ge(START+9*HOUR)
        frame.loc[future, ["open", "high", "low", "close", "volume", "atr", "ma", "ma_side"]] = np.nan
        frame.loc[future, "bullish_engulf"] = later_up
        current = run(frame, START+9*HOUR)
        for left, right in zip(expected, current): pd.testing.assert_frame_equal(left, right)
        # Later invalid source rows are processed only when available, and can
        # never rewrite the earlier emitted/unqualified terminal.
        extended = run(frame, START+20*HOUR)
        pd.testing.assert_frame_equal(expected[0], extended[0])
        pd.testing.assert_frame_equal(expected[1], extended[1])


@pytest.mark.parametrize("direction", [1, -1])
def test_unfinished_release_bar_future_colour_and_ohlc_cannot_leak(direction):
    frame = featured(direction=direction)
    cutoff = START+pd.Timedelta(hours=8, minutes=30)
    baseline = run(frame, cutoff)
    frame.loc[frame.open_time.ge(START+8*HOUR), ["open", "high", "low", "close", "atr", "ma_side"]] = np.nan
    actual = run(frame, cutoff)
    assert baseline[0].empty and baseline[1].status.iloc[0] == "censored_source_end"
    for left, right in zip(baseline, actual): pd.testing.assert_frame_equal(left, right)
