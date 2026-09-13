"""Render the owner's COMP 1H moving-average sequence case from reviewed rows.

This is a descriptive historical figure, not a strategy or performance test.
Input is complete, closed 1H OHLCV plus causal SMA/EMA20/60/120. No computed
future ordering is backdated to an entry. Screenshot markers identify the
owner-selected examples; they do not assert a particular Pine build fired.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd


def render(frame: pd.DataFrame, output: Path) -> None:
    """Use reviewed time_bj, OHLC and s/e20/60/120 columns; no outcome labels."""
    df = frame.rename(columns={"time_shanghai":"time_bj", **{f"{a}{n}":f"{b}{n}" for a,b in (("sma","s"),("ema","e")) for n in (20,60,120)}}).copy()
    df["time_bj"] = pd.to_datetime(df["time_bj"], utc=True).dt.tz_convert("Asia/Shanghai")
    df = df.set_index("time_bj").loc["2026-07-23":"2026-08-03"]
    assert len(df) >= 250 and df.index.is_unique
    assert (df.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
    plt.rcParams.update({"font.family": "Arial Unicode MS", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(3, 1, figsize=(17, 11), sharex=True,
                             gridspec_kw={"height_ratios": [4.1, 1.15, 1.15]})
    fig.patch.set_facecolor("#FAFBFC")
    fig.subplots_adjust(left=.064, right=.978, top=.875, bottom=.09, hspace=.12)
    fig.text(.064, .96, "COMP · 1H 均线形成过程", fontsize=23, weight="bold", color="#202E39")
    fig.text(.064, .923, "OKX · 2026/07/23–08/03 · 北京时间（K线开盘标时） · 2笔多头与1笔空头的已知案例", color="#536676")
    fig.text(.064, .895, "排列与扩散按当根收盘计算；之后形成的状态不能提前用于原箭头。峰值幅度不等于已实现收益。", fontsize=10, color="#687783")
    x = np.arange(len(df))
    for ax in axes:
        ax.set_facecolor("white")
        ax.grid(axis="y", color="#E6EBEF", linewidth=.7)
        ax.spines["left"].set_color("#CAD3DB")
        ax.spines["bottom"].set_color("#CAD3DB")
    for i, row in enumerate(df.itertuples()):
        up = row.close >= row.open
        col = "#437CB1" if up else "#657681"
        axes[0].vlines(i, row.low, row.high, color=col, linewidth=.75)
        axes[0].add_patch(Rectangle((i-.32, min(row.open, row.close)), .64,
                          max(abs(row.close-row.open), .004),
                          facecolor="white" if up else col, edgecolor=col, linewidth=.7, zorder=3))
    palette = {20: "#23799D", 60: "#BD8431", 120: "#555F68"}
    for n in (20, 60, 120):
        for kind, style in (("s", "-"), ("e", "--")):
            axes[0].plot(x, df[f"{kind}{n}"], color=palette[n], linestyle=style,
                         linewidth=1.35, label=f"{'SMA' if kind == 's' else 'EMA'}{n}")
    pairs = [(a, b) for na, nb in ((20,60),(20,120),(60,120))
             for a in (f"s{na}", f"e{na}") for b in (f"s{nb}", f"e{nb}")]
    short_score = sum((df[a] < df[b]).astype(int) for a,b in pairs)
    axes[1].plot(x, short_score, color="#23799D", linewidth=1.5)
    axes[1].set_ylim(-.6,12.6)
    axes[1].set_yticks([0,6,12], ["0 · 多序", "6 · 混合", "12 · 空序"])
    axes[1].set_ylabel("跨周期排序")
    axes[1].axhline(6, color="#8796A1", linewidth=.7, linestyle="--")
    width = df[[f"{k}{n}" for n in (20,60,120) for k in ("s","e")]].max(axis=1)-df[[f"{k}{n}" for n in (20,60,120) for k in ("s","e")]].min(axis=1)
    axes[2].plot(x, width/df.close*100, color="#BD8431", linewidth=1.5)
    axes[2].set_ylabel("六线宽度 / 价格 %")
    entries = [("2026-07-24 00:00",17.61,"A 多头 17.61",35),
               ("2026-07-26 12:00",17.43,"B 多头 17.43",36),
               ("2026-07-27 16:00",17.20,"C 空头 17.20",-43)]
    for when, price, label, offset in entries:
        t = pd.Timestamp(when, tz="Asia/Shanghai")
        i = df.index.get_loc(t)
        for ax in axes:
            ax.axvline(i, color="#677986", linestyle=":", linewidth=1)
        axes[0].scatter([i], [price], marker="D", s=31, color="#293B49", zorder=5)
        axes[0].annotate(label, (i,price), xytext=(0,offset), textcoords="offset points",
                         ha="center", fontsize=10, color="#243846",
                         arrowprops={"arrowstyle":"-", "color":"#7A8C98"},
                         bbox={"facecolor":"white", "edgecolor":"none", "pad":2})
    j=df.index.get_loc(pd.Timestamp("2026-07-28 20:00",tz="Asia/Shanghai"))
    axes[1].annotate("首次完整空序 · 已晚28根", (j,12), xytext=(28,-32),
                     textcoords="offset points", fontsize=10,color="#243846",
                     arrowprops={"arrowstyle":"-", "color":"#7A8C98"})
    i = df.index.get_loc(pd.Timestamp("2026-07-30 00:00", tz="Asia/Shanghai"))
    for ax in axes:
        ax.axvspan(i,len(df)-1,color="#E8EDF1",alpha=.55,zorder=0)
    axes[0].text(i+10,15.89,"下跌后重新收拢\n仍需重新识别方向", fontsize=12,color="#526878")
    axes[0].legend(ncol=6, loc="upper left", frameon=False, fontsize=10)
    axes[0].set_ylabel("USDT")
    axes[0].set_ylim(df.low.min()-.09,df.high.max()+.12)
    ticks = [i for i,t in enumerate(df.index) if t.hour==0]
    axes[2].set_xticks(ticks, [df.index[i].strftime("%m/%d") for i in ticks])
    axes[2].set_xlim(-1,len(df))
    fig.text(.064,.032,"数据：本机 OKX 15m 原始缓存，每4根完整聚合1H；排序比较20/60/120三组间12对，不要求同周期SMA与EMA固定先后。",fontsize=10,color="#687783")
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=155,facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    render(pd.read_csv(args.csv),args.output)


if __name__ == "__main__":
    main()
