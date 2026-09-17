"""Causal replay of the owner's "主下降趋势线 · 关键高点 V1" Pine indicator.

Source: the Pine v6 script the owner pasted on 2026-09-18. Every rule here is a
line-for-line translation of that script, including the order of operations
inside one confirmed bar: (1) test the line that already existed for a close
break or an age expiry, (2) push a newly confirmed pivot, (3) only then, and
only on a bar that confirmed a pivot and did not just break, search for a
replacement line.

Causality: a pivot at bar p is only known at bar p + right_bars, so a line born
at bar b uses nothing after b. `f_is_clean` walks bars x1..b, `projected_now`
compares the current close, and the break test reads the current close and the
current ATR. No future bar is ever read. The lag of `right_bars` is the drawing
method's own latency, not look-ahead.

Two deliberate departures from the pasted script, both documented in the V10
report:
  * data gaps clear the pivot store and kill the active line, because a line
    drawn across bars this replay never observed is not evidence;
  * the mirror (`direction=-1`) reads pivot lows and breaks downward, so short
    signals can be gated by the same construction. The pasted script only draws
    the descending resistance line.

ATR is the caller's series. The V9 replay and the V9 Pine script both use
RMA(TR,14) with an SMA seed, which is exactly `ta.atr(14)`, so the merged V10
script reuses that one series instead of instantiating a second ATR.

A second, independent port of the same Pine lives in
`yoyo/evaluation/trendline_v2_signals.py`, written by a parallel session for the
standalone strategy question rather than for gating V9. It is long-only and
assumes gap-free input, which is why the two are not merged. They are held
equal instead: `test_trendline_break.py` asserts identical break, born and
active flags on six seeds, so if either port drifts, a test says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class TrendlineParams:
    """Owner's Pine defaults, byte-for-byte. Changing one defines a new config."""

    left_bars: int = 12
    right_bars: int = 8
    lookback: int = 600
    min_span: int = 48
    min_drop_atr: float = 2.0
    min_touches: int = 2
    wick_tol_atr: float = 0.35
    touch_tol_atr: float = 0.50
    break_buf_atr: float = 0.20
    break_bars: int = 2
    max_candidates: int = 40

    def __post_init__(self) -> None:
        if self.lookback < self.min_span + self.right_bars:
            raise ValueError("lookback must cover min_span + right_bars, as the Pine runtime.error demands")
        if min(self.left_bars, self.right_bars, self.break_bars, self.min_touches) < 1:
            raise ValueError("bar counts must be positive")


@dataclass
class TrendlineResult:
    """Per-bar outputs; every array is aligned to the input bar clock."""

    break_event: np.ndarray
    born_event: np.ndarray
    line_active: np.ndarray
    break_age: np.ndarray
    line_price: np.ndarray
    anchors: list[dict] = field(default_factory=list)
    pivot_ties: int = 0


def _rolling_max(values: np.ndarray, width: int) -> np.ndarray:
    """windows[k] is max(values[k:k+width]); non-finite entries never win."""
    filled = np.where(np.isfinite(values), values, -np.inf)
    return np.lib.stride_tricks.sliding_window_view(filled, width).max(axis=1)


def _confirmed_pivots(u: np.ndarray, left: int, right: int, finite: np.ndarray) -> tuple[np.ndarray, int]:
    """Return the pivot bar for every confirmation bar, plus a tie count.

    A pivot needs `left` strictly lower bars before it and `right` strictly
    lower bars after it, every one of them a finite observed bar. TradingView
    does not publish the tie handling inside `ta.pivothigh`; bars that would
    only qualify on non-strict comparisons are counted and reported rather than
    silently included or silently dropped.
    """
    n = len(u)
    pivot_of = np.full(n, -1, dtype=np.int64)
    if n < left + right + 1:
        return pivot_of, 0
    centers = np.arange(left, n - right)
    left_max = _rolling_max(u, left)[centers - left]
    right_max = _rolling_max(u, right)[centers + 1]
    counts = np.concatenate([[0], np.cumsum(finite.astype(np.int64))])
    whole = counts[centers + right + 1] - counts[centers - left] == left + right + 1
    value = u[centers]
    strict = whole & (value > left_max) & (value > right_max)
    loose = whole & (value >= left_max) & (value >= right_max)
    pivot_of[centers[strict] + right] = centers[strict]
    return pivot_of, int((loose & ~strict).sum())


def trendline_breaks(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray,
                     gap: np.ndarray, tick: float, *, direction: int = 1,
                     params: TrendlineParams | None = None) -> TrendlineResult:
    """Replay the pivot-anchored line and its close-confirmed break, bar by bar.

    `direction=1` is the pasted script: a descending line over pivot highs,
    broken upward. `direction=-1` mirrors it onto pivot lows, broken downward.
    The mirror is a sign transform of the same arithmetic, not a second rule.
    """
    p = params or TrendlineParams()
    if direction not in (1, -1):
        raise ValueError("direction must be 1 (resistance) or -1 (support)")
    high, low, close, atr = (np.asarray(x, dtype=float) for x in (high, low, close, atr))
    gap = np.asarray(gap, dtype=bool)
    n = len(close)
    if not (len(high) == len(low) == len(atr) == len(gap) == n):
        raise ValueError("misaligned bar arrays")
    if not np.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")

    # One sign transform carries the mirror: u is the pivot/wick surface and c
    # is the close, both oriented so "higher" always means "further through".
    u = high if direction == 1 else -low
    c = close if direction == 1 else -close
    finite_bar = np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
    pivot_of, ties = _confirmed_pivots(u, p.left_bars, p.right_bars, finite_bar)

    break_event = np.zeros(n, dtype=bool)
    born_event = np.zeros(n, dtype=bool)
    line_active_out = np.zeros(n, dtype=bool)
    line_price = np.full(n, np.nan)

    # Python lists for the per-bar hot path; numpy is reserved for the window
    # arithmetic. This is a speed choice only, the arithmetic is identical.
    u_list = u.tolist()
    c_list = c.tolist()
    atr_list = atr.tolist()
    finite_list = finite_bar.tolist()
    gap_list = gap.tolist()

    px: list[int] = []
    py: list[float] = []
    pa: list[float] = []
    anchors: list[dict] = []

    active = False
    x1 = x2 = born_bar = -1
    y1 = y2 = slope = np.nan
    above_count = 0
    segment_start = 0

    for i in range(n):
        if gap_list[i] or not finite_list[i]:
            # An unobserved interval cannot certify a line, a touch or a break.
            px.clear(); py.clear(); pa.clear()
            active = False
            above_count = 0
            segment_start = i + 1 if not finite_list[i] else i
            continue

        broke_here = False
        if active:
            projected = y1 + slope * (i - x1)
            line_price[i] = direction * projected
            if i > born_bar:
                a_now = atr_list[i]
                above = a_now == a_now and c_list[i] > projected + a_now * p.break_buf_atr
                above_count = above_count + 1 if above else 0
                if above_count >= p.break_bars:
                    broke_here = True
                    break_event[i] = True
                    active = False
                    above_count = 0
            if active and i - x1 > p.lookback:
                # Expiry retires the line; it is deliberately not a break.
                active = False
                above_count = 0

        pivot_bar = int(pivot_of[i])
        new_pivot = False
        if pivot_bar - p.left_bars >= segment_start and atr_list[pivot_bar] == atr_list[pivot_bar]:
            px.append(pivot_bar)
            py.append(u_list[pivot_bar])
            pa.append(max(atr_list[pivot_bar], tick))
            new_pivot = True

        while px and (i - px[0] > p.lookback or len(px) > p.max_candidates):
            px.pop(0); py.pop(0); pa.pop(0)

        if not active and not broke_here and new_pivot and len(px) >= 2:
            k = len(px) - 1
            cx2, cy2, ca2 = px[k], py[k], pa[k]
            # One matrix pass replaces the Pine touch loop; row j holds the
            # candidate line, column m the pivot it is being measured against.
            X = np.asarray(px, dtype=float)
            Y = np.asarray(py, dtype=float)
            A = np.asarray(pa, dtype=float)
            span_all = cx2 - X[:k]
            with np.errstate(divide="ignore", invalid="ignore"):
                slope_all = (cy2 - Y[:k]) / span_all
            geometry = (span_all >= p.min_span) & (Y[:k] > cy2) & (Y[:k] - cy2 >= ca2 * p.min_drop_atr)
            geometry &= c_list[i] <= Y[:k] + slope_all * (i - X[:k])
            expected = Y[:k, None] + slope_all[:, None] * (X[None, :] - X[:k, None])
            hit = np.abs(Y[None, :] - expected) <= (A[None, :] * p.touch_tol_atr)
            hit &= np.arange(len(px))[None, :] >= np.arange(k)[:, None]
            touch_counts = hit.sum(axis=1)
            scores = span_all + np.maximum(touch_counts - 2, 0) * p.min_span * 2.0
            viable = geometry & (touch_counts >= p.min_touches)
            best_score = -1.0
            best_x1 = -1
            best_y1 = np.nan
            for j in np.flatnonzero(viable):
                score = float(scores[j])
                if score <= best_score:
                    continue
                cx1, cy1, cand_slope = px[j], py[j], float(slope_all[j])
                idx = np.arange(cx1, i + 1)
                ceiling = cy1 + cand_slope * (idx - cx1)
                window_atr = atr[idx]
                tol = np.where(np.isfinite(window_atr), window_atr, tick) * p.wick_tol_atr
                if np.any(u[idx] > ceiling + tol):
                    continue
                best_score, best_x1, best_y1 = score, cx1, cy1
            if best_x1 >= 0:
                x1, y1, x2, y2 = best_x1, best_y1, cx2, cy2
                slope = (y2 - y1) / (x2 - x1)
                active = True
                born_bar = i
                above_count = 0
                born_event[i] = True
                line_price[i] = direction * (y1 + slope * (i - x1))
                anchors.append({"born_i": i, "x1": x1, "y1": direction * y1,
                                "x2": x2, "y2": direction * y2, "score": best_score})
        line_active_out[i] = active

    # Bars since the most recent confirmed break; 0 on the break bar itself.
    # Bars since the break, cleared at a gap: a break on the far side of an
    # unobserved interval cannot be called fresh.
    break_age = np.full(n, -1, dtype=np.int64)
    last = -1
    for i in range(n):
        if gap[i] or not finite_bar[i]:
            last = -1
        if break_event[i]:
            last = i
        break_age[i] = -1 if last < 0 else i - last
    return TrendlineResult(break_event, born_event, line_active_out, break_age, line_price, anchors, ties)
