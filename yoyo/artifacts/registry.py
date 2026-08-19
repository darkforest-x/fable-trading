"""Load and cross-check artifacts/registry.yaml and experiments/registry.yaml.

Three classes of failure are caught here rather than in the record contract,
because none of them is visible from inside a single record:

  1. A duplicate artifact_id or experiment_id. Two rows claiming one name is how
     "the v11 weights" comes to mean two different files.
  2. An experiment citing an artifact_id that no artifact row declares. The
     dangling reference is the point where a report stops being reproducible.
  3. A schema_version this code does not understand -- read fail-closed rather
     than parsing an unknown layout optimistically.

The registry root is found the same way everything else in this project finds
it: `yoyo.contracts.paths.data_root`, never `Path(__file__).parents[n]`. That
inference is precisely what broke when yoyo left fable-trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from yoyo.contracts.artifacts import ArtifactRecord, ExperimentRecord, RegistryError
from yoyo.contracts.paths import data_root

ARTIFACT_REGISTRY_PATH = Path("artifacts") / "registry.yaml"
EXPERIMENT_REGISTRY_PATH = Path("experiments") / "registry.yaml"
SUPPORTED_SCHEMA_VERSIONS = (1,)


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem, not data
        raise RegistryError(
            str(path),
            "PyYAML is required to read the governance registries; it is listed in "
            "requirements.txt",
        ) from exc
    if not path.is_file():
        raise RegistryError(str(path), "registry file does not exist")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if raw is None:
        raise RegistryError(str(path), "registry file is empty")
    if not isinstance(raw, dict):
        raise RegistryError(str(path), "registry root must be a mapping")
    version = raw.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RegistryError(
            str(path),
            f"schema_version {version!r} is not one of {SUPPORTED_SCHEMA_VERSIONS}; "
            "refusing to guess the layout",
        )
    return raw


def _rows(raw: Dict[str, Any], key: str, path: Path) -> List[Dict[str, Any]]:
    rows = raw.get(key)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RegistryError(str(path), f"{key} must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise RegistryError(str(path), f"every {key} entry must be a mapping, got {row!r}")
    return rows


def _reject_duplicates(ids: List[str], path: Path, label: str) -> None:
    counts: Dict[str, int] = {}
    for value in ids:
        counts[value] = counts.get(value, 0) + 1
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    if duplicates:
        raise RegistryError(str(path), f"duplicate {label}: {duplicates}")


def _resolve(path: Optional[Path], default: Path, root: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    base = Path(root) if root is not None else data_root()
    return base / default


def load_artifact_registry(
    path: Optional[Path] = None, *, root: Optional[Path] = None
) -> List[ArtifactRecord]:
    resolved = _resolve(path, ARTIFACT_REGISTRY_PATH, root)
    raw = _load_yaml(resolved)
    records = [ArtifactRecord.from_mapping(row) for row in _rows(raw, "artifacts", resolved)]
    _reject_duplicates([r.artifact_id for r in records], resolved, "artifact_id")
    return records


def load_experiment_registry(
    path: Optional[Path] = None, *, root: Optional[Path] = None
) -> List[ExperimentRecord]:
    resolved = _resolve(path, EXPERIMENT_REGISTRY_PATH, root)
    raw = _load_yaml(resolved)
    records = [ExperimentRecord.from_mapping(row) for row in _rows(raw, "experiments", resolved)]
    _reject_duplicates([r.experiment_id for r in records], resolved, "experiment_id")
    return records


@dataclass(frozen=True)
class Registries:
    artifacts: List[ArtifactRecord]
    experiments: List[ExperimentRecord]

    def artifact(self, artifact_id: str) -> ArtifactRecord:
        for record in self.artifacts:
            if record.artifact_id == artifact_id:
                return record
        raise RegistryError(artifact_id, "no such artifact_id in the registry")


def load_registries(*, root: Optional[Path] = None) -> Registries:
    """Load both registries and enforce the rules that span them."""
    artifacts = load_artifact_registry(root=root)
    experiments = load_experiment_registry(root=root)

    known = {record.artifact_id for record in artifacts}
    dangling = {
        experiment.experiment_id: sorted(set(experiment.artifacts) - known)
        for experiment in experiments
        if set(experiment.artifacts) - known
    }
    if dangling:
        raise RegistryError(
            "experiments/registry.yaml",
            f"experiments cite artifact_ids that no artifact row declares: {dangling}",
        )
    return Registries(artifacts=artifacts, experiments=experiments)
