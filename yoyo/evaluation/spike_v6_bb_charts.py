"""Illustrate fixed V6/BB replay outcomes without recalculating entry decisions.

Examples are selected ex post within predeclared categories and are not a
success-rate sample. Price after entry is displayed for outcome inspection
only. All stored signal decisions were made on closed current/past bars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
import numpy as np
import pandas as pd


KEY = ["symbol", "timeframe_min", "fold", "side", "signal_bar_open"]


def draw(raw: Path, trade: pd.Series, path: Path, category: str):
    cache = pd.read_pickle(raw / f"{trade.symbol}_{int(trade.timeframe_min)}m_bb_diagnostic.pkl.gz")
    bars, diag = cache["bars"], cache["diagnostic"]
    signal, entry, exit_ = [pd.Timestamp(trade[k]) for k in ("signal_bar_open", "entry_time", "exit_time")]
    i, e, j = [int(bars.index.get_loc(t)) for t in (signal, entry, exit_)]
    start, end = max(0, i-250), min(len(bars), j+41)
    v, d = bars.iloc[start:end], diag.iloc[start:end]
    x = np.arange(len(v)); colors = np.where(v.close >= v.open, "#168976", "#cc536c")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig, axes = plt.subplots(4, 1, figsize=(18, 12), sharex=True,
                             gridspec_kw={"height_ratios": [4, 1.1, 1.5, .9]})
    fig.set_facecolor("#f2f5fa")
    axes[0].add_collection(LineCollection([[(k, l), (k, h)] for k, l, h in zip(x, v.low, v.high)], colors=colors, linewidths=.7))
    axes[0].add_collection(PolyCollection([[(k-.32, o), (k+.32, o), (k+.32, c), (k-.32, c)] for k, o, c in zip(x, v.open, v.close)], facecolors=colors, edgecolors=colors, linewidths=.35))
    for col, color, alpha in (("s20", "#1c998f", .8), ("e20", "#1c998f", .4), ("s60", "#5e89ca", .8), ("e60", "#5e89ca", .4), ("s120", "#576578", .8), ("e120", "#576578", .4)):
        axes[0].plot(x, v[col], color=color, alpha=alpha, lw=.85)
    upper, lower = d.bb_basis + 2*d.bb_std_ddof0, d.bb_basis - 2*d.bb_std_ddof0
    axes[0].fill_between(x, lower.to_numpy(), upper.to_numpy(), color="#94a4c3", alpha=.09, label="BB200 / 2 SD")
    axes[0].plot(x, upper, color="#9e8dc0", lw=.9, ls="--")
    axes[0].plot(x, lower, color="#9e8dc0", lw=.9, ls="--")
    axes[0].hlines(trade.initial_stop, e-start, j-start, color="#cc536c", lw=1.1, ls="--", label="Frozen initial SL")
    axes[0].scatter([e-start], [trade.entry_price], marker="^" if trade.side == 1 else "v", color="#075f58", s=90, zorder=8)
    axes[0].scatter([j-start], [trade.exit_price], marker="X", color="#804cb2", s=80, zorder=8)
    axes[0].annotate(f"ENTRY {trade.entry_price:.6g}", (e-start, trade.entry_price), xytext=(8, 23), textcoords="offset points", color="#075f58")
    axes[0].annotate(f"EXIT {trade.exit_price:.6g}\n{trade.exit_reason}", (j-start, trade.exit_price), xytext=(-8, -38), ha="right", textcoords="offset points", color="#804cb2")
    axes[0].set_ylim(v.low.min()*.985, v.high.max()*1.015)
    axes[0].legend(loc="upper left", frameon=False, ncol=3)
    axes[1].plot(x, v.md, color="#4d79be", lw=1.3, label="V6 IMACD")
    axes[1].plot(x, v.sb, color="#c9943c", lw=1.2, label="Signal")
    axes[1].axhline(0, color="#758399", lw=.6)
    axes[1].legend(loc="upper left", frameon=False, ncol=2)
    axes[2].plot(x, d.bb_width*100, color="#546dc6", lw=1.3, label="BB width %")
    axes[2].plot(x, d.bb_width_p10_prior500*100, color="#bc8833", lw=1.1, ls="--", label="Prior 500-bar P10")
    axes[2].fill_between(x, 0, d.bb_width.to_numpy()*100, where=d.bb_compressed.to_numpy(bool), color="#e9b34b", alpha=.5, label="Compressed now")
    axes[2].axvspan(max(0, i-start-12), i-start, color="#e9b34b", alpha=.13, label="12 preceding bars")
    axes[2].legend(loc="upper left", frameon=False, ncol=4)
    axes[3].plot(x, d.rsi6, color="#758797", lw=.85, label="RSI 6")
    axes[3].axhline(50, color="#9c8fae", lw=.8, ls="--")
    axes[3].set_ylim(0, 100); axes[3].legend(loc="upper left", frameon=False)
    for ax in axes:
        ax.set_facecolor("white"); ax.grid(alpha=.16)
        ax.axvspan(e-start, len(v)-1, color="#82bbe7", alpha=.055)
        ax.axvline(i-start, color="#8462bb", lw=1, ls=":")
        ax.spines[["top", "right"]].set_visible(False)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        ax.set_xlim(-1, len(v))
    xt = np.linspace(0, len(v)-1, 10, dtype=int)
    axes[-1].set_xticks(xt, [v.index[k].tz_convert("Asia/Shanghai").strftime("%m-%d\n%H:%M") for k in xt])
    axes[-1].set_xlabel("Beijing time | Violet: V6 signal bar | Blue shade: outcome-only future")
    at = diag.loc[signal]
    suffix = "LONG" if trade.side == 1 else "SHORT"
    axes[0].set_title(f"{category} | {trade.symbol} {int(trade.timeframe_min)}m {suffix}\nRealized {trade.net_r:+.2f}R | MFE {trade.mfe_r:.2f}R (not realized) | Signal {signal.tz_convert('Asia/Shanghai'):%Y-%m-%d %H:%M} open\nBB width {at.bb_width*100:.2f}% / prior P10 {at.bb_width_p10_prior500*100:.2f}% | Prior squeeze {bool(at.bb_prior_run3_in12)} | RSI6 {at.rsi6:.1f}", fontsize=13, loc="left", pad=15)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def build(raw: Path, post: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    trades = pd.read_csv(post / "closed_trades.csv.gz")
    base = trades[trades.variant.eq("A") & trades.fold.eq("validation")].copy()
    kept = trades.loc[trades.variant.eq("B"), KEY].drop_duplicates()
    base = base.merge(kept.assign(retained=True), how="left", on=KEY)
    base["retained"] = base.retained.eq(True)
    examples = []
    for minutes in (15, 30, 60, 240):
        p = base[base.timeframe_min.eq(minutes)]
        for slug, title, mask, asc in (
            ("retained", "B: retained profitable trend", p.retained & p.net_r.gt(0), False),
            ("missed", "B: rejected profitable trend", ~p.retained & p.net_r.gt(0), False),
            ("loss_filtered", "B: filtered loss", ~p.retained & p.net_r.lt(0), True),
        ):
            candidates = p[mask].sort_values(["net_r", "symbol", "signal_bar_open"], ascending=[asc, True, True])
            if candidates.empty:
                examples.append({"timeframe_min": minutes, "category": title, "missing": True}); continue
            t = candidates.iloc[0]; filename = f"{minutes}m_{slug}.png"
            draw(raw, t, output / filename, title)
            examples.append({**t.to_dict(), "category": title, "image": filename, "selection": "validation realized-netR extremum in category; illustration, not representative sample"})
    (output / "examples.json").write_text(json.dumps(examples, indent=2, default=str))
    (output / "manifest.json").write_text(json.dumps({"builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "raw": str(raw.resolve()), "post": str(post.resolve()), "artifacts_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ("raw", "post", "output"):
        p.add_argument("--"+name, type=Path, required=True)
    a = p.parse_args(); build(a.raw, a.post, a.output)
