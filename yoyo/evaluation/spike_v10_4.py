"""SPIKE V10.4 "突破+spike": dual-track three-point descending lines paired with V9 longs.

Source: the Pine v6 script "SPIKE V10.4 · 突破+spike" the owner pasted on
2026-09-18, stored verbatim at `yoyo/evaluation/pine/spike_burst_v10_4_owner.pine`.
This module translates only the parts that decide *when a joint signal fires*:
the "04 · V10.4 自动下降线" structure search, its per-line break/expiry
monitor, the SPIKE-evidence bookkeeping and the joint pairing. Drawing, the
"current main line" choice (the Pine says it only affects the picture) and
labels are not translated.

What is not re-derived here: the V9 core. The caller supplies V9's per-bar
facts (final long confirmation, raw side, the legacy parent range, the V9
gates) from the published engine, so V10.4 inherits V9 exactly.

Per-bar order inside one closed bar follows the Pine literally:
  1. every stored line is tested for expiry / close break, its saved SPIKE
     evidence is cancelled or kept, and a V9 long confirmation on this bar is
     saved onto every line that was already known before this bar;
  2. a line whose break and SPIKE are both present, at most `window` bars
     apart, with the later of the two on this bar, pairs if this bar still
     passes the joint gates; the lowest-score line wins, one SPIKE is consumed
     once;
  3. only then are newly confirmed pivots admitted, spent lines dropped, and a
     new three-point search run, so a line born on bar t cannot break or take
     a SPIKE on bar t.

Causality: a pivot at bar p is only known at p + right (8 bars); searches read
pivots, lows and closes up to the current bar; validation walks bars A..now;
the break test reads the current close and ATR. Nothing after the current bar
is read. Columns used: open, high, low, close, atr (RMA-14 of true range with
SMA seed, identical to Pine `ta.atr(14)` and to V9's `atr`), plus the V9 facts
listed in `joint_events`.

Declared departures from the Pine:
  * `ta.pivothigh` tie handling is not documented by TradingView; pivots are
    strict on both sides (the repo's existing port convention) and the number
    of bars that would qualify only on ties is returned, not hidden;
  * only the default linear price scale is translated (`v10Scale="线性"`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from yoyo.evaluation.trendline_break import _rolling_max

VERSION = "spike-v10.4-joint-20260918-v1"


@dataclass(frozen=True)
class V104Params:
    """The owner's Pine defaults. Changing any one defines a new configuration."""

    lookback: int = 600          # v10Lookback
    life: int = 600              # v10Life
    left: int = 12               # v10Left
    right: int = 8               # v10Right
    min_gap: int = 24            # v10MinGap
    min_span: int = 72           # v10MinSpan
    pullback: float = 2.0        # v10Pullback (ATR)
    drop: float = 1.0            # v10Drop (ATR)
    touch: float = 0.35          # v10Touch (ATR)
    wick_cap: float = 0.60       # v10WickCap (ATR) for the soft track
    body_tol: float = 0.15       # v10BodyTol (ATR)
    wick_tol: float = 0.35       # v10WickTol (ATR)
    needles: int = 2             # v10Needles
    needle_max: float = 3.0      # v10NeedleMax (ATR)
    break_bars: int = 2          # v10BreakBars
    break_buffer: float = 0.20   # v10BreakBuffer (ATR)
    window: int = 6              # v10Window: SPIKE <-> break max distance
    pivots_cap: int = 48         # V10_PIVOTS
    per_bucket: int = 4          # V10_PER_BUCKET
    per_track_group: int = 2     # V10_PER_TRACK_GROUP
    # ta.pivothigh tie rule, undocumented by TradingView: "strict" (both sides
    # strictly lower) or "right_inclusive" (left strictly lower, right <=).
    pivot_ties: str = "strict"

    @property
    def gap(self) -> int:
        return max(self.min_gap, self.right + 1)

    @property
    def span(self) -> int:
        return max(self.min_span, 2 * self.gap)

    def __post_init__(self) -> None:
        if self.lookback < self.span + self.right:
            raise ValueError("V10.4 搜索范围不足: lookback must cover span + right (Pine runtime.error)")
        if self.pivot_ties not in ("strict", "right_inclusive"):
            raise ValueError("pivot_ties must be 'strict' or 'right_inclusive'")


class Line:
    """One three-point structure; the line itself runs through A and B only."""

    __slots__ = ("uid", "ax", "ap", "bx", "bp", "cx", "cp", "fit", "wave", "score", "born", "phase",
                 "above", "stopped", "source", "broken", "usable", "joined", "spike", "parent_high",
                 "parent_low")

    def __init__(self, ax, ap, bx, bp, cx, cp, fit, born, source):
        self.uid = 0
        self.ax, self.ap, self.bx, self.bp, self.cx, self.cp = ax, ap, bx, bp, cx, cp
        self.fit, self.wave, self.score = fit, math.nan, math.nan
        self.born, self.phase, self.above, self.stopped = born, 1, 0, -1
        self.source, self.broken, self.usable, self.joined = source, -1, False, False
        self.spike = -1          # -1 is Pine's na for spikeBar
        self.parent_high = math.nan
        self.parent_low = math.nan

    def at(self, x: int) -> float:
        # Same operation order as f_v10_price, so float results are identical.
        fraction = float(x - self.ax) / (self.bx - self.ax)
        return self.ap + (self.bp - self.ap) * fraction


@dataclass
class V104Result:
    """Per-bar events plus one record per joint signal and per pairing refusal."""

    born_event: np.ndarray
    break_event: np.ndarray
    joint_event: np.ndarray
    joints: list[dict] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    pivot_ties: int = 0
    store_codes: dict = field(default_factory=dict)
    one_sided_extra_pivots: int = 0


def _bucket(span: int, p: V104Params) -> int:
    return 0 if span < p.span * 2 else 1 if span < p.span * 4 else 2


def _rank(bag: list[Line], item: Line, p: V104Params) -> None:
    """f_v10_rank: keep at most `per_bucket` lowest scores per span bucket."""
    bucket = _bucket(item.cx - item.ax, p)
    count, worst, worst_score = 0, -1, -1.0
    for i, old in enumerate(bag):
        if _bucket(old.cx - old.ax, p) == bucket:
            count += 1
            if old.score > worst_score:
                worst, worst_score = i, old.score
    if count < p.per_bucket:
        bag.append(item)
    elif worst >= 0 and item.score < worst_score:
        bag[worst] = item


def _same(first: Line, second: Line, i: int, atr: np.ndarray, tick: float, p: V104Params) -> bool:
    """f_v10_same: identical anchors, or coincident at both overlap edges and now."""
    same = (first.ax == second.ax and first.bx == second.bx
            and abs(first.ap - second.ap) < tick * 0.5 and abs(first.bp - second.bp) < tick * 0.5)
    left, right = max(first.ax, second.ax), min(first.cx, second.cx)
    if not same and right - left >= p.gap:
        left_tol = max(tick, p.touch * 0.5 * atr[left])
        right_tol = max(tick, p.touch * 0.5 * atr[right])
        now_tol = max(tick, p.touch * 0.5 * atr[i])
        same = (abs(first.at(left) - second.at(left)) <= left_tol
                and abs(first.at(right) - second.at(right)) <= right_tol
                and abs(first.at(i) - second.at(i)) <= now_tol)
    return same


def _validate(item: Line, i: int, high: np.ndarray, close: np.ndarray, body: np.ndarray,
              atr: np.ndarray, tick: float, p: V104Params) -> tuple[int, int]:
    """f_v10_validate over bars A..i: (0 ok | 1 body | 2 needle | 3 prior break, spikes).

    The Pine loop checks body, then needle, then the close run inside each
    bar and stops at the first failure; the earliest (step, check) wins here.
    """
    idx = np.arange(item.ax, i + 1)
    fraction = (idx - item.ax).astype(float) / (item.bx - item.ax)
    y = item.ap + (item.bp - item.ap) * fraction
    a = atr[idx]
    a = np.maximum(np.where(np.isfinite(a), a, tick), tick)
    body_bad = np.flatnonzero(body[idx] > y + p.body_tol * a + tick * 0.0001)
    first_body = int(body_bad[0]) if len(body_bad) else 1 << 60
    pierce = (high[idx] - y) / a
    spikes_at = np.flatnonzero(pierce > p.wick_tol)
    first_needle = 1 << 60
    limit = max(p.needle_max, p.wick_tol)
    for n, step in enumerate(spikes_at.tolist(), 1):
        if n > p.needles or pierce[step] > limit or (n > 1 and step - int(spikes_at[n - 2]) == 1):
            first_needle = step
            break
    above = close[idx] > y + p.break_buffer * a
    first_above = 1 << 60
    if len(above) >= p.break_bars:
        run = np.convolve(above.astype(np.int64), np.ones(p.break_bars, dtype=np.int64), "valid")
        hits = np.flatnonzero(run >= p.break_bars)
        if len(hits):
            first_above = int(hits[0]) + p.break_bars - 1
    first = min(first_body, first_needle, first_above)
    if first == 1 << 60:
        return 0, len(spikes_at)
    # The Pine counts a spike before it can fail on it, and a body failure
    # stops the bar before its spike test; only reason 0 uses the count.
    before = int(np.searchsorted(spikes_at, first))
    if first == first_body:
        return 1, before
    counted = before + int(before < len(spikes_at) and spikes_at[before] == first)
    return (2 if first == first_needle else 3), counted


def _search(xs: list, ps: list, ats: list, source: int, i: int, low: np.ndarray, close_i: float,
            atr: np.ndarray, tick: float, p: V104Params) -> list[Line]:
    """f_v10_search: newest pivot is C; A and B from older pivots; short-list per bucket."""
    n = len(xs)
    bag: list[Line] = []
    if n < 3:
        return bag
    c = n - 1
    cx, cp, ca = xs[c], ps[c], ats[c]
    oldest = xs[0]
    X = np.asarray(xs, dtype=np.int64)
    P = np.asarray(ps, dtype=float)
    A = np.asarray(ats, dtype=float)
    Xa, Pa, Aa = X[:n - 2, None], P[:n - 2, None], A[:n - 2, None]
    Xb, Pb, Ab = X[None, :c], P[None, :c], A[None, :c]
    ai = np.arange(n - 2)[:, None]
    bi = np.arange(c)[None, :]
    mask = (bi > ai) & (cx - Xa >= p.span) & (Pa > cp)
    mask &= (Xb - Xa >= p.gap) & (cx - Xb >= p.gap) & (Pa > Pb) & (Pb > cp)
    mask &= Pa - Pb >= p.drop * np.maximum(Aa, Ab)
    if not mask.any():
        return bag
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = (Xb - Xa).astype(float)
        at_c = Pa + (Pb - Pa) * ((cx - Xa).astype(float) / denom)
        at_now = Pa + (Pb - Pa) * ((i - Xa).astype(float) / denom)
        fit = np.abs(cp - at_c) / ca
    mask &= (fit <= p.touch) & (at_now > 0) & (close_i <= at_now)
    if not mask.any():
        return bag
    # lows[x - oldest] in the Pine: min(low[x .. cx-1]).
    suffix = np.minimum.accumulate(low[oldest:cx][::-1])[::-1]
    rows, cols = np.nonzero(mask)          # row-major: A ascending, then B ascending
    prefix = low[:0]
    current_a = -1
    for a, b in zip(rows.tolist(), cols.tolist()):
        ax, ap, aa = xs[a], ps[a], ats[a]
        if a != current_a:
            # prefix[k] in the Pine: min(low[ax+1 .. ax+k]); stored shifted by one.
            prefix = np.minimum.accumulate(low[ax + 1:cx])
            current_a = a
        bx, bp = xs[b], ps[b]
        item = Line(ax, ap, bx, bp, cx, cp, float(fit[a, b]), i, source)
        ba = max(atr[bx], tick)
        ab = (min(ap, bp) - prefix[bx - ax - 2]) / max(aa, ba)
        bc = (min(bp, cp) - suffix[bx + 1 - oldest]) / max(ba, ca)
        if ab >= p.pullback and bc >= p.pullback:
            item.wave = min(ab, bc)
            item.score = item.fit + 0.20 / (1.0 + item.wave)
            _rank(bag, item, p)
    return bag


def _store(pool: list[Line], item: Line, i: int, high, close, body, atr, tick, p: V104Params) -> int:
    """f_v10_store: 1 stored, 0 duplicate, 2 body, 3 needle, 4 already broken, 5 no room."""
    for known in pool:
        if _same(known, item, i, atr, tick, p):
            return 0
    reason, spikes = _validate(item, i, high, close, body, atr, tick, p)
    if reason != 0:
        return reason + 1
    item.score += 0.05 * spikes
    bucket = _bucket(item.cx - item.ax, p)
    group, worst, worst_score = 0, -1, -1.0
    for k, known in enumerate(pool):
        if known.source == item.source and _bucket(known.cx - known.ax, p) == bucket:
            group += 1
            pending = known.usable or known.spike >= 0
            if not pending and known.score > worst_score:
                worst, worst_score = k, known.score
    room = group < p.per_track_group
    if not room and worst >= 0 and item.score < worst_score:
        del pool[worst]
        room = True
    if room:
        pool.append(item)
        return 1
    return 5


def pivots(u: np.ndarray, left: int, right: int, mode: str = "strict") -> tuple[np.ndarray, int, int]:
    """Pivot bar for every confirmation bar (-1 if none), plus two tie counts.

    A pivot needs `left` bars before and `right` bars after it, all finite. The
    counts are how many bars qualify only under fully non-strict comparison
    (flat stretches included) and how many extra pivots the one-sided rule
    "left strict, right <=" adds over strict; the second is the number that
    measures how much the undocumented TradingView rule can matter.
    """
    u = np.asarray(u, dtype=float)
    n = len(u)
    pivot_of = np.full(n, -1, dtype=np.int64)
    if n < left + right + 1:
        return pivot_of, 0, 0
    finite = np.isfinite(u)
    centers = np.arange(left, n - right)
    left_max = _rolling_max(u, left)[centers - left]
    right_max = _rolling_max(u, right)[centers + 1]
    counts = np.concatenate([[0], np.cumsum(finite.astype(np.int64))])
    whole = counts[centers + right + 1] - counts[centers - left] == left + right + 1
    value = u[centers]
    strict = whole & (value > left_max) & (value > right_max)
    one_sided = whole & (value > left_max) & (value >= right_max)
    loose = whole & (value >= left_max) & (value >= right_max)
    chosen = strict if mode == "strict" else one_sided
    pivot_of[centers[chosen] + right] = centers[chosen]
    return pivot_of, int((loose & ~strict).sum()), int((one_sided & ~strict).sum())


def soft_peak(open_: np.ndarray, high: np.ndarray, close: np.ndarray, atr: np.ndarray,
              wick_cap: float) -> np.ndarray:
    """v10Peak in 削尖影线 mode: min(high, body top + cap*ATR); na while ATR is na."""
    body = np.maximum(open_, close)
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(atr), np.minimum(high, body + wick_cap * atr), np.nan)


def joint_events(open_, high, low, close, atr, *, can_run, confirmed_long, parent_high, parent_low,
                 raw_side, long_alive, momentum, current_gate, ref_long_exit, tick: float,
                 params: V104Params | None = None) -> V104Result:
    """Replay the V10.4 structure monitor and joint pairing bar by bar.

    V9 inputs, one value per closed bar, all known at that bar's close:
      can_run         prices valid, no data gap, ATR finite and > 0 (v10CanRun)
      confirmed_long  final V9 long confirmation after every V9 filter
      parent_high/low the long structure's legacy parent range on that bar
      raw_side        V9 structural confirmation before V9 filters (+1/-1/0)
      long_alive      ready, close above all six MAs, raw side not short
      momentum        md > sb and md > md[1]
      current_gate    BB compression gate, V9 bundle, <= 3 ATR from the MAs
      ref_long_exit   the indicator's V9 long reference ended on this bar
    """
    p = params or V104Params()
    o, h, lo, c, a = (np.asarray(x, dtype=float) for x in (open_, high, low, close, atr))
    n = len(c)
    arrays = (can_run, confirmed_long, parent_high, parent_low, raw_side, long_alive, momentum,
              current_gate, ref_long_exit)
    if any(len(x) != n for x in (o, h, lo, a, *arrays)):
        raise ValueError("misaligned bar arrays")
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")
    body = np.maximum(o, c)
    soft = soft_peak(o, h, c, a, p.wick_cap)
    raw_pivot_of, raw_ties, raw_extra = pivots(h, p.left, p.right, p.pivot_ties)
    soft_pivot_of, soft_ties, soft_extra = pivots(soft, p.left, p.right, p.pivot_ties)

    can = np.asarray(can_run, dtype=bool).tolist()
    confirmed = np.asarray(confirmed_long, dtype=bool).tolist()
    ph, pl = np.asarray(parent_high, float).tolist(), np.asarray(parent_low, float).tolist()
    side = np.asarray(raw_side, dtype=np.int64).tolist()
    alive = np.asarray(long_alive, dtype=bool).tolist()
    mom = np.asarray(momentum, dtype=bool).tolist()
    gate = np.asarray(current_gate, dtype=bool).tolist()
    ref_exit = np.asarray(ref_long_exit, dtype=bool).tolist()
    closes, atrs = c.tolist(), a.tolist()
    raw_of, soft_of = raw_pivot_of.tolist(), soft_pivot_of.tolist()

    born_event = np.zeros(n, dtype=bool)
    break_event = np.zeros(n, dtype=bool)
    joint_event = np.zeros(n, dtype=bool)
    joints: list[dict] = []
    refusals: list[dict] = []
    codes = {k: 0 for k in range(6)}

    rx: list[int] = []; rp: list[float] = []; ra: list[float] = []
    sx: list[int] = []; sp: list[float] = []; sa: list[float] = []
    tracks: list[Line] = []
    serial = 0
    segment_start = 0
    consumed = -1

    for i in range(n):
        if not can[i]:
            rx.clear(); rp.clear(); ra.clear(); sx.clear(); sp.clear(); sa.clear()
            tracks.clear()
            segment_start = i
            continue
        close_i, atr_i = closes[i], atrs[i]
        break_winner = joint_winner = None
        pair_attempt = False
        failure = ""
        for item in tracks:
            y = item.at(i)
            if item.phase == 1:
                if i - item.born > p.life or y <= 0:
                    item.phase, item.stopped, item.spike = 3, i, -1
                elif i > item.born:
                    item.above = item.above + 1 if close_i > y + atr_i * p.break_buffer else 0
                    if item.above >= p.break_bars:
                        item.phase, item.stopped, item.broken, item.usable = 2, i, i, True
                        if break_winner is None or item.score < break_winner.score:
                            break_winner = item
            if item.spike >= 0:
                if item.spike <= consumed or i - item.spike > p.window:
                    item.spike = -1
                elif (not alive[i] or math.isnan(item.parent_low) or close_i < item.parent_low
                      or ref_exit[i]):
                    item.spike = -1
            if item.usable:
                if i - item.broken > p.window or close_i <= y or side[i] == -1:
                    item.usable, item.spike = False, -1
            available = item.born < i and not item.joined and (item.phase == 1 or item.usable)
            if confirmed[i] and available:
                item.spike, item.parent_high, item.parent_low = i, ph[i], pl[i]
            if item.usable and not item.joined and item.spike >= 0 and item.spike > consumed:
                within = abs(item.spike - item.broken) <= p.window
                second_now = i == max(item.spike, item.broken)
                born_before = item.born < min(item.spike, item.broken)
                recovered = not math.isnan(item.parent_high) and close_i > item.parent_high
                if within and second_now and born_before:
                    pair_attempt = True
                    if alive[i] and mom[i] and gate[i] and recovered:
                        if joint_winner is None or item.score < joint_winner.score:
                            joint_winner = item
                    else:
                        failure = ("no_ma_support" if not alive[i] else "momentum" if not mom[i]
                                   else "parent_not_recovered" if not recovered else "gate")
        break_event[i] = break_winner is not None
        if pair_attempt and joint_winner is None:
            refusals.append({"i": i, "reason": failure})
        if joint_winner is not None:
            w = joint_winner
            joint_event[i] = True
            consumed = w.spike
            order = "same_bar" if w.spike == w.broken else "spike_first" if w.spike < w.broken else "break_first"
            joints.append({"i": i, "spike_i": w.spike, "break_i": w.broken, "order": order, "uid": w.uid,
                           "source": "full_wick" if w.source == 0 else "capped_wick", "score": w.score,
                           "ax": w.ax, "ap": w.ap, "bx": w.bx, "bp": w.bp, "cx": w.cx, "cp": w.cp,
                           "born_i": w.born, "line_at_signal": w.at(i),
                           "parent_high": w.parent_high, "parent_low": w.parent_low})
            w.joined, w.usable = True, False
            for item in tracks:
                if item.spike >= 0 and item.spike <= consumed:
                    item.spike = -1

        # New pivots confirmed on this bar, one price source per track.
        raw_new = soft_new = False
        px = i - p.right
        if px - p.left >= segment_start and px >= 0 and atrs[px] == atrs[px]:
            scale = max(atrs[px], tick)
            if raw_of[i] >= 0:
                rx.append(px); rp.append(float(h[px])); ra.append(scale); raw_new = True
            if soft_of[i] >= 0:
                sx.append(px); sp.append(float(soft[px])); sa.append(scale); soft_new = True
        for xs, ps, ats in ((rx, rp, ra), (sx, sp, sa)):
            while xs and (i - xs[0] > p.lookback or len(xs) > p.pivots_cap):
                xs.pop(0); ps.pop(0); ats.pop(0)
        for k in range(len(tracks) - 1, -1, -1):
            if tracks[k].phase != 1 and not tracks[k].usable:
                del tracks[k]
        for source, new, xs, ps, ats in ((0, raw_new, rx, rp, ra), (1, soft_new, sx, sp, sa)):
            if not new:
                continue
            for item in _search(xs, ps, ats, source, i, lo, close_i, a, tick, p):
                code = _store(tracks, item, i, h, c, body, a, tick, p)
                codes[code] += 1
                if code == 1:
                    serial += 1
                    item.uid = serial
                    born_event[i] = True
    return V104Result(born_event, break_event, joint_event, joints, refusals, raw_ties + soft_ties, codes,
                      raw_extra + soft_extra)


def reference_long_exits(high, low, close, atr, *, ready, gap, raw_side, signal_side,
                         tick: float, arm_r: float = 2.0, trail_atr: float = 4.0,
                         floor_atr: float = 2.0, buffer_atr: float = 0.2, stop_len: int = 5) -> np.ndarray:
    """The indicator's own V9 risk reference; True on bars where a LONG reference ended.

    Translated from the Pine sections "原有风险参考与退出", f_risk and f_path. The
    reference opens at the confirmation close (not a fill); it is only needed
    because V10.4 drops saved SPIKE evidence on a bar where the long reference
    ends. It is not the backtest's trade engine.
    """
    h, lo, c, a = (np.asarray(x, dtype=float) for x in (high, low, close, atr))
    n = len(c)
    ready, gap = np.asarray(ready, bool), np.asarray(gap, bool)
    raw, sig = np.asarray(raw_side, np.int64), np.asarray(signal_side, np.int64)
    recent_low = np.full(n, np.nan)
    recent_high = np.full(n, np.nan)
    if n >= stop_len:
        win_low = np.lib.stride_tricks.sliding_window_view(lo, stop_len).min(axis=1)
        win_high = np.lib.stride_tricks.sliding_window_view(h, stop_len).max(axis=1)
        recent_low[stop_len - 1:] = win_low
        recent_high[stop_len - 1:] = win_high
    out = np.zeros(n, dtype=bool)
    trend = 0
    entry_bar = -1
    entry = risk = protection = math.nan
    armed = False

    def f_risk(s: int, price: float, extreme: float, atr_i: float):
        if not (s in (1, -1) and price > 0 and atr_i > 0 and math.isfinite(extreme)):
            return math.nan, math.nan, False
        cand = (min(extreme - buffer_atr * atr_i, price - floor_atr * atr_i) if s == 1
                else max(extreme + buffer_atr * atr_i, price + floor_atr * atr_i))
        stop = math.floor(cand / tick) * tick if s == 1 else math.ceil(cand / tick) * tick
        r = s * (price - stop)
        ok = stop > 0 and r > 0
        return (stop if ok else math.nan), (r if ok else math.nan), ok

    for i in range(n):
        exit_side = 0
        if gap[i] and trend != 0:
            trend = 0
        ended = False
        if ready[i] and trend != 0 and i > entry_bar:
            stopped = lo[i] <= protection if trend == 1 else h[i] >= protection
            current = trend * (c[i] - entry) / risk
            armed = armed or (not stopped and current >= arm_r)
            if not stopped and armed and a[i] > 0:
                raw_stop = c[i] - trend * trail_atr * a[i]
                cand = math.floor(raw_stop / tick) * tick if trend == 1 else math.ceil(raw_stop / tick) * tick
                protection = max(protection, cand) if trend == 1 else min(protection, cand)
            if stopped:
                exit_side, ended, trend = trend, True, 0
        opposite_stop = ended and exit_side != 0 and raw[i] != exit_side
        if raw[i] != 0 and (not ended or opposite_stop) and raw[i] != trend:
            stop, r, ok = f_risk(int(raw[i]), c[i], recent_low[i] if raw[i] == 1 else recent_high[i], a[i])
            if trend != 0:
                exit_side, trend = trend, 0
            if sig[i] != 0 and ok:
                trend, entry_bar, entry, risk, protection, armed = int(sig[i]), i, c[i], r, stop, False
        out[i] = exit_side == 1
    return out
