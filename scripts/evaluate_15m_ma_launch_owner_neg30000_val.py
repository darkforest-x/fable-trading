#!/usr/bin/env python3
"""Measure fixed-threshold false fires on the immutable owner-v2 val negatives.

The script reads only the exact pre-holdout PNGs and empty YOLO labels exposed
by the frozen chronological val split. It never reads OHLCV, tunes a threshold,
changes a weight, or touches runtime state. At confidence 0.25 it reports false
fire rate separately for the hard dense-rope and easy background negatives.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MANIFEST_SHA256 = (
    "6e601034ab15765a74b788cc6d094e9326c3044c1fb615c908ef9de897d6e0af"
)
EXPECTED_VAL_COUNTS = {
    "positive": 1815,
    "negative": 5445,
    "negative_hard": 3572,
    "negative_easy": 1873,
}


class NegativeValError(ValueError):
    """Fail-closed immutable validation contract error."""


def sha256_file(path: Path) -> str:
    """Return one file's SHA-256 without loading it wholly into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_negative_rows(dataset: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Validate the exact manifest/count/empty-label contract and return val negatives."""

    manifest = dataset / "manifest.jsonl"
    if not manifest.is_file() or sha256_file(manifest) != EXPECTED_MANIFEST_SHA256:
        raise NegativeValError("owner-v2 manifest identity drifted")
    rows = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line
    ]
    val = [row for row in rows if row.get("split") == "val"]
    negatives = [row for row in val if row.get("sample_kind") == "negative"]
    actual = {
        "positive": sum(row.get("sample_kind") == "positive" for row in val),
        "negative": len(negatives),
        "negative_hard": sum(row.get("negative_kind") == "hard" for row in negatives),
        "negative_easy": sum(row.get("negative_kind") == "easy" for row in negatives),
    }
    if actual != EXPECTED_VAL_COUNTS:
        raise NegativeValError(f"validation composition drifted: {actual}")
    for row in negatives:
        image = dataset / str(row["image_path"])
        label = dataset / str(row["label_path"])
        if not image.is_file() or sha256_file(image) != row["image_sha256"]:
            raise NegativeValError(f"negative image drifted: {row['sample_id']}")
        if not label.is_file() or label.read_bytes() != b"":
            raise NegativeValError(f"negative label is not byte-empty: {row['sample_id']}")
    return manifest, sorted(negatives, key=lambda row: str(row["sample_id"]))


def summarize_fires(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate prediction receipts for one negative subset."""

    values = list(rows)
    fired = [row for row in values if int(row["boxes"]) > 0]
    boxes = sum(int(row["boxes"]) for row in values)
    classes: Counter[str] = Counter()
    confidences: list[float] = []
    for row in fired:
        classes.update(str(value) for value in row["classes"])
        confidences.extend(float(value) for value in row["confidences"])
    conf = np.asarray(confidences, dtype=float)
    return {
        "images": len(values),
        "fired_images": len(fired),
        "fire_rate": len(fired) / len(values),
        "boxes": boxes,
        "false_boxes_per_1000_images": boxes / len(values) * 1000.0,
        "class_box_counts": dict(sorted(classes.items())),
        "confidence": {
            "min": None if not len(conf) else float(conf.min()),
            "median": None if not len(conf) else float(np.median(conf)),
            "p90": None if not len(conf) else float(np.quantile(conf, 0.9)),
            "max": None if not len(conf) else float(conf.max()),
        },
        "top_fired_samples": sorted(
            (
                {
                    "sample_id": str(row["sample_id"]),
                    "negative_kind": str(row["negative_kind"]),
                    "boxes": int(row["boxes"]),
                    "max_confidence": max(float(value) for value in row["confidences"]),
                }
                for row in fired
            ),
            key=lambda row: (-row["max_confidence"], row["sample_id"]),
        )[:100],
    }


def evaluate(
    *,
    weights: Path,
    expected_weights_sha256: str,
    dataset: Path,
    output: Path,
    experiment_id: str,
    device: str,
    batch: int,
    imgsz: int,
    confidence: float,
) -> dict[str, Any]:
    """Run one fixed, untuned threshold over every immutable val negative."""

    if output.exists():
        raise FileExistsError(f"refusing to overwrite evaluation: {output}")
    if sha256_file(weights) != expected_weights_sha256:
        raise NegativeValError("weight identity drifted")
    manifest, rows = load_negative_rows(dataset)

    import torch
    import ultralytics
    from ultralytics import YOLO

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != {0: "dense_long", 1: "dense_short"}:
        raise NegativeValError(f"unexpected model classes: {names}")

    prediction_rows: list[dict[str, Any]] = []
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        results = model.predict(
            source=[str(dataset / row["image_path"]) for row in chunk],
            imgsz=imgsz,
            conf=confidence,
            iou=0.7,
            batch=batch,
            device=device,
            stream=False,
            verbose=False,
            save=False,
        )
        if len(results) != len(chunk):
            raise NegativeValError("prediction result count drifted")
        for row, result in zip(chunk, results):
            boxes = result.boxes
            count = 0 if boxes is None else len(boxes)
            prediction_rows.append(
                {
                    "sample_id": row["sample_id"],
                    "negative_kind": row["negative_kind"],
                    "boxes": count,
                    "classes": []
                    if not count
                    else boxes.cls.detach().cpu().numpy().astype(int).tolist(),
                    "confidences": []
                    if not count
                    else boxes.conf.detach().cpu().numpy().astype(float).tolist(),
                }
            )

    by_kind: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prediction_rows:
        by_kind[str(row["negative_kind"])].append(row)
    payload = {
        "experiment_id": experiment_id,
        "weights_path": str(weights),
        "weights_sha256": sha256_file(weights),
        "dataset": str(dataset),
        "manifest_sha256": sha256_file(manifest),
        "validation_counts": EXPECTED_VAL_COUNTS,
        "imgsz": imgsz,
        "confidence_threshold": confidence,
        "iou": 0.7,
        "threshold_tuned": False,
        "all_negatives": summarize_fires(prediction_rows),
        "hard_negatives": summarize_fires(by_kind["hard"]),
        "easy_negatives": summarize_fires(by_kind["easy"]),
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
        "deployed": False,
        "production_eligible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--weights-sha256", required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "datasets" / "ma_launch_owner_autofill10000_yolo_neg30000_v2",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--experiment-id",
        default="exp-15m-ma-launch-owner-yolo-neg30000-train960-v1",
    )
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--confidence", type=float, default=0.25)
    args = parser.parse_args()
    payload = evaluate(
        weights=args.weights.resolve(),
        expected_weights_sha256=args.weights_sha256,
        dataset=args.dataset.resolve(),
        output=args.output.resolve(),
        experiment_id=args.experiment_id,
        device=args.device,
        batch=args.batch,
        imgsz=args.imgsz,
        confidence=args.confidence,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
