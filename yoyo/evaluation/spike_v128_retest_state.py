"""Causal breakout, held-retest, and re-break confirmation for SPIKE V12.8.

This implements the owner-approved single timing hypothesis for offline study;
confirmation alone is not a trading or production qualification.
The state machine reads only completed bars after one frozen signal bar. It
uses that signal bar's high/low as the level, requires distinct bars for the
breakout, retest, and confirmation, and returns the last bar needed to decide
the event. It does not model fills, stops after entry, or trade exits.
"""
from __future__ import annotations

from numbers import Integral
from typing import Any

import numpy as np


def confirmation(
    open_,
    high,
    low,
    close,
    gap,
    raw_side,
    anchor_i: int,
    side: int,
    stop: float,
    max_wait_bars: int = 24,
) -> dict[str, Any]:
    """Return the first causal breakout/retest/re-break decision.

    ``anchor_i`` is the original signal bar. Longs freeze its high and shorts
    freeze its low. A later close must first break that level, a different bar
    must touch it and close strictly back on the favorable side, and a still
    later close must exceed the best favorable extreme observed from the first
    breakout bar through the bar immediately before the retest.

    Cancellation checks run before stage transitions on every post-anchor bar,
    in this order: data gap, opposite raw signal, original-stop touch, and
    (after breakout) close no longer strictly beyond the frozen level. A
    confirmation on ``anchor_i + max_wait_bars`` is allowed. If the available
    arrays end earlier, the result remains ``pending_boundary``.

    Malformed array shapes, indices, sides, wait limits, anchor bars, or stops
    raise ``ValueError``. A non-finite/malformed bar encountered before a
    decision returns ``pending_boundary`` at that bar, because its path cannot
    be evaluated safely. Values after the returned ``decision_i`` are never
    inspected.
    """
    arrays = {
        "open": np.asarray(open_),
        "high": np.asarray(high),
        "low": np.asarray(low),
        "close": np.asarray(close),
        "gap": np.asarray(gap),
        "raw_side": np.asarray(raw_side),
    }
    if any(array.ndim != 1 for array in arrays.values()):
        raise ValueError("all inputs must be one-dimensional arrays")
    lengths = {len(array) for array in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("all inputs must have the same length")
    size = lengths.pop()
    if isinstance(anchor_i, (bool, np.bool_)) or not isinstance(anchor_i, Integral):
        raise ValueError("anchor_i must be an integer")
    anchor_i = int(anchor_i)
    if size == 0 or anchor_i < 0 or anchor_i >= size:
        raise ValueError("anchor_i must identify a bar in the arrays")
    if isinstance(side, (bool, np.bool_)) or not isinstance(side, Integral) or int(side) not in (-1, 1):
        raise ValueError("side must be +1 for long or -1 for short")
    side = int(side)
    if isinstance(max_wait_bars, (bool, np.bool_)) or not isinstance(max_wait_bars, Integral):
        raise ValueError("max_wait_bars must be a non-negative integer")
    max_wait_bars = int(max_wait_bars)
    if max_wait_bars < 0:
        raise ValueError("max_wait_bars must be a non-negative integer")

    def finite_value(name: str, index: int) -> float | None:
        try:
            value = float(arrays[name][index])
        except (TypeError, ValueError, OverflowError):
            return None
        return value if np.isfinite(value) else None

    anchor_values = {name: finite_value(name, anchor_i) for name in ("open", "high", "low", "close")}
    if any(value is None for value in anchor_values.values()):
        raise ValueError("anchor bar must contain finite OHLC values")
    anchor_open = anchor_values["open"]
    anchor_high = anchor_values["high"]
    anchor_low = anchor_values["low"]
    anchor_close = anchor_values["close"]
    if anchor_high < anchor_low or not (anchor_low <= anchor_open <= anchor_high) or not (anchor_low <= anchor_close <= anchor_high):
        raise ValueError("anchor OHLC values are inconsistent")

    level = float(anchor_high if side == 1 else anchor_low)
    try:
        stop = float(stop)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("stop must be a finite adverse price") from exc
    if not np.isfinite(stop) or (side == 1 and stop >= level) or (side == -1 and stop <= level):
        raise ValueError("stop must be finite and adverse to the signal side")

    result: dict[str, Any] = {
        "status": "pending_boundary",
        "anchor_i": anchor_i,
        "breakout_i": None,
        "retest_i": None,
        "confirmation_i": None,
        "decision_i": anchor_i,
        "level": level,
        "first_leg_extreme": None,
        "cancel_reason": None,
        "boundary_reason": None,
    }

    def favorable(value: float, boundary: float) -> bool:
        return value > boundary if side == 1 else value < boundary

    def cancel(index: int, reason: str) -> dict[str, Any]:
        result.update(status="canceled", decision_i=index, cancel_reason=reason)
        return result

    last_allowed_i = anchor_i + max_wait_bars
    scan_end_i = min(size - 1, last_allowed_i)
    stage = "breakout"

    for index in range(anchor_i + 1, scan_end_i + 1):
        # The gap and direction checks do not need OHLC values, so their
        # required priority is preserved even when a gapped bar is incomplete.
        try:
            has_gap = bool(arrays["gap"][index])
        except (TypeError, ValueError):
            result.update(decision_i=index, boundary_reason="invalid_gap")
            return result
        if has_gap:
            return cancel(index, "gap")

        try:
            raw = float(arrays["raw_side"][index])
        except (TypeError, ValueError, OverflowError):
            result.update(decision_i=index, boundary_reason="invalid_raw_side")
            return result
        if not np.isfinite(raw) or raw not in (-1.0, 0.0, 1.0):
            result.update(decision_i=index, boundary_reason="invalid_raw_side")
            return result
        opposite = raw == -side
        if opposite:
            return cancel(index, "raw_opposite")

        values = {name: finite_value(name, index) for name in ("open", "high", "low", "close")}
        if any(value is None for value in values.values()):
            result.update(decision_i=index, boundary_reason="invalid_bar")
            return result
        bar_open = values["open"]
        bar_high = values["high"]
        bar_low = values["low"]
        bar_close = values["close"]
        if bar_high < bar_low or not (bar_low <= bar_open <= bar_high) or not (bar_low <= bar_close <= bar_high):
            result.update(decision_i=index, boundary_reason="invalid_bar")
            return result

        stop_touched = bar_low <= stop if side == 1 else bar_high >= stop
        if stop_touched:
            return cancel(index, "stop_touch")

        if stage != "breakout" and not favorable(bar_close, level):
            return cancel(index, "lost_level")

        if stage == "breakout":
            if favorable(bar_close, level):
                result["breakout_i"] = index
                result["first_leg_extreme"] = float(bar_high if side == 1 else bar_low)
                stage = "retest"
        elif stage == "retest":
            touches_level = bar_low <= level if side == 1 else bar_high >= level
            if touches_level:
                result["retest_i"] = index
                # Deliberately freeze before this bar: a retest candle's
                # favorable wick is not part of the first-leg breakout range.
                stage = "rebreak"
            else:
                extreme = result["first_leg_extreme"]
                result["first_leg_extreme"] = float(
                    max(extreme, bar_high) if side == 1 else min(extreme, bar_low)
                )
        else:  # rebreak
            extreme = float(result["first_leg_extreme"])
            if favorable(bar_close, extreme):
                result.update(status="confirmed", confirmation_i=index, decision_i=index)
                return result

        result["decision_i"] = index

    if last_allowed_i <= size - 1:
        result.update(status="expired", decision_i=last_allowed_i)
    else:
        result.update(status="pending_boundary", decision_i=size - 1, boundary_reason="insufficient_bars")
    return result
