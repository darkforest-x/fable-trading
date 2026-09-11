"""Build WVF comparison artifacts from the frozen, already replayed ledgers.

No signal oracle is run here. Selection of illustrations is explicitly ex post:
max/min realized net R within the four documented categories. Plotting future
bars serves outcome inspection only and cannot change a stored entry decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study import wvf_features
from yoyo.evaluation.spike_v6_wvf_study_post import canonicalize, matched_random_controls


FOLDS = {
    "development": ("2024-09-10T00:00:00Z", "2025-09-10T00:00:00Z"),
    "validation": ("2025-09-10T00:00:00Z", "2026-09-10T00:00:00Z"),
}
KEY = ["symbol", "timeframe_min", "fold", "side", "signal_bar_open"]


def metrics(group):
    gains = group.net_return.clip(lower=0).sum()
    losses = -group.net_return.clip(upper=0).sum()
    return pd.Series({
        "trades": len(group), "win_rate": group.net_return.gt(0).mean(),
        "profit_factor": gains / losses if losses else (np.inf if gains else np.nan),
        "sum_net_r": group.net_r.sum(), "mean_net_r": group.net_r.mean(),
        "mean_net_return": group.net_return.mean(),
        "realized_net_r_ge_10": group.net_r.ge(10).sum(),
        "mfe_r_ge_10": group.mfe_r.ge(10).sum(),
    })


def draw_case(raw, trade, path, title):
    """Render full trade context with fixed pre-entry and post-exit margins."""
    cache = pd.read_pickle(raw / f"{trade.symbol}_{int(trade.timeframe_min)}m_features.pkl.gz")
    bars = cache["bars"]
    signal_time, entry_time, exit_time = [pd.Timestamp(trade[k]) for k in ("signal_bar_open", "entry_time", "exit_time")]
    i, j = int(bars.index.get_loc(signal_time)), int(bars.index.get_loc(exit_time))
    start, end = max(0, i - 100), min(len(bars), j + 41)
    view = bars.iloc[start:end]
    x = np.arange(len(view))
    wvf = wvf_features(bars, data_gap=cache["data_gap"]).iloc[start:end]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(3, 1, figsize=(17, 10), sharex=True,
                             gridspec_kw={"height_ratios": [3.8, 1.2, 1.3]})
    fig.set_facecolor("#f4f7fa")
    up = view.close.to_numpy() >= view.open.to_numpy()
    colors = np.where(up, "#079b88", "#de5b72")
    axes[0].add_collection(LineCollection([[(k, lo), (k, hi)] for k, lo, hi in zip(x, view.low, view.high)], colors=colors, linewidths=.65))
    polygons = [[(k-.32, o), (k+.32, o), (k+.32, c), (k-.32, c)] for k, o, c in zip(x, view.open, view.close)]
    axes[0].add_collection(PolyCollection(polygons, facecolors=colors, edgecolors=colors, linewidths=.4))
    for name, color, alpha in (("s20", "#28a399", .8), ("e20", "#28a399", .45), ("s60", "#638dcc", .8), ("e60", "#638dcc", .45), ("s120", "#646e7a", .8), ("e120", "#646e7a", .45)):
        axes[0].plot(x, view[name], color=color, alpha=alpha, lw=1, label=name.upper())
    entry_x = int(bars.index.get_loc(entry_time)) - start
    exit_x = j - start
    axes[0].hlines(trade.initial_stop, entry_x, exit_x, color="#d15264", linestyle="--", lw=1.1, label="Initial SL")
    axes[0].scatter([entry_x], [trade.entry_price], marker="^", s=90, color="#087565", zorder=5, label="Actual entry")
    axes[0].scatter([exit_x], [trade.exit_price], marker="X", s=75, color="#793fc5", zorder=5, label="Actual exit")
    axes[0].annotate(f"ENTRY {trade.entry_price:.7g}", (entry_x, trade.entry_price), xytext=(8, 18), textcoords="offset points", color="#086757")
    close_labels = abs(exit_x-entry_x) < 20
    axes[0].annotate(f"EXIT {trade.exit_price:.7g}\n{trade.exit_reason}", (exit_x, trade.exit_price),
                     xytext=(16, -52) if close_labels else (-8, 23),
                     ha="left" if close_labels else "right", textcoords="offset points", color="#62339b",
                     arrowprops={"arrowstyle": "-", "color": "#9561ba", "lw": .7} if close_labels else None)
    axes[0].set_ylim(view.low.min() * .99, view.high.max() * 1.01)
    axes[0].legend(loc="upper left", ncol=5, fontsize=8, frameon=False)
    axes[1].plot(x, view.md, color="#487dcb", lw=1.4, label="IMACD")
    axes[1].plot(x, view.sb, color="#c99239", lw=1.3, label="Signal")
    axes[1].axhline(0, color="#708090", lw=.7)
    axes[1].legend(loc="upper left", frameon=False, fontsize=8)
    axes[2].bar(x, wvf.wvf, color=np.where(wvf.wvf_extreme, "#db9962", "#a7b6c8"), width=.75, label="WVF (orange = extreme)")
    axes[2].plot(x, wvf.wvf_upper_band, color="#9561ba", lw=.9, label="SMA20 + 2 SD")
    axes[2].plot(x, wvf.wvf_range_high, color="#777c86", lw=.8, linestyle="--", label="50-bar max x .85")
    axes[2].legend(loc="upper left", frameon=False, fontsize=8, ncol=3)
    for ax in axes:
        ax.set_facecolor("white")
        ax.axvspan(entry_x, len(view)-1, color="#76b8e8", alpha=.055)
        ax.axvline(i-start, color="#685dba", linestyle=":", lw=1)
        ax.grid(alpha=.18)
        ax.spines[["top", "right"]].set_visible(False)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        ax.set_xlim(-1, len(view))
    ticks = np.linspace(0, len(view)-1, 9, dtype=int)
    axes[-1].set_xticks(ticks, [view.index[k].tz_convert("Asia/Shanghai").strftime("%m-%d\n%H:%M") for k in ticks])
    axes[-1].set_xlabel("Beijing time | Violet line: closed-bar V6 signal | Blue shade: outcome-only future")
    axes[0].set_title(f"{title}\n{trade.symbol} / {int(trade.timeframe_min)}m | realized {trade.net_r:+.2f}R | MFE {trade.mfe_r:.2f}R (not realized)\nSignal {signal_time.tz_convert('Asia/Shanghai'):%Y-%m-%d %H:%M} open; entry {entry_time.tz_convert('Asia/Shanghai'):%m-%d %H:%M}", loc="left", fontsize=14, pad=18)
    fig.tight_layout()
    fig.savefig(path, dpi=145)
    plt.close(fig)


def build(raw: Path, output: Path):
    canonicalize(raw, output)
    trade = pd.read_csv(output / "canonical_closed_trades.csv.gz")
    trade.signal_bar_open = pd.to_datetime(trade.signal_bar_open, utc=True)
    trade.entry_time = pd.to_datetime(trade.entry_time, utc=True)
    trade.exit_time = pd.to_datetime(trade.exit_time, utc=True)
    identity = trade.groupby(KEY, dropna=False)[["entry_time", "entry_price", "net_r", "net_return"]].nunique()
    if identity.gt(1).any().any():
        raise ValueError("Shared signal identity no longer implies identical actual entry/outcome")
    by_side = trade.groupby(["fold", "variant", "side"]).apply(metrics).reset_index()
    by_side.to_csv(output / "metrics_by_side.csv", index=False)
    trade.groupby(["fold", "variant"]).apply(metrics).reset_index().to_csv(output / "metrics_all_sides.csv", index=False)
    trade.groupby(["fold", "variant", "symbol", "timeframe_min", "side"]).apply(metrics).reset_index().to_csv(output / "metrics_by_stream_side.csv", index=False)
    a = trade[trade.variant.eq("A")].copy()
    tails = []
    for variant in ("B12", "C24"):
        other = trade.loc[trade.variant.eq(variant), KEY]
        joined = a.merge(other.assign(retained=True), how="left", on=KEY)
        joined["retained"] = joined.retained.fillna(False).astype(bool)
        for (fold, side), g in joined.groupby(["fold", "side"]):
            tails.append({"variant": variant, "fold": fold, "side": side,
                          "baseline_trades": len(g), "retained_trades": int(g.retained.sum()),
                          "baseline_net10": int(g.net_r.ge(10).sum()),
                          "retained_net10": int((g.net_r.ge(10) & g.retained).sum()),
                          "rejected_winners": int((g.net_r.gt(0) & ~g.retained).sum()),
                          "rejected_losers": int((g.net_r.le(0) & ~g.retained).sum())})
    pd.DataFrame(tails).to_csv(output / "retention_by_fold_side.csv", index=False)
    original_manifest = json.loads((raw / "input_manifest.json").read_text())
    (output / "source_manifest.json").write_text(json.dumps(original_manifest, indent=2))
    controls = []
    for (symbol, minutes, fold), g in trade.groupby(["symbol", "timeframe_min", "fold"]):
        cache = pd.read_pickle(raw / f"{symbol}_{minutes}m_features.pkl.gz")
        # Actual shared entries have identical outcomes; never replay per variant.
        targets = g.drop_duplicates(["signal_bar_open", "side"])
        tick = float(next(item["tick"] for item in original_manifest["inputs"] if item["symbol"] == symbol))
        pairs, _ = matched_random_controls(cache, targets, tick=tick,
                    fold_start=pd.Timestamp(FOLDS[fold][0]), fold_end=pd.Timestamp(FOLDS[fold][1]))
        pairs["symbol"], pairs["timeframe_min"], pairs["fold"] = symbol, minutes, fold
        pairs.to_csv(output / f"controls_{symbol}_{minutes}m_{fold}.csv.gz", index=False, compression="gzip")
        links = g[["signal_bar_open", "side", "variant"]]
        pairs["target_time"] = pd.to_datetime(pairs.target_time, utc=True)
        linked = pairs.merge(links, left_on=["target_time", "side"], right_on=["signal_bar_open", "side"])
        controls.append(linked.groupby(["fold", "variant", "side", "seed"]).agg(
            total=("matched", "size"), matched=("matched", "sum"),
            sum_net_r_difference=("net_r_difference", "sum"),
            sum_net_return_difference=("net_return_difference", "sum")).reset_index())
        print(f"controls {symbol} {minutes}m {fold}: {len(targets)} unique trades", flush=True)
    c = pd.concat(controls).groupby(["fold", "variant", "side", "seed"])[["total", "matched", "sum_net_r_difference", "sum_net_return_difference"]].sum().reset_index()
    c["mean_excess_net_r"] = c.sum_net_r_difference / c.matched
    c["mean_excess_net_return"] = c.sum_net_return_difference / c.matched
    c["match_rate"] = c.matched / c.total
    c.to_csv(output / "matched_control_seeds.csv", index=False)
    c.groupby(["fold", "variant", "side"]).agg(
        mean_excess_net_r=("mean_excess_net_r", "mean"),
        excess_net_r_p05=("mean_excess_net_r", lambda s: s.quantile(.05)),
        excess_net_r_p95=("mean_excess_net_r", lambda s: s.quantile(.95)),
        mean_excess_return=("mean_excess_net_return", "mean"),
        seed_nonpositive_rate=("mean_excess_net_return", lambda s: s.le(0).mean()),
        mean_match_rate=("match_rate", "mean")).reset_index().to_csv(output / "matched_control_summary.csv", index=False)
    candidates = a[a.fold.eq("validation") & a.side.eq(1)].copy()
    bkeys = trade.loc[trade.variant.eq("B12"), KEY]
    candidates = candidates.merge(bkeys.assign(retained=True), how="left", on=KEY)
    candidates["retained"] = candidates.retained.fillna(False).astype(bool)
    reasons = []
    for row in candidates.itertuples():
        # Cache decisions, not future price, determine the category.
        name = f"{row.symbol}_{int(row.timeframe_min)}m"
        if name not in reasons_cache:
            reasons_cache[name] = pd.read_pickle(raw / f"{name}_features.pkl.gz")["reclaim12"]
        reasons.append(reasons_cache[name].loc[row.signal_bar_open, "wvf_filter_reason"])
    candidates["wvf_reason"] = reasons
    categories = [
        ("01_retained_winner", "A winner retained by B12", candidates.retained & candidates.net_r.gt(0), False),
        ("02_rejected_loss", "A loss rejected by B12", ~candidates.retained & candidates.net_r.lt(0), True),
        ("03_missed_big_trend", "A >10R winner missed by B12", ~candidates.retained & candidates.net_r.ge(10), False),
        ("04_no_panic_launch", "Profitable launch without recent WVF shock", candidates.wvf_reason.eq("no_prior_extreme") & candidates.net_r.gt(0), False),
    ]
    examples, used = [], set()
    for filename, title, mask, ascending in categories:
        pool = candidates[mask].sort_values(["net_r", "symbol", "timeframe_min", "signal_bar_open"], ascending=[ascending, True, True, True])
        row = next(r for _, r in pool.iterrows() if tuple(r[k] for k in KEY) not in used)
        used.add(tuple(row[k] for k in KEY))
        draw_case(raw, row, output / f"{filename}.png", title)
        examples.append({**row.to_dict(), "image": f"{filename}.png", "category": title,
                         "selection": "realized net R extremum within category; exclude earlier selected examples"})
    (output / "examples.json").write_text(json.dumps(examples, indent=2, default=str))
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    source = [Path(__file__), Path("yoyo/evaluation/spike_v6_wvf_study_post.py")]
    manifest = {"builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "source_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source},
                "source_raw": str(raw.resolve()), "artifacts_sha256": files,
                "no_new_oracle_run": True, "control_seeds": 99,
                "prototype_not_canonical": "results/canonical_03816d8 was built before its source commit and is superseded"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))


reasons_cache = {}
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.raw, args.output)
