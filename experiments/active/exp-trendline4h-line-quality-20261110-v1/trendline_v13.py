"""Prototype V1.3 of the 4H main trendline (V1.2 port + fit ranking and a floating-line cap).

Changes vs V1.2, nothing else: (1) among candidates with equal touches prefer the
smaller mean gap (line - high)/ATR over [A, x] instead of the longer span;
(2) reject candidates whose mean gap exceeds max_gap ATR. Original V1.2 docstring:

Python port of the 4H main trendline V1.2 (resistance side) for an event census.

Source: yoyo/evaluation/pine/auto_trendline_4h_v1_2.pine (same engine is merged
verbatim into spike_signal_cloud_v1_8.pine). Line for line it mirrors f_track /
f_pick / clean with sign = +1; drawing is dropped. Every bar is a confirmed bar.

Causality: a pivot at bar p is known at p + swing; a line can only be born on a
confirmed close after the settling window; the break is the second consecutive
close above line + 0.15 * ATR14. Inputs per bar: open/high/low/close and Pine's
ta.atr(14) (RMA of true range, SMA seed); no data after bar x is read at bar x.
Pivot ties: TradingView does not document ta.pivothigh tie handling; strict on
both sides here, and tie-only pivots are counted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

WINDOW = 360
TAIL = 24


def pine_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.where(np.isnan(prev), high - low,
                  np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev))))
    out = np.full(len(tr), np.nan)
    if len(tr) < length:
        return out
    out[length - 1] = tr[:length].mean()
    alpha = 1.0 / length
    for i in range(length, len(tr)):
        out[i] = out[i - 1] + alpha * (tr[i] - out[i - 1])
    return out


def strict_pivot_highs(high: np.ndarray, swing: int) -> tuple[np.ndarray, int]:
    """confirm[x] = True when bar x - swing is a strict pivot high (known at x)."""
    n = len(high)
    confirm = np.zeros(n, dtype=bool)
    ties = 0
    for p in range(swing, n - swing):
        left = high[p - swing:p]
        right = high[p + 1:p + swing + 1]
        # TradingView ta.pivothigh (verified 2026-11-10 on OKX:LPTUSDT.P 4H):
        # ties with bars on the LEFT still count, bars on the RIGHT must be lower.
        if high[p] >= left.max() and high[p] > right.max():
            confirm[p + swing] = True
        elif high[p] >= left.max() and high[p] >= right.max():
            ties += 1
    return confirm, ties


@dataclass
class Pivot:
    x: int
    y: float
    vol: float


@dataclass
class Candidate:
    a: Pivot
    b: Pivot
    touches: int
    fit: float = 0.0

    def level(self, x: float) -> float:
        return self.b.y + (self.b.y - self.a.y) * (x - self.b.x) / (self.b.x - self.a.x)


def outranks(self: Candidate, other: Candidate | None) -> bool:
    return other is None or self.touches > other.touches or (
        self.touches == other.touches and self.fit < other.fit)


@dataclass
class Event:
    kind: str  # "born" | "break" | "age"
    x: int
    a_x: int
    a_y: float
    b_x: int
    b_y: float
    touches: int
    level: float


@dataclass
class Result:
    events: list[Event] = field(default_factory=list)
    pivot_ties: int = 0


def run(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, *, swing: int = 3,
        min_span: int = 72, gap: int = 24, near_atr: float = 0.65, max_gap: float = 5.0) -> Result:
    n = len(c)
    atr = pine_atr(h, l, c)
    body_top = np.maximum(o, c)
    confirm, ties = strict_pivot_highs(h, swing)
    res = Result(pivot_ties=ties)

    def clean(cand: Candidate, x: int) -> bool:
        idx = np.arange(cand.a.x, x + 1)
        limit = cand.b.y + (cand.b.y - cand.a.y) * (idx - cand.b.x) / (cand.b.x - cand.a.x)
        a = atr[idx]
        bad = (h[idx] > limit + 0.30 * a) | (body_top[idx] > limit + 0.10 * a)  # NaN -> False like Pine
        cand.fit = float(np.nanmean(np.maximum(limit - h[idx], 0) / a))
        return not bool(bad.any()) and cand.fit <= max_gap

    def pick(pivots: list[Pivot], b: Pivot, closing: float, x: int) -> Candidate | None:
        best = None
        for idx, a in enumerate(pivots):
            if idx == len(pivots) - 1:
                break
            if b.x - a.x >= max(min_span, 2 * gap) and a.y - b.y >= 2 * b.vol:
                test = Candidate(a, b, 2)
                last_touch = a.x
                for j in range(idx + 1, len(pivots)):
                    mid = pivots[j]
                    if mid.x > b.x - gap:
                        break
                    if mid.x - last_touch >= gap and abs(mid.y - test.level(mid.x)) <= near_atr * mid.vol:
                        test.touches += 1
                        last_touch = mid.x
                if test.touches >= 3 and closing <= test.level(x) and clean(test, x) and outranks(test, best):
                    best = test
        return best

    pivots: list[Pivot] = []
    pending: Candidate | None = None
    due: int | None = None
    current: Candidate | None = None
    ended: int | None = None
    beyond = 0
    for x in range(n):
        closing = c[x]
        if current is not None and ended is None:
            value = current.level(x)
            beyond = beyond + 1 if closing > value + 0.15 * atr[x] else 0
            broken = beyond >= 2
            if broken or x - current.a.x >= WINDOW:
                ended = x
                res.events.append(Event("break" if broken else "age", x, current.a.x, current.a.y,
                                        current.b.x, current.b.y, current.touches, value))
                beyond = 0
                pending = None
                due = None
        if confirm[x] and not np.isnan(atr[x - swing]):
            b = Pivot(x - swing, float(h[x - swing]), float(atr[x - swing]))
            while pivots and x - pivots[0].x > WINDOW:
                pivots.pop(0)
            active = current is not None and ended is None
            if active:
                search = current.level(x) - closing > 3 * atr[x]
            else:
                search = ended is None or b.x - ended >= gap
            if search and len(pivots) >= 2:
                test = pick(pivots, b, closing, x)
                if test is not None:
                    distinct = (not active) or (test.a.x > current.b.x and test.level(x) < current.level(x))
                    if distinct and outranks(test, pending):
                        if pending is None:
                            due = x + 2 * swing
                        pending = test
            pivots.append(b)
        if pending is not None and x >= due:
            active = current is not None and ended is None
            still = (not active) or (current.level(x) - closing > 3 * atr[x] and pending.level(x) < current.level(x))
            if still and x - pending.a.x <= WINDOW and closing <= pending.level(x) and clean(pending, x):
                current = pending
                res.events.append(Event("born", x, current.a.x, current.a.y, current.b.x, current.b.y,
                                        current.touches, current.level(x)))
                ended = None
                beyond = 0
            pending = None
            due = None
    return res
