"""Smoke / full training for the dense MA-cluster detector.

All augmentations that would break temporal direction (flips/mosaic), spatial
semantics (mosaic/mixup) or red/green candle colors (hsv) are disabled — this
was a confirmed failure mode of the old project (v176-v181).

Speed knobs (accuracy-preserving defaults, 2026-07-15):
  - workers=6 (was 2): M4/MPS data-load bound; does not change optimization
  - cache=disk: second+ epoch skips re-decode; same samples
  - plots=False: less I/O; metrics unchanged
  - imgsz stays 960; SAFE_AUG unchanged (do not re-enable flip/mosaic/hsv)

Patience recipe (caller responsibility):
  - chain / fine-tune from prior best: --epochs 40 --patience 10
  - cold start (yolo11s.pt): --epochs 100 --patience 20

Learning rate (2026-07-16): chain runs auto-select AdamW lr0=1e-4 via FINETUNE_OPT.
Do not go back to optimizer='auto' for chain runs -- it picks lr0=0.002 and blows
the weights apart at epoch 3. Every model before this date was affected.

Usage:
  .venv/bin/python -m src.detection.train --data datasets/dense_15m/data.yaml --epochs 30
"""
from __future__ import annotations

import argparse
from pathlib import Path

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

# Fine-tune recipe. ultralytics' optimizer='auto' computes lr0 = 0.002*5/(4+nc),
# which is 0.002 for our single class, and pairs it with AdamW. That is a
# from-scratch rate: on an already-converged detector it destroys the weights the
# moment warmup finishes ramping up to it.
#
# Measured 2026-07-16 on two independent runs -- owner_v7_chain and
# owner_v7_holdout -- both collapsed at exactly epoch 3, the epoch where warmup
# ends and lr reaches 0.0019:
#     v7_chain     epoch 1 lr=0.000665 mAP50=0.383 -> epoch 3 lr=0.0019 mAP50=0.000
#     v7_holdout   epoch 1 lr=0.000665 mAP50=0.724 -> epoch 3 lr=0.0019 mAP50=0.041
# best.pt was epoch 1 in both, i.e. the base model plus one warmup step. Every
# "chain" model this project shipped was therefore barely trained, and the
# v6 0.595 -> v7 0.625 curve measured warmup noise, not the value of new labels.
# See docs/learnings/ultralytics-auto-lr-destroys-finetune.md
FINETUNE_OPT = dict(
    optimizer="AdamW",
    lr0=1e-4,      # 20x below auto's 0.002; standard for fine-tuning a converged net
    lrf=0.01,
    warmup_epochs=0.5,  # a converged model needs no 3-epoch ramp
)

# Defaults tuned for Apple Silicon (MPS) full runs without changing optim path.
DEFAULT_WORKERS = 6
DEFAULT_CACHE = "disk"  # False | True | "ram" | "disk"


def pick_device() -> str:
    """CUDA > MPS > CPU. CUDA first: an RTX 3060 runs this ~5x faster than M4
    MPS and has none of the MPS hangs that forced CPU fallback in forward.py.
    """
    import torch  # heavy; keep import local so unit tests stay light

    if torch.cuda.is_available():
        return "0"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _parse_cache(raw: str):
    """Map CLI string to ultralytics cache= arg (False | 'ram' | 'disk')."""
    key = raw.strip().lower()
    if key in {"0", "false", "no", "none", "off"}:
        return False
    if key == "ram":
        return "ram"
    if key in {"1", "true", "yes", "on", "disk"}:
        return "disk"
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="datasets/dense_15m/data.yaml")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument(
        "--patience",
        type=int,
        default=10,
        help="early-stop wait; chain fine-tune ~10, cold start ~20",
    )
    parser.add_argument("--device", default=None)
    parser.add_argument("--name", default="dense_15m_smoke")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint in run dir")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"dataloader workers (default {DEFAULT_WORKERS}; was 2)",
    )
    parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE,
        help='ultralytics cache: false|disk|ram (default "disk")',
    )
    parser.add_argument(
        "--plots",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="write train/val plots (default off for speed)",
    )
    parser.add_argument(
        "--finetune",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="AdamW lr0=1e-4 instead of auto's 0.002. Default: on when --model is "
             "a prior run's weights, off for a cold start from yolo11*.pt.",
    )
    args = parser.parse_args()

    from ultralytics import YOLO  # heavy; only needed for actual train

    device = args.device or pick_device()
    cache = _parse_cache(str(args.cache))

    # A chain run starts from a converged detector and must not be hit with the
    # from-scratch lr; a cold start from yolo11*.pt still wants auto. Infer from
    # the base weights so nobody has to remember the flag, but let it be forced.
    finetune = args.finetune
    if finetune is None:
        finetune = not Path(args.model).name.startswith("yolo")
    opt = dict(FINETUNE_OPT) if finetune else {}
    print(f"[train] finetune={finetune} opt={opt or 'auto (lr0=0.002)'}", flush=True)

    model = YOLO(args.model)
    results = model.train(
        data=str(Path(args.data).resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,
        device=device,
        workers=args.workers,
        cache=cache,
        project="runs/detect",
        name=args.name,
        exist_ok=True,
        plots=args.plots,
        rect=True,
        resume=args.resume,
        **opt,
        **SAFE_AUG,
    )
    print(results)


if __name__ == "__main__":
    main()
