"""Draw randomly chosen V10.4 joint trades so the ported line can be checked by eye.

Source: the committed runner's outputs (`joints.csv.gz`, `trades.csv.gz`) and the
same 5m archive, rebuilt with the runner's own loader and aggregation so bar
positions match. Examples are drawn with a fixed seed, not picked by outcome.
Each panel shows the frozen A-B line up to the joint bar, the C touch, the
V9 SPIKE bar, the break bar, the next-open entry, the initial stop and the exit.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study


def rebuild(symbol: str, timeframe: str) -> pd.DataFrame:
    minutes = study.TIMEFRAMES[timeframe]
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = study.load_5m(study.series_files()[symbol], earliest)
    since = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * minutes)
    bars, _ = study.aggregate(base.loc[base.index >= since.floor(f"{minutes}min")], minutes)
    return bars


def panel(ax, bars: pd.DataFrame, joint: pd.Series, trade: pd.Series) -> None:
    i, a_i = int(joint.i), int(joint.ax)
    exit_i = int(trade.exit_i) if pd.notna(trade.exit_i) else i + 30
    lo_i, hi_i = max(0, a_i - 15), min(len(bars) - 1, max(exit_i, i) + 15)
    part = bars.iloc[lo_i:hi_i + 1]
    x = np.arange(lo_i, hi_i + 1)
    up = (part.close >= part.open).to_numpy()
    ax.vlines(x, part.low, part.high, color="#8a8f98", linewidth=0.6)
    ax.bar(x, (part.close - part.open).abs(), bottom=np.minimum(part.open, part.close), width=0.7,
           color=np.where(up, "#008F82", "#D34B66"), linewidth=0)
    line_x = np.array([a_i, i])
    slope = (joint.bp - joint.ap) / (joint.bx - joint.ax)
    ax.plot(line_x, joint.ap + slope * (line_x - a_i), color="#222222", linewidth=1.4)
    for label, bx, by in (("A", joint.ax, joint.ap), ("B", joint.bx, joint.bp), ("C", joint.cx, joint.cp)):
        ax.annotate(label, (bx, by), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=8)
    ax.axvline(joint.spike_i, color="#4679C9", linewidth=0.9, linestyle=":")
    ax.axvline(joint.break_i, color="#AD7B29", linewidth=0.9, linestyle="--")
    entry_i = i + 1
    ax.hlines(trade.initial_stop, entry_i, exit_i, color="#D34B66", linewidth=0.8, linestyle="--")
    ax.plot([entry_i], [trade.entry_price], marker="^", color="#008F82", markersize=7)
    if pd.notna(trade.exit_price):
        ax.plot([exit_i], [trade.exit_price], marker="x", color="#222222", markersize=7)
    order = {"same_bar": "同根", "spike_first": "SPIKE→突破", "break_first": "突破→SPIKE"}[joint.order]
    ax.set_title(f"{joint.stream_key.split(':')[1]} {joint.timeframe} {pd.Timestamp(joint.signal_bar_open):%Y-%m-%d %H:%M} "
                 f"· {order} · {trade.net_r:+.2f}R", fontsize=9)
    ax.tick_params(labelsize=7)
    ax.set_xticks([])


def main(stats: Path, out: Path, timeframe: str, count: int, seed: int) -> None:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    joints = pd.read_csv(stats / "joints.csv.gz")
    trades = pd.read_csv(stats / "trades.csv.gz")
    trades = trades.loc[(trades.arm == "joint") & (trades.timeframe == timeframe) & ~trades.censored.astype(bool)]
    pool = trades.sample(n=min(count, len(trades)), random_state=seed).sort_values("signal_bar_open")
    fig, axes = plt.subplots(int(np.ceil(len(pool) / 2)), 2, figsize=(13, 3.2 * np.ceil(len(pool) / 2)))
    for ax, trade in zip(np.ravel(axes), pool.itertuples(index=False)):
        joint = joints.loc[(joints.stream_key == trade.stream_key) & (joints.i == trade.signal_i)].iloc[0]
        panel(ax, rebuild(trade.symbol, timeframe), joint, pd.Series(trade._asdict()))
    for ax in np.ravel(axes)[len(pool):]:
        ax.axis("off")
    fig.suptitle(f"V10.4 突破+spike · {timeframe} · 固定种子随机抽 {len(pool)} 笔（黑线=冻结A-B线，蓝点线=SPIKE，金虚线=突破）",
                 fontsize=10)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--seed", type=int, default=91918)
    args = parser.parse_args()
    main(args.stats, args.out, args.timeframe, args.count, args.seed)
