"""Bounded, causal research port of SPIKE V12.6 line and box pairing.

This is an evaluation reference for the delivered Pine source
``spike_burst_v12_6.pine``.  It ports the default *linear*, dual raw/soft
three-major-pivot family together with V12's major-A/B plus local-C family.
It deliberately does not claim Pine runtime parity: TradingView's pivot tie
rule remains unverified, so this module preserves the repository's explicit
``strict`` convention and records that boundary in ``trace``.

``line_events`` returns line events with this stable schema: ``i`` (event
bar), ``break_i``, ``born_i``, ``ax/ap/bx/bp/cx/cp``, ``source`` (``raw`` or
``soft``), ``kind`` (``three_major`` or ``local_touch``), ``uid`` and
``score``.  Chart geometry is in chart-bar indices.  Higher-timeframe callers
map its geometry to ``ax_t/bx_t/cx_t`` (numeric chart-open minutes) and add
``visible_i`` before calling ``pair_events``.

The two public functions read only data at or before their current replay bar.
They preserve V12.6 defaults: major 12/8 pivots, local 2/2 pivots, 2-ATR
valleys, 600-bar life, and two closes above a 0.20-ATR buffer.  No market data,
orders, exits, or training decisions are performed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping

import numpy as np

from yoyo.evaluation.spike_v10_4 import (
    Line,
    V104Params,
    _bucket,
    _same,
    _search,
    _validate,
    pivots,
    soft_peak,
)
from yoyo.evaluation.spike_v12_local_touch import LocalTouchParams, _local_search


VERSION = "spike-v12.6-engine-20260922-v1"
KIND_MAJOR = "three_major"
KIND_LOCAL = "local_touch"
_TRACK = {0: "raw", 1: "soft"}


@dataclass(frozen=True)
class V126Params:
    """Frozen V12.6 line defaults; geometry remains V10.4's published contract."""

    major: V104Params = field(default_factory=V104Params)
    local: LocalTouchParams = field(default_factory=LocalTouchParams)
    per_group: int = 2
    held_cap: int = 24

    def __post_init__(self) -> None:
        if self.per_group < 1 or self.held_cap < 1:
            raise ValueError("per_group and held_cap must be positive")


@dataclass
class V126LineResult:
    """Bounded line replay result; HTF ``events`` has only the best break per bar."""

    events: list[dict[str, Any]]
    winner_events: list[dict[str, Any]]
    born_event: np.ndarray
    break_event: np.ndarray
    lines: list[dict[str, Any]]
    trace: dict[str, Any]
    store_codes: dict[int, int]

    @property
    def born(self) -> bool:
        """Whether this replay admitted at least one new structure."""

        return bool(self.born_event.any())

    @property
    def store_counts(self) -> dict[int, int]:
        """Descriptive alias for Pine-compatible candidate-store result codes."""

        return self.store_codes


def _arrays(*values: Any) -> tuple[np.ndarray, ...]:
    out = tuple(np.asarray(value, dtype=float) for value in values)
    if any(value.ndim != 1 for value in out):
        raise ValueError("price arrays must be one-dimensional")
    if any(len(value) != len(out[0]) for value in out[1:]):
        raise ValueError("misaligned price arrays")
    return out


def _payload(item: Line, i: int, *, htf: bool) -> dict[str, Any]:
    """Freeze only geometry already known at the break or birth event."""

    return {
        "i": int(i), "break_i": int(i), "born_i": int(item.born),
        "ax": int(item.ax), "ap": float(item.ap), "bx": int(item.bx),
        "bp": float(item.bp), "cx": int(item.cx), "cp": float(item.cp),
        "source": _TRACK[int(item.source)], "source_id": int(item.source),
        "kind": KIND_LOCAL if getattr(item, "kind", 0) == 1 else KIND_MAJOR,
        "kind_id": int(getattr(item, "kind", 0)), "uid": int(item.uid),
        "score": float(item.score), "fit": float(item.fit), "wave": float(item.wave),
        "htf": bool(htf),
    }


def line_events(open_, high, low, close, atr, *, can_run, gap=None, tick: float,
                htf: bool = False, confirmed_long=None, parent_high=None,
                parent_low=None, raw_side=None, long_alive=None, ref_long_exit=None,
                params: V126Params | V104Params | None = None) -> V126LineResult:
    """Replay merged V12.6 line families without future data.

    Chart mode retains every break in ``events`` and maintains the V9 evidence
    fields used by Pine; its raw, ungated ``raw_side`` cancels usable breaks.
    HTF mode removes broken lines at once and exposes only the lowest-score
    same-bar winner in ``events``/``winner_events``.
    """

    o, h, lo, c, a = _arrays(open_, high, low, close, atr)
    n = len(c)
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")
    cfg = V126Params(major=params) if isinstance(params, V104Params) else (params or V126Params())
    p, lp = cfg.major, cfg.local
    can = np.asarray(can_run, dtype=bool)
    if can.ndim != 1 or len(can) != n:
        raise ValueError("can_run must align with prices")
    # Pine's v10CanRun includes valid prices and a positive *raw* ATR.  Keep
    # that contract even when a caller's preliminary mask was too permissive.
    can = can & np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c) & np.isfinite(a) & (a > 0)
    if gap is not None:
        gap_arr = np.asarray(gap, dtype=bool)
        if gap_arr.ndim != 1 or len(gap_arr) != n:
            raise ValueError("gap must align with prices")
        can = can & ~gap_arr

    def facts(value: Any, default: Any, dtype: Any) -> np.ndarray:
        if value is None:
            return np.full(n, default, dtype=dtype)
        out = np.asarray(value, dtype=dtype)
        if out.ndim != 1 or len(out) != n:
            raise ValueError("V9 facts must align with prices")
        return out

    confirmed = facts(confirmed_long, False, bool)
    ph = facts(parent_high, np.nan, float)
    pl = facts(parent_low, np.nan, float)
    side = facts(raw_side, 0, np.int64)
    alive = facts(long_alive, True, bool)
    ref_exit = facts(ref_long_exit, False, bool)
    body = np.maximum(o, c)
    sources = (h, soft_peak(o, h, c, a, p.wick_cap))
    major_of = [pivots(values, p.left, p.right, p.pivot_ties)[0] for values in sources]
    local_of = [pivots(values, lp.local_left, lp.local_right, p.pivot_ties)[0] for values in sources]
    pivot_meta = [pivots(values, p.left, p.right, p.pivot_ties)[1:] + pivots(values, lp.local_left, lp.local_right, p.pivot_ties)[1:]
                  for values in sources]

    major_xs: list[list[int]] = [[], []]; major_ps: list[list[float]] = [[], []]; major_ats: list[list[float]] = [[], []]
    pool: list[Line] = []
    kinds: dict[int, int] = {}
    line_records: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []; winners: list[dict[str, Any]] = []
    born = np.zeros(n, bool); broken = np.zeros(n, bool)
    codes = {i: 0 for i in range(6)}
    trace: dict[str, Any] = {
        "version": VERSION, "pivot_ties": p.pivot_ties,
        "pivot_tie_status": "unverified_tradingview_native_semantics",
        "pivot_tie_counts": {"raw": {"major_loose_only": pivot_meta[0][0], "major_one_sided_extra": pivot_meta[0][1], "local_loose_only": pivot_meta[0][2], "local_one_sided_extra": pivot_meta[0][3]},
                             "soft": {"major_loose_only": pivot_meta[1][0], "major_one_sided_extra": pivot_meta[1][1], "local_loose_only": pivot_meta[1][2], "local_one_sided_extra": pivot_meta[1][3]}},
        "snapshots": [], "line_events": [], "gaps": [], "active_count": np.zeros(n, dtype=np.int64),
    }
    serial = 0; segment_start = 0

    for i in range(n):
        if not can[i]:
            pool.clear()
            for xs, ps, ats in zip(major_xs, major_ps, major_ats):
                xs.clear(); ps.clear(); ats.clear()
            segment_start = i; trace["gaps"].append(i); trace["snapshots"].append({"i": i, "atr": None, "gap": True})
            continue
        atr_i = float(a[i])
        trace["snapshots"].append({"i": i, "atr": float(a[i]), "gap": False})
        newly_broken: list[Line] = []

        # Monitor before admitting pivots/candidates. A line cannot break on birth.
        for item in pool:
            y = item.at(i)
            if item.phase == 1:
                if i - item.born > p.life or not math.isfinite(y) or y <= 0:
                    item.phase, item.stopped, item.spike = 3, i, -1
                    trace["line_events"].append({"i": i, "uid": item.uid, "event": "expired"})
                elif i > item.born:
                    item.above = item.above + 1 if c[i] > y + atr_i * p.break_buffer else 0
                    if item.above >= p.break_bars:
                        item.phase, item.stopped, item.broken, item.usable = 2, i, i, not htf
                        newly_broken.append(item)
            if not htf and item.spike >= 0:
                if i - item.spike > p.window or not alive[i] or not math.isfinite(item.parent_low) or c[i] < item.parent_low or ref_exit[i]:
                    item.spike = -1
            if not htf and item.usable and (i - item.broken > p.window or c[i] <= y or side[i] == -1):
                item.usable, item.spike = False, -1
            if not htf and item.born < i and not item.joined and (item.phase == 1 or item.usable) and confirmed[i]:
                item.spike, item.parent_high, item.parent_low = i, ph[i], pl[i]

        if newly_broken:
            broken[i] = True
            payloads = [_payload_sidecar(item, i, htf, kinds) for item in newly_broken]
            if htf:
                winner = min(payloads, key=lambda event: event["score"])
                all_events.append(winner); winners.append(winner)
            else:
                all_events.extend(payloads)
                winner = min(payloads, key=lambda event: event["score"])
                winners.append(winner)
            trace["line_events"].extend({"i": i, "uid": item.uid, "event": "break"} for item in newly_broken)

        # Major pivots are admitted first and local candidates second, exactly
        # preserving the Pine ordering at a common confirmation bar.
        major_new = [False, False]
        px = i - p.right
        if px >= 0 and px - p.left >= segment_start and math.isfinite(a[px]):
            for source in range(2):
                pivot_i = int(major_of[source][i])
                if pivot_i >= 0:
                    major_xs[source].append(pivot_i); major_ps[source].append(float(sources[source][pivot_i])); major_ats[source].append(max(float(a[pivot_i]), tick)); major_new[source] = True
        for source in range(2):
            while major_xs[source] and (i - major_xs[source][0] > p.lookback or len(major_xs[source]) > p.pivots_cap):
                major_xs[source].pop(0); major_ps[source].pop(0); major_ats[source].pop(0)
        pool[:] = [item for item in pool if item.phase == 1 or (not htf and item.usable)]

        # ``Line`` is slotted in the older port.  Keep V12's extra family field
        # in this replay-local sidecar instead of changing the shared contract.
        def store_candidate(item: Line, kind: int) -> None:
            nonlocal serial
            # The sidecar preserves family metadata without changing Line.
            kinds[id(item)] = kind
            code = _store_merged_sidecar(pool, item, kinds, i, h, c, body, a, tick, p, cfg.per_group)
            codes[code] += 1
            if code == 1:
                serial += 1; item.uid = serial; born[i] = True
                record = _payload_sidecar(item, i, htf, kinds); record["break_i"] = None
                line_records.append(record); trace["line_events"].append({"i": i, "uid": item.uid, "event": "born"})

        for source in range(2):
            if major_new[source]:
                for item in _search(major_xs[source], major_ps[source], major_ats[source], source, i, lo, float(c[i]), a, tick, p):
                    store_candidate(item, 0)
        if lp.enabled:
            touch_i = i - lp.local_right
            if touch_i >= 0 and touch_i - lp.local_left >= segment_start and math.isfinite(a[touch_i]):
                for source in range(2):
                    local_i = int(local_of[source][i])
                    if local_i >= 0:
                        for item in _local_search(major_xs[source], major_ps[source], major_ats[source], cx=local_i, cp=float(sources[source][local_i]), ca=max(float(a[local_i]), tick), source=source, i=i, low=lo, close_i=float(c[i]), atr=a, tick=tick, p=p, local_params=lp):
                            store_candidate(item, 1)
        trace["active_count"][i] = len(pool)

    # Sidecar records retain the kind even when broken lines have left an HTF pool.
    uid_kind = {record["uid"]: 1 if record["kind"] == KIND_LOCAL else 0 for record in line_records}
    for event in all_events + winners:
        event["kind_id"] = uid_kind.get(event["uid"], event.get("kind_id", 0))
        event["kind"] = KIND_LOCAL if event["kind_id"] else KIND_MAJOR
    return V126LineResult(all_events, winners, born, broken, line_records, trace, codes)


def _store_merged_sidecar(pool: list[Line], item: Line, kinds: dict[int, int], i: int,
                          high: np.ndarray, close: np.ndarray, body: np.ndarray,
                          atr: np.ndarray, tick: float, p: V104Params, per_group: int) -> int:
    for known in pool:
        if _same(known, item, i, atr, tick, p): return 0
    reason, spikes = _validate(item, i, high, close, body, atr, tick, p)
    if reason: return reason + 1
    item.score += .05 * spikes; bucket = _bucket(item.cx - item.ax, p); kind = kinds.setdefault(id(item), 0)
    members = [old for old in pool if kinds.get(id(old), 0) == kind and old.source == item.source and _bucket(old.cx - old.ax, p) == bucket]
    if len(members) < per_group: pool.append(item); return 1
    candidates = [old for old in members if not (old.usable or old.spike >= 0)]
    if not candidates: return 5
    worst = max(candidates, key=lambda old: old.score)
    if item.score < worst.score:
        # Pine uses array.remove followed by array.push.  Retaining that order
        # matters for deterministic later scans and score ties.
        pool.remove(worst); pool.append(item); return 1
    return 5


def _payload_sidecar(item: Line, i: int, htf: bool, kinds: Mapping[int, int]) -> dict[str, Any]:
    event = _payload(item, i, htf=htf); kind = kinds.get(id(item), 0)
    event["kind_id"], event["kind"] = kind, KIND_LOCAL if kind else KIND_MAJOR
    return event


def pair_events(close, *, bar_times, chart_breaks: Iterable[Mapping[str, Any]],
                htf_breaks: Iterable[Mapping[str, Any]], box_id, confirmed_long,
                gap=None, life: int = 600) -> list[dict[str, Any]]:
    """Port V12.6 ordered box-first / held-break pairing with frozen geometry.

    Chart break records use bar-index anchors. HTF records must use ``ax_t``,
    ``bx_t``, ``cx_t`` and ``visible_i`` supplied by the caller.  The function
    returns each accepted event with its frozen source geometry and never
    backfills a box: break-first requires a later ``confirmed_long`` bar.
    """
    c = np.asarray(close, float); times = np.asarray(bar_times, float); boxes = np.asarray(box_id)
    confirmed = np.asarray(confirmed_long, bool); n = len(c)
    if any(len(x) != n for x in (times, boxes, confirmed)): raise ValueError("pair arrays must align")
    gaps = np.zeros(n, bool) if gap is None else np.asarray(gap, bool)
    if gaps.ndim != 1 or len(gaps) != n or life < 0: raise ValueError("gap must align and life must be non-negative")
    records: list[dict[str, Any]] = []
    for source, incoming in (("chart", chart_breaks), ("htf", htf_breaks)):
        for raw in incoming:
            event = dict(raw); event["pair_source"] = source
            event["visible_i"] = int(event.get("visible_i", event["break_i"]))
            if not (0 <= event["visible_i"] < n): raise ValueError("visible_i outside chart replay")
            records.append(event)
    by_i: dict[int, list[dict[str, Any]]] = {}
    for event in records: by_i.setdefault(event["visible_i"], []).append(event)
    held_chart: list[dict[str, Any]] = []
    held_htf: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []; used: set[Any] = set()

    def above(event: Mapping[str, Any], i: int) -> bool:
        if event["pair_source"] == "htf":
            y = float(event["ap"]) + (float(event["bp"]) - float(event["ap"])) * ((times[i] - float(event["ax_t"])) / (float(event["bx_t"]) - float(event["ax_t"])))
        else:
            y = float(event["ap"]) + (float(event["bp"]) - float(event["ap"])) * ((i - float(event["ax"])) / (float(event["bx"]) - float(event["ax"])))
        return math.isfinite(y) and y > 0 and c[i] > y

    for i in range(n):
        if gaps[i]:
            held_chart.clear(); held_htf.clear(); continue
        held_chart[:] = [event for event in held_chart if i - int(event["break_i"]) <= life and above(event, i)]
        # HTF ``break_i`` belongs to the HTF index clock.  Its V12 held-line
        # lifetime starts at the mapped chart bar where that event is visible.
        held_htf[:] = [event for event in held_htf if i - int(event["visible_i"]) <= life and above(event, i)]
        current = by_i.get(i, [])
        active = boxes[i] != -1
        box = boxes[i].item() if hasattr(boxes[i], "item") else boxes[i]
        chosen: dict[str, Any] | None = None; order: str | None = None
        if active and box not in used and current:
            # HTF wins a simultaneous visible bar; each source already supplied
            # its best same-bar break from line_events.
            chosen = max(current, key=lambda event: (event["pair_source"] == "htf", event["score"] * -1))
            order = "box-first"
        else:
            chart_current = [event for event in current if event["pair_source"] == "chart"]
            # Pine checks the first chart close on which a HTF break becomes
            # visible before holding it.  A box already open on that same bar
            # follows the earlier box-first branch above and is intentionally
            # not filtered by this hold-only test.
            htf_current = [event for event in current if event["pair_source"] == "htf" and above(event, i)]
            held_chart.extend(chart_current); held_htf.extend(htf_current)
            while len(held_chart) > 24: held_chart.pop(0)
            while len(held_htf) > 24: held_htf.pop(0)
            if active and confirmed[i] and box not in used:
                candidates = [event for event in held_chart + held_htf if int(event["visible_i"]) < i]
                if candidates:
                    def anchor(event: Mapping[str, Any]) -> int:
                        return int(event["visible_i"] if event["pair_source"] == "htf" else event["break_i"])
                    newest = max(anchor(event) for event in candidates)
                    ties = [event for event in candidates if anchor(event) == newest]
                    chart = [event for event in ties if event["pair_source"] == "chart"]
                    htf = [event for event in ties if event["pair_source"] == "htf"]
                    chosen = (max(htf, key=lambda event: int(event["visible_i"])) if htf else min(chart, key=lambda event: event["score"]))
                    order = "break-first"
        if chosen is not None:
            frozen = dict(chosen); frozen.update({"joint_i": i, "source": chosen["pair_source"], "order": order, "box_entry_i": int(box)})
            result.append(frozen); used.add(box); held_chart.clear(); held_htf.clear()
    return result


__all__ = ["VERSION", "V126Params", "V126LineResult", "line_events", "pair_events"]
