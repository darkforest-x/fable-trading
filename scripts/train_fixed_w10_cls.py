#!/usr/bin/env python3
"""W10 SIGNAL/NO_SIGNAL classifier on RTX 3060.

Reuses the eth3m v2 classification recipe: official yolo11n-cls, white
letterbox to 960, full-frame tensor (no center-crop), every temporal/color
augmentation off. Source charts are 1280x742; a square crop would drop the
right-edge confirmation bar. This file is self-contained so it can run from
C:/fable without importing yoyo or fable src.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


class WhiteLetterbox:
    """Pad with renderer white, keep aspect, no crop."""

    def __init__(self, size: int, fill: int = 255):
        self.size = int(size)
        self.fill = fill

    def __call__(self, img: Image.Image) -> Image.Image:
        w, h = img.size
        scale = self.size / max(w, h)
        nw = max(1, round(w * scale))
        nh = max(1, round(h * scale))
        img = img.resize((nw, nh), Image.BILINEAR)
        canvas = Image.new("RGB", (self.size, self.size), (self.fill, self.fill, self.fill))
        if img.mode != "RGB":
            img = img.convert("RGB")
        canvas.paste(img, ((self.size - nw) // 2, (self.size - nh) // 2))
        return canvas


def full_frame_transforms(size: int):
    import torchvision.transforms as transforms

    # Match eth3m v2: letterbox to square then ToTensor. No CenterCrop, no ImageNet
    # Normalize — that run on this same 3060 used ToTensor only.
    return transforms.Compose(
        [
            WhiteLetterbox(size),
            transforms.ToTensor(),
        ]
    )


def trainer_class():
    from ultralytics.models.yolo.classify.train import ClassificationTrainer

    class FullFrameClassificationTrainer(ClassificationTrainer):
        def build_dataset(self, img_path: str, mode: str = "train", batch=None):
            dataset = super().build_dataset(img_path, mode, batch)
            dataset.torch_transforms = full_frame_transforms(int(self.args.imgsz))
            return dataset

    return FullFrameClassificationTrainer


def count_split(root: Path, split: str) -> dict[str, int]:
    out = {}
    for lab in ("SIGNAL", "NO_SIGNAL"):
        d = root / split / lab
        out[lab] = len(list(d.glob("*.png"))) if d.is_dir() else 0
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--name", default="fixed_w10_core4_confirm1_v1_cls")
    parser.add_argument("--project", default=r"C:/fable/runs/classify")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()

    data = Path(args.data)
    model_path = Path(args.model)
    if not data.is_dir():
        raise SystemExit(f"missing dataset: {data}")
    if not model_path.is_file():
        raise SystemExit(f"missing model: {model_path}")
    train_c = count_split(data, "train")
    val_c = count_split(data, "val")
    if train_c["SIGNAL"] < 1 or train_c["NO_SIGNAL"] < 1:
        raise SystemExit(f"bad train counts: {train_c}")
    if val_c["SIGNAL"] < 1 or val_c["NO_SIGNAL"] < 1:
        raise SystemExit(f"bad val counts: {val_c}")

    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        raise SystemExit("CUDA not available")
    gpu = torch.cuda.get_device_name(0)
    print(
        json.dumps(
            {
                "run": args.name,
                "gpu": gpu,
                "cuda": True,
                "train": train_c,
                "val": val_c,
                "model": str(model_path),
                "data": str(data),
                "imgsz": args.imgsz,
                "batch": args.batch,
                "epochs": args.epochs,
                "patience": args.patience,
                "seed": args.seed,
                "augs": "all_off",
                "transform": "white_letterbox_full_frame",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    kwargs = dict(
        data=str(data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,
        device=str(args.device),
        workers=args.workers,
        cache=False,
        project=str(args.project),
        name=args.name,
        exist_ok=False,
        plots=True,
        seed=args.seed,
        deterministic=True,
        optimizer="AdamW",
        lr0=1e-4,
        lrf=0.01,
        warmup_epochs=0.5,
        fliplr=0.0,
        flipud=0.0,
        hsv_h=0.0,
        hsv_s=0.0,
        hsv_v=0.0,
        scale=0.0,
        translate=0.0,
        erasing=0.0,
        auto_augment=None,
        degrees=0.0,
        shear=0.0,
        perspective=0.0,
        bgr=0.0,
        mosaic=0.0,
        mixup=0.0,
        cutmix=0.0,
        copy_paste=0.0,
        amp=True,
        val=True,
    )
    model = YOLO(str(model_path), task="classify")
    result = model.train(trainer=trainer_class(), **kwargs)
    print(result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
