"""What an artifact and an experiment must declare before anyone may cite them.

Two registries, one contract module, because they answer halves of the same
question. `artifacts/registry.yaml` says *what a thing is and where it came
from*; `experiments/registry.yaml` says *what question was asked of it and how
that ended*. Splitting the validation would let a registry drift into naming an
artifact_id nothing declares.

The rules encoded here are the ones this project has already paid for:

  - Every record names its source_repo, source_commit and source_path. Task book
    section 11.1: an asset that cannot be traced to a commit is an asset whose
    provenance is a memory.
  - `holdout_status` is mandatory and closed-vocabulary. "I think this was
    pre-holdout" is exactly the sentence CLAUDE.md rule 1 exists to prevent.
  - `training_eligible` and `production_eligible` default to false and are
    separate flags. Owner confirming a *class protocol* is not owner confirming
    every sample, and a model that trained is not a model that may trade
    (docs/learnings/protocol-confirmation-is-not-sample-confirmation.md).
  - A negative experiment cannot be recorded as `inconclusive` without a reason,
    and cannot be silently deleted: `rejected` is a terminal, citable state.

Deliberately dataclasses and plain validation rather than pydantic. The four
repositories being merged used three different validation stacks; the one this
venv can run on Python 3.9 without a new dependency is none of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ARTIFACT_TYPES: Tuple[str, ...] = (
    "model",
    "weights",
    "dataset",
    "annotation_set",
    "manifest",
    "report",
    "config",
)

# Where an artifact stands relative to the frozen holdout boundary (2026-05-04).
HOLDOUT_STATUSES: Tuple[str, ...] = (
    "pre_holdout",             # built entirely from data before the boundary
    "historical",              # predates the boundary rule; status inherited, not re-derived
    "holdout_consumed",        # read holdout under a recorded, owner-approved authorisation
    "unknown",                 # provenance genuinely unestablished -- blocks promotion
)

EXPERIMENT_STATUSES: Tuple[str, ...] = (
    "active",
    "accepted",
    "rejected",
    "inconclusive",
    "superseded",
)

TERMINAL_NEGATIVE: Tuple[str, ...] = ("rejected", "inconclusive", "superseded")


class RegistryError(ValueError):
    """A registry record that cannot be trusted. Never downgraded to a warning."""

    def __init__(self, record_id: str, message: str) -> None:
        super().__init__(f"{record_id}: {message}")
        self.record_id = record_id


def _require(record: Dict[str, Any], key: str, record_id: str) -> Any:
    if key not in record or record[key] in (None, ""):
        raise RegistryError(record_id, f"missing required field {key!r}")
    return record[key]


def _require_bool(record: Dict[str, Any], key: str, record_id: str) -> bool:
    value = record.get(key, False)
    if not isinstance(value, bool):
        raise RegistryError(
            record_id, f"{key} must be a YAML boolean, got {value!r} ({type(value).__name__})"
        )
    return value


def _optional_sha256(value: Any, record_id: str, key: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text.lower()):
        raise RegistryError(record_id, f"{key} must be a 64-character SHA-256 hex digest")
    return text.lower()


@dataclass(frozen=True)
class ArtifactRecord:
    """One registered dataset, model, weight file, manifest or report.

    `storage_uri` rather than a committed copy is the point: task book section
    3.6 keeps large products out of git, so the registry holds identity (id,
    sha256, size) and a pointer, and the bytes stay where they already are.
    """

    artifact_id: str
    artifact_type: str
    role: str
    source_repo: str
    source_commit: str
    source_path: str
    holdout_status: str
    training_eligible: bool = False
    production_eligible: bool = False
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    storage_uri: Optional[str] = None
    generator_commit: Optional[str] = None
    config_hash: Optional[str] = None
    data_cutoff: Optional[str] = None
    notes: str = ""

    @classmethod
    def from_mapping(cls, raw: Dict[str, Any]) -> "ArtifactRecord":
        record_id = str(raw.get("artifact_id", "<no artifact_id>"))
        artifact_type = str(_require(raw, "artifact_type", record_id))
        if artifact_type not in ARTIFACT_TYPES:
            raise RegistryError(
                record_id, f"artifact_type {artifact_type!r} not in {ARTIFACT_TYPES}"
            )
        holdout_status = str(_require(raw, "holdout_status", record_id))
        if holdout_status not in HOLDOUT_STATUSES:
            raise RegistryError(
                record_id, f"holdout_status {holdout_status!r} not in {HOLDOUT_STATUSES}"
            )
        size = raw.get("size_bytes")
        if size is not None and (not isinstance(size, int) or size < 0):
            raise RegistryError(record_id, "size_bytes must be a non-negative integer")

        record = cls(
            artifact_id=str(_require(raw, "artifact_id", record_id)),
            artifact_type=artifact_type,
            role=str(_require(raw, "role", record_id)),
            source_repo=str(_require(raw, "source_repo", record_id)),
            source_commit=str(_require(raw, "source_commit", record_id)),
            source_path=str(_require(raw, "source_path", record_id)),
            holdout_status=holdout_status,
            training_eligible=_require_bool(raw, "training_eligible", record_id),
            production_eligible=_require_bool(raw, "production_eligible", record_id),
            sha256=_optional_sha256(raw.get("sha256"), record_id, "sha256"),
            size_bytes=size,
            storage_uri=raw.get("storage_uri"),
            generator_commit=raw.get("generator_commit"),
            config_hash=raw.get("config_hash"),
            data_cutoff=raw.get("data_cutoff"),
            notes=str(raw.get("notes", "")),
        )
        record.validate()
        return record

    def validate(self) -> None:
        if self.production_eligible and self.holdout_status == "unknown":
            raise RegistryError(
                self.artifact_id,
                "production_eligible with holdout_status=unknown: promotion requires "
                "an established holdout provenance, not an absent one",
            )
        if self.production_eligible and not self.sha256:
            raise RegistryError(
                self.artifact_id,
                "production_eligible without sha256: the executor identifies a bundle "
                "by hash, so an unhashed artifact cannot be the thing it loaded",
            )


@dataclass(frozen=True)
class ExperimentRecord:
    """One registered experiment: the question, the single variable, the verdict.

    `result` is required for every terminal state including the negative ones.
    CLAUDE.md: a failed experiment that is deleted or rewritten as "pending
    optimisation" pollutes the log for everyone who comes after.
    """

    experiment_id: str
    source_repo: str
    source_commit: str
    status: str
    question: str
    result: str
    holdout_consumed: bool = False
    production_eligible: bool = False
    training_eligible: bool = False
    canonical_report: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    single_variable: Optional[str] = None
    reuse_allowed: Optional[bool] = None
    notes: str = ""

    @classmethod
    def from_mapping(cls, raw: Dict[str, Any]) -> "ExperimentRecord":
        record_id = str(raw.get("experiment_id", "<no experiment_id>"))
        status = str(_require(raw, "status", record_id))
        if status not in EXPERIMENT_STATUSES:
            raise RegistryError(record_id, f"status {status!r} not in {EXPERIMENT_STATUSES}")
        artifacts = raw.get("artifacts") or []
        if not isinstance(artifacts, list) or any(not isinstance(a, str) for a in artifacts):
            raise RegistryError(record_id, "artifacts must be a list of artifact_id strings")

        record = cls(
            experiment_id=str(_require(raw, "experiment_id", record_id)),
            source_repo=str(_require(raw, "source_repo", record_id)),
            source_commit=str(_require(raw, "source_commit", record_id)),
            status=status,
            question=str(_require(raw, "question", record_id)),
            result=str(_require(raw, "result", record_id)),
            holdout_consumed=_require_bool(raw, "holdout_consumed", record_id),
            production_eligible=_require_bool(raw, "production_eligible", record_id),
            training_eligible=_require_bool(raw, "training_eligible", record_id),
            canonical_report=raw.get("canonical_report"),
            artifacts=list(artifacts),
            single_variable=raw.get("single_variable"),
            reuse_allowed=raw.get("reuse_allowed"),
            notes=str(raw.get("notes", "")),
        )
        record.validate()
        return record

    def validate(self) -> None:
        if self.status in TERMINAL_NEGATIVE and self.production_eligible:
            raise RegistryError(
                self.experiment_id,
                f"status={self.status} but production_eligible=true: a result that did "
                "not pass cannot also be cleared for production",
            )
        if self.status == "accepted" and not self.canonical_report:
            raise RegistryError(
                self.experiment_id,
                "accepted without a canonical_report: an accepted result that nobody "
                "can read is not a result",
            )
