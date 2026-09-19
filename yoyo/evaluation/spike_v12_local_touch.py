"""Bounded reference for the SPIKE V12 auxiliary local-touch family.

This module is deliberately separate from :mod:`spike_v10_4`.  V10.4's
three-point search requires all three pivots to be major pivots.  V12 keeps
the original confirmed major A/B pivots and lets C be a shorter, causal local
pivot.  The implementation is an audit reference for the supplemental family;
it does not claim parity with a merged Pine pool.

The chronology is the important part of this port:

* major pivots use the original left=12/right=8 confirmation;
* a local C uses ``local_left/local_right`` (2/2 by default), and a search is
  run only on the bar that confirms that C;
* B must be known by the candidate decision bar (``B + major_right <= i``),
  rather than by C itself.  This permits the ONE example where B becomes
  known shortly after C while still refusing to backfill C on later bars;
* a stored candidate is born after the local confirmation close.  It cannot
  break on its birth bar; two later close confirmations above the line are
  required for one break event.

The raw and soft tracks use the same geometry, validation, deduplication, and
capacity rules.  The soft track uses the original 0.60 ATR capped high by
default.  Inputs are ``open/high/low/close/atr`` and an optional ``can_run``
or ``gap`` mask.  No value after the current bar is read when an event is
decided.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Sequence

import numpy as np

from yoyo.evaluation.spike_v10_4 import (
    Line,
    V104Params,
    _bucket,
    _same,
    _validate,
    pivots,
    soft_peak,
)


VERSION = "spike-v12-local-touch-20260920-v1"
KIND = "auxiliary_touch"
TRACK_NAMES = {0: "raw", 1: "soft"}


@dataclass(frozen=True)
class LocalTouchParams:
    """Parameters owned by the V12 auxiliary local-touch family.

    The major geometry, validation, lifetime, and capacity values remain in
    :class:`V104Params` or the fixed V10.4 pool rules.  Three span buckets ×
    two tracks × two lines gives twelve active auxiliary candidates.  With
    ``rebound_at_b=True``, the A-B valley-to-B rebound uses B's ATR as its
    scale; ``False`` retains the legacy ``max(A_ATR, B_ATR, tick)`` scale for
    side-by-side audit comparisons.
    """

    local_left: int = 2
    local_right: int = 2
    min_gap: int = 4
    enabled: bool = True
    rebound_at_b: bool = True

    @property
    def gap(self) -> int:
        """The effective B-C spacing, including the causal confirmation gap."""

        return max(4, int(self.min_gap), int(self.local_right) + 1)

    def __post_init__(self) -> None:
        if self.local_left < 1 or self.local_right < 1:
            raise ValueError("local_left and local_right must be positive")
        if self.min_gap < 1:
            raise ValueError("min_gap must be positive")


# A descriptive alias is useful to callers that name all versioned parameter
# classes with a V12 prefix.  Both names construct the same immutable object.
V12LocalTouchParams = LocalTouchParams


@dataclass
class LocalTouchResult:
    """Per-bar auxiliary events plus the bounded candidates and audit trace."""

    born_event: np.ndarray
    break_event: np.ndarray
    lines: list[Line] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    store_codes: dict[int, int] = field(default_factory=dict)

    @property
    def auxiliary_born_event(self) -> np.ndarray:
        return self.born_event

    @property
    def auxiliary_break_event(self) -> np.ndarray:
        return self.break_event

    @property
    def active_lines(self) -> list[Line]:
        """The final live pool; ``lines`` retains historical stored lines."""

        return [item for item in self.lines if item.phase == 1]

    @property
    def max_active_count(self) -> int:
        """Maximum simultaneous auxiliary pool size observed during replay."""

        counts = self.trace.get("active_count")
        return int(np.max(counts)) if counts is not None and len(counts) else 0


def _track(source: int) -> str:
    return TRACK_NAMES.get(int(source), str(source))


def _line_payload(item: Line, *, break_i: int | None = None) -> dict[str, Any]:
    """Serialize stable geometry metadata without changing the shared Line."""

    return {
        "uid": int(item.uid),
        "kind": KIND,
        "kind_id": 1,
        "track": _track(item.source),
        "track_id": int(item.source),
        "source": int(item.source),
        "ax": int(item.ax),
        "ap": float(item.ap),
        "bx": int(item.bx),
        "bp": float(item.bp),
        "cx": int(item.cx),
        "cp": float(item.cp),
        "fit": float(item.fit),
        "wave": float(item.wave),
        "score": float(item.score),
        "born_i": int(item.born),
        "break_i": None if break_i is None else int(break_i),
    }


def _init_trace(trace: dict[str, Any] | None, n: int) -> dict[str, Any]:
    """Create the trace container used by both enabled and disabled runs."""

    out = trace if trace is not None else {}
    out.clear()
    out.update(
        {
            "version": VERSION,
            "kind": KIND,
            "lines": [],
            "line_events": [],
            "events": [],
            "pivot_events": [],
            "gaps": [],
            "born_event": np.zeros(n, dtype=bool),
            "break_event": np.zeros(n, dtype=bool),
            "active_count": np.zeros(n, dtype=np.int64),
        }
    )
    return out


def _rank_local(bag: list[Line], item: Line, p: V104Params, cap: int) -> None:
    """Keep a deterministic short-list for one local C and span bucket."""

    bucket = _bucket(item.cx - item.ax, p)
    same_bucket = [old for old in bag if _bucket(old.cx - old.ax, p) == bucket]
    if len(same_bucket) < cap:
        bag.append(item)
        return
    worst = max(same_bucket, key=lambda old: old.score)
    if item.score < worst.score:
        bag[bag.index(worst)] = item


def _finite_min(values: np.ndarray) -> float | None:
    """Return a finite minimum, or None when a causal valley is unavailable."""

    if values.size == 0:
        return None
    finite = values[np.isfinite(values)]
    return float(np.min(finite)) if finite.size else None


def _local_search(
    major_xs: Sequence[int],
    major_ps: Sequence[float],
    major_ats: Sequence[float],
    *,
    cx: int,
    cp: float,
    ca: float,
    source: int,
    i: int,
    low: np.ndarray,
    close_i: float,
    atr: np.ndarray,
    tick: float,
    p: V104Params | None = None,
    local_params: LocalTouchParams | None = None,
) -> list[Line]:
    """Search one explicitly supplied local C against known major pivots.

    ``major_xs/major_ps/major_ats`` contain only pivots confirmed by decision
    bar ``i``.  C is passed separately so this helper cannot accidentally use a
    future or a later local pivot.  It applies the original V10 geometry and
    valley definitions, then returns a short-list ranked exactly by the
    original fit/wave score.
    """

    p = p or V104Params()
    lp = local_params or LocalTouchParams()
    if len(major_xs) != len(major_ps) or len(major_xs) != len(major_ats):
        raise ValueError("major pivot arrays must have equal length")
    if not (0 <= cx <= i) or not (math.isfinite(cp) and math.isfinite(ca) and ca > 0):
        return []
    if not math.isfinite(close_i):
        return []

    bag: list[Line] = []
    n = len(major_xs)
    for ai in range(n - 1):
        ax, ap, aa = int(major_xs[ai]), float(major_ps[ai]), float(major_ats[ai])
        if not (math.isfinite(ap) and math.isfinite(aa) and aa > 0):
            continue
        if ax >= cx or cx - ax < p.span:
            continue
        for bi in range(ai + 1, n):
            bx, bp, ba = int(major_xs[bi]), float(major_ps[bi]), float(major_ats[bi])
            if not (ax < bx < cx and math.isfinite(bp) and math.isfinite(ba) and ba > 0):
                continue
            if bx - ax < p.gap or cx - bx < lp.gap:
                continue
            # This is the explicit V12 chronology rule.  The major B need not
            # have been known at C, but it must be known by this decision bar.
            if bx + p.right > i:
                continue
            if not (ap > bp > cp and ap - bp >= p.drop * max(aa, ba)):
                continue
            if bx - ax <= 0:
                continue

            at_c = ap + (bp - ap) * ((cx - ax) / float(bx - ax))
            at_now = ap + (bp - ap) * ((i - ax) / float(bx - ax))
            fit = abs(cp - at_c) / max(float(ca), tick)
            if fit > p.touch or at_now <= 0 or close_i > at_now:
                continue

            ab_valley = _finite_min(low[ax + 1 : bx])
            bc_valley = _finite_min(low[bx + 1 : cx])
            if ab_valley is None or bc_valley is None:
                continue
            # The A-B numerator measures the valley-to-B rebound.  When the
            # switch is enabled, scale that wave by B's ATR so a large shock
            # ATR at A cannot dilute a valid rebound.  The pullback threshold
            # itself remains the original 2 ATR; the disabled path preserves
            # the pre-switch denominator for audit comparisons.  B-C keeps
            # its original B/C scale.
            ab_scale = max(ba, tick) if lp.rebound_at_b else max(aa, ba, tick)
            ab = (min(ap, bp) - ab_valley) / ab_scale
            bc = (min(bp, cp) - bc_valley) / max(ba, ca, tick)
            if ab < p.pullback or bc < p.pullback:
                continue

            item = Line(ax, ap, bx, bp, cx, cp, float(fit), i, int(source))
            item.wave = min(float(ab), float(bc))
            item.score = item.fit + 0.20 / (1.0 + item.wave)
            # Pine builds the same four-per-span shortlist as the original
            # family; the auxiliary pool applies the tighter two-slot cap at
            # storage time below.
            _rank_local(bag, item, p, p.per_bucket)
    return bag


# Public spelling for callers that do not want to rely on a private helper.
search_local = _local_search
_search_local = _local_search


def _store_local(
    pool: list[Line],
    item: Line,
    *,
    i: int,
    high: np.ndarray,
    close: np.ndarray,
    body: np.ndarray,
    atr: np.ndarray,
    tick: float,
    p: V104Params,
    pool_cap: int,
    evicted: list[Line] | None = None,
) -> int:
    """Store a candidate with original dedup/validation and a local cap.

    Return codes match ``spike_v10_4._store``: 1 stored, 0 duplicate, 2 body,
    3 needle, 4 prior break, 5 no capacity.
    """

    for known in pool:
        if _same(known, item, i, atr, tick, p):
            return 0
    reason, spikes = _validate(item, i, high, close, body, atr, tick, p)
    if reason != 0:
        return reason + 1
    item.score += 0.05 * spikes
    bucket = _bucket(item.cx - item.ax, p)
    members = [known for known in pool if known.source == item.source and _bucket(known.cx - known.ax, p) == bucket]
    if len(members) < pool_cap:
        pool.append(item)
        return 1
    worst = max(members, key=lambda known: known.score)
    if item.score < worst.score:
        if evicted is not None:
            evicted.append(worst)
        pool[pool.index(worst)] = item
        return 1
    return 5


def _as_float_array(value: Any, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    return arr


def auxiliary_touch_events(
    open_,
    high,
    low,
    close,
    atr,
    *,
    can_run=None,
    gap=None,
    tick: float = 1e-12,
    params: LocalTouchParams | None = None,
    local_params: LocalTouchParams | None = None,
    major_params: V104Params | None = None,
    v104_params: V104Params | None = None,
    trace: dict[str, Any] | None = None,
) -> LocalTouchResult:
    """Replay V12 auxiliary local-touch lines causally, bar by bar.

    ``major_params`` is read-only configuration for the original V10.4
    geometry.  ``v104_params`` is accepted as a descriptive alias; callers
    must not pass both.  When the local family is disabled the result has
    correctly sized all-false event arrays and empty line/event/trace lists.
    """

    o = _as_float_array(open_, "open")
    h = _as_float_array(high, "high")
    lo = _as_float_array(low, "low")
    c = _as_float_array(close, "close")
    a = _as_float_array(atr, "atr")
    n = len(c)
    if any(len(x) != n for x in (o, h, lo, a)):
        raise ValueError("misaligned price arrays")
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")
    if params is not None and local_params is not None:
        raise ValueError("pass only one of params and local_params")
    if major_params is not None and v104_params is not None:
        raise ValueError("pass only one of major_params and v104_params")
    p = major_params or v104_params or V104Params()
    lp = params or local_params or LocalTouchParams()
    tr = _init_trace(trace, n)
    born_event = np.zeros(n, dtype=bool)
    break_event = np.zeros(n, dtype=bool)
    codes = {k: 0 for k in range(6)}
    if not lp.enabled:
        return LocalTouchResult(born_event, break_event, [], [], tr, codes)

    if can_run is None:
        can = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c) & np.isfinite(a) & (a > 0)
    else:
        can = np.asarray(can_run, dtype=bool)
        if can.ndim != 1 or len(can) != n:
            raise ValueError("can_run must align with price arrays")
        can = can.copy()
    if gap is not None:
        gap_arr = np.asarray(gap, dtype=bool)
        if gap_arr.ndim != 1 or len(gap_arr) != n:
            raise ValueError("gap must align with price arrays")
        can &= ~gap_arr

    body = np.maximum(o, c)
    soft = soft_peak(o, h, c, a, p.wick_cap)
    source_prices = (h, soft)
    major_of = tuple(pivots(price, p.left, p.right, p.pivot_ties)[0] for price in source_prices)
    local_of = tuple(pivots(price, lp.local_left, lp.local_right, p.pivot_ties)[0] for price in source_prices)

    major_xs = [[], []]
    major_ps = [[], []]
    major_ats = [[], []]
    local_seen: list[list[int]] = [[], []]
    pool: list[Line] = []
    all_lines: list[Line] = []
    events: list[dict[str, Any]] = []
    serial = 0
    segment_start = 0

    def finish_line(item: Line, event_i: int, event: str, **extra: Any) -> None:
        record = {"uid": int(item.uid), "i": int(event_i), "kind": KIND, "kind_id": 1,
                  "track": _track(item.source), "track_id": int(item.source),
                  "source": int(item.source), "event": event}
        record.update(extra)
        tr["line_events"].append(record)

    for i in range(n):
        if not bool(can[i]):
            if pool:
                for item in pool:
                    item.phase, item.stopped, item.spike = 3, i, -1
                    finish_line(item, i, "cleared_data_gap")
                pool.clear()
            for xs, ps, ats in zip(major_xs, major_ps, major_ats):
                xs.clear(); ps.clear(); ats.clear()
            for seen in local_seen:
                seen.clear()
            # Match the Pine engine's segment marker: the gap bar itself is
            # the segment boundary, while local/major admission applies the
            # respective left-window check below.
            segment_start = i
            tr["gaps"].append(int(i))
            continue

        close_i = float(c[i])
        atr_i = max(float(a[i]), tick)
        break_candidates: list[Line] = []
        for item in list(pool):
            y = item.at(i)
            if item.phase != 1:
                continue
            if i - item.born > p.life or not math.isfinite(y) or y <= 0:
                item.phase, item.stopped, item.spike = 3, i, -1
                finish_line(item, i, "expired")
                continue
            # A candidate is born after this bar's close.  Consequently this
            # branch intentionally starts at i > born and cannot break on birth.
            if i > item.born:
                if close_i > y + atr_i * p.break_buffer:
                    item.above += 1
                else:
                    item.above = 0
                if item.above >= p.break_bars:
                    item.phase, item.stopped, item.broken, item.usable = 2, i, i, True
                    break_candidates.append(item)
                    finish_line(item, i, "broke", A=int(item.ax), B=int(item.bx), C=int(item.cx),
                                born_i=int(item.born), break_i=int(i))

        # The event stream is one event per bar.  If several tracks break on a
        # close, retain the original score ordering for a deterministic winner.
        if break_candidates:
            winner = min(break_candidates, key=lambda item: (item.score, item.uid))
            break_event[i] = True
            for line_record in tr["lines"]:
                if line_record["uid"] in {int(item.uid) for item in break_candidates}:
                    line_record["break_i"] = int(i)
            event = _line_payload(winner, break_i=i)
            event.update({"event": "break", "i": int(i), "A": int(winner.ax), "B": int(winner.bx),
                          "C": int(winner.cx), "born_i": int(winner.born), "break_i": int(i)})
            events.append(event)
            tr["events"].append(event)

        # Broken/expired lines no longer consume capacity.  They remain in
        # all_lines and trace for historical audit.
        pool[:] = [item for item in pool if item.phase == 1]

        # Admit major pivots before local C searches on this decision bar.  A
        # major B confirmed exactly on i is therefore known in time when
        # B+right <= i, while a later confirmation cannot backfill old C.
        for source in (0, 1):
            px = int(major_of[source][i])
            # The major pivot's complete left comparison window must also be
            # inside the current segment, matching the Pine engine.
            if px - p.left >= segment_start and px >= p.left and math.isfinite(a[px]):
                major_xs[source].append(px)
                major_ps[source].append(float(source_prices[source][px]))
                major_ats[source].append(max(float(a[px]), tick))
                tr["pivot_events"].append({"i": int(i), "pivot_i": px, "kind": "major",
                                            "track": _track(source), "source": source})
            while major_xs[source] and (i - major_xs[source][0] > p.lookback or len(major_xs[source]) > p.pivots_cap):
                major_xs[source].pop(0); major_ps[source].pop(0); major_ats[source].pop(0)

        for source in (0, 1):
            cx = int(local_of[source][i])
            # The whole local window must belong to the current segment.  A
            # C immediately after a gap cannot borrow its left neighbours.
            if cx - lp.local_left < segment_start or cx < lp.local_left or not math.isfinite(a[cx]):
                continue
            if cx in local_seen[source]:
                continue
            local_seen[source].append(cx)
            tr["pivot_events"].append({"i": int(i), "pivot_i": cx, "kind": "local",
                                        "track": _track(source), "source": source})
            candidates = _local_search(
                major_xs[source], major_ps[source], major_ats[source],
                cx=cx, cp=float(source_prices[source][cx]), ca=max(float(a[cx]), tick),
                source=source, i=i, low=lo, close_i=close_i, atr=a, tick=tick,
                p=p, local_params=lp,
            )
            evicted: list[Line] = []
            for item in candidates:
                code = _store_local(
                    pool, item, i=i, high=h, close=c, body=body, atr=a, tick=tick,
                    p=p, pool_cap=2, evicted=evicted,
                )
                codes[code] += 1
                if code != 1:
                    continue
                serial += 1
                item.uid = serial
                all_lines.append(item)
                born_event[i] = True
                payload = _line_payload(item)
                payload.update({"A": int(item.ax), "B": int(item.bx), "C": int(item.cx),
                                "born_i": int(i), "break_i": None})
                tr["lines"].append(payload)
                finish_line(item, i, "born", A=int(item.ax), B=int(item.bx), C=int(item.cx), born_i=int(i))
            for gone in evicted:
                gone.phase, gone.stopped = 3, i
                finish_line(gone, i, "evicted_for_capacity")

        tr["born_event"][i] = bool(born_event[i])
        tr["break_event"][i] = bool(break_event[i])
        tr["active_count"][i] = len(pool)

    return LocalTouchResult(born_event, break_event, all_lines, events, tr, codes)


def local_touch_events(*args, **kwargs) -> LocalTouchResult:
    """Alias for :func:`auxiliary_touch_events` used by research callers."""

    return auxiliary_touch_events(*args, **kwargs)


def v12_local_touch_events(*args, **kwargs) -> LocalTouchResult:
    """Versioned alias for :func:`auxiliary_touch_events`."""

    return auxiliary_touch_events(*args, **kwargs)


__all__ = [
    "KIND",
    "LocalTouchParams",
    "LocalTouchResult",
    "V12LocalTouchParams",
    "VERSION",
    "_local_search",
    "_search_local",
    "auxiliary_touch_events",
    "local_touch_events",
    "search_local",
    "v12_local_touch_events",
]
