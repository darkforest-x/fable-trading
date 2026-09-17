"""SPIKE V10: V9 plus the owner's descending-trendline break as an entry gate.

Source: the owner's Pine indicator "主下降趋势线 · 关键高点 V1", pasted on
2026-09-18 with the instruction 「把这个趋势线突破 加入我们的spike v9 写一个v10」
and 「要求v9出了信号 同时 或者信号处于刚好趋势线突破 或者突破了一会的」.

The gate reads: a V9 confirmation is admitted only when the matching trendline
break is either happening on this very bar (age 0) or happened at most
`max_break_age` closed bars ago inside the same contiguous run. A long
confirmation needs an upward break of the descending line over pivot highs; a
short confirmation needs the mirrored downward break of the ascending line over
pivot lows. The mirror is the same arithmetic under a sign flip, matching how
V9 already mirrors its own long engine.

The gate can only remove V9 entries; it never creates one and never touches an
exit. Raw opposite V6 confirmations still end a reference exactly as in V9, so
the serial engine sees the same reversal stream it always did.

`max_break_age` is a research axis, not a tuned constant. The replay scores the
pre-registered ages side by side and reports all of them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.trendline_break import TrendlineParams, trendline_breaks

VERSION = "spike-v10-trendline-break-20260918-v1"
PARENT_VERSION = "spike-v9-entry-bundle-20260915-v1"

# Pre-registered before any replay. "now" is the owner's 刚好突破; the three
# windows are 一会 read at three orders of magnitude, not a search for a best
# one. On 30m bars they are 6 hours, one day and roughly four days.
ARMS: dict[str, int | None] = {"v9": None, "v10_now": 0, "v10_a12": 12, "v10_a48": 48, "v10_a200": 200}


def break_ages(bars: pd.DataFrame, gap: np.ndarray, tick: float,
               params: TrendlineParams | None = None) -> pd.DataFrame:
    """Return both directions' break flags and ages on the supplied bar clock.

    `bars` must carry open/high/low/close/atr on a contiguous-by-column clock;
    `gap` marks bars whose predecessor is missing. ATR is the caller's series,
    which in this replay is RMA(TR,14) -- identical to Pine's `ta.atr(14)`.
    """
    params = params or TrendlineParams()
    high, low, close, atr = (bars[name].to_numpy(float) for name in ("high", "low", "close", "atr"))
    long_side = trendline_breaks(high, low, close, atr, gap, tick, direction=1, params=params)
    short_side = trendline_breaks(high, low, close, atr, gap, tick, direction=-1, params=params)
    return pd.DataFrame({"long_break": long_side.break_event, "short_break": short_side.break_event,
                         "long_break_age": long_side.break_age, "short_break_age": short_side.break_age,
                         "long_line_active": long_side.line_active,
                         "short_line_active": short_side.line_active,
                         "long_line_price": long_side.line_price,
                         "short_line_price": short_side.line_price}, index=bars.index)


def signal_break_age(side: np.ndarray, ages: pd.DataFrame) -> np.ndarray:
    """Pick each bar's own-direction break age; -1 means no break to point at."""
    side = np.asarray(side, dtype=int)
    long_age = ages.long_break_age.to_numpy(np.int64)
    short_age = ages.short_break_age.to_numpy(np.int64)
    return np.where(side == 1, long_age, np.where(side == -1, short_age, -1))


def gate_mask(side: np.ndarray, ages: pd.DataFrame, max_break_age: int | None) -> np.ndarray:
    """True where this bar's confirmation sits inside the break window.

    `max_break_age=None` is the ungated V9 control. A signal on a bar whose own
    direction has no recorded break, or whose break sits on the far side of a
    data gap, is refused: an unobservable break is not a fresh break.
    """
    if max_break_age is None:
        return np.ones(len(ages), dtype=bool)
    if max_break_age < 0:
        raise ValueError("max_break_age must be zero or positive")
    age = signal_break_age(side, ages)
    return (age >= 0) & (age <= max_break_age)


def gate_reason(age: int, max_break_age: int | None) -> str:
    """Name why one confirmation passed or failed, for the decision ledger."""
    if max_break_age is None:
        return "ungated_v9_control"
    if age < 0:
        return "no_trendline_break_in_segment"
    if age > max_break_age:
        return f"break_age_{age}_gt_{max_break_age}"
    return "break_on_this_bar" if age == 0 else f"break_age_{age}"
