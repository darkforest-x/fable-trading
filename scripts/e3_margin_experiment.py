"""E3: the boundary-contradiction experiment (owner mandate: YOLO must be
"非常准、非常好用").

Hypothesis (from three trainings all plateauing at recall~0.70): GT boxes
whose defining spreads sit NEAR the rule threshold are pixel-indistinguishable
from unlabeled near-misses -- contradictory supervision that no model size
can fix. If true, filtering contradictions from TRAIN (val untouched) should
lift both P and R.

Phase A (diagnosis, val):
  reconstruct each val window from its filename (SYMBOL_START.png), verify the
  std-threshold rescan reproduces the GT txt (abort if <90% agreement),
  classify every GT box: core (still detected at 0.85x thresholds) vs
  boundary; predict with the current best.pt; report FN rate core vs
  boundary + detection-level P/R at IoU 0.5 and 0.3 (the 好用 metric).
Phase B (build): copy dataset to datasets/dense_15m_e3, dropping train images
  that are (all-boundary boxes) or (background with 1.15x-loose near-miss).

The queue only proceeds to training if diagnosis confirms:
  FN(boundary) - FN(core) >= 15pp.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

import src.detection.auto_label as AL
from src.detection.auto_label import label_window
from src.detection.data import add_mas, list_cache_files, load_ohlcv_csv
from src.detection.render import render_chart

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATASET = PROJECT_DIR / "datasets" / "dense_15m_full"
E3_DATASET = PROJECT_DIR / "datasets" / "dense_15m_e3"
OUT = PROJECT_DIR / "analysis" / "output" / "e3_margin_diagnosis.json"
WEIGHTS = PROJECT_DIR / "runs/detect/runs/detect/dense_15m_full_s_e21/weights/best.pt"
WINDOW = 200
CORE_SCALE, LOOSE_SCALE = 0.85, 1.15

_series_cache: dict[str, object] = {}
_symbol_paths: dict[str, Path] = {}


def _symbol_frame(symbol: str):
    if symbol not in _series_cache:
        if not _symbol_paths:
            for p in list_cache_files():
                _symbol_paths.setdefault(p.stem.rsplit("_", 2)[0], p)
        df = add_mas(load_ohlcv_csv(_symbol_paths[symbol]))
        _series_cache[symbol] = df
    return _series_cache[symbol]


def boxes_at_scale(sub, tf, scale: float):
    saved = (AL.FAST_SPREAD_MAX, AL.FULL_SPREAD_MAX)
    AL.FAST_SPREAD_MAX, AL.FULL_SPREAD_MAX = saved[0] * scale, saved[1] * scale
    try:
        return label_window(sub, tf)
    finally:
        AL.FAST_SPREAD_MAX, AL.FULL_SPREAD_MAX = saved


def load_txt(p: Path):
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        f = line.split()
        if len(f) == 5:
            out.append(tuple(map(float, f[1:])))
    return out


def iou(a, b):
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / union if union > 0 else 0.0


def reconstruct(stem: str):
    symbol, start = stem.rsplit("_", 1)
    df = _symbol_frame(symbol)
    sub = df.iloc[int(start):int(start) + WINDOW]
    if len(sub) < WINDOW:
        return None
    _, tf = render_chart(sub, out_path=None)
    return sub, tf


def classify_split(split: str):
    """Per image: gt boxes with core/boundary class + near-miss background flag."""
    img_dir, lbl_dir = DATASET / "images" / split, DATASET / "labels" / split
    info, agree = {}, [0, 0]
    for img in sorted(img_dir.glob("*.png")):
        gt = load_txt(lbl_dir / (img.stem + ".txt"))
        rec = reconstruct(img.stem)
        if rec is None:
            continue
        sub, tf = rec
        std = boxes_at_scale(sub, tf, 1.0)
        agree[1] += 1
        if len(std) == len(gt):
            agree[0] += 1
        core = boxes_at_scale(sub, tf, CORE_SCALE)
        loose = boxes_at_scale(sub, tf, LOOSE_SCALE)
        classes = []
        for g in gt:
            is_core = any(iou(g, c) >= 0.3 for c in core)
            classes.append("core" if is_core else "boundary")
        near_miss_bg = (not gt) and len(loose) > 0
        info[img.stem] = {"classes": classes, "near_miss_bg": near_miss_bg, "gt": gt}
    print(f"{split}: std-rescan box-count agreement {agree[0]}/{agree[1]}")
    if agree[1] and agree[0] / agree[1] < 0.90:
        raise SystemExit(f"ABORT: window reconstruction mismatch on {split} "
                         f"({agree[0]}/{agree[1]}) -- fix before trusting E3")
    return info


def diagnose_val(info: dict) -> dict:
    from ultralytics import YOLO
    model = YOLO(str(WEIGHTS))
    stats = {"core": [0, 0], "boundary": [0, 0]}  # [matched, total] at IoU .5
    det = {"tp3": 0, "fp3": 0, "fn3": 0}
    img_dir = DATASET / "images" / "val"
    stems = list(info)
    for i in range(0, len(stems), 64):
        batch = stems[i:i + 64]
        results = model.predict([str(img_dir / f"{s}.png") for s in batch],
                                conf=0.30, verbose=False)
        for stem, res in zip(batch, results):
            preds = [(float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                     for b in res.boxes.xywhn.cpu().numpy()] if res.boxes is not None else []
            gt, classes = info[stem]["gt"], info[stem]["classes"]
            used = set()
            for g, cls in zip(gt, classes):
                stats[cls][1] += 1
                hit5 = any(iou(g, p) >= 0.5 for p in preds)
                if hit5:
                    stats[cls][0] += 1
                # detection-level (usability) at IoU .3
                m = next((k for k, p in enumerate(preds)
                          if k not in used and iou(g, p) >= 0.3), None)
                if m is None:
                    det["fn3"] += 1
                else:
                    used.add(m)
                    det["tp3"] += 1
            det["fp3"] += len(preds) - len(used)
    fn = {c: 1 - (m / t if t else 0) for c, (m, t) in stats.items()}
    return {
        "recall_iou50_core": round(1 - fn["core"], 4),
        "recall_iou50_boundary": round(1 - fn["boundary"], 4),
        "fn_gap_pp": round(100 * (fn["boundary"] - fn["core"]), 1),
        "n_core": stats["core"][1], "n_boundary": stats["boundary"][1],
        "usability_iou30": {
            "recall": round(det["tp3"] / max(det["tp3"] + det["fn3"], 1), 4),
            "precision": round(det["tp3"] / max(det["tp3"] + det["fp3"], 1), 4),
        },
    }


def build_e3(train_info: dict) -> dict:
    dropped_contra, dropped_nearbg, kept = 0, 0, 0
    for sub in ("images/train", "labels/train", "images/val", "labels/val"):
        (E3_DATASET / sub).mkdir(parents=True, exist_ok=True)
    for split in ("val",):  # val copied verbatim: honest comparison
        for f in (DATASET / "images" / split).glob("*.png"):
            shutil.copy2(f, E3_DATASET / "images" / split / f.name)
        for f in (DATASET / "labels" / split).glob("*.txt"):
            shutil.copy2(f, E3_DATASET / "labels" / split / f.name)
    for stem, meta in train_info.items():
        drop = (meta["classes"] and all(c == "boundary" for c in meta["classes"])) \
            or meta["near_miss_bg"]
        if drop:
            if meta["near_miss_bg"]:
                dropped_nearbg += 1
            else:
                dropped_contra += 1
            continue
        kept += 1
        src_img = DATASET / "images/train" / f"{stem}.png"
        shutil.copy2(src_img, E3_DATASET / "images/train" / src_img.name)
        src_lbl = DATASET / "labels/train" / f"{stem}.txt"
        if src_lbl.exists():
            shutil.copy2(src_lbl, E3_DATASET / "labels/train" / src_lbl.name)
    (E3_DATASET / "data.yaml").write_text(
        f"path: {E3_DATASET}\ntrain: images/train\nval: images/val\n"
        f"names:\n  0: dense_cluster\n", encoding="utf-8")
    return {"kept_train": kept, "dropped_all_boundary": dropped_contra,
            "dropped_near_miss_bg": dropped_nearbg}


def main() -> int:
    val_info = classify_split("val")
    diagnosis = diagnose_val(val_info)
    print(json.dumps(diagnosis, indent=2))
    train_info = classify_split("train")
    build = build_e3(train_info)
    print(json.dumps(build, indent=2))
    OUT.write_text(json.dumps({"diagnosis": diagnosis, "e3_build": build},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
