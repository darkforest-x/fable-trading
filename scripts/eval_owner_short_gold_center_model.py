#!/usr/bin/env python3
"""Evaluate one Owner-short detector on an explicit frozen YOLO val split.

Inputs are the rendered images and labels named by ``data.yaml``.  The command
does not open raw OHLCV, future outcomes, or the repository holdout.  It writes
a machine-readable receipt containing the exact weight/data hashes, package
versions, dataset counts, evaluation arguments, and detection metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
import ultralytics
import yaml
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one immutable evaluation input."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_dataset(data_yaml: Path) -> tuple[Path, Path, list[Path], list[Path]]:
    """Resolve the val image/label paths named by a YOLO dataset file."""

    payload = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    dataset_root = Path(str(payload["path"]))
    if not dataset_root.is_absolute():
        dataset_root = (data_yaml.parent / dataset_root).resolve()
    val_images = (dataset_root / str(payload["val"])).resolve()
    if not val_images.is_dir():
        raise FileNotFoundError(f"missing val image directory: {val_images}")
    if val_images.name != "val" or val_images.parent.name != "images":
        raise ValueError(f"expected a rendered images/val split, got {val_images}")
    val_labels = val_images.parent.parent / "labels" / "val"
    images = sorted(
        path for path in val_images.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES
    )
    labels = sorted(val_labels.glob("*.txt"))
    if not images or len(images) != len(labels):
        raise ValueError(
            f"invalid frozen val pairing: images={len(images)} labels={len(labels)}"
        )
    missing = [path for path in images if not (val_labels / f"{path.stem}.txt").is_file()]
    if missing:
        raise ValueError(f"missing labels for {len(missing)} val images")
    return dataset_root, val_images, images, labels


def metric_receipt(result: Any) -> dict[str, float]:
    """Extract the four frozen detection metrics from an Ultralytics result."""

    box = result.box
    return {
        "precision": float(box.mp),
        "recall": float(box.mr),
        "map50": float(box.map50),
        "map50_95": float(box.map),
        "fitness": float(result.fitness),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    weights = args.weights.resolve()
    data_yaml = args.data.resolve()
    if not weights.is_file() or not data_yaml.is_file():
        raise FileNotFoundError("weights and data.yaml must both exist")
    dataset_root, val_images, images, labels = resolve_dataset(data_yaml)

    run_dir = args.out.resolve().parent / "mac_val_run"
    result = YOLO(str(weights)).val(
        data=str(data_yaml),
        split="val",
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        conf=0.001,
        iou=0.70,
        rect=True,
        plots=False,
        save_json=False,
        project=str(run_dir.parent),
        name=run_dir.name,
        exist_ok=True,
        verbose=False,
    )
    receipt = {
        "protocol": "owner_short_gold_center_frozen_val_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": "frozen_rendered_val",
        "holdout_read": False,
        "production_eligible": False,
        "weights": str(weights),
        "weights_sha256": sha256_file(weights),
        "data_yaml": str(data_yaml),
        "data_yaml_sha256": sha256_file(data_yaml),
        "dataset_root": str(dataset_root),
        "val_images_dir": str(val_images),
        "val_images": len(images),
        "val_labels": len(labels),
        "nonempty_val_labels": sum(bool(path.read_text(encoding="utf-8").strip()) for path in labels),
        "args": {
            "device": args.device,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "workers": args.workers,
            "conf": 0.001,
            "iou": 0.70,
            "rect": True,
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "numpy": np.__version__,
            "mps_available": bool(torch.backends.mps.is_available()),
        },
        "metrics": metric_receipt(result),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
