"""Evaluate the trained detector on val and render pred-vs-GT comparisons.

Usage:
  python -m src.detection.eval_visualize \
      --weights runs/detect/.../weights/best.pt \
      --data datasets/dense_15m/data.yaml --n-vis 5
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
from ultralytics import YOLO

from .train import pick_device

GT_COLOR = (0, 0, 0)      # black = rule-generated ground truth
PRED_COLOR = (255, 0, 255)  # magenta = model prediction


def draw_boxes(img, boxes_xyxy, color, tag):
    for i, (x1, y1, x2, y2) in enumerate(boxes_xyxy):
        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        if i == 0:
            cv2.putText(img, tag, (int(x1), max(int(y1) - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--data", default="datasets/dense_15m/data.yaml")
    parser.add_argument("--dataset-dir", default="datasets/dense_15m")
    parser.add_argument("--out-dir", default="analysis/output")
    parser.add_argument("--n-vis", type=int, default=5)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    device = pick_device()
    model = YOLO(args.weights)

    metrics = model.val(data=str(Path(args.data).resolve()), device=device, plots=False)
    summary = {
        "mAP50": round(float(metrics.box.map50), 4),
        "mAP50-95": round(float(metrics.box.map), 4),
        "precision": round(float(metrics.box.mp), 4),
        "recall": round(float(metrics.box.mr), 4),
    }
    print(json.dumps(summary, indent=2))

    dataset_dir = Path(args.dataset_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    val_images = sorted((dataset_dir / "images" / "val").glob("*.png"))
    # prefer images that actually contain GT boxes for a meaningful comparison
    with_gt = [
        p for p in val_images
        if (dataset_dir / "labels" / "val" / f"{p.stem}.txt").read_text().strip()
    ]
    rng = random.Random(args.seed)
    picks = rng.sample(with_gt, min(args.n_vis, len(with_gt)))

    for path in picks:
        img = cv2.imread(str(path))
        h, w = img.shape[:2]
        gt = []
        for line in (dataset_dir / "labels" / "val" / f"{path.stem}.txt").read_text().splitlines():
            _, xc, yc, bw, bh = map(float, line.split())
            gt.append(((xc - bw / 2) * w, (yc - bh / 2) * h, (xc + bw / 2) * w, (yc + bh / 2) * h))
        pred = model.predict(str(path), conf=args.conf, device=device, verbose=False)[0]
        pred_boxes = pred.boxes.xyxy.cpu().numpy().tolist() if pred.boxes is not None else []
        draw_boxes(img, gt, GT_COLOR, "GT")
        draw_boxes(img, pred_boxes, PRED_COLOR, "pred")
        out_path = out_dir / f"p2a_val_{path.stem}.png"
        cv2.imwrite(str(out_path), img)
        print(f"saved {out_path} (gt={len(gt)}, pred={len(pred_boxes)})")

    (out_dir / "p2a_val_metrics.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
