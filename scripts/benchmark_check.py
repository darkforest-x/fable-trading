"""Exemplar sanity gate: score a detector on the owner's ⭐ benchmark images.

The 176 exemplars in data/benchmark_exemplars.json are the owner's "textbook"
dense clusters. Any competent model must find nearly all of them; a model that
misses them is broken regardless of its aggregate F1. The optimizer='auto' lr
bug shipped models that were the base plus one warmup epoch, and their plausible
frozen-F1 hid that for months -- this gate would have flagged them on day one
("can't find the most textbook cluster" is louder than "F1 dipped 3 points").

Two numbers are reported, and the split matters:
  - eval-symbol exemplars (~24): the model has NEVER seen these symbols. True test.
  - train-symbol exemplars: the model trained on these very images. Missing any
    of them is an immediate red flag (underfit or broken weights).

Pass rule (heuristic, registered 2026-07-16): recall >= 0.90 on train-symbol
exemplars AND >= 0.60 on eval-symbol exemplars at conf 0.15, IoU 0.30.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/benchmark_check.py                  # owner_best.pt
  PYTHONPATH=. .venv/bin/python scripts/benchmark_check.py --weights runs/.../best.pt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.detection.owner_eval import _iou, is_eval_stem  # shared matching rule

PROJECT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT / "data/benchmark_exemplars.json"
IMG_DIRS = [PROJECT / "datasets" / d / "images" / s
            for d in ("dense_15m_full", "dense_swap_v1", "round6_scout",
                      "dense_owner_v6", "dense_owner_v7", "owner_eval_frozen")
            for s in ("train", "val")]

PASS_TRAIN_RECALL = 0.90
PASS_EVAL_RECALL = 0.60
CONF = 0.15
IOU = 0.30


def find_image(stem: str) -> Path | None:
    for d in IMG_DIRS:
        p = d / f"{stem}.png"
        if p.exists():
            return p
    return None


def run(weights: str | Path) -> dict:
    from ultralytics import YOLO

    reg = json.loads(REGISTRY.read_text())["exemplars"]
    model = YOLO(str(weights))

    buckets = {"train": {"tp": 0, "fn": 0, "misses": []},
               "eval": {"tp": 0, "fn": 0, "misses": []}}
    n_missing_img = 0
    for stem, info in reg.items():
        gt = [(b["cx"], b["cy"], b["w"], b["h"]) for b in info["boxes"]]
        if not gt:
            continue  # background exemplars carry no recall signal
        img = find_image(stem)
        if img is None:
            n_missing_img += 1
            continue
        res = model.predict(str(img), conf=CONF, verbose=False)[0]
        preds = ([tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()]
                 if res.boxes is not None else [])
        bucket = buckets["eval" if is_eval_stem(stem) else "train"]
        used = set()
        for g in gt:
            m = next((k for k, p in enumerate(preds)
                      if k not in used and _iou(g, p) >= IOU), None)
            if m is None:
                bucket["fn"] += 1
                bucket["misses"].append(stem)
            else:
                used.add(m)
                bucket["tp"] += 1

    out = {"weights": str(weights), "conf": CONF, "iou": IOU,
           "images_not_found": n_missing_img}
    for name, b in buckets.items():
        total = b["tp"] + b["fn"]
        rec = b["tp"] / total if total else None
        out[name] = {"boxes": total, "recall": round(rec, 3) if rec is not None else None,
                     "missed": sorted(set(b["misses"]))[:10]}
    tr, ev = out["train"]["recall"], out["eval"]["recall"]
    out["passed"] = bool(tr is not None and tr >= PASS_TRAIN_RECALL
                         and (ev is None or ev >= PASS_EVAL_RECALL))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", default=str(PROJECT / "models/owner_best.pt"))
    args = ap.parse_args()
    r = run(args.weights)
    print(f"标杆体检: {Path(r['weights']).name}")
    print(f"  训练侧标杆(模型见过): recall {r['train']['recall']}  "
          f"({r['train']['boxes']} 框, 需>={PASS_TRAIN_RECALL})")
    print(f"  eval侧标杆(从未见过): recall {r['eval']['recall']}  "
          f"({r['eval']['boxes']} 框, 需>={PASS_EVAL_RECALL})")
    if r["images_not_found"]:
        print(f"  ⚠️ 找不到图片: {r['images_not_found']} 张")
    for side in ("train", "eval"):
        if r[side]["missed"]:
            print(f"  {side} 漏检: {', '.join(r[side]['missed'][:5])}")
    print(f"  → {'✅ 通过' if r['passed'] else '❌ 不通过 —— 模型连教科书案例都检不出,先别信它的F1'}")
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
