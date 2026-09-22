"""Bounded initial-stop transforms for the SPIKE execution replay.

This offline research seam implements the owner-requested 2026-09-22
counterfactual only.  ``sma120`` is the chart-closed 120-window SMA of chart
closes.  ``htf_sma60`` is a confirmed higher-timeframe 60-window SMA made from
completed H1 candles; its value is visible at the chart-bar open.  ``atr`` is
the frozen signal-bar ATR14.  The transform receives those already-frozen
signal-close facts and never derives a moving average or reads a future bar.

The two MA arms can only move an initial stop farther from the entry.  A
changed initial risk is therefore also the denominator used by the existing
2R trail when this transform is passed through ``replay_serial``.
"""
from __future__ import annotations

import math
from collections.abc import Mapping


ARMS = ("baseline", "sma120", "htf_sma60", "both")
_TINY_TICK_FRACTION = 1e-8


def _finite_positive(value: object) -> bool:
    """Return whether ``value`` is a finite, strictly positive number."""
    try:
        return math.isfinite(float(value)) and float(value) > 0.0
    except (TypeError, ValueError):
        return False


def _selected_ma(arm: str, side: int, sma120: object, htf_sma60: object) -> float | None:
    """Select a validated MA, combining both arms in the widening direction."""
    if arm == "sma120":
        return float(sma120) if _finite_positive(sma120) else None
    if arm == "htf_sma60":
        return float(htf_sma60) if _finite_positive(htf_sma60) else None
    if arm == "both":
        if not (_finite_positive(sma120) and _finite_positive(htf_sma60)):
            return None
        # The lower long support / higher short resistance is the only
        # direction that can widen risk under the bounded min/max rule.
        values = (float(sma120), float(htf_sma60))
        return min(values) if side == 1 else max(values)
    raise ValueError(f"unsupported stop arm: {arm}")


def transform_initial(
    row: Mapping[str, object],
    *,
    arm: str,
    sma120: object,
    htf_sma60: object,
    atr: object,
    tick: object,
    buffer_atr: float = 0.2,
) -> dict[str, object] | None:
    """Return a bounded initial-stop counterfactual for one frozen entry.

    ``row`` is the dictionary returned by the existing next-open initializer.
    The baseline is an exact shallow dictionary copy.  MA arms require the
    selected signal-close MA to be finite and positive; missing/invalid input
    rejects the candidate instead of falling back to another MA or the
    original stop.  Long candidates use ``min(original_stop, ma-buffer*atr)``
    and short candidates use ``max(original_stop, ma+buffer*atr)``.  Prices are
    rounded outward to the tick grid (floor for long, ceil for short), and the
    resulting stop must leave more than a tiny fraction of one tick of risk.

    The original ``entry_price`` is already the next observed open.  No future
    bar or future MA is consulted; ``atr`` is the frozen current signal-bar
    ATR14.  All fields not recomputed below are preserved verbatim.
    """
    if arm not in ARMS:
        raise ValueError(f"unsupported stop arm: {arm}")
    if arm == "baseline":
        return dict(row)

    # Keep malformed rows fail-closed.  Baseline intentionally bypasses these
    # checks so its identity remains an exact copy of the source initializer.
    try:
        side = int(row["side"])
        entry = float(row["entry_price"])
        original_stop = float(row["initial_stop"])
        atr_value = float(atr)
        tick_value = float(tick)
        buffer_value = float(buffer_atr)
    except (KeyError, TypeError, ValueError):
        return None
    if side not in (-1, 1) or not _finite_positive(entry) or not _finite_positive(original_stop):
        return None
    if (not _finite_positive(atr_value) or not _finite_positive(tick_value)
            or not math.isfinite(buffer_value) or buffer_value < 0.0):
        return None
    selected = _selected_ma(arm, side, sma120, htf_sma60)
    if selected is None:
        return None

    raw = selected - buffer_value * atr_value if side == 1 else selected + buffer_value * atr_value
    # Preserve a source stop exactly when the bounded candidate cannot widen
    # risk.  Re-dividing an already rounded value such as 0.29 by 0.01 can be
    # 28.999... and accidentally move the no-op case one tick farther out.
    if (side == 1 and raw >= original_stop) or (side == -1 and raw <= original_stop):
        stop = original_stop
    else:
        # Rounding away from the entry keeps a long stop no higher than its
        # raw level and a short stop no lower than its raw level.
        stop = (math.floor(raw / tick_value) * tick_value if side == 1
                else math.ceil(raw / tick_value) * tick_value)
    risk = side * (entry - stop)
    tiny_tick = tick_value * _TINY_TICK_FRACTION
    if not (_finite_positive(stop) and math.isfinite(risk) and risk > tiny_tick):
        return None

    out = dict(row)
    out.update(initial_stop=float(stop), initial_risk=float(risk),
               initial_risk_frac=float(risk / entry), protection=float(stop))
    return out
