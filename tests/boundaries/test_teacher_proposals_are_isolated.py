"""A teacher proposes. It cannot become gold and it cannot reach the executor.

Three separations, each with a number behind it:

  proposal vs signal   the old detector reproduces its own boxes at 62-72% with
                       full context and 9-10% at the tip. A full-context
                       proposal is research; calling it fresh is CLAUDE.md
                       rule 12's failure mode.
  proposal vs gold     a generator's output is a proposal until a human reviews
                       it; a rule that writes labels is a rule marking its own
                       homework.
  teacher vs bundle    the executor's model comes from a promoted bundle, never
                       from whatever weight file happened to be newest.

Enforced by constructors that raise, not by review.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yoyo.contracts.candidates import (
    CandidateContractError,
    CandidateProposal,
    SourceWindow,
)
from yoyo.layers.l1_detection.teacher import (
    PatternTeacher,
    TeacherRegistrationError,
    describe_teacher,
    resolve_teacher_artifact,
)

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
BAR = timedelta(minutes=15)


def _window(start_i=0, end_i=199, visible_end_at=None) -> SourceWindow:
    return SourceWindow(
        start_i=start_i,
        end_i=end_i,
        visible_end_at=visible_end_at or (T0 + (end_i - start_i) * BAR),
    )


def _proposal(**overrides) -> CandidateProposal:
    window = overrides.pop("source_window", None) or _window()
    base = dict(
        candidate_id="cand-1",
        symbol="ETH-USDT-SWAP",
        timeframe="15m",
        generator_id="owner-v10-chain",
        generator_kind="pattern_teacher",
        generator_version="v10_chain",
        generator_sha256="b9a84b5f5ebf0032dfa8ddf1ed1f12c19b7cc2d410a57480bd196d76cbc7d953",
        source_window=window,
        available_at=window.visible_end_at,
        decision_i=190,
    )
    base.update(overrides)
    return CandidateProposal(**base)


# -- proposal is not a signal ---------------------------------------------

def test_a_well_formed_proposal_is_accepted():
    assert _proposal().lookahead_bars == 9


def test_a_tip_causal_proposal_reports_zero_lookahead():
    assert _proposal(decision_i=199).lookahead_bars == 0


def test_available_at_cannot_predate_the_last_bar_the_generator_saw():
    """The rule that stops an after-the-fact detection looking fresh."""
    window = _window()
    with pytest.raises(CandidateContractError, match="BEFORE the window's last visible bar"):
        _proposal(source_window=window, available_at=window.visible_end_at - 20 * BAR)


def test_a_proposal_cannot_declare_itself_production_eligible():
    with pytest.raises(CandidateContractError, match="not a signal"):
        _proposal(production_eligible=True)


def test_a_proposal_cannot_declare_itself_training_eligible():
    with pytest.raises(CandidateContractError, match="becomes a training label only"):
        _proposal(training_eligible=True)


def test_a_generator_without_a_hash_is_refused():
    with pytest.raises(CandidateContractError, match="cannot be reproduced"):
        _proposal(generator_sha256="")


def test_a_malformed_generator_hash_is_refused():
    with pytest.raises(CandidateContractError, match="SHA-256"):
        _proposal(generator_sha256="not-a-digest")


def test_a_decision_bar_outside_its_own_window_is_refused():
    with pytest.raises(CandidateContractError, match="outside the source window"):
        _proposal(decision_i=500)


def test_an_unknown_generator_kind_is_refused():
    with pytest.raises(CandidateContractError, match="generator_kind"):
        _proposal(generator_kind="hunch")


def test_a_naive_available_at_is_refused():
    with pytest.raises(CandidateContractError, match="timezone-aware"):
        _proposal(available_at=datetime(2026, 3, 1, 12, 0))


def test_a_bbox_outside_the_unit_square_is_refused():
    with pytest.raises(CandidateContractError, match="four values in"):
        _proposal(bbox_xywhn=(0.5, 0.5, 1.4, 0.2))


# -- the teacher must be registered ---------------------------------------

def test_an_unregistered_teacher_cannot_be_used():
    with pytest.raises(TeacherRegistrationError, match="not in artifacts/registry.yaml"):
        resolve_teacher_artifact("some-weight-nobody-registered", root=REPO)


def test_a_registered_teacher_resolves_and_its_bytes_are_verified():
    record = resolve_teacher_artifact("owner-v10-chain", root=REPO)
    assert record.role == "pattern_teacher"
    assert record.production_eligible is False
    assert record.sha256 == "b9a84b5f5ebf0032dfa8ddf1ed1f12c19b7cc2d410a57480bd196d76cbc7d953"


def test_a_teacher_whose_bytes_moved_on_another_machine_needs_an_explicit_waiver():
    """The 3060's 59 weights are registered but not present here."""
    with pytest.raises(TeacherRegistrationError, match="does not exist|no sha256"):
        resolve_teacher_artifact("yolo-xx-detector-weights-3060", root=REPO)
    record = resolve_teacher_artifact(
        "yolo-xx-detector-weights-3060", root=REPO, verify_hash=False
    )
    assert record.storage_uri.startswith("host://")


def test_a_non_teacher_artifact_is_refused_as_a_teacher():
    with pytest.raises(TeacherRegistrationError, match="role="):
        resolve_teacher_artifact("yoyo-eth-mvp-report", root=REPO)


def test_the_provenance_block_always_carries_the_same_fields():
    record = resolve_teacher_artifact("owner-v10-chain", root=REPO)
    described = describe_teacher(record)
    assert set(described) == {
        "teacher_artifact_id",
        "teacher_sha256",
        "teacher_source_repo",
        "teacher_source_commit",
        "teacher_holdout_status",
        "production_eligible",
    }
    assert described["production_eligible"] is False


# -- the protocol ----------------------------------------------------------

def test_anything_with_the_two_methods_satisfies_the_protocol():
    class FakeTeacher:
        teacher_artifact_id = "owner-v10-chain"

        def propose(self, closed_bars, available_at):
            return []

        def embed(self, rendered_input):
            return [0.0]

    assert isinstance(FakeTeacher(), PatternTeacher)


def test_something_missing_embed_does_not():
    class Halfway:
        teacher_artifact_id = "x"

        def propose(self, closed_bars, available_at):
            return []

    assert not isinstance(Halfway(), PatternTeacher)
