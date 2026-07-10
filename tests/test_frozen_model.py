from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.judgment.frozen import (
    FrozenArtifactError,
    FrozenConfig,
    cache_matches_artifact,
    cache_metadata,
    latest_artifact,
    read_dataset_before,
)
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import HOLDOUT_START


def _write_artifact(project_dir: Path, artifact_date: str, dataset_sha256: str) -> None:
    models_dir = project_dir / "models"
    models_dir.mkdir(exist_ok=True)
    stem = f"frozen_tp5_sl2_swap_ma206_{artifact_date}"
    model_path = models_dir / f"{stem}.txt"
    metadata_path = models_dir / f"{stem}.json"
    model_path.write_text("fake model\n", encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "artifact_version": 1,
                "config": "tp5_sl2_swap_ma206",
                "model_path": f"models/{stem}.txt",
                "dataset_path": "data/ma206/swap_tp5_sl2_ma206.csv",
                "dataset_sha256": dataset_sha256,
                "dataset_size_bytes": 123,
                "threshold_val_q90": 0.67,
                "score_quantile": 0.9,
                "feature_columns": list(FEATURE_COLUMNS),
                "best_iteration": 42,
                "splits": {
                    "val": {
                        "n": 2,
                        "range": ["2026-03-21 20:00:00+00:00", "2026-05-03 00:00:00+00:00"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_latest_artifact_picks_highest_dated_metadata(tmp_path: Path) -> None:
    config = FrozenConfig(
        name="tp5_sl2_swap_ma206",
        project_dir=tmp_path,
        dataset_path=tmp_path / "data" / "ma206" / "swap_tp5_sl2_ma206.csv",
        models_dir=tmp_path / "models",
        score_quantile=0.9,
        horizon_bars=72,
    )
    _write_artifact(tmp_path, "20260708", "old")
    _write_artifact(tmp_path, "20260709", "new")

    artifact = latest_artifact(config)

    assert artifact is not None
    assert artifact.relative_model_path == "models/frozen_tp5_sl2_swap_ma206_20260709.txt"
    assert artifact.dataset_sha256 == "new"
    assert artifact.val_start == pd.Timestamp("2026-03-21 20:00:00+00:00")


def test_cache_metadata_must_match_current_artifact(tmp_path: Path) -> None:
    config = FrozenConfig(
        name="tp5_sl2_swap_ma206",
        project_dir=tmp_path,
        dataset_path=tmp_path / "data" / "ma206" / "swap_tp5_sl2_ma206.csv",
        models_dir=tmp_path / "models",
        score_quantile=0.9,
        horizon_bars=72,
    )
    _write_artifact(tmp_path, "20260709", "expected")
    artifact = latest_artifact(config)

    assert artifact is not None
    assert cache_matches_artifact(cache_metadata(0.67, artifact), artifact)
    assert not cache_matches_artifact({"threshold": 0.67, "dataset_sha256": "old"}, artifact)


def test_read_dataset_before_excludes_holdout_rows(tmp_path: Path) -> None:
    path = tmp_path / "ordered.csv"
    pd.DataFrame(
        {
            "signal_time": [
                "2026-05-03 23:30:00+00:00",
                "2026-05-03 23:45:00+00:00",
                "2026-05-04 00:00:00+00:00",
                "2026-05-04 00:15:00+00:00",
            ],
            "value": [1, 2, 3, 4],
        }
    ).to_csv(path, index=False)

    safe = read_dataset_before(path, end_before=HOLDOUT_START)

    assert safe["value"].tolist() == [1, 2]
    assert (safe["signal_time"] < HOLDOUT_START).all()


def test_read_dataset_before_rejects_unsorted_input(tmp_path: Path) -> None:
    path = tmp_path / "unsorted.csv"
    pd.DataFrame(
        {
            "signal_time": ["2026-05-03 23:45:00+00:00", "2026-05-03 23:30:00+00:00"],
            "value": [1, 2],
        }
    ).to_csv(path, index=False)

    with pytest.raises(FrozenArtifactError, match="sorted by signal_time"):
        read_dataset_before(path, end_before=HOLDOUT_START)
