"""One-off probe: does v12 tip-firing depend on MA render semantics?

For the first 30 eligible signals, render the tip window BOTH ways:
  A) add_mas(full series) then slice  (live scan semantics)
  B) slice then add_mas               (tip_detectability / htip training clones)
and compare v12 detections. Torch-only process (no lightgbm).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import list_series, load_series
from src.detection.data import add_mas
from src.detection.render import render_chart
from src.judgment.yolo_candidates import load_yolo_model, right_edge_to_bar

WINDOW = 200
eligible = pd.read_csv(
    PROJECT_DIR / "analysis/output/tip_subset_eligible.csv",
    parse_dates=["signal_time"],
).head(30)
series_paths = {(s, y): p for (s, y), p in list_series(bar="15m").items()}
model = load_yolo_model(PROJECT_DIR / "models" / "owner_best.pt")
tmp = PROJECT_DIR / "data" / "_tip_probe.png"

rows = []
for r in eligible.itertuples():
    paths = series_paths.get((r.source, r.symbol))
    if paths is None:
        continue
    frame = load_series(paths)
    pos = int(r.signal_i)
    if not (0 <= pos < len(frame)) or frame["open_time"].iloc[pos] != r.signal_time:
        continue
    start = pos - WINDOW + 1
    if start < 0:
        continue
    out = {"symbol": r.symbol, "signal_i": pos}
    subs = {
        "full_ma": add_mas(frame).iloc[start : pos + 1],
        "slice_ma": add_mas(frame.iloc[start : pos + 1].reset_index(drop=True)),
    }
    for name, sub in subs.items():
        _, tf = render_chart(sub, out_path=tmp)
        res = model.predict(str(tmp), conf=0.30, verbose=False, device="cpu")[0]
        best_bar, best_norm, n = -1, 0.0, 0
        if res.boxes is not None and len(res.boxes):
            n = len(res.boxes)
            for b in res.boxes.xywhn.cpu().numpy():
                cx, _, w, _ = map(float, b[:4])
                best_bar = max(best_bar, right_edge_to_bar(cx, w, tf, n_bars=WINDOW))
                best_norm = max(best_norm, cx + w / 2)
        out[f"{name}_n"] = n
        out[f"{name}_bar"] = best_bar
        out[f"{name}_norm"] = round(best_norm, 3)
    rows.append(out)

df = pd.DataFrame(rows)
print(df.to_string())
print("full_ma tip(bar>=199):", (df["full_ma_bar"] >= 199).sum(), "/", len(df))
print("slice_ma tip(bar>=199):", (df["slice_ma_bar"] >= 199).sum(), "/", len(df))
