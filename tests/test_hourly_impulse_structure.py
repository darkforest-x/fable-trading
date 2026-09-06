"""Synthetic causality/clock tests; no files, prices, or outcomes are read."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse import BAR_COLUMNS
from yoyo.data.hourly_impulse_structure import (
    HOURLY_STRUCTURE_COLUMNS, STRUCTURE_COLUMNS, add_hourly_structure_state,
    add_structure_context,
)


START = pd.Timestamp("2024-01-01", tz="UTC")
HOUR = pd.Timedelta(hours=1)


def hours(n=55):
    return pd.DataFrame({
        "open_time": pd.date_range(START, periods=n, freq="h"),
        "open": np.full(n, 100.0), "high": np.full(n, 101.0),
        "low": np.full(n, 99.0), "close": np.full(n, 100.0),
        "volume": np.ones(n), "segment_id": np.full(n, 42),
    })


def scenario(direction=1, n=55):
    data = hours(n)
    data.loc[10, "high"] = 110.0
    data.loc[10, "low"] = 90.0
    data.loc[21, ["high", "close"]] = [112.0, 111.0]
    # Price recrosses the same high in the same direction: no new state event.
    data.loc[23, ["high", "close"]] = [112.0, 111.0]
    data.loc[24, ["low", "close"]] = [88.0, 89.0]
    if direction == -1:
        data[["open", "high", "low", "close"]] = np.column_stack((
            200 - data.open, 200 - data.low, 200 - data.high, 200 - data.close))
    return data


def to_raw(hourly):
    rows = []
    for row in hourly.itertuples():
        for offset in range(12):
            rows.append({
                "open_time": row.open_time + pd.Timedelta(minutes=5 * offset),
                "open": row.open, "high": row.high, "low": row.low,
                "close": row.close, "volume": row.volume / 12,
                "segment_id": "ignored-source-label",
            })
    return pd.DataFrame(rows, columns=BAR_COLUMNS + ["segment_id"])


def requests(data, positions, directions=None):
    if directions is None:
        directions = [1] * len(positions)
    return pd.DataFrame({
        "event_id": ["event-%d" % i for i in range(len(positions))],
        "signal_time": [data.open_time.iloc[i] for i in positions],
        "decision_time": [data.open_time.iloc[i] + HOUR for i in positions],
        "signal_close": [data.close.iloc[i] for i in positions],
        "direction": directions,
    })


@pytest.mark.parametrize("direction", [1, -1])
def test_confirm_at_ten_right_closes_and_persistent_alternating_state(direction):
    data = scenario(direction)
    out = add_hourly_structure_state(data)
    for i in range(20):
        assert pd.isna(out.structure_high.iloc[i])
        assert pd.isna(out.structure_state.iloc[i])
    assert out.structure_high_origin.iloc[20] == START + 10 * HOUR
    assert out.structure_high_confirmed_at.iloc[20] == START + 21 * HOUR
    assert not out.structure_known.iloc[20]
    assert pd.isna(out.structure_state_before.iloc[21])
    assert out.structure_state.iloc[21] == direction
    assert out.structure_break_direction.iloc[21] == direction
    assert out.structure_last_break_available_at.iloc[21] == START + 22 * HOUR
    for i in (22, 23):
        assert out.structure_state.iloc[i] == direction
        assert out.structure_state_before.iloc[i] == direction
        assert not out.structure_break_on_k1.iloc[i]
        assert out.structure_last_break_available_at.iloc[i] == START + 22 * HOUR
    assert out.structure_state_before.iloc[24] == direction
    assert out.structure_state.iloc[24] == -direction
    assert out.structure_last_break_available_at.iloc[24] == START + 25 * HOUR
    assert out.structure_state.iloc[25] == -direction


def test_tied_extrema_confirmed_without_claiming_pine_tie_parity():
    out = add_hourly_structure_state(hours(24))
    assert out.structure_high.iloc[20] == 101
    assert out.structure_low.iloc[20] == 99
    assert out.structure_high_origin.iloc[21] == START + 11 * HOUR
    assert out.structure_high_confirmed_at.iloc[21] == START + 22 * HOUR
    assert not out.structure_known.any()
    assert out.structure_state.isna().all()
    assert out.structure_reason.iloc[20] == "no_confirmed_break"


@pytest.mark.parametrize("direction", [1, -1])
def test_equality_not_break_but_later_strict_close_is(direction):
    data = hours(24)
    boundary = 101 if direction == 1 else 99
    data.loc[21, "close"] = boundary
    data.loc[22, "close"] = boundary + direction
    data.loc[22, "high" if direction == 1 else "low"] = boundary + direction
    out = add_hourly_structure_state(data)
    assert not out.structure_known.iloc[21]
    assert out.structure_state.iloc[22] == direction


def test_same_priced_replacement_pivot_keeps_price_guard():
    data = hours(23)
    data.loc[21, ["high", "close"]] = [102, 102]
    out = add_hourly_structure_state(data)
    assert out.structure_high.iloc[21] == out.structure_high.iloc[20] == 101
    # The new centre cannot confirm when current high exceeds it. On equal
    # replacement rows origins still advance; guard compares prices, not IDs.
    assert out.structure_high_origin.iloc[20] == START + 10 * HOUR
    assert out.structure_break_direction.iloc[21] == 1


def test_different_level_does_not_create_a_synthetic_cross():
    data = hours(44)
    data.loc[10, "high"] = 120
    data.loc[21, "high"] = 110
    # At 31 a lower high confirms; price stays BELOW it, never crosses it.
    data.loc[30:32, "close"] = 109
    data.loc[30:32, "high"] = 109.5
    out = add_hourly_structure_state(data)
    assert out.structure_high.iloc[30] == 120
    assert out.structure_high.iloc[31] == 110
    assert not out.structure_break_on_k1.iloc[31]
    assert not out.structure_known.iloc[32]
    # An actual next-hour strict crossing of the now stable level is allowed.
    data.loc[33, ["high", "close"]] = [112, 111]
    out = add_hourly_structure_state(data)
    assert out.structure_state.iloc[33] == 1


@pytest.mark.parametrize("missing_count", [1, 2, 12])
def test_raw_gap_or_incomplete_hour_resets_all_state(missing_count):
    data = scenario(n=60)
    raw = to_raw(data)
    start = 26 * 12
    raw = raw.drop(raw.index[start:start + missing_count]).reset_index(drop=True)
    query = requests(data, [25, 26, 27, 46, 47])
    out = add_structure_context(query, raw)
    assert out.structure_state.iloc[0] == -1
    assert out.structure_reason.iloc[1] == "missing_signal_hour"
    assert out.structure_reason.iloc[2] == "warmup"
    assert pd.isna(out.structure_state_before.iloc[2])
    assert pd.isna(out.structure_high.iloc[2])
    assert pd.isna(out.structure_last_break_available_at.iloc[2])
    assert out.structure_count.iloc[2] == 1
    assert out.structure_count.iloc[3] == 20
    assert out.structure_reason.iloc[4] == "no_confirmed_break"


def test_two_source_gaps_in_same_hour_do_not_compare_segment_id_spaces():
    data = scenario(n=60)
    raw = to_raw(data).drop(index=[26 * 12 + 1, 26 * 12 + 7]).reset_index(drop=True)
    out = add_structure_context(requests(data, [47]), raw).iloc[0]
    assert out.structure_segment_id == 1
    assert out.structure_raw_segment_id == 2
    assert out.structure_count == 21
    assert out.structure_reason == "no_confirmed_break"


def test_own_control_times_directions_and_request_identity_preserved():
    data = scenario()
    query = requests(data, [25, 22, 21, 22, 10], [-1, -1, 1, 1, 1])
    query.index = pd.Index([8, 8, 1, 0, 99], name="original-index")
    query["parent_event_id"] = ["p"] * len(query)
    query["outcome"] = ["untrusted-unused"] * len(query)
    query.attrs = {"receipt": {"immutable": True}}
    before = query.copy(deep=True)
    raw = to_raw(data)
    raw_before = raw.copy(deep=True)
    out = add_structure_context(query, raw)
    assert_frame_equal(out[query.columns], query)
    assert_frame_equal(query, before)
    assert_frame_equal(raw, raw_before)
    assert out.attrs == query.attrs
    assert out.structure_gate_state.tolist() == ["accepted", "abstain", "accepted", "accepted", "unknown"]
    assert not out.structure_break_on_k1.iloc[3]
    assert out.structure_state.dtype == "Int64"


@pytest.mark.parametrize("cut", [1, 10, 20, 21, 22, 24, 25, 31, 45])
def test_hourly_prefix_causality_and_future_mutation(cut):
    data = scenario()
    full = add_hourly_structure_state(data)
    prefix = add_hourly_structure_state(data.iloc[:cut])
    assert_frame_equal(full.iloc[:cut], prefix)
    mutated = data.copy()
    mutated.loc[cut:, ["open", "high", "low", "close"]] *= 9
    changed = add_hourly_structure_state(mutated)
    assert_frame_equal(full.iloc[:cut], changed.iloc[:cut])


@pytest.mark.parametrize("position", [0, 19, 20, 21, 22, 24, 32, 54])
def test_raw_future_prices_not_selected_or_validated_and_batch_prefix_agrees(position):
    data = scenario()
    raw = to_raw(data)
    query = requests(data, [position])
    before = add_structure_context(query, raw)
    end = (position + 1) * 12
    after = raw.copy()
    after.loc[end:, ["open", "high", "low", "close", "volume"]] = np.nan
    assert_frame_equal(before, add_structure_context(query, after))
    assert_frame_equal(before, add_structure_context(query, raw.iloc[:end]))
    all_requests = requests(data, list(range(len(data))))
    batched = add_structure_context(all_requests, raw)
    assert_frame_equal(before[STRUCTURE_COLUMNS].reset_index(drop=True),
                       batched.iloc[[position]][STRUCTURE_COLUMNS].reset_index(drop=True))


def test_absent_k1_never_uses_latest_stale_hour():
    data = scenario()
    query = requests(data, [22])
    raw = to_raw(data)
    raw = raw.loc[raw.open_time < START + 22 * HOUR]
    out = add_structure_context(query, raw).iloc[0]
    assert out.structure_gate_state == "unknown"
    assert out.structure_reason == "missing_signal_hour"
    assert pd.isna(out.structure_state)
    assert pd.isna(out.structure_signal_close)


def test_confirming_hour_and_k1_require_all_twelve_raw_bars():
    data = scenario()
    raw = to_raw(data)
    # The pivot centred at hour 10 cannot be published before hour 20 closes.
    query = requests(data, [20, 21])
    truncated = raw.loc[raw.open_time < START + 20 * HOUR + pd.Timedelta(minutes=55)]
    before = add_structure_context(query, truncated)
    assert before.structure_reason.tolist() == ["missing_signal_hour", "missing_signal_hour"]
    assert before.structure_high.isna().all()
    completed = add_structure_context(query.iloc[:1], raw.iloc[:21 * 12])
    assert completed.structure_high.iloc[0] == 110
    assert not completed.structure_known.iloc[0]
    # Missing the decisive closing subbar drops the ENTIRE K1, not just its close.
    without_k1_last = raw.drop(index=22 * 12 - 1)
    out = add_structure_context(query, without_k1_last)
    assert out.structure_reason.iloc[1] == "missing_signal_hour"
    assert pd.isna(out.structure_state.iloc[1])


@pytest.mark.parametrize("direction", [1, -1])
def test_only_actual_final_k1_close_establishes_state_not_wicks_or_earlier_closes(direction):
    data = scenario(direction)
    raw = to_raw(data)
    mask = raw.open_time.ge(START + 21 * HOUR) & raw.open_time.lt(START + 22 * HOUR)
    raw.loc[mask, "close"] = 111 if direction == 1 else 89
    raw.loc[22 * 12 - 1, "close"] = 100
    query = requests(data, [21], [direction])
    query.signal_close = 100
    out = add_structure_context(query, raw).iloc[0]
    assert not out.structure_known
    assert out.structure_signal_close == 100
    assert out.structure_gate_state == "unknown"


def test_partial_source_hour_with_no_complete_hours_is_unknown_not_error():
    data = hours(1)
    raw = to_raw(data).iloc[:11]
    out = add_structure_context(requests(data, [0]), raw).iloc[0]
    assert out.structure_reason == "missing_signal_hour"
    assert out.structure_gate_state == "unknown"
    assert pd.isna(out.structure_state)


def test_earlier_no_source_unchanged_by_later_request():
    data = scenario()
    raw = to_raw(data.iloc[10:])
    query = requests(data, [0, 22])
    out = add_structure_context(query, raw)
    early = add_structure_context(query.iloc[:1], raw)
    assert out.structure_reason.iloc[0] == "no_source"
    assert_frame_equal(out.iloc[:1], early)


@pytest.mark.parametrize("position", [0, 10, 21, 22])
def test_own_signal_close_mismatch_raises_even_when_not_warmed_up(position):
    data = scenario()
    query = requests(data, [position])
    query.loc[0, "signal_close"] += 1
    with pytest.raises(ValueError, match="signal_close parity"):
        add_structure_context(query, to_raw(data))


@pytest.mark.parametrize("column", ["signal_time", "decision_time"])
@pytest.mark.parametrize("bad", ["2024-01-01", 1704067200000, 1.0, True, pd.NaT, None])
def test_invalid_request_clocks_rejected(column, bad):
    query = requests(hours(), [1])
    query[column] = [bad]
    with pytest.raises(ValueError):
        add_structure_context(query, to_raw(hours()))


@pytest.mark.parametrize("column,bad", [
    ("direction", 0), ("direction", True), ("direction", np.bool_(True)),
    ("direction", "1"), ("direction", np.nan), ("signal_close", True),
    ("signal_close", 0), ("signal_close", -1), ("signal_close", np.inf),
    ("signal_close", np.nan), ("event_id", None),
])
def test_invalid_request_values_rejected(column, bad):
    query = requests(hours(), [1])
    query[column] = [bad]
    with pytest.raises(ValueError):
        add_structure_context(query, to_raw(hours()))


@pytest.mark.parametrize("kind", ["naive", "numeric", "duplicate", "unordered", "offgrid", "null"])
def test_invalid_raw_or_hourly_clocks_rejected(kind):
    for source, call in ((hours(), add_hourly_structure_state),
                         (to_raw(hours()), lambda frame: add_structure_context(requests(hours(), [1]), frame))):
        if kind == "naive":
            source.open_time = source.open_time.dt.tz_localize(None)
        elif kind == "numeric":
            source.open_time = np.arange(len(source))
        elif kind == "duplicate":
            source.loc[1, "open_time"] = source.open_time.iloc[0]
        elif kind == "unordered":
            source = source.iloc[::-1]
        elif kind == "offgrid":
            source.open_time += pd.Timedelta(nanoseconds=1)
        else:
            source.loc[0, "open_time"] = pd.NaT
        with pytest.raises(ValueError):
            call(source)


@pytest.mark.parametrize("column,value", [("close", np.nan), ("high", 0),
    ("low", 103), ("open", True), ("high", np.inf)])
def test_invalid_source_ohlc_rejected(column, value):
    data = hours()
    data[column] = data[column].astype(object)
    data.loc[0, column] = value
    with pytest.raises(ValueError):
        add_hourly_structure_state(data)
    raw = to_raw(hours())
    raw[column] = raw[column].astype(object)
    raw.loc[0, column] = value
    with pytest.raises(ValueError):
        add_structure_context(requests(hours(), [1]), raw)


def test_timezone_conversion_preserves_original_request_fields():
    data = scenario()
    query = requests(data, [22])
    query.signal_time = query.signal_time.dt.tz_convert("Asia/Shanghai")
    query.decision_time = query.decision_time.dt.tz_convert("America/New_York")
    raw = to_raw(data)
    raw.open_time = raw.open_time.dt.tz_convert("Asia/Shanghai")
    out = add_structure_context(query, raw)
    assert_frame_equal(out[query.columns], query)
    assert out.structure_available_at.iloc[0] == START + 23 * HOUR
    assert out.structure_gate_state.iloc[0] == "accepted"


@pytest.mark.parametrize("target", ["request", "hourly", "raw"])
def test_duplicate_schema_missing_schema_or_output_overwrite_rejected(target):
    data = hours()
    query = requests(data, [1])
    raw = to_raw(data)
    frame = {"request": query, "hourly": data, "raw": raw}[target]
    first = "signal_time" if target == "request" else "open_time"
    for changed in (frame.drop(columns=first), pd.concat([frame, frame[[first]]], axis=1)):
        with pytest.raises(ValueError):
            if target == "request":
                add_structure_context(changed, raw)
            elif target == "hourly":
                add_hourly_structure_state(changed)
            else:
                add_structure_context(query, changed)
    if target != "raw":
        frame["structure_anything"] = 0
        with pytest.raises(ValueError, match="refuse overwrite"):
            (add_hourly_structure_state(frame) if target == "hourly"
             else add_structure_context(frame, raw))


def test_duplicate_event_ids_and_nonhour_request_rejected():
    query = requests(hours(), [1, 2])
    query.event_id = "same"
    with pytest.raises(ValueError, match="identities"):
        add_structure_context(query, to_raw(hours()))
    query = requests(hours(), [1])
    query.signal_time += pd.Timedelta(minutes=5)
    query.decision_time += pd.Timedelta(minutes=5)
    with pytest.raises(ValueError, match="hour OPEN"):
        add_structure_context(query, to_raw(hours()))


def test_empty_requests_and_empty_sources_keep_fixed_schema():
    query = requests(hours(), [])
    query.attrs = {"source": "synthetic"}
    out = add_structure_context(query, pd.DataFrame())
    assert out.empty
    assert set(out.columns) == set(query.columns) | set(STRUCTURE_COLUMNS)
    assert out.attrs == query.attrs
    assert out.structure_state.dtype == "Int64"
    assert out.structure_available_at.dtype == "datetime64[ns, UTC]"
    hour_out = add_hourly_structure_state(hours(0))
    assert hour_out.empty
    assert set(HOURLY_STRUCTURE_COLUMNS).issubset(hour_out)
    raw = to_raw(hours(0))
    unknown = add_structure_context(requests(hours(), [1]), raw).iloc[0]
    assert unknown.structure_reason == "no_source"
    assert unknown.structure_gate_state == "unknown"


def test_unused_feature_and_segment_labels_cannot_affect_state():
    data = scenario()
    before = add_hourly_structure_state(data)
    data.segment_id = ["unrelated-%d" % i for i in range(len(data))]
    data["ma"] = np.nan
    data["atr"] = -1
    data["net_return"] = np.inf
    data.volume = np.nan  # Pure hourly state does not use volume at all.
    after = add_hourly_structure_state(data)
    assert_frame_equal(before[HOURLY_STRUCTURE_COLUMNS], after[HOURLY_STRUCTURE_COLUMNS])
