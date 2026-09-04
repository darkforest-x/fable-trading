#!/usr/bin/env python3
"""Build figures and derived checks for the BTC 15m runner-isolation report.

All inputs are fixed outputs of the pre-holdout retrospective experiment.  The
builder does not open raw market data and cannot read the repository holdout.
It deliberately separates entry-time selection, executable management probes,
and the outcome-conditioned 170-trade decomposition.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    ROOT
    / "experiments/active"
    / "exp-btcusdtp-15m-runner-isolation-preholdout-20260904-v1"
)
RESULTS = EXPERIMENT / "results"
OUTPUT = ROOT / "analysis/output/btcusdtp_15m_runner_isolation_20260904"
PARENT_RESULTS = (
    ROOT
    / "experiments/active"
    / "exp-btcusdtp-15m-multifactor-confluence-preholdout-20260904-v1"
    / "results"
)
COLORS = {
    "blue": "#3976B8",
    "gold": "#DB9B24",
    "teal": "#1B998B",
    "coral": "#D96570",
    "ink": "#26323A",
    "gray": "#87949D",
    "grid": "#DCE3E7",
}
PHASE_LABEL = {
    "development": "2023 dev",
    "confirmation": "2024 replay",
    "audit": "2025-26 audit",
}
VARIANT_LABEL = {
    "signal_score": "Signal score",
    "best_single_feature_arm": "Best 1 feature",
    "strict_k1k2_rule": "Strict K1-K2",
    "lgb_arm_all103": "LGB arm (103)",
    "lgb_profitable_runner_all103": "LGB profitable arm",
    "lgb_two_stage_expected_net": "Two-stage EV",
    "aeon_minirocket_arm_seq64": "MiniRocket (64 bars)",
}


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.dpi": 180,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "axes.edgecolor": COLORS["grid"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": COLORS["grid"],
            "grid.linewidth": 0.7,
            "grid.alpha": 0.75,
            "legend.frameon": False,
        }
    )


def _save(fig: plt.Figure, name: str) -> None:
    fig.savefig(OUTPUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_exante_transport(frozen: pd.DataFrame) -> pd.DataFrame:
    future = frozen.loc[frozen["phase"].isin(["confirmation", "audit"])].copy()
    order = list(VARIANT_LABEL)
    y = np.arange(len(order))
    fig, axes = plt.subplots(1, 3, figsize=(14, 6.1), sharey=True)
    metrics = [
        ("runner_arm_auc", "Runner AUC", 0.5),
        ("runner_arm_precision_lift_pp", "Precision lift (pp)", 0.0),
        ("mean_net_bp", "Net mean (bp)", 0.0),
    ]
    for phase, offset, color in (
        ("confirmation", -0.16, COLORS["blue"]),
        ("audit", 0.16, COLORS["gold"]),
    ):
        lookup = future.loc[future["phase"].eq(phase)].set_index("variant")
        for axis, (column, title, reference) in zip(axes, metrics):
            values = [float(lookup.loc[item, column]) for item in order]
            axis.barh(
                y + offset,
                values,
                height=0.29,
                color=color,
                label=PHASE_LABEL[phase],
                alpha=0.92,
            )
            axis.axvline(reference, color=COLORS["ink"], linewidth=1.0, alpha=0.8)
            axis.set_title(title)
    axes[0].set_yticks(y, [VARIANT_LABEL[item] for item in order])
    axes[0].invert_yaxis()
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(0.985, 0.985))
    axes[1].set_xlabel("Above zero is better")
    axes[2].set_xlabel("20bp round-trip cost included")
    fig.suptitle(
        "Entry-time selectors do not transport",
        x=0.06,
        ha="left",
        fontsize=16,
        fontweight="bold",
        color=COLORS["ink"],
    )
    fig.text(
        0.06,
        0.925,
        "Thresholds learned on 2023 and replayed unchanged; no future-period row clears both discrimination and economics.",
        color=COLORS["gray"],
    )
    fig.subplots_adjust(top=0.84, wspace=0.18, left=0.22)
    _save(fig, "exante_selector_transport.png")
    future.to_csv(OUTPUT / "exante_selector_transport.csv", index=False)
    return future


def plot_density(density: pd.DataFrame) -> pd.DataFrame:
    frame = density.loc[density["variant"].eq("lgb_arm_all103")].copy()
    fig, axes = plt.subplots(1, 3, figsize=(13.6, 4.4))
    quantiles = [0.5, 0.7, 0.8, 0.9, 0.95, 0.975, 0.99]
    positions = np.arange(len(quantiles))
    for phase, color in (
        ("confirmation", COLORS["blue"]),
        ("audit", COLORS["gold"]),
    ):
        part = frame.loc[frame["phase"].eq(phase)].sort_values("training_quantile")
        axes[0].plot(
            positions,
            part["runner_arm_precision"].to_numpy(dtype=float) * 100.0,
            marker="o",
            color=color,
            label=PHASE_LABEL[phase],
        )
        axes[1].plot(positions, part["mean_net_bp"], marker="o", color=color)
        axes[2].plot(
            positions,
            part["runner_arm_recall"].to_numpy(dtype=float) * 100.0,
            marker="o",
            color=color,
        )
    axes[0].axhline(54.49, color=COLORS["coral"], linestyle="--", linewidth=1.2)
    axes[0].set_title("Runner precision")
    axes[0].set_ylabel("% selected trades armed")
    axes[0].legend(loc="lower right")
    axes[1].axhline(0.0, color=COLORS["ink"], linewidth=1.0)
    axes[1].set_title("Net return")
    axes[1].set_ylabel("bp / trade")
    axes[2].set_title("Runner recall")
    axes[2].set_ylabel("% of all armed trades retained")
    for axis in axes:
        axis.set_xlabel("Frozen 2023 score percentile")
        axis.set_xticks(positions, ["50", "70", "80", "90", "95", "97.5", "99"])
    fig.suptitle(
        "Raising the threshold trades almost all recall for an unstable tail",
        x=0.065,
        ha="left",
        fontsize=16,
        fontweight="bold",
        color=COLORS["ink"],
    )
    fig.text(
        0.065,
        0.89,
        "Dashed line is the approximate 54.5% break-even precision. Audit q99 is positive on 24 trades; the same frozen threshold loses in 2024.",
        color=COLORS["gray"],
    )
    fig.subplots_adjust(top=0.79, wspace=0.3)
    _save(fig, "density_precision_recall_tradeoff.png")
    frame.to_csv(OUTPUT / "density_precision_recall_tradeoff.csv", index=False)
    return frame


def _parent_baselines() -> pd.DataFrame:
    files = {
        "development": "development_feature_ledger.csv.gz",
        "confirmation": "confirmation_feature_ledger.csv.gz",
        "audit": "audit_feature_ledger.csv.gz",
    }
    rows = []
    for phase, name in files.items():
        frame = pd.read_csv(PARENT_RESULTS / name, usecols=["net_return"])
        rows.append(
            {
                "phase": phase,
                "policy": "Frozen runner baseline",
                "mean_net_bp": float(frame["net_return"].mean() * 1e4),
            }
        )
    return pd.DataFrame(rows)


def plot_management(
    progress: pd.DataFrame,
    early: pd.DataFrame,
    delayed: pd.DataFrame,
) -> pd.DataFrame:
    baseline = _parent_baselines()
    progress_choice = progress.loc[
        progress["deadline_bars"].eq(4)
        & progress["required_close_atr"].eq(0.25)
    ].copy()
    progress_choice["policy"] = "4-bar progress stop"
    early_choice = early.loc[
        early["checkpoint_bars"].eq(2)
        & early["keep_training_quantile"].eq(0.5)
    ].copy()
    early_choice["policy"] = "2-bar learned gate"
    comparison = pd.concat(
        [
            baseline,
            progress_choice[["phase", "policy", "mean_net_bp"]],
            early_choice[["phase", "policy", "mean_net_bp"]],
        ],
        ignore_index=True,
    )
    policy_order = [
        "Frozen runner baseline",
        "4-bar progress stop",
        "2-bar learned gate",
    ]
    phase_order = ["development", "confirmation", "audit"]
    colors = [COLORS["gray"], COLORS["teal"], COLORS["blue"]]
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 4.6))
    x = np.arange(len(phase_order))
    for position, (policy, color) in enumerate(zip(policy_order, colors)):
        lookup = comparison.loc[comparison["policy"].eq(policy)].set_index("phase")
        axes[0].bar(
            x + (position - 1) * 0.24,
            [lookup.loc[phase, "mean_net_bp"] for phase in phase_order],
            width=0.22,
            color=color,
            label=policy,
        )
    axes[0].axhline(0.0, color=COLORS["ink"], linewidth=1.0)
    axes[0].set_xticks(x, [PHASE_LABEL[item] for item in phase_order])
    axes[0].set_ylabel("net bp / original candidate")
    axes[0].set_title("Executable early exits remain negative")
    axes[0].legend(loc="upper right")

    delayed_order = ["development", "confirmation", "audit", "audit_selected"]
    delayed_lookup = delayed.set_index("phase")
    axes[1].bar(
        np.arange(len(delayed_order)),
        [delayed_lookup.loc[item, "mean_net_bp"] for item in delayed_order],
        color=[COLORS["teal"], COLORS["blue"], COLORS["gold"], COLORS["coral"]],
    )
    axes[1].axhline(0.0, color=COLORS["ink"], linewidth=1.0)
    axes[1].set_xticks(
        np.arange(len(delayed_order)),
        ["2023", "2024", "2025-26", "the 170"],
    )
    axes[1].set_ylabel("net bp / confirmed entry")
    axes[1].set_title("Waiting for +2ATR gives away the launch")
    fig.suptitle(
        "Recognizing the trend later does not rescue the execution",
        x=0.065,
        ha="left",
        fontsize=16,
        fontweight="bold",
        color=COLORS["ink"],
    )
    fig.text(
        0.065,
        0.90,
        "Left uses the original candidate denominator; right contains only trades that actually confirmed +2ATR.",
        color=COLORS["gray"],
    )
    fig.subplots_adjust(top=0.80, wspace=0.28)
    _save(fig, "management_probe_economics.png")
    comparison.to_csv(OUTPUT / "management_probe_comparison.csv", index=False)
    return comparison


def plot_early_classifier(early: pd.DataFrame) -> pd.DataFrame:
    q50 = early.loc[early["keep_training_quantile"].eq(0.5)].copy()
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.3))
    for phase, color in (
        ("confirmation", COLORS["blue"]),
        ("audit", COLORS["gold"]),
    ):
        part = q50.loc[q50["phase"].eq(phase)].sort_values("checkpoint_bars")
        axes[0].plot(
            part["checkpoint_bars"],
            part["eligible_runner_arm_auc"],
            marker="o",
            color=color,
            label=PHASE_LABEL[phase],
        )
        axes[1].plot(
            part["checkpoint_bars"],
            part["mean_net_bp"],
            marker="o",
            color=color,
        )
    axes[0].axhline(0.5, color=COLORS["ink"], linewidth=1.0)
    axes[0].set_title("State becomes distinguishable")
    axes[0].set_ylabel("eligible runner AUC")
    axes[0].legend(loc="lower right")
    axes[1].axhline(0.0, color=COLORS["ink"], linewidth=1.0)
    axes[1].set_title("But the policy stays unprofitable")
    axes[1].set_ylabel("net bp / original candidate")
    for axis in axes:
        axis.set_xlabel("completed bars after entry")
        axis.set_xticks([2, 4, 8, 12])
    fig.suptitle(
        "Classification quality and tradable value diverge",
        x=0.08,
        ha="left",
        fontsize=16,
        fontweight="bold",
        color=COLORS["ink"],
    )
    fig.subplots_adjust(top=0.80, wspace=0.27)
    _save(fig, "early_classifier_auc_vs_pnl.png")
    q50.to_csv(OUTPUT / "early_classifier_q50.csv", index=False)
    return q50


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _style()
    frozen = pd.read_csv(RESULTS / "frozen_exante_metrics.csv")
    density = pd.read_csv(RESULTS / "frozen_density_sensitivity.csv")
    progress = pd.read_csv(RESULTS / "progress_stop_grid.csv")
    early = pd.read_csv(RESULTS / "early_classifier_grid.csv")
    delayed = pd.read_csv(RESULTS / "delayed_confirmation_entry_metrics.csv")
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    future = plot_exante_transport(frozen)
    density_frame = plot_density(density)
    management = plot_management(progress, early, delayed)
    early_q50 = plot_early_classifier(early)

    parent = summary["parent_outcome_conditioned_decomposition"]
    armed = float(parent["runner_armed_mean_net_bp"])
    unarmed = float(parent["never_armed_mean_net_bp"])
    break_even = -unarmed / (armed - unarmed)
    audit_q99 = density_frame.loc[
        density_frame["phase"].eq("audit")
        & density_frame["training_quantile"].eq(0.99)
    ].iloc[0]
    confirmation_q99 = density_frame.loc[
        density_frame["phase"].eq("confirmation")
        & density_frame["training_quantile"].eq(0.99)
    ].iloc[0]
    checks: dict[str, Any] = {
        "experiment_id": summary["experiment_id"],
        "holdout_rows_read": int(summary["holdout_rows_read"]),
        "approximate_break_even_arm_precision": break_even,
        "confirmation_q99": {
            "events": int(confirmation_q99["events"]),
            "runner_arm_precision": float(confirmation_q99["runner_arm_precision"]),
            "runner_arm_recall": float(confirmation_q99["runner_arm_recall"]),
            "mean_net_bp": float(confirmation_q99["mean_net_bp"]),
        },
        "audit_q99": {
            "events": int(audit_q99["events"]),
            "runner_arm_precision": float(audit_q99["runner_arm_precision"]),
            "runner_arm_recall": float(audit_q99["runner_arm_recall"]),
            "mean_net_bp": float(audit_q99["mean_net_bp"]),
        },
        "future_selector_rows": int(len(future)),
        "management_rows": int(len(management)),
        "early_q50_rows": int(len(early_q50)),
        "all_registered_gates_pass": bool(summary["gates"]["all_pass"]),
    }
    (OUTPUT / "report_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
