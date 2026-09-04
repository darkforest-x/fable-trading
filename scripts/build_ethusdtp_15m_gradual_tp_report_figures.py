"""Build the pre-holdout ETH 15m gradual take-profit diagnostic figure.

Sources are immutable audit summaries and paired exit deltas from the ETHUSDT.P
15-minute management experiments.  The script never reads market bars and has
no path to the repository holdout; it only visualizes already-frozen artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "experiments" / "active"
OUT = ROOT / "analysis" / "figures" / "ethusdtp_15m_gradual_tp_v16.png"

AUDITS = {
    "V1": "exp-ethusdtp-15m-progressive-scaleout-preholdout-20260904-v1",
    "V2": "exp-ethusdtp-15m-progressive-scaleout-pareto-preholdout-20260904-v2",
    "V6": "exp-ethusdtp-15m-wide-profit-ladder-preholdout-20260905-v6",
    "V11": "exp-ethusdtp-15m-streak-harvest-preholdout-20260905-v11",
    "V13": "exp-ethusdtp-15m-positive-strength-harvest-preholdout-20260905-v13",
    "V15": "exp-ethusdtp-15m-causal-path-harvest-preholdout-20260905-v15",
    "V16": "exp-ethusdtp-15m-micro-profit-ladder-preholdout-20260905-v16",
}


def load_json(path: Path) -> dict:
    """Return a JSON object from a frozen result artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def build_frontier() -> pd.DataFrame:
    """Collect audit mean return and right-tail retention by experiment."""
    rows: list[dict] = []
    for version, experiment_id in AUDITS.items():
        summary = load_json(ACTIVE / experiment_id / "results" / "audit_summary.json")
        baseline = summary["baseline"]
        candidate = summary["candidate"]
        rows.append(
            {
                "version": version,
                "mean_net_bp": candidate["mean_net_bp"],
                "p95_retention_pct": 100
                * candidate["p95_net_bp"]
                / baseline["p95_net_bp"],
            }
        )
    return pd.DataFrame(rows)


def build_figure() -> None:
    """Render the audit frontier and V16 paired-impact decomposition."""
    frontier = build_frontier()
    v16_dir = ACTIVE / AUDITS["V16"] / "results"
    summary = load_json(v16_dir / "audit_summary.json")
    pairs = pd.read_csv(v16_dir / "audit_paired_exit_deltas.csv")
    baseline = pairs["net_return_baseline"]
    delta_bp = pairs["delta"] * 10_000
    top_cut = baseline.quantile(0.90)

    components = pd.Series(
        {
            "Baseline <= 0": delta_bp[baseline <= 0].sum(),
            "Positive, not top 10%": delta_bp[(baseline > 0) & (baseline < top_cut)].sum(),
            "Top 10% winners": delta_bp[baseline >= top_cut].sum(),
            "Net impact": delta_bp.sum(),
        }
    )

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.4), constrained_layout=True)

    ax = axes[0]
    ax.axhline(0, color="#787B86", linewidth=1)
    ax.scatter(
        frontier["p95_retention_pct"],
        frontier["mean_net_bp"],
        s=70,
        color="#7D8CA3",
        zorder=3,
    )
    for row in frontier.itertuples(index=False):
        colour = "#17A297" if row.version == "V16" else "#4D5B73"
        weight = "bold" if row.version == "V16" else "normal"
        ax.annotate(
            row.version,
            (row.p95_retention_pct, row.mean_net_bp),
            xytext=(5, 5),
            textcoords="offset points",
            color=colour,
            fontweight=weight,
        )
    base = summary["baseline"]
    ax.scatter([100], [base["mean_net_bp"]], s=105, marker="*", color="#F2A900", zorder=4)
    ax.annotate("Pure runner", (100, base["mean_net_bp"]), xytext=(-74, 7), textcoords="offset points")
    ax.set_title("Audit frontier: expectancy vs. right-tail retention")
    ax.set_xlabel("P95 return retained vs. pure runner (%)")
    ax.set_ylabel("Mean net return after 20 bp cost (bp/trade)")
    ax.set_xlim(35, 103)
    ax.text(
        0.02,
        0.95,
        "V16 = 2.5% at +2/+4/+8/+12 ATR; 90% remains on SMA60 runner",
        transform=ax.transAxes,
        fontsize=9,
        color="#17A297",
        va="top",
        bbox={"facecolor": "white", "alpha": 0.82, "edgecolor": "none"},
    )

    ax = axes[1]
    colours = ["#17A297", "#17A297", "#E05260", "#F2A900"]
    bars = ax.bar(components.index, components.values, color=colours, width=0.66)
    ax.axhline(0, color="#787B86", linewidth=1)
    for bar, value in zip(bars, components.values):
        offset = 8 if value >= 0 else -20
        va = "bottom" if value >= 0 else "top"
        ax.annotate(
            f"{value:+.0f} bp",
            (bar.get_x() + bar.get_width() / 2, value),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va=va,
            fontweight="bold",
        )
    ax.set_title("V16 paired impact across 114 audit trades")
    ax.set_ylabel("Sum of candidate-minus-runner return (bp)")
    ax.tick_params(axis="x", rotation=14)
    ax.set_ylim(-515, 175)
    ax.text(
        0.02,
        0.95,
        "Small profit banks rescue giveback cases, but trim rare outsized winners.",
        transform=ax.transAxes,
        fontsize=9,
        color="#4D5B73",
        va="top",
        bbox={"facecolor": "white", "alpha": 0.82, "edgecolor": "none"},
    )

    fig.suptitle("ETHUSDT.P 15m gradual take-profit diagnostic — pre-holdout only", fontsize=15)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=180, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    build_figure()
