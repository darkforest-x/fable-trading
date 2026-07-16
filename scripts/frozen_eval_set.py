"""Build a PERMANENT frozen eval set for the owner detector.

The learning-curve confound: every owner_vN split its own growing pool into
train/val by symbol hash, so v4=0.511 and v5=0.495 were measured on DIFFERENT
images and are not comparable. This carves out a fixed set of HELD-OUT symbols
(never used for training in any version) so every model is scored on one ruler.

Held-out symbols = those whose sha1 % 7 == 0 (disjoint from the %5 val split
convention, ~14% of symbols). Their labeled images become datasets/owner_eval_frozen.
Training datasets must EXCLUDE these symbols going forward (build_owner_dataset
gains --exclude-eval).
"""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path

from src.detection.owner_eval import is_eval_stem as is_eval_symbol

PROJECT_DIR = Path(__file__).resolve().parents[1]
POOL = PROJECT_DIR / "data/golden_pool.json"
DST = PROJECT_DIR / "datasets/owner_eval_frozen"
SRC_DIRS = [PROJECT_DIR / "datasets/dense_15m_full/images/val",
            PROJECT_DIR / "datasets/dense_15m_full/images/train",
            PROJECT_DIR / "datasets/dense_swap_v1/images/train",
            PROJECT_DIR / "datasets/dense_swap_v1/images/val"]



def main() -> int:
    pool = json.loads(POOL.read_text())
    (DST / "images/val").mkdir(parents=True, exist_ok=True)
    (DST / "labels/val").mkdir(parents=True, exist_ok=True)
    n_img = n_box = 0
    syms = set()
    for stem, boxes in pool.items():
        if not is_eval_symbol(stem):
            continue
        src = next((d / f"{stem}.png" for d in SRC_DIRS if (d / f"{stem}.png").exists()), None)
        if src is None:
            continue
        shutil.copy2(src, DST / "images/val" / src.name)
        (DST / "labels/val" / f"{stem}.txt").write_text(
            "".join(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h in boxes))
        n_img += 1; n_box += len(boxes); syms.add(stem.rsplit("_", 1)[0])
    (DST / "data.yaml").write_text(
        f"path: {DST}\ntrain: images/val\nval: images/val\nnames:\n  0: dense_cluster\n")
    json.dump(sorted(syms), open(PROJECT_DIR / "data/eval_frozen_symbols.json", "w"))
    print(json.dumps({"images": n_img, "boxes": n_box, "symbols": len(syms)}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
