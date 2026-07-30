from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.detection.train_direction_classifier import (
    DirectionDatasetError,
    DirectionTrainConfig,
    build_train_options,
)


def _classification_dataset(root: Path) -> Path:
    for split in ("train", "val"):
        for class_name in ("long", "no_trade", "short"):
            (root / split / class_name).mkdir(parents=True, exist_ok=True)
    (root / "manifest.csv").write_text("source,symbol,signal_time\n", encoding="utf-8")
    (root / "dataset_summary.json").write_text(
        json.dumps({"image_size": [640, 640]}),
        encoding="utf-8",
    )
    return root


def test_build_train_options_freezes_causal_classification_recipe(tmp_path: Path) -> None:
    dataset = _classification_dataset(tmp_path / "direction")

    options = build_train_options(
        DirectionTrainConfig(dataset=dataset, device="cpu", name="direction_fixed")
    )

    assert options["data"] == str(dataset.resolve())
    assert options["epochs"] == 20
    assert options["imgsz"] == 320
    assert options["batch"] == 32
    assert options["patience"] == 8
    assert options["seed"] == 42
    assert options["device"] == "cpu"
    assert options["project"] == "runs/classify"
    assert options["name"] == "direction_fixed"
    assert options["fliplr"] == 0.0
    assert options["flipud"] == 0.0
    assert options["hsv_h"] == 0.0
    assert options["hsv_s"] == 0.0
    assert options["hsv_v"] == 0.0
    assert options["scale"] == 0.0
    assert options["erasing"] == 0.0
    assert options["auto_augment"] is None


def test_build_train_options_rejects_non_square_dataset(tmp_path: Path) -> None:
    dataset = _classification_dataset(tmp_path / "direction")
    (dataset / "dataset_summary.json").write_text(
        json.dumps({"image_size": [742, 1280]}),
        encoding="utf-8",
    )

    with pytest.raises(DirectionDatasetError, match="square"):
        build_train_options(DirectionTrainConfig(dataset=dataset, device="cpu"))


def test_build_train_options_rejects_missing_class_folder(tmp_path: Path) -> None:
    dataset = _classification_dataset(tmp_path / "direction")
    (dataset / "val" / "short").rmdir()

    with pytest.raises(DirectionDatasetError, match="classes"):
        build_train_options(DirectionTrainConfig(dataset=dataset, device="cpu"))
