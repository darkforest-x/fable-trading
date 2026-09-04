#!/usr/bin/env python3
"""Build the BTCUSDT.P 15m multifactor-confluence research report artifacts.

Inputs are committed feature and score ledgers produced by
``research_btcusdtp_15m_multifactor_confluence``.  All entry diagnostics use
features available on the completed signal bar or earlier.  Forward outcome
columns are used only to evaluate the frozen runner.  The repository holdout
beginning 2026-05-04 is neither opened nor scored.

The additional q95/q97.5/q99 density curves and conditional slices are
explicitly post-hoc diagnostics.  They may nominate a future hypothesis but
must not be interpreted as a newly validated threshold or production rule.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_multifactor_confluence import (
    score_from_contract,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-multifactor-confluence-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
RESULTS = EXPERIMENT / "results"
OUTPUT = ROOT / "analysis" / "output" / "btcusdtp_15m_multifactor_confluence_20260904"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
QUANTILES = (0.90, 0.95, 0.975, 0.99)
VARIANTS = ("signal_score", "legacy_28", "legacy_plus_participation")
PERIOD_FILES = {
    "2024 replay": "confirmation_feature_ledger.csv.gz",
    "2025-2026P1 audit": "audit_feature_ledger.csv.gz",
}
COLORS = {
    "teal": "#159A91",
    "orange": "#F59E0B",
    "red": "#E85D75",
    "ink": "#26323A",
    "gray": "#9AA5AD",
    "blue": "#4378BF",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_csv(frame: pd.DataFrame, name: str) -> Path:
    path = OUTPUT / name
    compression: str | dict[str, Any] | None = None
    if path.suffix == ".gz":
        compression = {"method": "gzip", "mtime": 0}
    frame.to_csv(path, index=False, compression=compression)
    return path


def profit_factor(values: pd.Series) -> float:
    positive = float(values.loc[values.gt(0.0)].sum())
    negative = float(-values.loc[values.lt(0.0)].sum())
    return positive / negative if negative > 0.0 else np.inf


def return_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    net = pd.to_numeric(frame["net_return"], errors="coerce").dropna()
    return {
        "events": len(net),
        "mean_net_bp": float(net.mean() * 1e4) if len(net) else np.nan,
        "median_net_bp": float(net.median() * 1e4) if len(net) else np.nan,
        "win_rate": float(net.gt(0.0).mean()) if len(net) else np.nan,
        "profit_factor": profit_factor(net) if len(net) else np.nan,
        "sum_net_bp": float(net.sum() * 1e4) if len(net) else np.nan,
    }


def load_contracts() -> dict[str, Mapping[str, Any]]:
    payload = json.loads((RESULTS / "model_contract.json").read_text(encoding="utf-8"))
    return payload["contracts"]


def density_quality_curve(
    development: pd.DataFrame,
    periods: Mapping[str, pd.DataFrame],
    contracts: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    """Apply thresholds learned only from 2023 full-fit score distributions."""

    rows: list[dict[str, Any]] = []
    for variant_id in VARIANTS:
        contract = contracts[variant_id]
        train_scores = score_from_contract(development, contract)
        period_scores = {
            period: score_from_contract(frame, contract)
            for period, frame in periods.items()
        }
        for quantile in QUANTILES:
            threshold = float(np.nanquantile(train_scores, quantile))
            if quantile == 0.90:
                expected = float(contract["score_threshold"])
                if not np.isclose(threshold, expected, rtol=0.0, atol=1e-12):
                    raise RuntimeError(f"q90 threshold drift for {variant_id}")
            for period, frame in periods.items():
                selected = frame.loc[period_scores[period] >= threshold]
                rows.append(
                    {
                        "period": period,
                        "variant_id": variant_id,
                        "training_score_quantile": quantile,
                        "threshold": threshold,
                        "pool_events": len(frame),
                        "selection_rate": len(selected) / len(frame),
                        **return_metrics(selected),
                        "interpretation": (
                            "registered" if quantile == 0.90 else "posthoc sensitivity only"
                        ),
                    }
                )
    return pd.DataFrame(rows)


def development_selected() -> pd.DataFrame:
    ledger = pd.read_csv(RESULTS / "development_feature_ledger.csv.gz")
    scores = pd.read_csv(RESULTS / "development_oof_scores.csv.gz")
    selected = scores.loc[
        scores["variant_id"].eq("legacy_plus_participation")
        & scores["selected"].astype(bool),
        ["setup_id", "fold"],
    ]
    return selected.merge(ledger, on="setup_id", validate="one_to_one")


def conditional_stability(periods: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Describe fixed entry-time gates; this is not a model-selection table."""

    gates: dict[str, tuple[str, Any]] = {
        "LONG": ("direction", 1),
        "SHORT": ("direction", -1),
        "K1 found": ("k1_found", 1.0),
        "K1 absent": ("k1_found", 0.0),
        "1h aligned": ("vote_htf_aligned", 1.0),
        "1h not aligned": ("vote_htf_aligned", 0.0),
        "trend constructive": ("vote_trend_constructive", 1.0),
        "trend not constructive": ("vote_trend_constructive", 0.0),
        "volume confirmed": ("vote_participation_confirmed", 1.0),
        "volume not confirmed": ("vote_participation_confirmed", 0.0),
        "ETH aligned": ("vote_eth_aligned", 1.0),
        "ETH not aligned": ("vote_eth_aligned", 0.0),
    }
    rows: list[dict[str, Any]] = []
    for period, frame in periods.items():
        for gate, (column, value) in gates.items():
            subset = frame.loc[frame[column].eq(value)]
            rows.append(
                {
                    "period": period,
                    "gate": gate,
                    "column": column,
                    "value": value,
                    **return_metrics(subset),
                }
            )
        for family, subset in frame.groupby("signal_family", sort=True):
            rows.append(
                {
                    "period": period,
                    "gate": f"family={family}",
                    "column": "signal_family",
                    "value": family,
                    **return_metrics(subset),
                }
            )
    return pd.DataFrame(rows)


def q99_halfyear_stability(
    development: pd.DataFrame,
    periods: Mapping[str, pd.DataFrame],
    contracts: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    """Stress-test the superficially positive, post-hoc 99th-percentile tail."""

    rows: list[dict[str, Any]] = []
    for variant_id in VARIANTS:
        contract = contracts[variant_id]
        threshold = float(
            np.nanquantile(score_from_contract(development, contract), 0.99)
        )
        for period, frame in periods.items():
            scores = score_from_contract(frame, contract)
            selected = frame.loc[scores >= threshold].copy()
            times = pd.to_datetime(selected["entry_time"], utc=True)
            selected["halfyear"] = times.map(
                lambda stamp: f"{stamp.year}H{1 if stamp.month <= 6 else 2}"
            )
            for halfyear, group in selected.groupby("halfyear", sort=True):
                rows.append(
                    {
                        "period": period,
                        "halfyear": halfyear,
                        "variant_id": variant_id,
                        "threshold": threshold,
                        **return_metrics(group),
                    }
                )
    return pd.DataFrame(rows)


def corrected_failure_mechanics(trades: pd.DataFrame) -> pd.DataFrame:
    """Classify outcomes against the runner's actual close-confirmed arm state.

    ``mfe_at_exit_atr >= 2`` is not equivalent to ``runner_armed`` because an
    intrabar high/low may touch +2 ATR without the completed bar closing there.
    Exit giveback is also measured only through the exit bar; the longer
    horizon maximum is retained separately as a post-exit opportunity gap.
    """

    out = trades.copy()
    out["realized_atr"] = (
        out["gross_return"].astype(float)
        * out["entry_price"].astype(float)
        / out["signal_atr"].astype(float)
    )
    out["exit_giveback_atr"] = (
        out["mfe_at_exit_atr"].astype(float) - out["realized_atr"]
    )
    out["horizon_opportunity_gap_atr"] = (
        out["horizon_mfe_atr"].astype(float) - out["realized_atr"]
    )
    armed = out["runner_armed"].astype(bool)
    net = out["net_return"].astype(float)
    gross = out["gross_return"].astype(float)
    hard_stop = out["outcome"].astype(str).str.contains("hard_stop")
    early_stop = hard_stop & out["mfe_at_exit_atr"].lt(0.5)
    later_recovery = out["horizon_mfe_atr"].ge(2.0)
    out["diagnostic_category"] = np.select(
        [
            net.gt(0.0) & ~armed,
            net.gt(0.0) & armed & out["exit_giveback_atr"].ge(2.0),
            net.gt(0.0) & armed,
            net.le(0.0) & gross.gt(0.0),
            net.le(0.0) & ~armed & early_stop & later_recovery,
            net.le(0.0) & ~armed & early_stop & ~later_recovery,
            net.le(0.0) & armed,
            net.le(0.0) & ~armed & out["outcome"].eq("timeout"),
            net.le(0.0) & ~armed,
        ],
        [
            "winner_unarmed",
            "winner_large_exit_giveback",
            "winner_retained",
            "gross_win_erased_by_cost",
            "early_stop_then_later_recovered",
            "false_launch_early_stop",
            "armed_then_loss",
            "timeout_negative",
            "failed_before_arm_other",
        ],
        default="unknown",
    )
    if out["diagnostic_category"].eq("unknown").any():
        raise RuntimeError("failure classifier left unknown rows")
    return out


def failure_contribution(failures: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for category, group in failures.groupby("diagnostic_category", sort=False):
        rows.append(
            {
                "diagnostic_category": category,
                **return_metrics(group),
                "runner_armed_rate": float(group["runner_armed"].astype(bool).mean()),
                "mean_mfe_at_exit_atr": float(group["mfe_at_exit_atr"].mean()),
                "mean_horizon_mfe_atr": float(group["horizon_mfe_atr"].mean()),
                "mean_exit_giveback_atr": float(group["exit_giveback_atr"].mean()),
                "mean_horizon_opportunity_gap_atr": float(
                    group["horizon_opportunity_gap_atr"].mean()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("sum_net_bp").reset_index(drop=True)


def setup_plot() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#CDD3D8",
            "axes.labelcolor": COLORS["ink"],
            "axes.titlecolor": COLORS["ink"],
            "xtick.color": "#56616A",
            "ytick.color": "#56616A",
            "font.size": 10,
            "axes.grid": True,
            "grid.alpha": 0.18,
            "grid.color": "#7F8C93",
            "savefig.bbox": "tight",
            "savefig.dpi": 180,
        }
    )


def plot_development(summary: pd.DataFrame) -> None:
    ordered = summary.sort_values("mean_net_bp")
    colors = [
        COLORS["teal"]
        if value == "legacy_plus_participation"
        else COLORS["orange"]
        if value == "signal_score"
        else COLORS["gray"]
        for value in ordered["variant_id"]
    ]
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(ordered["variant_id"], ordered["mean_net_bp"], color=colors)
    ax.axvline(0.0, color=COLORS["ink"], linewidth=1.0)
    ax.set_title("2023 expanding-OOF: every registered factor bundle remained negative")
    ax.set_xlabel("selected-trade mean net return (bp, after 20 bp cost)")
    ax.set_ylabel("")
    for index, value in enumerate(ordered["mean_net_bp"]):
        ax.text(value - 0.4, index, f"{value:.1f}", ha="right", va="center", color="white")
    fig.tight_layout()
    fig.savefig(OUTPUT / "development_factor_bundles.png")
    plt.close(fig)


def plot_period_comparison(confirmation: pd.DataFrame, audit: pd.DataFrame) -> None:
    joined = pd.concat(
        [
            confirmation.assign(period="2024 replay"),
            audit.assign(period="2025-2026P1 audit"),
        ],
        ignore_index=True,
    )
    variants = joined["variant_id"].drop_duplicates().tolist()
    x = np.arange(len(variants))
    width = 0.36
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for offset, (period, color) in enumerate(
        [("2024 replay", COLORS["blue"]), ("2025-2026P1 audit", COLORS["teal"])]
    ):
        part = joined.loc[joined["period"].eq(period)].set_index("variant_id").reindex(variants)
        values = part["mean_net_bp"].to_numpy(dtype=float)
        positions = x + (offset - 0.5) * width
        ax.bar(positions, values, width, label=period, color=color)
        for xpos, value in zip(positions, values):
            ax.text(xpos, value - 1.0, f"{value:.1f}", ha="center", va="top", color="white", fontsize=9)
    ax.axhline(0.0, color=COLORS["ink"], linewidth=1.0)
    ax.set_xticks(x, variants, rotation=18, ha="right")
    ax.set_ylabel("mean net return (bp)")
    ax.set_title("Frozen 2023 scorers: no comparison cleared costs in both later periods")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUTPUT / "frozen_period_comparison.png")
    plt.close(fig)


def plot_density_quality(curve: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    styles = {
        "signal_score": (COLORS["orange"], "o"),
        "legacy_28": (COLORS["gray"], "s"),
        "legacy_plus_participation": (COLORS["teal"], "D"),
    }
    for ax, period in zip(axes, PERIOD_FILES):
        part = curve.loc[curve["period"].eq(period)]
        for variant_id, (color, marker) in styles.items():
            group = part.loc[part["variant_id"].eq(variant_id)].sort_values(
                "training_score_quantile"
            )
            ax.plot(
                group["selection_rate"] * 100.0,
                group["mean_net_bp"],
                marker=marker,
                color=color,
                linewidth=2,
                label=variant_id,
            )
            for _, row in group.iterrows():
                ax.annotate(
                    f"q{row['training_score_quantile'] * 100:g}",
                    (row["selection_rate"] * 100.0, row["mean_net_bp"]),
                    xytext=(3, 4),
                    textcoords="offset points",
                    fontsize=8,
                )
        ax.axhline(0.0, color=COLORS["ink"], linewidth=1.0)
        ax.set_title(period)
        ax.set_xlabel("selected share of candidate pool (%)")
    axes[0].set_ylabel("mean net return (bp)")
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Post-hoc density sensitivity: sparse positive cells are not time-stable")
    fig.tight_layout()
    fig.savefig(OUTPUT / "density_quality_sensitivity.png")
    plt.close(fig)


def plot_failure_contribution(contribution: pd.DataFrame) -> None:
    labels = {
        "false_launch_early_stop": "false launch / early stop",
        "early_stop_then_later_recovered": "early stop, later recovery",
        "failed_before_arm_other": "other failure before arm",
        "armed_then_loss": "armed then loss",
        "timeout_negative": "negative timeout",
        "gross_win_erased_by_cost": "gross win erased by cost",
        "winner_large_exit_giveback": "winner with large exit giveback",
        "winner_retained": "winner retained",
        "winner_unarmed": "winner before arm",
    }
    ordered = contribution.sort_values("sum_net_bp")
    colors = [COLORS["red"] if value < 0.0 else COLORS["teal"] for value in ordered["sum_net_bp"]]
    fig, ax = plt.subplots(figsize=(10, 5.8))
    ax.barh(
        [labels.get(value, value) for value in ordered["diagnostic_category"]],
        ordered["sum_net_bp"],
        color=colors,
    )
    ax.axvline(0.0, color=COLORS["ink"], linewidth=1.0)
    ax.set_xlabel("sum of per-trade net returns (bp; non-compounded diagnostic)")
    ax.set_title("2025-2026P1: pre-arm failures dominate the loss ledger")
    for index, row in enumerate(ordered.to_dict("records")):
        value = float(row["sum_net_bp"])
        ax.text(
            value + (-180 if value >= 0 else 180),
            index,
            f"n={int(row['events'])}",
            ha="right" if value >= 0 else "left",
            va="center",
            fontsize=9,
            color="white",
        )
    fig.tight_layout()
    fig.savefig(OUTPUT / "audit_failure_contribution.png")
    plt.close(fig)


def plot_resonance_stability(stability: pd.DataFrame) -> None:
    gates = [
        "LONG",
        "SHORT",
        "K1 found",
        "1h aligned",
        "trend constructive",
        "volume confirmed",
        "ETH aligned",
    ]
    periods = ["2023 OOF", "2024 replay", "2025-2026P1 audit"]
    matrix = (
        stability.loc[stability["gate"].isin(gates)]
        .pivot(index="gate", columns="period", values="mean_net_bp")
        .reindex(index=gates, columns=periods)
    )
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    image = ax.imshow(matrix.to_numpy(dtype=float), cmap="RdYlGn", vmin=-45.0, vmax=45.0, aspect="auto")
    ax.set_xticks(range(len(periods)), periods)
    ax.set_yticks(range(len(gates)), gates)
    ax.set_title("Conditional resonance is not stable across time")
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix.iat[row, column]
            ax.text(column, row, f"{value:.1f}", ha="center", va="center", color=COLORS["ink"])
    colorbar = fig.colorbar(image, ax=ax, shrink=0.82)
    colorbar.set_label("mean net return (bp)")
    fig.tight_layout()
    fig.savefig(OUTPUT / "resonance_time_stability.png")
    plt.close(fig)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    setup_plot()
    development = pd.read_csv(RESULTS / "development_feature_ledger.csv.gz")
    raw_periods = {
        period: pd.read_csv(RESULTS / filename)
        for period, filename in PERIOD_FILES.items()
    }
    latest = max(
        pd.to_datetime(frame["entry_time"], utc=True).max() for frame in raw_periods.values()
    )
    if latest >= HOLDOUT_START:
        raise RuntimeError(f"holdout leakage: latest entry {latest}")
    contracts = load_contracts()
    curve = density_quality_curve(development, raw_periods, contracts)
    selected_periods = {
        "2023 OOF": development_selected(),
        "2024 replay": pd.read_csv(RESULTS / "confirmation_selected_trades.csv.gz"),
        "2025-2026P1 audit": pd.read_csv(RESULTS / "audit_selected_trades.csv.gz"),
    }
    stability = conditional_stability(selected_periods)
    q99 = q99_halfyear_stability(development, raw_periods, contracts)
    failures = corrected_failure_mechanics(
        pd.read_csv(RESULTS / "audit_selected_trades.csv.gz")
    )
    contribution = failure_contribution(failures)
    development_summary = pd.read_csv(RESULTS / "development_variant_summary.csv")
    confirmation = pd.read_csv(RESULTS / "confirmation_variant_comparison.csv")
    audit = pd.read_csv(RESULTS / "audit_variant_comparison.csv")

    paths = {
        "density_quality": write_csv(curve, "posthoc_density_quality_curve.csv"),
        "conditional_stability": write_csv(stability, "conditional_resonance_stability.csv"),
        "q99_halfyear": write_csv(q99, "posthoc_q99_halfyear_stability.csv"),
        "failure_contribution": write_csv(contribution, "audit_failure_contribution.csv"),
        "corrected_failure_ledger": write_csv(
            failures, "corrected_failure_mechanics.csv.gz"
        ),
    }
    plot_development(development_summary)
    plot_period_comparison(confirmation, audit)
    plot_density_quality(curve)
    plot_failure_contribution(contribution)
    plot_resonance_stability(stability)

    q99_candidate = q99.loc[q99["variant_id"].eq("legacy_plus_participation")]
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "status": "rejected",
        "reason": "No registered factor bundle had a positive 2023 OOF fold; the nominated bundle failed every economic and statistical gate on 2024 and remained negative in every later audit slice.",
        "holdout_rows_read": 0,
        "holdout_start": HOLDOUT_START,
        "latest_entry_time": latest,
        "production_eligible": False,
        "shap_run": False,
        "shap_reason": "The preregistered contract permits SHAP only after positive 2024 mean net return and score p<0.01; neither gate passed.",
        "registered": {
            "candidate_variants": len(development_summary),
            "feature_count": 104,
            "nominated_variant": "legacy_plus_participation",
            "development": development_summary.iloc[0].to_dict(),
            "confirmation": confirmation.loc[
                confirmation["variant_id"].eq("legacy_plus_participation")
            ].iloc[0].to_dict(),
            "audit": audit.loc[
                audit["variant_id"].eq("legacy_plus_participation")
            ].iloc[0].to_dict(),
        },
        "posthoc_q99_candidate_halfyears": q99_candidate.to_dict("records"),
        "failure_contribution": contribution.to_dict("records"),
        "source_hashes": {
            "config": sha256(EXPERIMENT / "config.json"),
            "selection_receipt": sha256(RESULTS / "selection_receipt.json"),
            "confirmation_receipt": sha256(RESULTS / "confirmation_receipt.json"),
            "audit_receipt": sha256(RESULTS / "audit_receipt.json"),
            "model_contract": sha256(RESULTS / "model_contract.json"),
        },
        "artifact_hashes": {key: sha256(path) for key, path in paths.items()},
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(json_value(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
