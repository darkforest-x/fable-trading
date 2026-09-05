#!/usr/bin/env python3
"""Build diagnostics for the consumed altcoin daily V4 holdout.

Only committed V4 result ledgers are read. No candle cache, sealed V3
confirmation-B source, model, TradingView, ACTIVE, forward, deployment, or live
state is opened or changed. All feature and subgroup analyses are explicitly
post-holdout diagnostics; they cannot select a revised historical strategy.
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
    / "exp-altcoin-1d-k1k2-early-launch-holdout-20260905-v4"
)
RESULTS = EXPERIMENT / "results"
OUTPUT = (
    ROOT
    / "analysis/output"
    / "altcoin_1d_k1k2_early_launch_holdout_20260905_v4"
)

INK = "#24313A"
BLUE = "#267A91"
GOLD = "#D9A441"
RED = "#C95C54"
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
    """Exact one-sided label permutation for the eight breadth-passing trades."""

    valid = outcome.notna() & score.notna()
    labels = outcome.loc[valid].astype(bool).to_numpy()
    ranks = score.loc[valid].astype(float).rank(method="average").to_numpy()
    positive = int(labels.sum())
    negative = int((~labels).sum())
    observed = rank_auc(outcome.loc[valid], score.loc[valid])
    null: list[float] = []
    for indices in itertools.combinations(range(len(labels)), positive):
        rank_sum = float(ranks[list(indices)].sum())
        null.append(
            (rank_sum - positive * (positive + 1) / 2)
            / (positive * negative)
        )
    values = np.asarray(null, dtype=float)
    return observed, float(np.mean(values >= observed)), int(len(values))


def exact_group_mean_p(
    selected: pd.DataFrame, pool: pd.DataFrame
) -> tuple[float, float, int]:
    """Exact one-sided allocation test for a frozen selected-versus-rejected split."""

    rejected = pool.loc[~pool["setup_id"].isin(selected["setup_id"])].copy()
    values = np.r_[
        selected["net_return"].to_numpy(dtype=float),
        rejected["net_return"].to_numpy(dtype=float),
    ]
    selected_count = len(selected)
    observed = float(
        selected["net_return"].mean() - rejected["net_return"].mean()
    )
    null: list[float] = []
    for indices in itertools.combinations(range(len(values)), selected_count):
        mask = np.zeros(len(values), dtype=bool)
        mask[list(indices)] = True
        null.append(float(values[mask].mean() - values[~mask].mean()))
    return observed, float(np.mean(np.asarray(null) >= observed)), int(len(null))


def policy_comparison(receipt: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, label in (
        ("baseline", "Raw K1→K2"),
        ("breadth_only", "Breadth Δ5 ≥ 2pp"),
        ("candidate", "+ K1 extension ≤ 0.75ATR"),
    ):
        metrics = receipt[key]
        rows.append(
            {
                "policy": label,
                "events": int(metrics["events"]),
                "symbols": int(metrics["symbols"]),
                "mean_net_bp": float(metrics["mean_net_bp"]),
                "median_net_bp": float(metrics["median_net_bp"]),
                "profit_factor": float(metrics["profit_factor"]),
                "win_rate": float(metrics["win_rate"]),
                "mean_capped_net_r": float(metrics["mean_capped_net_r"]),
                "positive_folds": int(metrics["positive_folds"]),
                "total_folds": int(metrics["total_folds"]),
                "minimum_fold_events": int(metrics["minimum_fold_events"]),
                "positive_symbol_share": float(metrics["positive_symbol_share"]),
                "runner_armed_share": float(metrics["runner_armed_share"]),
                "week_cluster_signflip_p": float(
                    metrics["week_cluster_signflip_p"]
                ),
                "sample_eligible": bool(metrics["eligible"]),
            }
        )
    return pd.DataFrame(rows)


def candidate_diagnostics(candidate: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "symbol",
        "direction",
        "source_cohort",
        "k1_time",
        "signal_time",
        "entry_time",
        "exit_time",
        "hold_bars",
        "k1_signed_slow_side_atr",
        "context_breadth_change5",
        "transition_votes",
        "signal_score",
        "outcome",
        "net_return",
        "net_return_r",
        "runner_armed",
        "bank_hits",
        "banked_fraction",
        "horizon_mfe_r",
        "gave_back_r",
    ]
    result = candidate[columns].copy()
    result["side"] = np.where(result["direction"].eq(1), "LONG", "SHORT")
    result["net_bp"] = result["net_return"] * 10_000.0
    result["exit_observation"] = np.where(
        result["outcome"].eq("phase_end_timeout"), "right_censored", "rule_exit"
    )
    return result


def subgroup_diagnostics(candidate: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    groups = {
        "SHORT": candidate["direction"].eq(-1),
        "LONG": candidate["direction"].eq(1),
        "phase-end mark": candidate["outcome"].eq("phase_end_timeout"),
        "rule exit": ~candidate["outcome"].eq("phase_end_timeout"),
        "runner armed": candidate["runner_armed"].astype(bool),
        "runner not armed": ~candidate["runner_armed"].astype(bool),
    }
    for label, mask in groups.items():
        current = candidate.loc[mask]
        rows.append(
            {
                "group": label,
                "events": int(len(current)),
                "mean_net_bp": float(current["net_return"].mean() * 10_000.0),
                "total_net_bp": float(current["net_return"].sum() * 10_000.0),
                "win_rate": float(current["net_return"].gt(0).mean()),
                "mean_net_r": float(current["net_return_r"].mean()),
            }
        )
    return pd.DataFrame(rows)


def coverage_diagnostics(
    source: pd.DataFrame, reference: pd.DataFrame
) -> pd.DataFrame:
    expected_last = pd.Timestamp("2026-08-31", tz="UTC")
    rows: list[dict[str, Any]] = []
    source["last_daily_bar"] = pd.to_datetime(source["last_daily_bar"], utc=True)
    for cohort, current in source.groupby("cohort", sort=True):
        eligible = current.loc[current["status"].eq("eligible")]
        last = eligible["last_daily_bar"].max()
        rows.append(
            {
                "cohort": cohort,
                "configured_symbols": int(len(current)),
                "eligible_symbols": int(len(eligible)),
                "latest_daily_bar": last,
                "calendar_days_missing_to_registered_end": int(
                    (expected_last - last).days
                ),
            }
        )
    reference["last_daily_bar"] = pd.to_datetime(reference["last_daily_bar"], utc=True)
    for row in reference.to_dict("records"):
        last = row["last_daily_bar"]
        rows.append(
            {
                "cohort": f"reference_{row['symbol']}",
                "configured_symbols": 1,
                "eligible_symbols": int(row["status"] == "eligible"),
                "latest_daily_bar": last,
                "calendar_days_missing_to_registered_end": int(
                    (expected_last - last).days
                ),
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
    comparison: pd.DataFrame,
    candidate: pd.DataFrame,
    folds: pd.DataFrame,
    subgroups: pd.DataFrame,
) -> Path:
    figure, axes = plt.subplots(2, 2, figsize=(15, 10))
    figure.patch.set_facecolor("white")
    figure.subplots_adjust(
        left=0.07, right=0.985, bottom=0.08, top=0.88, hspace=0.42, wspace=0.18
    )
    figure.suptitle(
        "Altcoin daily K1→K2 early-launch holdout",
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
        "REJECT · one-shot 2026-05-04→2026-08-31 · 20 bp cost · no production change",
        ha="left",
        fontsize=10,
        color="#5C6973",
    )

    axis = axes[0, 0]
    style_axis(axis)
    bars = axis.bar(
        ["Raw\nK1→K2", "Breadth\nfilter", "Early-launch\ncap"],
        comparison["events"],
        color=[NEUTRAL, GOLD, BLUE],
        edgecolor=INK,
        linewidth=0.5,
    )
    axis.axhline(20, color=RED, linestyle="--", linewidth=1.3, label="Minimum n=20")
    axis.set_ylabel("Executed holdout trades", color=INK)
    axis.set_title("Signal funnel", loc="left", color=INK, fontweight="bold")
    axis.bar_label(bars, labels=comparison["events"].astype(str), padding=3)
    axis.legend(frameon=False, loc="upper right")

    axis = axes[0, 1]
    style_axis(axis)
    ordered = candidate.sort_values("net_return").copy()
    ordered["net_bp"] = ordered["net_return"] * 10_000.0
    colors = np.where(ordered["direction"].eq(-1), BLUE, GOLD)
    bars = axis.barh(
        ordered["symbol"], ordered["net_bp"], color=colors, edgecolor=INK, linewidth=0.5
    )
    axis.axvline(0, color=INK, linewidth=1.0)
    axis.grid(axis="x", color=GRID, linewidth=0.8)
    axis.grid(axis="y", visible=False)
    axis.set_xlabel("Net return (bp)", color=INK)
    axis.set_title("Four accepted trades: blue SHORT, gold LONG", loc="left", color=INK, fontweight="bold")
    axis.bar_label(
        bars,
        fmt="%+.0f",
        label_type="center",
        fontsize=9,
        color=INK,
        fontweight="bold",
    )

    axis = axes[1, 0]
    style_axis(axis)
    bars = axis.bar(
        ["May–Jun", "Jul–Aug"],
        folds["events"],
        color=[BLUE, NEUTRAL],
        edgecolor=INK,
        linewidth=0.5,
    )
    axis.axhline(5, color=RED, linestyle="--", linewidth=1.3, label="Minimum n=5/fold")
    labels = []
    for row in folds.to_dict("records"):
        mean = row["mean_net_bp"]
        labels.append(
            f"n={int(row['events'])}\n{mean:+.0f}bp" if np.isfinite(mean) else "n=0"
        )
    axis.bar_label(bars, labels=labels, padding=4, fontsize=9)
    axis.set_ylabel("Trades", color=INK)
    axis.set_title("Temporal stability failed", loc="left", color=INK, fontweight="bold")
    axis.legend(frameon=False, loc="upper right")

    axis = axes[1, 1]
    style_axis(axis)
    subgroup_order = ["SHORT", "LONG", "phase-end mark", "rule exit"]
    plotted = subgroups.set_index("group").loc[subgroup_order]
    bars = axis.bar(
        ["SHORT", "LONG", "Phase-end\nmark", "Rule\nexit"],
        plotted["mean_net_bp"],
        color=[BLUE, GOLD, BLUE, GOLD],
        edgecolor=INK,
        linewidth=0.5,
    )
    axis.axhline(0, color=INK, linewidth=1.0)
    axis.set_ylabel("Mean net return (bp/trade)", color=INK)
    axis.set_title(
        "Direction = censoring in this n=4 sample",
        loc="left",
        color=INK,
        fontweight="bold",
    )
    axis.set_ylim(-1700, 6500)
    for bar, value, events in zip(
        bars, plotted["mean_net_bp"], plotted["events"]
    ):
        y = float(value) + (160 if value >= 0 else -160)
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            y,
            f"{value:+.0f}\nn={events}",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=9,
            color=INK,
        )

    path = OUTPUT / "altcoin_1d_k1k2_early_launch_holdout_diagnosis.png"
    figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return path


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = read_json(RESULTS / "holdout_receipt.json")
    baseline = pd.read_csv(RESULTS / "holdout_baseline_trades.csv.gz")
    breadth = pd.read_csv(RESULTS / "holdout_breadth_trades.csv.gz")
    candidate = pd.read_csv(RESULTS / "holdout_candidate_trades.csv.gz")
    folds = pd.read_csv(RESULTS / "holdout_candidate_folds.csv")
    source = pd.read_csv(RESULTS / "holdout_source_quality.csv")
    reference = pd.read_csv(RESULTS / "holdout_reference_quality.csv")
    attempts = pd.read_csv(RESULTS / "holdout_signal_attempts.csv.gz")
    matched_pairs = pd.read_csv(RESULTS / "holdout_matched_pairs.csv")

    comparison = policy_comparison(receipt)
    candidate_table = candidate_diagnostics(candidate)
    subgroups = subgroup_diagnostics(candidate)
    coverage = coverage_diagnostics(source, reference)
    gate_table = pd.DataFrame(
        [
            {"gate": gate, "passed": bool(passed)}
            for gate, passed in receipt["gate_checks"].items()
        ]
    )
    rejection_counts = (
        pd.read_csv(RESULTS / "holdout_candidate_eligibility_rejections.csv.gz")
        ["context_rejection_reason"]
        .value_counts()
        .rename_axis("reason")
        .reset_index(name="setups")
    )
    breadth_delta, breadth_p, breadth_null = exact_group_mean_p(breadth, baseline)
    cap_delta, cap_p, cap_null = exact_group_mean_p(candidate, breadth)
    auc, auc_p, auc_null = exact_auc_p(
        breadth["net_return"].gt(0), -breadth["k1_signed_slow_side_atr"]
    )
    ordered = candidate.sort_values("net_return", ascending=False)
    top_two_share = float(ordered.head(2)["net_return"].sum() / ordered["net_return"].sum())
    leave_top_two_mean = float(ordered.iloc[2:]["net_return"].mean() * 10_000.0)

    write_csv(comparison, OUTPUT / "policy_comparison.csv")
    write_csv(folds, OUTPUT / "candidate_fold_metrics.csv")
    write_csv(candidate_table, OUTPUT / "candidate_trade_diagnostics.csv")
    write_csv(subgroups, OUTPUT / "direction_and_censoring.csv")
    write_csv(coverage, OUTPUT / "source_coverage.csv")
    write_csv(gate_table, OUTPUT / "acceptance_gates.csv")
    write_csv(rejection_counts, OUTPUT / "eligibility_rejections.csv")
    figure = render_figure(comparison, candidate, folds, subgroups)

    summary = {
        "experiment_id": receipt["experiment_id"],
        "verdict": receipt["status"],
        "holdout_consumption_number": receipt[
            "holdout_consumption_number_for_this_configuration"
        ],
        "signal_funnel": {
            "full_materialized_window_k1_attempts": int(len(attempts)),
            "holdout_raw_executed": int(len(baseline)),
            "holdout_breadth_executed": int(len(breadth)),
            "holdout_candidate_executed": int(len(candidate)),
            "matched_candidate_events": int(
                matched_pairs["match_status"].eq("matched_exact_context_eligible").sum()
            ),
        },
        "frozen_increment_tests": {
            "breadth_selected_minus_rejected_mean_bp": breadth_delta * 10_000.0,
            "breadth_exact_one_sided_p": breadth_p,
            "breadth_null_allocations": breadth_null,
            "extension_selected_minus_rejected_mean_bp": cap_delta * 10_000.0,
            "extension_exact_one_sided_p": cap_p,
            "extension_null_allocations": cap_null,
        },
        "single_feature_baseline": {
            "feature": "negative k1_signed_slow_side_atr",
            "pool": "eight breadth-passing holdout trades",
            "positive_trade_auc": auc,
            "exact_one_sided_label_permutation_p": auc_p,
            "exact_null_assignments": auc_null,
        },
        "model_validation_auc": None,
        "model_validation_auc_reason": "No model was trained or scored in this deterministic rule experiment.",
        "concentration": {
            "largest_winner_share": receipt["winner_concentration"][
                "largest_winner_share_of_total"
            ],
            "leave_largest_winner_out_mean_bp": receipt["winner_concentration"][
                "leave_largest_winner_out_mean_net_bp"
            ],
            "top_two_winner_share": top_two_share,
            "leave_top_two_out_mean_bp": leave_top_two_mean,
        },
        "coverage": {
            "eligible_target_symbols": receipt["eligible_target_symbols"],
            "configured_target_symbols": receipt["selected_target_symbols"],
            "reference_last_complete_day": "2026-07-11",
            "target_last_complete_day": "2026-08-13",
            "registered_end_last_day": "2026-08-31",
            "sealed_confirmation_b_paths_opened": 0,
        },
        "figure": str(figure.relative_to(ROOT)),
        "interpretation": "Post-holdout diagnostics only; they may define a future prospective preregistration but cannot justify a historical rerun or threshold change.",
    }
    write_json(OUTPUT / "summary.json", summary)
    write_json(
        OUTPUT / "chart_map.json",
        {
            "charts": [
                "signal funnel versus minimum sample",
                "candidate trade returns by direction",
                "fold sample and mean return",
                "direction/right-censoring confound",
            ],
            "qa_surface": str(figure.relative_to(ROOT)),
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
