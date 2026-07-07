"""Smoke training for the dense MA-cluster detector.

All augmentations that would break temporal direction (flips/mosaic), spatial
semantics (mosaic/mixup) or red/green candle colors (hsv) are disabled — this
was a confirmed failure mode of the old project (v176-v181).

Usage:
  python -m src.detection.train --data datasets/dense_15m/data.yaml --epochs 30
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from ultralytics import YOLO

# Chart images have a fixed meaning per axis and per color: never flip,
# never mosaic/mix, keep colors intact, allow only tiny geometric jitter.
SAFE_AUG = dict(
    fliplr=0.0,
    flipud=0.0,
    mosaic=0.0,
    mixup=0.0,
    copy_paste=0.0,
    hsv_h=0.0,
    hsv_s=0.05,
    hsv_v=0.05,
    translate=0.02,
    scale=0.1,
    degrees=0.0,
    shear=0.0,
    perspective=0.0,
    erasing=0.0,
    auto_augment=None,
)


def pick_device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="datasets/dense_15m/data.yaml")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--device", default=None)
    parser.add_argument("--name", default="dense_15m_smoke")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint in run dir")
    args = parser.parse_args()

    device = args.device or pick_device()
    model = YOLO(args.model)
    results = model.train(
        data=str(Path(args.data).resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,
        device=device,
        workers=2,
        project="runs/detect",
        name=args.name,
        exist_ok=True,
        plots=True,
        rect=True,
        resume=args.resume,
        **SAFE_AUG,
    )
    print(results)


if __name__ == "__main__":
    main()
