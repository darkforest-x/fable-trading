"""Render sampled V1.2 4H lines on their own price action for a visual quality audit.

Each panel: 4H candles, the line from its 12-day left guide to its end (+24 bars
tail), anchors A/B (squares), counted retest touches (circles), all strict
pivot highs (grey dots), birth (dotted vertical) and end (break = triangle,
age-out = x, replaced = diamond). Sample: seeded random lines, no outcome used.
Usage: python3 gallery.py <lines.csv> <out_prefix> [n] [seed] [since YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp-trendline4h-breakout-runs-20261109-v1"))
import trendline_v12 as t  # noqa: E402

SRC = HERE.parents[2] / "data/crypto/market/okx_swap_4h_20261109"
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]


def touches(h, atr, piv, a, b, gap=24, near=.65):
    lvl = lambda x: h[b] + (h[b] - h[a]) * (x - b) / (b - a)
    out, last = [], a
    for p in piv:
        if p <= a:
            continue
        if p > b - gap:
            break
        if p - last >= gap and abs(h[p] - lvl(p)) <= near * atr[p]:
            out.append(p)
            last = p
    return out


def panel(ax, row, df_cache):
    f = next(SRC.glob(f"okx_{row.symbol}_4H_*.csv"))
    df = df_cache.setdefault(row.symbol, pd.read_csv(f))
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = t.pine_atr(h, l, c)
    conf, _ = t.strict_pivot_highs(h, 3)
    piv = np.flatnonzero(conf) - 3
    a, b, born, end = int(row.a_x), int(row.b_x), int(row.born), int(row.end)
    lo, hi = max(0, a - 80), min(len(c) - 1, end + 40)
    xs = np.arange(lo, hi + 1)
    up = c[xs] >= o[xs]
    ax.vlines(xs, l[xs], h[xs], color="#888", lw=.5)
    ax.vlines(xs[up], o[xs][up], c[xs][up], color="#26a69a", lw=1.6)
    ax.vlines(xs[~up], c[xs][~up], o[xs][~up], color="#ef5350", lw=1.6)
    lvl = lambda x: row.b_y + (row.b_y - row.a_y) * (x - b) / (b - a)
    x2 = min(hi, end + 24)
    ax.plot([max(lo, a - 72), x2], [lvl(max(lo, a - 72)), lvl(x2)], color="#1f77b4", lw=1.2)
    pv = piv[(piv >= lo) & (piv <= hi)]
    ax.scatter(pv, h[pv], s=6, color="#bbb", zorder=3)
    tt = touches(h, atr, piv, a, b)
    ax.scatter(tt, h[tt], s=28, facecolors="none", edgecolors="orange", zorder=4)
    ax.scatter([a, b], [h[a], h[b]], marker="s", s=24, color="#1f77b4", zorder=5)
    ax.axvline(born, color="k", ls=":", lw=.8)
    mk = {"break": "^", "age": "x", "replaced": "D", "open": "o"}[row.how]
    ax.scatter([end], [c[end]], marker=mk, s=40, color="purple", zorder=6)
    ax.set_xlim(lo, hi)
    ax.set_ylim(l[xs].min() * .97, h[xs].max() * 1.03)
    date = pd.to_datetime(df.ts[born], unit="ms").strftime("%Y-%m-%d")
    ax.set_title(f"#{row.Index} {row.symbol.replace('_USDT_SWAP','')} 建线{date} 触点{row.touches if hasattr(row,'touches') else len(tt)+2} {row.how}", fontsize=8)
    ax.tick_params(labelsize=6)


def main() -> int:
    lines = pd.read_csv(sys.argv[1])
    out = Path(sys.argv[2])
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    if len(sys.argv) > 5:
        cut = pd.Timestamp(sys.argv[5], tz="UTC").value // 1_000_000
        lines = lines[lines.born_close >= cut]
    sample = lines.sample(n=min(n, len(lines)), random_state=seed)
    cache: dict = {}
    for page in range(0, len(sample), 12):
        fig, axes = plt.subplots(4, 3, figsize=(18, 16))
        for ax, row in zip(axes.flat, sample.iloc[page:page + 12].itertuples()):
            panel(ax, row, cache)
        for ax in axes.flat[len(sample.iloc[page:page + 12]):]:
            ax.axis("off")
        fig.tight_layout()
        fig.savefig(f"{out}_p{page // 12 + 1}.png", dpi=80)
        plt.close(fig)
    sample.to_csv(f"{out}_sample.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
