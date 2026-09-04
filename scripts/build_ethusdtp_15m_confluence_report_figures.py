"""Build diagnostics and a report figure for ETHUSDT.P 15m confluence V17/V18.

Sources are the frozen V17/V18 configs and result receipts plus the bounded
ETH/BTC 15-minute prefixes returned by ``build_feature_ledger``.  Entry-time
features use the completed K2 bar and earlier; the reconstruction ends before
2026-03-01 and asserts that zero repository-holdout rows (>= 2026-05-04) were
returned.  Outcome columns are used only for post-audit diagnosis and plotting.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_ma_state_trend import json_value, utc
from scripts.research_ethusdtp_15m_bank_only_runner_v4 import _matched_controls
from scripts.research_ethusdtp_15m_causal_confluence_v17 import (
    build_feature_ledger,
)
from scripts.research_ethusdtp_15m_expansion_confluence_v18 import (
    _add_expansion_scores,
)

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "experiments" / "active"
V17_ID = "exp-ethusdtp-15m-causal-confluence-preholdout-20260905-v17"
V18_ID = "exp-ethusdtp-15m-expansion-confluence-preholdout-20260905-v18"
V17 = ACTIVE / V17_ID
V18 = ACTIVE / V18_ID
V16_CONFIG = (
    ACTIVE
    / "exp-ethusdtp-15m-micro-profit-ladder-preholdout-20260905-v16"
    / "config.json"
)
OUTPUT = ROOT / "analysis" / "output" / "ethusdtp_15m_confluence_v17_v18"
FIGURE = ROOT / "analysis" / "figures" / "ethusdtp_15m_confluence_v17_v18.png"

INK = "#2E3540"
GRID = "#D8DDE6"
BASE = "#577590"
CANDIDATE = "#E09F3E"
NEUTRAL = "#7A8493"


def load_json(path: Path) -> dict[str, Any]:
    """Load one frozen JSON receipt."""

    return json.loads(path.read_text(encoding="utf-8"))


def reconstruct() -> tuple[
    dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, dict[str, Any]]
]:
    """Reconstruct bounded development/audit ledgers for diagnostic grouping."""

    config = load_json(V17 / "config.json")
    ledgers: dict[str, pd.DataFrame] = {}
    markets: dict[str, pd.DataFrame] = {}
    sources: dict[str, dict[str, Any]] = {}
    for phase in ("selection", "audit"):
        eth, events, source = build_feature_ledger(config, phase)
        if int(source["repository_holdout_rows_read"]) != 0:
            raise RuntimeError(f"{phase} unexpectedly returned repository holdout rows")
        events = _add_expansion_scores(events)
        events["selected"] = events["expansion_floor"].ge(0.85)
        events["winner"] = events["net_return"].gt(0.0)
        events["net_bp"] = events["net_return"] * 10_000.0
        ledgers[phase] = events
        markets[phase] = eth
        sources[phase] = source
    return ledgers, markets, sources


def build_diagnostics(
    ledgers: dict[str, pd.DataFrame],
    markets: dict[str, pd.DataFrame],
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Write exact diagnostic tables without selecting another threshold."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    direction_rows: list[dict[str, Any]] = []
    runner_rows: list[dict[str, Any]] = []
    for phase, events in ledgers.items():
        for (selected, direction), group in events.groupby(
            ["selected", "direction"], sort=True
        ):
            direction_rows.append(
                {
                    "phase": phase,
                    "selected": bool(selected),
                    "direction": "LONG" if int(direction) == 1 else "SHORT",
                    "events": len(group),
                    "mean_net_bp": float(group["net_bp"].mean()),
                    "win_rate": float(group["winner"].mean()),
                    "runner_arm_rate": float(group["runner_armed"].mean()),
                    "mean_horizon_mfe_atr": float(group["horizon_mfe_atr"].mean()),
                }
            )
        for (selected, runner_armed), group in events.groupby(
            ["selected", "runner_armed"], sort=True
        ):
            runner_rows.append(
                {
                    "phase": phase,
                    "selected": bool(selected),
                    "runner_armed": bool(runner_armed),
                    "events": len(group),
                    "mean_net_bp": float(group["net_bp"].mean()),
                    "mean_horizon_mfe_atr": float(group["horizon_mfe_atr"].mean()),
                    "mean_giveback_atr": float(group["gave_back_atr"].mean()),
                }
            )

    direction = pd.DataFrame(direction_rows)
    runner = pd.DataFrame(runner_rows)
    direction.to_csv(OUTPUT / "phase_direction_breakdown.csv", index=False)
    runner.to_csv(OUTPUT / "phase_runner_breakdown.csv", index=False)

    audit = ledgers["audit"]
    top_count = max(1, math.ceil(0.10 * len(audit)))
    top = audit.sort_values(["net_return", "setup_id"], ascending=[False, True]).head(
        top_count
    )
    top_columns = [
        "setup_id",
        "signal_time",
        "direction",
        "net_bp",
        "expansion_floor",
        "eth_atr_ratio96",
        "eth_bb_width_ratio96",
        "selected",
        "runner_armed",
        "outcome",
    ]
    top[top_columns].to_csv(OUTPUT / "audit_top_decile_trades.csv", index=False)

    quantile_rows: list[dict[str, Any]] = []
    for phase, events in ledgers.items():
        for selected, group in events.groupby("selected", sort=True):
            quantile_rows.append(
                {
                    "phase": phase,
                    "selected": bool(selected),
                    "events": len(group),
                    "expansion_floor_median": float(group["expansion_floor"].median()),
                    "atr_ratio96_median": float(group["eth_atr_ratio96"].median()),
                    "bb_width_ratio96_median": float(
                        group["eth_bb_width_ratio96"].median()
                    ),
                }
            )
    pd.DataFrame(quantile_rows).to_csv(
        OUTPUT / "phase_expansion_distribution.csv", index=False
    )

    v17_config = load_json(V17 / "config.json")
    v18_config = load_json(V18 / "config.json")
    parent = load_json(V16_CONFIG)
    parent["matched_control"] = dict(v18_config["matched_control"])
    split_bounds = {
        "selection": (
            v17_config["splits"]["development_start_inclusive"],
            v17_config["splits"]["development_end_exclusive"],
        ),
        "audit": (
            v17_config["splits"]["audit_start_inclusive"],
            v17_config["splits"]["audit_end_exclusive"],
        ),
    }
    matched_rows: list[dict[str, Any]] = []
    for phase, events in ledgers.items():
        candidate = events.loc[events["selected"]].copy()
        start, end = split_bounds[phase]
        _, pairs = _matched_controls(
            candidate,
            markets[phase],
            parent,
            bank=0.10,
            start=utc(start),
            end=utc(end),
        )
        matched = pairs.loc[pairs["match_status"].eq("matched_exact")].copy()
        matched.to_csv(OUTPUT / f"{phase}_matched_pairs.csv", index=False)
        excess = matched["paired_excess_return"].astype(float)
        matched_rows.append(
            {
                "phase": phase,
                "matched_events": len(matched),
                "candidate_mean_net_bp": float(
                    matched["candidate_net_return"].mean() * 10_000.0
                ),
                "control_mean_net_bp": float(
                    matched["control_mean_net_return"].mean() * 10_000.0
                ),
                "excess_bp": float(excess.mean() * 10_000.0),
                "signflip_p_one_sided": float(
                    signflip_p(
                        excess,
                        resamples=100_000,
                        seed=2026090520 if phase == "selection" else 2026090519,
                    )
                ),
            }
        )
    pd.DataFrame(matched_rows).to_csv(
        OUTPUT / "phase_matched_random_summary.csv", index=False
    )

    selected_top = top.loc[top["selected"]]
    summary = {
        "threshold_reoptimized_on_audit": False,
        "selection_rule": "expansion_floor >= 0.85",
        "audit_events": len(audit),
        "audit_top_decile_events": top_count,
        "audit_top_decile_selected_events": len(selected_top),
        "audit_top_decile_selected_rate": len(selected_top) / top_count,
        "audit_top_decile_selected_net_bp": float(selected_top["net_bp"].sum()),
        "audit_top_decile_total_net_bp": float(top["net_bp"].sum()),
        "audit_top_decile_selected_net_share": float(
            selected_top["net_bp"].sum() / top["net_bp"].sum()
        ),
        "source": sources,
        "repository_holdout_rows_read": int(
            sum(
                int(source["repository_holdout_rows_read"])
                for source in sources.values()
            )
        ),
    }
    (OUTPUT / "diagnostic_summary.json").write_text(
        json.dumps(json_value(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _label_bars(ax: plt.Axes, bars: Any, suffix: str = "") -> None:
    """Direct-label signed bars with consistent spacing."""

    for bar in bars:
        value = float(bar.get_height())
        offset = 4 if value >= 0 else -5
        va = "bottom" if value >= 0 else "top"
        ax.annotate(
            f"{value:+.1f}{suffix}",
            (bar.get_x() + bar.get_width() / 2.0, value),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va=va,
            color=INK,
            fontsize=8.5,
            fontweight="bold",
        )


def build_figure() -> None:
    """Render transport, fold-stability, and right-tail comparisons."""

    selection = load_json(V18 / "results" / "selection_receipt.json")
    audit = load_json(V18 / "results" / "audit_receipt.json")
    development_folds = pd.read_csv(V18 / "results" / "development_fold_metrics.csv")
    audit_folds = pd.read_csv(V18 / "results" / "audit_fold_metrics.csv")
    folds = pd.concat(
        [
            development_folds.assign(phase="Development"),
            audit_folds.assign(phase="Transport audit"),
        ],
        ignore_index=True,
    )

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(1, 3, figsize=(16.2, 5.8))

    periods = ["Development\n2023–2024", "Transport audit\n2025–Feb 2026"]
    baseline_values = [
        selection["baseline"]["mean_net_bp"],
        audit["baseline"]["mean_net_bp"],
    ]
    candidate_values = [
        selection["candidate"]["mean_net_bp"],
        audit["candidate"]["mean_net_bp"],
    ]
    x = np.arange(len(periods))
    width = 0.34
    ax = axes[0]
    base_bars = ax.bar(
        x - width / 2,
        baseline_values,
        width,
        label="V16 all signals",
        color="white",
        edgecolor=BASE,
        linewidth=1.5,
        hatch="//",
    )
    candidate_bars = ax.bar(
        x + width / 2,
        candidate_values,
        width,
        label="Expansion gate",
        color=CANDIDATE,
        edgecolor="#9A6417",
        linewidth=1.0,
    )
    ax.axhline(0, color=INK, linewidth=1.0)
    _label_bars(ax, base_bars, " bp")
    _label_bars(ax, candidate_bars, " bp")
    ax.set_xticks(x, periods)
    ax.set_ylim(-55, 16)
    ax.set_ylabel("Mean net return (bp/trade)")
    ax.set_title("Mean return transport")
    ax.legend(frameon=False, fontsize=8.5, loc="lower left")

    fold_order = ["2023H1", "2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026P1"]
    baseline_fold = folds.loc[folds["gate_id"].eq("v16_all")].set_index("fold")
    candidate_fold = folds.loc[folds["gate_id"].eq("expansion_floor_0p85")].set_index(
        "fold"
    )
    ax = axes[1]
    xx = np.arange(len(fold_order))
    fold_base = [float(baseline_fold.loc[name, "mean_net_bp"]) for name in fold_order]
    fold_candidate = [
        float(candidate_fold.loc[name, "mean_net_bp"]) for name in fold_order
    ]
    ax.bar(
        xx - 0.18,
        fold_base,
        0.36,
        color="white",
        edgecolor=BASE,
        linewidth=1.2,
        hatch="//",
    )
    ax.bar(
        xx + 0.18,
        fold_candidate,
        0.36,
        color=CANDIDATE,
        edgecolor="#9A6417",
        linewidth=0.8,
    )
    ax.axhline(0, color=INK, linewidth=1.0)
    ax.axvline(3.5, color=NEUTRAL, linewidth=1.0, linestyle="--")
    ax.set_xticks(xx, fold_order, rotation=35, ha="right")
    ax.set_ylabel("Mean net return (bp/trade)")
    ax.set_title("Half-year stability")
    ax.text(
        0.02,
        0.97,
        "Dashed line = start of transport audit",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        color=NEUTRAL,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85},
    )

    tail_labels = ["Selection\nrate", "Top-decile\nPnL capture", "P95\nretention"]
    development_tail = [
        selection["candidate"]["selection_rate"] * 100.0,
        selection["candidate"]["baseline_top_decile_positive_pnl_capture"] * 100.0,
        selection["candidate"]["candidate_p95_net_retention"] * 100.0,
    ]
    audit_tail = [
        audit["candidate"]["selection_rate"] * 100.0,
        audit["candidate"]["baseline_top_decile_positive_pnl_capture"] * 100.0,
        audit["candidate"]["candidate_p95_net_retention"] * 100.0,
    ]
    ax = axes[2]
    xxx = np.arange(len(tail_labels))
    dev_bars = ax.bar(
        xxx - width / 2,
        development_tail,
        width,
        label="Development",
        color="white",
        edgecolor=BASE,
        linewidth=1.5,
        hatch="//",
    )
    audit_bars = ax.bar(
        xxx + width / 2,
        audit_tail,
        width,
        label="Transport audit",
        color=CANDIDATE,
        edgecolor="#9A6417",
        linewidth=1.0,
    )
    _label_bars(ax, dev_bars, "%")
    _label_bars(ax, audit_bars, "%")
    ax.set_xticks(xxx, tail_labels)
    ax.set_ylim(0, 150)
    ax.set_ylabel("Percent")
    ax.set_title("Signal density and right-tail retention")
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")

    for ax in axes:
        ax.grid(axis="x", visible=False)
        ax.grid(axis="y", color=GRID, linewidth=0.7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(colors=INK)
        ax.title.set_color(INK)

    fig.subplots_adjust(left=0.055, right=0.985, bottom=0.23, top=0.82, wspace=0.28)
    fig.suptitle(
        "ETHUSDT.P 15m expansion-confluence transport diagnostic",
        fontsize=15,
        color=INK,
        y=0.96,
    )
    fig.text(
        0.5,
        0.045,
        "Exact 0.85 gate; V16 execution frozen; 20 bp round-trip cost; holdout rows returned: 0",
        ha="center",
        va="bottom",
        fontsize=9,
        color=NEUTRAL,
    )
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE, dpi=180, facecolor="white", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    """Build all report diagnostics and the static figure."""

    ledgers, markets, sources = reconstruct()
    build_diagnostics(ledgers, markets, sources)
    build_figure()


if __name__ == "__main__":
    main()
