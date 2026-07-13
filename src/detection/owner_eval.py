"""Shared owner-taste F1 evaluation (single source of truth).

Four queue scripts grew their own copies of this loop and they were one
tweak away from drifting apart. Import this instead:

    from src.detection.owner_eval import evaluate_owner_f1
    best, sweep = evaluate_owner_f1("runs/.../best.pt", "datasets/dense_owner_v4")

Matching rule (identical to all published numbers so far): greedy IoU>=0.30
per GT box against unused predictions; F1 sweep over confidences.
"""
from __future__ import annotations

from pathlib import Path


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / union if union > 0 else 0.0


def _load_txt(p: Path):
    if not p.exists():
        return []
    return [tuple(map(float, l.split()[1:]))
            for l in p.read_text().splitlines() if len(l.split()) == 5]


def evaluate_owner_f1(weights: str | Path, dataset_dir: str | Path,
                      confs=(0.15, 0.2, 0.3, 0.4), iou_thr: float = 0.30,
                      split: str = "val") -> tuple[dict, list[dict]]:
    """Return (best_row, sweep_rows); rows: conf/f1/p/r/tp/fp/fn."""
    from ultralytics import YOLO  # heavyweight import kept local
    dataset_dir = Path(dataset_dir)
    vi, vl = dataset_dir / "images" / split, dataset_dir / "labels" / split
    model = YOLO(str(weights))
    images = sorted(vi.glob("*.png"))
    sweep = []
    for conf in confs:
        tp = fp = fn = 0
        for img in images:
            gt = _load_txt(vl / (img.stem + ".txt"))
            res = model.predict(str(img), conf=conf, verbose=False)[0]
            preds = ([tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()]
                     if res.boxes is not None else [])
            used = set()
            for g in gt:
                m = next((k for k, p in enumerate(preds)
                          if k not in used and _iou(g, p) >= iou_thr), None)
                if m is None:
                    fn += 1
                else:
                    used.add(m)
                    tp += 1
            fp += len(preds) - len(used)
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        sweep.append({"conf": conf, "f1": round(f1, 3), "p": round(prec, 3),
                      "r": round(rec, 3), "tp": tp, "fp": fp, "fn": fn})
    best = max(sweep, key=lambda r: r["f1"])
    return best, sweep
