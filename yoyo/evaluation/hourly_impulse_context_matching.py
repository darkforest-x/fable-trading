"""Causal, outcome-free control assignment for hourly context experiments.

Features: hourly ATR fraction uses the current completed signal-hour ATR/close;
its terciles use only the preceding 720 same-segment hourly observations
(minimum 168), shifted by one hour. Five-minute colour is available at that
bar's open + 5 minutes and must be completed at the decision timestamp. Prior
4h context is supplied at the SIGNAL HOUR OPEN and its available timestamp is
checked against that open. The 24-hour crossing/efficiency and volume features
are copied from the supplied causal hourly frame, never recomputed using labels.

Random candidates exclude only a strict body crossing completed now or one
hour ago. A crossing completed in the future cannot exclude a current candidate.
The original event's exact next-open fill establishes its risk/ATR; each control
transfers that ratio to its own prior-hour ATR and contemporaneous open. Only
raw5m open/time/segment columns are used. Outcome, close-status and PNL columns
in events are ignored. No trades are simulated and no files are read or written.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd


CONTEXT_COLUMNS = ["context_valid", "context_side", "context_slope_atr", "context_available"]
REQUEST_COLUMNS = [
    "event_id", "parent_event_id", "signal_time", "decision_time", "direction",
    "signal_open", "signal_high", "signal_low", "signal_close", "initial_stop",
    "signal_atr", "transferred_risk_atr", "fold", "ma", "ma_side", "body_ratio",
    "range_atr", "volume_ratio", "cross_count24", "efficiency24", "ma_slope_atr",
    "close_location", "extension_atr", "context_valid", "context_side",
    "context_slope_atr", "context_available", "vol_bucket", "ltf_side",
    "ltf_available", "entry_open", "context_slope_sign",
]


def _numeric(frame: pd.DataFrame, key: str) -> pd.Series:
    return pd.to_numeric(frame[key], errors="coerce")


def _bool(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])


def _validate(frame: pd.DataFrame, required: Sequence[str], label: str) -> pd.DataFrame:
    missing = set(required) - set(frame)
    if missing:
        raise ValueError("{} missing columns: {}".format(label, sorted(missing)))
    result = frame.copy()
    result["open_time"] = pd.to_datetime(result["open_time"], utc=True)
    times = result["open_time"]
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("{} timestamps must be unique, finite and chronological".format(label))
    return result


def _context_support(frame: pd.DataFrame) -> pd.Series:
    missing = set(CONTEXT_COLUMNS + ["open_time"]) - set(frame)
    if missing:
        raise ValueError("Missing common-context columns: {}".format(sorted(missing)))
    side, slope = _numeric(frame, "context_side"), _numeric(frame, "context_slope_atr")
    available = pd.to_datetime(frame["context_available"], utc=True, errors="coerce")
    signal_open = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    return _bool(frame["context_valid"]) & side.isin([-1, 1]) & np.isfinite(slope) & available.notna() & available.le(signal_open)


def arm_mask(frame: pd.DataFrame, direction: Any, arm: Mapping[str, Any]) -> pd.Series:
    """Shared root/matcher arm eligibility on UNSIGNED hourly feature columns.

    Direction is scalar +/-1 or an index-aligned Series. All arms require prior
    4h common-context support. Hourly slope is multiplied by direction once.
    Context trend requires same-side context and direction-adjusted slope >0.
    A finite extension cap requires 0 <= direction*(close-ma)/atr <= cap; an
    infinite cap adds no extension restriction. No future outcome is inspected.
    """
    required = {"ma_slope_atr", "close", "ma", "atr"}
    if not required.issubset(frame):
        raise ValueError("Hourly arm mask lacks MA/ATR features")
    if isinstance(direction, pd.Series):
        sign = pd.to_numeric(direction.reindex(frame.index), errors="coerce")
    else:
        sign = pd.Series(direction, index=frame.index, dtype=float)
    atr = _numeric(frame, "atr")
    ma, close = _numeric(frame, "ma"), _numeric(frame, "close")
    valid = _context_support(frame) & sign.isin([-1, 1]) & np.isfinite(atr) & atr.gt(0) & np.isfinite(ma) & np.isfinite(close)
    if arm.get("require_hourly_slope", False):
        hourly_slope = _numeric(frame, "ma_slope_atr")
        valid &= np.isfinite(hourly_slope) & (sign * hourly_slope).gt(0)
    if arm.get("require_context_trend", False):
        valid &= (sign * _numeric(frame, "context_side")).eq(1) & (sign * _numeric(frame, "context_slope_atr")).gt(0)
    maximum = float(arm.get("max_extension_atr", np.inf))
    if np.isnan(maximum) or maximum < 0:
        raise ValueError("max_extension_atr must be nonnegative or positive infinity")
    if np.isfinite(maximum):
        extension = sign * (close - ma) / atr
        valid &= extension.ge(0) & extension.le(maximum)
    return valid.fillna(False)


def build_matching_frame(
    hourly_context_frame: pd.DataFrame,
    raw5m: pd.DataFrame,
    management5m: pd.DataFrame,
    folds: Sequence[Sequence[Any]],
    max_hours: float = 72,
) -> pd.DataFrame:
    """Annotate each hour's known matching state, including explicit exclusions.

    Returning the full annotated frame permits prefix-invariance and exclusion
    audits. ``control_eligible`` is common support plus no current/prior raw
    crossing. Arm-specific direction filters are applied later by ``arm_mask``.
    No candidate uses a future management bar or future crossing status.
    """
    if not np.isfinite(max_hours) or max_hours <= 0:
        raise ValueError("max_hours must be finite and positive")
    required = ["open_time", "open", "high", "low", "close", "atr", "ma", "ma_side", "ma_slope_atr", "segment_id"] + CONTEXT_COLUMNS
    h = _validate(hourly_context_frame, required, "hourly_context_frame")
    raw = _validate(raw5m, ["open_time", "open", "segment_id"], "raw5m")
    management = _validate(management5m, ["open_time", "ma_side", "segment_id"], "management5m")
    h["context_available"] = pd.to_datetime(h["context_available"], utc=True, errors="coerce")
    h["signal_time"] = h["open_time"]
    h["decision_time"] = h["open_time"] + pd.Timedelta(hours=1)
    h["signal_atr"] = _numeric(h, "atr")
    h["atr_fraction"] = _numeric(h, "atr") / _numeric(h, "close").where(_numeric(h, "close").gt(0))
    for name, quantile in (("atr_cut_low", 1 / 3), ("atr_cut_high", 2 / 3)):
        h[name] = h.groupby("segment_id")["atr_fraction"].transform(
            lambda values: values.shift(1).rolling(720, min_periods=168).quantile(quantile)
        )
    valid_vol = np.isfinite(h["atr_fraction"]) & np.isfinite(h["atr_cut_low"]) & np.isfinite(h["atr_cut_high"])
    bucket = h["atr_fraction"].gt(h["atr_cut_low"]).astype(int) + h["atr_fraction"].gt(h["atr_cut_high"]).astype(int)
    h["vol_bucket"] = bucket.where(valid_vol).astype("Int64")

    colour = management[["open_time", "ma_side", "segment_id"]].copy()
    colour["ltf_available"] = colour["open_time"] + pd.Timedelta(minutes=5)
    colour = colour.rename(columns={"ma_side": "ltf_side", "segment_id": "ltf_segment_id"})
    h = pd.merge_asof(
        h.sort_values("decision_time"),
        colour[["ltf_available", "ltf_side", "ltf_segment_id"]].sort_values("ltf_available"),
        left_on="decision_time", right_on="ltf_available", direction="backward",
    )
    indexed_raw = raw.set_index("open_time")
    # Segment IDs are local to a time grid: two 5m gaps inside one rejected
    # hourly bar increment raw IDs twice but hourly IDs only once. Compare
    # lower-frame/entry segments with the original 5m segment at signal open;
    # hourly segment_id remains exclusively the hourly ATR-window grouping.
    h["signal_source_segment_id"] = h["open_time"].map(indexed_raw["segment_id"])
    # A complete source hour supplies its final completed 5m bar at decision.
    # Do not recycle a colour across a missing source bar or a new segment.
    h["known_ltf_colour"] = h["ltf_available"].eq(h["decision_time"]) & h["ltf_side"].isin([-1, 1]) & h["ltf_segment_id"].eq(h["signal_source_segment_id"])
    h["entry_open"] = pd.to_numeric(h["decision_time"].map(indexed_raw["open"]), errors="coerce")
    h["entry_segment_id"] = h["decision_time"].map(indexed_raw["segment_id"])
    h["known_entry_open"] = np.isfinite(pd.to_numeric(h["entry_open"], errors="coerce")) & h["entry_open"].gt(0)
    h["entry_segment_valid"] = h["entry_segment_id"].eq(h["signal_source_segment_id"])
    h["known_context"] = _context_support(h)
    h["context_slope_sign"] = np.sign(_numeric(h, "context_slope_atr"))
    h["month"] = h["decision_time"].dt.strftime("%Y-%m")
    h["utc_session"] = h["decision_time"].dt.hour // 6
    h["raw_body_cross"] = ((_numeric(h, "open") < _numeric(h, "ma")) & (_numeric(h, "close") > _numeric(h, "ma"))) | ((_numeric(h, "open") > _numeric(h, "ma")) & (_numeric(h, "close") < _numeric(h, "ma")))
    crosses = set(h.loc[h["raw_body_cross"], "decision_time"])
    banned = crosses | {time + pd.Timedelta(hours=1) for time in crosses}
    h["past_or_current_cross_banned"] = h["decision_time"].isin(banned)
    h["fold"] = pd.Series(pd.NA, index=h.index, dtype="object")
    horizon = pd.Timedelta(hours=max_hours)
    previous_end = None
    fold_ids = set()
    for name, start, end in folds:
        start, end = pd.to_datetime(start, utc=True), pd.to_datetime(end, utc=True)
        if pd.isna(start) or pd.isna(end) or start >= end or name in fold_ids or (previous_end is not None and start < previous_end):
            raise ValueError("Folds must have unique IDs and ordered nonoverlapping intervals")
        fold_ids.add(name)
        previous_end = end
        within = h["decision_time"].ge(start) & h["decision_time"].lt(end - horizon)
        h.loc[within, "fold"] = name
    h["common_eligible"] = h["known_context"] & valid_vol.to_numpy() & h["known_ltf_colour"] & h["known_entry_open"] & h["entry_segment_valid"] & h["fold"].notna() & h["signal_atr"].gt(0)
    h["control_eligible"] = h["common_eligible"] & ~h["past_or_current_cross_banned"]
    return h


def assign_controls(
    hourly_context_frame: pd.DataFrame,
    raw5m: pd.DataFrame,
    management5m: pd.DataFrame,
    events: pd.DataFrame,
    arm: Mapping[str, Any],
    folds: Sequence[Sequence[Any]],
    max_hours: float = 72,
    count: int = 3,
    seed: int = 20260906,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Return deterministic control REQUESTS, all-event statuses and a receipt.

    All events are considered regardless of outcome/PNL columns. No random
    timestamp can be reused within an arm. Exact matching requires month,
    UTC six-hour session, causal ATR tercile, known 5m colour, prior 4h side and
    context-slope sign; every selected control must also pass the same arm mask.
    Insufficient matches are reported without relaxing any constraint.
    """
    required_events = {"event_id", "fold", "direction", "decision_time", "initial_stop", "signal_atr"}
    if not required_events.issubset(events):
        raise ValueError("Missing event request columns: {}".format(sorted(required_events - set(events))))
    if events["event_id"].isna().any() or events["event_id"].duplicated().any():
        raise ValueError("Source event IDs must be finite and unique")
    if isinstance(count, bool) or int(count) != count or count < 1:
        raise ValueError("count must be a positive integer")
    h = build_matching_frame(hourly_context_frame, raw5m, management5m, folds, max_hours)
    lookup = h.set_index("decision_time")
    pool = h.loc[h["control_eligible"]].copy()
    arm_pools = {direction: pool.loc[arm_mask(pool, direction, arm)] for direction in (-1, 1)}
    source = events.copy()
    source["decision_time"] = pd.to_datetime(source["decision_time"], utc=True, errors="coerce")
    source = source.sort_values(["decision_time", "event_id"], kind="mergesort")
    requests, pairs, used_times = [], [], set()

    for event in source.to_dict("records"):
        time = event["decision_time"]
        base_pair = {"event_id": event["event_id"], "decision_time": time, "entry_time": time, "fold": event["fold"], "direction": event["direction"]}

        def reject(status: str, **extra: Any) -> None:
            pairs.append({**base_pair, "match_status": status, "matched_controls": 0, **extra})

        if pd.isna(time) or time not in lookup.index:
            reject("missing_hourly_decision")
            continue
        own = lookup.loc[time]
        try:
            direction, stop, event_atr = float(event["direction"]), float(event["initial_stop"]), float(event["signal_atr"])
        except (TypeError, ValueError):
            reject("invalid_event_risk")
            continue
        if direction not in (-1, 1) or not np.isfinite([stop, event_atr]).all() or stop <= 0 or event_atr <= 0:
            reject("invalid_event_risk")
            continue
        if not own["known_context"]:
            reject("invalid_prior_context")
            continue
        if not own["known_entry_open"] or not own["entry_segment_valid"]:
            reject("missing_or_gapped_entry_open")
            continue
        if not own["known_ltf_colour"]:
            reject("missing_completed_5m_colour")
            continue
        if pd.isna(own["vol_bucket"]):
            reject("missing_causal_atr_bucket")
            continue
        if pd.isna(own["fold"]) or own["fold"] != event["fold"]:
            reject("outside_registered_fold_horizon")
            continue
        if not np.isclose(event_atr, own["signal_atr"], rtol=1e-9, atol=1e-12):
            reject("event_signal_atr_mismatch")
            continue
        if not bool(arm_mask(own.to_frame().T, direction, arm).iloc[0]):
            reject("event_arm_filter_mismatch")
            continue
        risk_atr = direction * (float(own["entry_open"]) - stop) / event_atr
        if not np.isfinite(risk_atr) or risk_atr <= 0:
            reject("invalid_event_risk")
            continue
        candidates = arm_pools[int(direction)]
        exact = candidates.loc[
            candidates["fold"].eq(event["fold"])
            & candidates["month"].eq(own["month"])
            & candidates["utc_session"].eq(own["utc_session"])
            & candidates["vol_bucket"].eq(own["vol_bucket"])
            & candidates["ltf_side"].eq(own["ltf_side"])
            & candidates["context_side"].eq(own["context_side"])
            & candidates["context_slope_sign"].eq(own["context_slope_sign"])
            & ~candidates["decision_time"].isin(used_times)
            & candidates["decision_time"].ne(time)
        ]
        # Invalid transferred long stops would be rejected at execution. Keep
        # that known-at-fill risk condition in the candidate pool, not labels.
        transferred_stop = exact["entry_open"] - direction * risk_atr * exact["signal_atr"]
        exact = exact.loc[np.isfinite(transferred_stop) & transferred_stop.gt(0)]
        ordered = exact.to_dict("records")
        ordered.sort(key=lambda row: hashlib.sha256("{}|{}|{}".format(seed, event["event_id"], row["decision_time"].isoformat()).encode("utf-8")).hexdigest())
        if len(ordered) < count:
            reject("insufficient_exact_controls", available_controls=len(ordered), event_risk_atr=risk_atr)
            continue
        for position, candidate in enumerate(ordered[:count]):
            record = {
                "event_id": "{}::control{}".format(event["event_id"], position), "parent_event_id": event["event_id"],
                "signal_time": candidate["open_time"], "decision_time": candidate["decision_time"], "direction": int(direction),
                "initial_stop": candidate["entry_open"] - direction * risk_atr * candidate["signal_atr"],
                "signal_atr": candidate["signal_atr"], "transferred_risk_atr": risk_atr, "fold": candidate["fold"],
                "ma": candidate["ma"], "ma_side": candidate["ma_side"],
                "ma_slope_atr": direction * candidate["ma_slope_atr"],
                "extension_atr": direction * (candidate["close"] - candidate["ma"]) / candidate["signal_atr"],
                "close_location": candidate.get("long_close_location" if direction == 1 else "short_close_location", np.nan),
            }
            for column in ("open", "high", "low", "close"):
                record["signal_" + column] = candidate[column]
            for column in ("body_ratio", "range_atr", "volume_ratio", "cross_count24", "efficiency24") + tuple(CONTEXT_COLUMNS) + ("vol_bucket", "ltf_side", "ltf_available", "entry_open", "context_slope_sign"):
                record[column] = candidate.get(column, np.nan)
            requests.append(record)
            used_times.add(candidate["decision_time"])
        pairs.append({**base_pair, "match_status": "matched", "matched_controls": count, "available_controls": len(ordered), "event_risk_atr": risk_atr,
                      "month": own["month"], "utc_session": own["utc_session"], "vol_bucket": own["vol_bucket"], "ltf_side": own["ltf_side"],
                      "context_side": own["context_side"], "context_slope_sign": own["context_slope_sign"]})
    request_frame = pd.DataFrame(requests, columns=REQUEST_COLUMNS)
    pair_frame = pd.DataFrame(pairs)
    if pair_frame.empty:
        pair_frame = pd.DataFrame(columns=["event_id", "decision_time", "entry_time", "fold", "direction", "match_status", "matched_controls"])
    hash_rows = []
    for row in requests:
        hash_rows.append({
            key: row[key].isoformat() if isinstance(row[key], pd.Timestamp) else row[key]
            for key in ("event_id", "parent_event_id", "decision_time", "direction", "initial_stop", "signal_atr", "transferred_risk_atr")
        })
    assignment_hash = hashlib.sha256(json.dumps(hash_rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    receipt = {
        "assignment_hash": assignment_hash, "assignments_sha256": assignment_hash, "seed": seed,
        "arm_id": arm.get("id"), "event_count": len(events), "matched_events": int(pair_frame["match_status"].eq("matched").sum()),
        "request_count": len(requests), "count_per_event": count, "unique_control_times": len(used_times),
        "control_time_reuse_allowed": False, "outcomes_used": False, "fallback_used": False,
        "future_cross_exclusion_used": False, "context_available_by_signal_open": True,
        "candidate_hour_count": len(h), "common_support_count": int(h["common_eligible"].sum()),
        "eligible_control_count_before_arm": int(h["control_eligible"].sum()),
        "event_status_counts": pair_frame["match_status"].value_counts().sort_index().to_dict(),
    }
    return request_frame, pair_frame, receipt
