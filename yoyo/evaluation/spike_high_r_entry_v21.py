"""Causal low-reference-risk entry gate for an already prepared SPIKE V9 arm.

This offline V2.1 gate reads no price path or outcome.  At every original V9
long signal it joins the confirmation-close ``reference_risk_fraction`` emitted
by :func:`spike_v9_full_replay.prepare_v9` to a root-built monthly panel.  The
panel's 10th-percentile cutoff is restricted to the three complete calendar
months before the UTC month containing the signal's available-at time.  Actual
next-open fills, stops, costs, raw reversals and all parent serial behaviour
remain the frozen V9 contract.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v1_v8_be05 as engine


VERSION = POLICY = "high_r_entry_v21"
QUANTILE = 0.1
LOOKBACK_MONTHS = 3
MIN_HISTORY = 100

_DECISION_COLUMNS = [
    "local_i", "signal_i", "stream_key", "signal_bar_open", "available_at", "side", "score",
    "gate_known", "gate_passed", "reason", "baseline_allowed", "risk_cutoff", "threshold_month",
    "n_history", "reference_risk_fraction",
]
_CANDIDATE_COLUMNS = {"local_i", "signal_i", "side", "reference_risk_fraction"}
_THRESHOLD_COLUMNS = {"timeframe_min", "month", "cutoff", "history_end", "history_start", "n_history", "known"}


def _as_utc(value: object, *, field: str) -> pd.Timestamp:
    """Parse one threshold timestamp as an unambiguous UTC instant."""
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(f"invalid {field}")
    return stamp.tz_convert("UTC")


def _month_start(stamp: pd.Timestamp) -> pd.Timestamp:
    """Return the UTC calendar-month start that contains an available-at time."""
    utc = stamp.tz_convert("UTC")
    return utc.normalize().replace(day=1)


def _known_flag(value: object) -> bool:
    """Parse a threshold availability flag without truthiness coercion."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value in {"true", "false", "True", "False"}:
        return value == "true" or value == "True"
    raise ValueError("invalid threshold known flag")


def _threshold_lookup(thresholds: pd.DataFrame) -> dict[tuple[int, str], dict[str, object]]:
    """Validate and index root-built per-timeframe UTC calendar-month cutoffs."""
    missing = _THRESHOLD_COLUMNS.difference(thresholds.columns)
    if missing:
        raise ValueError(f"thresholds missing columns: {sorted(missing)}")
    indexed: dict[tuple[int, str], dict[str, object]] = {}
    for row in thresholds.loc[:, sorted(_THRESHOLD_COLUMNS)].itertuples(index=False):
        values = row._asdict()
        try:
            timeframe = int(values["timeframe_min"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid threshold timeframe") from exc
        month_value = values["month"]
        if (timeframe <= 0 or not isinstance(month_value, str) or len(month_value) != 7
                or month_value[4] != "-" or not month_value[:4].isdigit() or not month_value[5:].isdigit()):
            raise ValueError("invalid threshold month")
        try:
            month = pd.Timestamp(month_value + "-01").strftime("%Y-%m")
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid threshold month") from exc
        if month != month_value:
            raise ValueError("invalid threshold month")
        key = (timeframe, month)
        if key in indexed:
            raise ValueError("duplicate threshold timeframe/month")
        indexed[key] = values
    return indexed


def _v9_candidate_lookup(v9_decisions: pd.DataFrame) -> dict[tuple[int, int, int], float]:
    """Index original V9 candidates by their frozen local/original ordinal and side."""
    missing = _CANDIDATE_COLUMNS.difference(v9_decisions.columns)
    if missing:
        raise ValueError(f"v9 decisions missing columns: {sorted(missing)}")
    candidates = v9_decisions
    if "v9" in candidates:
        candidates = candidates.loc[candidates.v9.fillna(False).astype(bool)]
    if candidates.duplicated(["signal_i"]).any() or candidates.duplicated(["local_i", "signal_i", "side"]).any():
        raise ValueError("duplicate V9 candidate ordinal")
    lookup: dict[tuple[int, int, int], float] = {}
    for row in candidates.loc[:, ["local_i", "signal_i", "side", "reference_risk_fraction"]].itertuples(index=False):
        try:
            key = (int(row.local_i), int(row.signal_i), int(row.side))
            risk = float(row.reference_risk_fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid V9 candidate ordinal") from exc
        lookup[key] = risk
    return lookup


def _known_cutoff(row: dict[str, object], *, available_at: pd.Timestamp,
                  timeframe: int, month: str) -> tuple[float, int] | None:
    """Return a valid three-complete-month cutoff or reject malformed lookups."""
    if not _known_flag(row["known"]):
        return None
    try:
        n_history, cutoff = int(row["n_history"]), float(row["cutoff"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid known threshold values") from exc
    if n_history < MIN_HISTORY or not math.isfinite(cutoff) or cutoff <= 0:
        return None
    month_start = _month_start(available_at)
    history_end = _as_utc(row["history_end"], field="threshold history_end")
    history_start = _as_utc(row["history_start"], field="threshold history_start")
    expected_start = month_start - pd.DateOffset(months=LOOKBACK_MONTHS)
    if history_end > available_at or history_end != month_start:
        raise ValueError("future or cross-month threshold history_end")
    if history_start != expected_start:
        raise ValueError("cross-month threshold history_start")
    if int(row["timeframe_min"]) != timeframe or str(row["month"]) != month:
        raise ValueError("ambiguous threshold lookup")
    return cutoff, n_history


def prepare_entry_v21(prepared: engine.PreparedArm, v9_decisions: pd.DataFrame,
                      thresholds: pd.DataFrame) -> tuple[engine.PreparedArm, pd.DataFrame]:
    """Filter original V9 longs by their causal three-month risk percentile.

    Each original V9 candidate has ``available_at = signal_bar_open + interval``.
    Its UTC calendar month chooses exactly one row of the supplied threshold
    panel for ``prepared.context.minutes``.  A long passes when the finite,
    positive confirmation-close ``reference_risk_fraction`` is less than or
    equal to that row's known cutoff with at least ``MIN_HISTORY`` entries;
    equality passes.  Missing risk or threshold history is an unknown rejection.
    The score is the descriptive value ``-reference_risk_fraction``.  Shorts
    are exempt and retain their original V9 permission.  This function copies
    only ``allowed`` and never reads actual next-open prices or future outcomes.
    """
    index, context = prepared.frame.index, prepared.context
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None or context.minutes <= 0:
        raise ValueError("prepared V9 arm requires an ordered timezone-aware positive interval")
    candidate_lookup = _v9_candidate_lookup(v9_decisions)
    threshold_lookup = _threshold_lookup(thresholds)
    baseline_allowed = np.asarray(prepared.allowed, dtype=bool)
    allowed = baseline_allowed.copy()
    rows: list[dict[str, object]] = []
    interval = pd.Timedelta(minutes=context.minutes)

    for i in np.flatnonzero(baseline_allowed):
        stamp = pd.Timestamp(index[i])
        available_at = stamp + interval
        side = int(prepared.raw_side[i])
        signal_i = prepared.ordinal.get(stamp)
        if signal_i is None:
            raise ValueError("original V9 candidate missing frozen ordinal")
        month = _month_start(available_at).strftime("%Y-%m")
        values: dict[str, object] = {
            "local_i": int(i), "signal_i": int(signal_i), "stream_key": context.key,
            "signal_bar_open": stamp, "available_at": available_at, "side": side,
            "score": math.nan, "gate_known": True, "gate_passed": True,
            "reason": "short_unchanged" if side == -1 else "passed", "baseline_allowed": True,
            "risk_cutoff": math.nan, "threshold_month": month, "n_history": math.nan,
            "reference_risk_fraction": math.nan,
        }
        if side != 1:
            rows.append(values)
            continue

        risk = candidate_lookup.get((int(i), int(signal_i), side), math.nan)
        if not math.isfinite(risk) or risk <= 0:
            values.update(gate_known=False, gate_passed=False, reason="reference_risk_unknown")
        else:
            values.update(reference_risk_fraction=risk, score=-risk)
            threshold = threshold_lookup.get((int(context.minutes), month))
            if threshold is None:
                values.update(gate_known=False, gate_passed=False, reason="threshold_unknown")
            else:
                valid = _known_cutoff(threshold, available_at=available_at,
                                      timeframe=int(context.minutes), month=month)
                if valid is None:
                    values.update(gate_known=False, gate_passed=False, reason="insufficient_history")
                else:
                    cutoff, n_history = valid
                    passed = risk <= cutoff
                    values.update(risk_cutoff=cutoff, n_history=n_history, gate_passed=passed,
                                  reason="passed" if passed else "risk_above_cutoff")
        allowed[i] = bool(values["gate_passed"])
        rows.append(values)
    return replace(prepared, allowed=allowed), pd.DataFrame(rows, columns=_DECISION_COLUMNS)
