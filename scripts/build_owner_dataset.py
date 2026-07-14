"""Build datasets/dense_owner_v1 from the consolidated golden pool
(data/golden_pool.json): owner boxes as YOLO labels over the existing
rendered images. Split by SYMBOL hash (no same-symbol leakage across
train/val). Then evaluate: rule boxes vs owner on the val slice, giving the
baseline the owner-taste model must beat (rule-family F1 ceiling ~0.45).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
POOL = PROJECT_DIR / "data/golden_pool.json"
SRC_DIRS = [PROJECT_DIR / "datasets/dense_15m_full/images/val",
            PROJECT_DIR / "datasets/dense_15m_full/images/train",
            PROJECT_DIR / "datasets/dense_swap_v1/images/train",
            PROJECT_DIR / "datasets/dense_swap_v1/images/val"]
import sys
DST = PROJECT_DIR / "datasets" / (sys.argv[1] if len(sys.argv) > 1 else "dense_owner_v1")


def split_of(stem: str) -> str:
    symbol = stem.rsplit("_", 1)[0]
    return "val" if int(hashlib.sha1(symbol.encode()).hexdigest(), 16) % 5 == 0 else "train"


def main() -> int:
    pool = json.loads(POOL.read_text())
    counts = {"train": [0, 0], "val": [0, 0]}  # images, boxes
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (DST / sub).mkdir(parents=True, exist_ok=True)
    for stem, boxes in pool.items():
        src = next((d / f"{stem}.png" for d in SRC_DIRS if (d / f"{stem}.png").exists()), None)
        if src is None:
            continue
        split = split_of(stem)
        shutil.copy2(src, DST / "images" / split / src.name)
        lines = "".join(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h in boxes)
        (DST / "labels" / split / f"{stem}.txt").write_text(lines)
        counts[split][0] += 1
        counts[split][1] += len(boxes)
    (DST / "data.yaml").write_text(
        f"path: {DST}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n")
    print(json.dumps({k: {"images": v[0], "boxes": v[1]} for k, v in counts.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
