"""Small semantic oracle for SPIKE V12 break/V9 pairing.

This module has no market-data or Pine-runtime dependency.  It models only the
ordered state machine needed to audit the V12 request: an already confirmed
line breaks, the close remains above that frozen line, and a later V9 event may
join it.  It deliberately documents semantics rather than claiming full Pine
runtime parity.

``BreakSpec.visible_bar`` is the first chart decision bar on which a break is
known.  It equals ``break_i`` for chart lines and is later for an HTF line.
``PairBar.above`` supplies the causal close-vs-line result for lines that were
already known on that bar.  Missing entries mean the close is above the line;
tests can therefore state only the touch/cross bars that matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "spike-v12-pairing-reference-20260920-v1"


@dataclass(frozen=True)
class PairingParams:
    """Bounded controls for the semantic state machine.

    ``mode='box'`` models the V11.2 multi-bar V9 box.  A break while a box is
    already active joins immediately (the original box-first path).  With
    ``hold_pair=True``, a break before a new box may join a later V9 only while
    every intervening close stays above the line.

    ``mode='window'`` models the legacy six-bar event window.  It is useful as
    a negative control for a V9 arriving after that window.
    """

    mode: str = "box"
    hold_pair: bool = True
    window_bars: int = 6
    life_bars: int = 600
    max_held: int = 24
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"box", "window"}:
            raise ValueError("mode must be 'box' or 'window'")
        if self.window_bars < 0 or self.life_bars < 0 or self.max_held < 1:
            raise ValueError("window/life must be non-negative and max_held positive")


V12PairingParams = PairingParams


@dataclass(frozen=True)
class BreakSpec:
    """One already confirmed line break and its first visible decision bar."""

    line_id: str
    break_i: int
    source: str = "chart"
    visible_bar: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.line_id:
            raise ValueError("line_id must be non-empty")
        if self.source not in {"chart", "htf"}:
            raise ValueError("source must be 'chart' or 'htf'")
        visible = self.break_i if self.visible_bar is None else self.visible_bar
        if visible < self.break_i:
            raise ValueError("visible_bar cannot precede break_i")
        if self.source == "chart" and visible != self.break_i:
            raise ValueError("chart breaks are visible on their break bar")

    @property
    def visible_i(self) -> int:
        return self.break_i if self.visible_bar is None else self.visible_bar


@dataclass(frozen=True)
class PairBar:
    """One closed chart decision bar supplied to :func:`replay_pairing`.

    ``box_id`` identifies the active V9 long frame.  Set ``v9=True`` on the
    bar that opens a new frame; a frame may remain active on later bars by
    repeating the same id.  A gap is an explicit causal reset.
    """

    i: int
    v9: bool = False
    box_id: Optional[str] = None
    above: Mapping[str, bool] = field(default_factory=dict)
    gap: bool = False


@dataclass(frozen=True)
class PairEvent:
    """One accepted joint event with its causal line provenance."""

    line_id: str
    source: str
    break_i: int
    visible_bar: int
    joint_i: int
    box_id: Optional[str]
    order: str


@dataclass
class PairingResult:
    """Events and a compact audit trace from one bounded replay."""

    events: List[PairEvent]
    trace: List[Dict[str, object]]
    remaining_held: List[str]

    @property
    def joint_bars(self) -> List[int]:
        return [event.joint_i for event in self.events]


@dataclass
class _Held:
    spec: BreakSpec
    anchor_i: int


def _normalise_specs(specs: Iterable[BreakSpec], source: str) -> List[BreakSpec]:
    out = list(specs)
    if any(item.source != source for item in out):
        raise ValueError("break source does not match its input stream")
    out.sort(key=lambda item: (item.visible_i, item.break_i, item.line_id))
    return out


def _emit(
    events: List[PairEvent],
    spec: BreakSpec,
    bar_i: int,
    box_id: Optional[str],
    order: str,
) -> PairEvent:
    event = PairEvent(
        line_id=spec.line_id,
        source=spec.source,
        break_i=spec.break_i,
        visible_bar=spec.visible_i,
        joint_i=bar_i,
        box_id=box_id,
        order=order,
    )
    events.append(event)
    return event


def _choose_newest(items: Sequence[_Held]) -> _Held:
    return max(items, key=lambda item: (item.anchor_i, item.spec.visible_i, item.spec.line_id))


def _choose_oldest(items: Sequence[_Held]) -> _Held:
    return min(items, key=lambda item: (item.anchor_i, item.spec.visible_i, item.spec.line_id))


def replay_pairing(
    bars: Sequence[PairBar],
    *,
    chart_breaks: Iterable[BreakSpec] = (),
    htf_breaks: Iterable[BreakSpec] = (),
    params: Optional[PairingParams] = None,
) -> PairingResult:
    """Replay ordered break/V9 state without reading future bars.

    A break is admitted only on its ``visible_i``.  A break-first pair is
    accepted only on a later ``v9`` bar, after the line survived each supplied
    close.  A touch/cross, explicit gap, or life expiry removes it permanently.
    In box mode a break observed while ``box_id`` is active follows the
    original box-first path and does not need a held state.
    """

    p = params or PairingParams()
    ordered = list(bars)
    if any(ordered[i].i >= ordered[i + 1].i for i in range(len(ordered) - 1)):
        raise ValueError("bars must have strictly increasing i")
    chart = _normalise_specs(chart_breaks, "chart")
    htf = _normalise_specs(htf_breaks, "htf")
    specs = chart + htf
    if len({item.line_id for item in specs}) != len(specs):
        raise ValueError("line_id must be unique across break streams")

    events: List[PairEvent] = []
    trace: List[Dict[str, object]] = []
    if not p.enabled:
        return PairingResult(events, [{"i": bar.i, "disabled": True} for bar in ordered], [])

    by_visible: Dict[int, List[BreakSpec]] = {}
    for spec in specs:
        by_visible.setdefault(spec.visible_i, []).append(spec)

    held: Dict[str, _Held] = {}
    used_boxes: set[str] = set()
    pending_v9: List[Tuple[int, Optional[str]]] = []
    seen_lines: set[str] = set()

    for bar in ordered:
        canceled: List[Dict[str, object]] = []
        admitted: List[str] = []
        joined: Optional[PairEvent] = None

        if bar.gap:
            for line_id, state in list(held.items()):
                canceled.append({"line_id": line_id, "reason": "gap"})
                del held[line_id]
            pending_v9.clear()
            trace.append({"i": bar.i, "held": [], "admitted": [], "canceled": canceled, "joint": None, "gap": True})
            continue

        # A held line is judged before the current V9 event.  Thus a close
        # touching the frozen line on this bar permanently defeats the pair.
        for line_id, state in list(held.items()):
            if bar.i - state.anchor_i > p.life_bars:
                canceled.append({"line_id": line_id, "reason": "expiry"})
            elif not bar.above.get(line_id, True):
                canceled.append({"line_id": line_id, "reason": "touch_or_cross"})
            if line_id in {row["line_id"] for row in canceled}:
                del held[line_id]

        current = by_visible.get(bar.i, [])
        for spec in current:
            if spec.line_id in seen_lines:
                continue
            seen_lines.add(spec.line_id)
            admitted.append(spec.line_id)

        if p.mode == "window":
            # Legacy pairing consumes a break only when the second event is
            # inside the bounded event window.  Above-line state still guards
            # against pairing after an intervening reclaim.
            for old in list(held.values()):
                if bar.i - old.anchor_i > p.window_bars:
                    del held[old.spec.line_id]
            pending_v9[:] = [
                entry for entry in pending_v9 if bar.i - entry[0] <= p.window_bars
            ]
            if bar.v9:
                pending_v9.append((bar.i, bar.box_id))
            for spec in current:
                held[spec.line_id] = _Held(spec, spec.visible_i)
            if pending_v9 and held:
                candidates = [
                    state
                    for state in held.values()
                    if any(abs(v9_i - state.anchor_i) <= p.window_bars for v9_i, _ in pending_v9)
                ]
                if candidates:
                    chosen = _choose_newest(candidates)
                    v9_i, box_id = max(
                        (entry for entry in pending_v9 if abs(entry[0] - chosen.anchor_i) <= p.window_bars),
                        key=lambda entry: entry[0],
                    )
                    order = "samebar" if v9_i == chosen.spec.visible_i else ("break-first" if chosen.spec.visible_i < v9_i else "v9-first")
                    joined = _emit(events, chosen.spec, bar.i, box_id, order)
                    held.clear()
                    pending_v9.clear()
        else:
            active_box = bar.box_id is not None
            if active_box and current and bar.box_id not in used_boxes:
                # Original V11.2 box-first behavior: the current break wins;
                # every parallel held opportunity is consumed by this frame.
                chosen_spec = max(current, key=lambda spec: (spec.source == "htf", spec.visible_i, spec.line_id))
                joined = _emit(events, chosen_spec, bar.i, bar.box_id, "box-first")
                used_boxes.add(str(bar.box_id))
                held.clear()
            else:
                for spec in current:
                    if p.hold_pair:
                        if len(held) >= p.max_held:
                            oldest = _choose_oldest(list(held.values()))
                            del held[oldest.spec.line_id]
                        held[spec.line_id] = _Held(spec, spec.visible_i)

                if p.hold_pair and bar.v9 and active_box and str(bar.box_id) not in used_boxes:
                    candidates = list(held.values())
                    if candidates:
                        chart_best = [item for item in candidates if item.spec.source == "chart"]
                        htf_best = [item for item in candidates if item.spec.source == "htf"]
                        best_chart = _choose_newest(chart_best) if chart_best else None
                        best_htf = _choose_newest(htf_best) if htf_best else None
                        if best_htf is not None and (best_chart is None or best_htf.spec.visible_i >= best_chart.spec.break_i):
                            chosen = best_htf
                        elif best_chart is not None:
                            chosen = best_chart
                        else:
                            chosen = None
                        if chosen is not None and chosen.spec.visible_i < bar.i:
                            joined = _emit(events, chosen.spec, bar.i, bar.box_id, "break-first")
                            used_boxes.add(str(bar.box_id))
                            held.clear()

        trace.append(
            {
                "i": bar.i,
                "held": sorted(held),
                "admitted": admitted,
                "canceled": canceled,
                "joint": None if joined is None else joined.line_id,
                "gap": False,
            }
        )

    return PairingResult(events, trace, sorted(held))


pair_breaks = replay_pairing
v12_pairing_events = replay_pairing


__all__ = [
    "BreakSpec",
    "PairBar",
    "PairEvent",
    "PairingParams",
    "PairingResult",
    "VERSION",
    "V12PairingParams",
    "pair_breaks",
    "replay_pairing",
    "v12_pairing_events",
]
