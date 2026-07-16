"""Build a dense_owner_* dataset from the consolidated golden pool
(data/golden_pool.json): owner boxes as YOLO labels over the existing rendered
images. Split by SYMBOL hash (no same-symbol leakage across train/val).

Frozen-eval symbols are EXCLUDED by default (2026-07-16). They were not before,
and this script accepts any output name as argv[1], so every dataset it built
quietly contained ~12% eval-symbol images. That is the likely origin of
datasets/dense_owner_v7h's 596 leaked images, which alone invalidated the
decisive A/B: its "held-out" detector had trained on the ruler.

A model trained on eval symbols scores an inflated frozen-F1 -- v5_from_v4's
0.663 outranked every honest model for a week on exactly this. Excluding by
default means the trap has to be opened deliberately: pass --allow-eval-leak to
reproduce a pre-2026-07-16 dataset, and expect promote_owner_best.py to refuse
anything trained on it.

Usage:
  PYTHONPATH=. python3 scripts/build_owner_dataset.py dense_owner_v9
  PYTHONPATH=. python3 scripts/build_owner_dataset.py legacy_v1 --allow-eval-leak
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from src.detection.owner_eval import is_eval_stem, split_of

PROJECT_DIR = Path(__file__).resolve().parents[1]
POOL = PROJECT_DIR / "data/golden_pool.json"
SRC_DIRS = [PROJECT_DIR / "datasets/dense_15m_full/images/val",
            PROJECT_DIR / "datasets/dense_15m_full/images/train",
            PROJECT_DIR / "datasets/dense_swap_v1/images/train",
            PROJECT_DIR / "datasets/dense_swap_v1/images/val"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name", nargs="?", default="dense_owner_v1")
    ap.add_argument(
        "--allow-eval-leak",
        action="store_true",
        help="include frozen-eval symbols (pre-2026-07-16 behaviour; poisons the ruler)",
    )
    args = ap.parse_args()
    dst = PROJECT_DIR / "datasets" / args.name

    pool = json.loads(POOL.read_text())
    counts = {"train": [0, 0], "val": [0, 0]}  # images, boxes
    skipped_eval = 0
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    for stem, boxes in pool.items():
        if is_eval_stem(stem) and not args.allow_eval_leak:
            skipped_eval += 1
            continue
        src = next((d / f"{stem}.png" for d in SRC_DIRS if (d / f"{stem}.png").exists()), None)
        if src is None:
            continue
        split = split_of(stem)
        shutil.copy2(src, dst / "images" / split / src.name)
        lines = "".join(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h in boxes)
        (dst / "labels" / split / f"{stem}.txt").write_text(lines)
        counts[split][0] += 1
        counts[split][1] += len(boxes)
    (dst / "data.yaml").write_text(
        f"path: {dst}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n")
    print(json.dumps({
        "dataset": str(dst),
        **{k: {"images": v[0], "boxes": v[1]} for k, v in counts.items()},
        "skipped_eval_symbols": skipped_eval,
        "eval_leak_allowed": args.allow_eval_leak,
    }, indent=2))
    if args.allow_eval_leak:
        print("\n⚠️  含 eval 币种 —— 这个数据集训出的模型 F1 虚高，promote 会拒绝它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
