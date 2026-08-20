"""The cross-layer pattern contract: one event, stated in time, with its warrant.

Two storage schemas arrived with the consolidation and both are kept, because
each is right about something:

  yoyo/datasets/gold_schema.py            owner's verdict at a decision bar --
                                          shape label, local window, core box,
                                          holdout seal
  yoyo/layers/l1_detection/onset/events/  one event's six anchors, its side and
  schema.py                               its provenance

What neither states, and what every layer boundary needs, is *time*: which bar
the decision was made on, and how much of the chart the labeller could see when
they made it. Indices are only meaningful inside the window that produced them,
so an index-based record cannot be checked for lookahead once it leaves the
builder that made it.

This module is that statement, and deliberately not a third schema. It has no
storage format of its own; `from_gold_row` and `from_pattern_event` convert the
two that exist, so the semantics converge here instead of diverging into a
third.

Five rules, each of which this project has already paid for:

1. ``visible_end_at <= decision_at``. The reviewer may not have seen past the
   bar they judged. 499 starred labels were checked once and only 2 were drawn
   at the live edge, median 97 bars of visible future
   (docs/learnings/zero-live-edge-labels-means-the-target-is-unverified.md).

2. Every causal anchor sits inside the window. An anchor outside the window it
   was judged in is not an anchor, it is an index from somewhere else.

3. ``pattern_valid`` may not be justified by outcome. "It went down afterwards"
   is not evidence that the shape was there. CLAUDE.md is explicit: do not judge
   a core box positive because price later moved.

4. ``causal_onset_i`` needs a stated human origin, and box geometry is not one
   of the allowed values. Seeding onset from a box's right edge would fill
   thousands of rows instantly and bake in the very semantics the causal-onset
   work exists to undo -- the right edge marks where the owner drew *having seen
   what came next*.

5. A proposal is not gold. ``training_eligible`` requires ``label_origin`` to be
   a human one. Rules and models make proposals; owner confirming a class
   protocol is not owner confirming every sample
   (docs/learnings/protocol-confirmation-is-not-sample-confirmation.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

#: Who produced the label. Only the first two are human.
LABEL_ORIGINS: Tuple[str, ...] = ("owner", "consensus", "model_proposal", "rule_proposal")
HUMAN_ORIGINS: Tuple[str, ...] = ("owner", "consensus")

#: Where a non-null causal_onset_i may have come from. Box geometry is absent by
#: design -- see rule 4 in the module docstring.
ONSET_SOURCES: Tuple[str, ...] = (
    "owner_causal_review",     # owner stepped through bars with the future hidden
    "causal_onset_review",     # the progressive-reveal review pack
    "consensus_review",        # two or more reviewers, recorded agreement
)

QUALITY_GRADES: Tuple[str, ...] = ("A", "B", "C", "not_a_pattern")


class PatternContractError(ValueError):
    """A pattern record that cannot be trusted across a layer boundary."""

    def __init__(self, event_id: str, message: str) -> None:
        super().__init__(f"{event_id}: {message}")
        self.event_id = event_id


def _as_datetime(value: Any, event_id: str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise PatternContractError(event_id, f"{field_name} is not ISO-8601: {value!r}") from exc
    else:
        raise PatternContractError(event_id, f"{field_name} must be a datetime or ISO string")
    if moment.tzinfo is None:
        raise PatternContractError(
            event_id,
            f"{field_name} is naive; a bar time without a zone cannot be compared "
            "against a holdout boundary",
        )
    return moment


@dataclass(frozen=True)
class PatternEvent:
    """One labelled pattern, stated so that another layer can check it.

    Indices are relative to the window that starts at ``window_start_at``.
    ``decision_at`` is the close time of the bar the judgement was made on;
    ``visible_end_at`` is the close time of the last bar the reviewer could see.
    They are equal for a causally clean label and differ -- legally, but
    visibly -- for a hindsight label that is being carried for reference.
    """

    event_id: str
    symbol: str
    timeframe: str
    window_start_at: datetime
    decision_at: datetime
    visible_end_at: datetime
    pattern_valid: bool
    label_origin: str
    window_bars: int
    formation_start_i: Optional[int] = None
    causal_onset_i: Optional[int] = None
    causal_onset_source: Optional[str] = None
    formation_confirm_i: Optional[int] = None
    launch_i: Optional[int] = None
    side: Optional[str] = None
    quality_grade: Optional[str] = None
    near_miss_reason: Optional[str] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    source_artifact_id: Optional[str] = None
    training_eligible: bool = False
    production_eligible: bool = False
    validity_justification: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    # -- rules ------------------------------------------------------------
    def validate(self) -> None:
        eid = self.event_id
        if self.label_origin not in LABEL_ORIGINS:
            raise PatternContractError(eid, f"label_origin {self.label_origin!r} not in {LABEL_ORIGINS}")

        # Zones first: everything below compares these three, and comparing a
        # naive datetime against an aware one raises TypeError rather than
        # saying what is wrong. A bar time without a zone also cannot be placed
        # relative to the holdout boundary, which is the comparison that matters.
        for name in ("window_start_at", "decision_at", "visible_end_at", "reviewed_at"):
            moment = getattr(self, name)
            if moment is None:
                continue
            if not isinstance(moment, datetime):
                raise PatternContractError(eid, f"{name} must be a datetime, got {type(moment).__name__}")
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise PatternContractError(
                    eid,
                    f"{name} is naive; a bar time without a zone cannot be compared "
                    "against a holdout boundary",
                )

        # 1. the reviewer did not see past the bar they judged
        if self.visible_end_at > self.decision_at:
            raise PatternContractError(
                eid,
                f"visible_end_at {self.visible_end_at.isoformat()} is after decision_at "
                f"{self.decision_at.isoformat()}: the label saw future bars",
            )
        if self.window_start_at > self.decision_at:
            raise PatternContractError(eid, "window_start_at is after decision_at")
        if self.window_bars < 1:
            raise PatternContractError(eid, "window_bars must be at least 1")

        # 2. anchors live inside their own window
        last_i = self.window_bars - 1
        for name in ("formation_start_i", "causal_onset_i", "formation_confirm_i", "launch_i"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                raise PatternContractError(eid, f"{name} must be an int or None")
            if not 0 <= value <= last_i:
                raise PatternContractError(
                    eid, f"{name}={value} outside window [0,{last_i}]"
                )

        ordered = [
            (name, getattr(self, name))
            for name in ("formation_start_i", "causal_onset_i", "formation_confirm_i")
        ]
        ordered = [(name, value) for name, value in ordered if value is not None]
        for (first_name, first), (second_name, second) in zip(ordered, ordered[1:]):
            if first > second:
                raise PatternContractError(
                    eid, f"anchor order violated: {first_name}({first}) > {second_name}({second})"
                )

        # 3. validity may not be justified by what happened next
        if self.validity_justification and _mentions_outcome(self.validity_justification):
            raise PatternContractError(
                eid,
                "validity_justification appeals to the outcome. Whether the shape was "
                "there is a question about the bars up to decision_at; what price did "
                "afterwards is a different question and belongs in an Outcome record.",
            )

        # 4. a causal onset needs a human origin that is not box geometry
        if self.causal_onset_i is not None:
            if self.causal_onset_source is None:
                raise PatternContractError(
                    eid,
                    "causal_onset_i is set but causal_onset_source is null: an onset "
                    "with no stated origin cannot be told apart from one a rule wrote "
                    "from the box edge",
                )
            if self.causal_onset_source not in ONSET_SOURCES:
                raise PatternContractError(
                    eid,
                    f"causal_onset_source {self.causal_onset_source!r} not in {ONSET_SOURCES}; "
                    "box geometry is deliberately not an allowed origin",
                )
        elif self.causal_onset_source is not None:
            raise PatternContractError(
                eid, "causal_onset_source is set while causal_onset_i is null"
            )

        # 5. a proposal is not gold
        if self.training_eligible and self.label_origin not in HUMAN_ORIGINS:
            raise PatternContractError(
                eid,
                f"training_eligible with label_origin={self.label_origin!r}: rules and "
                "models generate proposals, and a proposal does not become gold by "
                "being written to disk",
            )
        if self.production_eligible and not self.training_eligible:
            raise PatternContractError(
                eid, "production_eligible without training_eligible"
            )
        if self.quality_grade is not None and self.quality_grade not in QUALITY_GRADES:
            raise PatternContractError(
                eid, f"quality_grade {self.quality_grade!r} not in {QUALITY_GRADES}"
            )
        if self.side is not None and self.side not in ("short", "long"):
            raise PatternContractError(eid, f"side must be 'short', 'long' or None, got {self.side!r}")

    @property
    def future_bars_visible(self) -> int:
        """0 for a causally clean label. Non-zero means hindsight, stated openly."""
        return 0 if self.visible_end_at <= self.decision_at else -1


_OUTCOME_WORDS = (
    "went down", "went up", "dropped", "rallied", "profitable", "pnl",
    "return", "tp hit", "sl hit", "afterwards it", "later fell", "later rose",
)


def _mentions_outcome(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _OUTCOME_WORDS)


_TIMEFRAME_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60, "1h": 60}


def timeframe_delta(timeframe: str, event_id: str = "<unknown>") -> timedelta:
    """Bar duration for a timeframe string. Unknown timeframes fail closed.

    Guessing a bar duration would silently misplace window_start_at, and a
    misplaced window start is a lookahead check that passes for the wrong reason.
    """
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        raise PatternContractError(
            event_id, f"unknown timeframe {timeframe!r}; add it to _TIMEFRAME_MINUTES"
        )
    return timedelta(minutes=minutes)


# -- adapters ---------------------------------------------------------------

def from_gold_row(row: Dict[str, Any]) -> PatternEvent:
    """Convert one yoyo.datasets.gold_schema row.

    A gold row is already causal by construction -- validate_gold refuses a core
    box whose right edge is past decision_bar, and refuses any row that reports
    a holdout read -- so visible_end_at equals decision_at. label_origin is
    "owner" because a gold row *is* an owner verdict; that is what the file is.

    window_start_at is computed from the bar grid rather than accepted as an
    argument: the row already states decision_time, decision_bar and
    local_start_bar, so the window start is determined, and letting a caller
    pass a different one would let the lookahead check be satisfied by a
    number nobody derived.
    """
    eid = str(row.get("gold_id", "<no gold_id>"))
    decision_at = _as_datetime(row["decision_time"], eid, "decision_time")
    start = int(row["local_start_bar"])
    length = int(row["local_window_length"])
    timeframe = str(row["timeframe"])
    bars_back = int(row["decision_bar"]) - start
    window_start_at = decision_at - bars_back * timeframe_delta(timeframe, eid)
    core_start = row.get("core_start_bar")
    core_end = row.get("core_end_bar")
    return PatternEvent(
        event_id=eid,
        symbol=str(row["symbol"]),
        timeframe=timeframe,
        window_start_at=window_start_at,
        decision_at=decision_at,
        visible_end_at=decision_at,
        pattern_valid=row["shape_label"] == "POSITIVE",
        label_origin="owner",
        window_bars=length,
        formation_start_i=None if core_start is None else int(core_start) - start,
        formation_confirm_i=None if core_end is None else int(core_end) - start,
        reviewer=row.get("reviewer"),
        source_artifact_id=row.get("source_path"),
        extra={"box_rule": row.get("box_rule"), "box_status": row.get("box_status")},
    )


#: Review protocols whose verdicts are a valid warrant for a causal onset.
_ONSET_PROTOCOLS = {
    "causal_onset_review_v1": "causal_onset_review",
    "owner_causal_review_v1": "owner_causal_review",
    "consensus_review_v1": "consensus_review",
}


def _onset_source_from_review(review: Dict[str, Any], event_id: str) -> str:
    """Read the warrant off the record. Never supply one.

    An adapter that stamps "causal_onset_review" onto whatever it is handed
    would defeat rule 4 by being the rule that writes the onset -- the check
    would then only ever confirm that this function ran.
    """
    protocol = review.get("protocol_version")
    if protocol not in _ONSET_PROTOCOLS:
        raise PatternContractError(
            event_id,
            f"causal_onset_i is set but review.protocol_version is {protocol!r}, which "
            f"is not one of the causal review protocols {sorted(_ONSET_PROTOCOLS)}. An "
            "onset whose origin the record cannot name is indistinguishable from one "
            "derived from the box edge.",
        )
    return _ONSET_PROTOCOLS[protocol]


def from_pattern_event(
    record: Dict[str, Any],
    *,
    decision_at: Any,
    visible_end_at: Any = None,
) -> PatternEvent:
    """Convert one onset-package PatternEvent (v3) dict.

    ``decision_at`` is an argument rather than a field because the v3 record does
    not carry one: it stores bar indices, and an index cannot be checked for
    lookahead without knowing when its bar closed. Requiring the caller to say so
    is the point -- guessing here would manufacture the answer.

    It is read as the close of the window's LAST bar (``source_window.end_i``),
    which fixes the window start on the bar grid. ``visible_end_at`` defaults to
    the same instant, i.e. a causally clean label; pass it explicitly, and later,
    to carry a hindsight label openly rather than by omission.
    """
    eid = str(record.get("event_id", "<no event_id>"))
    window = record.get("source_window") or {}
    start_i = int(window.get("start_i", 0))
    bars = int(window.get("bars", 0))
    anchors = record.get("anchors") or {}

    def rel(key: str) -> Optional[int]:
        value = anchors.get(key)
        return None if value is None else int(value) - start_i

    decision = _as_datetime(decision_at, eid, "decision_at")
    timeframe = str(record.get("timeframe", "15m"))
    if bars < 1:
        raise PatternContractError(eid, f"source_window.bars={bars} is not a window")
    window_start_at = decision - (bars - 1) * timeframe_delta(timeframe, eid)
    onset = rel("causal_onset_i")
    review = record.get("review") or {}
    onset_source = _onset_source_from_review(review, eid) if onset is not None else None
    return PatternEvent(
        event_id=eid,
        symbol=str(record["symbol"]),
        timeframe=timeframe,
        window_start_at=window_start_at,
        decision_at=decision,
        visible_end_at=_as_datetime(visible_end_at or decision, eid, "visible_end_at"),
        pattern_valid=record.get("event_validity") == "valid",
        label_origin="owner" if review.get("reviewer") == "owner" else "model_proposal",
        window_bars=bars,
        formation_start_i=rel("formation_start_i"),
        causal_onset_i=onset,
        causal_onset_source=onset_source,
        formation_confirm_i=rel("formation_confirm_i"),
        launch_i=rel("launch_i"),
        side=record.get("side"),
        quality_grade=record.get("quality_label"),
        reviewer=review.get("reviewer"),
        source_artifact_id=(record.get("provenance") or {}).get("pattern_library_sha256"),
    )
