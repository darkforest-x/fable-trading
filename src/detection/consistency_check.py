"""Consistency gate: YOLO preds vs auto_label GT boxes (IoU>=0.5 one-to-one).

P2-11 formal path: after relabel + retrain, report match_rate = matched / GT.
Does not evaluate holdout. Does not retrain.

Usage:
  .venv/bin/python -m src.detection.consistency_check \\
      --dataset datasets/dense_15m_full --split val \\
      --preds datasets/dense_15m_full/preds_val_conf30 \\
      --out analysis/output/consistency_e21_pred_vs_gt.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_yolo_boxes(path: Path, *, with_conf: bool = False) -> list[tuple[float, float, float, float]]:
    """Return list of (cx, cy, w, h) normalized."""
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if with_conf and len(parts) >= 6:
            _, cx, cy, w, h = map(float, parts[:5])
        elif len(parts) >= 5:
            _, cx, cy, w, h = map(float, parts[:5])
        else:
            continue
        boxes.append((cx, cy, w, h))
    return boxes


def _xywhn_to_xyxy(cx: float, cy: float, w: float, h: float) -> tuple[float, float, float, float]:
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = _xywhn_to_xyxy(*a)
    bx1, by1, bx2, by2 = _xywhn_to_xyxy(*b)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match_greedy(
    gt: list[tuple[float, float, float, float]],
    pred: list[tuple[float, float, float, float]],
    *,
    iou_thr: float = 0.5,
) -> tuple[int, int, int]:
    """One-to-one greedy match. Returns (matched, n_gt, n_pred)."""
    if not gt:
        return 0, 0, len(pred)
    used_p: set[int] = set()
    matched = 0
    for g in gt:
        best_j, best_iou = -1, 0.0
        for j, p in enumerate(pred):
            if j in used_p:
                continue
            v = iou(g, p)
            if v > best_iou:
                best_iou, best_j = v, j
        if best_j >= 0 and best_iou >= iou_thr:
            used_p.add(best_j)
            matched += 1
    return matched, len(gt), len(pred)


def run(
    dataset: Path,
    split: str,
    pred_labels: Path,
    *,
    iou_thr: float = 0.5,
) -> dict:
    gt_dir = dataset / "labels" / split
    img_dir = dataset / "images" / split
    stems = sorted(p.stem for p in img_dir.glob("*.png"))
    total_m = total_gt = total_pred = 0
    per_image = []
    for stem in stems:
        gt = _load_yolo_boxes(gt_dir / f"{stem}.txt")
        pr = _load_yolo_boxes(pred_labels / f"{stem}.txt", with_conf=True)
        m, ng, np_ = match_greedy(gt, pr, iou_thr=iou_thr)
        total_m += m
        total_gt += ng
        total_pred += np_
        if ng or np_:
            per_image.append({"stem": stem, "matched": m, "n_gt": ng, "n_pred": np_})
    match_rate = (total_m / total_gt) if total_gt else None
    precision_like = (total_m / total_pred) if total_pred else None
    return {
        "dataset": str(dataset),
        "split": split,
        "pred_labels": str(pred_labels),
        "iou_thr": iou_thr,
        "n_images": len(stems),
        "n_gt_boxes": total_gt,
        "n_pred_boxes": total_pred,
        "matched_iou50": total_m,
        "match_rate_vs_gt": round(match_rate, 4) if match_rate is not None else None,
        "precision_like": round(precision_like, 4) if precision_like is not None else None,
        "gate_match_rate_ge_0_95": bool(match_rate is not None and match_rate >= 0.95),
        "worst_misses": sorted(
            [x for x in per_image if x["n_gt"] > 0 and x["matched"] < x["n_gt"]],
            key=lambda x: x["matched"] / max(x["n_gt"], 1),
        )[:30],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="datasets/dense_15m_full")
    parser.add_argument("--split", default="val")
    parser.add_argument(
        "--preds",
        default="datasets/dense_15m_full/preds_val_conf30",
        help="dir with labels/<split>/*.txt or labels as flat txt",
    )
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--out", default="analysis/output/consistency_pred_vs_gt.json")
    args = parser.parse_args()

    dataset = Path(args.dataset)
    pred_root = Path(args.preds)
    candidates = [
        pred_root / "labels" / args.split,
        pred_root / args.split,
        pred_root,
    ]
    pred_labels = next((p for p in candidates if p.is_dir() and list(p.glob("*.txt"))), None)
    if pred_labels is None:
        raise SystemExit(f"no prediction labels under {pred_root}")

    summary = run(dataset, args.split, pred_labels, iou_thr=args.iou)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in summary if k != "worst_misses"}, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
