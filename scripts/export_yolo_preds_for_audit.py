"""Run frozen/best YOLO weights on a dataset split and save YOLO-format labels.

Used by FiftyOne mistakenness / Label Studio pre-annotation — does NOT retrain
and does NOT touch holdout.

Usage:
  .venv/bin/python scripts/export_yolo_preds_for_audit.py \\
      --dataset datasets/dense_15m_full --split val --conf 0.30
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="datasets/dense_15m_full")
    parser.add_argument("--split", default="val", choices=("val", "train"))
    parser.add_argument(
        "--weights",
        default="runs/detect/runs/detect/dense_15m_full_s/weights/best.pt",
    )
    parser.add_argument("--conf", type=float, default=0.30)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument(
        "--out",
        default="",
        help="default: <dataset>/preds_<split>_confXX",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 = all images")
    args = parser.parse_args()

    dataset = Path(args.dataset)
    img_dir = dataset / "images" / args.split
    images = sorted(img_dir.glob("*.png")) + sorted(img_dir.glob("*.jpg"))
    if args.limit > 0:
        images = images[: args.limit]
    if not images:
        raise SystemExit(f"no images under {img_dir}")

    out = Path(args.out) if args.out else dataset / f"preds_{args.split}_conf{int(args.conf*100):02d}"
    label_dir = out / "labels" / args.split
    label_dir.mkdir(parents=True, exist_ok=True)
    # mirror empty images path for FO YOLOv5Dataset layout optional
    (out / "images" / args.split).mkdir(parents=True, exist_ok=True)

    model = YOLO(args.weights)
    n_pred = 0
    for i, path in enumerate(images):
        results = model.predict(
            source=str(path),
            conf=args.conf,
            imgsz=args.imgsz,
            verbose=False,
        )
        lines: list[str] = []
        r0 = results[0]
        if r0.boxes is not None and len(r0.boxes):
            xywhn = r0.boxes.xywhn.cpu().tolist()
            cls = r0.boxes.cls.cpu().tolist()
            confs = r0.boxes.conf.cpu().tolist()
            for (cx, cy, w, h), c, conf in zip(xywhn, cls, confs):
                lines.append(f"{int(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f} {conf:.6f}")
                n_pred += 1
        (label_dir / f"{path.stem}.txt").write_text(
            ("\n".join(lines) + ("\n" if lines else "")), encoding="utf-8"
        )
        if (i + 1) % 100 == 0 or i + 1 == len(images):
            print(f"predicted {i+1}/{len(images)} images, boxes_so_far={n_pred}")

    meta = {
        "dataset": str(dataset.resolve()),
        "split": args.split,
        "weights": args.weights,
        "conf": args.conf,
        "imgsz": args.imgsz,
        "n_images": len(images),
        "n_pred_boxes": n_pred,
        "out": str(out.resolve()),
    }
    (out / "pred_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
