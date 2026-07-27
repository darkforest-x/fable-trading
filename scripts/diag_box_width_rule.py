"""Which width rule reproduces the owner's box width? Measure before training.

v7 widened the box instead of narrowing it -- 2.00x the owner's width against
v6's 1.24x -- because I picked the dense-run threshold from what was already in
the code (expanded, fast_spread <= 0.0045) rather than measuring which rule
lands on the owner's median of 10 bars. That threshold is a loose qualification
gate, not a framing rule: the bundle stays under it for a long stretch, so
walking back from the trough while it holds produced ~20 bars.

The check costs minutes and needed no training run. Doing it now, on the owner's
own boxes, before spending another 50 minutes on the 3060.

Candidates, all anchored at the same trough and walking backwards:
  expanded  fast_spread <= 0.0045     (what v7 used)
  strict    fast_spread <= 0.0028     (the retired strict preset)
  trough    fast_spread <= trough * k  (adaptive: relative to how tight it got)
  fixed     a constant bar count

Scored on how close the produced width lands to the owner's, per box, so a rule
that is right on average but wildly variable does not win.

Read-only. No training, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_box_width_rule.py --limit 260
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402
from scripts.build_htip_dataset import WINDOW, resolve_series  # noqa: E402
from scripts.build_star_tip_dataset_v7 import archive_index, load_star_boxes, symbol_of  # noqa: E402

LOOKBACK, MAX_BACK = 24, 60


def run_back(fast: np.ndarray, trough: int, thr: float) -> int:
    i = trough
    lo = max(0, trough - MAX_BACK)
    while i > lo and np.isfinite(fast[i - 1]) and fast[i - 1] <= thr:
        i -= 1
    return trough - i + 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=260)
    args = ap.parse_args()

    known = {s for (_x, s) in list_series(bar="15m")}
    arch = archive_index()
    stars = load_star_boxes()
    rows = []

    for stem, boxes in list(stars.items())[: args.limit]:
        sym = symbol_of(stem, known)
        if sym is None:
            continue
        base = resolve_series(sym)
        if base is None:
            continue
        framed = add_mas(base)
        fast = add_features(add_indicators(base))["fast_spread"].to_numpy(dtype=float)
        m = re.search(r"_(\d+)$", stem)
        if not m:
            continue
        stored = cv2.imread(str(arch[stem])) if stem in arch else None
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed, stored_img=stored)
        if r is None:
            continue
        _mode, ws, _mad = r
        sub = framed.iloc[ws:ws + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            continue
        _cut, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub))
        if not spans:
            continue
        b0, b1, _ph, _pl = spans[0]
        own = b1 - b0 + 1
        cut = ws + b1
        lo = max(0, cut - LOOKBACK)
        seg = fast[lo:cut + 1]
        if not np.isfinite(seg).any():
            continue
        trough = lo + int(np.nanargmin(seg))
        tv = fast[trough]
        if not np.isfinite(tv) or tv <= 0:
            continue
        rows.append({
            "own": own,
            "expanded_0.0045": run_back(fast, trough, 0.0045),
            "strict_0.0028": run_back(fast, trough, 0.0028),
            "trough_x1.2": run_back(fast, trough, tv * 1.2),
            "trough_x1.5": run_back(fast, trough, tv * 1.5),
            "trough_x2.0": run_back(fast, trough, tv * 2.0),
            "fixed_10": 10,
        })

    d = pd.DataFrame(rows)
    if d.empty:
        print("无可用样本")
        return 1
    print(f"n={len(d)}   owner 框宽: p25={d.own.quantile(.25):.0f} "
          f"p50={d.own.median():.0f} p75={d.own.quantile(.75):.0f}\n")

    print(f"{'规则':<18} {'宽度p50':>8} {'比值p50':>8} {'|比值-1|中位':>12} {'比值p90':>8}")
    out = []
    for c in [c for c in d.columns if c != "own"]:
        ratio = d[c] / d["own"]
        err = float((ratio - 1).abs().median())
        out.append({"rule": c, "width_p50": float(d[c].median()),
                    "ratio_p50": round(float(ratio.median()), 3),
                    "abs_err_p50": round(err, 3),
                    "ratio_p90": round(float(ratio.quantile(.9)), 3)})
        print(f"{c:<18} {d[c].median():>8.0f} {ratio.median():>8.2f} "
              f"{err:>12.2f} {ratio.quantile(.9):>8.2f}")

    best = min(out, key=lambda r: r["abs_err_p50"])
    print(f"\n判读: 最接近 owner 的是 {best['rule']}  "
          f"(比值中位 {best['ratio_p50']}, 误差中位 {best['abs_err_p50']})")
    print(f"对照: v7 用的 expanded_0.0045 误差中位 "
          f"{[r for r in out if r['rule']=='expanded_0.0045'][0]['abs_err_p50']}")

    (PROJECT / "analysis" / "output" / "diag_box_width_rule.json").write_text(
        json.dumps({"n": len(d), "owner_width_p50": float(d.own.median()),
                    "rules": out, "best": best["rule"]},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
