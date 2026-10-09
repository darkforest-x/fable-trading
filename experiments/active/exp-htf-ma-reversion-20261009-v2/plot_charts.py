"""Charts for exp-htf-ma-reversion-20261009-v2 (owner sample: TradingView long box 2411 / 2396 / 2491).

Times in UTC+8, TradingView-style position boxes (green = entry to target, red = entry to stop, from
entry to exit). Figures:

  1_owner_sample_vs_rule.png  the owner's 2026-10-08/09 ETH sample on OKX 5m next to what the rule
                              (5% from the line, 0.6% stop, 5R) does with one entry and with re-entry
  2_eth_six_longs.png         six ETH longs after 2025 from the v2 cell drawn the same way
  3_results.png               cumulative net vs matched random entries and every v2 cell's two periods

The sample figure reads data/research/htf_ma_reversion_okx_tail_20261009 (OKX history-candles, fetched
2026-10-09 with src.data.fetch_okx --bar 5m --days 20); the others read results/trades.csv.gz.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v2/plot_charts.py [--only sample]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from yoyo.evaluation import htf_ma_reversion_v1 as hr  # noqa: E402

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v2")
OKX = Path("data/research/htf_ma_reversion_okx_tail_20261009/okx_ETH_USDT_SWAP_5m_5799.csv")
CELL = {"minutes": 5, "vix": False, "measure": "pct", "level": 0.05, "stop": "pct0.6", "target_r": 5}
SAMPLE = {"entry_utc": "2026-10-08T17:25:00Z", "fill": 2411.0, "stop": 2396.0, "target": 2491.0}
TZ = "Asia/Shanghai"
UP, DOWN, LINE, TRIG = "#26a69a", "#ef5350", "#9c6ade", "#f39c12"

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams.update({"xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "axes.labelsize": 8})


def candles(ax, o, h, l, c, width: float = 0.7) -> None:
    x = np.arange(len(o))
    col = np.where(c >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, linewidth=0.6, zorder=2)
    ax.bar(x, np.maximum(np.abs(c - o), (np.nanmax(h) - np.nanmin(l)) * 0.0008), bottom=np.minimum(o, c),
           width=width, color=col, linewidth=0, zorder=3)


def time_ticks(ax, index: np.ndarray, n: int = 6) -> None:
    pos = np.linspace(0, len(index) - 1, n).astype(int)
    labels = pd.to_datetime(index[pos], unit="ms", utc=True).tz_convert(TZ).strftime("%m-%d %H:%M")
    ax.set_xticks(pos, labels)


def position_box(ax, xi: int, xe: int, side: int, fill: float, stop: float, target: float, kind: str,
                 note: str = "", labels: bool = True) -> None:
    """TradingView-style box: green entry->target, red entry->stop, from entry to exit."""
    span = max(xe - xi, 1)
    ax.add_patch(Rectangle((xi, min(fill, target)), span, abs(target - fill), fc=UP, alpha=0.22, ec=UP, lw=0.8,
                           zorder=1, label="止盈区" if labels else None))
    ax.add_patch(Rectangle((xi, min(fill, stop)), span, abs(stop - fill), fc=DOWN, alpha=0.22, ec=DOWN, lw=0.8,
                           zorder=1, label="止损区" if labels else None))
    ax.hlines(fill, xi, xi + span, color="#444444", lw=0.8, zorder=5)
    ax.scatter([xi], [fill], marker="^" if side > 0 else "v", s=28, color="#2980b9", edgecolor="white", zorder=6)
    ex = {"stop": stop, "target": target}.get(kind)
    if ex is not None:
        ax.scatter([xe], [ex], marker="X", s=28, color="black", zorder=6)
    if note:
        ax.annotate(note, (xi, stop), xytext=(0, -9 if side > 0 else 9), textcoords="offset points", fontsize=6.5,
                    ha="left", va="top" if side > 0 else "bottom", color="#333333")


def okx_eth() -> dict:
    raw = pd.read_csv(OKX)[["ts", "open", "high", "low", "close", "volume"]].drop_duplicates("ts")
    raw = raw.sort_values("ts").reset_index(drop=True)
    return hr.chart_frames(raw, 5, 15)


def sample_rule_trades(fr: dict, level: float, stop_rule: str, target_r: float, max_re: int) -> list[tuple]:
    """The v2 rule on the OKX tail (5m chart, long side, no Vix Fix); an unfinished trade is dropped."""
    chart, path, per, line = fr["chart"], fr["path"], fr["per"], fr["line"]
    o, h, l, c = (path[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    pidx = path.index.to_numpy()
    chart_of = np.arange(len(path)) // per
    cl, ch, cc = (chart[k].to_numpy(float) for k in ("low", "high", "close"))
    trig = hr.triggers("pct", level, line, fr["atr"], cl, ch, 8640)[1]
    back = ~(np.isfinite(trig) & np.isfinite(cc)) | (cc - (line - (line - trig) / 2) >= 0)
    tp = trig[chart_of]
    start = pd.Timestamp("2026-10-08T12:00:00Z").value // 10**6
    with np.errstate(invalid="ignore"):
        touch = (pidx >= start) & (l <= tp)
    exc = hr.excursions(touch, back, chart_of, per)
    first = [hr.find_entry(p0, j, 1, tp, o, h, l, chart_of, per, None) for p0, j in exc]
    return hr.chain_with_reentry(exc, first, np.flatnonzero(touch), 1, tp, (o, h, l, c), chart_of, per, None,
                                 stop_rule, fr["line"][chart_of], target_r, 576, 0.002, max_re)


def fig_sample(out: Path) -> None:
    fr = okx_eth()
    path = fr["path"]
    pidx = path.index.to_numpy()
    lo = pd.Timestamp("2026-10-08T14:00:00Z").value // 10**6
    hi = pd.Timestamp("2026-10-09T05:00:00Z").value // 10**6
    sel = (pidx >= lo) & (pidx <= hi)
    w = path.loc[sel]
    line_w = fr["line"][np.arange(len(path)) // fr["per"]][sel]
    x0 = int(np.flatnonzero(sel)[0])
    once = sample_rule_trades(fr, 0.05, "pct0.6", 5.0, 0)
    again = sample_rule_trades(fr, 0.05, "pct0.6", 5.0, 3)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2), sharey=True)
    titles = [f"你的样例：{SAMPLE['fill']:.0f} 入场，止损 {SAMPLE['stop']:.0f}（0.6%），止盈 {SAMPLE['target']:.0f}（约 1:5）",
              "规则·只进一次：离线 5% 就开，止损 0.6%，1:5",
              "规则·止损后可再进（同一段偏离最多再进 3 次）"]
    wi = w.index.to_numpy()
    for ax, title in zip(axes, titles):
        candles(ax, *(w[k].to_numpy(float) for k in ("open", "high", "low", "close")))
        ax.step(np.arange(len(w)), line_w, where="mid", color=LINE, lw=1.6, label="V13.1 线 (15m EMA120)")
        ax.step(np.arange(len(w)), line_w * 0.95, where="mid", color=TRIG, lw=1.0, ls="--", label="离线 5%")
        ax.set_title(title, fontsize=8)
        time_ticks(ax, wi)
        ax.grid(alpha=0.15)
    t_entry = pd.Timestamp(SAMPLE["entry_utc"]).value // 10**6
    xi = int(np.searchsorted(wi, t_entry))
    h = w.high.to_numpy()
    xe = next(i for i in range(xi + 1, len(h)) if h[i] >= SAMPLE["target"])
    position_box(axes[0], xi, xe, 1, SAMPLE["fill"], SAMPLE["stop"], SAMPLE["target"], "target",
                 "10:50 止盈，+3.3%（未扣成本）")
    for ax, trades in ((axes[1], once), (axes[2], again)):
        rows = []
        for k, (_, p, fill, _, s) in enumerate(trades):
            risk = fill * 0.006
            position_box(ax, p - x0, int(s["exit_i"]) - x0, 1, fill, fill - risk, fill + 5 * risk, s["kind"],
                         labels=(k == 0))
            ax.annotate(f"{k + 1}", (p - x0, fill + 5 * risk), xytext=(0, 2), textcoords="offset points",
                        fontsize=7, ha="left", va="bottom", fontweight="bold")
            when = pd.Timestamp(int(path.index[p]), unit="ms", tz="UTC").tz_convert(TZ).strftime("%H:%M")
            rows.append(f"{k + 1}  {when} 开 {fill:.0f} → {'止损' if s['kind'] == 'stop' else '止盈'} {100 * s['net']:+.1f}%")
        total = sum(t[4]["net"] for t in trades)
        ax.text(0.98, 0.97, "\n".join(rows + [f"合计（已扣 0.2% 成本）{100 * total:+.1f}%"]), transform=ax.transAxes,
                ha="right", va="top", fontsize=7.5, linespacing=1.5, bbox=dict(boxstyle="round", fc="white", ec="#bbbbbb"))
    axes[0].legend(loc="upper right", fontsize=6.5)
    axes[0].set_ylim(2375, 2600)
    fig.suptitle("ETHUSDT.P (OKX) 5m · 2026-10-08/09 · 你画的那笔 vs 规则在同一段行情里会怎么做", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(out / "1_owner_sample_vs_rule.png", dpi=100)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=EXP / "charts")
    parser.add_argument("--only", choices=["sample"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fig_sample(args.out)
    print(sorted(p.name for p in args.out.glob("*.png")))


if __name__ == "__main__":
    main()
