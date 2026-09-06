"""Outcome-free random mother assignment for K1 versus K2 waiting studies.

The mother, not a successful K2 or closed trade, is the assignment unit.
Hour ``open_time + 1h`` is its decision. Hourly ATR fraction uses that completed
hour; terciles use the previous 720 same-hourly-segment values, shifted one
observation, minimum 168. The latest completed 5m colour must be available
exactly at the mother decision. Hourly colour and direction-adjusted slope
sign also refer to the just-completed maternal hour. No 4h data are used.

Raw5 contributes only timestamps, opens and source-segment IDs. Source IDs are
mapped at the maternal hour OPEN: hourly segment counters are never compared
with 5m counters. Candidate exclusion uses raw strict body crosses completed
now or one hour earlier, and actual K1 decision timestamps. Future crossings,
waiting success, exits and PNL cannot affect a mother's assignment.

The real mother's known next-open K1 risk/ATR is transferred to each random
mother's own known open and maternal ATR. Invalid real risk is reported only
as unmatchable; the original mother frame is never altered or filtered. These
controls share measured strata, not randomized treatment, so matching alone
does not establish a causal treatment effect.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


CONTROL_COLUMNS = [
    "event_id", "parent_event_id", "matched_event_id", "source_mother_decision_time",
    "signal_time", "decision_time", "direction", "initial_stop", "signal_atr",
    "transferred_risk_atr", "entry_open", "fold", "signal_open", "signal_high",
    "signal_low", "signal_close", "ma", "ma_side", "ma_slope_atr", "body_ratio",
    "range_atr", "volume_ratio", "cross_count24", "efficiency24", "close_location",
    "extension_atr", "vol_bucket", "known_5m_colour", "known_5m_available",
    "known_hourly_colour", "signed_hourly_slope_sign", "source_segment_id",
]


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
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


def build_matching_frame(
    raw5: pd.DataFrame,
    hourly_featured: pd.DataFrame,
    management_featured: pd.DataFrame,
    all_k1_requests: pd.DataFrame,
) -> pd.DataFrame:
    """Return all hourly matching states and explicit support/exclusion flags.

    Every feature is known by its own candidate's decision, including the
    contemporaneous next open used for risk. The full frame, not just eligible
    rows, supports audits that mutate future candles or gap segment counters.
    ``candidate_eligible`` has no outcome, waiting or 4h condition.
    """
    raw = _validate(raw5, ["open_time", "open", "segment_id"], "raw5")
    h = _validate(hourly_featured, ["open_time", "open", "high", "low", "close", "atr", "ma", "ma_side", "ma_slope_atr", "segment_id"], "hourly_featured")
    management = _validate(management_featured, ["open_time", "ma_side", "segment_id"], "management_featured")
    if "decision_time" not in all_k1_requests:
        raise ValueError("all_k1_requests requires decision_time")
    if not h["open_time"].eq(h["open_time"].dt.floor("h")).all():
        raise ValueError("Maternal hours must use exact UTC hour opens")
    h["signal_time"] = h["open_time"]
    h["decision_time"] = h["open_time"] + pd.Timedelta(hours=1)
    h["signal_atr"] = _numeric(h, "atr")
    h["atr_fraction"] = h["signal_atr"] / _numeric(h, "close").where(_numeric(h, "close").gt(0))
    for column, q in (("atr_tercile_low", 1/3), ("atr_tercile_high", 2/3)):
        h[column] = h.groupby("segment_id")["atr_fraction"].transform(
            lambda values: values.shift(1).rolling(720, min_periods=168).quantile(q)
        )
    vol_valid = h[["atr_fraction", "atr_tercile_low", "atr_tercile_high"]].notna().all(axis=1)
    bucket = h["atr_fraction"].gt(h["atr_tercile_low"]).astype(int) + h["atr_fraction"].gt(h["atr_tercile_high"]).astype(int)
    h["vol_bucket"] = bucket.where(vol_valid).astype("Int64")
    indexed_raw = raw.set_index("open_time")
    h["source_segment_id"] = h["open_time"].map(indexed_raw["segment_id"])
    h["entry_source_segment_id"] = h["decision_time"].map(indexed_raw["segment_id"])
    h["entry_open"] = pd.to_numeric(h["decision_time"].map(indexed_raw["open"]), errors="coerce")
    h["known_entry_open"] = np.isfinite(h["entry_open"]) & h["entry_open"].gt(0)
    h["entry_source_continuous"] = h["source_segment_id"].notna() & h["entry_source_segment_id"].eq(h["source_segment_id"])
    colour = management[["open_time", "ma_side", "segment_id"]].copy()
    colour["known_5m_available"] = colour["open_time"] + pd.Timedelta(minutes=5)
    colour = colour.rename(columns={"ma_side": "known_5m_colour", "segment_id": "management_source_segment_id"})
    h = pd.merge_asof(
        h.sort_values("decision_time"),
        colour[["known_5m_available", "known_5m_colour", "management_source_segment_id"]].sort_values("known_5m_available"),
        left_on="decision_time", right_on="known_5m_available", direction="backward",
    )
    h["known_5m_valid"] = h["known_5m_available"].eq(h["decision_time"]) & h["known_5m_colour"].isin([-1, 1]) & h["management_source_segment_id"].eq(h["source_segment_id"])
    h["known_hourly_colour"] = _numeric(h, "ma_side")
    h["unsigned_hourly_slope_sign"] = np.sign(_numeric(h, "ma_slope_atr"))
    h["known_hourly_valid"] = h["known_hourly_colour"].isin([-1, 1]) & h["unsigned_hourly_slope_sign"].notna() & _numeric(h, "ma").notna()
    h["month"] = h["decision_time"].dt.strftime("%Y-%m")
    h["utc_6h_bucket"] = h["decision_time"].dt.hour // 6
    h["raw_strict_body_cross"] = ((_numeric(h, "open") < _numeric(h, "ma")) & (_numeric(h, "close") > _numeric(h, "ma"))) | ((_numeric(h, "open") > _numeric(h, "ma")) & (_numeric(h, "close") < _numeric(h, "ma")))
    cross_times = set(h.loc[h["raw_strict_body_cross"], "decision_time"])
    banned = cross_times | {time + pd.Timedelta(hours=1) for time in cross_times}
    h["current_or_prior_cross_excluded"] = h["decision_time"].isin(banned)
    actual_times = pd.to_datetime(all_k1_requests["decision_time"], utc=True, errors="coerce").dropna()
    h["actual_mother_decision_excluded"] = h["decision_time"].isin(set(actual_times))
    h["matching_support"] = h["vol_bucket"].notna() & h["signal_atr"].gt(0) & h["known_entry_open"] & h["entry_source_continuous"] & h["known_5m_valid"] & h["known_hourly_valid"]
    h["candidate_eligible"] = h["matching_support"] & ~h["current_or_prior_cross_excluded"] & ~h["actual_mother_decision_excluded"]
    return h


def assign_controls(
    mothers: pd.DataFrame,
    matching_frame: pd.DataFrame,
    *,
    count: int = 3,
    seed: int = 20260906,
    end_exclusive: Any,
    embargo_hours: float = 72,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Assign controls to every original mother before any K2 wait or outcome.

    Call once per fold: one ``end_exclusive`` cannot encode several fold ends.
    Exact month matching keeps candidates within the same registered halfyear.
    A mother after ``end_exclusive - embargo_hours`` remains in assignments as
    excluded; it is not removed from the caller's waitbuilder input. Original
    outcome/closed/K2-success columns, if supplied, are ignored.
    """
    required = {"event_id", "decision_time", "direction", "initial_stop", "signal_atr", "fold"}
    if not required.issubset(mothers):
        raise ValueError("Mother table missing columns: {}".format(sorted(required - set(mothers))))
    if mothers["event_id"].isna().any() or mothers["event_id"].duplicated().any():
        raise ValueError("Mother event IDs must be finite and unique")
    if mothers["fold"].isna().any() or mothers["fold"].nunique() > 1:
        raise ValueError("Call control assignment once per fold")
    if isinstance(count, bool) or int(count) != count or count < 1:
        raise ValueError("count must be a positive integer")
    end = pd.to_datetime(end_exclusive, utc=True)
    if pd.isna(end) or not np.isfinite(embargo_hours) or embargo_hours < 0:
        raise ValueError("A finite fold end and nonnegative embargo are required")
    latest_decision = end - pd.Timedelta(hours=embargo_hours)
    h = matching_frame.copy()
    h["decision_time"] = pd.to_datetime(h["decision_time"], utc=True)
    if h["decision_time"].duplicated().any() or h["decision_time"].isna().any():
        raise ValueError("Matching frame needs unique finite decisions")
    lookup = h.set_index("decision_time")
    original_times = set(pd.to_datetime(mothers["decision_time"], utc=True, errors="coerce").dropna())
    # Defensively exclude all supplied real mothers even when the frame was
    # constructed with an incomplete request list. No forward offsets are used.
    pool = h.loc[h["candidate_eligible"] & h["decision_time"].lt(latest_decision) & ~h["decision_time"].isin(original_times)]
    source = mothers.copy()
    source["decision_time"] = pd.to_datetime(source["decision_time"], utc=True, errors="coerce")
    source = source.sort_values(["decision_time", "event_id"], kind="mergesort")
    requests, assignments, used_times = [], [], set()

    for mother in source.to_dict("records"):
        time = mother["decision_time"]
        base = {"event_id": mother["event_id"], "mother_event_id": mother["event_id"], "decision_time": time, "entry_time": time, "fold": mother["fold"], "direction": mother["direction"]}

        def reject(status: str, **extra: Any) -> None:
            assignments.append({**base, "match_status": status, "assigned_controls": 0, **extra})

        if pd.isna(time) or time not in lookup.index:
            reject("missing_mother_hourly_decision")
            continue
        if time >= latest_decision:
            reject("outside_fold_embargo")
            continue
        own = lookup.loc[time]
        try:
            direction, stop, signal_atr = float(mother["direction"]), float(mother["initial_stop"]), float(mother["signal_atr"])
        except (TypeError, ValueError):
            reject("invalid_mother_risk")
            continue
        if direction not in (-1, 1) or not np.isfinite([stop, signal_atr]).all() or stop <= 0 or signal_atr <= 0:
            reject("invalid_mother_risk")
            continue
        if not own["known_entry_open"] or not own["entry_source_continuous"]:
            reject("missing_or_gapped_mother_open")
            continue
        risk_atr = direction * (float(own["entry_open"]) - stop) / signal_atr
        if not np.isfinite(risk_atr) or risk_atr <= 0:
            reject("invalid_mother_risk")
            continue
        if not np.isclose(signal_atr, own["signal_atr"], rtol=1e-9, atol=1e-12):
            reject("mother_atr_mismatch")
            continue
        if not own["matching_support"]:
            reject("missing_causal_matching_support", mother_risk_atr=risk_atr)
            continue
        own_signed_slope = direction * own["unsigned_hourly_slope_sign"]
        same = pool.loc[
            pool["month"].eq(own["month"])
            & pool["utc_6h_bucket"].eq(own["utc_6h_bucket"])
            & pool["vol_bucket"].eq(own["vol_bucket"])
            & pool["known_5m_colour"].eq(own["known_5m_colour"])
            & pool["known_hourly_colour"].eq(own["known_hourly_colour"])
            & (direction * pool["unsigned_hourly_slope_sign"]).eq(own_signed_slope)
            & ~pool["decision_time"].isin(used_times)
        ]
        synthetic_stop = same["entry_open"] - direction * risk_atr * same["signal_atr"]
        same = same.loc[np.isfinite(synthetic_stop) & synthetic_stop.gt(0)]
        candidates = same.to_dict("records")
        candidates.sort(key=lambda row: hashlib.sha256("{}|{}|{}".format(seed, mother["event_id"], row["decision_time"].isoformat()).encode("utf-8")).hexdigest())
        if len(candidates) < count:
            reject("insufficient_exact_controls", available_controls=len(candidates), mother_risk_atr=risk_atr)
            continue
        for ordinal, candidate in enumerate(candidates[:count]):
            control = {
                "event_id": "{}::control{}".format(mother["event_id"], ordinal),
                "parent_event_id": mother["event_id"], "matched_event_id": mother["event_id"],
                "source_mother_decision_time": time, "signal_time": candidate["signal_time"],
                "decision_time": candidate["decision_time"], "direction": int(direction),
                "initial_stop": candidate["entry_open"] - direction * risk_atr * candidate["signal_atr"],
                "signal_atr": candidate["signal_atr"], "transferred_risk_atr": risk_atr,
                "entry_open": candidate["entry_open"], "fold": mother["fold"],
                "ma_slope_atr": direction * candidate["ma_slope_atr"],
                "extension_atr": direction * (candidate["close"] - candidate["ma"]) / candidate["signal_atr"],
                "close_location": candidate.get("long_close_location" if direction == 1 else "short_close_location", np.nan),
                "signed_hourly_slope_sign": own_signed_slope,
            }
            for field in ("open", "high", "low", "close"):
                control["signal_" + field] = candidate[field]
            for field in ("ma", "ma_side", "body_ratio", "range_atr", "volume_ratio", "cross_count24", "efficiency24", "vol_bucket", "known_5m_colour", "known_5m_available", "known_hourly_colour", "source_segment_id"):
                control[field] = candidate.get(field, np.nan)
            requests.append(control)
            used_times.add(candidate["decision_time"])
        assignments.append({**base, "match_status": "matched", "assigned_controls": count, "available_controls": len(candidates),
                            "mother_risk_atr": risk_atr, "month": own["month"], "utc_6h_bucket": own["utc_6h_bucket"],
                            "vol_bucket": own["vol_bucket"], "known_5m_colour": own["known_5m_colour"],
                            "known_hourly_colour": own["known_hourly_colour"], "signed_hourly_slope_sign": own_signed_slope})
    controls = pd.DataFrame(requests, columns=CONTROL_COLUMNS)
    assignment_frame = pd.DataFrame(assignments)
    if assignment_frame.empty:
        assignment_frame = pd.DataFrame(columns=["event_id", "mother_event_id", "decision_time", "entry_time", "fold", "direction", "match_status", "assigned_controls"])
    hash_records = [{key: row[key].isoformat() if isinstance(row[key], pd.Timestamp) else row[key]
                     for key in ("event_id", "parent_event_id", "decision_time", "direction", "initial_stop", "signal_atr", "transferred_risk_atr")}
                    for row in requests]
    assignment_hash = hashlib.sha256(json.dumps(hash_records, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    diagnostics = {
        "assignment_hash": assignment_hash, "assignments_sha256": assignment_hash, "seed": seed,
        "mother_count": len(mothers), "matched_mothers": int(assignment_frame["match_status"].eq("matched").sum()),
        "control_count": len(controls), "count_per_mother": count, "unique_control_times": len(used_times),
        "mother_status_counts": assignment_frame["match_status"].value_counts().sort_index().to_dict(),
        "candidate_count_before_exact_keys": len(pool), "control_time_reuse_allowed": False,
        "outcomes_used": False, "k2_success_used": False, "mother_rows_removed": 0,
        "future_cross_exclusion_used": False, "fallback_used": False,
        "end_exclusive": end.isoformat(), "embargo_hours": embargo_hours,
    }
    return controls, assignment_frame, diagnostics
