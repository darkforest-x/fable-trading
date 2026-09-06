"""Outcome-free controls for the frozen-source first-release hourly study.

The only exact keys are the single supplied BTC instrument/fold, UTC month,
six-hour decision-time bucket, causal ATR-fraction tercile, and actual native
5m colour completed at that decision. Case direction is transferred, not inferred
from control hourly colour. Neither hourly colour/slope, SMA body crossings,
source-zone membership, future release success nor outcomes filter candidates.

ATR fractions use each completed hourly ATR and close. Their terciles use the
previous 720 contiguous hourly observations, shifted one bar, minimum 168, with
reset on hourly gaps/segments. Raw5 contributes only timestamps, known opens and
segment IDs. The entire source hour and exact last completed 5m must connect to
the entry open; management and hourly segment counters are NEVER compared to
raw5 counters. A true case excludes only its exact own decision, never a future
or prior window around it. Request/PNL selection never determines support.

Official pandas 2.3.3 sources: backward asof permits equality, but stale colours
are explicitly rejected; shifted rolling quantiles exclude the current value.
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.merge_asof.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.core.window.rolling.Rolling.quantile.html

Matching is a conditional observational benchmark, not randomized treatment.
Both source frames must describe the same BTC contract; this module does not
fetch, combine or select symbols. Original cases are never removed on failure.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_k2_matching import CONTROL_COLUMNS as _V4_CONTROL_COLUMNS


CONTROL_COLUMNS = list(_V4_CONTROL_COLUMNS) + ["parent_zone_id", "month", "utc_6h_bucket"]
ASSIGNMENT_COLUMNS = [
    "event_id", "mother_event_id", "decision_time", "entry_time", "fold", "direction",
    "parent_zone_id", "match_status", "assigned_controls", "eligible_controls_before_reuse",
    "available_controls", "mother_risk_atr", "month", "utc_6h_bucket", "vol_bucket",
    "known_5m_colour", "selected_control_times", "assignment_hash",
]
_CASE_COLUMNS = ["event_id", "decision_time", "direction", "initial_stop", "signal_atr", "fold"]
_FIVE_MINUTES = pd.Timedelta(minutes=5)
_ONE_HOUR = pd.Timedelta(hours=1)


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    values = pd.to_numeric(frame[name], errors="coerce")
    return values.where(np.isfinite(values))


def _validate(frame: pd.DataFrame, required: list, name: str) -> pd.DataFrame:
    missing = set(required) - set(frame)
    if missing:
        raise ValueError("{} missing columns: {}".format(name, sorted(missing)))
    result = frame.copy()
    result["open_time"] = pd.to_datetime(result["open_time"], utc=True)
    times = result["open_time"]
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("{} timestamps must be finite, unique, chronological".format(name))
    return result


def build_source_matching_frame(
    raw5: pd.DataFrame,
    hourly_featured: pd.DataFrame,
    management_featured: pd.DataFrame,
    all_case_requests: pd.DataFrame,
) -> pd.DataFrame:
    """Build fresh V7 eligibility, with no inherited V4 cross/hourly gates.

    All hourly rows are retained, including unsupported ones. Unknown volatility
    remains a nullable bucket, not zero. Hourly MA/colour/slope may be unavailable
    because none is a V7 matching-support requirement. Optional feature columns
    survive for control diagnostics but cannot affect eligibility or assignment.
    """
    raw = _validate(raw5, ["open_time", "open", "segment_id"], "raw5")
    h = _validate(hourly_featured, ["open_time", "open", "high", "low", "close", "atr", "segment_id"], "hourly_featured")
    mg = _validate(management_featured, ["open_time", "ma_side", "segment_id"], "management_featured")
    if not raw["open_time"].eq(raw["open_time"].dt.floor("5min")).all():
        raise ValueError("Raw5 opens must use the exact five-minute grid")
    if not h["open_time"].eq(h["open_time"].dt.floor("h")).all():
        raise ValueError("Hourly opens must use exact UTC hour boundaries")
    if "decision_time" not in all_case_requests and len(all_case_requests):
        raise ValueError("all_case_requests requires decision_time")

    h["signal_time"] = h["open_time"]
    h["decision_time"] = h["open_time"] + _ONE_HOUR
    for name in ("ma", "ma_side", "ma_slope_atr"):
        h[name] = _numeric(h, name)
    h["signal_atr"] = _numeric(h, "atr")
    close = _numeric(h, "close")
    h["atr_fraction"] = h["signal_atr"].where(h["signal_atr"].gt(0)) / close.where(close.gt(0))
    # Numeric counters may differ between grids; only this hourly run partitions
    # the backward volatility window. A physical hour gap resets even if the
    # caller forgot to increment its aggregate counter.
    hourly_segment = _numeric(h, "segment_id")
    runs = (h["open_time"].diff().ne(_ONE_HOUR) | hourly_segment.ne(hourly_segment.shift()) | hourly_segment.isna()).cumsum()
    for name, quantile in (("atr_tercile_low", 1 / 3), ("atr_tercile_high", 2 / 3)):
        h[name] = h["atr_fraction"].groupby(runs).transform(
            lambda values: values.shift(1).rolling(720, min_periods=168).quantile(quantile)
        )
    vol_valid = h[["atr_fraction", "atr_tercile_low", "atr_tercile_high"]].notna().all(axis=1)
    bucket = h["atr_fraction"].gt(h["atr_tercile_low"]).astype(int) + h["atr_fraction"].gt(h["atr_tercile_high"]).astype(int)
    h["vol_bucket"] = bucket.where(vol_valid).astype("Int64")

    indexed_raw = raw.set_index("open_time")
    source_segment = pd.Series(_numeric(raw, "segment_id").to_numpy(), index=raw["open_time"])
    source_position = pd.Series(np.arange(len(raw)), index=raw["open_time"])
    h["source_segment_id"] = h["open_time"].map(source_segment)
    h["entry_source_segment_id"] = h["decision_time"].map(source_segment)
    h["entry_open"] = pd.to_numeric(h["decision_time"].map(indexed_raw["open"]), errors="coerce")
    h["known_entry_open"] = np.isfinite(h["entry_open"]) & h["entry_open"].gt(0)
    h["source_hour_complete"] = (h["decision_time"].map(source_position) - h["open_time"].map(source_position)).eq(12)
    h["entry_source_continuous"] = (
        h["source_segment_id"].notna() & h["entry_source_segment_id"].eq(h["source_segment_id"])
        & h["source_hour_complete"]
    )
    colour = pd.DataFrame({
        "known_5m_open_time": mg["open_time"],
        "known_5m_available": mg["open_time"] + _FIVE_MINUTES,
        "known_5m_colour": _numeric(mg, "ma_side"),
        "management_segment_id": _numeric(mg, "segment_id"),
        "known_5m_source_segment_id": mg["open_time"].map(source_segment),
    })
    h = pd.merge_asof(
        h.sort_values("decision_time"), colour.sort_values("known_5m_available"),
        left_on="decision_time", right_on="known_5m_available", direction="backward",
        allow_exact_matches=True,
    )
    h["known_5m_valid"] = (
        h["known_5m_available"].eq(h["decision_time"])
        & h["known_5m_open_time"].eq(h["decision_time"] - _FIVE_MINUTES)
        & h["known_5m_colour"].isin([-1, 1]) & h["management_segment_id"].notna()
        & h["known_5m_source_segment_id"].notna()
        & h["known_5m_source_segment_id"].eq(h["entry_source_segment_id"])
    )
    h["known_hourly_colour"] = _numeric(h, "ma_side")
    h["unsigned_hourly_slope_sign"] = np.sign(_numeric(h, "ma_slope_atr"))
    h["month"] = h["decision_time"].dt.strftime("%Y-%m")
    h["utc_6h_bucket"] = h["decision_time"].dt.hour // 6
    actual_times = (pd.to_datetime(all_case_requests["decision_time"], utc=True, errors="coerce").dropna()
                    if "decision_time" in all_case_requests else pd.Series(dtype="datetime64[ns, UTC]"))
    h["actual_case_decision_excluded"] = h["decision_time"].isin(set(actual_times))
    # Deliberately constructed afresh: no SMA cross, hourly colour, slope,
    # source-zone membership or realised outcome appears in these expressions.
    h["matching_support"] = h["vol_bucket"].notna() & h["signal_atr"].gt(0) & h["known_entry_open"] & h["entry_source_continuous"] & h["known_5m_valid"]
    h["candidate_eligible"] = h["matching_support"] & ~h["actual_case_decision_excluded"]
    return h


def _hash(records: Any) -> str:
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def assign_source_controls(
    cases: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    count: int = 3,
    seed: int = 20260906,
    start_inclusive: Any,
    end_exclusive: Any,
    embargo_hours: float = 72,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Assign exact-key controls once per explicit fold, without replacement.

    Both clocks are mandatory: all case/control decisions must fall inside
    [start_inclusive, end_exclusive - embargo_hours). Count, support and matching
    never use outcomes. A case with fewer than count eligible unused controls
    gets none, not a partial match or progressively relaxed keys. Failed cases
    retain an assignment row and do not consume candidate times.
    """
    missing = set(_CASE_COLUMNS) - set(cases)
    if missing and len(cases):
        raise ValueError("Case table missing columns: {}".format(sorted(missing)))
    source = cases.copy()
    for name in missing:
        source[name] = pd.Series(dtype=object)
    if source["event_id"].isna().any() or source["event_id"].duplicated().any():
        raise ValueError("Case event IDs must be known and unique")
    if source["fold"].isna().any() or source["fold"].nunique() > 1:
        raise ValueError("Call source-control assignment once per fold")
    if isinstance(count, (bool, np.bool_)) or not isinstance(count, (int, np.integer)) or count < 1:
        raise ValueError("count must be a positive integer")
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, (int, np.integer)):
        raise ValueError("seed must be an integer")
    start, end = pd.to_datetime(start_inclusive, utc=True), pd.to_datetime(end_exclusive, utc=True)
    if start is None or end is None or pd.isna(start) or pd.isna(end) or start >= end:
        raise ValueError("Explicit finite increasing fold bounds are required")
    if isinstance(embargo_hours, (bool, np.bool_)) or not np.isfinite(embargo_hours) or embargo_hours < 0:
        raise ValueError("A finite nonnegative embargo is required")
    latest = end - pd.Timedelta(hours=embargo_hours)
    if latest <= start:
        raise ValueError("The fold must contain decisions before its embargo")
    h = frame.copy()
    required = {"decision_time", "signal_time", "entry_open", "signal_atr", "known_entry_open", "entry_source_continuous", "matching_support", "candidate_eligible", "month", "utc_6h_bucket", "vol_bucket", "known_5m_colour"}
    if not required.issubset(h):
        raise ValueError("Source matching frame missing columns: {}".format(sorted(required - set(h))))
    h["decision_time"] = pd.to_datetime(h["decision_time"], utc=True)
    if h["decision_time"].isna().any() or h["decision_time"].duplicated().any():
        raise ValueError("Matching decisions must be finite and unique")
    lookup = h.set_index("decision_time")
    source["decision_time"] = pd.to_datetime(source["decision_time"], utc=True, errors="coerce")
    actual = set(source["decision_time"].dropna())
    pool = h.loc[
        h["matching_support"].eq(True) & h["candidate_eligible"].eq(True)
        & h["decision_time"].ge(start) & h["decision_time"].lt(latest)
        & ~h["decision_time"].isin(actual)
    ]
    source = source.sort_values(["decision_time", "event_id"], kind="mergesort")
    controls, assignments, used_times = [], [], set()
    for case in source.to_dict("records"):
        time = case["decision_time"]
        row = {name: np.nan for name in ASSIGNMENT_COLUMNS}
        row.update(event_id=case["event_id"], mother_event_id=case["event_id"],
                   decision_time=time, entry_time=time, fold=case["fold"], direction=case["direction"],
                   parent_zone_id=case.get("zone_id", case.get("parent_zone_id", None)),
                   assigned_controls=0, eligible_controls_before_reuse=0, available_controls=0,
                   selected_control_times="[]", assignment_hash=_hash([]))

        def reject(reason: str) -> None:
            row["match_status"] = reason
            assignments.append(row)

        if pd.isna(time) or time not in lookup.index:
            reject("missing_case_hourly_decision")
            continue
        if time < start or time >= latest:
            reject("outside_fold_embargo")
            continue
        own = lookup.loc[time]
        for name in ("month", "utc_6h_bucket", "vol_bucket", "known_5m_colour"):
            row[name] = own[name]
        try:
            direction, stop, signal_atr = map(float, (case["direction"], case["initial_stop"], case["signal_atr"]))
        except (TypeError, ValueError):
            reject("invalid_case_risk")
            continue
        if direction not in (-1, 1) or not np.isfinite([stop, signal_atr]).all() or stop <= 0 or signal_atr <= 0:
            reject("invalid_case_risk")
            continue
        if not own["known_entry_open"] or not own["entry_source_continuous"]:
            reject("missing_or_gapped_case_open")
            continue
        risk_atr = direction * (float(own["entry_open"]) - stop) / signal_atr
        row["mother_risk_atr"] = risk_atr
        if not np.isfinite(risk_atr) or risk_atr <= 0:
            reject("invalid_case_risk")
            continue
        if not np.isclose(signal_atr, own["signal_atr"], rtol=1e-9, atol=1e-12):
            reject("case_atr_mismatch")
            continue
        if "signal_time" in case and pd.to_datetime(case["signal_time"], utc=True, errors="coerce") != own["signal_time"]:
            reject("case_signal_time_mismatch")
            continue
        if not own["matching_support"]:
            reject("missing_causal_matching_support")
            continue
        same = pool.loc[
            pool["month"].eq(own["month"]) & pool["utc_6h_bucket"].eq(own["utc_6h_bucket"])
            & pool["vol_bucket"].eq(own["vol_bucket"]) & pool["known_5m_colour"].eq(own["known_5m_colour"])
        ]
        synthetic_stop = same["entry_open"] - direction * risk_atr * same["signal_atr"]
        same = same.loc[np.isfinite(synthetic_stop) & synthetic_stop.gt(0)]
        row["eligible_controls_before_reuse"] = len(same)
        candidates = same.loc[~same["decision_time"].isin(used_times)].to_dict("records")
        candidates.sort(key=lambda candidate: hashlib.sha256(
            "{}|{}|{}".format(seed, case["event_id"], candidate["decision_time"].isoformat()).encode("utf-8")
        ).hexdigest())
        row["available_controls"] = len(candidates)
        if len(candidates) < count:
            reject("insufficient_exact_controls")
            continue
        selected_records = []
        for ordinal, candidate in enumerate(candidates[:count]):
            control = {
                "event_id": "{}::control{}".format(case["event_id"], ordinal),
                "parent_event_id": case["event_id"], "matched_event_id": case["event_id"],
                "source_mother_decision_time": time, "parent_zone_id": row["parent_zone_id"],
                "signal_time": candidate["signal_time"], "decision_time": candidate["decision_time"],
                "direction": int(direction), "initial_stop": candidate["entry_open"] - direction * risk_atr * candidate["signal_atr"],
                "signal_atr": candidate["signal_atr"], "transferred_risk_atr": risk_atr,
                "entry_open": candidate["entry_open"], "fold": case["fold"],
                "ma_slope_atr": direction * candidate.get("ma_slope_atr", np.nan),
                "signed_hourly_slope_sign": direction * candidate.get("unsigned_hourly_slope_sign", np.nan),
                "extension_atr": direction * (candidate["close"] - candidate.get("ma", np.nan)) / candidate["signal_atr"],
                "close_location": candidate.get("long_close_location" if direction == 1 else "short_close_location", np.nan),
            }
            for name in ("open", "high", "low", "close"):
                control["signal_" + name] = candidate[name]
            for name in ("ma", "ma_side", "body_ratio", "range_atr", "volume_ratio", "cross_count24", "efficiency24", "vol_bucket", "known_5m_colour", "known_5m_available", "known_hourly_colour", "source_segment_id", "month", "utc_6h_bucket"):
                control[name] = candidate.get(name, np.nan)
            controls.append(control)
            used_times.add(candidate["decision_time"])
            selected_records.append({name: control[name].isoformat() if isinstance(control[name], pd.Timestamp) else control[name]
                                     for name in ("event_id", "parent_event_id", "decision_time", "direction", "initial_stop", "signal_atr", "transferred_risk_atr")})
        row.update(match_status="matched", assigned_controls=int(count), assignment_hash=_hash(selected_records),
                   selected_control_times=json.dumps([item["decision_time"] for item in selected_records], separators=(",", ":")))
        assignments.append(row)
    control_frame = pd.DataFrame(controls, columns=CONTROL_COLUMNS)
    assignment_frame = pd.DataFrame(assignments, columns=ASSIGNMENT_COLUMNS)
    assignment_hash = _hash([{
        "event_id": row["event_id"], "match_status": row["match_status"], "assignment_hash": row["assignment_hash"],
    } for row in assignments])
    diagnostics = {
        "assignment_hash": assignment_hash, "assignments_sha256": assignment_hash, "seed": int(seed),
        "case_count": len(cases), "mother_count": len(cases),
        "matched_cases": int(assignment_frame["match_status"].eq("matched").sum()),
        "matched_mothers": int(assignment_frame["match_status"].eq("matched").sum()),
        "control_count": len(control_frame), "count_per_mother": int(count),
        "unique_control_times": len(used_times), "control_time_reuse_allowed": False,
        "case_status_counts": assignment_frame["match_status"].value_counts().sort_index().to_dict(),
        "candidate_count_before_exact_keys": len(pool), "case_rows_removed": 0, "mother_rows_removed": 0,
        "outcomes_used": False, "source_zone_gate_used": False, "hourly_colour_gate_used": False,
        "hourly_slope_gate_used": False, "sma_cross_exclusion_used": False,
        "future_window_exclusion_used": False, "fallback_used": False,
        "start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(), "embargo_hours": embargo_hours,
    }
    return control_frame, assignment_frame, diagnostics
