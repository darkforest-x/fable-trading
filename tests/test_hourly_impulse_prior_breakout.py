"""Synthetic-only fixed prior20-hour context tests; no price files or returns."""
import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import BAR_COLUMNS
from yoyo.data.hourly_impulse_prior_breakout import BREAKOUT_COLUMNS, add_prior_breakout_context


START = pd.Timestamp("2024-01-01", tz="UTC")


def at(hours):
    return START + pd.Timedelta(hours=hours)


def raw(hours=25):
    return pd.DataFrame({"open_time": pd.date_range(START, periods=hours*12, freq="5min"),
        "open": 100., "high": 110., "low": 90., "close": 100., "volume": 1.})


def set_hour(frame, hour, close):
    selected = frame.open_time.ge(at(hour)) & frame.open_time.lt(at(hour+1))
    frame.loc[selected, "close"] = close
    frame.loc[selected, "high"] = max(110., close)
    frame.loc[selected, "low"] = min(90., close)


def requests(hours=(20,), directions=None, closes=None):
    times = pd.to_datetime([at(hour) for hour in hours], utc=True)
    return pd.DataFrame({"event_id": [f"e{i}" for i in range(len(hours))],
        "signal_time": times, "decision_time": times+pd.Timedelta(hours=1),
        "direction": directions if directions is not None else [1]*len(hours),
        "signal_close": closes if closes is not None else [100.]*len(hours),
        "ma": np.nan, "parent_event_id": "unchanged"})


@pytest.mark.parametrize("direction,close,gate", [(1,111,"accepted"), (-1,89,"accepted"),
    (1,110,"abstain"), (-1,90,"abstain"), (1,109,"abstain"), (-1,91,"abstain")])
def test_strict_own_close_breakout_and_equality(direction, close, gate):
    frame = raw()
    set_hour(frame, 20, close)
    result = add_prior_breakout_context(requests(directions=[direction], closes=[close]), frame).iloc[0]
    assert result.prior_breakout_known
    assert result.prior_breakout_gate_state == gate
    assert result.prior_breakout_count == 20
    assert result.prior_breakout_high == 110
    assert result.prior_breakout_low == 90
    assert result.prior_breakout_signal_close == close
    assert result.prior_breakout_window_start == at(0)
    assert result.prior_breakout_window_end == at(19)
    assert result.prior_breakout_available_at == at(20)
    assert result.prior_breakout_signal_available_at == at(21)


def test_k1_entire_range_excluded_and_signal_uses_last_subbar_close():
    frame = raw()
    frame.loc[frame.open_time.ge(at(20)), ["high", "low"]] = [1000., 1.]
    frame.loc[frame.open_time.eq(at(20)+pd.Timedelta(minutes=55)), "close"] = 111.
    result = add_prior_breakout_context(requests(closes=[111.]), frame).iloc[0]
    assert (result.prior_breakout_high, result.prior_breakout_low) == (110., 90.)
    assert result.prior_breakout_gate_state == "accepted"


def test_exact20_support_not19_not21_and_count_capped():
    result = add_prior_breakout_context(requests([19,20,24]), raw())
    assert result.prior_breakout_count.tolist() == [19,20,20]
    assert result.prior_breakout_reason.tolist() == ["warmup","known","known"]
    assert result.prior_breakout_gate_state.tolist() == ["unknown","abstain","abstain"]
    assert np.isnan(result.prior_breakout_high.iloc[0])


def test_window_moves_and_does_not_reuse_twenty_first_old_hour():
    frame = raw()
    frame.loc[frame.open_time.lt(at(1)), ["high", "low"]] = [200., 1.]
    result = add_prior_breakout_context(requests([20,21]), frame)
    assert result.prior_breakout_high.tolist() == [200.,110.]
    assert result.prior_breakout_low.tolist() == [1.,90.]


@pytest.mark.parametrize("missing", [0, 7, 19])
def test_single_missing_raw_bar_breaks_prior_support(missing):
    frame = raw()
    frame = frame.loc[frame.open_time.ne(at(missing)+pd.Timedelta(minutes=25))]
    result = add_prior_breakout_context(requests(), frame).iloc[0]
    assert result.prior_breakout_count == 19-missing
    assert not result.prior_breakout_known
    assert result.prior_breakout_reason == "source_gap"
    assert result.prior_breakout_gate_state == "unknown"
    assert np.isnan(result.prior_breakout_high)


def test_whole_hour_gap_resets_until_exact20_rebuilt():
    frame = raw(43)
    frame = frame.loc[~(frame.open_time.ge(at(1)) & frame.open_time.lt(at(2)))]
    result = add_prior_breakout_context(requests([21,22]), frame)
    assert result.prior_breakout_count.tolist() == [19,20]
    assert result.prior_breakout_reason.tolist() == ["source_gap","known"]
    assert result.prior_breakout_raw_segment_id.tolist() == [1,1]


@pytest.mark.parametrize("minute", [0, 25, 55])
def test_incomplete_own_k1_is_unknown_even_with_twenty_prior_hours(minute):
    frame = raw()
    frame = frame.loc[frame.open_time.ne(at(20)+pd.Timedelta(minutes=minute))]
    result = add_prior_breakout_context(requests(), frame).iloc[0]
    assert result.prior_breakout_count == 20
    assert result.prior_breakout_high == 110.
    assert result.prior_breakout_reason == "missing_signal_hour"
    assert result.prior_breakout_gate_state == "unknown"
    assert pd.isna(result.prior_breakout_signal_close)
    assert pd.isna(result.prior_breakout_raw_segment_id)


@pytest.mark.parametrize("hour", [1,20])
def test_close_parity_raises_even_when_prior_warmup(hour):
    with pytest.raises(ValueError, match="signal_close parity"):
        add_prior_breakout_context(requests([hour], closes=[101.]), raw())


def test_close_parity_small_csv_roundoff_allowed():
    result = add_prior_breakout_context(requests(closes=[100.+1e-11]), raw())
    assert result.prior_breakout_known.all()


def test_no_future_ohlcv_validation_and_prefix_suffix_invariance():
    frame = raw()
    query = requests()
    expected = add_prior_breakout_context(query, frame)
    frame.loc[frame.open_time.ge(at(21)), BAR_COLUMNS[1:]] = np.nan
    pd.testing.assert_frame_equal(expected, add_prior_breakout_context(query, frame))
    pd.testing.assert_frame_equal(expected, add_prior_breakout_context(query, frame.loc[frame.open_time.lt(at(21))]))


def test_future_valid_price_mutation_does_not_change_earlier_query_in_batch():
    frame = raw()
    query = requests([20,24])
    expected = add_prior_breakout_context(query, frame).iloc[[0]]
    frame.loc[frame.open_time.ge(at(21)), ["high","low"]] = [300.,1.]
    actual = add_prior_breakout_context(query, frame).iloc[[0]]
    pd.testing.assert_frame_equal(expected, actual)


def test_own_control_window_not_case_boundary_and_own_direction():
    frame = raw(42)
    own_control_window = frame.open_time.ge(at(21)) & frame.open_time.lt(at(41))
    frame.loc[own_control_window, ["high", "low"]] = [150., 80.]
    set_hour(frame,20,111.)
    set_hour(frame,41,79.)
    query = requests([20,41,41], [1,-1,1], [111.,79.,79.])
    query["population"] = ["case","control","control"]
    result = add_prior_breakout_context(query, frame)
    assert result.prior_breakout_gate_state.tolist() == ["accepted","accepted","abstain"]
    assert result.prior_breakout_low.tolist() == [90.,80.,80.]
    assert result.prior_breakout_high.tolist() == [110.,150.,150.]
    assert result.prior_breakout_window_start.tolist() == [at(0),at(21),at(21)]


def test_extra_raw_columns_and_opaque_segment_ids_not_used_as_continuity():
    frame = raw()
    frame["segment_id"] = np.arange(len(frame))*97
    frame["index"] = -1
    result = add_prior_breakout_context(requests(), frame)
    assert result.prior_breakout_known.all()
    assert result.prior_breakout_raw_segment_id.tolist() == [0]


def test_order_duplicate_query_times_indices_timezone_attrs_and_no_input_mutation():
    query = requests([24,20,24], [1,-1,1])
    query["signal_time"] = query.signal_time.dt.tz_convert("Asia/Shanghai")
    query.index = pd.Index([7,7,2], name="owner_index")
    query.attrs["source"] = {"owner": "unchanged"}
    original = query.copy(deep=True)
    frame = raw()
    original_frame = frame.copy(deep=True)
    result = add_prior_breakout_context(query, frame)
    pd.testing.assert_frame_equal(result[query.columns], original)
    pd.testing.assert_frame_equal(frame, original_frame)
    assert result.attrs == query.attrs
    assert list(result.columns) == list(query.columns)+BREAKOUT_COLUMNS


def test_empty_requests_and_empty_source_complete_schema():
    query = requests([])
    result = add_prior_breakout_context(query, pd.DataFrame())
    assert result.empty
    assert list(result.columns) == list(query.columns)+BREAKOUT_COLUMNS
    result = add_prior_breakout_context(requests(), raw(0)).iloc[0]
    assert result.prior_breakout_count == 0
    assert result.prior_breakout_reason == "no_source"
    assert result.prior_breakout_gate_state == "unknown"


def test_adding_later_request_cannot_change_earlier_no_source_reason():
    frame = raw()
    early = requests([-1])
    expected = add_prior_breakout_context(early, frame)
    batch = requests([-1,20])
    actual = add_prior_breakout_context(batch, frame).iloc[[0]]
    pd.testing.assert_frame_equal(expected, actual)


def test_exact_complete_k1_cutoff_one_minute_early_is_not_complete():
    frame = raw()
    frame = frame.loc[frame.open_time.lt(at(20)+pd.Timedelta(minutes=55))]
    result = add_prior_breakout_context(requests(), frame).iloc[0]
    assert result.prior_breakout_reason == "missing_signal_hour"
    assert result.prior_breakout_count == 20


@pytest.mark.parametrize("bad", [None, "2024-01-01", 1704067200000, pd.NaT, at(20)+pd.Timedelta(minutes=5)])
def test_invalid_or_shifted_signal_clock_rejected(bad):
    query = requests()
    query["signal_time"] = pd.Series([bad], dtype=object)
    with pytest.raises(ValueError):
        add_prior_breakout_context(query, raw())


def test_shifted_hour_still_must_match_own_close_and_decision():
    query = requests()
    query["signal_time"] = query.signal_time+pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="decision_time"):
        add_prior_breakout_context(query, raw())
    query["decision_time"] = query.decision_time+pd.Timedelta(hours=1)
    frame = raw()
    set_hour(frame,21,111.)
    with pytest.raises(ValueError, match="signal_close parity"):
        add_prior_breakout_context(query, frame)


@pytest.mark.parametrize("bad", [0, 2, True, np.nan, "1"])
def test_invalid_direction_rejected(bad):
    query = requests()
    query["direction"] = bad
    with pytest.raises(ValueError, match="direction"):
        add_prior_breakout_context(query, raw())


@pytest.mark.parametrize("bad", [0, -1, np.nan, np.inf, True])
def test_invalid_saved_close_rejected(bad):
    with pytest.raises(ValueError, match="signal_close"):
        add_prior_breakout_context(requests(closes=[bad]), raw())


@pytest.mark.parametrize("kind", ["duplicate", "unsorted", "numeric", "naive", "misaligned"])
def test_invalid_raw_clock_rejected(kind):
    frame = raw()
    if kind == "duplicate":
        frame = pd.concat([frame.iloc[[0]], frame], ignore_index=True)
    elif kind == "unsorted":
        frame = frame.iloc[::-1]
    elif kind == "numeric":
        frame["open_time"] = np.arange(len(frame))
    elif kind == "naive":
        frame["open_time"] = frame.open_time.dt.tz_localize(None)
    else:
        frame["open_time"] += pd.Timedelta(seconds=1)
    with pytest.raises(ValueError):
        add_prior_breakout_context(requests(), frame)


def test_invalid_completed_ohlcv_not_silently_unknown():
    frame = raw()
    frame.loc[0,"close"] = 200.
    with pytest.raises(ValueError, match="OHLC"):
        add_prior_breakout_context(requests(), frame)


@pytest.mark.parametrize("kind", ["collision", "duplicate_id", "null_id", "missing_close", "duplicate_column"])
def test_invalid_request_schema_or_id_rejected(kind):
    query = requests([20,21])
    if kind == "collision":
        query[BREAKOUT_COLUMNS[0]] = 0
    elif kind == "duplicate_id":
        query["event_id"] = "same"
    elif kind == "null_id":
        query.loc[0,"event_id"] = None
    elif kind == "missing_close":
        query = query.drop(columns="signal_close")
    else:
        query = pd.concat([query,query[["signal_close"]]],axis=1)
    with pytest.raises(ValueError):
        add_prior_breakout_context(query, raw())
