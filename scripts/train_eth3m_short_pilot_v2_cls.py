#!/usr/bin/env python3
"""Train the preregistered ETH 3m v2 full-frame diagnostic classifier."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.detection.eth3m_v2_classification import (
    IMAGE_SIZE,
    OUTPUT_DATASET,
    PREREG,
    sha256,
    validate_authorization,
    verify_prepared,
)

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT / "models/yolo11n-cls.pt"
PROJECT_RUNS = PROJECT / "runs/classify"


def full_frame_transforms(size: int):
    """Return an explicit resize-only transform with no random/center crop."""
    import torchvision.transforms as transforms

    return transforms.Compose(
        [
            transforms.Resize(
                (size, size),
                interpolation=transforms.InterpolationMode.BILINEAR,
                antialias=True,
            ),
            transforms.ToTensor(),
        ]
    )


def trainer_class():
    """Build lazily so preflight/tests do not import torch or Ultralytics."""
    from ultralytics.models.yolo.classify.train import ClassificationTrainer

    class FullFrameClassificationTrainer(ClassificationTrainer):
        """Use every square pixel in both train and val; never crop the tip."""

        def build_dataset(self, img_path: str, mode: str = "train", batch=None):
            dataset = super().build_dataset(img_path, mode, batch)
            dataset.torch_transforms = full_frame_transforms(int(self.args.imgsz))
            return dataset

    return FullFrameClassificationTrainer


def train_kwargs(prereg: dict, data: Path, name: str) -> dict:
    recipe = prereg["training"]
    aug = recipe["augmentations"]
    return {
        "data": str(data.resolve()),
        "epochs": recipe["epochs"],
        "imgsz": recipe["imgsz"],
        "batch": recipe["batch"],
        "patience": recipe["patience"],
        "device": str(recipe["device"]),
        "workers": recipe["workers"],
        "cache": recipe["cache"],
        "project": str(PROJECT_RUNS),
        "name": name,
        "exist_ok": False,
        "plots": True,
        "seed": recipe["seed"],
        "deterministic": recipe["deterministic"],
        "optimizer": recipe["optimizer"],
        "lr0": recipe["lr0"],
        "lrf": recipe["lrf"],
        "warmup_epochs": recipe["warmup_epochs"],
        "fliplr": aug["fliplr"],
        "flipud": aug["flipud"],
        "hsv_h": aug["hsv_h"],
        "hsv_s": aug["hsv_s"],
        "hsv_v": aug["hsv_v"],
        "scale": aug["scale"],
        "translate": aug["translate"],
        "erasing": aug["erasing"],
        "auto_augment": aug["auto_augment"],
        "degrees": 0.0,
        "shear": 0.0,
        "perspective": 0.0,
        "bgr": 0.0,
        "mosaic": 0.0,
        "mixup": 0.0,
        "cutmix": 0.0,
        "copy_paste": 0.0,
        "amp": True,
        "val": True,
    }


def preflight(data: Path, model: Path, prereg_path: Path, name: str) -> tuple[dict, dict]:
    prereg = validate_authorization(prereg_path)
    meta = verify_prepared(data)
    if sha256(prereg_path) != meta.get("prereg_sha256"):
        raise ValueError("prepared dataset was built under a different preregistration")
    if model.name != prereg["training"]["model"] or not model.is_file():
        raise ValueError(f"missing preregistered pretrained checkpoint: {model}")
    if sha256(model) != prereg["training"]["model_sha256"]:
        raise ValueError("pretrained checkpoint SHA256 differs from preregistration")
    if name != prereg["training"]["run_name"]:
        raise ValueError("run name differs from preregistration")
    run_dir = PROJECT_RUNS / name
    if run_dir.exists():
        raise FileExistsError(f"refusing to overwrite or auto-increment existing run: {run_dir}")
    return prereg, meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=OUTPUT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--prereg", type=Path, default=PREREG)
    parser.add_argument("--name", default=None)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    prereg_data = validate_authorization(args.prereg)
    name = args.name or prereg_data["training"]["run_name"]
    prereg_data, meta = preflight(args.data, args.model, args.prereg, name)
    kwargs = train_kwargs(prereg_data, args.data, name)
    print(
        json.dumps(
            {
                "status": "preflight_passed",
                "run": name,
                "model_sha256": sha256(args.model),
                "training_images": meta["total"],
                "kwargs": kwargs,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.preflight_only:
        return

    from ultralytics import YOLO

    model = YOLO(str(args.model), task="classify")
    result = model.train(trainer=trainer_class(), **kwargs)
    print(result, flush=True)


if __name__ == "__main__":
    main()
