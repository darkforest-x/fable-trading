#!/usr/bin/env python3
"""Build pre-holdout visual evidence for the BTCUSDT.P 15m regime refactor.

All plotted price bars and signal rows end before 2026-03-01.  The script reads
only already-produced deterministic experiment artifacts; it does not select
parameters or inspect repository holdout rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_trend_regime_episode import (
    load_config,
    load_frame,
)

ROOT = Path(__file__).resolve().parents[1]
PARENT_EXPERIMENT = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-trend-regime-episode-preholdout-20260904-v1"
)
EXPERIMENT = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-trend-regime-live-entry-preholdout-20260904-v1"
)
RESULTS = EXPERIMENT / "results"
OUT = RESULTS / "trend_regime_episode_summary.png"


def _auc(binary: pd.Series, score: pd.Series) -> float:
    positives = int(binary.sum())
    negatives = int(len(binary) - positives)
    if positives == 0 or negatives == 0:
        return float("nan")
    ranks = score.rank(method="average")
    rank_sum = float(ranks[binary.astype(bool)].sum())
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _ranking_diagnostics() -> dict[str, object]:
    """Describe the existing morphology score; never use it to tune V5."""

    output: dict[str, object] = {
        "definition": "top ceil(10%) by unchanged K1/K2 morphology score",
        "positive_label_for_auc": "net_return > 0 after 0.2% round-trip cost",
        "permutation_null": "100,000 random subsets of the same size; one-sided top-minus-pool mean net return",
        "seed": 90417,
    }
    rng = np.random.default_rng(90417)
    for window in ("development", "audit"):
        trades = _read_trades(f"{window}_v5_trades.csv.gz")
        top_n = max(1, int(np.ceil(len(trades) * 0.10)))
        top = trades.nlargest(top_n, "signal_score")
        pool = trades["net_return"].to_numpy(dtype=float)
        observed = float(top["net_return"].mean() - trades["net_return"].mean())
        exceed = 0
        for _ in range(100_000):
            sampled = rng.choice(pool, size=top_n, replace=False)
            exceed += int(float(sampled.mean() - pool.mean()) >= observed)
        output[window] = {
            "events": len(trades),
            "top_decile_events": top_n,
            "auc": _auc(trades["net_return"].gt(0), trades["signal_score"]),
            "pool_mean_net_bp": float(trades["net_return"].mean() * 1e4),
            "top_decile_mean_gross_bp": float(top["gross_return"].mean() * 1e4),
            "top_decile_mean_net_bp": float(top["net_return"].mean() * 1e4),
            "top_decile_win_rate": float(top["net_return"].gt(0).mean()),
            "top_minus_pool_mean_net_bp": float(observed * 1e4),
            "permutation_p": float((exceed + 1) / 100_001),
        }
    (RESULTS / "ranking_diagnostics.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def _read_trades(name: str) -> pd.DataFrame:
    frame = pd.read_csv(RESULTS / name)
    frame["signal_time"] = pd.to_datetime(frame["signal_time"], utc=True)
    return frame


def _densest_window(baseline: pd.DataFrame, selected: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Choose a fixed 48h pre-holdout example without outcome-based ranking."""

    counts = (
        baseline.set_index("signal_time")
        .assign(event=1)["event"]
        .resample("48h")
        .sum()
        .sort_values(ascending=False)
    )
    for start in counts.index:
        end = start + pd.Timedelta(hours=48)
        old_n = baseline["signal_time"].between(start, end, inclusive="left").sum()
        new_n = selected["signal_time"].between(start, end, inclusive="left").sum()
        if old_n >= 4 and new_n <= 1:
            return start, end
    raise RuntimeError("no deterministic crowded pre-holdout example found")


def build() -> Path:
    config = load_config()
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    _ranking_diagnostics()
    base = _read_trades("audit_v3_trades.csv.gz")
    v4 = _read_trades("audit_v4_trades.csv.gz")
    chosen = _read_trades("audit_v5_trades.csv.gz")
    price, quality = load_frame(config)
    if int(quality["holdout_rows_read"]) != 0:
        raise RuntimeError("report builder materialized repository holdout")

    start, end = _densest_window(base, chosen)
    view = price[(price["open_time"] >= start) & (price["open_time"] < end)].copy()
    old_view = base[base["signal_time"].between(start, end, inclusive="left")]
    v4_view = v4[v4["signal_time"].between(start, end, inclusive="left")]
    new_view = chosen[chosen["signal_time"].between(start, end, inclusive="left")]

    plt.rcParams.update({"font.size": 10, "axes.titleweight": "bold"})
    fig = plt.figure(figsize=(13.5, 8.2), constrained_layout=True, facecolor="#f8fafc")
    grid = fig.add_gridspec(2, 3, height_ratios=[1.25, 1.0])
    ax_price = fig.add_subplot(grid[0, :])
    ax_density = fig.add_subplot(grid[1, 0])
    ax_burst = fig.add_subplot(grid[1, 1])
    ax_econ = fig.add_subplot(grid[1, 2])

    ax_price.set_facecolor("white")
    ax_price.plot(view["open_time"], view["close"], color="#334155", lw=1.2, label="BTC close")
    ax_price.plot(view["open_time"], view["reference_ma"], color="#0f9f95", lw=1.4, label="EMA30")
    for _, row in old_view.iterrows():
        color = "#14b8a6" if int(row["direction"]) > 0 else "#f59e0b"
        ax_price.axvline(row["signal_time"], color=color, alpha=0.28, lw=1.0)
        y = view.iloc[(view["open_time"] - row["signal_time"]).abs().argsort()[:1]]["close"].iloc[0]
        ax_price.scatter(row["signal_time"], y, s=42, facecolors="none", edgecolors=color, lw=1.4)
    for _, row in v4_view.iterrows():
        color = "#0f766e" if int(row["direction"]) > 0 else "#c2410c"
        y = view.iloc[(view["open_time"] - row["signal_time"]).abs().argsort()[:1]]["close"].iloc[0]
        ax_price.scatter(row["signal_time"], y, marker="D", s=46, facecolors="white", edgecolors=color, lw=1.2, zorder=4)
    for _, row in new_view.iterrows():
        color = "#047857" if int(row["direction"]) > 0 else "#b45309"
        y = view.iloc[(view["open_time"] - row["signal_time"]).abs().argsort()[:1]]["close"].iloc[0]
        ax_price.scatter(row["signal_time"], y, marker="*", s=150, color=color, zorder=5)
    ax_price.set_title(
        f"Pre-holdout crowded 48h example: V3={len(old_view)}, V4={len(v4_view)}, V5={len(new_view)} signal(s)"
    )
    ax_price.text(
        0.01,
        0.03,
        "○ V3 position reset   ◇ V4 one per regime   ★ V5 regime + current trend alive",
        transform=ax_price.transAxes,
        color="#475569",
    )
    ax_price.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=start.tz))
    ax_price.grid(alpha=0.18)
    ax_price.legend(loc="upper left", frameon=False, ncol=2)

    labels = ["Development\n2023–2024", "Audit\n2025–2026P1"]
    b = [summary["development"]["v3"], summary["audit"]["v3"]]
    v = [summary["development"]["v4"], summary["audit"]["v4"]]
    live = [summary["development"]["v5"], summary["audit"]["v5"]]
    x = np.arange(2)
    width = 0.25
    ax_density.bar(x - width, [z["signals_per_30d"] for z in b], width, color="#cbd5e1", label="V3")
    ax_density.bar(x, [z["signals_per_30d"] for z in v], width, color="#67e8f9", label="V4")
    ax_density.bar(x + width, [z["signals_per_30d"] for z in live], width, color="#0f9f95", label="V5")
    ax_density.set_xticks(x, labels)
    ax_density.set_ylabel("Signals / 30d")
    ax_density.set_title("Entry density")
    ax_density.grid(axis="y", alpha=0.18)
    ax_density.legend(frameon=False)

    ax_burst.bar(x - width, [100 * z["within_24h_previous_share"] for z in b], width, color="#cbd5e1")
    ax_burst.bar(x, [100 * z["within_24h_previous_share"] for z in v], width, color="#67e8f9")
    ax_burst.bar(x + width, [100 * z["within_24h_previous_share"] for z in live], width, color="#0f9f95")
    ax_burst.set_xticks(x, labels)
    ax_burst.set_ylabel("Previous signal within 24h (%)")
    ax_burst.set_title("Same-range burst rate")
    ax_burst.grid(axis="y", alpha=0.18)

    econ_labels = ["2023H1", "2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026P1"]
    dev_fold = pd.read_csv(RESULTS / "development_v5_fold_metrics.csv")
    audit_fold = pd.read_csv(RESULTS / "audit_v5_fold_metrics.csv")
    fold = pd.concat([dev_fold, audit_fold], ignore_index=True).set_index("fold").loc[econ_labels]
    colors = ["#0f9f95" if z >= 0 else "#f97316" for z in fold["mean_net_bp"]]
    ax_econ.bar(np.arange(len(fold)), fold["mean_net_bp"], color=colors)
    ax_econ.axhline(0, color="#334155", lw=0.8)
    ax_econ.set_xticks(np.arange(len(fold)), econ_labels, rotation=45, ha="right")
    ax_econ.set_ylabel("Mean net return (bp)")
    ax_econ.set_title("Economic stability remains unproven")
    ax_econ.grid(axis="y", alpha=0.18)

    fig.suptitle(
        "BTCUSDT.P 15m · A remembered regime is not a live trend",
        fontsize=17,
        fontweight="bold",
        color="#0f172a",
    )
    fig.savefig(OUT, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return OUT


if __name__ == "__main__":
    print(build())
