"""V13 pure colour availability tests, synthetic5m candles only."""
import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse_prior_colour import COLOUR_COLUMNS, add_prior_colour_context


START = pd.Timestamp("2024-01-01", tz="UTC")


def raw(count=41, *, slope=1, flat=False):
    times = pd.date_range(START, periods=count * 48, freq="5min")
    value = 100. + slope * np.repeat(np.arange(count), 48) if not flat else np.full(len(times), 100.)
    return pd.DataFrame({"open_time": times, "open": value, "high": value,
        "low": value, "close": value, "volume": 0.})


def requests(times, directions=None):
    times = pd.to_datetime(times, utc=True)
    return pd.DataFrame({"event_id": [f"e{i}" for i in range(len(times))],
        "signal_time": times, "direction": directions if directions is not None else [1]*len(times),
        "decision_time": times + pd.Timedelta(hours=1), "signal_close": np.inf, "ma": -1.})


def at(hours):
    return START + pd.Timedelta(hours=hours)


def test_39_unknown_40_known_not_43_even_zero_atr_or_slope():
    query = requests([at(156), at(160), at(164)], [1, 1, -1])
    result = add_prior_colour_context(query, raw(flat=True))
    assert result.prior_colour_count.tolist() == [39, 40, 41]
    assert result.prior_colour_known.tolist() == [False, True, True]
    assert result.prior_colour_reason.tolist() == ["warmup", "known", "known"]
    assert result.prior_colour_gate_state.tolist() == ["unknown", "accepted", "abstain"]
    assert pd.isna(result.prior_colour_side.iloc[0])
    assert result.prior_colour_side.iloc[1:].tolist() == [1, 1]
    assert result.prior_colour_ma.iloc[1:].eq(100).all()


@pytest.mark.parametrize("direction", [1, -1])
def test_real_4h_hl2_colour_and_own_direction_mirror(direction):
    query = requests([at(160), at(160)], [direction, -direction])
    result = add_prior_colour_context(query, raw(slope=direction))
    assert result.prior_colour_side.tolist() == [direction, direction]
    assert result.prior_colour_gate_state.tolist() == ["accepted", "abstain"]
    assert result.prior_colour_ma.iloc[0] == pytest.approx(100 + direction*19.5)
    assert result.prior_colour_hl2.iloc[0] == 100 + direction*39


def test_colour_is_hl2_not_last_close_or_open_colour():
    frame = raw(flat=True)
    final = frame.open_time.ge(at(156)) & frame.open_time.lt(at(160))
    frame.loc[final, ["open", "close", "low"]] = 90.
    frame.loc[final, "high"] = 130.
    result = add_prior_colour_context(requests([at(160)]), frame)
    assert result.prior_colour_hl2.iloc[0] == 110.
    assert result.prior_colour_side.iloc[0] == 1


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
def test_latest_completed_exact_boundary_and_hour_phases(phase):
    result = add_prior_colour_context(requests([at(160+phase)]), raw(42))
    assert result.prior_colour_bar_open.iloc[0] == at(156)
    assert result.prior_colour_available_at.iloc[0] == at(160)
    assert result.prior_colour_count.iloc[0] == 40
    assert result.prior_colour_known.iloc[0]


def test_k1_open_not_later_decision_close_and_future_suffix_is_ignored():
    query = requests([at(159)])
    frame = raw(41)
    expected = add_prior_colour_context(query, frame)
    assert expected.prior_colour_count.iloc[0] == 39
    assert expected.prior_colour_available_at.iloc[0] == at(156)
    # At decision_time160h the40th4h bar would be complete: it is not eligible.
    frame.loc[frame.open_time.ge(at(159)), ["open", "high", "low", "close", "volume"]] = np.nan
    actual = add_prior_colour_context(query, frame)
    pd.testing.assert_frame_equal(expected, actual)
    pd.testing.assert_frame_equal(expected, add_prior_colour_context(query, frame.loc[frame.open_time.lt(at(159))]))


def test_developing_4h_prices_cannot_change_prior_completed_colour():
    query = requests([at(163)])
    frame = raw(42)
    expected = add_prior_colour_context(query, frame)
    developing = frame.open_time.ge(at(160)) & frame.open_time.lt(at(163))
    frame.loc[developing, ["open", "high", "low", "close"]] = 1.
    actual = add_prior_colour_context(query, frame)
    pd.testing.assert_frame_equal(expected, actual)


def test_unsorted_requests_duplicate_signal_times_index_attrs_and_fields_preserved():
    query = requests([at(164), at(160), at(164)], [-1, 1, 1])
    query.index = pd.Index([7, 7, 2], name="owner_index")
    query.attrs["provenance"] = {"unchanged": True}
    original = query.copy(deep=True)
    result = add_prior_colour_context(query, raw())
    pd.testing.assert_frame_equal(result[query.columns], original)
    assert result.attrs == query.attrs
    assert result.prior_colour_gate_state.tolist() == ["abstain", "accepted", "accepted"]
    assert list(result.columns) == list(query.columns) + COLOUR_COLUMNS


@pytest.mark.parametrize("missing_hour", [160, 161, 162])
def test_one_intervening_raw_gap_invalidates_old_context(missing_hour):
    frame = raw(42)
    frame = frame.loc[frame.open_time.ne(at(missing_hour)+pd.Timedelta(minutes=15))]
    result = add_prior_colour_context(requests([at(163)]), frame)
    assert result.prior_colour_reason.iloc[0] == "source_gap"
    assert result.prior_colour_gate_state.iloc[0] == "unknown"
    assert pd.isna(result.prior_colour_side.iloc[0])
    assert result.prior_colour_available_at.iloc[0] == at(160)


def test_exact_final_raw5_bar_required_to_prove_continuity():
    frame = raw(41)
    frame = frame.loc[frame.open_time.ne(at(162)-pd.Timedelta(minutes=5))]
    result = add_prior_colour_context(requests([at(162)]), frame)
    assert result.prior_colour_reason.iloc[0] == "source_gap"
    assert not result.prior_colour_known.iloc[0]


def test_incomplete_latest4h_is_not_replaced_by_stale_previous_bar():
    frame = raw(42)
    frame = frame.loc[frame.open_time.ne(at(161))]
    result = add_prior_colour_context(requests([at(164)]), frame)
    assert result.prior_colour_available_at.iloc[0] == at(160)
    assert result.prior_colour_reason.iloc[0] == "stale_context"
    assert result.prior_colour_gate_state.iloc[0] == "unknown"


def test_missing4h_restarts_count_even_when40_old_bars_exist():
    frame = raw(82)
    frame = frame.loc[frame.open_time.ne(at(161))]
    result = add_prior_colour_context(requests([at(168), at(320), at(324)]), frame)
    assert result.prior_colour_count.tolist() == [1, 39, 40]
    assert result.prior_colour_known.tolist() == [False, False, True]
    assert result.prior_colour_reason.tolist() == ["warmup", "warmup", "known"]


def test_prefix_invariance_for_multiple_queries_and_valid_future_mutation():
    frame = raw(50)
    early = requests([at(160), at(163)])
    full_query = requests([at(160), at(163), at(196)])
    expected = add_prior_colour_context(early, frame.loc[frame.open_time.lt(at(163))])
    full = add_prior_colour_context(full_query, frame)
    pd.testing.assert_frame_equal(expected, full.iloc[:2])
    later = frame.open_time.ge(at(163))
    frame.loc[later, ["open", "high", "low", "close"]] *= .5
    mutated = add_prior_colour_context(full_query, frame)
    pd.testing.assert_frame_equal(expected, mutated.iloc[:2])


def test_opaque_supplied_raw_segment_ids_are_not_cross_grid_counters():
    frame = raw()
    original = add_prior_colour_context(requests([at(160)]), frame)
    frame["segment_id"] = 99
    actual = add_prior_colour_context(requests([at(160)]), frame)
    pd.testing.assert_frame_equal(original, actual)


def test_timezone_normalization_only_applies_to_added_diagnostics():
    query = requests([at(160)])
    query["signal_time"] = query.signal_time.dt.tz_convert("Asia/Shanghai")
    frame = raw()
    frame["open_time"] = frame.open_time.dt.tz_convert("America/New_York")
    result = add_prior_colour_context(query, frame)
    pd.testing.assert_frame_equal(result[query.columns], query)
    assert str(result.prior_colour_available_at.dtype) == "datetime64[ns, UTC]"
    assert result.prior_colour_available_at.iloc[0] == at(160)


@pytest.mark.parametrize("case", ["empty_raw", "no_full4h", "before_first", "empty_requests"])
def test_empty_and_no_context_are_schema_stable_unknown(case):
    frame = raw(2)
    query = requests([at(4)])
    if case == "empty_raw": frame = frame.iloc[:0]
    elif case == "no_full4h": frame = frame.iloc[:47]
    elif case == "before_first": query = requests([START])
    else: query = query.iloc[:0]
    result = add_prior_colour_context(query, frame)
    assert len(result) == len(query)
    assert set(COLOUR_COLUMNS).issubset(result)
    assert result.prior_colour_gate_state.eq("unknown").all()
    assert result.prior_colour_side.isna().all()
    assert str(result.prior_colour_side.dtype) == "Int64"


@pytest.mark.parametrize("change", ["direction0", "directionbool", "duplicatedid", "nullid", "collide",
    "duplicatecolumn", "missingcolumn", "naive", "numeric", "nulltime", "not_hour", "rawduplicate", "rawreverse",
    "rawnumeric", "rawnaive", "rawoffgrid", "badbounds", "rawnan", "rawmissing"])
def test_malformed_contracts_fail_not_silently_filter(change):
    query = requests([at(160), at(164)])
    frame = raw()
    if change == "direction0": query["direction"] = 0
    elif change == "directionbool": query["direction"] = True
    elif change == "duplicatedid": query["event_id"] = "duplicate"
    elif change == "nullid": query["event_id"] = None
    elif change == "collide": query[COLOUR_COLUMNS[0]] = pd.NaT
    elif change == "duplicatecolumn": query = pd.concat([query, query[["direction"]]], axis=1)
    elif change == "missingcolumn": query = query.drop(columns="direction")
    elif change == "naive": query["signal_time"] = query.signal_time.dt.tz_localize(None)
    elif change == "numeric": query["signal_time"] = 1000
    elif change == "nulltime": query["signal_time"] = pd.NaT
    elif change == "not_hour": query["signal_time"] += pd.Timedelta(minutes=5)
    elif change == "rawduplicate": frame = pd.concat([frame.iloc[:1], frame], ignore_index=True)
    elif change == "rawreverse": frame = frame.iloc[::-1]
    elif change == "rawnumeric": frame["open_time"] = np.arange(len(frame))
    elif change == "rawnaive": frame["open_time"] = frame.open_time.dt.tz_localize(None)
    elif change == "rawoffgrid": frame["open_time"] += pd.Timedelta(seconds=1)
    elif change == "badbounds": frame["high"] = 1.
    elif change == "rawnan": frame["low"] = np.nan
    else: frame = frame.drop(columns="volume")
    with pytest.raises((ValueError, TypeError)):
        add_prior_colour_context(query, frame)
