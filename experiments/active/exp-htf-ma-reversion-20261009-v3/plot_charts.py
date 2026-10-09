"""Summary chart for exp-htf-ma-reversion-20261009-v3 (OKX ETHUSDT.P, two-year parameter search).

Left: cumulative net % (per-trade net summed, 1x notional) of the select-year best cell over both
years, its matched random entries, and the owner-sample-like cell (5m long, 5%, 0.6% stop, 5R,
re-entry). Right: every cell's select-year vs check-year total net %, with the select top 10, the
owner-like cell and the two-year in-sample best marked. Times in UTC+8.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v3/plot_charts.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import htf_ma_reversion_v1 as hr  # noqa: E402

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v3")
TZ = "Asia/Shanghai"
OWNER_LIKE = {"minutes": 5, "side": 1, "vix": False, "measure": "pct", "level": 0.05, "stop": "pct0.6",
              "target_r": 5, "reentry": 3}

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams.update({"xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "axes.labelsize": 8})


def pick(frame: pd.DataFrame, spec: dict) -> pd.DataFrame:
    m = np.ones(len(frame), bool)
    for k, v in spec.items():
        m &= (frame[k] == v).to_numpy()
    return frame.loc[m]


def describe(spec: dict) -> str:
    side = "做多" if spec["side"] == 1 else "做空"
    dist = {"pct": f"离线 {100 * spec['level']:g}%", "atr": f"离线 {spec['level']:g} 倍 ATR",
            "pctl": f"30 天 {100 * spec['level']:g}% 分位"}[spec["measure"]]
    stop = "止损=到线距离/3" if spec["stop"] == "line3" else f"止损 {spec['stop'][3:]}%"
    extra = ("，开 VixFix" if spec["vix"] else "") + ("，止损后可再进" if spec["reentry"] else "")
    return f"{spec['minutes']}m {side} {dist} {stop} 1:{spec['target_r']}{extra}"


def main() -> None:
    res = EXP / "results"
    trades = pd.read_csv(res / "trades.csv.gz")
    trades = trades.loc[np.isfinite(trades.net_ret) & np.isfinite(trades.control_net_ret)]
    summary = pd.read_csv(res / "summary.csv")
    cell = hr.cell_keys(summary)
    top = pd.read_csv(res / "top10.csv")
    best = {k: top.iloc[0][k] for k in cell}
    split = pd.Timestamp("2025-10-09", tz="UTC").tz_convert(TZ)
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.25, 1]})
    for spec, color, name in ((best, "#2980b9", "第一年最优：" + describe(best)),
                              (OWNER_LIKE, "#e67e22", "你的样例参数：" + describe(OWNER_LIKE))):
        g = pick(trades, spec).sort_values("time")
        t = pd.to_datetime(g.time).dt.tz_convert(TZ)
        ax.plot(t, 100 * g.net_ret.cumsum(), color=color, lw=1.6, label=f"{name}（{len(g)} 笔）")
        if spec is best:
            ax.plot(t, 100 * g.control_net_ret.cumsum(), color="#95a5a6", lw=1.2, label="同一批时间的随机入场对照")
    ax.axvline(split, color="black", ls="--", lw=1)
    ax.axhline(0, color="#bbbbbb", lw=0.8)
    ax.text(0.02, 0.97, "第一年：挑参数", transform=ax.transAxes, fontsize=8, va="top")
    ax.text(0.98, 0.97, "第二年：检验", transform=ax.transAxes, fontsize=8, va="top", ha="right")
    ax.set_title("累计净收益（每笔 % 相加，已扣 0.2% 成本）", fontsize=9)
    ax.set_ylabel("累计 %")
    ax.legend(loc="lower left", fontsize=6.5)
    ax.grid(alpha=0.2)
    s = summary.assign(total=summary.mean_net_pct * summary.trades)
    w = s.pivot_table(index=cell, columns="period", values="total").dropna()
    bx.scatter(w.select, w.check, s=4, alpha=0.25, color="#7f8c8d", label=f"全部 {len(w)} 组参数")
    t10 = top.set_index(cell)
    bx.scatter(t10.select_total_net_pct, t10.check_total_net_pct, s=40, facecolor="none", edgecolor="#2980b9", lw=1.3,
               label="第一年前 10 名")
    ow = w.loc[tuple(OWNER_LIKE[k] for k in cell)]
    bx.scatter([ow.select], [ow.check], marker="*", s=150, color="#e67e22", edgecolor="black", zorder=5,
               label="你的样例参数")
    both = w.select + w.check
    ib = w.loc[both.idxmax()]
    bx.scatter([ib.select], [ib.check], marker="D", s=40, color="#27ae60", edgecolor="black", zorder=5,
               label="两年合计最好（事后挑的，不可信）")
    bx.axhline(0, color="black", lw=0.8)
    bx.axvline(0, color="black", lw=0.8)
    bx.set_xlabel("第一年合计净收益 %")
    bx.set_ylabel("第二年合计净收益 %")
    bx.set_title(f"第一年 vs 第二年 · 两年都赚的 {int(((w.select > 0) & (w.check > 0)).sum())} 组 / {len(w)}", fontsize=9)
    bx.legend(loc="lower left", fontsize=6.5)
    bx.grid(alpha=0.2)
    fig.suptitle("ETHUSDT.P (OKX) 近两年参数寻优：第一年最优的参数，第二年亏回去了", fontsize=11)
    fig.tight_layout()
    out = EXP / "charts"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "two_year_search.png", dpi=100)
    print(out / "two_year_search.png")


if __name__ == "__main__":
    main()
