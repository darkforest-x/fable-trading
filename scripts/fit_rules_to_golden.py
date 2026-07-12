"""Fit dense-rule thresholds to the owner's round-1 golden labels.

Grid over (fast_max, full_max, min_bars, max_bars) scales; for each combo,
regenerate boxes on the 80 golden windows (explicit kwargs -- the def-time
default binding bug is documented in docs/learnings/) and score F1 vs the
owner's boxes at IoU>=0.30. 60/20 image split guards against overfitting
the grid to all 80.

Output: analysis/output/rule_fit_golden.json (top combos + achievable ceiling).
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from e3_margin_experiment import iou, reconstruct  # noqa: E402  (window rebuild + IoU)
from golden_disagreement import rects  # noqa: E402

from src.detection.auto_label import find_dense_segments, segment_to_bbox  # noqa: E402

EXPORT = PROJECT_DIR / "output" / "label_studio" / "export_round1.json"
OUT = PROJECT_DIR / "analysis" / "output" / "rule_fit_golden.json"
BASE_FAST, BASE_FULL = 0.0028, 0.0055

GRID = {
    "fast_scale": (0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    "full_scale": (0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    "min_bars": (5, 8, 12),
    "max_bars": (12, 20, 40),
}


def boxes_for(sub, tf, fast_scale, full_scale, min_bars, max_bars):
    segs = find_dense_segments(sub, fast_max=BASE_FAST * fast_scale,
                               full_max=BASE_FULL * full_scale,
                               min_bars=min_bars, max_bars=max_bars)
    boxes = (segment_to_bbox(sub, s, tf) for s in segs)
    return [b for b in boxes if b is not None]


def f1_on(items, params):
    tp = fp = fn = 0
    for sub, tf, owner in items:
        try:
            pred = boxes_for(sub, tf, *params)
        except Exception:
            return -1.0
        used = set()
        for ob in owner:
            m = next((k for k, pb in enumerate(pred)
                      if k not in used and iou(ob, pb) >= 0.30), None)
            if m is None:
                fn += 1
            else:
                used.add(m)
                tp += 1
        fp += len(pred) - len(used)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return 2 * prec * rec / max(prec + rec, 1e-9), prec, rec


def main() -> int:
    tasks = json.loads(EXPORT.read_text())
    items = []
    for t in sorted(tasks, key=lambda x: x.get("data", {}).get("stem", "")):
        stem = t.get("data", {}).get("stem")
        rec = reconstruct(stem)
        if rec is None:
            continue
        sub, tf = rec
        owner = rects(t["annotations"][0]) if t.get("annotations") else []
        items.append((sub, tf, owner))
    fit, hold = items[:60], items[60:]
    print(f"golden windows: fit {len(fit)} / holdout {len(hold)}")

    rows = []
    for combo in product(*GRID.values()):
        f1p = f1_on(fit, combo)
        if f1p == -1.0:
            continue
        rows.append({"params": dict(zip(GRID.keys(), combo)),
                     "fit_f1": round(f1p[0], 4), "fit_p": round(f1p[1], 3),
                     "fit_r": round(f1p[2], 3)})
    rows.sort(key=lambda r: -r["fit_f1"])
    for r in rows[:5]:
        combo = tuple(r["params"].values())
        h = f1_on(hold, combo)
        r["hold_f1"], r["hold_p"], r["hold_r"] = round(h[0], 4), round(h[1], 3), round(h[2], 3)
    baseline = f1_on(fit, (1.0, 1.0, 5, 12))
    result = {
        "baseline_current_rules": {"fit_f1": round(baseline[0], 4),
                                   "p": round(baseline[1], 3), "r": round(baseline[2], 3)},
        "top5": rows[:5],
        "grid_size": len(rows),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
