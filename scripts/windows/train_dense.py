r"""Standalone dense-MA-cluster YOLO trainer for the Windows RTX 3060 box.

This is the tracked source of truth for ``C:\fable\train_dense.py``. It keeps
the fine-tune recipe used by the legacy Windows trainer while enforcing the
current project contract: CUDA is mandatory and every augmentation that can
change time direction or candle colour semantics is disabled.

The Windows box is intentionally not a repository checkout. The Mac syncs this
file, a dataset, and a base weight before a permitted training run. Training is
still subject to the phase gates in HANDOFF.md and ROADMAP.md.
"""
from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Ultralytics' optimizer='auto' computes lr0 = 0.002 for one class and pairs it
# with AdamW. That from-scratch rate destroyed converged chain detectors when
# warmup ended. See docs/learnings/ultralytics-auto-lr-destroys-finetune.md.
FINETUNE_OPT = dict(
    optimizer="AdamW",
    lr0=1e-4,
    lrf=0.01,
    warmup_epochs=0.5,
)

# Chart axes and candle colours carry semantics. Keep every time/colour-changing
# augmentation off; only small geometry jitter remains allowed.
SAFE_AUG = dict(
    fliplr=0.0,
    flipud=0.0,
    mosaic=0.0,
    mixup=0.0,
    copy_paste=0.0,
    hsv_h=0.0,
    hsv_s=0.0,
    hsv_v=0.0,
    translate=0.02,
    scale=0.1,
    degrees=0.0,
    shear=0.0,
    perspective=0.0,
    erasing=0.0,
    auto_augment=None,
)


def write_data_yaml(dataset_dir: Path) -> Path:
    """Write a Windows-local YOLO dataset path.

    Reads no market columns. It only rewrites dataset layout metadata because
    a YAML copied from macOS contains an unusable ``/Users/...`` root.
    """
    yaml_path = dataset_dir / "data.yaml"
    yaml_path.write_text(
        f"path: {dataset_dir.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_cluster\n",
        encoding="utf-8",
    )
    return yaml_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(HERE / "datasets" / "dense_owner_v7"))
    parser.add_argument("--model", default=str(HERE / "base" / "owner_v7_chain.pt"))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8, help="keep 8 for historical comparability")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--patience", type=int, default=10, help="chain ~10, cold start ~20")
    parser.add_argument("--device", default=None, help="default: CUDA device 0")
    parser.add_argument("--name", default="owner_v8_chain")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cache", default="disk", help="false|disk|ram")
    parser.add_argument(
        "--finetune",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use AdamW lr0=1e-4. Default: on unless --model is a yolo11*.pt cold start.",
    )
    parser.add_argument(
        "--min-train",
        type=int,
        default=1000,
        help="refuse a suspiciously small copied train set; lower deliberately for small-data work",
    )
    args = parser.parse_args()

    import torch
    from ultralytics import YOLO

    if args.device is not None:
        device = args.device
    elif torch.cuda.is_available():
        device = "0"
    else:
        raise SystemExit(
            "CUDA not available. Reinstall the pinned CUDA torch build; "
            "this trainer refuses to fall back to CPU."
        )

    gpu_name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f"GPU: {gpu_name}  VRAM: {vram:.1f} GB", flush=True)

    dataset_dir = Path(args.dataset).resolve()
    data_yaml = write_data_yaml(dataset_dir)
    n_train = len(list((dataset_dir / "images" / "train").glob("*.png")))
    n_val = len(list((dataset_dir / "images" / "val").glob("*.png")))
    print(f"dataset: {dataset_dir}  train={n_train} val={n_val}", flush=True)
    if n_train < args.min_train:
        raise SystemExit(
            f"train set too small ({n_train} < {args.min_train}); "
            "did the copy finish? Use --min-train only for a preregistered small-data run."
        )

    cache = {"false": False, "0": False, "no": False}.get(
        str(args.cache).lower(), args.cache
    )
    finetune = args.finetune
    if finetune is None:
        finetune = not Path(args.model).name.startswith("yolo")
    opt = dict(FINETUNE_OPT) if finetune else {}
    print(f"[train] finetune={finetune} opt={opt or 'auto (lr0=0.002)'}", flush=True)

    model = YOLO(args.model)
    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        seed=args.seed,
        patience=args.patience,
        device=device,
        workers=args.workers,
        cache=cache,
        project="runs/detect",
        name=args.name,
        exist_ok=True,
        plots=False,
        rect=True,
        **opt,
        **SAFE_AUG,
    )
    print(results)
    best = Path(model.trainer.best).resolve()
    print(f"\n{'=' * 60}\nweights: {best}\n{'=' * 60}", flush=True)


if __name__ == "__main__":
    # Windows spawns dataloader workers as new processes; this guard prevents
    # every worker from re-entering main().
    main()
