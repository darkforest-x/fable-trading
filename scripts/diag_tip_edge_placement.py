"""Does tip_v1b actually place its box AT the tip, or a few bars short of it?

Motivation: the detector is trained with translate=0.02 at imgsz 960, which
jitters each training image by up to +-0.02*960 = ~19px. At 200 bars over the
~940px plot that is ~4 bars. But inference gates boxes to the last
TIP_EDGE_BARS=2 bars. If training taught "near the right edge" while inference
demands "within 2 bars of it", boxes would routinely be rejected -- which is the
shape of the tip-smoke result (forced tip windows fire 19/27, live only 4/27).

Test: run v1b over its own VAL split, where images were built so the tip is the
right edge and the ground-truth box ends there. Measure, per image, how many
bars short of the tip the PREDICTED box's right edge lands. No tip-edge filter
is applied, so the raw placement distribution is visible.

Read: mass at 0-2 bars means placement is fine and translate is exonerated;
mass at 3+ means the jitter is plausibly costing live fires and a translate=0
retrain (v1c) is worth running.

Read-only: no training, no promote, no holdout. Val split only.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_tip_edge_placement.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v6/weights/best.pt"
VAL_IMAGES = PROJECT / "datasets/dense_owner_short_star_tip_v6/images/val"
VAL_LABELS = PROJECT / "datasets/dense_owner_short_star_tip_v6/labels/val"
TIP_EDGE_BARS = 2


def bar_transform():
    """x-mapping for a WINDOW-bar chart (price bounds do not affect x)."""
    dummy = pd.DataFrame({
        "open": np.ones(WINDOW), "high": np.ones(WINDOW),
        "low": np.ones(WINDOW), "close": np.ones(WINDOW),
    })
    return make_chart_transform(dummy)


def right_edge_of(path: Path) -> float | None:
    """Ground-truth box right edge (normalized) — largest box in the label."""
    if not path.exists():
        return None
    best = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        try:
            cx, _, w, h = (float(v) for v in p[1:5])
        except ValueError:
            continue
        if best is None or w * h > best[1]:
            best = (cx + w / 2, w * h)
    return best[0] if best else None


def main() -> int:
    if not WEIGHTS.exists():
        print(f"weights missing: {WEIGHTS}")
        return 2
    imgs = sorted(VAL_IMAGES.glob("*.png"))
    if not imgs:
        print(f"no val images under {VAL_IMAGES}")
        return 2
    model = load_yolo_model(str(WEIGHTS))
    tf = bar_transform()

    pred_off, gt_off, no_fire = [], [], 0
    for i in range(0, len(imgs), 16):
        chunk = imgs[i:i + 16]
        results = model.predict([str(p) for p in chunk], conf=DEFAULT_CONF,
                                verbose=False, device="cpu")
        for p, res in zip(chunk, results):
            gt = right_edge_of(VAL_LABELS / f"{p.stem}.txt")
            if gt is not None:
                gt_off.append((WINDOW - 1) - right_edge_to_bar(gt, 0.0, tf, n_bars=WINDOW))
            boxes = res.boxes
            if boxes is None or len(boxes) == 0:
                no_fire += 1
                continue
            xywhn = boxes.xywhn.cpu().numpy()
            # rightmost box on the image = the one the tip gate would consider
            edges = [(float(b[0]) + float(b[2]) / 2, float(b[2])) for b in xywhn]
            cx_r, w = max(edges, key=lambda e: e[0])
            bar = right_edge_to_bar(cx_r, 0.0, tf, n_bars=WINDOW)
            pred_off.append((WINDOW - 1) - bar)

    a = np.array(pred_off)
    g = np.array(gt_off)
    buckets = {"0": int((a == 0).sum()), "1": int((a == 1).sum()), "2": int((a == 2).sum()),
               "3-6": int(((a >= 3) & (a <= 6)).sum()), "7+": int((a >= 7).sum())}
    within_gate = int((a <= TIP_EDGE_BARS).sum())
    out = {
        "weights": str(WEIGHTS), "n_val_images": len(imgs), "n_fired": int(len(a)),
        "n_no_fire": no_fire, "conf": DEFAULT_CONF, "tip_edge_bars": TIP_EDGE_BARS,
        "gt_offset_from_tip": {"p50": float(np.median(g)) if len(g) else None,
                               "max": int(g.max()) if len(g) else None},
        "pred_offset_from_tip": {
            "p50": float(np.median(a)), "p90": float(np.percentile(a, 90)),
            "mean": round(float(a.mean()), 2), "max": int(a.max())},
        "buckets_bars_short_of_tip": buckets,
        "pct_within_gate": round(within_gate / max(len(a), 1) * 100, 1),
        "pct_rejected_by_gate": round((len(a) - within_gate) / max(len(a), 1) * 100, 1),
    }
    (PROJECT / "analysis" / "output" / "diag_tip_edge_placement.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    print(f"val images {len(imgs)}  开火 {len(a)}  未开火 {no_fire}")
    print(f"真值框右缘距 tip: p50={out['gt_offset_from_tip']['p50']} 根 "
          f"(按构造应为 0)")
    print("预测框右缘距 tip 的分布(根):")
    for k, v in buckets.items():
        bar = "#" * max(1, round(v / max(len(a), 1) * 50)) if v else ""
        print(f"  差 {k:>4} 根: {v:4d}  {bar}")
    print(f"\n落在 tip-edge 门内(<={TIP_EDGE_BARS} 根): {out['pct_within_gate']}%")
    print(f"会被门挡掉            : {out['pct_rejected_by_gate']}%")
    verdict = ("translate 洗清:框基本贴 tip,门不是瓶颈"
               if out["pct_rejected_by_gate"] < 15
               else "坐实:大量框差 3+ 根被门挡掉 → translate=0 重训 v1c 值得做")
    print(f"\n判读: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
