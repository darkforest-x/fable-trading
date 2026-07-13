"""Round-5+ labeling packs with MODEL prelabels (active-learning loop).

Samples unlabeled images from a rendered dataset, runs the newest owner
detector, and writes Label Studio tasks whose predictions are the MODEL's
boxes (not the rules') -- the owner now corrects the student directly.
Sampling favors uncertainty: half the pack from images whose top box conf
falls in [0.15, 0.45] (the model's confusion zone), half random.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/model_prelabel_pack.py \
      --dataset dense_swap_v1 --count 500 --out output/label_studio/tasks_round5_chunk1.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


def pick_model() -> Path:
    fixed = PROJECT_DIR / "models/owner_best.pt"
    if fixed.exists():
        return fixed
    runs = sorted(PROJECT_DIR.glob("runs/detect/runs/detect/owner_v*/weights/best.pt"),
                  key=lambda p: p.stat().st_mtime)
    return runs[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dense_swap_v1")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260714)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from ultralytics import YOLO

    rng = random.Random(args.seed)
    pool = set(json.loads((PROJECT_DIR / "data/golden_pool.json").read_text()))
    ds = PROJECT_DIR / "datasets" / args.dataset
    stems = [p.stem for split in ("train", "val")
             for p in (ds / "images" / split).glob("*.png") if p.stem not in pool]
    rng.shuffle(stems)
    candidates = stems[: args.count * 4]  # score a wider set, then select

    weights = pick_model()
    model = YOLO(str(weights))
    scored = []
    for stem in candidates:
        img = next(p for split in ("train", "val")
                   for p in [ds / "images" / split / f"{stem}.png"] if p.exists())
        split = img.parent.name
        res = model.predict(str(img), conf=0.10, verbose=False)[0]
        boxes = []
        if res.boxes is not None:
            for b, c in zip(res.boxes.xywhn.cpu().numpy(), res.boxes.conf.cpu().numpy()):
                if float(c) >= 0.20:
                    boxes.append(tuple(map(float, b[:4])))
            top = float(res.boxes.conf.max()) if len(res.boxes) else 0.0
        else:
            top = 0.0
        scored.append((stem, split, boxes, top))

    uncertain = [s for s in scored if 0.15 <= s[3] <= 0.45]
    rest = [s for s in scored if s not in uncertain]
    take = uncertain[: args.count // 2] + rest[: args.count - min(len(uncertain), args.count // 2)]

    tasks = []
    for stem, split, boxes, _ in take[: args.count]:
        results = [{
            "type": "rectanglelabels", "from_name": "label", "to_name": "image",
            "original_width": 1280, "original_height": 742,
            "value": {"x": (cx - w/2) * 100, "y": (cy - h/2) * 100,
                      "width": w * 100, "height": h * 100,
                      "rectanglelabels": ["dense_cluster"]},
        } for cx, cy, w, h in boxes]
        tasks.append({
            "data": {"image": f"/data/local-files/?d={args.dataset}/images/{split}/{stem}.png",
                     "stem": stem, "split": split},
            "predictions": [{"model_version": weights.name, "result": results}],
        })
    Path(args.out).write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}: {len(tasks)} tasks "
          f"({sum(1 for t in tasks if t['predictions'][0]['result'])} with model boxes, "
          f"model={weights.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
