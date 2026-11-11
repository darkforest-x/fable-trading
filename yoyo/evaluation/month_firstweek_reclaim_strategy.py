"""Monthly first-week 1.618 H4 strategy, evaluated at a completed hourly close.

The caller supplies the first recorded touch for this contract, NY range month
and rail (first touch per Beijing day catalogue) and the already frozen rail.
The only candle inputs are the 12 UTC hours of the touching 12H candle and the
four complete hours immediately following it, columns [open_ms, O, H, L, C].
"""
from decimal import Decimal

import numpy as np

HOUR_MS = 3_600_000
PRIMARY_GROUPS = ("上轨守住", "下轨重新收高")
CONTROL_GROUP = "下轨未重新收高"
STOP_FRACTION = Decimal("0.05")
TARGET_FRACTION = Decimal("0.20")


def classify_fourth_close(closed_bars, *, side, rail, first_touch_ms, range_expires_ms):
    """Return primary or control group at H4 close, or None when ineligible."""
    if side not in ("long", "short") or not np.isfinite(rail) or rail <= 0:
        raise ValueError("A known direction and positive frozen rail are required")
    start = first_touch_ms // (12*HOUR_MS) * (12*HOUR_MS)
    if len(closed_bars) != 16 or start + 16*HOUR_MS > range_expires_ms:
        return None
    if not np.array_equal(closed_bars[:, 0], start + np.arange(16)*HOUR_MS):
        return None
    touch = closed_bars[(first_touch_ms-start)//HOUR_MS]
    if touch[0] != first_touch_ms or not touch[3] <= rail <= touch[2]:
        return None
    reference = closed_bars[11, 4]
    first_four = closed_bars[12:, 4]
    sign = 1 if side == "long" else -1
    if sign*(reference-rail) <= 0 or sign*(first_four[0]-reference) > 0:
        return None
    distance = sign*(first_four-rail)/rail
    if np.any(distance < -1e-12) or distance[-1] <= 1e-12:
        return None
    if side == "short":
        return PRIMARY_GROUPS[0]
    return PRIMARY_GROUPS[1] if np.any(first_four[1:] > reference) else CONTROL_GROUP


def bracket(price, side):
    """Compute the fixed stop and target only once the opening fill is known."""
    entry = Decimal(str(price))
    if side not in (-1, 1) or entry <= 0:
        raise ValueError("Invalid fill or direction")
    return float(entry*(1-side*STOP_FRACTION)), float(entry*(1+side*TARGET_FRACTION))
