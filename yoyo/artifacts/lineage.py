"""What produced this artifact, and can it be produced again.

Adapted from darkforest-one src/darkforest_one/governance/manifest.py and
candidate/artifact_manifest.py at fd36dd1, dropped to Python 3.9 (no slots) and
to this repository's registry vocabulary.

The rule the source repository enforced and this one keeps: a manifest names
the code, the configuration and the inputs that made the artifact, and the
identifiers are hashes rather than paths. A path says where a file was; a hash
says what it contained.

Two rules this repository adds, both from its own scar tissue:

  - `builder_committed_at` is recorded next to `generated_at`, and
    `assert_builder_landed_first` compares them. An artifact whose generator
    was never committed cannot be reproduced by anyone, and the discovery mode
    is always the same: the manifest looks complete
    (docs/learnings/artifacts-built-before-their-builder-landed.md).
  - Reproducibility is recorded per axis, not as a boolean. w20_midbox rebuilt
    2635/2635 images byte-identically while 405 samples landed in the wrong
    split, and a single `reproducible: true` would have been issued for it
    (docs/learnings/reproducibility-is-per-axis-not-a-boolean.md).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MANIFEST_SCHEMA_VERSION = 1

#: The axes an artifact can be reproducible along. Equality on one says nothing
#: about the others.
REPRODUCIBILITY_AXES = ("content", "sample_set", "split_assignment", "ordering")


class LineageError(ValueError):
    """A lineage claim that cannot be checked. Never downgraded."""


@dataclass(frozen=True)
class ArtifactDigest:
    path: str
    sha256: str
    size_bytes: int


def digest_file(path) -> ArtifactDigest:
    artifact_path = Path(path)
    if not artifact_path.is_file():
        raise LineageError(f"artifact does not exist: {artifact_path}")
    digest = hashlib.sha256()
    size = 0
    with artifact_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return ArtifactDigest(str(artifact_path), digest.hexdigest(), size)


def canonical_json_sha256(payload: Any) -> str:
    """Hash of a canonical encoding: sorted keys, fixed separators, no NaN.

    allow_nan=False on purpose. NaN and Infinity are not JSON, and a manifest
    that serialises them produces a file other parsers reject -- which surfaces
    as "the manifest is corrupt" long after the run that wrote it.
    """
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_utc(value: Optional[datetime], name: str) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise LineageError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True)
class RunManifest:
    """One production of one artifact, with everything needed to repeat it."""

    artifact_id: str
    protocol_version: str
    generator_path: str
    generator_commit: str
    config_sha256: str
    input_digests: List[ArtifactDigest] = field(default_factory=list)
    output_digests: List[ArtifactDigest] = field(default_factory=list)
    generated_at: Optional[datetime] = None
    builder_committed_at: Optional[datetime] = None
    data_cutoff: Optional[str] = None
    holdout_read: bool = False
    reproducibility: Dict[str, Optional[bool]] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for name in ("artifact_id", "protocol_version", "generator_path", "generator_commit"):
            if not getattr(self, name):
                raise LineageError(f"{name} cannot be empty")
        if len(self.config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.config_sha256.lower()
        ):
            raise LineageError("config_sha256 must be a SHA-256 hex digest")
        _require_utc(self.generated_at, "generated_at")
        _require_utc(self.builder_committed_at, "builder_committed_at")
        unknown = set(self.reproducibility) - set(REPRODUCIBILITY_AXES)
        if unknown:
            raise LineageError(
                f"unknown reproducibility axes {sorted(unknown)}; known axes are "
                f"{REPRODUCIBILITY_AXES}"
            )

    def assert_builder_landed_first(self) -> None:
        """Refuse an artifact older than the commit that introduced its builder.

        The failure this catches states itself badly: the artifact exists, its
        hashes are right, and every reproduction claim about it is unverified,
        because the code that produced it is not in git.
        """
        if self.generated_at is None or self.builder_committed_at is None:
            raise LineageError(
                f"{self.artifact_id}: cannot check builder ordering without both "
                "generated_at and builder_committed_at"
            )
        if self.generated_at < self.builder_committed_at:
            raise LineageError(
                f"{self.artifact_id} was generated at {self.generated_at.isoformat()}, "
                f"before its builder {self.generator_path} landed in git at "
                f"{self.builder_committed_at.isoformat()}. Commit the builder, then "
                "rebuild -- every reproduction claim about this artifact is currently "
                "unverified."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "artifact_id": self.artifact_id,
            "protocol_version": self.protocol_version,
            "generator_path": self.generator_path,
            "generator_commit": self.generator_commit,
            "config_sha256": self.config_sha256,
            "generated_at": None if self.generated_at is None else self.generated_at.isoformat(),
            "builder_committed_at": (
                None if self.builder_committed_at is None else self.builder_committed_at.isoformat()
            ),
            "data_cutoff": self.data_cutoff,
            "holdout_read": self.holdout_read,
            "reproducibility": {axis: self.reproducibility.get(axis) for axis in REPRODUCIBILITY_AXES},
            "inputs": [d.__dict__ for d in self.input_digests],
            "outputs": [d.__dict__ for d in self.output_digests],
            "notes": self.notes,
        }

    def manifest_sha256(self) -> str:
        return canonical_json_sha256(self.to_dict())


def compare_reproduction(
    original: Iterable[ArtifactDigest], rebuilt: Iterable[ArtifactDigest]
) -> Dict[str, Any]:
    """Per-file comparison of two builds. Reports, never summarises to a boolean.

    Returns the identical, differing, missing and added sets separately, because
    "2635/2635 images identical" and "405 samples in the wrong split" were true
    at the same time and one sentence cannot hold both.
    """
    left = {d.path: d.sha256 for d in original}
    right = {d.path: d.sha256 for d in rebuilt}
    shared = sorted(set(left) & set(right))
    return {
        "n_original": len(left),
        "n_rebuilt": len(right),
        "identical": [p for p in shared if left[p] == right[p]],
        "differing": [p for p in shared if left[p] != right[p]],
        "missing_from_rebuild": sorted(set(left) - set(right)),
        "added_by_rebuild": sorted(set(right) - set(left)),
    }
