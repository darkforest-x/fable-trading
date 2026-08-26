#!/usr/bin/env python3
"""Evaluate the frozen 960 and 1280 t-3 weights on a 2x2 imgsz grid.

The script reads only the immutable pre-holdout ``ma_launch_t3_10000_v1``
images and labels. It changes neither weights nor thresholds, and it never
reads OHLCV. The grid separates the resolution used to train a weight from the
resolution used for validation so a native-resolution comparison is not
mistaken for a pure training-resolution effect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MANIFEST_SHA256 = (
    "dd55246938b03c4b2013d159cdfee94b4e9db56ecb298ad30145eb5d1bc2bc3a"
)
EXPECTED_VAL_IMAGES = 2940
EXPECTED_VAL_INSTANCES = {"dense_long": 822, "dense_short": 648}


class ResolutionGridError(ValueError):
    """Fail-closed resolution-grid contract error."""


def sha256_file(path: Path) -> str:
    """Hash one immutable input or result artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_weight(path: Path, expected_sha256: str) -> str:
    """Require an explicitly pinned weight before loading it."""

    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ResolutionGridError(
            f"weight hash drifted for {path}: {actual} != {expected_sha256}"
        )
    return actual


def metrics_payload(metrics: Any) -> dict[str, Any]:
    """Project Ultralytics ``DetMetrics`` into stable JSON fields."""

    names = {int(key): str(value) for key, value in metrics.names.items()}
    if names != {0: "dense_long", 1: "dense_short"}:
        raise ResolutionGridError(f"unexpected class names: {names}")
    overall_raw: Mapping[str, Any] = metrics.results_dict
    key_map = {
        "metrics/precision(B)": "precision",
        "metrics/recall(B)": "recall",
        "metrics/mAP50(B)": "map50",
        "metrics/mAP50-95(B)": "map50_95",
        "fitness": "fitness",
    }
    missing = set(key_map) - set(overall_raw)
    if missing:
        raise ResolutionGridError(f"validation metrics missing: {sorted(missing)}")
    overall = {
        target: float(overall_raw[source]) for source, target in key_map.items()
    }
    per_class: dict[str, dict[str, float]] = {}
    for class_id, class_name in names.items():
        precision, recall, map50, map50_95 = metrics.box.class_result(class_id)
        per_class[class_name] = {
            "precision": float(precision),
            "recall": float(recall),
            "map50": float(map50),
            "map50_95": float(map50_95),
        }
    return {"overall": overall, "per_class": per_class}


def validate_dataset(dataset: Path) -> tuple[Path, Path]:
    """Bind evaluation to the exact pre-holdout val manifest and YAML."""

    manifest = dataset / "manifest.jsonl"
    data_yaml = dataset / "data.yaml"
    if sha256_file(manifest) != EXPECTED_MANIFEST_SHA256:
        raise ResolutionGridError("dataset manifest hash drifted")
    rows = [json.loads(line) for line in manifest.read_text().splitlines() if line]
    val = [row for row in rows if row["split"] == "val"]
    if len(val) != EXPECTED_VAL_IMAGES:
        raise ResolutionGridError(f"val image count drifted: {len(val)}")
    instances = {
        name: sum(row.get("class_name") == name for row in val)
        for name in EXPECTED_VAL_INSTANCES
    }
    if instances != EXPECTED_VAL_INSTANCES:
        raise ResolutionGridError(f"val instance counts drifted: {instances}")
    if any(not (dataset / row["image_path"]).is_file() for row in val):
        raise ResolutionGridError("val image is missing")
    if any(not (dataset / row["label_path"]).is_file() for row in val):
        raise ResolutionGridError("val label is missing")
    return manifest, data_yaml


def evaluate(
    *,
    baseline_weights: Path,
    baseline_sha256: str,
    treatment_weights: Path,
    treatment_sha256: str,
    dataset: Path,
    out: Path,
    device: str,
    batch: int,
) -> dict[str, Any]:
    """Run the preregistered 2x2 weight x inference-resolution grid."""

    manifest, data_yaml = validate_dataset(dataset)
    weights = {
        "trained_960": {
            "path": baseline_weights,
            "sha256": validate_weight(baseline_weights, baseline_sha256),
            "training_imgsz": 960,
        },
        "trained_1280": {
            "path": treatment_weights,
            "sha256": validate_weight(treatment_weights, treatment_sha256),
            "training_imgsz": 1280,
        },
    }

    import numpy as np
    import torch
    import ultralytics
    from ultralytics import YOLO

    cells: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="t3_resolution_grid_") as temp_dir:
        temp = Path(temp_dir)
        for weight_name, weight in weights.items():
            model = YOLO(str(weight["path"]))
            if {int(key): str(value) for key, value in model.names.items()} != {
                0: "dense_long",
                1: "dense_short",
            }:
                raise ResolutionGridError(f"unexpected model classes: {weight_name}")
            for inference_imgsz in (960, 1280):
                metrics = model.val(
                    data=str(data_yaml),
                    imgsz=inference_imgsz,
                    batch=batch,
                    device=device,
                    workers=2,
                    rect=True,
                    conf=0.001,
                    iou=0.7,
                    plots=False,
                    save_json=False,
                    verbose=False,
                    project=str(temp),
                    name=f"{weight_name}_eval_{inference_imgsz}",
                )
                cells.append(
                    {
                        "weight": weight_name,
                        "weight_sha256": weight["sha256"],
                        "training_imgsz": weight["training_imgsz"],
                        "inference_imgsz": inference_imgsz,
                        **metrics_payload(metrics),
                    }
                )

    by_key = {
        (int(cell["training_imgsz"]), int(cell["inference_imgsz"])): cell
        for cell in cells
    }
    baseline_native = by_key[(960, 960)]["overall"]
    treatment_native = by_key[(1280, 1280)]["overall"]
    payload = {
        "experiment_id": "exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1",
        "dataset": str(dataset),
        "manifest_sha256": sha256_file(manifest),
        "val_images": EXPECTED_VAL_IMAGES,
        "val_instances": EXPECTED_VAL_INSTANCES,
        "weights": {
            name: {
                "path": str(row["path"]),
                "sha256": row["sha256"],
                "training_imgsz": row["training_imgsz"],
            }
            for name, row in weights.items()
        },
        "cells": cells,
        "native_treatment_minus_baseline": {
            key: float(treatment_native[key]) - float(baseline_native[key])
            for key in ("precision", "recall", "map50", "map50_95")
        },
        "evaluation_contract": {
            "confidence": 0.001,
            "iou": 0.7,
            "rect": True,
            "threshold_tuned": False,
            "weight_or_dataset_changed": False,
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "numpy": np.__version__,
            "device": device,
            "batch": batch,
        },
        "holdout_consumed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "production_eligible": False,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-weights", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--treatment-weights", type=Path, required=True)
    parser.add_argument("--treatment-sha256", required=True)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "datasets" / "ma_launch_t3_10000_v1"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=8)
    args = parser.parse_args()
    payload = evaluate(
        baseline_weights=args.baseline_weights.resolve(),
        baseline_sha256=args.baseline_sha256,
        treatment_weights=args.treatment_weights.resolve(),
        treatment_sha256=args.treatment_sha256,
        dataset=args.dataset.resolve(),
        out=args.out.resolve(),
        device=args.device,
        batch=args.batch,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
