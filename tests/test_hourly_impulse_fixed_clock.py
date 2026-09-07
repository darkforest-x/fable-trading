"""Synthetic fixed-clock labels only; no archive prices or strategy runs."""
from copy import deepcopy
from decimal import Decimal, localcontext

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from yoyo.evaluation.hourly_impulse_fixed_clock import (
    HORIZONS_HOURS, LABEL_COLUMNS, build_fixed_clock_labels,
)


E = pd.Timestamp("2023-01-02T00:00:00Z")
FOLDS = {"F": ("2023-01-01T00:00:00Z", "2023-01-10T00:00:00Z")}


def fixture(direction=1):
    raw = pd.DataFrame({"open_time": pd.date_range(E-pd.Timedelta(hours=1), E+pd.Timedelta(hours=25), freq="5min"),
                        "open": "100"})
    requests = pd.DataFrame([dict(event_id="mother", decision_time=E, direction=direction, fold="F")], index=[17])
    return raw, requests


def at(raw, when, price):
    raw.loc[raw.open_time.eq(when), "open"] = price


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("hours", HORIZONS_HOURS)
def test_exact_endpoint_and_long_short_formula(direction, hours):
    raw, requests = fixture(direction)
    at(raw, E+pd.Timedelta(hours=hours), "101")
    result = build_fixed_clock_labels(raw, requests, FOLDS)
    row = result[result.horizon_hours.eq(hours)].iloc[0]
    assert len(result) == 4 and list(result.horizon_hours) == list(HORIZONS_HOURS)
    assert row.status == row.reason == "known"
    assert row.gross_markout == direction*.01
    assert row.cost_threshold_markout == float(Decimal(direction)*Decimal(".01")-Decimal(".002"))
    assert row.n_expected == row.n_observed == 12*hours+1
    assert row.endpoint_time == E+pd.Timedelta(hours=hours)
    assert row.role == ("primary" if hours == 4 else "descriptive")
    assert result.attrs["executable_pnl"] is False and result.attrs["independent_samples"] is False


@pytest.mark.parametrize("direction,endpoint", [(1, "100.2"), (-1, "99.8")])
def test_exact_20bp_is_zero_even_with_changed_global_decimal_context(direction, endpoint):
    raw, requests = fixture(direction)
    at(raw, E+pd.Timedelta(hours=4), endpoint)
    with localcontext() as ctx:
        ctx.prec = 3
        row = build_fixed_clock_labels(raw, requests, FOLDS).iloc[1]
    assert row.gross_markout == .002 and row.cost_threshold_markout == 0.


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("above", [False, True])
def test_nextafter_cost_boundary_keeps_real_sign(direction, above):
    raw, requests = fixture(direction)
    endpoint = np.nextafter(100+direction*.2, np.inf if above == (direction == 1) else -np.inf)
    at(raw, E+pd.Timedelta(hours=4), endpoint)
    row = build_fixed_clock_labels(raw, requests, FOLDS).iloc[1]
    assert (row.cost_threshold_markout > 0) == above and row.cost_threshold_markout != 0


@pytest.mark.parametrize("offset", [0, 5, 60, 120, 240, 720, 1440])
def test_missing_bar_only_invalidates_containing_horizons(offset):
    raw, requests = fixture()
    raw = raw.loc[raw.open_time.ne(E+pd.Timedelta(minutes=offset))]
    result = build_fixed_clock_labels(raw, requests, FOLDS)
    for row in result.itertuples():
        affected = offset <= row.horizon_hours*60
        assert row.reason == ("missing_bar" if affected else "known")
        assert row.n_observed == row.n_expected-int(affected)
        assert pd.isna(row.gross_markout) == affected
        assert pd.isna(row.cost_threshold_markout) == affected


@pytest.mark.parametrize("bad", [None, np.nan, np.inf, -np.inf, 0, -1, True, np.bool_(True), "bad", "NaN", "Infinity", "1e1000", "1e-1000"])
@pytest.mark.parametrize("offset", [0, 120, 240])
def test_bad_open_is_unknown_not_a_drop_or_zero_return(bad, offset):
    raw, requests = fixture()
    at(raw, E+pd.Timedelta(minutes=offset), bad)
    result = build_fixed_clock_labels(raw, requests, FOLDS)
    assert len(result) == 4
    for row in result.itertuples():
        affected = offset <= row.horizon_hours*60
        assert row.reason == ("invalid_open" if affected else "known")
        assert row.n_observed == row.n_expected
        assert pd.isna(row.gross_markout) == affected


def test_missing_takes_precedence_but_observed_count_includes_bad_open():
    raw, requests = fixture()
    at(raw, E+pd.Timedelta(minutes=10), np.nan)
    raw = raw.loc[raw.open_time.ne(E+pd.Timedelta(minutes=5))]
    result = build_fixed_clock_labels(raw, requests, FOLDS)
    assert result.reason.eq("missing_bar").all()
    assert result.n_observed.eq(result.n_expected-1).all()


@pytest.mark.parametrize("end_minutes,known", [(60, []), (65, [1]), (240, [1]), (245, [1, 4]), (1440, [1, 4, 12]), (1445, [1, 4, 12, 24])])
def test_endpoint_must_be_strictly_before_own_fold_end(end_minutes, known):
    raw, requests = fixture()
    folds = {"F": (E, E+pd.Timedelta(minutes=end_minutes))}
    result = build_fixed_clock_labels(raw, requests, folds)
    for row in result.itertuples():
        if row.horizon_hours in known:
            assert row.status == "known"
        else:
            assert row.reason == "endpoint_outside_fold" and pd.isna(row.n_observed)
            assert pd.isna(row.entry_open) and pd.isna(row.endpoint_open)


def test_fold_guard_does_not_borrow_adjacent_fold_quote():
    raw, requests = fixture()
    folds = {"F": (E, E+pd.Timedelta(hours=4)), "G": (E+pd.Timedelta(hours=4), E+pd.Timedelta(days=2))}
    row = build_fixed_clock_labels(raw, requests, folds).iloc[1]
    assert row.reason == "endpoint_outside_fold" and pd.isna(row.endpoint_open)


def test_early_exit_stop_outcome_and_all_hlc_are_irrelevant_to_labels():
    raw, requests = fixture()
    expected = build_fixed_clock_labels(raw, requests, FOLDS)
    requests = requests.assign(initial_stop=99., exit_time=E+pd.Timedelta(minutes=5), closed=True,
                               net_return=123., max_favourable_r=999., ma_side=-1, outcome="hard_stop")
    raw = raw.assign(high=np.nan, low=-999, close="not a price", volume=np.inf, segment_id=np.nan)
    assert_frame_equal(build_fixed_clock_labels(raw, requests, FOLDS), expected)
    requests.loc[:, "net_return"] = -999
    requests.loc[:, "exit_time"] = E+pd.Timedelta(days=99)
    requests.loc[:, "closed"] = False
    assert_frame_equal(build_fixed_clock_labels(raw, requests, FOLDS), expected)


def test_future_after_max_clock_and_preentry_prices_cannot_change_any_label():
    raw, requests = fixture()
    expected = build_fixed_clock_labels(raw, requests, FOLDS)
    raw.loc[raw.open_time.lt(E)|raw.open_time.gt(E+pd.Timedelta(hours=24)), "open"] = np.nan
    assert_frame_equal(build_fixed_clock_labels(raw, requests, FOLDS), expected)
    raw = raw[raw.open_time.between(E, E+pd.Timedelta(hours=24))]
    assert_frame_equal(build_fixed_clock_labels(raw, requests, FOLDS), expected)


@pytest.mark.parametrize("hours", HORIZONS_HOURS)
def test_prefix_at_exact_endpoint_preserves_that_label(hours):
    raw, requests = fixture()
    at(raw, E+pd.Timedelta(hours=hours), "98.25")
    full = build_fixed_clock_labels(raw, requests, FOLDS)
    prefix = build_fixed_clock_labels(raw[raw.open_time.le(E+pd.Timedelta(hours=hours))], requests, FOLDS)
    assert_frame_equal(full[full.horizon_hours.eq(hours)], prefix[prefix.horizon_hours.eq(hours)])


@pytest.mark.parametrize("bad", [None, np.nan, 12, True, "", " "])
def test_invalid_request_ids_rejected(bad):
    raw, requests = fixture()
    requests["event_id"] = bad
    with pytest.raises(ValueError, match="event_id"):
        build_fixed_clock_labels(raw, requests, FOLDS)


def test_duplicate_request_ids_rejected():
    raw, requests = fixture()
    with pytest.raises(ValueError, match="event_id"):
        build_fixed_clock_labels(raw, pd.concat([requests, requests]), FOLDS)


@pytest.mark.parametrize("bad", [True, False, np.bool_(True), 0, 2, -2, np.nan, np.inf, "1", "-1", None])
def test_invalid_or_boolean_direction_rejected(bad):
    raw, requests = fixture()
    requests["direction"] = bad
    with pytest.raises(ValueError, match="direction"):
        build_fixed_clock_labels(raw, requests, FOLDS)


@pytest.mark.parametrize("which", ["raw", "request"])
@pytest.mark.parametrize("bad", [None, pd.NaT, 1672617600000, True, "2023-01-02", "2023-01-02T08:00:00+08:00", "2023-01-02T00:01:00Z", "2023-01-02T00:00:00.000000001Z"])
def test_invalid_naive_numeric_or_offgrid_time_rejected(which, bad):
    raw, requests = fixture()
    frame, field = (raw, "open_time") if which == "raw" else (requests, "decision_time")
    frame[field] = frame[field].astype(object)
    frame.iloc[0, frame.columns.get_loc(field)] = bad
    with pytest.raises(ValueError): build_fixed_clock_labels(raw, requests, FOLDS)


@pytest.mark.parametrize("mutation", ["reverse", "duplicate"])
def test_raw_time_order_and_duplicates_rejected(mutation):
    raw, requests = fixture()
    raw = raw.iloc[::-1] if mutation == "reverse" else pd.concat([raw.iloc[:1], raw])
    with pytest.raises(ValueError, match="sorted and unique"):
        build_fixed_clock_labels(raw, requests, FOLDS)


@pytest.mark.parametrize("folds", [None, {}, {"F": E}, {"F": (E,)}, {"": (E,E+pd.Timedelta(days=1))},
    {"F": (E,E)}, {"F": (E+pd.Timedelta(days=1),E)}, {"F": ("2023-01-01", "2023-01-10")},
    {"F": (E,E+pd.Timedelta(days=2)), "G": (E+pd.Timedelta(days=1),E+pd.Timedelta(days=3))}])
def test_explicit_nonoverlapping_fold_bounds_required(folds):
    raw, requests = fixture()
    with pytest.raises(ValueError): build_fixed_clock_labels(raw, requests, folds)


@pytest.mark.parametrize("fold,entry", [("unknown", E), (None, E), ("F", "2022-12-31T23:55:00Z"), ("F", "2023-01-10T00:00:00Z")])
def test_request_must_be_inside_its_known_fold(fold, entry):
    raw, requests = fixture()
    requests["fold"], requests["decision_time"] = fold, entry
    with pytest.raises(ValueError): build_fixed_clock_labels(raw, requests, FOLDS)


def test_all_empty_missing_rows_keep_schema_without_mutating_inputs():
    raw, requests = fixture()
    raw.attrs["source"] = {"nested": [1]}; requests.attrs["metadata"] = {"nested": [2]}
    old_raw, old_requests = deepcopy(raw), deepcopy(requests)
    result = build_fixed_clock_labels(raw.iloc[:0], requests, FOLDS)
    assert len(result) == 4 and result.reason.eq("missing_bar").all()
    assert result.n_observed.eq(0).all() and result.gross_markout.isna().all()
    empty = build_fixed_clock_labels(raw, requests.iloc[:0], FOLDS)
    assert list(empty) == list(LABEL_COLUMNS) and empty.empty
    assert str(empty.decision_time.dtype) == "datetime64[ns, UTC]"
    assert_frame_equal(raw, old_raw); assert_frame_equal(requests, old_requests)
    assert raw.attrs == old_raw.attrs and requests.attrs == old_requests.attrs


def test_all_251_mothers_each_keep_four_rows_not_independent_sample_count():
    raw, request = fixture()
    requests = pd.concat([request.assign(event_id="event%d" % i, direction=1 if i%2 else -1) for i in range(251)])
    result = build_fixed_clock_labels(raw, requests, FOLDS)
    assert len(result) == 1004 and result.event_id.nunique() == 251
    assert result.groupby("event_id").size().eq(4).all()
    assert result.role.eq("primary").sum() == 251
    assert result.attrs["independent_samples"] is False


def test_own_control_clock_not_mother_or_old_exit_clock_and_request_order_kept():
    raw, request = fixture()
    at(raw, E+pd.Timedelta(hours=1), "110")
    control = request.assign(event_id="control", decision_time=E+pd.Timedelta(hours=1))
    result = build_fixed_clock_labels(raw, pd.concat([control, request]), FOLDS)
    assert list(result.event_id) == ["control"]*4+["mother"]*4
    assert result.iloc[0].entry_open == 110 and result.iloc[4].entry_open == 100
    assert result.iloc[0].gross_markout == float(Decimal(-10)/Decimal(110))


@pytest.mark.parametrize("offset", [5, 10, 55])
def test_valid_non_hourly_entry_uses_its_own_exact_grid_endpoint(offset):
    raw, requests = fixture()
    entry = E+pd.Timedelta(minutes=offset)
    requests["decision_time"] = entry
    at(raw, entry, "80")
    at(raw, entry+pd.Timedelta(hours=4), "82")
    result = build_fixed_clock_labels(raw, requests, FOLDS)
    assert result.iloc[1].decision_time == entry
    assert result.iloc[1].endpoint_time == entry+pd.Timedelta(hours=4)
    assert result.iloc[1].gross_markout == .025


def test_gap_cannot_be_compensated_by_extra_neighbouring_quotes():
    raw, requests = fixture()
    raw = raw.loc[raw.open_time.ne(E+pd.Timedelta(minutes=35))]
    row = build_fixed_clock_labels(raw, requests, FOLDS).iloc[0]
    assert row.n_observed == 12 and row.n_expected == 13
    assert row.entry_open == row.endpoint_open == 100
    assert row.reason == "missing_bar" and pd.isna(row.gross_markout)


def test_finite_quotes_with_nonfinite_float_markout_fail_unknown():
    raw, requests = fixture()
    at(raw, E, "1e-308"); at(raw, E+pd.Timedelta(hours=4), "1e308")
    row = build_fixed_clock_labels(raw, requests, FOLDS).iloc[1]
    assert row.reason == "nonfinite_markout" and pd.isna(row.gross_markout)


@pytest.mark.parametrize("which", ["raw", "request"])
def test_missing_or_duplicate_input_columns_rejected(which):
    raw, requests = fixture()
    if which == "raw": raw = raw.drop(columns="open")
    else: requests = requests.drop(columns="direction")
    with pytest.raises(ValueError): build_fixed_clock_labels(raw, requests, FOLDS)
    raw, requests = fixture()
    if which == "raw": raw = pd.concat([raw, raw[["open"]]],axis=1)
    else: requests = pd.concat([requests, requests[["direction"]]],axis=1)
    with pytest.raises(ValueError): build_fixed_clock_labels(raw, requests, FOLDS)
