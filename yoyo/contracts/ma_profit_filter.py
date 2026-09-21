"""Resolve retrospective MA-event barrier labels without reading model images.

These labels deliberately inspect future OHLC after the original five-bar
confirmation: they are research outcomes, never live features or evidence that
a chart image was profitable.  The stop reads only the supplied core interval.
``target_r`` is the conventional *gross* price-risk multiple; ``net_r`` is
reported separately after the fixed round-trip cost and may be used by a later
policy if its economic definition changes.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _base(outcome: str, *, reason: str, direction: str | None = None) -> dict[str, Any]:
    return {"outcome": outcome, "reason": reason, "direction": direction, "retained": False,
            "gross_r": None, "net_r": None, "risk_price": None, "entry_price": None,
            "stop_price": None, "target_price": None, "decision_close_time_utc": None,
            "entry_open_time_utc": None, "exit_time_utc": None, "label_window_end_utc": None}


def _valid_ohlc(values: object) -> bool:
    """Validate one positive candle without normalizing or repairing source values."""

    try:
        opening, high, low, close = (float(value) for value in values)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite((opening, high, low, close)).all() and min(opening, high, low, close) > 0
                and low <= min(opening, close) <= max(opening, close) <= high)


def resolve_ma_profit_event(
    frame: pd.DataFrame,
    core_start_i: int,
    core_end_i: int,
    direction: str,
    *,
    bar_minutes: int = 15,
    confirmation_bars: int = 5,
    horizon_hours: int = 12,
    target_r: float = 3.0,
    round_trip_cost: float = 0.002,
) -> dict[str, Any]:
    """Return an exact future-aware barrier outcome for one LONG or SHORT core.

    Decision is the close of ``core_end_i + confirmation_bars``.  The next bar's
    open is the entry, and the 12-hour label horizon contains that entry bar.
    Same-bar TP/SL collisions resolve to SL.  An opening gap through SL exits at
    that open; a gap through TP exits at the target, conservatively.  A missing
    tail or gap is ``UNKNOWN`` unless an earlier barrier already closed the event.
    """

    required = ("open_time", "open", "high", "low", "close")
    if not isinstance(frame, pd.DataFrame) or any(column not in frame for column in required):
        return _base("INVALID", reason="missing_required_columns")
    if direction not in {"LONG", "SHORT"}:
        return _base("INVALID", reason="invalid_direction", direction=direction)
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (bar_minutes, confirmation_bars, horizon_hours)):
        return _base("INVALID", reason="invalid_bar_or_horizon", direction=direction)
    if not np.isfinite(target_r) or target_r <= 0 or not np.isfinite(round_trip_cost) or round_trip_cost < 0:
        return _base("INVALID", reason="invalid_target_or_cost", direction=direction)
    if not (0 <= core_start_i <= core_end_i < len(frame)):
        return _base("INVALID", reason="invalid_core_indices", direction=direction)
    if (horizon_hours * 60) % bar_minutes:
        return _base("INVALID", reason="horizon_not_whole_bars", direction=direction)
    try:
        times = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    except (TypeError, ValueError):
        return _base("INVALID", reason="invalid_open_time", direction=direction)
    decision_i = core_end_i + confirmation_bars
    entry_i = decision_i + 1
    horizon_bars = horizon_hours * 60 // bar_minutes
    expected_last_i = entry_i + horizon_bars - 1
    if decision_i >= len(frame) or entry_i >= len(frame):
        return _base("UNKNOWN", reason="missing_confirmation_or_entry", direction=direction)
    expected_step = pd.Timedelta(minutes=bar_minutes)
    try:
        core_ohlc = frame.iloc[core_start_i:core_end_i + 1][["open", "high", "low", "close"]].to_numpy()
    except (TypeError, ValueError):
        return _base("INVALID", reason="invalid_core_ohlc", direction=direction)
    if not all(_valid_ohlc(values) for values in core_ohlc):
        return _base("INVALID", reason="invalid_core_ohlc", direction=direction)
    # A gap before entry invalidates the decision-to-entry lineage itself.
    known_until_entry = times.iloc[core_start_i:entry_i + 1]
    if known_until_entry.isna().any() or len(known_until_entry) < 2 or not (known_until_entry.diff().iloc[1:] == expected_step).all():
        return _base("UNKNOWN", reason="gap_before_entry", direction=direction)
    if not all(_valid_ohlc(values) for values in frame.iloc[core_start_i:entry_i + 1][["open", "high", "low", "close"]].to_numpy()):
        return _base("INVALID", reason="invalid_preentry_ohlc", direction=direction)
    entry = float(frame["open"].iloc[entry_i])
    decision_time = times.iloc[decision_i] + expected_step
    entry_time = times.iloc[entry_i]
    window_end = decision_time + pd.Timedelta(hours=horizon_hours)
    if not np.isfinite(entry) or entry <= 0:
        result = _base("INVALID", reason="nonfinite_entry", direction=direction)
        result.update({"decision_close_time_utc": decision_time.isoformat(), "entry_open_time_utc": entry_time.isoformat(), "label_window_end_utc": window_end.isoformat()})
        return result
    stop = float(frame["low"].iloc[core_start_i:core_end_i + 1].min()) if direction == "LONG" else float(frame["high"].iloc[core_start_i:core_end_i + 1].max())
    risk = entry - stop if direction == "LONG" else stop - entry
    if not np.isfinite(stop) or not np.isfinite(risk) or risk <= 0:
        result = _base("INVALID", reason="stop_not_beyond_entry", direction=direction)
        result.update({"decision_close_time_utc": decision_time.isoformat(), "entry_open_time_utc": entry_time.isoformat(), "label_window_end_utc": window_end.isoformat(), "entry_price": entry, "stop_price": stop})
        return result
    target = entry + target_r * risk if direction == "LONG" else entry - target_r * risk
    if direction == "SHORT" and target <= 0:
        result = _base("INVALID", reason="nonpositive_short_target", direction=direction)
        result.update({"decision_close_time_utc": decision_time.isoformat(), "entry_open_time_utc": entry_time.isoformat(), "label_window_end_utc": window_end.isoformat(), "entry_price": entry, "stop_price": stop, "risk_price": risk, "target_price": target})
        return result
    common = {"direction": direction, "decision_index": decision_i, "entry_index": entry_i, "core_start_i": core_start_i, "core_end_i": core_end_i, "confirmation_bars": confirmation_bars, "horizon_bars": horizon_bars, "decision_close_time_utc": decision_time.isoformat(), "entry_open_time_utc": entry_time.isoformat(), "label_window_end_utc": window_end.isoformat(), "entry_price": entry, "stop_price": stop, "target_price": target, "risk_price": risk}

    def closed(outcome: str, exit_price: float, exit_i: int, execution: str) -> dict[str, Any]:
        signed = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
        gross = signed / risk
        net = (signed - entry * round_trip_cost) / risk
        return {**common, "outcome": outcome, "reason": execution, "exit_index": exit_i,
                "exit_time_utc": (times.iloc[exit_i] if "_gap_" in execution else times.iloc[exit_i] + expected_step).isoformat(),
                "exit_price": exit_price, "gross_r": gross, "net_r": net,
                "retained": bool(outcome == "TP" and gross >= target_r - 1e-10 and net > 0)}

    last_known = min(expected_last_i, len(frame) - 1)
    for index in range(entry_i, last_known + 1):
        values = frame.iloc[index][["open", "high", "low", "close"]].to_numpy()
        if not _valid_ohlc(values):
            result = _base("INVALID", reason="invalid_future_ohlc", direction=direction); result.update(common); return result
        if index > entry_i and times.iloc[index] - times.iloc[index - 1] != expected_step:
            result = _base("UNKNOWN", reason="future_gap", direction=direction); result.update(common); return result
        opening, high, low, close = map(float, values)
        if direction == "LONG":
            if opening <= stop: return closed("SL", opening, index, "sl_gap_open")
            if opening >= target: return closed("TP", target, index, "tp_gap_target")
            if low <= stop: return closed("SL", stop, index, "sl_intrabar")
            if high >= target: return closed("TP", target, index, "tp_intrabar")
        else:
            if opening >= stop: return closed("SL", opening, index, "sl_gap_open")
            if opening <= target: return closed("TP", target, index, "tp_gap_target")
            if high >= stop: return closed("SL", stop, index, "sl_intrabar")
            if low <= target: return closed("TP", target, index, "tp_intrabar")
    if expected_last_i >= len(frame):
        result = _base("UNKNOWN", reason="insufficient_future", direction=direction); result.update(common); return result
    return closed("TIMEOUT", float(frame["close"].iloc[expected_last_i]), expected_last_i, "horizon_close")
