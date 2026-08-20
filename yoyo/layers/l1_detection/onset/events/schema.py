"""Pattern Event v2 — one event, six time anchors instead of one signal_i.

Pattern Library v1 gave every event a single `signal_i`, derived from the right
edge of owner's box. That number was made to carry meanings it cannot hold at
once: where the formation began, when it first became recognisable without
looking ahead, when it was confirmed, and when price left. Formation v1 then
predicted "K bars before the box edge", which is not the same question as "the
earliest causally identifiable point".

So an event now carries:

    box_start_i           owner's box, left edge      (mechanical, from v1)
    box_end_i             owner's box, right edge     (mechanical, from v1)
    formation_start_i     structure begins to compress
    causal_onset_i        FIRST bar at which owner, seeing nothing after it,
                          would say "this is the pattern"    <- the target
    formation_confirm_i   complete without needing later bars
    launch_i              price/MAs visibly leave

Only the two box_* anchors can be derived from v1 data. The other four require a
human looking at bars one at a time with the future hidden, and stay null until
that happens. Filling them by rule is forbidden: on 2026-07-23 rule-written
prelabels produced a "51.5% mis-fire" verdict that killed v16, and owner found
the labels wrong, not the model.

v3 adds one field: `side`.

An event says where the six lines converged, and said nothing about which way
price then left. That omission is not cosmetic. Of the 619 golden_pool boxes
that carry owner's direction call, 287 are long. Scored as shorts -- which is
what gold_hindsight.csv does to all of them -- the short-side boxes return
+273.9bp net at an 83.3% hit rate while the long-side boxes return -182.6bp at
0/24. Pooling them reported 141.3bp / 58.9% and hid both.

It also poisons feature work: price-relative-to-cluster separates short boxes
from long boxes at AUC 0.988, so any model trained on a pooled set learns
direction and any single-feature study on a pooled set measures it. A/B grades
are themselves skewed (A is 74% short, B is 37%), so grade alone leaks it too.

`side` is a human answer, like the anchors. It may be backfilled from owner's
own direction review, but never inferred from geometry -- an inferred side would
reproduce the 0.988 by construction and prove nothing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = 3

StageLabel = Literal["A", "B", "C", "not_a_pattern"]
EventValidity = Literal["valid", "invalid", "uncertain", "unreviewed"]
Side = Literal["short", "long"]

#: Where a non-null `side` is allowed to have come from. Geometry is absent by
#: design: see the module docstring.
SIDE_SOURCES = ("owner_side_review", "causal_onset_review", "owner_direct")

ANCHOR_FIELDS = (
    "formation_start_i",
    "causal_onset_i",
    "formation_confirm_i",
    "launch_i",
)

#: Anchors that must be non-decreasing when present. launch_i sits outside this
#: chain: it usually follows confirm, but a real exception must surface in the
#: audit rather than be clamped into order.
ORDERED_ANCHORS = (
    "box_start_i",
    "formation_start_i",
    "causal_onset_i",
    "formation_confirm_i",
)


@dataclass
class SourceWindow:
    start_i: int
    end_i: int
    bars: int
    available_at: str | None = None


@dataclass
class OriginalBox:
    xywhn: list[float] | None
    box_start_i: int | None
    box_end_i: int | None


@dataclass
class Anchors:
    formation_start_i: int | None = None
    causal_onset_i: int | None = None
    formation_confirm_i: int | None = None
    launch_i: int | None = None


@dataclass
class Review:
    reviewer: str | None = None
    reviewed_at: str | None = None
    protocol_version: str | None = None
    session_id: str | None = None
    repeat_group: str | None = None
    confidence: int | None = None
    notes: str = ""


@dataclass
class Provenance:
    pattern_library_sha256: str | None = None
    source_ohlcv_sha256: str | None = None
    render_version: str | None = None
    code_commit: str | None = None


@dataclass
class PatternEvent:
    event_id: str
    source_pattern_id: str
    source: str
    symbol: str
    timeframe: str
    source_window: SourceWindow
    original_box: OriginalBox
    anchors: Anchors = field(default_factory=Anchors)
    anchors_time: dict[str, str | None] = field(default_factory=dict)
    quality_label: StageLabel | None = None
    side: Side | None = None
    side_source: str | None = None
    event_validity: EventValidity = "unreviewed"
    review: Review = field(default_factory=Review)
    provenance: Provenance = field(default_factory=Provenance)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {"schema_version": d.pop("schema_version"), **d}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PatternEvent":
        return PatternEvent(
            event_id=d["event_id"],
            source_pattern_id=d["source_pattern_id"],
            source=d["source"],
            symbol=d["symbol"],
            timeframe=d["timeframe"],
            source_window=SourceWindow(**d["source_window"]),
            original_box=OriginalBox(**d["original_box"]),
            anchors=Anchors(**d.get("anchors", {})),
            anchors_time=d.get("anchors_time", {}),
            quality_label=d.get("quality_label"),
            side=d.get("side"),
            side_source=d.get("side_source"),
            event_validity=d.get("event_validity", "unreviewed"),
            review=Review(**d.get("review", {})),
            provenance=Provenance(**d.get("provenance", {})),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )
