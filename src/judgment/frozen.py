"""Frozen LightGBM artifacts for forward validation.

The project selected tp5_sl2 on the SWAP universe as the current mainline.
This module centralizes artifact discovery, metadata fingerprints, and
frozen-model scoring so dashboards and forward tracking do not retrain.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Mapping, TypedDict

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import DEFAULT_HORIZON_BARS, load_splits, train_model

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
BAR: Final = pd.Timedelta(minutes=15)
DEFAULT_SCORE_QUANTILE: Final = 0.90
# Mainline after 2026-07-15 owner cutover: YOLO candidates + TP5/SL2 labels.
DEFAULT_CONFIG_NAME: Final = "tp5_sl2_swap_yolo"
# Legacy rule-scan freeze (pre-cutover); kept for rollback / comparisons.
LEGACY_RULES_CONFIG_NAME: Final = "tp5_sl2_swap"


class ScoreCacheMetadata(TypedDict, total=False):
    threshold: float
    model_path: str
    dataset_path: str
    dataset_sha256: str


@dataclass(frozen=True)
class FrozenConfig:
    __slots__ = ("name", "project_dir", "dataset_path", "models_dir", "score_quantile", "horizon_bars")

    name: str
    project_dir: Path
    dataset_path: Path
    models_dir: Path
    score_quantile: float
    horizon_bars: int


@dataclass(frozen=True)
class FrozenArtifact:
    __slots__ = (
        "config",
        "model_path",
        "metadata_path",
        "dataset_path",
        "relative_model_path",
        "relative_dataset_path",
        "threshold",
        "feature_columns",
        "dataset_sha256",
        "dataset_size_bytes",
        "best_iteration",
    )

    config: FrozenConfig
    model_path: Path
    metadata_path: Path
    dataset_path: Path
    relative_model_path: str
    relative_dataset_path: str
    threshold: float
    feature_columns: tuple[str, ...]
    dataset_sha256: str
    dataset_size_bytes: int
    best_iteration: int


class FrozenArtifactError(RuntimeError):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def default_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """YOLO-candidate mainline freeze (judgment trained on judgment_yolo_swap)."""
    return FrozenConfig(
        name=DEFAULT_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "judgment_yolo_swap.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
    )


def rules_legacy_config(project_dir: Path = PROJECT_DIR) -> FrozenConfig:
    """Pre-cutover rule-scan freeze (rollback only)."""
    return FrozenConfig(
        name=LEGACY_RULES_CONFIG_NAME,
        project_dir=project_dir,
        dataset_path=project_dir / "data" / "swap_replication" / "swap_tp5_sl2.csv",
        models_dir=project_dir / "models",
        score_quantile=DEFAULT_SCORE_QUANTILE,
        horizon_bars=DEFAULT_HORIZON_BARS,
    )


DEFAULT_FROZEN_CONFIG: Final = default_config()


def latest_artifact(config: FrozenConfig = DEFAULT_FROZEN_CONFIG) -> FrozenArtifact | None:
    # date-suffix only: frozen_{name}_YYYYMMDD.json -- a greedy * here once
    # matched a different config (…_ma206_…) and crashed the dashboard
    pattern = re.compile(rf"^frozen_{re.escape(config.name)}_\d{{8}}\.json$")
    metadata_paths = sorted(
        p for p in config.models_dir.glob(f"frozen_{config.name}_*.json")
        if pattern.match(p.name))
    for path in reversed(metadata_paths):  # newest valid wins; corrupt ones skip
        try:
            return load_artifact(config, path)
        except FrozenArtifactError as exc:
            print(f"frozen: skipping {path.name}: {exc}")
    return None


def load_artifact(config: FrozenConfig, metadata_path: Path) -> FrozenArtifact:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    feature_columns = tuple(metadata["feature_columns"])
    if feature_columns != tuple(FEATURE_COLUMNS):
        raise FrozenArtifactError(metadata_path, "feature list does not match current FEATURE_COLUMNS")
    model_path = _project_path(config, metadata["model_path"])
    if not model_path.exists():
        raise FrozenArtifactError(metadata_path, "model file is missing")
    dataset_path = _project_path(config, metadata["dataset_path"])
    return FrozenArtifact(
        config=config,
        model_path=model_path,
        metadata_path=metadata_path,
        dataset_path=dataset_path,
        relative_model_path=str(metadata["model_path"]),
        relative_dataset_path=str(metadata["dataset_path"]),
        threshold=float(metadata["threshold_val_q90"]),
        feature_columns=feature_columns,
        dataset_sha256=str(metadata["dataset_sha256"]),
        dataset_size_bytes=int(metadata["dataset_size_bytes"]),
        best_iteration=int(metadata["best_iteration"]),
    )


def train_frozen_artifact(config: FrozenConfig, artifact_date: str) -> FrozenArtifact:
    config.models_dir.mkdir(parents=True, exist_ok=True)
    train, val, _ = load_splits(config.dataset_path, horizon_bars=config.horizon_bars)
    model = train_model(train, val)
    best_iteration = int(model.best_iteration or model.current_iteration())
    val_scores = model.predict(val[FEATURE_COLUMNS], num_iteration=best_iteration)
    threshold = float(np.quantile(val_scores, config.score_quantile))

    stem = f"frozen_{config.name}_{artifact_date}"
    model_path = config.models_dir / f"{stem}.txt"
    metadata_path = config.models_dir / f"{stem}.json"
    model.save_model(str(model_path), num_iteration=best_iteration)
    metadata = {
        "artifact_version": 1,
        "config": config.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_path": _relative_path(config, model_path),
        "dataset_path": _relative_path(config, config.dataset_path),
        "dataset_sha256": file_sha256(config.dataset_path),
        "dataset_size_bytes": config.dataset_path.stat().st_size,
        "threshold_val_q90": threshold,
        "score_quantile": config.score_quantile,
        "feature_columns": list(FEATURE_COLUMNS),
        "best_iteration": best_iteration,
        "splits": {
            "train": _split_summary(train),
            "val": _split_summary(val),
        },
        "holdout_policy": "holdout excluded from training and threshold selection; not evaluated",
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_artifact(config, metadata_path)


def score_with_artifact(artifact: FrozenArtifact) -> tuple[pd.DataFrame, float]:
    model = lgb.Booster(model_file=str(artifact.model_path))
    full = pd.read_csv(artifact.dataset_path, parse_dates=["signal_time"])
    full["score"] = model.predict(full[list(artifact.feature_columns)], num_iteration=artifact.best_iteration)
    full["entry_time"] = full["signal_time"] + BAR
    full["exit_time"] = full["entry_time"] + full["exit_offset"] * BAR
    return full.sort_values(["entry_time", "score"], ascending=[True, False]), artifact.threshold


def cache_metadata(threshold: float, artifact: FrozenArtifact | None) -> ScoreCacheMetadata:
    metadata: ScoreCacheMetadata = {"threshold": threshold}
    if artifact is not None:
        metadata["model_path"] = artifact.relative_model_path
        metadata["dataset_path"] = artifact.relative_dataset_path
        metadata["dataset_sha256"] = artifact.dataset_sha256
    return metadata


def cache_matches_artifact(
    metadata: Mapping[str, str | float],
    artifact: FrozenArtifact | None,
) -> bool:
    if artifact is None:
        return "model_path" not in metadata
    return (
        metadata.get("model_path") == artifact.relative_model_path
        and metadata.get("dataset_path") == artifact.relative_dataset_path
        and metadata.get("dataset_sha256") == artifact.dataset_sha256
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_path(config: FrozenConfig, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return config.project_dir / path


def _relative_path(config: FrozenConfig, path: Path) -> str:
    return path.relative_to(config.project_dir).as_posix()


def _split_summary(frame: pd.DataFrame) -> dict[str, int | list[str]]:
    return {
        "n": int(len(frame)),
        "range": [str(frame["signal_time"].min()), str(frame["signal_time"].max())],
    }
