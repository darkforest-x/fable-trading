"""Synthetic V25 support tests: no I/O, real prices, labels or outcomes."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse_structure import add_hourly_structure_state
from yoyo.evaluation.hourly_impulse_structure_event_support import (
    COUNT_COLUMNS, DEFAULT_FOLDS, EVENT_COLUMNS, GATE, MATCHED_COLUMNS,
    add_structure_event_context, build_structure_event_support,
    validate_hourly_trace,
)


START = pd.Timestamp("2024-01-01T00:00:00Z")
HOUR = pd.Timedelta(hours=1)


def hourly(n=55, direction=1):
    frame = pd.DataFrame({"open_time": pd.date_range(START, periods=n, freq="h"),
                          "open": 100., "high": 101., "low": 99., "close": 100.})
    frame.loc[10, ["high", "low"]] = [110., 90.]
    frame.loc[21, ["high", "close"]] = [112., 111.]
    frame.loc[23, ["high", "close"]] = [112., 111.]
    frame.loc[24, ["low", "close"]] = [88., 89.]
    if direction == -1:
        frame[["open", "high", "low", "close"]] = np.column_stack((
            200-frame.open, 200-frame.low, 200-frame.high, 200-frame.close))
    return frame


def requests(positions, directions=None, signal_close=True):
    if directions is None:
        directions = [1]*len(positions)
    frame = pd.DataFrame({"event_id": ["m%d" % i for i in range(len(positions))],
                          "signal_time": [START+i*HOUR for i in positions],
                          "decision_time": [START+(i+1)*HOUR for i in positions],
                          "direction": directions})
    if signal_close:
        source = hourly()
        frame["signal_close"] = [source.close.iloc[i] if i < len(source) else 100. for i in positions]
    return frame


def support_inputs(unknown_control=False, unmatched=True):
    cases = requests([24, 30] if unmatched else [24], [-1, -1] if unmatched else [-1])
    cases["fold"], cases["request_kind"] = "2024H1", "case"
    cases["mother_id"], cases["control_slot"] = cases.event_id, pd.NA
    cases["mother_month"] = "2024-01"
    cases["matched_support"] = [True, False] if unmatched else [True]
    controls = requests([20 if unknown_control else 23, 25, 26], [-1]*3, signal_close=False)
    controls["event_id"] = ["m0:c%d" % i for i in range(3)]
    controls["fold"], controls["request_kind"], controls["mother_id"] = "2024H1", "control", "m0"
    controls["control_slot"], controls["mother_month"], controls["matched_support"] = range(3), "2024-01", True
    assignments = pd.DataFrame({"event_id": cases.event_id, "matched_support": cases.matched_support,
                                "assigned_controls": [3, 0] if unmatched else [3]})
    allocation = pd.DataFrame({"event_id": "m0", "candidate_id": controls.decision_time.map(lambda t: t.isoformat()),
                               "candidate_time": controls.decision_time, "control_slot": range(3),
                               "control_event_id": controls.event_id})
    return cases, controls, add_hourly_structure_state(hourly()), assignments, allocation


@pytest.mark.parametrize("direction", [-1, 1])
def test_only_first_establishing_and_reversal_event(direction):
    data = hourly(direction=direction)
    req = requests([20, 21, 22, 23, 24], [direction]*5, signal_close=False)
    out = add_structure_event_context(req, add_hourly_structure_state(data))
    assert out[GATE].tolist() == ["unknown", "accepted", "abstain", "abstain", "abstain"]
    assert out.structure_event_reason.tolist() == ["no_confirmed_break", "directional_break", "no_current_break", "no_current_break", "opposite_break"]
    assert out.structure_gate_state.tolist() == ["unknown", "accepted", "accepted", "accepted", "abstain"]
    opposite = req.iloc[[-1]].copy(); opposite["direction"] = -direction
    assert add_structure_event_context(opposite, add_hourly_structure_state(data))[GATE].iloc[0] == "accepted"


def test_pivots_without_established_state_remain_unknown_and_confirmation_equality_allowed():
    trace = add_hourly_structure_state(hourly())
    out = add_structure_event_context(requests([19, 20, 21]), trace)
    assert out[GATE].tolist() == ["unknown", "unknown", "accepted"]
    assert out.structure_high_confirmed_at.iloc[1] == out.decision_time.iloc[1]
    assert out.structure_high_origin.iloc[1]+11*HOUR == out.structure_high_confirmed_at.iloc[1]
    assert out.structure_event_reason.iloc[0] == "warmup"


def test_missing_exact_hour_no_asof_and_gap_resets():
    data = hourly().drop(index=22)
    trace = add_hourly_structure_state(data)
    out = add_structure_event_context(requests([21, 22, 23, 24]), trace)
    assert out[GATE].tolist() == ["accepted", "unknown", "unknown", "unknown"]
    assert out.structure_reason.iloc[1] == "missing_signal_hour"
    assert out.structure_reason.iloc[2] == "warmup"
    assert out.structure_count.iloc[2] == 1
    assert pd.isna(out.structure_high.iloc[2])


def test_empty_trace_and_own_hour_after_trace_preserve_unknown():
    trace = add_hourly_structure_state(hourly().iloc[:0])
    req = requests([21, 60])
    out = add_structure_event_context(req, trace)
    assert out.structure_reason.tolist() == ["no_source", "no_source"]
    assert out.structure_event_known.tolist() == [False, False]
    assert out.structure_state.isna().all()
    assert out.structure_available_at.tolist() == req.decision_time.tolist()
    assert add_structure_event_context(req.iloc[[1]], add_hourly_structure_state(hourly())).structure_reason.iloc[0] == "missing_signal_hour"


def test_original_columns_index_attrs_preserved_and_outcomes_not_consumed():
    req = requests([21, 22]);req.index = [8, 8];req.attrs = {"source": {"tag": "immutable"}}
    req["old_net_return"] = [object(), {"unread": True}]
    req["old_exit_time"] = ["not-a-clock", None]
    trace = add_hourly_structure_state(hourly());trace["future_outcome"] = [object()]*len(trace)
    before = req.copy(deep=True); before_attrs = deepcopy(req.attrs); source = trace.copy(deep=True)
    out = add_structure_event_context(req, trace)
    assert_frame_equal(out[req.columns], before)
    assert_frame_equal(req, before);assert_frame_equal(trace, source)
    assert out.attrs == req.attrs == before_attrs
    assert out.index.tolist() == [8, 8]


def test_future_suffix_and_truncation_preserve_earlier_context():
    data = hourly();req = requests([20, 21, 24])
    full = add_structure_event_context(req, add_hourly_structure_state(data))
    truncated = add_structure_event_context(req, add_hourly_structure_state(data.iloc[:25]))
    data.loc[30:, ["open", "high", "low", "close"]] = [400., 500., 300., 450.]
    changed = add_structure_event_context(req, add_hourly_structure_state(data))
    assert_frame_equal(full, truncated);assert_frame_equal(full, changed)


@pytest.mark.parametrize("field,value", [
    ("structure_available_at", START+23*HOUR),
    ("structure_high_confirmed_at", START+23*HOUR),
    ("structure_high_origin", START+11*HOUR),
    ("structure_state", -1), ("structure_state_before", 1),
    ("structure_count", 999), ("structure_segment_id", 9),
    ("structure_break_direction", 0), ("structure_break_on_k1", False),
    ("structure_known", False), ("structure_reason", "warmup"),
    ("structure_signal_close", 110.), ("structure_high", 109.),
    ("structure_last_break_available_at", START+23*HOUR),
])
def test_trace_semantic_corruption_not_silently_repaired(field, value):
    trace = add_hourly_structure_state(hourly());trace.loc[21, field] = value
    with pytest.raises(ValueError, match="semantics mismatch"):
        add_structure_event_context(requests([21]), trace)


@pytest.mark.parametrize("field,value", [
    ("structure_known", 1), ("structure_break_on_k1", "True"),
    ("structure_count", True), ("structure_state", True),
    ("structure_high", True), ("structure_known", None),
])
def test_numeric_bool_string_flag_confusion_rejected(field, value):
    trace = add_hourly_structure_state(hourly());trace[field] = trace[field].astype(object);trace.loc[21, field] = value
    with pytest.raises(ValueError):validate_hourly_trace(trace)


@pytest.mark.parametrize("field,value", [
    ("signal_time", "2024-01-01 21:00:00"), ("signal_time", 1704142800),
    ("signal_time", "2024-01-01T21:00:00.000000001Z"),
    ("signal_time", "2024-01-01T22:00:00+01:00"),
    ("decision_time", START+23*HOUR), ("direction", True),
    ("direction", "1"), ("direction", np.nan), ("direction", 0),
    ("event_id", None), ("event_id", ""), ("event_id", 1),
    ("signal_close", True), ("signal_close", np.inf), ("signal_close", 0),
])
def test_request_validation(field, value):
    req = requests([21]);req[field] = pd.Series([value], dtype=object)
    with pytest.raises(ValueError):add_structure_event_context(req, add_hourly_structure_state(hourly()))


def test_duplicate_request_trace_keys_and_reserved_columns_rejected():
    req = requests([21]);trace = add_hourly_structure_state(hourly())
    with pytest.raises(ValueError):add_structure_event_context(pd.concat([req, req]), trace)
    with pytest.raises(ValueError):add_structure_event_context(req, pd.concat([trace, trace.iloc[[-1]]]))
    with pytest.raises(ValueError):add_structure_event_context(req, trace.iloc[::-1])
    for value in [None, "2024-01-01", 1700000000, START+pd.Timedelta(minutes=5)]:
        bad = trace.copy();bad["open_time"] = bad.open_time.astype(object);bad.loc[0, "open_time"] = value
        with pytest.raises(ValueError):add_structure_event_context(req, bad)
    req["structure_gate_state"] = "accepted"
    with pytest.raises(ValueError, match="overwrite"):add_structure_event_context(req, trace)


def test_supplied_case_close_checked_but_controls_need_no_close_column():
    trace = add_hourly_structure_state(hourly());req = requests([21]);req["signal_close"] = 100.
    with pytest.raises(ValueError, match="signal_close mismatch"):add_structure_event_context(req, trace)
    assert add_structure_event_context(req.drop(columns="signal_close"), trace)[GATE].iloc[0] == "accepted"


def test_trace_gap_dropped_without_state_reset_rejected():
    trace = add_hourly_structure_state(hourly()).drop(index=22)
    with pytest.raises(ValueError, match="semantics mismatch"):validate_hourly_trace(trace)


def test_support_preserves_unmatched_and_distinguishes_known_from_control_acceptance():
    inputs = support_inputs();copies = [x.copy(deep=True) for x in inputs]
    tables, summary = build_structure_event_support(*inputs)
    for x, old in zip(inputs, copies):assert_frame_equal(x, old)
    assert set(tables) == {"case_context", "control_context", "counts", "matched_support"}
    assert tables["case_context"][GATE].tolist() == ["accepted", "abstain"]
    assert tables["control_context"][GATE].tolist() == ["abstain"]*3
    matched = tables["matched_support"]
    assert tuple(matched.columns) == MATCHED_COLUMNS and len(matched) == 2
    assert matched.complete_known.tolist() == [True, False]
    assert matched.accepted_case_complete_known.tolist() == [True, False]
    assert matched.control_accepted.tolist() == [0, 0]
    assert matched.control_total.tolist() == [3, 0]
    assert summary["coverage"]["complete_known_triples_all_cases"] == {"numerator": 1, "denominator": 2, "rate": .5}
    assert summary["coverage"]["complete_known_triples_accepted_cases"] == {"numerator": 1, "denominator": 1, "rate": 1.}
    assert summary["accepted_case_control_states"] == {"total":3,"accepted":0,"abstain":3,"unknown":0,"known":3}
    assert not summary["support_pass"] and not summary["outcomes_read_or_computed"]
    assert not summary["economic_acceptance"]


def test_single_unknown_control_invalidates_whole_triple_not_other_contexts():
    tables, summary = build_structure_event_support(*support_inputs(unknown_control=True))
    assert tables["case_context"][GATE].iloc[0] == "accepted"
    assert tables["control_context"][GATE].tolist() == ["unknown", "abstain", "abstain"]
    assert not tables["matched_support"].complete_known.any()
    assert summary["coverage"]["complete_known_triples_accepted_cases"] == {"numerator":0,"denominator":1,"rate":0.}


def test_unknown_case_is_not_complete_even_if_three_controls_known():
    c,k,t,a,m = support_inputs()
    c.loc[0, ["signal_time", "decision_time", "signal_close"]] = [START+20*HOUR, START+21*HOUR, 100.]
    tables, summary = build_structure_event_support(c,k,t,a,m)
    assert tables["case_context"][GATE].iloc[0] == "unknown"
    assert tables["matched_support"].control_unknown.iloc[0] == 0
    assert not tables["matched_support"].complete_known.any()
    assert summary["coverage"]["complete_known_triples_matched_cases"]["numerator"] == 0


def test_fixed_practical_gates_can_pass_without_claiming_economics_or_triple_coverage():
    blocks, records = [], []
    for fold, start, _ in DEFAULT_FOLDS:
        for j in range(20):
            origin = pd.Timestamp(start) + pd.DateOffset(months=j % 3) + pd.Timedelta(days=2*(j//3))
            block = hourly().iloc[:22].copy()
            block["open_time"] = pd.date_range(origin, periods=22, freq="h")
            blocks.append(block)
            identity = fold + "-" + str(j)
            records.append({"event_id":identity,"signal_time":origin+21*HOUR,
                            "decision_time":origin+22*HOUR,"direction":1,"signal_close":111.,
                            "fold":fold,"request_kind":"case","mother_id":identity,
                            "control_slot":pd.NA,"mother_month":origin.strftime("%Y-%m"),"matched_support":False})
    c = pd.DataFrame(records)
    trace = add_hourly_structure_state(pd.concat(blocks).sort_values("open_time").reset_index(drop=True))
    _,k,_,_,m = support_inputs()
    a = pd.DataFrame({"event_id":c.event_id,"matched_support":False,"assigned_controls":0})
    tables, summary = build_structure_event_support(c,k.iloc[:0],trace,a,m.iloc[:0])
    assert summary["support_values"] == {"events":80,"minimum_fold_events":20,"active_months":12,"minimum_fold_months":3}
    assert summary["support_pass"] and all(summary["support_gates"].values())
    assert not summary["economic_acceptance"] and not summary["outcomes_read_or_computed"]
    assert len(tables["matched_support"]) == 80 and not tables["matched_support"].matched_support.any()
    assert summary["coverage"]["complete_known_triples_accepted_cases"] == {"numerator":0,"denominator":80,"rate":0.}


def test_all_expected_zero_months_and_folds_retained_in_counts_and_minima():
    tables, summary = build_structure_event_support(*support_inputs())
    counts = tables["counts"]
    assert tuple(counts.columns) == COUNT_COLUMNS and len(counts) == 62
    months = counts.loc[counts.dimension.eq("month")]
    assert len(months) == 48 and months.key.nunique() == 24
    folds = counts.loc[counts.dimension.eq("fold") & counts.population.eq("case")]
    assert folds.key.tolist() == [x[0] for x in DEFAULT_FOLDS]
    assert folds.total.tolist() == [0, 0, 2, 0]
    assert summary["support_values"] == {"events":1,"minimum_fold_events":0,"active_months":1,"minimum_fold_months":0}
    assert all(value is False for value in summary["support_gates"].values())


def test_zero_requests_zero_accepted_have_explicit_empty_schema_and_zero_denominators():
    c,k,t,a,m = support_inputs()
    tables, summary = build_structure_event_support(c.iloc[:0], k.iloc[:0], t, a.iloc[:0], m.iloc[:0])
    assert tables["case_context"].empty and set(EVENT_COLUMNS).issubset(tables["case_context"])
    assert len(tables["counts"]) == 62 and tables["matched_support"].empty
    assert summary["support_values"] == {"events":0,"minimum_fold_events":0,"active_months":0,"minimum_fold_months":0}
    for value in summary["coverage"].values():assert value == {"numerator":0,"denominator":0,"rate":None}
    assert tables["counts"].accepted_rate.isna().all()


@pytest.mark.parametrize("mutation", [
    "drop_control", "drop_allocation", "duplicate_allocation", "orphan_control",
    "swap_parent", "wrong_slot", "slot_duplicate", "wrong_direction", "wrong_fold",
    "wrong_month", "reused_time", "actual_mother_time", "assignment_drop",
    "assignment_extra", "assignment_bool_count", "assignment_false",
    "assignment_partial", "control_false", "candidate_wrong_clock", "candidate_wrong_id",
    "case_wrong_mother", "case_nonempty_slot", "population_wrong", "duplicate_global_id",
])
def test_original_membership_and_global_nonreuse_fail_closed(mutation):
    c,k,t,a,m = support_inputs()
    if mutation == "drop_control":k = k.iloc[:-1]
    elif mutation == "drop_allocation":m = m.iloc[:-1]
    elif mutation == "duplicate_allocation":m = pd.concat([m,m.iloc[[0]]])
    elif mutation == "orphan_control":k.loc[0,"mother_id"] = "orphan"
    elif mutation == "swap_parent":k.loc[0,"mother_id"] = "m1"
    elif mutation == "wrong_slot":m.loc[0,"control_slot"] = 8
    elif mutation == "slot_duplicate":k.loc[0,"control_slot"] = 1;m.loc[0,"control_slot"] = 1
    elif mutation == "wrong_direction":k.loc[0,"direction"] = 1
    elif mutation == "wrong_fold":k.loc[0,"fold"] = "2023H1"
    elif mutation == "wrong_month":k.loc[0,"mother_month"] = "2024-02"
    elif mutation == "reused_time":k.loc[1,["signal_time","decision_time"]] = k.loc[0,["signal_time","decision_time"]].to_numpy()
    elif mutation == "actual_mother_time":k.loc[0,["signal_time","decision_time"]] = c.loc[0,["signal_time","decision_time"]].to_numpy()
    elif mutation == "assignment_drop":a = a.iloc[:1]
    elif mutation == "assignment_extra":a = pd.concat([a,pd.DataFrame([{"event_id":"extra","matched_support":False,"assigned_controls":0}])])
    elif mutation == "assignment_bool_count":a["assigned_controls"] = a.assigned_controls.astype(object);a.loc[0,"assigned_controls"] = True
    elif mutation == "assignment_false":a.loc[0,"matched_support"] = False
    elif mutation == "assignment_partial":a.loc[0,"assigned_controls"] = 2
    elif mutation == "control_false":k.loc[0,"matched_support"] = False
    elif mutation == "candidate_wrong_clock":m.loc[0,"candidate_time"] += HOUR
    elif mutation == "candidate_wrong_id":m.loc[0,"candidate_id"] = "wrong"
    elif mutation == "case_wrong_mother":c.loc[0,"mother_id"] = "m1"
    elif mutation == "case_nonempty_slot":c.loc[0,"control_slot"] = 0
    elif mutation == "population_wrong":k.loc[0,"request_kind"] = "case"
    elif mutation == "duplicate_global_id":k.loc[0,"event_id"] = "m0";m.loc[0,"control_event_id"] = "m0"
    with pytest.raises(ValueError):build_structure_event_support(c,k,t,a,m)


@pytest.mark.parametrize("folds", [[], [("x", "2024-01-01", "2024-07-01")],
    [("x", "2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z")]*2,
    [("x", "2024-01-02T00:00:00Z", "2024-07-01T00:00:00Z")],
    [("x", "2024-07-01T00:00:00Z", "2024-01-01T00:00:00Z")],
    [("x", "2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
     ("y", "2024-06-01T00:00:00Z", "2025-01-01T00:00:00Z")],
])
def test_invalid_expected_folds_rejected(folds):
    with pytest.raises(ValueError):build_structure_event_support(*support_inputs(), folds=folds)
