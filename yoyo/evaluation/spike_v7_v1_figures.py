"""Render outcome-labelled V7 retained and missed trend examples from replay caches.

Images are retrospective review artifacts: they deliberately show future bars,
which were not inputs to the entry filter. Selection is by realized net R and
is illustrative only, never an estimate of strategy performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v7_v1_report import bools


def choose_examples(trades: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    """Pick winners, losses and missed tails with explicit retrospective labels."""
    closed = trades.loc[~bools(trades.censored)].copy()
    keys = ["venue", "symbol", "timeframe_min", "segment", "side", "signal_bar_open"]
    v7 = closed.loc[closed.variant.eq("v7_bb_both")]
    base = closed.loc[closed.variant.eq("v6_unfiltered_both")]
    missing = base.merge(v7[keys].assign(_retained=True), on=keys, how="left")
    missing = missing.loc[missing._retained.isna()]
    selected = []
    for title, frame, ascending in (("V7 realized winner", v7, False),
                                    ("V7 realized loss", v7, True),
                                    ("V6 trend missed by V7", missing, False)):
        for _, row in frame.sort_values("net_r", ascending=ascending).drop_duplicates(["symbol", "timeframe_min"]).head(2).iterrows():
            selected.append((title, row))
    return selected


def render(raw: Path, post: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(post / "common_execution_trades.csv.gz")
    folders = {}
    for path in (raw / "streams").glob("*/receipt.csv"):
        if path.parent.name.startswith("."):
            continue
        r = pd.read_csv(path).iloc[0]
        folders[(r.venue, r.symbol, int(r.minutes), int(r.segment))] = path.parent
    receipts = []
    for n, (title, trade) in enumerate(choose_examples(trades), 1):
        folder = folders[(trade.venue, trade.symbol, int(trade.timeframe_min), int(trade.segment))]
        cache_file = folder / "control_cache.pkl.gz"
        receipt = json.loads((folder / "control_cache.receipt.json").read_text())
        if hashlib.sha256(cache_file.read_bytes()).hexdigest() != receipt["cache_sha256"]:
            raise ValueError("figure cache hash mismatch")
        bars = pd.read_pickle(cache_file)["bars"]
        begin, end = pd.Timestamp(trade.signal_bar_open), pd.Timestamp(trade.exit_time)
        i, j = int(bars.index.get_indexer([begin])[0]), int(bars.index.searchsorted(end))
        if i < 0:
            raise ValueError("trade signal missing from source cache")
        left, right = max(0, i-100), min(len(bars), max(j+40, i+100))
        shown = bars.iloc[left:right].copy()
        # Long holds use evenly spaced review bars without pretending those
        # bars are a new execution timeframe; ordinary cases show every bar.
        stride = max(1, int(np.ceil(len(shown)/1100)))
        shown = shown.iloc[::stride]
        x = np.arange(len(shown))
        fig, (ax, osc) = plt.subplots(2, 1, figsize=(16, 8), sharex=True,
                                      gridspec_kw={"height_ratios": [4, 1]}, facecolor="#0b121b")
        for panel in (ax, osc):
            panel.set_facecolor("#0e1824")
            panel.grid(alpha=.14, color="#8092a4")
            panel.tick_params(colors="#aabbca", labelsize=8)
            for spine in panel.spines.values():
                spine.set_color("#263746")
        colors = np.where(shown.close >= shown.open, "#4bd0a8", "#ef7984")
        ax.add_collection(LineCollection([[(k,l), (k,h)] for k,l,h in zip(x,shown.low,shown.high)], colors=colors, linewidths=.8))
        for k, o, c, color in zip(x, shown.open, shown.close, colors):
            ax.add_patch(Rectangle((k-.3,min(o,c)), .6, max(abs(c-o), abs(c)*.00001), facecolor=color, edgecolor="none"))
        for col, color in zip(("s20","e20","s60","e60","s120","e120"),
                              ("#59c6ba","#86d2c9","#6496cb","#8bafd5","#a5abb8","#d2d8e0")):
            ax.plot(x, shown[col], color=color, linewidth=.8, alpha=.7)
        osc.plot(x, shown.md, color="#6d9eff", linewidth=1.3, label="IMACD")
        osc.plot(x, shown.sb, color="#eeb465", linewidth=1.2, label="Signal")
        osc.axhline(0,color="#8797a7",linewidth=.6)
        si, ei = (i-left)/stride, (j-left)/stride
        ax.axvspan(si, len(shown), color="#527ea2", alpha=.07)
        for panel in (ax,osc):
            panel.axvline(si,color="#edd586",linestyle="--",linewidth=1)
            panel.axvline(ei,color="#f194a4",linestyle=":",linewidth=1)
        ax.scatter([si+1/stride], [trade.entry_price], marker="^" if trade.side==1 else "v", s=65,color="#edd586",zorder=7)
        ax.scatter([ei], [trade.exit_price], marker="x", s=55,color="#f194a4",zorder=7)
        ax.set_ylim(shown.low.min()*.99, shown.high.max()*1.01)
        ax.set_xlim(-1,len(shown))
        ax.set_title(f"{title} | {trade.venue.upper()} {trade.symbol} {int(trade.timeframe_min)}m | "
                     f"{'LONG' if trade.side==1 else 'SHORT'} | realized {trade.net_r:.2f}R",loc="left",color="#e6edf5",pad=15,fontsize=13)
        ticks = np.linspace(0,len(shown)-1,8).astype(int)
        osc.set_xticks(ticks, [shown.index[k].tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M") for k in ticks])
        osc.legend(loc="upper left",facecolor="#0e1824",edgecolor="none",labelcolor="#aabbca",fontsize=8)
        fig.text(.06,.025,f"Yellow line: signal candle; triangle: next-open entry. Pink: simulated exit. "
                 f"Shaded future is review-only. UTC+8. Cost 0.2% round trip. Exit: {trade.exit_reason}. "
                 f"Review stride: {stride}.",color="#9eafbf",fontsize=9)
        fig.subplots_adjust(left=.06,right=.97,top=.93,bottom=.09,hspace=.1)
        filename=f"{n:02d}_{trade.venue}_{trade.symbol}_{int(trade.timeframe_min)}m.png"
        fig.savefig(output/filename,dpi=150,facecolor=fig.get_facecolor())
        plt.close(fig)
        receipts.append({"figure":filename,"selection":title,"variant":trade.variant,"venue":trade.venue,
                         "symbol":trade.symbol,"timeframe_min":int(trade.timeframe_min),"side":int(trade.side),
                         "signal_bar_open":str(begin),"net_r":float(trade.net_r),"cache_sha256":receipt["cache_sha256"],
                         "review_stride":stride,"future_for_review_only":True})
    (output/"figures_manifest.json").write_text(json.dumps(receipts,indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw",type=Path); parser.add_argument("post",type=Path); parser.add_argument("output",type=Path)
    args=parser.parse_args()
    render(args.raw,args.post,args.output)
