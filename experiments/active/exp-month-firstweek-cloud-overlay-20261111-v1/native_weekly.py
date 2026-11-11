"""Native weekly 1.618 reclaim sides using confirmed 12H bars.

The Monday range is rebuilt from exactly the complete New York Monday 00:00
through Tuesday 00:00 set of H1 candles for each NY week.  The returned side
is known only at the 12H bar close; no later H1 range or 12H observation is
used to decide it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


HOUR_MS = 3_600_000
STEP_12H = 12 * HOUR_MS
_DAY_NS = 86_400_000_000_000
_NY = ZoneInfo("America/New_York")
_EPOCH_DATE = date(1970, 1, 1)


def _week_day_keys(times_ms: np.ndarray) -> np.ndarray:
    """Return local Monday dates as integer days, without fixed-offset DST math."""
    local = pd.to_datetime(times_ms, unit="ms", utc=True).tz_convert(_NY)
    # Strip the zone before subtracting calendar days: subtracting timedeltas
    # from a zoned index would move a midnight to 23:00 across a DST boundary.
    wall_midnight = local.tz_localize(None).normalize()
    week_midnight = wall_midnight - pd.to_timedelta(local.weekday, unit="D")
    return (week_midnight.asi8 // _DAY_NS).astype(np.int64, copy=False)


def _monday_bounds(day_key: int) -> tuple[int, int, int]:
    """NY Monday open, Tuesday range-ready time, and next Monday expiry."""
    monday = _EPOCH_DATE + timedelta(days=int(day_key))
    local_start = datetime.combine(monday, time.min, tzinfo=_NY)
    local_ready = datetime.combine(monday + timedelta(days=1), time.min, tzinfo=_NY)
    local_expiry = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=_NY)
    return tuple(int(x.timestamp() * 1000) for x in (local_start, local_ready, local_expiry))


def _weekly_ranges(hour_df: pd.DataFrame) -> dict[int, tuple[float, float, int, int]]:
    """Build complete NY Monday H1 anchors keyed by local Monday day."""
    required = {"time", "high", "low"}
    missing = required.difference(hour_df.columns)
    if missing:
        raise ValueError(f"hour_df is missing columns: {sorted(missing)}")
    if hour_df.empty:
        return {}

    try:
        times = hour_df["time"].to_numpy(dtype=np.int64, copy=False)
        highs = hour_df["high"].to_numpy(dtype=float, copy=False)
        lows = hour_df["low"].to_numpy(dtype=float, copy=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("hour_df time/high/low must be numeric UTC-millisecond OHLC fields") from exc
    if not (times.ndim == highs.ndim == lows.ndim == 1 and len(times) == len(highs) == len(lows)):
        raise ValueError("hour_df time/high/low must be one-dimensional and equally sized")

    order = np.argsort(times, kind="stable")
    times, highs, lows = times[order], highs[order], lows[order]
    if len(times) > 1 and np.any(np.diff(times) == 0):
        raise ValueError("hour_df contains duplicate H1 open times")
    if np.any(times % HOUR_MS != 0):
        raise ValueError("hour_df times must be UTC-aligned H1 opens")

    week_keys = _week_day_keys(times)
    ranges: dict[int, tuple[float, float, int, int]] = {}
    for day_key in np.unique(week_keys):
        start_ms, ready_ms, expires_ms = _monday_bounds(int(day_key))
        expected = np.arange(start_ms, ready_ms, HOUR_MS, dtype=np.int64)
        lo = int(np.searchsorted(times, start_ms, side="left"))
        hi = int(np.searchsorted(times, ready_ms, side="left"))
        observed_times = times[lo:hi]
        observed_highs, observed_lows = highs[lo:hi], lows[lo:hi]
        # Exact expected opens make missing, extra, or DST-shifted hours fail
        # closed; Monday itself always has a full 24 hours in New York.
        if (len(observed_times) != len(expected)
                or not np.array_equal(observed_times, expected)
                or not np.isfinite(observed_highs).all()
                or not np.isfinite(observed_lows).all()
                or np.any(observed_highs <= 0)
                or np.any(observed_lows <= 0)
                or np.any(observed_highs < observed_lows)):
            continue
        high, low = float(observed_highs.max()), float(observed_lows.min())
        if high > low:
            ranges[int(day_key)] = (high, low, ready_ms, expires_ms)
    return ranges


def _valid_plan(side: int, entry: float, target: float) -> bool:
    """Apply Pine V1.9 positive entry/stop/target/risk/reward gate.

    The caller has no symbol parameter. Checking both supported default stop
    tiers is equivalent for any positive entry and preserves both BTC/ETH 1.5%
    and other-symbol 3% plan validation.
    """
    if not np.isfinite([entry, target]).all() or entry <= 0 or target <= 0:
        return False
    for stop_pct in (0.015, 0.03):
        stop = entry * (1.0 - side * stop_pct)
        risk = (entry - stop) * side
        reward = (target - entry) * side
        if not np.isfinite([stop, risk, reward]).all() or min(entry, stop, target, risk, reward) <= 0:
            return False
    return True


def native_sides(hour_df, bars, fast, slow, ready) -> np.ndarray:
    """Return Pine V1.9 native 12H Monday-wick-reclaim sides, aligned to ``bars``.

    ``hour_df`` supplies H1 ``time`` (UTC open milliseconds), ``high`` and
    ``low``. ``bars`` supplies native UTC 12H ``time`` (open ms), ``high``,
    ``low`` and ``close``. ``fast``, ``slow`` and ``ready`` are close-confirmed
    EMA20, EMA50 and warmup arrays aligned row-for-row with ``bars``.

    For each NY signal week, the anchor is that same week's complete H1 Monday
    range, known Tuesday 00:00 NY. Signals must open after the anchor is ready,
    close no later than the following Monday 00:00 NY, and be the first valid
    signal of that NY week. Long/short use Pine's strict blue-key-candle rules:
    EMA20 > EMA50 and low < lower < close < upper; or EMA20 < EMA50 and
    lower < close < upper < high. The 1.618 levels are low - 0.618*range and
    high + 0.618*range. Missing/incomplete Monday anchors produce side 0.
    """
    required = {"time", "high", "low", "close"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"bars is missing columns: {sorted(missing)}")
    n = len(bars)
    out = np.zeros(n, dtype=np.int8)
    if n == 0:
        return out

    try:
        times = bars["time"].to_numpy(dtype=np.int64, copy=False)
        highs = bars["high"].to_numpy(dtype=float, copy=False)
        lows = bars["low"].to_numpy(dtype=float, copy=False)
        closes = bars["close"].to_numpy(dtype=float, copy=False)
        fast = np.asarray(fast, dtype=float)
        slow = np.asarray(slow, dtype=float)
        ready = np.asarray(ready, dtype=bool)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("bars OHLC/time and EMA arrays must be numeric") from exc
    if any(x.ndim != 1 or len(x) != n for x in (times, highs, lows, closes, fast, slow, ready)):
        raise ValueError("bars and EMA arrays must be one-dimensional and equally sized")
    order = np.argsort(times, kind="stable")
    t, hi, lo, close = times[order], highs[order], lows[order], closes[order]
    if len(t) > 1 and np.any(np.diff(t) == 0):
        raise ValueError("bars contains duplicate 12H open times")
    e20, e50, warmed = fast[order], slow[order], ready[order]
    week_keys = _week_day_keys(t)
    ranges = _weekly_ranges(hour_df)

    # Join one compact weekly range table onto the signal bars. This keeps the
    # per-bar work vectorized; only each distinct calendar week is aggregated.
    anchor_high = np.full(n, np.nan, dtype=float)
    anchor_low = np.full(n, np.nan, dtype=float)
    anchor_ready = np.zeros(n, dtype=np.int64)
    anchor_expires = np.zeros(n, dtype=np.int64)
    if ranges:
        range_keys = np.fromiter(sorted(ranges), dtype=np.int64)
        range_table = [ranges[int(key)] for key in range_keys]
        range_high = np.fromiter((x[0] for x in range_table), dtype=float)
        range_low = np.fromiter((x[1] for x in range_table), dtype=float)
        range_ready = np.fromiter((x[2] for x in range_table), dtype=np.int64)
        range_expires = np.fromiter((x[3] for x in range_table), dtype=np.int64)
        range_index = np.searchsorted(range_keys, week_keys)
        in_table = range_index < len(range_keys)
        found = np.zeros(n, dtype=bool)
        found[in_table] = range_keys[range_index[in_table]] == week_keys[in_table]
        selected = range_index[found]
        anchor_high[found] = range_high[selected]
        anchor_low[found] = range_low[selected]
        anchor_ready[found] = range_ready[selected]
        anchor_expires[found] = range_expires[selected]

    span = anchor_high - anchor_low
    upper, lower = anchor_high + 0.618 * span, anchor_low - 0.618 * span
    close_at = t + STEP_12H
    valid = (warmed & (t % STEP_12H == 0) & (t >= anchor_ready)
             & (close_at <= anchor_expires) & np.isfinite(e20) & np.isfinite(e50)
             & np.isfinite(hi) & np.isfinite(lo) & np.isfinite(close)
             & (hi > 0) & (lo > 0) & (close > 0)
             & np.isfinite(upper) & np.isfinite(lower) & (upper > lower)
             & (upper > 0) & (lower > 0))

    long = valid & (e20 > e50) & (lo < lower) & (lower < close) & (close < upper)
    short = valid & (e20 < e50) & (lower < close) & (close < upper) & (upper < hi)
    candidates = long | short

    # Pine consumes the weekly key on its first valid plan, whichever side it
    # is. The NY Monday key is stable across UTC and DST transitions.
    candidate_indices = np.flatnonzero(candidates)
    if candidate_indices.size:
        _, first_in_week = np.unique(week_keys[candidate_indices], return_index=True)
        chosen = candidate_indices[first_in_week]
        sorted_sides = np.where(long[chosen], 1, -1).astype(np.int8, copy=False)
        out[order[chosen]] = sorted_sides
    return out
