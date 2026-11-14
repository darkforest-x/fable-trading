"""Shape metrics of each V1.2 line over its anchor span [A, B], in ATR units.

gap_med: median over bars of (line - high)/ATR (how far price floats below the line)
depth: max of (line - low)/ATR (V depth); wickA/wickB: anchor upper wick above body top, ATR.
Inputs are bars <= B (known at birth). No outcome used.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp-trendline4h-breakout-runs-20261109-v1"))
import trendline_v12 as t
SRC = HERE.parents[2] / "data/crypto/market/okx_swap_4h_20261109"
ln = pd.read_csv(HERE.parent / "exp-trendline4h-cloudflip-1h-exit-20261109-v1/lines.csv")
rows = []
for sym, g in ln.groupby("symbol"):
    df = pd.read_csv(next(SRC.glob(f"okx_{sym}_4H_*.csv")))
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = t.pine_atr(h, l, c); top = np.maximum(o, c)
    for r in g.itertuples():
        a, b = int(r.a_x), int(r.b_x); i = np.arange(a, b + 1)
        lv = r.b_y + (r.b_y - r.a_y) * (i - b) / (b - a)
        rows.append(dict(idx=r.Index, gap_med=np.nanmedian((lv - h[i]) / atr[i]), depth=np.nanmax((lv - l[i]) / atr[i]),
                         wickA=(h[a] - top[a]) / atr[a], wickB=(h[b] - top[b]) / atr[b],
                         drop_atr=(r.a_y - r.b_y) / atr[b]))
m = pd.DataFrame(rows).set_index("idx").sort_index()
m.to_csv(HERE / "line_metrics.csv")
print(m.describe(percentiles=[.1, .25, .5, .75, .9]).round(2).to_string())
bad = [2970, 5197, 3126, 87, 4833, 830, 5203, 1122, 4208]
s = pd.read_csv(HERE / "sample_v12_sample.csv", index_col=0)
x = m.loc[s.index].assign(bad=lambda d: d.index.isin(bad))
print(x.sort_values("gap_med").round(2).to_string())
