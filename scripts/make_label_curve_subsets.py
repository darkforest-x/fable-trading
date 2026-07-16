"""Build train-set subsamples of a dataset to measure the value of more labels.

The question this exists to answer: is the detector limited by how many images
the owner has labelled? Two prior data points suggest not -- v6_coco (3911 imgs)
scored frozen-F1 0.554 and v8_coco (5659 imgs, +45%) scored 0.549 -- but those
ran on different machines (MPS vs CUDA), so the comparison carries a confound.

This produces a proper learning curve on ONE machine from ONE dataset lineage:
train on 25% / 50% / 100% of the labelled images, cold-start each, and score all
three on the frozen eval set. If 50% -> 100% is flat, then 100% -> 145% (what
round7's 3000 tasks would buy) is flat too, and the labelling hours are better
spent elsewhere.

Design choices that matter:
  - Subsample by IMAGE, not by symbol. Labelling rounds add images spread across
    symbols, so dropping random images is the honest analogue of "had we labelled
    less". Dropping whole symbols would instead measure symbol coverage.
  - val stays identical across arms. Only the train set shrinks, so any F1 delta
    is attributable to train size and nothing else.
  - Seeded, so the 25% set is a strict subset of the 50% set: the arms are nested
    like real labelling rounds are, not three unrelated draws.

Usage (runs on whichever box holds the dataset):
  python scripts/make_label_curve_subsets.py --dataset datasets/dense_owner_v7 \
      --fractions 0.25 0.5
"""
from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


def build_subset(src: Path, dst: Path, frac: float, rng: random.Random) -> dict:
    """Copy `frac` of src's train images (+ their labels) and all of val."""
    if dst.exists():
        shutil.rmtree(dst)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)

    train = sorted((src / "images/train").glob("*.png"))
    # Shuffle once with a fixed seed, then take a prefix: the 25% arm is then a
    # strict subset of the 50% arm, mirroring how labelling rounds accumulate.
    order = list(train)
    rng.shuffle(order)
    keep = order[: max(1, int(len(order) * frac))]

    n_box = 0
    for img in keep:
        shutil.copy2(img, dst / "images/train" / img.name)
        lab = src / "labels/train" / f"{img.stem}.txt"
        if lab.exists():
            shutil.copy2(lab, dst / "labels/train" / lab.name)
            n_box += sum(1 for _ in open(lab))

    n_val = n_val_box = 0
    for img in sorted((src / "images/val").glob("*.png")):
        shutil.copy2(img, dst / "images/val" / img.name)
        lab = src / "labels/val" / f"{img.stem}.txt"
        if lab.exists():
            shutil.copy2(lab, dst / "labels/val" / lab.name)
            n_val_box += sum(1 for _ in open(lab))
        n_val += 1

    (dst / "data.yaml").write_text(
        f"path: {dst.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_cluster\n",
        encoding="utf-8",
    )
    return {
        "dataset": dst.name,
        "frac": frac,
        "train_images": len(keep),
        "train_boxes": n_box,
        "val_images": n_val,
        "val_boxes": n_val_box,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--fractions", type=float, nargs="+", default=[0.25, 0.5])
    ap.add_argument("--seed", type=int, default=20260716)
    args = ap.parse_args()

    src = Path(args.dataset).resolve()
    if not (src / "images/train").exists():
        raise SystemExit(f"没有训练图: {src}/images/train")

    for frac in args.fractions:
        # Re-seed per arm so each draw is reproducible on its own, and shuffle
        # the same base order so the arms nest.
        rng = random.Random(args.seed)
        pct = int(round(frac * 100))
        dst = src.parent / f"{src.name}_p{pct}"
        info = build_subset(src, dst, frac, rng)
        print(
            f"  {info['dataset']}: train {info['train_images']} 图 / {info['train_boxes']} 框"
            f"  |  val {info['val_images']} 图 / {info['val_boxes']} 框",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
