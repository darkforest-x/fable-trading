from __future__ import annotations

import json
from pathlib import Path

from src.judgment.frozen import (
    FrozenConfig,
    cache_matches_artifact,
    cache_metadata,
    latest_artifact,
)
from src.judgment.features import FEATURE_COLUMNS


def _write_artifact(project_dir: Path, artifact_date: str, dataset_sha256: str) -> None:
    models_dir = project_dir / "models"
    models_dir.mkdir(exist_ok=True)
    stem = f"frozen_tp5_sl2_swap_{artifact_date}"
    model_path = models_dir / f"{stem}.txt"
    metadata_path = models_dir / f"{stem}.json"
    model_path.write_text("fake model\n", encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_version": 1,
                "config": "tp5_sl2_swap",
                "model_path": f"models/{stem}.txt",
                "dataset_path": "data/swap_replication/swap_tp5_sl2.csv",
                "dataset_sha256": dataset_sha256,
                "dataset_size_bytes": 123,
                "threshold_val_q90": 0.67,
                "score_quantile": 0.9,
                "feature_columns": list(FEATURE_COLUMNS),
                "best_iteration": 42,
            }
        ),
        encoding="utf-8",
    )


def test_latest_artifact_picks_highest_dated_metadata(tmp_path: Path) -> None:
    config = FrozenConfig(
        name="tp5_sl2_swap",
        project_dir=tmp_path,
        dataset_path=tmp_path / "data" / "swap_replication" / "swap_tp5_sl2.csv",
        models_dir=tmp_path / "models",
        score_quantile=0.9,
        horizon_bars=72,
        objective="binary",
    )
    _write_artifact(tmp_path, "20260708", "old")
    _write_artifact(tmp_path, "20260709", "new")

    artifact = latest_artifact(config)

    assert artifact is not None
    assert artifact.relative_model_path == "models/frozen_tp5_sl2_swap_20260709.txt"
    assert artifact.dataset_sha256 == "new"


def test_cache_metadata_must_match_current_artifact(tmp_path: Path) -> None:
    config = FrozenConfig(
        name="tp5_sl2_swap",
        project_dir=tmp_path,
        dataset_path=tmp_path / "data" / "swap_replication" / "swap_tp5_sl2.csv",
        models_dir=tmp_path / "models",
        score_quantile=0.9,
        horizon_bars=72,
        objective="binary",
    )
    _write_artifact(tmp_path, "20260709", "expected")
    artifact = latest_artifact(config)

    assert artifact is not None
    assert cache_matches_artifact(cache_metadata(0.67, artifact), artifact)
    assert not cache_matches_artifact({"threshold": 0.67, "dataset_sha256": "old"}, artifact)
