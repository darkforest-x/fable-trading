"""Train the single fixed causal MA206 long/short/no-trade classifier.

The input dataset must be square so Ultralytics classification transforms keep
the complete time axis, including the signal bar at the right edge. This
challenger never reads holdout data or changes the active detection model.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

from src.detection.train import pick_device

MODEL: Final = "yolo11n-cls.pt"
EXPECTED_CLASSES: Final = frozenset({"long", "no_trade", "short"})
EXPECTED_IMAGE_SIZE: Final = (640, 640)


class DirectionDatasetError(RuntimeError):
    """Raised before YOLO starts when the causal dataset contract is invalid."""


@dataclass(frozen=True)
class DirectionTrainConfig:
    dataset: Path
    device: str | None = None
    name: str = "ma206_direction_causal_yolo11n"


class DirectionTrainOptions(TypedDict):
    data: str
    epochs: int
    imgsz: int
    batch: int
    patience: int
    device: str
    workers: int
    project: str
    name: str
    exist_ok: bool
    plots: bool
    seed: int
    deterministic: bool
    fraction: float
    fliplr: float
    flipud: float
    mosaic: float
    mixup: float
    copy_paste: float
    hsv_h: float
    hsv_s: float
    hsv_v: float
    translate: float
    scale: float
    degrees: float
    shear: float
    perspective: float
    erasing: float
    auto_augment: None


def _validate_dataset(dataset: Path) -> Path:
    resolved = dataset.resolve()
    if not resolved.is_dir():
        raise DirectionDatasetError(f"direction dataset is missing: {resolved}")
    for split in ("train", "val"):
        split_dir = resolved / split
        classes = {path.name for path in split_dir.iterdir() if path.is_dir()} if split_dir.is_dir() else set()
        if classes != EXPECTED_CLASSES:
            raise DirectionDatasetError(
                f"{split} classes must be {sorted(EXPECTED_CLASSES)}, observed {sorted(classes)}"
            )
    manifest_path = resolved / "manifest.csv"
    summary_path = resolved / "dataset_summary.json"
    if not manifest_path.is_file() or not summary_path.is_file():
        raise DirectionDatasetError("direction dataset requires manifest.csv and dataset_summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    observed_size = summary.get("image_size")
    if not isinstance(observed_size, list) or tuple(observed_size) != EXPECTED_IMAGE_SIZE:
        raise DirectionDatasetError(
            f"direction images must be square {EXPECTED_IMAGE_SIZE}, observed {summary.get('image_size')}"
        )
    return resolved


def build_train_options(config: DirectionTrainConfig) -> DirectionTrainOptions:
    """Return the predeclared one-run recipe after validating the dataset."""
    dataset = _validate_dataset(config.dataset)
    return {
        "data": str(dataset),
        "epochs": 20,
        "imgsz": 320,
        "batch": 32,
        "patience": 8,
        "device": config.device or pick_device(),
        "workers": 2,
        "project": "runs/classify",
        "name": config.name,
        "exist_ok": False,
        "plots": True,
        "seed": 42,
        "deterministic": True,
        "fraction": 1.0,
        "fliplr": 0.0,
        "flipud": 0.0,
        "mosaic": 0.0,
        "mixup": 0.0,
        "copy_paste": 0.0,
        "hsv_h": 0.0,
        "hsv_s": 0.0,
        "hsv_v": 0.0,
        "translate": 0.0,
        "scale": 0.0,
        "degrees": 0.0,
        "shear": 0.0,
        "perspective": 0.0,
        "erasing": 0.0,
        "auto_augment": None,
    }


def run_training(config: DirectionTrainConfig) -> None:
    """Start exactly one classification run after all local safety checks."""
    from ultralytics import YOLO

    options = build_train_options(config)
    output = Path(options["project"]) / options["name"]
    if output.exists():
        raise FileExistsError(f"direction training output already exists: {output}")
    print(YOLO(MODEL).train(**options))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("datasets/ma206_direction_causal_v1"))
    parser.add_argument("--device", default=None)
    parser.add_argument("--name", default="ma206_direction_causal_yolo11n")
    args = parser.parse_args()
    run_training(DirectionTrainConfig(dataset=args.data, device=args.device, name=args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
