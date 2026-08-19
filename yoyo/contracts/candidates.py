"""A candidate is a proposal. It is never, by itself, a reason to trade.

The distinction this module exists to hold is the one that cost the most to
learn: recognising a completed pattern and recognising one at the live edge are
different tasks. The old detector reproduces its own boxes at 62-72% given full
context and at 9-10% at the tip. A proposal made from a full-context window is
a research object; treating it as a live signal is the failure mode CLAUDE.md
rule 12 names, and the rule is stated in terms this module encodes:

    the live path scans only the tip / tip-1 / tip-2 causal window
    any model that uses bars after the core pattern may not impersonate a fresh
    signal, and may not enter tip-smoke, forward, ACTIVE or a deployment
    if such a model is ever used, its output time must be recorded as the RIGHT
    EDGE OF ITS FULL DETECTION WINDOW, not as the pattern's location

So `available_at` here is defined as the close of the last bar the generator
looked at -- not the right edge of the box it drew. Those differ by exactly the
amount of future the generator saw, and conflating them is what makes an
after-the-fact detection look fresh.

Every proposal names the artifact that made it, by id and by hash. A proposal
whose generator cannot be identified cannot be reproduced, cannot be audited
for what data it was trained on, and cannot be told apart from one made by a
different version of the same file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

#: A proposal's role in the pipeline. None of them is "signal".
GENERATOR_KINDS: Tuple[str, ...] = (
    "pattern_teacher",     # a detector used to find candidates for study
    "numeric_scanner",     # a rule over indicators
    "onset_model",         # a causal-onset classifier
    "human_proposal",      # someone pointed at a chart
)


class CandidateContractError(ValueError):
    """A proposal that cannot be trusted downstream."""

    def __init__(self, candidate_id: str, message: str) -> None:
        super().__init__(f"{candidate_id}: {message}")
        self.candidate_id = candidate_id


@dataclass(frozen=True)
class SourceWindow:
    """The bars the generator was shown, and when the last of them closed.

    `visible_end_at` is the close of `end_i`. It is stated rather than derived
    so that a window whose bars are not contiguous still has to say where its
    knowledge ends.
    """

    start_i: int
    end_i: int
    visible_end_at: datetime

    def __post_init__(self) -> None:
        if self.end_i < self.start_i:
            raise ValueError(f"source window ends before it starts: [{self.start_i},{self.end_i}]")
        if self.visible_end_at.tzinfo is None or self.visible_end_at.utcoffset() is None:
            raise ValueError("visible_end_at must be timezone-aware")

    @property
    def bars(self) -> int:
        return self.end_i - self.start_i + 1


@dataclass(frozen=True)
class CandidateProposal:
    """One proposed bar, with the evidence needed to judge and reproduce it.

    `production_eligible` defaults to False and there is no code path in this
    repository that sets it True. Promotion is an owner decision (CLAUDE.md
    rules 10 and 11); the field exists so that the absence of promotion is
    recorded rather than merely unmentioned.
    """

    candidate_id: str
    symbol: str
    timeframe: str
    generator_id: str
    generator_kind: str
    generator_version: str
    generator_sha256: str
    source_window: SourceWindow
    available_at: datetime
    decision_i: int
    confidence: Optional[float] = None
    bbox_xywhn: Optional[Tuple[float, float, float, float]] = None
    training_eligible: bool = False
    production_eligible: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        cid = self.candidate_id
        if self.generator_kind not in GENERATOR_KINDS:
            raise CandidateContractError(
                cid, f"generator_kind {self.generator_kind!r} not in {GENERATOR_KINDS}"
            )
        for name in ("generator_id", "generator_version", "generator_sha256"):
            if not getattr(self, name):
                raise CandidateContractError(
                    cid,
                    f"{name} is empty. A proposal whose generator cannot be identified "
                    "cannot be reproduced or audited for what it was trained on.",
                )
        digest = self.generator_sha256.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise CandidateContractError(cid, "generator_sha256 must be a SHA-256 hex digest")

        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise CandidateContractError(cid, "available_at must be timezone-aware")

        # The rule that makes an after-the-fact detection stop looking fresh.
        if self.available_at < self.source_window.visible_end_at:
            raise CandidateContractError(
                cid,
                f"available_at {self.available_at.isoformat()} is BEFORE the window's last "
                f"visible bar closed at {self.source_window.visible_end_at.isoformat()}. "
                "A proposal becomes available when the generator's last input bar closes, "
                "not when the pattern it points at occurred -- the difference is exactly "
                "the future the generator saw (CLAUDE.md rule 12).",
            )

        if not self.source_window.start_i <= self.decision_i <= self.source_window.end_i:
            raise CandidateContractError(
                cid,
                f"decision_i={self.decision_i} is outside the source window "
                f"[{self.source_window.start_i},{self.source_window.end_i}]",
            )

        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise CandidateContractError(cid, f"confidence {self.confidence} outside [0,1]")

        if self.bbox_xywhn is not None:
            if len(self.bbox_xywhn) != 4 or any(
                not 0.0 <= float(v) <= 1.0 for v in self.bbox_xywhn
            ):
                raise CandidateContractError(cid, "bbox_xywhn must be four values in [0,1]")

        if self.production_eligible:
            raise CandidateContractError(
                cid,
                "production_eligible=True on a candidate proposal. A proposal is not a "
                "signal; entering the order path requires a promoted ModelBundle and an "
                "owner decision (CLAUDE.md rules 10-11).",
            )
        if self.training_eligible:
            raise CandidateContractError(
                cid,
                "training_eligible=True on a candidate proposal. A generator's output is "
                "a proposal; it becomes a training label only through human review "
                "(yoyo.contracts.pattern rule 5).",
            )

    @property
    def lookahead_bars(self) -> int:
        """How many bars past the proposed one the generator was allowed to see.

        Zero for a tip-causal proposal. Positive means the proposal was made
        with hindsight, which is legitimate for research and disqualifying for
        the live path. The number is here so that it appears in a report rather
        than being reconstructed from the window arithmetic by whoever reads it.
        """
        return self.source_window.end_i - self.decision_i
