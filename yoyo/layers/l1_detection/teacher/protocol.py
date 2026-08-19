"""The interface every candidate generator presents, and the gate it passes.

A teacher may only be used through an artifact that the registry knows about.
That is the whole mechanism: `resolve_teacher_artifact` looks the weight up in
artifacts/registry.yaml, checks the file on disk still hashes to what the
registry recorded, and refuses otherwise. An unregistered weight file cannot be
turned into proposals, so "which model produced these candidates" is always
answerable, and a weight that was swapped underneath a recorded id is caught at
load rather than discovered in a report.

Deliberately a Protocol rather than a base class. The two teachers that exist
are a YOLO detector and a numeric scanner, they share no implementation, and
inheritance would be a shared parent invented purely so that a type checker had
something to point at.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:  # Protocol is 3.8+, runtime_checkable 3.8+; the try is for very old hosts
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from yoyo.artifacts.registry import load_artifact_registry
from yoyo.contracts.artifacts import ArtifactRecord
from yoyo.contracts.candidates import CandidateProposal

#: An embedding is just a vector; the shape is the teacher's business.
Embedding = Sequence[float]


class TeacherRegistrationError(RuntimeError):
    """A teacher that cannot be identified. Never downgraded to a warning."""


@runtime_checkable
class PatternTeacher(Protocol):
    """Proposes candidates and embeds rendered inputs. Fires nothing."""

    #: artifact_id of the weights, as registered in artifacts/registry.yaml
    teacher_artifact_id: str

    def propose(self, closed_bars: Any, available_at: Any) -> List[CandidateProposal]:
        """Candidates from bars that have closed, available no earlier than the last one."""
        ...

    def embed(self, rendered_input: Any) -> Embedding:
        """A vector for retrieval and similarity. Not a score, not a decision."""
        ...


def resolve_teacher_artifact(
    artifact_id: str,
    *,
    root: Optional[Path] = None,
    verify_hash: bool = True,
) -> ArtifactRecord:
    """Look a teacher up in the registry and prove the file still matches.

    `verify_hash=False` exists for a registry row whose bytes live on another
    machine -- the 59 detector weights on the Windows 3060, for instance. It
    skips the file check; it does not skip the registration check, because an
    unregistered teacher is the thing this function exists to refuse.
    """
    records = {record.artifact_id: record for record in load_artifact_registry(root=root)}
    record = records.get(artifact_id)
    if record is None:
        raise TeacherRegistrationError(
            f"{artifact_id!r} is not in artifacts/registry.yaml. Register the weights "
            "before using them: a proposal whose generator is unregistered cannot be "
            "reproduced or audited for what data it was trained on."
        )
    if record.role != "pattern_teacher":
        raise TeacherRegistrationError(
            f"{artifact_id!r} is registered with role={record.role!r}, not pattern_teacher"
        )
    if record.production_eligible:
        raise TeacherRegistrationError(
            f"{artifact_id!r} is marked production_eligible. A teacher proposes "
            "candidates for study; the live path takes its model from a promoted "
            "ModelBundle, not from a teacher (CLAUDE.md rule 12)."
        )

    if not verify_hash:
        return record
    if not record.sha256:
        raise TeacherRegistrationError(
            f"{artifact_id!r} has no sha256 in the registry, so 'the file still matches' "
            "cannot be checked. Record the digest, or pass verify_hash=False and say in "
            "the report that the bytes were not verified."
        )

    from yoyo.artifacts.lineage import digest_file
    from yoyo.contracts.paths import data_root

    base = Path(root) if root is not None else data_root()
    path = base / record.source_path
    if not path.is_file():
        raise TeacherRegistrationError(
            f"{artifact_id!r} is registered at {record.source_path}, which does not exist "
            f"under {base}. The registry row may point at another machine; pass "
            "verify_hash=False only if the report says the bytes were not verified."
        )
    actual = digest_file(path).sha256
    if actual != record.sha256:
        raise TeacherRegistrationError(
            f"{artifact_id!r} on disk hashes to {actual}, but the registry records "
            f"{record.sha256}. The file has been replaced under a recorded id -- every "
            "proposal attributed to this id is now attributed to the wrong model."
        )
    return record


def describe_teacher(record: ArtifactRecord) -> Dict[str, Any]:
    """The provenance block a proposal carries, built from the registry row.

    One function so that every generator attaches the same fields, rather than
    each one deciding for itself which parts of provenance are worth recording.
    """
    return {
        "teacher_artifact_id": record.artifact_id,
        "teacher_sha256": record.sha256,
        "teacher_source_repo": record.source_repo,
        "teacher_source_commit": record.source_commit,
        "teacher_holdout_status": record.holdout_status,
        "production_eligible": False,
    }
