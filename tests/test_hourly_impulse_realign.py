"""Synthetic first-alignment, timing, and finite-domain causal invariants."""

from itertools import product

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse_realign import ADDED_COLUMNS, STATUS_COLUMNS, build_realign_requests


START = pd.Timestamp("2024-01-01", tz="UTC")
BASE = START + pd.Timedelta(hours=1)


def fixture(*, direction=1, periods=100):
    times = pd.date_range(START-pd.Timedelta(minutes=5), periods=periods, freq="5min")
    raw = pd.DataFrame({"open_time": times, "open": 100.0, "high": 101.0,
                        "low": 99.0, "close": 100.0, "segment_id": 7})
    mg = raw.copy()
    mg["ma"], mg["ma_side"], mg["segment_id"] = 100.0, -direction, 91
    mg.attrs.update(bar_minutes=5, ma_kind="SMA", ma_length=40)
    requests = pd.DataFrame({
        "event_id": ["a"], "decision_time": [BASE], "mother_decision_time": [START],
        "mother_deadline": [START+pd.Timedelta(hours=72)], "direction": [direction],
        "initial_stop": [90.0 if direction == 1 else 110.0], "signal_atr": [5.0],
        "signal_time": [START-pd.Timedelta(hours=1)], "wait_hours": [1.0],
        "fold": ["2024H1"], "body_ratio": [.75], "sentinel": ["immutable"],
    })
    return raw, mg, requests


def colour(mg, boundary, value):
    mg.loc[mg.open_time.eq(boundary-pd.Timedelta(minutes=5)), "ma_side"] = value


def run(raw, mg, requests, cutoff=None):
    return build_realign_requests(raw, mg, requests, observed_through=cutoff or START+pd.Timedelta(hours=8))


@pytest.mark.parametrize("direction", [1, -1])
def test_already_aligned_preserves_original_request_and_mirror(direction):
    raw, mg, requests = fixture(direction=direction)
    colour(mg, BASE, direction)
    result, status = run(raw, mg, requests, BASE)
    pd.testing.assert_frame_equal(result[requests.columns], requests)
    assert status.status.tolist() == ["request_emitted"]
    assert result.realign_initial_state.tolist() == ["aligned"]
    assert result.realign_wait_minutes.tolist() == [0.0]
    assert result.total_wait_minutes.tolist() == [60.0]
    assert result.realign_confirmation_bar_open.iloc[0] == BASE-pd.Timedelta(minutes=5)
    assert result.realign_confirmation_available_at.iloc[0] == BASE


@pytest.mark.parametrize("direction", [1, -1])
def test_first_not_best_alignment_and_fractional_wait(direction):
    raw, mg, requests = fixture(direction=direction)
    first = BASE+pd.Timedelta(minutes=15)
    colour(mg, first, direction)
    colour(mg, first+pd.Timedelta(minutes=10), direction)
    result, status = run(raw, mg, requests)
    assert result.decision_time.iloc[0] == first
    assert result.wait_hours.iloc[0] == 1.25
    assert result.realign_wait_minutes.iloc[0] == 15
    assert result.total_wait_minutes.iloc[0] == 75
    assert status.terminal_time.iloc[0] == first
    assert result.realign_initial_state.iloc[0] == "opposite"


def test_colour_uses_supplied_hl2_ma_side_not_body_or_close_side():
    raw, mg, requests = fixture()
    index = mg.index[mg.open_time.eq(BASE-pd.Timedelta(minutes=5))][0]
    mg.loc[index, ["ma", "high", "low", "close", "ma_side"]] = [100, 101, 99, 99.2, 1]
    raw.loc[index, ["open", "high", "low", "close"]] = [100.8, 101, 99, 99.2]
    result, _ = run(raw, mg, requests)
    assert result.decision_time.iloc[0] == BASE


@pytest.mark.parametrize("stop", [101.0, -1.0, np.nan])
def test_first_alignment_emitted_once_despite_old_or_new_invalid_risk(stop):
    raw, mg, requests = fixture()
    requests["initial_stop"] = stop
    first = BASE+pd.Timedelta(minutes=10)
    colour(mg, first, 1)
    colour(mg, first+pd.Timedelta(minutes=5), 1)
    result, status = run(raw, mg, requests)
    assert len(result) == 1 and result.decision_time.iloc[0] == first
    assert status.status.iloc[0] == "request_emitted"
    pd.testing.assert_series_equal(result.initial_stop, requests.initial_stop)


def test_crossing_k1_stop_while_flat_does_not_cancel():
    raw, mg, requests = fixture()
    raw.loc[raw.open_time.eq(BASE), "low"] = 80.0
    colour(mg, BASE+pd.Timedelta(minutes=10), 1)
    result, status = run(raw, mg, requests)
    assert status.status.iloc[0] == "request_emitted"
    assert result.initial_stop.iloc[0] == 90
    assert result.decision_time.iloc[0] == BASE+pd.Timedelta(minutes=10)


@pytest.mark.parametrize("base_at_deadline", [False, True])
@pytest.mark.parametrize("aligned", [False, True])
def test_deadline_inclusive_confirmation_before_expiration(base_at_deadline, aligned):
    raw, mg, requests = fixture()
    deadline = START+pd.Timedelta(hours=8)
    if base_at_deadline:
        requests["decision_time"], requests["wait_hours"] = deadline, 8.0
    if aligned:
        colour(mg, deadline, 1)
    result, status = run(raw, mg, requests, deadline)
    assert status.terminal_time.iloc[0] == deadline
    assert status.status.iloc[0] == ("request_emitted" if aligned else "expired_no_alignment")
    assert len(result) == int(aligned)
    if aligned:
        assert result.wait_hours.iloc[0] == 8


def test_alignment_after_deadline_cannot_rescue_known_expiration():
    raw, mg, requests = fixture()
    colour(mg, START+pd.Timedelta(hours=8, minutes=5), 1)
    result, status = run(raw, mg, requests, START+pd.Timedelta(hours=8, minutes=10))
    assert result.empty and status.status.iloc[0] == "expired_no_alignment"


@pytest.mark.parametrize("cutoff", [BASE-pd.Timedelta(minutes=1), BASE, BASE+pd.Timedelta(minutes=4),
                                    START+pd.Timedelta(hours=7, minutes=59)])
def test_incomplete_observation_is_unknown_end_not_known_expiry(cutoff):
    raw, mg, requests = fixture()
    result, status = run(raw, mg, requests, cutoff)
    assert result.empty and status.status.iloc[0] == "censored_realign_end"
    assert status.terminal_time.iloc[0] == cutoff


@pytest.mark.parametrize("at_base", [False, True])
@pytest.mark.parametrize("defect,expected", [
    ("missing_raw", "censored_realign_gap"),
    ("raw_segment", "censored_realign_gap"),
    ("missing_management", "censored_realign_colour"),
    ("invalid_management_side", "censored_realign_colour"),
    ("invalid_ma", "censored_realign_colour"),
    ("invalid_management_hlc", "censored_realign_colour"),
    ("unknown_management_segment", "censored_realign_colour"),
    ("invalid_completed_raw", "censored_realign_colour"),
    ("invalid_open", "censored_realign_colour"),
    ("unknown_raw_segment", "censored_realign_colour"),
])
def test_unknown_first_observation_censors_not_skip_to_later_alignment(at_base, defect, expected):
    raw, mg, requests = fixture()
    boundary = BASE if at_base else BASE+pd.Timedelta(minutes=10)
    prior_index = raw.index[raw.open_time.eq(boundary-pd.Timedelta(minutes=5))][0]
    now_index = prior_index+1
    colour(mg, boundary+pd.Timedelta(minutes=5), 1)
    if defect == "missing_raw": raw = raw.drop(index=now_index)
    elif defect == "raw_segment": raw.loc[now_index:, "segment_id"] = 8
    elif defect == "missing_management": mg = mg.drop(index=prior_index)
    elif defect == "invalid_management_side": mg.loc[prior_index, "ma_side"] = 0
    elif defect == "invalid_ma": mg.loc[prior_index, "ma"] = np.inf
    elif defect == "invalid_management_hlc": mg.loc[prior_index, "low"] = 200
    elif defect == "unknown_management_segment": mg.loc[prior_index, "segment_id"] = np.nan
    elif defect == "invalid_completed_raw": raw.loc[prior_index, "close"] = 200
    elif defect == "invalid_open": raw.loc[now_index, "open"] = np.nan
    else: raw.loc[now_index, "segment_id"] = np.nan
    result, status = run(raw, mg, requests)
    assert result.empty
    assert status.status.iloc[0] == expected
    assert status.terminal_time.iloc[0] == boundary
    assert status.realign_initial_state.iloc[0] == ("unknown" if at_base else "opposite")


def test_management_segment_switch_is_not_compared_to_raw_namespace():
    raw, mg, requests = fixture()
    colour(mg, BASE+pd.Timedelta(minutes=10), 1)
    result, _ = run(raw, mg, requests)
    assert len(result) == 1  # raw segment 7, management segment 91 are legitimate.
    mg.loc[mg.open_time.ge(BASE), "segment_id"] = 92
    result, status = run(raw, mg, requests)
    assert result.empty and status.reason.iloc[0] == "management_segment_changed"
    assert status.terminal_time.iloc[0] == BASE+pd.Timedelta(minutes=5)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 0, 2, True, "1"])
def test_bad_side_is_unknown_not_opposite(value):
    raw, mg, requests = fixture()
    mg["ma_side"] = mg.ma_side.astype(object)
    colour(mg, BASE, value)
    result, status = run(raw, mg, requests)
    assert result.empty and status.status.iloc[0] == "censored_realign_colour"
    assert pd.isna(status.realign_initial_side.iloc[0])


def test_empty_sources_are_explicit_unknown_not_expiration():
    raw, mg, requests = fixture()
    assert run(raw.iloc[:0], mg, requests)[1].status.iloc[0] == "censored_realign_gap"
    assert run(raw, mg.iloc[:0], requests)[1].status.iloc[0] == "censored_realign_colour"


def test_competing_mothers_order_index_attrs_and_input_immutability():
    raw, mg, requests = fixture()
    requests = pd.concat([requests, requests.assign(event_id="b", direction=-1)], ignore_index=True).iloc[::-1]
    requests.index = pd.Index([7, 7], name="original_row")
    requests.attrs["lineage"] = "synthetic"
    originals = [frame.copy(deep=True) for frame in (raw, mg, requests)]
    colour(mg, BASE+pd.Timedelta(minutes=10), 1)
    originals[1] = mg.copy(deep=True)
    result, status = run(raw, mg, requests)
    assert result.event_id.tolist() == ["b", "a"]
    assert status.event_id.tolist() == ["b", "a"]
    assert result.decision_time.tolist() == [BASE, BASE+pd.Timedelta(minutes=10)]
    assert result.index.equals(requests.index) and result.attrs == requests.attrs
    unchanged = [column for column in requests.columns if column not in ("decision_time", "wait_hours")]
    pd.testing.assert_frame_equal(result[unchanged], requests[unchanged])
    for frame, original in zip((raw, mg, requests), originals):
        pd.testing.assert_frame_equal(frame, original)


def test_empty_requests_return_complete_schema():
    raw, mg, requests = fixture()
    result, status = run(raw, mg, requests.iloc[:0])
    assert result.empty and status.empty
    assert result.columns.tolist() == requests.columns.tolist()+ADDED_COLUMNS
    assert status.columns.tolist() == STATUS_COLUMNS
    assert str(status.terminal_time.dtype) == "datetime64[ns, UTC]"
    assert str(result.realign_confirmation_side.dtype) == "Int64"


def test_timezone_normalization_preserves_original_mother_values():
    raw, mg, requests = fixture()
    colour(mg, BASE, 1)
    for frame, names in ((raw, ["open_time"]), (mg, ["open_time"]),
                         (requests, ["decision_time", "mother_decision_time", "mother_deadline"])):
        for name in names: frame[name] = frame[name].dt.tz_convert("Asia/Shanghai")
    result, status = run(raw, mg, requests, BASE.tz_convert("Asia/Shanghai"))
    assert result.decision_time.iloc[0] == BASE
    pd.testing.assert_series_equal(result.mother_decision_time, requests.mother_decision_time)
    assert status.realign_confirmation_available_at.iloc[0] == BASE


@pytest.mark.parametrize("problem", ["duplicate_raw", "duplicate_mg", "unsorted_raw", "off_grid",
                                    "numeric_raw", "numeric_object", "numeric_request", "numeric_cutoff",
                                    "duplicate_id", "direction", "deadline", "late_base", "collision", "stale_context", "wrong_ma"])
def test_invalid_contracts_fail_explicitly(problem):
    raw, mg, requests = fixture()
    cutoff = START+pd.Timedelta(hours=8)
    if problem == "duplicate_raw": raw.loc[1, "open_time"] = raw.loc[0, "open_time"]
    elif problem == "duplicate_mg": mg.loc[1, "open_time"] = mg.loc[0, "open_time"]
    elif problem == "unsorted_raw": raw = raw.iloc[::-1]
    elif problem == "off_grid": requests["decision_time"] += pd.Timedelta(seconds=1)
    elif problem == "numeric_raw": raw["open_time"] = raw.open_time.astype("int64")
    elif problem == "numeric_object": raw["open_time"] = raw.open_time.astype("int64").astype(object)
    elif problem == "numeric_request": requests["decision_time"] = 1700000000
    elif problem == "numeric_cutoff": cutoff = 1700000000
    elif problem == "duplicate_id": requests = pd.concat([requests, requests])
    elif problem == "direction": requests["direction"] = True
    elif problem == "deadline": requests["mother_deadline"] += pd.Timedelta(minutes=5)
    elif problem == "late_base": requests["decision_time"] = START+pd.Timedelta(hours=8, minutes=5)
    elif problem == "collision": requests["realign_initial_state"] = "aligned"
    elif problem == "stale_context": requests["ltf_entry_state"] = "aligned"
    else: mg.attrs["ma_length"] = 20
    with pytest.raises(ValueError): run(raw, mg, requests, cutoff)


@pytest.mark.parametrize("direction", [1, -1])
def test_exhaustive_short_colour_sequences_first_alignment_prefix_and_future_invariance(direction):
    # Exhaustive short-domain oracle, not random sampling or a new dependency.
    for pattern in product((-1, 1), repeat=4):
        raw, mg, requests = fixture(direction=direction)
        boundaries = [BASE+pd.Timedelta(minutes=5*i) for i in range(4)]
        for boundary, side in zip(boundaries, pattern): colour(mg, boundary, side)
        expected = next((time for time, side in zip(boundaries, pattern) if side == direction), None)
        cutoff = boundaries[-1]
        result, status = run(raw, mg, requests, cutoff)
        if expected is None:
            assert result.empty and status.status.iloc[0] == "censored_realign_end"
            terminal = cutoff
        else:
            assert result.decision_time.tolist() == [expected]
            terminal = expected
        # Full history with future invalid prices must equal a source prefix.
        future_raw, future_mg = raw.copy(), mg.copy()
        for name in ("high", "low", "close"):
            future_raw.loc[future_raw.open_time.ge(terminal), name] = np.nan
        future_raw.loc[future_raw.open_time.gt(terminal), "open"] = np.nan
        future_mg.loc[future_mg.open_time.ge(terminal), ["ma_side", "ma", "high", "low", "close"]] = np.nan
        mutated = run(future_raw, future_mg, requests, cutoff)
        prefix = run(future_raw.loc[future_raw.open_time.le(terminal)],
                     future_mg.loc[future_mg.open_time.lt(terminal)], requests, cutoff)
        for actual in (mutated, prefix):
            pd.testing.assert_frame_equal(result, actual[0])
            pd.testing.assert_frame_equal(status, actual[1])


def test_ongoing_bar_at_cutoff_cannot_supply_confirmation_or_invalidation():
    raw, mg, requests = fixture()
    cutoff = BASE+pd.Timedelta(minutes=7)
    colour(mg, BASE+pd.Timedelta(minutes=10), 1)
    raw.loc[raw.open_time.ge(BASE+pd.Timedelta(minutes=5)), ["high", "low", "close"]] = np.nan
    result, status = run(raw, mg, requests, cutoff)
    assert result.empty and status.status.iloc[0] == "censored_realign_end"
    assert status.terminal_time.iloc[0] == cutoff
