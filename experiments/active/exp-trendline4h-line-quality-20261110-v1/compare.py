"""Old V1.2 line (blue) vs prototype V1.3 lines (orange) on the same windows as the V1.2 sample.
Usage: python3 compare.py <sample.csv> <out_prefix> [max_gap]"""
import sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "exp-trendline4h-breakout-runs-20261109-v1"))
import trendline_v12 as v12, trendline_v13 as v13
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
SRC = HERE.parents[2] / "data/crypto/market/okx_swap_4h_20261109"
def lines_of(ev, n):
    out, cur = [], None
    for e in ev:
        if e.kind == "born":
            if cur and cur["end"] is None: cur["end"], cur["how"] = e.x, "replaced"
            cur = dict(a=e.a_x, ay=e.a_y, b=e.b_x, by=e.b_y, born=e.x, end=None, how=None); out.append(cur)
        elif cur and cur["end"] is None: cur["end"], cur["how"] = e.x, e.kind
    if cur and cur["end"] is None: cur["end"], cur["how"] = n - 1, "open"
    return out
def draw(ax, L, lo, hi, color, lw):
    lv = lambda x: L["by"] + (L["by"] - L["ay"]) * (x - L["b"]) / (L["b"] - L["a"])
    x1, x2 = max(lo, L["a"] - 72), min(hi, L["end"] + 24)
    if x2 <= x1: return
    ax.plot([x1, x2], [lv(x1), lv(x2)], color=color, lw=lw)
    ax.scatter([L["a"], L["b"]], [L["ay"], L["by"]], marker="s", s=18, color=color, zorder=5)
    mk = {"break": "^", "age": "x", "replaced": "D", "open": "o"}[L["how"]]
    if lo <= L["end"] <= hi: ax.scatter([L["end"]], [lv(L["end"])], marker=mk, s=36, color=color, zorder=6)
def main():
    s = pd.read_csv(sys.argv[1], index_col=0); out = sys.argv[2]; g = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    cache = {}
    for page in range(0, len(s), 12):
        fig, axes = plt.subplots(4, 3, figsize=(18, 16))
        for ax, r in zip(axes.flat, s.iloc[page:page + 12].itertuples()):
            if r.symbol not in cache:
                df = pd.read_csv(next(SRC.glob(f"okx_{r.symbol}_4H_*.csv")))
                arr = [df[k].to_numpy(float) for k in ("open", "high", "low", "close")]
                cache[r.symbol] = (df, arr, lines_of(v13.run(*arr, max_gap=g).events, len(df)))
            df, (o, h, l, c), new = cache[r.symbol]
            lo, hi = max(0, int(r.a_x) - 80), min(len(c) - 1, int(r.end) + 40)
            xs = np.arange(lo, hi + 1); up = c[xs] >= o[xs]
            ax.vlines(xs, l[xs], h[xs], color="#999", lw=.5)
            ax.vlines(xs[up], o[xs][up], c[xs][up], color="#26a69a", lw=1.6); ax.vlines(xs[~up], c[xs][~up], o[xs][~up], color="#ef5350", lw=1.6)
            draw(ax, dict(a=int(r.a_x), ay=r.a_y, b=int(r.b_x), by=r.b_y, born=int(r.born), end=int(r.end), how=r.how), lo, hi, "#1f77b4", 1.0)
            k = 0
            for L in new:
                if L["born"] <= hi and L["end"] + 24 >= lo and L["a"] >= lo - 72:
                    draw(ax, L, lo, hi, "#ff7f0e", 1.6); k += 1
            ax.set_xlim(lo, hi); ax.set_ylim(l[xs].min() * .97, h[xs].max() * 1.03)
            ax.set_title(f"#{r.Index} {r.symbol.replace('_USDT_SWAP','')} 蓝=V1.2 橙=新({k}条)", fontsize=8); ax.tick_params(labelsize=6)
        fig.tight_layout(); fig.savefig(f"{out}_p{page // 12 + 1}.png", dpi=80); plt.close(fig)
main()
