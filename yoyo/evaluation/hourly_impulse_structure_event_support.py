"""Saved-hourly support for V25's current, directional structure EVENT.

Only request identity/own clocks/direction (and optional own signal_close),
saved complete-hour OHLC, and the frozen V20 structure columns are consulted.
The full saved trace is checked against add_hourly_structure_state's 10-left /
10-right, tie-inclusive Python approximation. This is reuse of that trusted
reference, NOT an independent Pine-parity or raw5 aggregation verification.
Origins become available at origin OPEN + 11h. Same-side recrossings do not
create another event; gaps reset levels/state and no_confirmed_break is unknown.

Each request attaches ONLY open_time == signal_time and available_at == E.
Among old known contexts, accept only a current first-establishing/reversing
break in the request's direction; stale aligned state is an observed abstention.
No asof/fill, outcome, markout, exit, MA/ATR gate, resampling, I/O or inference.
The original random triple membership is immutable; controls use OWN clocks.

Version contract: pandas 2.3.3 / numpy 2.0.2. Deep copying preserves inputs;
all assignments below use positional arrays, including duplicate input indices.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.copy.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timestamp.html
Frozen reference and its explicit non-Pine tie contract:
yoyo/data/hourly_impulse_structure.py
"""
from __future__ import annotations

from datetime import datetime, timedelta
from numbers import Number, Real

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_structure import (
    HOURLY_STRUCTURE_COLUMNS, add_hourly_structure_state,
)


HOUR = pd.Timedelta(hours=1)
DEFAULT_FOLDS = (
    ("2023H1", "2023-01-01T00:00:00Z", "2023-07-01T00:00:00Z"),
    ("2023H2", "2023-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
    ("2024H1", "2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
    ("2024H2", "2024-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
)
EVENT_COLUMNS = ("structure_event_known", "structure_event_gate_state",
                 "structure_event_reason")
GATE = "structure_event_gate_state"
STATES = ("accepted", "abstain", "unknown")
COUNT_COLUMNS = ("population", "dimension", "key", "total", *STATES,
                 "known", "accepted_rate")
MATCHED_COLUMNS = (
    "event_id", "fold", "case_state", "matched_support", "control_ids",
    "control_total", "control_accepted", "control_abstain", "control_unknown",
    "complete_known", "accepted_case_complete_known",
)
_OHLC = ("open_time", "open", "high", "low", "close")
_TIMES = tuple(c for c in HOURLY_STRUCTURE_COLUMNS
               if c.endswith("_at") or c.endswith("_origin"))
_INTS = ("structure_count", "structure_segment_id", "structure_state_before",
         "structure_state", "structure_break_direction")
_BOOLS = ("structure_break_on_k1", "structure_known")


def _frame(frame, required, name):
    if (not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique
            or not set(required).issubset(frame)):
        raise ValueError("%s requires unique columns and %s" % (name, sorted(required)))


def _time(value, nullable=False):
    if nullable and pd.isna(value):
        return pd.NaT
    if isinstance(value, (Number, np.bool_)) or not isinstance(value, (str, datetime, pd.Timestamp)):
        raise ValueError("Explicit UTC hour timestamps required")
    try:
        time = pd.Timestamp(value)
        if (pd.isna(time) or time.tzinfo is None or time.utcoffset() != timedelta(0)
                or time != time.floor("h")):
            raise ValueError("Explicit UTC hour timestamps required")
        return time.tz_convert("UTC").as_unit("ns")
    except (TypeError, OverflowError) as error:
        raise ValueError("Explicit UTC hour timestamps required") from error


def _boolean(value):
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError("Boolean flags must not be missing, numeric or strings")
    return bool(value)


def _integer(value, nullable=False):
    if nullable and pd.isna(value):
        return None
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
            or not np.isfinite(value) or value != int(value)):
        raise ValueError("Finite integer value required")
    return int(value)


def _ids(frame, name):
    if any(not isinstance(v, str) or not v.strip() for v in frame.event_id) or not frame.event_id.is_unique:
        raise ValueError("%s event_id must be unique nonempty strings" % name)


def _requests(requests):
    _frame(requests, ("event_id", "signal_time", "decision_time", "direction"), "requests")
    _ids(requests, "requests")
    if any(str(c).startswith("structure_") for c in requests):
        raise ValueError("Refusing to overwrite existing structure context")
    times = [_time(v) for v in requests.signal_time]
    decisions = [_time(v) for v in requests.decision_time]
    directions = [_integer(v) for v in requests.direction]
    if any(d not in (-1, 1) for d in directions):
        raise ValueError("Own direction must be numeric +1/-1")
    if any(e != t + HOUR for t, e in zip(times, decisions)):
        raise ValueError("Own decision_time must equal signal_time+1h")
    if "signal_close" in requests:
        for value in requests.signal_close:
            if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
                    or not np.isfinite(value) or value <= 0):
                raise ValueError("Own signal_close must be finite positive numeric")
    return times, decisions, directions


def validate_hourly_trace(hourly_trace):
    """Return a normalized, verified trace; no saved state is silently repaired.

    All HOURLY_STRUCTURE_COLUMNS must match a replay of the frozen reference
    from the supplied complete-hour OHLC. Timestamp-derived hourly segment IDs
    are checked; an unrelated input segment_id/raw segment is never compared.
    Extra columns, including any old outcome columns, are not inspected.
    """
    _frame(hourly_trace, (*_OHLC, *HOURLY_STRUCTURE_COLUMNS), "hourly_trace")
    times = pd.DatetimeIndex([_time(t) for t in hourly_trace.open_time])
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("Trace hours must be unique and chronologically ordered")
    base = hourly_trace.loc[:, list(_OHLC)].copy(deep=True)
    base["open_time"] = pd.array(times, dtype="datetime64[ns, UTC]")
    expected = add_hourly_structure_state(base)
    for column in HOURLY_STRUCTURE_COLUMNS:
        actual = hourly_trace[column].tolist()
        wanted = expected[column].tolist()
        if column in _TIMES:
            actual = [_time(v, nullable=True) for v in actual]
            equal = all((pd.isna(a) and pd.isna(e)) or a == e for a, e in zip(actual, wanted))
        elif column in _BOOLS:
            equal = [_boolean(v) for v in actual] == wanted
        elif column in _INTS:
            equal = [_integer(v, nullable=True) for v in actual] == [
                None if pd.isna(v) else int(v) for v in wanted]
        elif column == "structure_reason":
            equal = actual == wanted
        else:
            if any(isinstance(v, (bool, np.bool_)) for v in actual):
                raise ValueError("Trace prices must not be bool")
            try:
                equal = np.allclose(np.asarray(actual, dtype=float), np.asarray(wanted, dtype=float),
                                    rtol=1e-12, atol=1e-12, equal_nan=True)
            except (ValueError, TypeError) as error:
                raise ValueError("Invalid saved structure numeric column") from error
        if not equal:
            raise ValueError("Frozen V20 trace semantics mismatch: " + column)
    return expected


def _attach(requests, trace):
    times, decisions, directions = _requests(requests)
    lookup = trace.set_index("open_time")
    records = []
    for position, (time, decision, direction) in enumerate(zip(times, decisions, directions)):
        row = {c: pd.NaT if c in _TIMES else pd.NA if c in _INTS else np.nan
               for c in HOURLY_STRUCTURE_COLUMNS}
        reason = "no_source" if trace.empty or trace.open_time.iloc[0] >= decision else "missing_signal_hour"
        row.update(structure_available_at=decision, structure_count=0,
                   structure_break_direction=0, structure_break_on_k1=False,
                   structure_known=False, structure_reason=reason)
        if time in lookup.index:
            own = lookup.loc[time]
            if own.structure_available_at != decision:
                raise ValueError("Exact own hour is not available at decision")
            if "signal_close" in requests and not np.isclose(
                    own.close, requests.signal_close.iloc[position], rtol=1e-12, atol=1e-12):
                raise ValueError("Own complete-hour signal_close mismatch")
            row.update({c: own[c] for c in HOURLY_STRUCTURE_COLUMNS})
        known = bool(row["structure_known"])
        event = known and bool(row["structure_break_on_k1"])
        accepted = event and row["structure_break_direction"] == direction
        row["structure_gate_state"] = ("accepted" if row["structure_state"] == direction else "abstain") if known else "unknown"
        row.update(structure_event_known=known,
                   structure_event_gate_state="accepted" if accepted else "abstain" if known else "unknown",
                   structure_event_reason=("directional_break" if accepted else "opposite_break" if event
                                           else "no_current_break" if known else row["structure_reason"]))
        records.append(row)
    result = requests.copy(deep=True)
    for column in (*HOURLY_STRUCTURE_COLUMNS, "structure_gate_state", *EVENT_COLUMNS):
        data = [r[column] for r in records]
        dtype = ("datetime64[ns, UTC]" if column in _TIMES else "Int64" if column in _INTS
                 else bool if column in (*_BOOLS, "structure_event_known") else object)
        result[column] = pd.array(data, dtype=dtype)
    return result


def add_structure_event_context(requests, hourly_trace):
    """Preserve all requests/index/attrs; append own event and old-state diagnostics.

    Existing structure_* columns are rejected, never overwritten. Missing own
    hours retain V20 no_source/missing_signal_hour unknown semantics. No raw
    segment is fabricated from an hourly segment. Unknown state after a valid
    pivot remains unknown, and equal/old same-side state is not a fresh event.
    """
    _requests(requests)
    return _attach(requests, validate_hourly_trace(hourly_trace))


def _bounds(folds):
    if not isinstance(folds, (tuple, list)) or not folds:
        raise ValueError("Expected folds must include explicit start/end bounds")
    result = {}
    for item in folds:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ValueError("Each fold requires name/start/end")
        name, start, end = item
        if not isinstance(name, str) or not name.strip() or name in result:
            raise ValueError("Unique named folds required")
        start, end = _time(start), _time(end)
        if start >= end or start.day != 1 or end.day != 1 or start.hour or end.hour:
            raise ValueError("Folds must have ascending UTC month boundaries")
        result[name] = (start, end)
    ordered = sorted(result.values())
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Fold intervals must not overlap")
    return result


def _membership(cases, controls, assignments, allocation, bounds):
    for name, frame in (("case", cases), ("control", controls)):
        _requests(frame)
        _frame(frame, ("fold", "request_kind", "mother_id", "control_slot", "matched_support", "mother_month"), name)
        for r in frame.itertuples():
            if r.fold not in bounds or not bounds[r.fold][0] <= _time(r.decision_time) < bounds[r.fold][1]:
                raise ValueError("Request outside its expected fold")
            if r.request_kind != name or r.mother_month != _time(r.decision_time).strftime("%Y-%m"):
                raise ValueError("Request population/month mismatch")
            _boolean(r.matched_support)
    if set(cases.event_id) & set(controls.event_id):
        raise ValueError("Case/control event IDs must be globally unique")
    _frame(assignments, ("event_id", "matched_support", "assigned_controls"), "assignments")
    _ids(assignments, "assignments")
    if set(assignments.event_id) != set(cases.event_id):
        raise ValueError("Assignments must preserve every original mother")
    _frame(allocation, ("event_id", "candidate_id", "candidate_time", "control_slot", "control_event_id"), "allocation")
    if (allocation.control_event_id.isna().any() or not allocation.control_event_id.is_unique
            or set(allocation.control_event_id) != set(controls.event_id)
            or not set(allocation.event_id).issubset(cases.event_id)):
        raise ValueError("Original allocation membership changed")
    times = [_time(t) for t in controls.decision_time]
    if len(set(times)) != len(times) or set(times) & {_time(t) for t in cases.decision_time}:
        raise ValueError("Control decision time reused or equals an actual mother")
    ci, ai = controls.set_index("event_id"), assignments.set_index("event_id")
    cm = cases.set_index("event_id")
    for a in allocation.itertuples():
        c = ci.loc[a.control_event_id]
        if (not isinstance(a.candidate_id, str) or a.candidate_id != _time(a.candidate_time).isoformat()
                or _time(a.candidate_time) != _time(c.decision_time)
                or c.mother_id != a.event_id or _integer(c.control_slot) != _integer(a.control_slot)):
            raise ValueError("Control clock/member/slot differs from frozen allocation")
        m = cm.loc[a.event_id]
        if c.fold != m.fold or c.direction != m.direction or c.mother_month != m.mother_month:
            raise ValueError("Control must retain own time and mother's fold/month/direction")
    for m in cases.itertuples():
        group = controls.loc[controls.mother_id.eq(m.event_id)]
        match = _boolean(ai.loc[m.event_id, "matched_support"])
        count = _integer(ai.loc[m.event_id, "assigned_controls"])
        if (m.mother_id != m.event_id or not pd.isna(m.control_slot)
                or match != _boolean(m.matched_support) or count != (3 if match else 0)
                or len(group) != count or (count and set(group.control_slot.map(_integer)) != {0, 1, 2})
                or (count and not all(_boolean(v) for v in group.matched_support))):
            raise ValueError("Each original mother requires exactly its frozen triple or zero")


def _counts(frame):
    values = frame[GATE]
    if values.isna().any() or not values.isin(STATES).all():
        raise ValueError("Explicit event support states required")
    out = {"total": len(frame), **{s: int(values.eq(s).sum()) for s in STATES}}
    out["known"] = out["total"] - out["unknown"]
    return out


def _ratio(n, d):
    return {"numerator": int(n), "denominator": int(d), "rate": n / d if d else None}


def build_structure_event_support(case_requests, control_requests, hourly_trace,
                                  assignments, allocation, *, folds=DEFAULT_FOLDS):
    """Pure support-only tables; original study population sizes belong to runner.

    Return (tables, summary). tables has case_context, control_context, counts,
    matched_support. counts includes both populations and zero rows for every
    supplied fold/month (plus all/direction); default gives 62 rows. Every mother
    is retained in matched_support, including unmatched/unknown/abstain cases.
    Complete-known means the mother and all THREE own controls are known; it
    does not mean that the controls pass their own event gates. Thresholds are
    fixed 80 cases, 12/fold, 12 active months, 3 active months/fold. A support
    pass only permits a separately preregistered outcome phase, never economics.
    """
    bounds = _bounds(folds)
    _membership(case_requests, control_requests, assignments, allocation, bounds)
    trace = validate_hourly_trace(hourly_trace)
    cases, controls = _attach(case_requests, trace), _attach(control_requests, trace)
    counts = []
    months = sorted({t.strftime("%Y-%m") for start, end in bounds.values()
                     for t in pd.date_range(start, end, freq="MS", inclusive="left")})
    for population, frame in (("case", cases), ("control", controls)):
        for dimension, keys in (("all", ["all"]), ("fold", list(bounds)),
                                ("direction", ["1", "-1"]), ("month", months)):
            for key in keys:
                values = (frame.fold if dimension == "fold" else frame.direction.map(lambda v: str(int(v)))
                          if dimension == "direction" else frame.decision_time.map(lambda v: _time(v).strftime("%Y-%m")))
                part = frame if dimension == "all" else frame.loc[values.eq(key)]
                n = _counts(part)
                counts.append(dict(population=population, dimension=dimension, key=key, **n,
                                   accepted_rate=n["accepted"] / n["total"] if n["total"] else None))
    matched = []
    for case in cases.itertuples():
        group = controls.loc[controls.mother_id.eq(case.event_id)].sort_values("control_slot")
        c = _counts(group)
        complete = case.structure_event_known and len(group) == 3 and c["unknown"] == 0
        matched.append({"event_id": case.event_id, "fold": case.fold,
                        "case_state": getattr(case, GATE), "matched_support": bool(len(group)),
                        "control_ids": "|".join(group.event_id), "control_total": len(group),
                        **{"control_" + s: c[s] for s in STATES}, "complete_known": bool(complete),
                        "accepted_case_complete_known": bool(complete and getattr(case, GATE) == "accepted")})
    matched = pd.DataFrame(matched, columns=MATCHED_COLUMNS)
    accepted = cases.loc[cases[GATE].eq("accepted")]
    per_fold = [int(accepted.fold.eq(f).sum()) for f in bounds]
    month_counts = [accepted.loc[accepted.fold.eq(f), "decision_time"].map(
        lambda v: _time(v).strftime("%Y-%m")).nunique() for f in bounds]
    values = {"events": len(accepted), "minimum_fold_events": min(per_fold),
              "active_months": accepted.decision_time.map(lambda v: _time(v).strftime("%Y-%m")).nunique(),
              "minimum_fold_months": min(month_counts)}
    gates = {"minimum_events": values["events"] >= 80,
             "minimum_per_fold": values["minimum_fold_events"] >= 12,
             "minimum_active_months": values["active_months"] >= 12,
             "minimum_months_per_fold": values["minimum_fold_months"] >= 3}
    matched_n = int(matched.matched_support.sum())
    complete_n = int(matched.complete_known.sum())
    population = {"case": _counts(cases), "control": _counts(controls)}
    coverage = {"case_known": _ratio(population["case"]["known"], len(cases)),
                "control_known": _ratio(population["control"]["known"], len(controls)),
                "complete_known_triples_all_cases": _ratio(complete_n, len(cases)),
                "complete_known_triples_matched_cases": _ratio(complete_n, matched_n),
                "complete_known_triples_accepted_cases": _ratio(int(matched.accepted_case_complete_known.sum()), len(accepted)),
                "accepted_cases_with_matched_support": _ratio(int(matched.loc[matched.case_state.eq("accepted"), "matched_support"].sum()), len(accepted))}
    summary = {"population": population, "support_values": values, "support_gates": gates,
               "support_pass": all(gates.values()),
               "status": "support_pass_requires_separate_outcome_preregistration" if all(gates.values()) else "insufficient_support_no_outcomes",
               "coverage": coverage,
               "accepted_case_control_states": _counts(controls.loc[controls.mother_id.isin(accepted.event_id)]),
               "trace_validation": {"rows": len(trace), "frozen_reference_recomputed": True,
                                    "raw5_aggregation_verified": False, "pine_builtin_parity": False},
               "outcomes_read_or_computed": False, "economic_acceptance": False}
    return {"case_context": cases, "control_context": controls,
            "counts": pd.DataFrame(counts, columns=COUNT_COLUMNS), "matched_support": matched}, summary
