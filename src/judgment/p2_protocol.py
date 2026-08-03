"""Pre-registered P2-L2 split and selector rules for the immutable P1 pool.

The only permitted observations are rows in the content-addressed P1 short-L2
dataset.  Features stop at ``signal_time``; labels occupy the explicit
``[interval_start, interval_end]`` interval.  The main split therefore purges
rows whose label interval crosses a boundary and refuses an event group that
appears in more than one segment.  This is stricter than a row-count or fixed
72-bar purge and is the dependency rule required by the takeover plan.

The runtime selector is calibrated on scores only after training.  Its rule is
fixed here in advance: q90, ``>=``, no slicing of equal scores.  When the q90
boundary is separable, a midpoint makes the offline and runtime sets identical;
when it is tied, the whole equality block passes and the health gate decides
whether the resulting pass/equality rates are acceptable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

HOLDOUT_CUTOFF = pd.Timestamp("2026-05-04T00:00:00Z")
EARLY_STOP_START = pd.Timestamp("2026-03-27T00:00:00Z")
CALIBRATION_START = pd.Timestamp("2026-04-14T00:00:00Z")
CALIBRATION_QUANTILE = 0.90
THRESHOLD_OPERATOR = ">="
MAX_PASS_RATE_DEVIATION = 0.02
MAX_THRESHOLD_EQUAL_RATE = 0.02
MIN_DISTINCT_SCORES = 100


class P2ProtocolError(ValueError):
    """The P2 dataset, split, or selector violates its pre-registration."""


@dataclass(frozen=True)
class ThreeWaySplit:
    """Chronological train, early-stop validation, and calibration segments."""

    train: pd.DataFrame
    early_stop: pd.DataFrame
    calibration: pd.DataFrame
    purged: pd.DataFrame


def _as_utc(frame: pd.DataFrame, column: str) -> pd.Series:
    try:
        values = pd.to_datetime(frame[column], utc=True, errors="raise")
    except (KeyError, TypeError, ValueError) as exc:
        raise P2ProtocolError(f"invalid or missing {column}") from exc
    if values.isna().any():
        raise P2ProtocolError(f"{column} contains null timestamps")
    return values


def prepare_three_way_split(frame: pd.DataFrame) -> ThreeWaySplit:
    """Create the pre-registered split and purge all cross-boundary intervals.

    Columns used: ``signal_time``, ``interval_start``, ``interval_end``, and
    ``event_group_id``.  No outcome or feature value participates in choosing a
    boundary or assigning a row.
    """
    data = frame.copy()
    for column in ("signal_time", "interval_start", "interval_end"):
        data[column] = _as_utc(data, column)
    if "event_group_id" not in data or data["event_group_id"].isna().any():
        raise P2ProtocolError("event_group_id is required and must be non-null")
    if data["event_group_id"].astype(str).str.strip().eq("").any():
        raise P2ProtocolError("event_group_id must not be blank")
    if (data["interval_start"] < data["signal_time"]).any():
        raise P2ProtocolError("label interval starts before signal_time")
    if (data["interval_end"] < data["interval_start"]).any():
        raise P2ProtocolError("label interval end precedes its start")
    if (data["signal_time"] >= HOLDOUT_CUTOFF).any():
        raise P2ProtocolError("holdout signal reached P2")
    if (data["interval_end"] >= HOLDOUT_CUTOFF).any():
        raise P2ProtocolError("label interval reached holdout")

    train_mask = (
        (data["signal_time"] < EARLY_STOP_START)
        & (data["interval_end"] < EARLY_STOP_START)
    )
    early_mask = (
        (data["signal_time"] >= EARLY_STOP_START)
        & (data["signal_time"] < CALIBRATION_START)
        & (data["interval_end"] < CALIBRATION_START)
    )
    calibration_mask = data["signal_time"] >= CALIBRATION_START

    masks = (train_mask, early_mask, calibration_mask)
    assigned = train_mask | early_mask | calibration_mask
    # A row whose label interval crosses a boundary taints its complete
    # connected component.  Without this propagation, a neighbour from the
    # same event could survive on the other side even though the crossing row
    # itself was removed.
    boundary_groups = set(
        data.loc[~assigned, "event_group_id"].astype(str)
    )
    if boundary_groups:
        boundary_group_mask = data["event_group_id"].astype(str).isin(boundary_groups)
        assigned &= ~boundary_group_mask
        masks = tuple(mask & ~boundary_group_mask for mask in masks)
    parts = [data.loc[mask].copy() for mask in masks]
    group_sets = [set(part["event_group_id"].astype(str)) for part in parts]
    shared = (group_sets[0] & group_sets[1]) | (group_sets[0] & group_sets[2]) | (
        group_sets[1] & group_sets[2]
    )
    if shared:
        # Purge the complete connected component instead of choosing which side
        # keeps it.  That preserves the dependency contract without using labels.
        shared_mask = data["event_group_id"].astype(str).isin(shared)
        assigned &= ~shared_mask
        parts = [data.loc[mask & ~shared_mask].copy() for mask in masks]

    train, early, calibration = (
        part.sort_values(["signal_time", "event_group_id"]).reset_index(drop=True)
        for part in parts
    )
    purged = data.loc[~assigned].sort_values(
        ["signal_time", "event_group_id"]
    ).reset_index(drop=True)
    final_group_sets = [
        set(part["event_group_id"].astype(str))
        for part in (train, early, calibration)
    ]
    if (
        final_group_sets[0] & final_group_sets[1]
        or final_group_sets[0] & final_group_sets[2]
        or final_group_sets[1] & final_group_sets[2]
    ):
        raise P2ProtocolError("event group survived in multiple segments")
    if any(part.empty for part in (train, early, calibration)):
        raise P2ProtocolError("a pre-registered split segment is empty")
    return ThreeWaySplit(train, early, calibration, purged)


def apply_runtime_gate(scores: np.ndarray, *, threshold: float) -> np.ndarray:
    """Apply the pre-registered runtime operator (``score >= threshold``)."""
    values = np.asarray(scores, dtype=float)
    if not np.isfinite(values).all() or not np.isfinite(float(threshold)):
        raise P2ProtocolError("runtime gate requires finite scores and threshold")
    return values >= float(threshold)


def calibrate_runtime_gate(scores: np.ndarray) -> dict[str, object]:
    """Calibrate q90 without arbitrary slicing of a boundary tie.

    This function receives scores only.  It never sees returns or labels, so it
    cannot move the gate in response to profitability.
    """
    values = np.asarray(scores, dtype=float)
    if values.ndim != 1 or len(values) < 10 or not np.isfinite(values).all():
        raise P2ProtocolError("calibration scores must be a finite 1-D array with n>=10")
    selected_target = max(1, len(values) // 10)
    descending = np.sort(values)[::-1]
    selected_boundary = float(descending[selected_target - 1])
    rejected_boundary = float(descending[selected_target])
    boundary_separable = selected_boundary > rejected_boundary
    if boundary_separable:
        threshold = rejected_boundary + (selected_boundary - rejected_boundary) / 2.0
        if not rejected_boundary < threshold <= selected_boundary:
            threshold = float(np.nextafter(rejected_boundary, selected_boundary))
    else:
        threshold = selected_boundary

    mask = apply_runtime_gate(values, threshold=threshold)
    pass_rate = float(mask.mean())
    equal_rate = float(np.mean(values == threshold))
    distinct = int(np.unique(values).size)
    expected_rate = 1.0 - CALIBRATION_QUANTILE
    health_checks = {
        "pass_rate_within_8_12pct": abs(pass_rate - expected_rate)
        <= MAX_PASS_RATE_DEVIATION,
        "threshold_equal_rate_le_2pct": equal_rate <= MAX_THRESHOLD_EQUAL_RATE,
        "distinct_scores_ge_100": distinct >= MIN_DISTINCT_SCORES,
    }
    return {
        "calibration_quantile": CALIBRATION_QUANTILE,
        "threshold": float(threshold),
        "threshold_operator": THRESHOLD_OPERATOR,
        "tie_policy": "never_slice_equal_scores; whole equality block follows >=",
        "n": int(len(values)),
        "target_selected_n": int(selected_target),
        "actual_selected_n": int(mask.sum()),
        "actual_pass_rate": pass_rate,
        "threshold_equal_n": int(np.sum(values == threshold)),
        "threshold_equal_rate": equal_rate,
        "distinct_score_count": distinct,
        "boundary_separable": bool(boundary_separable),
        "exact_top_decile_available_without_tie_slice": bool(boundary_separable),
        "health_checks": health_checks,
        "health_accepted": bool(all(health_checks.values())),
    }
