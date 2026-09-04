#!/usr/bin/env python3
"""Build the ETH/XAU 15m asset-specific K1->K2 diagnosis artifacts.

Inputs are the frozen selection, audit, and confirmation receipts/trade ledgers
from the two registered experiments.  Signal-time fields use only completed K2
and earlier data.  Fixed-horizon and fixed-barrier calculations below are
explicit postmortem outcome diagnostics; they never feed signal selection.

The price loader is physically bounded at 2026-05-01, before the repository
holdout beginning 2026-05-04.  Output tables distinguish registered results
from postmortem stress tests so the latter cannot be mistaken for validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_asset_specific_k1k2_15m import (
    ROOT,
    load_bounded_frame,
    load_config,
    utc,
)
from scripts.research_btcusdtp_15m_dual_ma_runner import _stop_fill
from scripts.research_btcusdtp_15m_ma_state_trend import write_csv, write_json

OUTPUT_DIR = ROOT / "analysis/output/asset_specific_k1k2_15m_20260905"
SAFE_END = utc("2026-05-01T00:00:00Z")
ASSETS = {
    "ETH": ROOT
    / "experiments/active/exp-ethusdtp-15m-asset-specific-k1k2-preholdout-20260905-v19",
    "XAU": ROOT
    / "experiments/active/exp-xauusdtp-15m-asset-specific-k1k2-preholdout-20260905-v1",
}
PHASES = ("selection", "audit", "confirmation")


def _trade_path(experiment: Path, phase: str) -> Path:
    suffix = "final_trades" if phase == "selection" else "candidate_trades"
    return experiment / "results" / f"{phase}_{suffix}.csv.gz"


def _setup_path(experiment: Path, phase: str) -> Path:
    suffix = "final_setups" if phase == "selection" else "candidate_setups"
    return experiment / "results" / f"{phase}_{suffix}.csv.gz"


def _receipt_path(experiment: Path, phase: str) -> Path:
    return experiment / "results" / f"{phase}_receipt.json"


def _profit_factor(net_returns: pd.Series) -> float:
    positive = float(net_returns.clip(lower=0.0).sum())
    negative = float(-net_returns.clip(upper=0.0).sum())
    return positive / negative if negative > 0.0 else np.inf


def _metrics(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {
            "events": 0,
            "mean_net_bp": np.nan,
            "profit_factor": np.nan,
            "win_rate": np.nan,
        }
    return {
        "events": len(rows),
        "mean_net_bp": float(rows["net_return"].mean() * 1e4),
        "profit_factor": float(_profit_factor(rows["net_return"])),
        "win_rate": float(rows["net_return"].gt(0.0).mean()),
    }


def build_phase_summary() -> pd.DataFrame:
    """Collect frozen registered metrics without recalculating selection."""

    rows: list[dict[str, Any]] = []
    for asset, experiment in ASSETS.items():
        for phase in PHASES:
            receipt = json.loads(_receipt_path(experiment, phase).read_text())
            for policy in ("baseline", "candidate"):
                summary = receipt[policy]
                rows.append(
                    {
                        "asset": asset,
                        "phase": phase,
                        "policy": policy,
                        "events": int(summary["events"]),
                        "mean_gross_bp": float(summary["mean_gross_bp"]),
                        "mean_net_bp": float(summary["mean_net_bp"]),
                        "profit_factor": float(summary["profit_factor"]),
                        "win_rate": float(summary["win_rate"]),
                        "positive_folds": int(summary["positive_folds"]),
                        "total_folds": int(summary["total_folds"]),
                        "p95_net_bp": float(summary["p95_net_bp"]),
                        "runner_armed_share": float(summary["runner_armed_share"]),
                        "matched_random_mean_net_bp": (
                            float(receipt["matched_random"]["control_mean_net_bp"])
                            if policy == "candidate" and "matched_random" in receipt
                            else np.nan
                        ),
                        "matched_excess_bp": (
                            float(receipt["matched_random"]["excess_bp"])
                            if policy == "candidate" and "matched_random" in receipt
                            else np.nan
                        ),
                        "matched_signflip_p": (
                            float(receipt["matched_random"]["signflip_p"])
                            if policy == "candidate" and "matched_random" in receipt
                            else np.nan
                        ),
                        "registered_result": True,
                    }
                )
    return pd.DataFrame(rows)


def build_trade_diagnostics() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize direction and failure mechanics in the frozen trade ledgers."""

    directions: list[dict[str, Any]] = []
    mechanics: list[dict[str, Any]] = []
    for asset, experiment in ASSETS.items():
        for phase in PHASES:
            trades = pd.read_csv(_trade_path(experiment, phase))
            trades["fee_to_risk"] = 0.002 / trades["risk_fraction"]
            for direction, selected in trades.groupby("direction", sort=True):
                directions.append(
                    {
                        "asset": asset,
                        "phase": phase,
                        "direction": "LONG" if int(direction) > 0 else "SHORT",
                        **_metrics(selected),
                        "runner_armed_share": float(selected["runner_armed"].mean()),
                        "hard_stop_share": float(selected["outcome"].eq("hard_stop").mean()),
                        "median_regime_age_bars": float(selected["regime_age_bars"].median()),
                        "median_fee_to_risk": float(selected["fee_to_risk"].median()),
                    }
                )
            mechanics.append(
                {
                    "asset": asset,
                    "phase": phase,
                    "events": len(trades),
                    "runner_armed_share": float(trades["runner_armed"].mean()),
                    "hard_stop_share": float(trades["outcome"].eq("hard_stop").mean()),
                    "timeout_share": float(trades["outcome"].eq("timeout").mean()),
                    "median_regime_age_bars": float(trades["regime_age_bars"].median()),
                    "median_k1_k2_gap_bars": float(trades["k1_gap"].median()),
                    "median_fee_to_risk": float(trades["fee_to_risk"].median()),
                    "median_mfe_at_exit_atr": float(trades["mfe_at_exit_atr"].median()),
                    "median_horizon_mfe_atr": float(trades["horizon_mfe_atr"].median()),
                    "median_giveback_atr": float(trades["gave_back_atr"].median()),
                }
            )
    return pd.DataFrame(directions), pd.DataFrame(mechanics)


def build_postmortem_gate_stress() -> pd.DataFrame:
    """Test causal scalar gates on already-frozen ledgers for diagnosis only."""

    specifications = {
        "fee_to_risk_max": ("fee_to_risk", "le", [0.20, 0.35, 0.50, 0.80]),
        "regime_age_max": ("regime_age_bars", "le", [24, 48, 96]),
        "efficiency24_min": ("efficiency24", "ge", [0.10, 0.15, 0.20]),
        "ma_side_flips24_max": ("ma_side_flips_24", "le", [2, 4, 6]),
    }
    rows: list[dict[str, Any]] = []
    for asset, experiment in ASSETS.items():
        for phase in PHASES:
            trades = pd.read_csv(_trade_path(experiment, phase))
            trades["fee_to_risk"] = 0.002 / trades["risk_fraction"]
            for gate, (column, operator, values) in specifications.items():
                for value in values:
                    if operator == "le":
                        selected = trades[trades[column].le(value)]
                    else:
                        selected = trades[trades[column].ge(value)]
                    rows.append(
                        {
                            "asset": asset,
                            "phase": phase,
                            "gate": gate,
                            "threshold": value,
                            **_metrics(selected),
                            "acceptance_rate": float(len(selected) / len(trades)),
                            "registered_result": False,
                            "evidence_role": "postmortem_sensitivity_not_validation",
                        }
                    )
    return pd.DataFrame(rows)


def build_fixed_horizon_returns() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Calculate no-stop directional returns after each frozen next-open entry."""

    horizons = (1, 2, 4, 8, 16, 32, 64, 96)
    rows: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for asset, experiment in ASSETS.items():
        config = load_config(experiment / "config.json")
        frame, quality = load_bounded_frame(config, end_exclusive=SAFE_END)
        sources[asset] = quality
        for phase in PHASES:
            trades = pd.read_csv(_trade_path(experiment, phase))
            for horizon in horizons:
                values: list[float] = []
                for trade in trades.to_dict("records"):
                    entry_i = int(trade["entry_i"])
                    exit_i = entry_i + horizon - 1
                    if exit_i >= len(frame):
                        continue
                    if int(frame.loc[exit_i, "segment_id"]) != int(
                        frame.loc[entry_i, "segment_id"]
                    ):
                        continue
                    gross = int(trade["direction"]) * (
                        float(frame.loc[exit_i, "close"]) / float(trade["entry_price"])
                        - 1.0
                    )
                    values.append(gross)
                series = pd.Series(values, dtype=float)
                rows.append(
                    {
                        "asset": asset,
                        "phase": phase,
                        "horizon_bars": horizon,
                        "events": len(series),
                        "mean_gross_bp": float(series.mean() * 1e4),
                        "median_gross_bp": float(series.median() * 1e4),
                        "positive_share": float(series.gt(0.0).mean()),
                        "registered_result": False,
                        "evidence_role": "postmortem_path_diagnostic_without_exit",
                    }
                )
    return pd.DataFrame(rows), sources


def _fixed_barrier_returns(
    frame: pd.DataFrame,
    setups: pd.DataFrame,
    *,
    stop_atr: float,
    target_atr: float,
    horizon_bars: int = 96,
    cost_fraction: float = 0.002,
) -> pd.Series:
    """Resolve fixed barriers with conservative same-bar stop-first ordering."""

    output: list[float] = []
    for setup in setups.to_dict("records"):
        entry_i = int(setup["entry_i"])
        direction = int(setup["direction"])
        entry = float(setup["entry_price"])
        atr = float(setup["signal_atr"])
        stop = entry - direction * stop_atr * atr
        target = entry + direction * target_atr * atr
        end_i = min(entry_i + horizon_bars - 1, len(frame) - 1)
        exit_price: float | None = None
        for index in range(entry_i, end_i + 1):
            open_price = float(frame.loc[index, "open"])
            high = float(frame.loc[index, "high"])
            low = float(frame.loc[index, "low"])
            stop_hit = low <= stop if direction > 0 else high >= stop
            target_hit = high >= target if direction > 0 else low <= target
            if stop_hit:
                exit_price = _stop_fill(open_price, stop, direction)
                break
            if target_hit:
                exit_price = target
                break
        if exit_price is None:
            exit_price = float(frame.loc[end_i, "close"])
        output.append(direction * (exit_price / entry - 1.0) - cost_fraction)
    return pd.Series(output, dtype=float)


def build_exit_sensitivity() -> pd.DataFrame:
    """Show whether wider fixed barriers rescue the frozen entries (they do not)."""

    rows: list[dict[str, Any]] = []
    for asset, experiment in ASSETS.items():
        config = load_config(experiment / "config.json")
        frame, _ = load_bounded_frame(config, end_exclusive=SAFE_END)
        for phase in PHASES:
            setups = pd.read_csv(_setup_path(experiment, phase))
            for stop_atr in (2.0, 4.0, 6.0):
                for target_atr in (2.0, 4.0, 6.0, 8.0, 12.0):
                    values = _fixed_barrier_returns(
                        frame,
                        setups,
                        stop_atr=stop_atr,
                        target_atr=target_atr,
                    )
                    rows.append(
                        {
                            "asset": asset,
                            "phase": phase,
                            "stop_atr": stop_atr,
                            "target_atr": target_atr,
                            "events": len(values),
                            "mean_net_bp": float(values.mean() * 1e4),
                            "profit_factor": float(_profit_factor(values)),
                            "win_rate": float(values.gt(0.0).mean()),
                            "registered_result": False,
                            "evidence_role": "postmortem_exit_sensitivity_not_selection",
                        }
                    )
    return pd.DataFrame(rows)


def render_figure(
    phase_summary: pd.DataFrame,
    mechanics: pd.DataFrame,
    horizons: pd.DataFrame,
) -> Path:
    """Render one compact four-panel diagnostic used by the Markdown report."""

    candidate = phase_summary[phase_summary["policy"].eq("candidate")].copy()
    phase_order = list(PHASES)
    x = np.arange(len(phase_order), dtype=float)
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.0), constrained_layout=True)
    colors = {"ETH": "#167d77", "XAU": "#d28b00"}

    ax = axes[0, 0]
    for offset, asset in ((-0.18, "ETH"), (0.18, "XAU")):
        data = candidate[candidate["asset"].eq(asset)].set_index("phase")
        values = [float(data.loc[phase, "mean_net_bp"]) for phase in phase_order]
        ax.bar(x + offset, values, width=0.34, label=asset, color=colors[asset])
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xticks(x, ["Selection", "Audit", "Confirmation"])
    ax.set_ylabel("Mean net return (bp/trade)")
    ax.set_title("Frozen profile returns fail to transport")
    ax.legend(frameon=False)

    ax = axes[0, 1]
    subset = candidate[candidate["phase"].isin(("audit", "confirmation"))]
    labels: list[str] = []
    values: list[float] = []
    bar_colors: list[str] = []
    for asset in ("ETH", "XAU"):
        for phase in ("audit", "confirmation"):
            row = subset[(subset["asset"].eq(asset)) & (subset["phase"].eq(phase))].iloc[0]
            labels.append(f"{asset}\n{phase[:4]}")
            values.append(float(row["matched_excess_bp"]))
            bar_colors.append(colors[asset])
    ax.bar(np.arange(len(values)), values, color=bar_colors)
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xticks(np.arange(len(values)), labels)
    ax.set_ylabel("Excess vs matched random (bp/trade)")
    ax.set_title("No significant matched-random edge")

    ax = axes[1, 0]
    confirmation = mechanics[mechanics["phase"].eq("confirmation")].set_index("asset")
    positions = np.arange(2, dtype=float)
    armed = [float(confirmation.loc[a, "runner_armed_share"]) for a in ("ETH", "XAU")]
    stopped = [float(confirmation.loc[a, "hard_stop_share"]) for a in ("ETH", "XAU")]
    ax.bar(positions - 0.18, armed, width=0.34, label="Runner armed", color="#167d77")
    ax.bar(positions + 0.18, stopped, width=0.34, label="Hard stop", color="#b54a54")
    ax.set_xticks(positions, ["ETH", "XAU"])
    ax.set_ylim(0.0, 1.0)
    ax.set_ylabel("Share of confirmation trades")
    ax.set_title("Most trades fail before trend management starts")
    ax.legend(frameon=False)

    ax = axes[1, 1]
    for asset in ("ETH", "XAU"):
        data = horizons[
            horizons["asset"].eq(asset) & horizons["phase"].eq("confirmation")
        ].sort_values("horizon_bars")
        ax.plot(
            data["horizon_bars"],
            data["mean_gross_bp"],
            marker="o",
            linewidth=1.8,
            label=asset,
            color=colors[asset],
        )
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8, 16, 32, 64, 96], [1, 2, 4, 8, 16, 32, 64, 96])
    ax.set_xlabel("Bars after next-open entry")
    ax.set_ylabel("Mean gross directional return (bp)")
    ax.set_title("Confirmation continuation decays rather than compounds")
    ax.legend(frameon=False)

    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.18, linewidth=0.6)
        axis.spines[["top", "right"]].set_visible(False)
    path = OUTPUT_DIR / "asset_specific_k1k2_diagnosis.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    phase_summary = build_phase_summary()
    directions, mechanics = build_trade_diagnostics()
    gate_stress = build_postmortem_gate_stress()
    horizons, sources = build_fixed_horizon_returns()
    exits = build_exit_sensitivity()

    write_csv(phase_summary, OUTPUT_DIR / "phase_summary.csv")
    write_csv(directions, OUTPUT_DIR / "direction_summary.csv")
    write_csv(mechanics, OUTPUT_DIR / "failure_mechanics.csv")
    write_csv(gate_stress, OUTPUT_DIR / "postmortem_gate_stress.csv")
    write_csv(horizons, OUTPUT_DIR / "fixed_horizon_returns.csv")
    write_csv(exits, OUTPUT_DIR / "exit_sensitivity.csv")
    figure = render_figure(phase_summary, mechanics, horizons)
    write_json(
        OUTPUT_DIR / "summary.json",
        {
            "status": "both_profiles_failed_registered_confirmation_gates",
            "registered_experiments": {
                asset: path.name for asset, path in ASSETS.items()
            },
            "registered_candidate_rows": len(
                phase_summary[phase_summary["policy"].eq("candidate")]
            ),
            "postmortem_gate_rows": len(gate_stress),
            "postmortem_exit_rows": len(exits),
            "figure": figure.relative_to(ROOT).as_posix(),
            "safe_end_exclusive": SAFE_END,
            "repository_holdout_rows_read": int(
                sum(int(source["holdout_rows_read"]) for source in sources.values())
            ),
            "sources": sources,
            "production_or_live_changed": False,
        },
    )


if __name__ == "__main__":
    main()
