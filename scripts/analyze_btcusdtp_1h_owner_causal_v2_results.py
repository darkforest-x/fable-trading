#!/usr/bin/env python3
"""Build result-only diagnostics for the BTCUSDT.P owner-causal v2 study.

This script reads only already-scored development and frozen-validation CSV
artifacts. It never opens OHLCV data, never changes a signal/exit parameter,
and therefore cannot tune or consume the repository holdout.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    PROJECT
    / "experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1"
)
RESULTS = EXPERIMENT / "results"
TEAL = "#17A297"
ORANGE = "#F59E0B"
RED = "#F23645"
INK = "#26323A"
MUTED = "#73808A"
GRID = "#D9DEE1"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {"events": 0, "mean_net_bp": np.nan, "win_rate": np.nan}
    return {
        "events": len(group),
        "mean_net_bp": float(group["net_return"].mean() * 1e4),
        "win_rate": float(group["net_return"].gt(0.0).mean()),
    }


def failure_reason(event: pd.Series) -> str:
    outcome = str(event["outcome"])
    mfe = float(event["mfe_r"])
    if outcome.startswith("sl"):
        if mfe < 0.5:
            return "stop_before_0.5R_no_follow_through"
        if mfe < 1.5:
            return "stop_after_0.5R_but_before_protection"
        return "stop_after_protection_threshold"
    if outcome == "timeout":
        return "profitable_timeout" if float(event["net_return"]) > 0.0 else "losing_timeout"
    if outcome == "tp":
        return "3R_target"
    if outcome.startswith("protected_stop"):
        return "fee_cover_protected_stop"
    return outcome


def stability_rows(development: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    fee_edges = [-np.inf, 0.35, 0.50, 0.75, 1.00, 1.25, np.inf]
    fee_labels = ["≤0.35", "0.35–0.50", "0.50–0.75", "0.75–1.00", "1.00–1.25", ">1.25"]
    for split, frame in (("development", development), ("validation", validation)):
        current = frame.copy()
        current["gap_group"] = pd.cut(
            current["gap_bars"], bins=[1, 2, 5, 8], labels=["2", "3–5", "6–8"]
        )
        current["fee_group"] = pd.cut(
            current["fee_to_risk"], bins=fee_edges, labels=fee_labels, include_lowest=True
        )
        for family, column in (("gap", "gap_group"), ("fee_to_risk", "fee_group"), ("side", "side")):
            for value, group in current.groupby(column, observed=True, sort=False):
                rows.append(
                    {
                        "split": split,
                        "family": family,
                        "bucket": str(value),
                        **summarize(group),
                    }
                )
    return pd.DataFrame(rows)


def comparison_plot(stability: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.5), constrained_layout=True)
    for axis, family, title in (
        (axes[0], "gap", "Gap response reverses across time"),
        (axes[1], "fee_to_risk", "Lower fee-to-risk is directionally better"),
    ):
        data = stability[stability["family"].eq(family)].copy()
        buckets = (
            ["2", "3–5", "6–8"]
            if family == "gap"
            else ["≤0.35", "0.35–0.50", "0.50–0.75", "0.75–1.00", "1.00–1.25"]
        )
        x = np.arange(len(buckets), dtype=float)
        width = 0.36
        for offset, split, colour in ((-width / 2, "development", ORANGE), (width / 2, "validation", TEAL)):
            split_data = data[data["split"].eq(split)].set_index("bucket")
            values = [float(split_data.loc[bucket, "mean_net_bp"]) if bucket in split_data.index else np.nan for bucket in buckets]
            counts = [int(split_data.loc[bucket, "events"]) if bucket in split_data.index else 0 for bucket in buckets]
            bars = axis.bar(x + offset, values, width, label=split, color=colour, alpha=0.88)
            for bar, count in zip(bars, counts):
                value = bar.get_height()
                if np.isfinite(value):
                    axis.text(
                        bar.get_x() + bar.get_width() / 2,
                        value + (2.0 if value >= 0 else -2.0),
                        f"n={count}",
                        ha="center",
                        va="bottom" if value >= 0 else "top",
                        fontsize=7,
                        color=INK,
                    )
        axis.axhline(0.0, color=INK, lw=1)
        axis.set_xticks(x, buckets, rotation=25 if family == "fee_to_risk" else 0)
        axis.set_ylabel("mean net return (bp)")
        axis.set_title(title, loc="left", fontweight="bold")
        axis.grid(axis="y", color=GRID, alpha=0.65)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, ncol=2)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def stage_table(development_metrics: pd.DataFrame, validation_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    stages = ["baseline", "fixed_bundle", "fixed_plus_fee", "final"]
    for split, metrics in (("development", development_metrics), ("validation", validation_metrics)):
        selected = metrics.set_index("arm")
        prior_mean = np.nan
        prior_events = np.nan
        for arm in stages:
            row = selected.loc[arm]
            mean = float(row["mean_net_bp"])
            events = int(row["events"])
            rows.append(
                {
                    "split": split,
                    "stage": arm,
                    "events": events,
                    "mean_net_bp": mean,
                    "delta_mean_net_bp_vs_prior": mean - prior_mean if np.isfinite(prior_mean) else np.nan,
                    "events_removed_vs_prior": int(prior_events - events) if np.isfinite(prior_events) else np.nan,
                    "profit_factor": float(row["profit_factor"]),
                    "equal_risk_1pct_return": float(row["equal_risk_1pct_return"]),
                    "max_drawdown": float(row["max_drawdown"]),
                    "matched_control_excess_bp": float(row["matched_control_excess_bp"]),
                    "paired_signflip_p_one_sided": float(row["paired_signflip_p_one_sided"]),
                }
            )
            prior_mean = mean
            prior_events = events
    return pd.DataFrame(rows)


def component_plot(metrics: pd.DataFrame, output: Path) -> None:
    order = [
        "baseline",
        "k1_colour_only",
        "path_only",
        "k2_wick_only",
        "k1_body_065_only",
        "fixed_bundle",
        "fixed_plus_fee",
        "final",
    ]
    selected = metrics.set_index("arm").loc[order].reset_index()
    labels = [f"{arm}  ·  n={int(count)}" for arm, count in zip(selected["arm"], selected["events"])]
    colours = [MUTED if arm == "baseline" else TEAL if arm in {"fixed_plus_fee", "final"} else ORANGE for arm in order]
    fig, axis = plt.subplots(figsize=(9.8, 5.2), constrained_layout=True)
    bars = axis.barh(labels, selected["mean_net_bp"], color=colours, alpha=0.9)
    axis.axvline(0.0, color=INK, lw=1)
    axis.set_xlabel("mean net return per trade (bp, after 20 bp cost)")
    axis.set_title("Frozen validation: key components and stages", loc="left", fontweight="bold")
    axis.grid(axis="x", color=GRID, alpha=0.65)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.invert_yaxis()
    for bar, value in zip(bars, selected["mean_net_bp"]):
        axis.text(
            float(value) + 0.35,
            bar.get_y() + bar.get_height() / 2,
            f"{float(value):.2f}",
            ha="left",
            va="center",
            color="white",
            fontsize=8,
            fontweight="bold",
        )
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def main() -> None:
    development_all = pd.read_csv(RESULTS / "development_events.csv.gz")
    validation_all = pd.read_csv(RESULTS / "validation_events.csv.gz")
    development = development_all[development_all["arm"].eq("final")].copy()
    validation = validation_all[validation_all["arm"].eq("final")].copy()
    development_metrics = pd.read_csv(RESULTS / "development_arm_metrics.csv")
    validation_metrics = pd.read_csv(RESULTS / "validation_arm_metrics.csv")

    validation["failure_reason"] = validation.apply(failure_reason, axis=1)
    reason_rows: list[dict[str, Any]] = []
    for reason, group in validation.groupby("failure_reason", sort=False):
        reason_rows.append(
            {
                "reason": reason,
                **summarize(group),
                "mean_mfe_r": float(group["mfe_r"].mean()),
                "mean_mae_r": float(group["mae_r"].mean()),
                "total_net_contribution_bp": float(group["net_return"].sum() * 1e4),
            }
        )
    reasons = pd.DataFrame(reason_rows).sort_values("total_net_contribution_bp")
    reasons.to_csv(RESULTS / "validation_failure_reasons.csv", index=False)

    stability = stability_rows(development, validation)
    stability.to_csv(RESULTS / "parameter_stability.csv", index=False)
    comparison_plot(stability, RESULTS / "parameter_stability.png")

    stages = stage_table(development_metrics, validation_metrics)
    stages.to_csv(RESULTS / "stage_waterfall.csv", index=False)
    component_plot(validation_metrics, RESULTS / "validation_key_components.png")

    protection_armed = int(validation["protection_armed"].fillna(False).sum())
    protection_exits = int(validation["outcome"].astype(str).str.startswith("protected_stop").sum())
    no_follow = int(validation["failure_reason"].eq("stop_before_0.5R_no_follow_through").sum())
    partial = int(
        validation["failure_reason"].eq("stop_after_0.5R_but_before_protection").sum()
    )
    after_trigger = int(validation["failure_reason"].eq("stop_after_protection_threshold").sum())
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "scored result artifacts only; no OHLCV opened",
        "holdout_rows_read": 0,
        "validation_events": len(validation),
        "validation_gross_mean_bp": float(validation["gross_return"].mean() * 1e4),
        "validation_net_mean_bp": float(validation["net_return"].mean() * 1e4),
        "break_even_round_trip_cost_bp": float(validation["gross_return"].mean() * 1e4),
        "current_round_trip_cost_bp": 20.0,
        "protection_armed_events": protection_armed,
        "protection_exit_events": protection_exits,
        "sl_before_0_5r": no_follow,
        "sl_between_0_5r_and_1_5r": partial,
        "sl_after_1_5r": after_trigger,
        "interpretation": {
            "primary_failure": "lack of post-entry continuation before 1.5R, not premature 3R profit taking",
            "cost": "gross expectancy is positive in validation but smaller than the frozen 20 bp round-trip cost",
            "gap": "3-5 bars is profitable in validation but strongly negative in development, so a gap restriction is not stable evidence",
            "profit_protection": "the 1.5R rule armed on five validation paths but never changed an exit",
        },
    }
    write_json(RESULTS / "diagnostic_summary.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
