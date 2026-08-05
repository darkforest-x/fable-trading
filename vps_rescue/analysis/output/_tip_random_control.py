"""Control: v12 tip-fire rate on RANDOM (non-signal) windows, both MA semantics.

If v12 fires at the tip on random slice-MA windows as often as on signal
windows, its 92.5% tip_hit_rate is a render-artifact (NaN MA warmup), not
signal discrimination. Torch-only process.
"""
from __future__ import annotations

import random
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
N_SAMPLES = 60
random.seed(20260721)

eligible = pd.read_csv(
    PROJECT_DIR / "analysis/output/tip_subset_eligible.csv", parse_dates=["signal_time"]
)
signal_keys = set(zip(eligible["symbol"], eligible["signal_i"]))
# Avoid bars within 20 of any eligible signal for the same symbol.
sig_by_sym: dict[str, list[int]] = {}
for sym, si in signal_keys:
    sig_by_sym.setdefault(sym, []).append(si)

groups = list(list_series(bar="15m").items())
random.shuffle(groups)
model = load_yolo_model(PROJECT_DIR / "models" / "owner_best.pt")
tmp = PROJECT_DIR / "data" / "_tip_ctrl.png"

rows = []
for (source, symbol), paths in groups:
    if len(rows) >= N_SAMPLES:
        break
    if source != "okx" or not symbol.endswith("_USDT_SWAP"):
        continue
    frame = load_series(paths)
    if len(frame) < WINDOW + 400:
        continue
    for _try in range(10):
        pos = random.randint(WINDOW + 200, len(frame) - 80)
        if all(abs(pos - s) > 20 for s in sig_by_sym.get(symbol, [])):
            break
    else:
        continue
    start = pos - WINDOW + 1
    out = {"symbol": symbol, "pos": pos}
    subs = {
        "full_ma": add_mas(frame).iloc[start : pos + 1],
        "slice_ma": add_mas(frame.iloc[start : pos + 1].reset_index(drop=True)),
    }
    for name, sub in subs.items():
        _, tf = render_chart(sub, out_path=tmp)
        res = model.predict(str(tmp), conf=0.30, verbose=False, device="cpu")[0]
        best_bar, best_conf, n = -1, 0.0, 0
        if res.boxes is not None and len(res.boxes):
            n = len(res.boxes)
            for b, c in zip(res.boxes.xywhn.cpu().numpy(), res.boxes.conf.cpu().numpy()):
                cx, _, w, _ = map(float, b[:4])
                bar = right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
                if bar > best_bar:
                    best_bar = bar
                if bar >= WINDOW - 1:
                    best_conf = max(best_conf, float(c))
        out[f"{name}_n"] = n
        out[f"{name}_tipbar"] = best_bar
        out[f"{name}_tipconf"] = round(best_conf, 3)
    rows.append(out)

df = pd.DataFrame(rows)
print(df.to_string())
print(f"random windows n={len(df)}")
print("full_ma  tip fire:", int((df['full_ma_tipbar'] >= 199).sum()))
print("slice_ma tip fire:", int((df['slice_ma_tipbar'] >= 199).sum()))
