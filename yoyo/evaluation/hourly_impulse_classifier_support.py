"""Causal support-only port of ChartPrime Trend Classifier, default10/100.

Source: https://www.tradingview.com/script/AtJtdaDe-Trend-Classifier-ChartPrime/
Frozen Pine AtJtdaDe.pine SHA0e425fb43caeda0638bb671ee8e944f61844205833fb52f0dcdd8f8728ebbddd.
Formula attribution: ChartPrime, MPL2.0. This file ports only numeric state,
not the seven plot bands,400bar opacity calculation or offset=-1 diamond.

Input columns: native completed-hour open_time/open/high/low/close. Center is
SMA10(EMA10(close)); step is SMA100(EMA100(high-low)). Each EMA recursively
uses all available past/current values within its contiguous hourly segment,
seeded by the first observed value. Both SMAs need their complete windows.
Available_at = open_time+1h, never the back-plotted diamond's hour. Missing
hours reset both recursions and rolling windows. No future, filling, I/O,
outcomes, optimization, resampling or executable returns. Missing warmup is
unknown rather than Pine's cosmetic neutral; not an exact TV runtime claim.

pandas2.3.3 ewm(adjust=False) seed/recursion:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.ewm.html
Frozen V25 identity/allocation helpers are reused as contracts, not features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_structure_event_support import (
    DEFAULT_FOLDS, _bounds, _frame, _membership, _requests, _time,
)

OHLC = ["open_time", "open", "high", "low", "close"]
HOUR = pd.Timedelta(hours=1)
GATE = "classifier_gate_state"
STATES = ("accepted", "abstain", "unknown")
CONTEXT = ["classifier_available_at", "classifier_segment", "classifier_count",
           "classifier_center", "classifier_step", "classifier_previous_center",
           "classifier_direction", "classifier_known", "classifier_reason"]


def classify_state(center, previous, close, step):
    """Pine branch order with strict bands and flat-slope short eligibility."""
    rising = center > previous
    known = np.isfinite(center) & np.isfinite(previous) & np.isfinite(step)
    direction = np.select([rising & (close > center+step),
                           ~rising & (close < center-step)], [1., -1.], default=0.)
    return np.where(known, direction, np.nan)


def classifier_trace(hourly):
    """Return one row per supplied hour; reject malformed source, reset gaps.

Uses OHLC only; surplus features/outcomes are not accessed. Invalid prices
fail the dataset,not silently map to neutral. Stable original positional order
is preserved;duplicate row indices are harmless but duplicate clocks fail.
"""
    _frame(hourly, OHLC, "hourly")
    result = hourly[OHLC].copy(deep=True).reset_index(drop=True)
    times = pd.DatetimeIndex([_time(t) for t in result.open_time])
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("Unique chronological native hours required")
    result["open_time"] = pd.array(times, dtype="datetime64[ns, UTC]")
    for col in OHLC[1:]:
        if result[col].map(lambda v: isinstance(v, (bool, np.bool_))).any():
            raise ValueError("Boolean OHLC forbidden")
        result[col] = pd.to_numeric(result[col], errors="raise").astype(float)
        if not (np.isfinite(result[col]) & result[col].gt(0)).all():
            raise ValueError("Finite positive OHLC required")
    if ((result.high < result[["open", "close", "low"]].max(axis=1)) |
            (result.low > result[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC geometry")
    result["classifier_available_at"] = result.open_time + HOUR
    result["classifier_segment"] = result.open_time.diff().ne(HOUR).cumsum().astype(int)
    parts = []
    for _, part in result.groupby("classifier_segment", sort=False):
        part = part.copy()
        center = part.close.ewm(span=10, adjust=False).mean().rolling(10, min_periods=10).mean()
        step = (part.high-part.low).ewm(span=100, adjust=False).mean().rolling(100, min_periods=100).mean()
        previous = center.shift(1)
        known = np.isfinite(center) & np.isfinite(previous) & np.isfinite(step)
        part["classifier_count"] = np.arange(1, len(part)+1)
        part["classifier_center"] = center
        part["classifier_step"] = step
        part["classifier_previous_center"] = previous
        part["classifier_direction"] = classify_state(center, previous, part.close, step)
        part["classifier_known"] = known.astype(bool)
        part["classifier_reason"] = np.where(known, "known", "warmup")
        parts.append(part)
    if not parts:
        for col in CONTEXT:
            if col not in result:
                result[col] = pd.Series(dtype=bool if col == "classifier_known" else object)
        return result
    return pd.concat(parts, ignore_index=True)


def attach_context(requests, trace):
    """Attach actual completed K1 state,not state at K1 OPEN or future marker.

Only exact signal_time joins; own controls retain their own clocks/direction.
No unmatched mother is dropped. Unknown is separate from observed abstention.
"""
    times, decisions, directions = _requests(requests)
    if any(str(c).startswith("classifier_") for c in requests):
        raise ValueError("Refusing existing classifier context")
    lookup = trace.set_index("open_time")
    rows = []
    for pos, (time, decision, direction) in enumerate(zip(times, decisions, directions)):
        row = {c: np.nan for c in CONTEXT}
        row.update(classifier_available_at=decision, classifier_segment=None,
                   classifier_count=0, classifier_known=False, classifier_reason="missing_signal_hour")
        if time in lookup.index:
            own = lookup.loc[time]
            if own.classifier_available_at != decision:
                raise ValueError("Source not available at own decision")
            if "signal_close" in requests and not np.isclose(
                    own.close, requests.signal_close.iloc[pos], rtol=1e-12, atol=1e-12):
                raise ValueError("Own close differs from source")
            row.update({c: own[c] for c in CONTEXT})
        row[GATE] = ("accepted" if row["classifier_direction"] == direction else "abstain") if row["classifier_known"] else "unknown"
        rows.append(row)
    out = requests.copy(deep=True)
    for col in [*CONTEXT, GATE]:
        out[col] = [r[col] for r in rows]
    return out


def counts(frame):
    return {"total": len(frame), **{s: int(frame[GATE].eq(s).sum()) for s in STATES},
            "known": int(frame[GATE].ne("unknown").sum())}


def build_support(cases, controls, hourly, assignments, allocation):
    """Audit coverage and counts,never consult outcomes or choose parameters.

Support gates80 total,12 perhalf,12 months,3 months/half inherited before
evaluation. They are feasibility gates,not a power calculation. Original
251/744 population validation belongs to the runner. All own clock contexts,
including unsupported mothers and neutral states,remain in saved tables.
"""
    bounds = _bounds(DEFAULT_FOLDS)
    _membership(cases, controls, assignments, allocation, bounds)
    trace = classifier_trace(hourly)
    cases, controls = attach_context(cases, trace), attach_context(controls, trace)
    rows = []
    months = [t.strftime("%Y-%m") for t in pd.date_range("2023-01-01", periods=24, freq="MS")]
    for population, frame in (("case", cases), ("control", controls)):
        for dimension, keys in (("all", ["all"]), ("fold", list(bounds)),
                                ("direction", ["1", "-1"]), ("month", months)):
            for key in keys:
                values = (frame.fold if dimension == "fold" else frame.direction.map(lambda d: str(int(d)))
                          if dimension == "direction" else frame.decision_time.map(lambda t: _time(t).strftime("%Y-%m")))
                part = frame if dimension == "all" else frame.loc[values.eq(key)]
                c = counts(part)
                rows.append(dict(population=population, dimension=dimension, key=key, **c,
                                 accepted_rate=c["accepted"]/c["total"] if c["total"] else None))
    matched = []
    for case in cases.itertuples():
        group = controls.loc[controls.mother_id.eq(case.event_id)].sort_values("control_slot")
        c = counts(group)
        complete = bool(case.classifier_known and len(group) == 3 and c["unknown"] == 0)
        matched.append(dict(event_id=case.event_id, fold=case.fold, case_state=getattr(case, GATE),
                            matched_support=bool(len(group)), control_ids="|".join(group.event_id),
                            control_total=len(group), **{"control_"+s:c[s] for s in STATES},
                            complete_known=complete,
                            accepted_case_complete_known=complete and getattr(case, GATE) == "accepted"))
    matched = pd.DataFrame(matched)
    accepted = cases.loc[cases[GATE].eq("accepted")]
    per_fold = [int(accepted.fold.eq(f).sum()) for f in bounds]
    month_counts = [accepted.loc[accepted.fold.eq(f), "decision_time"].map(
        lambda t: _time(t).strftime("%Y-%m")).nunique() for f in bounds]
    values = dict(events=len(accepted), minimum_fold_events=min(per_fold),
                  active_months=accepted.decision_time.map(lambda t: _time(t).strftime("%Y-%m")).nunique(),
                  minimum_fold_months=min(month_counts))
    gates = dict(minimum_events=values["events"] >= 80, minimum_per_fold=values["minimum_fold_events"] >= 12,
                 minimum_active_months=values["active_months"] >= 12, minimum_months_per_fold=values["minimum_fold_months"] >= 3)
    def ratio(n, d):
        return dict(numerator=int(n), denominator=int(d), rate=n/d if d else None)
    coverage = dict(case_known=ratio(int(cases.classifier_known.sum()), len(cases)),
                    control_known=ratio(int(controls.classifier_known.sum()), len(controls)),
                    complete_known_triples_all_cases=ratio(int(matched.complete_known.sum()), len(cases)),
                    complete_known_triples_accepted_cases=ratio(int(matched.accepted_case_complete_known.sum()), len(accepted)))
    summary = dict(population=dict(case=counts(cases), control=counts(controls)),
                   support_values=values, support_gates=gates, support_pass=all(gates.values()), coverage=coverage,
                   accepted_case_control_states=counts(controls.loc[controls.mother_id.isin(accepted.event_id)]),
                   status="support_pass_requires_separate_outcome_preregistration" if all(gates.values()) else "insufficient_support_no_outcomes",
                   outcomes_read_or_computed=False, economic_acceptance=False,
                   trace_validation=dict(rows=len(trace), segments=int(trace.classifier_segment.nunique()),
                                         raw5_aggregation_verified=False, pine_builtin_parity=False))
    return dict(hourly_trace=trace, case_context=cases, control_context=controls,
                counts=pd.DataFrame(rows), matched_support=matched), summary
