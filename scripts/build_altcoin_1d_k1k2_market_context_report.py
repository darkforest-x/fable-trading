#!/usr/bin/env python3
"""Build postmortem diagnostics for the frozen altcoin daily V3 experiment.

Only committed V3 result ledgers are read. No candle cache, confirmation B,
repository holdout, model training, TradingView, ACTIVE, forward or order state
is touched. Feature AUC and concentration statistics are post-confirmation
diagnostics and must never be presented as preregistered selection evidence.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    ROOT
    / "experiments/active"
    / "exp-altcoin-1d-k1k2-market-context-preholdout-20260905-v3"
)
RESULTS = EXPERIMENT / "results"
OUTPUT = ROOT / "analysis/output/altcoin_1d_k1k2_market_context_20260905_v3"

INK = "#24313A"
BLUE = "#2A6F97"
GOLD = "#D9A441"
NEUTRAL = "#AAB4BD"
GRID = "#DDE3E8"
BACKGROUND = "#FAFBFC"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def rank_auc(outcome: pd.Series, score: pd.Series) -> float:
    valid = outcome.notna() & score.notna()
    labels = outcome.loc[valid].astype(bool).to_numpy()
    ranks = score.loc[valid].astype(float).rank(method="average").to_numpy()
    positive = int(labels.sum())
    negative = int((~labels).sum())
    if positive == 0 or negative == 0:
        return float("nan")
    return float(
        (ranks[labels].sum() - positive * (positive + 1) / 2)
        / (positive * negative)
    )


def exact_auc_p(outcome: pd.Series, score: pd.Series) -> tuple[float, float, int]:
    """Exact one-sided label-permutation p for the small confirmation sample."""

    valid = outcome.notna() & score.notna()
    labels = outcome.loc[valid].astype(bool).to_numpy()
    ranks = score.loc[valid].astype(float).rank(method="average").to_numpy()
    positive = int(labels.sum())
    negative = int((~labels).sum())
    observed = rank_auc(outcome.loc[valid], score.loc[valid])
    statistics: list[float] = []
    for indices in itertools.combinations(range(len(labels)), positive):
        rank_sum = float(ranks[list(indices)].sum())
        statistics.append(
            (rank_sum - positive * (positive + 1) / 2) / (positive * negative)
        )
    null = np.asarray(statistics, dtype=float)
    p_value = float((np.sum(null >= observed) + 1) / (len(null) + 1))
    return observed, p_value, int(len(null))


def phase_comparison(
    development: dict[str, Any], confirmation: dict[str, Any]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for phase, receipt in (
        ("development_seen_52", development),
        ("confirmation_a_disjoint", confirmation),
    ):
        for policy in ("baseline", "candidate"):
            metrics = receipt[policy]
            rows.append(
                {
                    "phase": phase,
                    "policy": policy,
                    "events": int(metrics["events"]),
                    "symbols": int(metrics["symbols"]),
                    "mean_net_bp": float(metrics["mean_net_bp"]),
                    "median_net_bp": float(metrics["median_net_bp"]),
                    "profit_factor": float(metrics["profit_factor"]),
                    "win_rate": float(metrics["win_rate"]),
                    "mean_net_r": float(metrics["mean_net_r"]),
                    "robust_score_r": float(metrics["robust_score_r"]),
                    "positive_folds": int(metrics["positive_folds"]),
                    "total_folds": int(metrics["total_folds"]),
                    "minimum_fold_events": int(metrics["minimum_fold_events"]),
                    "positive_symbol_share": float(metrics["positive_symbol_share"]),
                    "runner_armed_share": float(metrics["runner_armed_share"]),
                    "p95_raw_net_r": float(metrics["p95_raw_net_r"]),
                    "top_score_decile_events": int(
                        metrics["top_score_decile_events"]
                    ),
                    "top_score_decile_mean_net_bp": float(
                        metrics["top_score_decile_mean_net_bp"]
                    ),
                    "week_cluster_signflip_p": float(
                        metrics["week_cluster_signflip_p"]
                    ),
                }
            )
    return pd.DataFrame(rows)


def trade_concentration(candidate: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    ordered = candidate.sort_values("net_return", ascending=False).copy()
    ordered["net_bp"] = ordered["net_return"] * 10_000.0
    ordered["rank"] = np.arange(1, len(ordered) + 1)
    total = float(ordered["net_return"].sum())
    rows: list[dict[str, Any]] = []
    for count in (1, 2, 3, 5):
        remaining = ordered.iloc[count:]
        rows.append(
            {
                "removed_top_winners": count,
                "removed_return_sum": float(ordered.head(count)["net_return"].sum()),
                "share_of_total_net_return": (
                    float(ordered.head(count)["net_return"].sum() / total)
                    if total != 0
                    else np.nan
                ),
                "remaining_events": int(len(remaining)),
                "remaining_mean_net_bp": (
                    float(remaining["net_return"].mean() * 10_000.0)
                    if len(remaining)
                    else np.nan
                ),
            }
        )
    summary = {
        "events": int(len(ordered)),
        "total_net_return_sum": total,
        "winning_events": int(ordered["net_return"].gt(0).sum()),
        "losing_events": int(ordered["net_return"].le(0).sum()),
        "winner_return_sum": float(ordered.loc[ordered["net_return"].gt(0), "net_return"].sum()),
        "loser_return_sum": float(ordered.loc[ordered["net_return"].le(0), "net_return"].sum()),
        "largest_winner_symbol": str(ordered.iloc[0]["symbol"]),
        "largest_winner_net_bp": float(ordered.iloc[0]["net_bp"]),
        "largest_winner_share_of_total": float(ordered.iloc[0]["net_return"] / total),
        "leave_largest_winner_out_mean_net_bp": float(
            ordered.iloc[1:]["net_return"].mean() * 10_000.0
        ),
    }
    return pd.DataFrame(rows), summary


def runner_split(candidate: pd.DataFrame) -> pd.DataFrame:
    return (
        candidate.assign(
            runner_group=np.where(candidate["runner_armed"], "runner armed", "not armed")
        )
        .groupby("runner_group", as_index=False)
        .agg(
            events=("net_return", "size"),
            mean_net_bp=("net_return", lambda values: float(values.mean() * 10_000.0)),
            median_net_r=("net_return_r", "median"),
            win_rate=("net_return", lambda values: float(values.gt(0).mean())),
            mean_mfe_r=("mfe_at_exit_r", "mean"),
            mean_giveback_r=("gave_back_r", "mean"),
        )
        .sort_values("runner_group", kind="mergesort")
        .reset_index(drop=True)
    )


def gate_increment(
    baseline: pd.DataFrame, candidate: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    removed = baseline.loc[~baseline["setup_id"].isin(set(candidate["setup_id"]))].copy()
    removed["net_bp"] = removed["net_return"] * 10_000.0
    summary = {
        "removed_executed_trades": int(len(removed)),
        "removed_winners": int(removed["net_return"].gt(0).sum()),
        "removed_losers": int(removed["net_return"].le(0).sum()),
        "removed_mean_net_bp": float(removed["net_bp"].mean()),
        "removed_total_net_bp": float(removed["net_bp"].sum()),
        "baseline_total_net_bp": float(baseline["net_return"].sum() * 10_000.0),
        "candidate_total_net_bp": float(candidate["net_return"].sum() * 10_000.0),
    }
    return removed, summary


def feature_diagnostics(baseline: pd.DataFrame, *, phase: str) -> pd.DataFrame:
    features = (
        "context_breadth_change5",
        "context_breadth_level",
        "context_major_score",
        "context_relative_score",
        "context_mean",
        "signal_score",
    )
    rows: list[dict[str, Any]] = []
    for feature in features:
        rows.append(
            {
                "phase": phase,
                "feature": feature,
                "events": int(baseline[[feature, "net_return"]].dropna().shape[0]),
                "positive_trade_auc": rank_auc(
                    baseline["net_return"].gt(0), baseline[feature]
                ),
                "runner_armed_auc": rank_auc(
                    baseline["runner_armed"], baseline[feature]
                ),
                "spearman_net_r": float(
                    baseline[feature].corr(baseline["net_return_r"], method="spearman")
                ),
            }
        )
    return pd.DataFrame(rows)


def signal_funnel() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for phase in ("development", "confirmation_a"):
        attempts = pd.read_csv(RESULTS / f"{phase}_signal_attempts.csv.gz")
        pairs = pd.read_csv(RESULTS / f"{phase}_signal_pairs.csv.gz")
        setups = pd.read_csv(RESULTS / f"{phase}_context_setups.csv.gz")
        candidate = pd.read_csv(RESULTS / f"{phase}_candidate_trades.csv.gz")
        statuses = attempts["attempt_status"].value_counts()
        rows.append(
            {
                "phase": phase,
                "k1_attempts": int(len(attempts)),
                "wrong_side_invalidations": int(
                    statuses.get("invalidated_wrong_side_close", 0)
                ),
                "expired_without_k2": int(statuses.get("expired_without_k2", 0)),
                "k2_vote_rejections": int(statuses.get("k2_vote_rejected", 0)),
                "accepted_k2_attempts": int(statuses.get("k2_accepted", 0)),
                "all_k2_pairs": int(len(pairs)),
                "phase_eligible_setups": int(len(setups)),
                "context_eligible_trades": int(len(candidate)),
            }
        )
    return pd.DataFrame(rows)


def style_axis(axis: plt.Axes) -> None:
    axis.set_facecolor(BACKGROUND)
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines[["left", "bottom"]].set_color(NEUTRAL)
    axis.tick_params(colors=INK, labelsize=9)
    axis.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.9)
    axis.set_axisbelow(True)


def render_figure(
    phases: pd.DataFrame,
    folds: pd.DataFrame,
    candidate: pd.DataFrame,
    runners: pd.DataFrame,
) -> Path:
    figure, axes = plt.subplots(2, 2, figsize=(15, 10), constrained_layout=False)
    figure.patch.set_facecolor("white")
    figure.subplots_adjust(left=0.07, right=0.985, bottom=0.08, top=0.88, hspace=0.34, wspace=0.13)
    figure.suptitle(
        "Altcoin daily K1→K2 market-context evidence",
        x=0.04,
        y=0.975,
        ha="left",
        fontsize=20,
        fontweight="bold",
        color=INK,
    )
    figure.text(
        0.04,
        0.94,
        "Pre-2026-05-01 evidence only · 20 bp round-trip cost · confirmation B sealed",
        ha="left",
        fontsize=10,
        color="#5C6973",
    )

    axis = axes[0, 0]
    style_axis(axis)
    phase_order = ["development_seen_52", "confirmation_a_disjoint"]
    labels = ["Development\n(seen 52)", "Confirmation A\n(disjoint)"]
    x = np.arange(len(phase_order))
    width = 0.34
    baseline_values = [
        phases.loc[(phases.phase.eq(phase)) & phases.policy.eq("baseline"), "mean_net_bp"].iloc[0]
        for phase in phase_order
    ]
    candidate_values = [
        phases.loc[(phases.phase.eq(phase)) & phases.policy.eq("candidate"), "mean_net_bp"].iloc[0]
        for phase in phase_order
    ]
    bars_a = axis.bar(x - width / 2, baseline_values, width, color=NEUTRAL, label="No context gate")
    bars_b = axis.bar(x + width / 2, candidate_values, width, color=BLUE, label="Breadth Δ5 ≥ 2pp")
    axis.axhline(0, color=INK, linewidth=1.0)
    axis.set_xticks(x, labels)
    axis.set_ylabel("Mean net return (bp/trade)", color=INK)
    axis.set_title("Mean net return by phase", loc="left", color=INK, fontweight="bold")
    axis.legend(frameon=False, loc="upper left")
    axis.bar_label(bars_a, fmt="%+.0f", padding=3, color=INK, fontsize=9)
    axis.bar_label(bars_b, fmt="%+.0f", padding=3, color=INK, fontsize=9)

    axis = axes[0, 1]
    style_axis(axis)
    fold_labels = folds["fold"].str.replace("_", " ").tolist()
    fold_values = folds["mean_net_bp"].to_numpy(dtype=float)
    colors = [BLUE if value >= 0 else GOLD for value in fold_values]
    bars = axis.bar(fold_labels, fold_values, color=colors, edgecolor=INK, linewidth=0.5)
    axis.axhline(0, color=INK, linewidth=1.0)
    axis.set_ylabel("Mean net return (bp/trade)", color=INK)
    axis.set_title("Confirmation mean net return by fold", loc="left", color=INK, fontweight="bold")
    axis.tick_params(axis="x", rotation=18)
    axis.bar_label(
        bars,
        labels=[
            f"{value:+.0f}\nn={events}"
            for value, events in zip(fold_values, folds["events"].astype(int))
        ],
        padding=3,
        fontsize=8,
        color=INK,
    )

    axis = axes[1, 0]
    style_axis(axis)
    ranked = candidate.sort_values("net_return", ascending=True).copy()
    ranked["net_bp"] = ranked["net_return"] * 10_000.0
    colors = [
        GOLD if symbol == "YGG" else (BLUE if value >= 0 else NEUTRAL)
        for symbol, value in zip(ranked["symbol"], ranked["net_bp"])
    ]
    bars = axis.barh(ranked["symbol"], ranked["net_bp"], color=colors, edgecolor=INK, linewidth=0.4)
    axis.axvline(0, color=INK, linewidth=1.0)
    axis.grid(axis="x", color=GRID, linewidth=0.8)
    axis.grid(axis="y", visible=False)
    axis.set_xlabel("Net return (bp)", color=INK)
    axis.set_title("Confirmation trade distribution", loc="left", color=INK, fontweight="bold")
    axis.text(0.99, 0.02, "Gold = largest winner", transform=axis.transAxes, ha="right", fontsize=8, color=INK)

    axis = axes[1, 1]
    style_axis(axis)
    runner_order = ["not armed", "runner armed"]
    plotted = runners.set_index("runner_group").loc[runner_order].reset_index()
    bars = axis.bar(
        ["Not armed", "Runner armed"],
        plotted["mean_net_bp"],
        color=[NEUTRAL, BLUE],
        edgecolor=INK,
        linewidth=0.5,
    )
    axis.axhline(0, color=INK, linewidth=1.0)
    axis.set_ylabel("Mean net return (bp/trade)", color=INK)
    axis.set_title("Runner activation split (post-entry)", loc="left", color=INK, fontweight="bold")
    labels = [
        f"{value:+.0f}\nn={events}"
        for value, events in zip(plotted["mean_net_bp"], plotted["events"])
    ]
    axis.bar_label(bars, labels=labels, padding=4, fontsize=9, color=INK)
    axis.set_ylim(
        min(-850.0, float(plotted["mean_net_bp"].min()) * 1.35),
        max(2100.0, float(plotted["mean_net_bp"].max()) * 1.15),
    )

    path = OUTPUT / "altcoin_1d_k1k2_market_context_diagnosis.png"
    figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return path


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    development = read_json(RESULTS / "development_receipt.json")
    confirmation = read_json(RESULTS / "confirmation_a_receipt.json")
    boundary = read_json(EXPERIMENT / "confirmation_a_boundary_amendment.json")
    baseline = pd.read_csv(RESULTS / "confirmation_a_baseline_trades.csv.gz")
    candidate = pd.read_csv(RESULTS / "confirmation_a_candidate_trades.csv.gz")
    development_baseline = pd.read_csv(RESULTS / "development_baseline_trades.csv.gz")
    folds = pd.read_csv(RESULTS / "confirmation_a_candidate_folds.csv")

    phases = phase_comparison(development, confirmation)
    concentration_table, concentration = trade_concentration(candidate)
    runners = runner_split(candidate)
    removed, increment = gate_increment(baseline, candidate)
    features = pd.concat(
        [
            feature_diagnostics(development_baseline, phase="development_seen_52"),
            feature_diagnostics(baseline, phase="confirmation_a_disjoint"),
        ],
        ignore_index=True,
    )
    funnel = signal_funnel()
    auc, auc_p, auc_null = exact_auc_p(
        baseline["net_return"].gt(0), baseline["context_breadth_change5"]
    )

    write_csv(phases, OUTPUT / "phase_comparison.csv")
    write_csv(folds, OUTPUT / "confirmation_fold_metrics.csv")
    write_csv(concentration_table, OUTPUT / "winner_concentration.csv")
    write_csv(runners, OUTPUT / "runner_split.csv")
    write_csv(removed, OUTPUT / "gate_removed_trades.csv")
    write_csv(features, OUTPUT / "context_feature_diagnostics.csv")
    write_csv(funnel, OUTPUT / "signal_funnel.csv")
    figure = render_figure(phases, folds, candidate, runners)
    chart_map = {
        "delivery_surface": "project-mandated markdown plus md_to_html HTML",
        "palette_policy": "hard two-root cap: blue and gold plus neutrals",
        "charts": [
            {
                "segment": "context gate effect",
                "family": "comparison",
                "type": "grouped bar",
                "fields": ["phase", "policy", "mean_net_bp"],
            },
            {
                "segment": "temporal stability",
                "family": "comparison",
                "type": "period bar",
                "fields": ["fold", "mean_net_bp", "events"],
            },
            {
                "segment": "winner concentration",
                "family": "distribution",
                "type": "ranked horizontal bar",
                "fields": ["symbol", "net_return"],
            },
            {
                "segment": "runner diagnostic",
                "family": "comparison",
                "type": "two-category bar",
                "fields": ["runner_armed", "mean_net_bp", "events"],
            },
        ],
        "qa_surface": str(figure.relative_to(ROOT)),
    }
    write_json(OUTPUT / "chart_map.json", chart_map)
    summary = {
        "experiment_id": confirmation["experiment_id"],
        "registered_result": confirmation["status"],
        "selected_context_params": confirmation["selected_context_params"],
        "development": development,
        "confirmation_a": confirmation,
        "boundary_incident": boundary,
        "gate_increment": increment,
        "winner_concentration": concentration,
        "single_feature_postmortem": {
            "feature": "context_breadth_change5",
            "positive_trade_auc": auc,
            "exact_one_sided_label_permutation_p": auc_p,
            "exact_null_assignments": auc_null,
            "role": "post-confirmation diagnostic; not a selected model or preregistered gate",
        },
        "figure": str(figure.relative_to(ROOT)),
        "repository_holdout_rows_scored": 0,
        "sealed_confirmation_b_rows_read": 0,
    }
    write_json(OUTPUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
