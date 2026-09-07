"""V31 support-only prior improvement of ChartPrime's DeltaPulse Wave.

Source: https://www.tradingview.com/script/lfaZVLub-DeltaPulse-Wave-ChartPrime/
Frozen lfaZVLub.pine SHA256:
e92978a8e5fa0873ba25e60c37d0d4b5bea6b33533bbc28cbcb2ad39d2a9e2fe.
Numeric formula attribution: ChartPrime, MPL2.0. This ports neither the source
divergence signals (whose drawings use offset=-1) nor its candle colours.

Only native-hour open_time/open/high/low/close/volume are read. A bullish real
body assigns ALL volume to buy, a bearish body ALL to sell, and a doji neither.
EMA20(buy), EMA20(sell), EMA20(volume if volume>0 else 1) recursively use the
entire observed contiguous segment, each seeded at its first value. Their
relative difference times100 is smoothed by first-value-seeded EMA5. This is
an OHLCV proxy, not actual aggressor order flow. Gaps reset every recursion;
no missing-hour fill or cross-gap borrowing is permitted.

For K1 OPEN T and own decision E=T+1h, the ONLY admission test is strict
direction*(wave[T-1h]-wave[T-2h])>0. Both inputs were available at/before T;
K1 wave is diagnostic only. Source K1 must exist and optional signal_close
must agree. T-2 requires100 observed continuous bars (5*source20 RESEARCH
warmup), hence current segment count>=102. This warmup is not exact Pine
runtime parity. Missing own K1/prior hours and warmup remain unknown; known
zero/opposite changes abstain. This is improvement, not a first-turn event.

No I/O, outcomes, support search, parameter tuning, resampling, execution or
inference. Frozen V25 helpers validate identities/allocation, not features.
pandas2.3.3 / numpy2.0.2; ewm(adjust=False) is first-value seeded recursion:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.ewm.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.copy.html
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_structure_event_support import (
    COUNT_COLUMNS, DEFAULT_FOLDS, MATCHED_COLUMNS, _bounds, _frame,
    _membership, _requests, _time,
)

OHLCV = ["open_time", "open", "high", "low", "close", "volume"]
HOUR = pd.Timedelta(hours=1)
WAVE_LENGTH = 20
SMOOTH_LENGTH = 5
PRIOR_WARMUP = 100
GATE = "wave_gate_state"
STATES = ("accepted", "abstain", "unknown")
TIME_COLUMNS = (
    "wave_available_at", "wave_previous_open_time", "wave_previous_available_at",
    "wave_previous2_open_time", "wave_previous2_available_at",
)
INT_COLUMNS = ("wave_segment", "wave_count", "wave_previous_count", "wave_previous2_count")
NUMERIC_COLUMNS = (
    "wave_buy_volume", "wave_sell_volume", "wave_total_input", "wave_ema_buy",
    "wave_ema_sell", "wave_ema_total", "wave_raw", "wave_value",
    "wave_previous", "wave_previous2", "wave_prior_delta",
)
CONTEXT = (*TIME_COLUMNS, *INT_COLUMNS, *NUMERIC_COLUMNS, "wave_known", "wave_reason")


def _reserved(frame):
    if any(str(c).startswith("wave_") for c in frame):
        raise ValueError("Refusing to overwrite existing wave_ context")


def wave_trace(hourly):
    """Return all supplied hours plus replay/debug fields; surplus data ignored.

    Invalid OHLCV fails explicitly. Duplicate row indices are harmless; clocks
    must be unique chronological UTC hours. Diagnostics are available from the
    seed but wave_known refers to the frozen PRIOR comparison's eligibility.
    """
    _frame(hourly, OHLCV, "hourly")
    _reserved(hourly)
    result = hourly[OHLCV].copy(deep=True).reset_index(drop=True)
    times = pd.DatetimeIndex([_time(t) for t in result.open_time])
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("Unique chronological native hours required")
    result["open_time"] = pd.array(times, dtype="datetime64[ns, UTC]")
    for col in OHLCV[1:]:
        if result[col].map(lambda v: isinstance(v, (bool, np.bool_))).any():
            raise ValueError("Boolean OHLCV forbidden")
        result[col] = pd.to_numeric(result[col], errors="raise").astype(float)
        valid = np.isfinite(result[col]) & (result[col].ge(0) if col == "volume" else result[col].gt(0))
        if not valid.all():
            raise ValueError("Finite positive OHLC and finite nonnegative volume required")
    if ((result.high < result[["open", "close", "low"]].max(axis=1)) |
            (result.low > result[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC geometry")
    result["wave_available_at"] = result.open_time+HOUR
    result["wave_segment"] = result.open_time.diff().ne(HOUR).cumsum().astype(int)
    parts = []
    for _, part in result.groupby("wave_segment", sort=False):
        part = part.copy()
        part["wave_count"] = np.arange(1, len(part)+1)
        part["wave_buy_volume"] = part.volume.where(part.close > part.open, 0.)
        part["wave_sell_volume"] = part.volume.where(part.close < part.open, 0.)
        part["wave_total_input"] = part.volume.where(part.volume > 0, 1.)
        for source, target in (("buy_volume", "ema_buy"), ("sell_volume", "ema_sell"),
                               ("total_input", "ema_total")):
            part["wave_"+target] = part["wave_"+source].ewm(span=WAVE_LENGTH, adjust=False).mean()
        denominator = part.wave_ema_total.where(part.wave_ema_total > 0, 1.)
        part["wave_raw"] = (part.wave_ema_buy-part.wave_ema_sell)/denominator*100.
        part["wave_value"] = part.wave_raw.ewm(span=SMOOTH_LENGTH, adjust=False).mean()
        for lag, stem in ((1, "wave_previous"), (2, "wave_previous2")):
            part[stem] = part.wave_value.shift(lag)
            part[stem+"_count"] = part.wave_count.shift(lag)
            part[stem+"_open_time"] = part.open_time.shift(lag)
            part[stem+"_available_at"] = part.wave_available_at.shift(lag)
        part["wave_prior_delta"] = part.wave_previous-part.wave_previous2
        part["wave_known"] = part.wave_previous2_count.ge(PRIOR_WARMUP)
        part["wave_reason"] = np.where(part.wave_count < 3, "missing_prior_hours",
                                       np.where(part.wave_known, "known", "warmup"))
        parts.append(part)
    if parts:
        result = pd.concat(parts, ignore_index=True)
    for col in CONTEXT:
        dtype = ("datetime64[ns, UTC]" if col in TIME_COLUMNS else "Int64" if col in INT_COLUMNS
                 else bool if col == "wave_known" else object if col == "wave_reason" else float)
        result[col] = pd.array(result[col] if col in result else [], dtype=dtype)
    return result[[*OHLCV, *CONTEXT]]


def validate_wave_trace(trace):
    """Verify supplied derived context against OHLCV; never repair silently."""
    _frame(trace, [*OHLCV, *CONTEXT], "wave trace")
    expected = wave_trace(trace[OHLCV])
    for col in CONTEXT:
        actual, wanted = trace[col].tolist(), expected[col].tolist()
        if col in TIME_COLUMNS:
            actual = [_time(v, nullable=True) for v in actual]
            equal = all((pd.isna(a) and pd.isna(b)) or a == b for a, b in zip(actual, wanted))
        elif col in ("wave_known", "wave_reason"):
            if col == "wave_known" and any(not isinstance(v, (bool, np.bool_)) for v in actual):
                raise ValueError("wave_known must be explicit boolean")
            equal = actual == wanted
        else:
            if any(isinstance(v, (bool, np.bool_)) for v in actual):
                raise ValueError("Wave numeric fields cannot be bool")
            try:
                values = np.asarray([np.nan if pd.isna(v) else v for v in actual], dtype=float)
                desired = np.asarray([np.nan if pd.isna(v) else v for v in wanted], dtype=float)
                equal = np.allclose(values, desired, rtol=0 if col in INT_COLUMNS else 1e-12,
                                    atol=0 if col in INT_COLUMNS else 1e-12, equal_nan=True)
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid wave numeric field") from error
        if not equal:
            raise ValueError("Wave trace semantics mismatch: "+col)
    return expected


def attach_context(requests, trace):
    """Append own prior-hour improvement, retaining all IDs/indices/attributes.

    wave_available_at is the current source clock E, not the evidence clock.
    wave_previous_available_at is T. Current wave_value never selects a row.
    Optional signal_close is only a source-identity consistency check.
    """
    times, decisions, directions = _requests(requests)
    _reserved(requests)
    lookup = validate_wave_trace(trace).set_index("open_time")
    rows = []
    for pos, (time, decision, direction) in enumerate(zip(times, decisions, directions)):
        row = {c: pd.NaT if c in TIME_COLUMNS else pd.NA if c in INT_COLUMNS else np.nan for c in CONTEXT}
        row.update(wave_count=0, wave_known=False, wave_reason="missing_signal_hour")
        if time in lookup.index:
            own = lookup.loc[time]
            if own.wave_available_at != decision:
                raise ValueError("Source not available at own decision")
            if "signal_close" in requests and not np.isclose(
                    own.close, requests.signal_close.iloc[pos], rtol=1e-12, atol=1e-12):
                raise ValueError("Own signal_close differs from source")
            row.update({c: own[c] for c in CONTEXT})
        if row["wave_known"]:
            if (row["wave_previous_open_time"] != time-HOUR or
                    row["wave_previous2_open_time"] != time-2*HOUR or
                    row["wave_previous_available_at"] != time):
                raise ValueError("Prior evidence must have exact own pre-K1 clocks")
            signed_change = direction*row["wave_prior_delta"]
            row[GATE] = "accepted" if signed_change > 0 else "abstain"
            row["wave_reason"] = "prior_improving" if signed_change > 0 else "flat" if signed_change == 0 else "opposite"
        else:
            row[GATE] = "unknown"
        rows.append(row)
    out = requests.copy(deep=True)
    for col in (*CONTEXT, GATE):
        dtype = ("datetime64[ns, UTC]" if col in TIME_COLUMNS else "Int64" if col in INT_COLUMNS
                 else bool if col == "wave_known" else object if col in ("wave_reason", GATE) else float)
        out[col] = pd.array([r[col] for r in rows], dtype=dtype)
    return out


def counts(frame):
    """Count all explicit gate states; unknown is not observed abstention."""
    _frame(frame, [GATE], "context")
    if frame[GATE].isna().any() or not frame[GATE].isin(STATES).all():
        raise ValueError("Explicit wave gate states required")
    return dict(total=len(frame), **{s: int(frame[GATE].eq(s).sum()) for s in STATES},
                known=int(frame[GATE].ne("unknown").sum()))


def _ratio(n, d):
    return dict(numerator=int(n), denominator=int(d), rate=n/d if d else None)


def build_support(cases, controls, hourly, assignments, allocation):
    """Return support tables, never economic outcomes or optimized parameters.

    All own clocks and frozen triples use V25 identity/allocation contracts.
    The runner freezes251 cases/744 controls/248 triples/3 unmatched. This pure
    function also accepts small synthetic populations. Default62 count rows
    retain zero months and every mother retains its matched ledger row.
    80 total,12/half,12 months,3 months/half are fixed feasibility gates, not
    statistical power or economic acceptance. Controls are not prefiltered.
    """
    bounds = _bounds(DEFAULT_FOLDS)
    _membership(cases, controls, assignments, allocation, bounds)
    trace = wave_trace(hourly)
    cases, controls = attach_context(cases, trace), attach_context(controls, trace)
    rows = []
    months = [t.strftime("%Y-%m") for t in pd.date_range("2023-01-01", periods=24, freq="MS")]
    for population, frame in (("case", cases), ("control", controls)):
        dimensions = dict(fold=frame.fold, direction=frame.direction.map(lambda d: str(int(d))),
                          month=frame.decision_time.map(lambda t: _time(t).strftime("%Y-%m")))
        for dimension, keys in (("all", ["all"]), ("fold", list(bounds)),
                                ("direction", ["1", "-1"]), ("month", months)):
            for key in keys:
                part = frame if dimension == "all" else frame.loc[dimensions[dimension].eq(key)]
                c = counts(part)
                rows.append(dict(population=population, dimension=dimension, key=key, **c,
                                 accepted_rate=c["accepted"]/c["total"] if c["total"] else None))
    matched = []
    for case in cases.itertuples():
        group = controls.loc[controls.mother_id.eq(case.event_id)].sort_values("control_slot")
        c = counts(group)
        complete = bool(case.wave_known and len(group) == 3 and c["unknown"] == 0)
        matched.append(dict(event_id=case.event_id, fold=case.fold, case_state=getattr(case, GATE),
                            matched_support=bool(len(group)), control_ids="|".join(group.event_id),
                            control_total=len(group), **{"control_"+s: c[s] for s in STATES},
                            complete_known=complete,
                            accepted_case_complete_known=complete and getattr(case, GATE) == "accepted"))
    matched = pd.DataFrame(matched, columns=MATCHED_COLUMNS)
    accepted = cases.loc[cases[GATE].eq("accepted")]
    per_fold = [int(accepted.fold.eq(f).sum()) for f in bounds]
    month_counts = [accepted.loc[accepted.fold.eq(f), "decision_time"].map(
        lambda t: _time(t).strftime("%Y-%m")).nunique() for f in bounds]
    values = dict(events=len(accepted), minimum_fold_events=min(per_fold),
                  active_months=int(accepted.decision_time.map(lambda t: _time(t).strftime("%Y-%m")).nunique()),
                  minimum_fold_months=int(min(month_counts)))
    gates = dict(minimum_events=values["events"] >= 80, minimum_per_fold=values["minimum_fold_events"] >= 12,
                 minimum_active_months=values["active_months"] >= 12, minimum_months_per_fold=values["minimum_fold_months"] >= 3)
    coverage = dict(case_known=_ratio(cases.wave_known.sum(), len(cases)),
                    control_known=_ratio(controls.wave_known.sum(), len(controls)),
                    complete_known_triples_all_cases=_ratio(matched.complete_known.sum(), len(cases)),
                    complete_known_triples_accepted_cases=_ratio(matched.accepted_case_complete_known.sum(), len(accepted)))
    passed = all(gates.values())
    summary = dict(population=dict(case=counts(cases), control=counts(controls)),
                   support_values=values, support_gates=gates, support_pass=passed, coverage=coverage,
                   accepted_case_control_states=counts(controls.loc[controls.mother_id.isin(accepted.event_id)]),
                   status="support_pass_requires_separate_outcome_preregistration" if passed else "insufficient_support_no_outcomes",
                   outcomes_read_or_computed=False, economic_acceptance=False,
                   trace_validation=dict(rows=len(trace), segments=int(trace.wave_segment.nunique()),
                                         prior_warmup=PRIOR_WARMUP, current_count_minimum=PRIOR_WARMUP+2,
                                         raw5_aggregation_verified=False, pine_builtin_parity=False))
    return dict(hourly_trace=trace, case_context=cases, control_context=controls,
                counts=pd.DataFrame(rows, columns=COUNT_COLUMNS), matched_support=matched), summary
