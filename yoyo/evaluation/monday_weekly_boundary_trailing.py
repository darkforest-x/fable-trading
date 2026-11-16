"""One-hour trailing protection after the first Monday-boundary touch.

The first boundary-touch candle is observed only at its close. The hard stop
remains the only active stop during that candle; close-derived protection is
installed for the following hour. OHLC stop matching is delegated to the
VectorBT-native ``first_hit`` adapter used by the Monday wick-R engine.
"""
from __future__ import annotations

import numpy as np

from yoyo.evaluation.monday_wick_r_engine import first_hit
from yoyo.evaluation.month_firstweek_12h_wick_exits import _native_arrays


_CENSORED = (-1, float("nan"), "censored", -1)


def trail_fill(bars, entry, side, target, hard_stop, distance):
    """Return the first stop fill after entry and a Monday boundary touch.

    Args:
        bars: ``N x 5`` array of ``[ts, open, high, low, close]`` rows, from
            the actual entry hour through the inclusive continuous-data end.
        entry: Actual entry price.
        side: ``1`` for long or ``-1`` for short.
        target: Favorable Monday near-side boundary price.
        hard_stop: Original adverse stop, active from the first input bar.
        distance: Trailing distance as a fraction, e.g. ``0.01`` for 1%.

    Returns:
        ``(exit_index, exit_price, kind, first_touch_index)``. Indices are
        zero-based against ``bars``. An open path returns ``(-1, nan,
        "censored", first_touch_index)``; before any target touch the touch
        index is ``-1``.

    The first-touch hour is matched with both the fixed hard stop and target.
    If both barriers are crossed in that hour, VectorBT's conservative stop
    precedence produces an initial-stop exit. Once touched, trailing levels
    use the best completed close from the touch hour onward and only apply to
    the next hour. Matching is split only when that close-derived stop changes;
    each segment is still resolved by ``first_hit`` rather than a local OHLC
    fill loop.
    """
    raw = np.asarray(bars)
    if raw.ndim != 2 or raw.shape[1] != 5:
        raise ValueError("bars must be an N x 5 array [ts, open, high, low, close]")
    entry = float(entry)
    target = float(target)
    hard_stop = float(hard_stop)
    distance = float(distance)
    side = float(side)
    if not np.isfinite(entry) or entry <= 0:
        raise ValueError("entry must be a finite positive price")
    if side not in (-1.0, 1.0):
        raise ValueError("side must be 1 (long) or -1 (short)")
    if not np.isfinite(target) or target <= 0:
        raise ValueError("target must be a finite positive price")
    if not np.isfinite(hard_stop) or hard_stop <= 0:
        raise ValueError("hard_stop must be a finite positive price")
    if side * (target - entry) <= 0:
        raise ValueError("target must be favorable to entry for side")
    if side * (hard_stop - entry) >= 0:
        raise ValueError("hard_stop must be adverse to entry for side")
    if not np.isfinite(distance) or not 0.0 < distance < 1.0:
        raise ValueError("distance must be a finite fraction between 0 and 1")

    n = len(raw)
    if n == 0:
        return _CENSORED

    ohlc = np.asarray(raw[:, 1:5], dtype=np.float64)
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        raise ValueError("bar OHLC values must be finite positive prices")
    if (ohlc[:, 2] > np.minimum(ohlc[:, 0], ohlc[:, 3])).any():
        raise ValueError("bar lows cannot exceed opens or closes")
    if (ohlc[:, 1] < np.maximum(ohlc[:, 0], ohlc[:, 3])).any():
        raise ValueError("bar highs cannot be below opens or closes")
    if (ohlc[:, 2] > ohlc[:, 1]).any():
        raise ValueError("bar lows cannot exceed highs")

    o, h, l, c, po, ty, scratch = _native_arrays(ohlc)

    # Use the complete OHLC extremes only to locate the first target-touch
    # hour. Then resolve the original stop natively through that hour. This
    # keeps a target-crossing open from masking a later hard-stop touch in the
    # same bar (first_hit's marketable-target rule is intentionally bypassed).
    touch_tol = 4.0 * np.finfo(np.float64).eps * entry
    if side > 0:
        touched = np.flatnonzero(ohlc[:, 1] >= target - touch_tol)
    else:
        touched = np.flatnonzero(ohlc[:, 2] <= target + touch_tol)
    first_touch = int(touched[0]) if len(touched) else -1
    initial_end = first_touch + 1 if first_touch >= 0 else n
    hit_i, hit_price, hit_kind, _ = first_hit(
        o, h, l, c, 1, initial_end, entry, hard_stop, np.nan,
        side, po, ty, scratch,
    )
    if hit_kind == 1:
        # If stop and target both occur in the target-touch hour, the hard
        # stop is guaranteed and the ambiguous touch is not attributed.
        return int(hit_i - 1), float(hit_price), "initial_stop", -1
    if first_touch < 0:
        return _CENSORED

    best_close = float(ohlc[first_touch, 3])
    if side > 0:
        protection = max(hard_stop, best_close * (1.0 - distance))
    else:
        protection = min(hard_stop, best_close * (1.0 + distance))

    # The touch candle still used hard_stop. Its close-derived stop first
    # applies at first_touch + 1. Find each next stop-change close in vectorized
    # form, then let native matching resolve the old stop through that bar.
    cursor = first_touch + 1
    while cursor < n:
        closes = ohlc[cursor:, 3]
        if side > 0:
            running_best = np.maximum.accumulate(np.maximum(best_close, closes))
            candidate = np.maximum(hard_stop, running_best * (1.0 - distance))
            changes = np.flatnonzero(candidate > protection)
        else:
            running_best = np.minimum.accumulate(np.minimum(best_close, closes))
            candidate = np.minimum(hard_stop, running_best * (1.0 + distance))
            changes = np.flatnonzero(candidate < protection)

        if len(changes):
            change_i = cursor + int(changes[0])
            seg_end = change_i
        else:
            change_i = -1
            seg_end = n - 1

        ref = float(ohlc[cursor - 1, 3])
        stop_i, stop_price, stop_kind, _ = first_hit(
            o, h, l, c, cursor + 1, seg_end + 1, ref, protection,
            np.nan, side, po, ty, scratch,
        )
        if stop_kind == 1:
            kind = "trailing_stop" if protection != hard_stop else "initial_stop"
            return int(stop_i - 1), float(stop_price), kind, first_touch

        if change_i < 0:
            break
        # The stop found from change_i's completed close becomes active on the
        # next bar; no information from that close is applied retroactively.
        best_close = float(running_best[change_i - cursor])
        protection = float(candidate[change_i - cursor])
        cursor = change_i + 1

    return -1, float("nan"), "censored", first_touch
