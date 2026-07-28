"""Was the owner looking at the future when they drew these boxes?

Each star label was drawn on a 200-bar rendered window. If the box's right edge
sits at bar 150 of 200, the owner could see 50 bars of what happened next while
deciding whether to mark it. That would make the +126bp of gold_unfiltered.py
hindsight rather than eye, and would make the target unlearnable by any detector
that only sees up to the tip.

Splits the same 499 tips by how much future was visible in the labelling image.
"""
from __future__ import annotations

import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/zhangzc/fable-trading")
sys.path.insert(0, "/Users/zhangzc/fable-trading/scripts")

import cv2  # noqa: E402

from diag_v9_precision_vs_recall import (  # noqa: E402
    WINDOW,
    add_mas,
    archive_index,
    boxes_cut_and_spans,
    load_star_boxes,
    make_chart_transform,
    resolve_series,
    resolve_win_start,
    symbol_of,
)
from src.data.loader import list_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import HORIZON_BARS, label_short_candidate  # noqa: E402

OUTDIR = "analysis/output/"

known = {s for (_x, s) in list_series(bar="15m")}
arch = archive_index()
stars = load_star_boxes()

rows = []
for stem, boxes in stars.items():
    sym = symbol_of(stem, known)
    if sym is None:
        continue
    base = resolve_series(sym)
    if base is None:
        continue
    framed = add_mas(base)
    m = re.search(r"_(\d+)$", stem)
    if not m:
        continue
    stored = cv2.imread(str(arch[stem])) if stem in arch else None
    r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed, stored_img=stored)
    if r is None:
        continue
    _mo, ws, _mad = r
    sub_old = framed.iloc[ws : ws + WINDOW].reset_index(drop=True)
    if len(sub_old) != WINDOW:
        continue
    _c, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub_old))
    if not spans:
        continue
    _b0, b1, _ph, _pl = spans[0]
    tip = ws + b1
    if tip < WINDOW or tip + 1 + HORIZON_BARS >= len(framed):
        continue
    enr = add_indicators(framed)
    o = label_short_candidate(enr, int(tip), tp_mult=5.0, sl_mult=2.0)
    if o is None:
        continue
    ts = pd.Timestamp(enr["open_time"].iloc[int(tip)])
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    rows.append(
        {
            "symbol": sym,
            "t": ts,
            "short_ret": o.realized_ret,
            "atr_pct": float(enr["atr_pct"].iloc[int(tip)]),
            "b1": int(b1),
            "right_frac": b1 / (WINDOW - 1),
            "future_bars_visible": (WINDOW - 1) - int(b1),
        }
    )

g = pd.DataFrame(rows)
g.to_csv(OUTDIR + "gold_hindsight.csv", index=False)
cost = 0.001

print(f"n = {len(g)}\n")
print("=== how much future was visible in the labelling image ===")
print(f"future bars right of the box: p10={np.percentile(g.future_bars_visible,10):.0f}  "
      f"p50={np.percentile(g.future_bars_visible,50):.0f}  "
      f"p90={np.percentile(g.future_bars_visible,90):.0f}  max={g.future_bars_visible.max():.0f}")
print(f"box right edge as fraction of window: p50={g.right_frac.median():.3f}")
print(f"tips drawn at the live edge (<=2 future bars): {(g.future_bars_visible<=2).sum()} "
      f"of {len(g)} = {(g.future_bars_visible<=2).mean()*100:.1f}%")
print(f"the 72-bar barrier horizon was fully visible for: "
      f"{(g.future_bars_visible>=72).sum()} = {(g.future_bars_visible>=72).mean()*100:.1f}%\n")


def show(x: pd.Series, name: str) -> None:
    if len(x) < 8:
        print(f"{name:46s} n={len(x):5d}  (too few)")
        return
    se = x.std() / np.sqrt(len(x))
    print(f"{name:46s} n={len(x):5d}  gross={x.mean()*10000:+8.2f} bp  "
          f"net={(x.mean()-cost)*10000:+8.2f} bp  win={(x>0).mean()*100:5.1f}%  t={x.mean()/se:+6.2f}")


print("=== short return, split by how much future the owner could see ===")
bins = [(-1, 2), (2, 24), (24, 72), (72, 999)]
names = ["live edge      (0-2 future bars)", "some future    (3-24)",
         "much future    (25-72)", "whole horizon  (>72)"]
for (lo, hi), nm in zip(bins, names):
    show(g.short_ret[(g.future_bars_visible > lo) & (g.future_bars_visible <= hi)], nm)

print()
print("correlation(future bars visible, short return) = "
      f"{np.corrcoef(g.future_bars_visible, g.short_ret)[0,1]:+.3f}")

# Matched control, live-edge subset only.
base = pd.read_csv(OUTDIR + "base_rate_random_short_atr.csv")
base["t"] = pd.to_datetime(base["t"], utc=True)
pool = pd.read_csv("/Users/zhangzc/fable-trading/data/judgment_yolo_owner_side_short_100_6m.csv")
edges = np.quantile(pool.atr_pct, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
edges[0], edges[-1] = -np.inf, np.inf
for d in (g, base):
    d["aq"] = pd.cut(d.atr_pct, edges, labels=False, include_lowest=True)
    d["cell"] = d["t"].dt.strftime("%Y-%m") + "|q" + d["aq"].astype(str)
b = base.groupby("cell")["realized_ret"].agg(rnd_n="count", rnd_m="mean")
mg = g.merge(b, left_on="cell", right_index=True, how="inner")
mg = mg[mg.rnd_n >= 20]
print("\n=== excess over matched random, by visibility ===")
for (lo, hi), nm in zip(bins, names):
    s = mg[(mg.future_bars_visible > lo) & (mg.future_bars_visible <= hi)]
    if len(s) < 8:
        print(f"{nm:46s} n={len(s):5d}  (too few)")
        continue
    ex = s.short_ret - s.rnd_m
    se = ex.std() / np.sqrt(len(ex))
    print(f"{nm:46s} n={len(s):5d}  excess={ex.mean()*10000:+8.2f} bp  "
          f"t={ex.mean()/se:+6.2f}  net={ex.mean()*10000-10:+8.2f} bp")
