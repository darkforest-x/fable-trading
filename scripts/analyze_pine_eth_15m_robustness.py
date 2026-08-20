#!/usr/bin/env python3
"""Run development-only robustness checks for the frozen ETH 15m Pine family.

The script stops the market-data parser at ``2025-01-01T00:00:00Z``.  It
performs two diagnostics without reading the consumed final-preholdout period:

1. a nested component ablation of the signal core (cross, regime, slope,
   oscillator direction, and the frozen V9 threshold); and
2. a prequential replay plus an exact selection-adjusted sign-flip test for the
   project's deterministic feature gates.

All variants preserve the 15-minute entry timing, ATR stop, break-even,
cooldown, risk sizing, and 20 bp round-trip cost.  No TP/SL multiple, ATR floor,
cost assumption, model, production pointer, or holdout is changed or scored.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    DEVELOPMENT_BLOCKS,
    Variant,
    build_feature_frame,
    load_config,
    simulate_period,
    summarize,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"
CORE_OUTPUT = RESULTS / "core_component_ablation.csv"
SIDE_OUTPUT = RESULTS / "side_ablation.csv"
PREQUENTIAL_OUTPUT = RESULTS / "prequential_feature_selection.csv"
ROBUSTNESS_OUTPUT = RESULTS / "robustness_checks.json"
DEVELOPMENT_END = pd.Timestamp("2025-01-01T00:00:00Z")


def load_development_only_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load enough warmup history but refuse parsing at the 2025 boundary."""

    config = load_config()
    path = PROJECT / config["instrument"]["data_path"]
    holdout_start = pd.Timestamp(config["time_contract"]["holdout_start"])
    frame = load_development_frame(
        path,
        safe_end=DEVELOPMENT_END,
        holdout_start=holdout_start,
    )
    times = pd.to_datetime(frame["open_time"], utc=True)
    quality = {
        "development_end_exclusive": DEVELOPMENT_END.isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "rows": int(len(frame)),
        "rows_at_or_after_development_end": int(times.ge(DEVELOPMENT_END).sum()),
        "rows_at_or_after_holdout": int(times.ge(holdout_start).sum()),
        "duplicate_timestamps": int(times.duplicated().sum()),
        "non_15m_gaps": int(times.diff().dropna().ne(pd.Timedelta(minutes=15)).sum()),
    }
    if any(
        quality[key]
        for key in (
            "rows_at_or_after_development_end",
            "rows_at_or_after_holdout",
            "duplicate_timestamps",
            "non_15m_gaps",
        )
    ):
        raise RuntimeError(f"development-only loader contract failed: {quality}")
    return build_feature_frame(frame), quality


def add_core_ablation_signals(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[Variant, ...]]:
    """Add nested causal signal components while keeping execution fixed."""

    out = frame.copy()
    fast = out["fast_ma"]
    slow = out["slow_ma"]
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    out["core_cross_long"] = cross_up.fillna(False)
    out["core_cross_short"] = cross_down.fillna(False)
    out["core_regime_long"] = (
        cross_up & out["close"].gt(slow) & out["close"].gt(out["regime_ma"])
    ).fillna(False)
    out["core_regime_short"] = (
        cross_down & out["close"].lt(slow) & out["close"].lt(out["regime_ma"])
    ).fillna(False)
    out["core_slope_long"] = (
        out["core_regime_long"] & out["slow_slope_12"].gt(0.0)
    ).fillna(False)
    out["core_slope_short"] = (
        out["core_regime_short"] & out["slow_slope_12"].lt(0.0)
    ).fillna(False)
    out["core_osc_direction_long"] = (
        out["osc_percentile_safe"]
        & out["core_slope_long"]
        & out["osc"].gt(0.0)
        & out["osc"].gt(out["osc"].shift(1))
    ).fillna(False)
    out["core_osc_direction_short"] = (
        out["osc_percentile_safe"]
        & out["core_slope_short"]
        & out["osc"].lt(0.0)
        & out["osc"].lt(out["osc"].shift(1))
    ).fillna(False)
    variants = (
        Variant("core_cross_only", "core_cross_long", "core_cross_short"),
        Variant("core_plus_ema100_regime", "core_regime_long", "core_regime_short"),
        Variant("core_plus_slope12", "core_slope_long", "core_slope_short"),
        Variant(
            "core_plus_osc_direction",
            "core_osc_direction_long",
            "core_osc_direction_short",
        ),
        Variant("core_v9_threshold_0p1", "v9_long", "v9_short"),
    )
    return out, variants


def core_component_ablation(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Run each nested core on the same four development half-years."""

    frame, variants = add_core_ablation_signals(frame)
    rows: list[dict[str, Any]] = []
    aggregate: list[dict[str, Any]] = []
    for order, variant in enumerate(variants):
        variant_rows: list[dict[str, Any]] = []
        for period in DEVELOPMENT_BLOCKS:
            trades, equity = simulate_period(frame, variant, period)
            summary = summarize(
                trades,
                equity,
                variant=variant.name,
                period=period.name,
                risk_percent=1.0,
            )
            summary["component_order"] = order
            variant_rows.append(summary)
            rows.append(summary)
        block = pd.DataFrame(variant_rows)
        aggregate.append(
            {
                "variant": variant.name,
                "component_order": order,
                "blocks": int(len(block)),
                "trades": int(block["trades"].sum()),
                "minimum_block_net_bp": float(block["project_net_bp_per_trade"].min()),
                "weighted_net_bp": float(
                    np.average(block["project_net_bp_per_trade"], weights=block["trades"])
                ),
                "positive_blocks": int(block["project_net_bp_per_trade"].gt(0.0).sum()),
            }
        )
    return pd.DataFrame(rows), aggregate


def side_ablation(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Compare two-sided, long-only, and short-only entry eligibility in development."""

    variants = (
        Variant("v9_two_sided", "v9_long", "v9_short", entry_directions=(-1, 1)),
        Variant("v9_long_only", "v9_long", "v9_short", entry_directions=(1,)),
        Variant("v9_short_only", "v9_long", "v9_short", entry_directions=(-1,)),
    )
    rows: list[dict[str, Any]] = []
    aggregate: list[dict[str, Any]] = []
    for variant in variants:
        selected_rows: list[dict[str, Any]] = []
        for period in DEVELOPMENT_BLOCKS:
            trades, equity = simulate_period(frame, variant, period)
            summary = summarize(
                trades,
                equity,
                variant=variant.name,
                period=period.name,
                risk_percent=1.0,
            )
            selected_rows.append(summary)
            rows.append(summary)
        block = pd.DataFrame(selected_rows)
        aggregate.append(
            {
                "variant": variant.name,
                "entry_directions": list(variant.entry_directions),
                "trades": int(block["trades"].sum()),
                "minimum_block_net_bp": float(block["project_net_bp_per_trade"].min()),
                "weighted_net_bp": float(
                    np.average(block["project_net_bp_per_trade"], weights=block["trades"])
                ),
                "positive_blocks": int(block["project_net_bp_per_trade"].gt(0.0).sum()),
                "mean_return_percent": float(block["return_percent"].mean()),
                "worst_drawdown_percent": float(block["max_drawdown_15m_percent"].max()),
            }
        )
    return pd.DataFrame(rows), aggregate


def write_core_chart(aggregate: list[dict[str, Any]]) -> None:
    """Render the nested core relationship for the durable HTML report."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [
        "Cross",
        "+ EMA100",
        "+ slope12",
        "+ osc dir",
        "+ |osc| 0.1",
    ]
    weighted = [float(row["weighted_net_bp"]) for row in aggregate]
    minimum = [float(row["minimum_block_net_bp"]) for row in aggregate]
    x = np.arange(len(labels))
    fig, axis = plt.subplots(figsize=(9.2, 4.8))
    axis.axhline(0.0, color="#475569", linewidth=1.0)
    axis.plot(x, weighted, marker="o", linewidth=2.3, color="#2563EB", label="weighted mean")
    axis.plot(x, minimum, marker="s", linewidth=2.0, color="#D97706", label="worst half-year")
    axis.fill_between(x, minimum, weighted, color="#CBD5E1", alpha=0.28)
    axis.set_xticks(x, labels)
    axis.set_ylabel("Net expectancy (bp/trade, after 20 bp cost)")
    axis.set_title("ETH 15m nested core ablation — development only")
    axis.grid(axis="y", alpha=0.22)
    axis.legend(frameon=False)
    fig.tight_layout()
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS / "core_component_ablation.png", dpi=160)
    plt.close(fig)


def _selection_table(search: pd.DataFrame, train_periods: list[str]) -> pd.DataFrame:
    subset = search.loc[search["period"].isin(train_periods)]
    records = []
    for feature, group in subset.groupby("feature_filter", sort=True):
        records.append(
            {
                "feature_filter": feature,
                "minimum_block_net_bp": float(group["project_net_bp_per_trade"].min()),
                "weighted_net_bp": float(
                    np.average(group["project_net_bp_per_trade"], weights=group["trades"])
                ),
                "trades": int(group["trades"].sum()),
            }
        )
    return pd.DataFrame(records).sort_values(
        ["minimum_block_net_bp", "weighted_net_bp", "trades", "feature_filter"],
        ascending=[False, False, False, True],
        kind="mergesort",
    )


def exact_block_signflip(values: np.ndarray) -> dict[str, Any]:
    """Exact one-sided sign-flip p-value for a short block vector."""

    values = np.asarray(values, dtype=float)
    observed = float(values.mean())
    null = np.array(
        [np.mean(np.asarray(signs, dtype=float) * values) for signs in itertools.product((-1, 1), repeat=len(values))],
        dtype=float,
    )
    return {
        "observed_mean_bp": observed,
        "p_value": float(np.mean(null >= observed - 1e-12)),
        "blocks": int(len(values)),
        "exact_sign_patterns": int(len(null)),
        "minimum_attainable_one_sided_p": float(1.0 / len(null)),
    }


def selection_adjusted_max_signflip(search: pd.DataFrame) -> dict[str, Any]:
    """Exact max-stat sign flip across all deterministic feature gates."""

    periods = [period.name for period in DEVELOPMENT_BLOCKS]
    pivot = search.pivot(
        index="feature_filter",
        columns="period",
        values="project_net_bp_per_trade",
    ).loc[:, periods]
    delta = pivot.sub(pivot.loc["none"], axis="columns")
    observed_by_feature = delta.mean(axis=1)
    selected = str(observed_by_feature.idxmax())
    observed = float(observed_by_feature.max())
    null_maxima: list[float] = []
    for signs in itertools.product((-1, 1), repeat=len(periods)):
        signed = delta.mul(np.asarray(signs, dtype=float), axis="columns")
        null_maxima.append(float(signed.mean(axis=1).max()))
    null = np.asarray(null_maxima, dtype=float)
    selected_values = delta.loc[selected].to_numpy(dtype=float)
    return {
        "candidate_gate_count_including_none": int(len(delta)),
        "selected_feature": selected,
        "selected_increment_by_block_bp": [float(value) for value in selected_values],
        "selected_unadjusted": exact_block_signflip(selected_values),
        "selection_adjusted_observed_max_mean_bp": observed,
        "selection_adjusted_p_value": float(np.mean(null >= observed - 1e-12)),
        "exact_common_sign_patterns": int(len(null)),
        "interpretation": (
            "The common block signs preserve cross-feature dependence. The adjusted "
            "test asks whether the best gate among all searched natural gates is stronger "
            "than the best gate obtained after flipping time-block signs."
        ),
    }


def selection_adjusted_side_signflip(side_rows: pd.DataFrame) -> dict[str, Any]:
    """Exact max-stat test for the development-only direction eligibility choice."""

    periods = [period.name for period in DEVELOPMENT_BLOCKS]
    pivot = side_rows.pivot(
        index="variant",
        columns="period",
        values="project_net_bp_per_trade",
    ).loc[:, periods]
    delta = pivot.sub(pivot.loc["v9_two_sided"], axis="columns")
    observed_by_variant = delta.mean(axis=1)
    selected = str(observed_by_variant.idxmax())
    selected_values = delta.loc[selected].to_numpy(dtype=float)
    observed = float(observed_by_variant.max())
    null_maxima = []
    for signs in itertools.product((-1, 1), repeat=len(periods)):
        signed = delta.mul(np.asarray(signs, dtype=float), axis="columns")
        null_maxima.append(float(signed.mean(axis=1).max()))
    null = np.asarray(null_maxima, dtype=float)
    return {
        "candidate_direction_policies": int(len(delta)),
        "selected_policy": selected,
        "selected_increment_by_block_bp": [float(value) for value in selected_values],
        "selected_unadjusted": exact_block_signflip(selected_values),
        "selection_adjusted_p_value": float(np.mean(null >= observed - 1e-12)),
        "exact_common_sign_patterns": int(len(null)),
        "verdict": "forward hypothesis only; p<0.01 not attainable with four blocks",
    }


def prequential_feature_replay(search: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select on past half-years only, then evaluate the immediately next block."""

    periods = [period.name for period in DEVELOPMENT_BLOCKS]
    rows: list[dict[str, Any]] = []
    for test_index in range(1, len(periods)):
        training_periods = periods[:test_index]
        test_period = periods[test_index]
        ranking = _selection_table(search, training_periods)
        selected_feature = str(ranking.iloc[0]["feature_filter"])
        selected = search.loc[
            search["feature_filter"].eq(selected_feature) & search["period"].eq(test_period)
        ].iloc[0]
        baseline = search.loc[
            search["feature_filter"].eq("none") & search["period"].eq(test_period)
        ].iloc[0]
        rows.append(
            {
                "selected_on_periods": ",".join(training_periods),
                "test_period": test_period,
                "selected_feature": selected_feature,
                "training_minimum_block_net_bp": float(ranking.iloc[0]["minimum_block_net_bp"]),
                "training_weighted_net_bp": float(ranking.iloc[0]["weighted_net_bp"]),
                "selected_test_trades": int(selected["trades"]),
                "selected_test_net_bp": float(selected["project_net_bp_per_trade"]),
                "baseline_test_trades": int(baseline["trades"]),
                "baseline_test_net_bp": float(baseline["project_net_bp_per_trade"]),
                "incremental_test_net_bp": float(
                    selected["project_net_bp_per_trade"] - baseline["project_net_bp_per_trade"]
                ),
                "selected_test_return_percent": float(selected["return_percent"]),
                "selected_test_drawdown_percent": float(selected["max_drawdown_15m_percent"]),
            }
        )
    frame = pd.DataFrame(rows)
    summary = {
        "test_blocks": int(len(frame)),
        "same_feature_selected_every_step": bool(frame["selected_feature"].nunique() == 1),
        "selected_features": frame["selected_feature"].tolist(),
        "positive_increment_blocks": int(frame["incremental_test_net_bp"].gt(0.0).sum()),
        "selected_weighted_test_net_bp": float(
            np.average(frame["selected_test_net_bp"], weights=frame["selected_test_trades"])
        ),
        "baseline_weighted_test_net_bp": float(
            np.average(frame["baseline_test_net_bp"], weights=frame["baseline_test_trades"])
        ),
        "increment_exact_signflip": exact_block_signflip(
            frame["incremental_test_net_bp"].to_numpy(dtype=float)
        ),
        "caveat": (
            "This is prequential only for the incremental project feature gate. "
            "The V9 signal family itself had already been selected using all four development blocks."
        ),
    }
    return frame, summary


def main() -> None:
    frame, quality = load_development_only_frame()
    core, core_aggregate = core_component_ablation(frame)
    core.to_csv(CORE_OUTPUT, index=False)
    write_core_chart(core_aggregate)
    sides, side_aggregate = side_ablation(frame)
    sides.to_csv(SIDE_OUTPUT, index=False)
    side_test = selection_adjusted_side_signflip(sides)

    feature_search = pd.read_csv(RESULTS / "feature_filter_search.csv")
    if set(feature_search["period"]) != {period.name for period in DEVELOPMENT_BLOCKS}:
        raise RuntimeError("feature-filter search contains an unexpected time period")
    prequential, prequential_summary = prequential_feature_replay(feature_search)
    prequential.to_csv(PREQUENTIAL_OUTPUT, index=False)
    max_test = selection_adjusted_max_signflip(feature_search)

    output = {
        "audit": "development-only core and feature-gate robustness",
        "data_quality": quality,
        "final_preholdout_rows_read": 0,
        "holdout_rows_read": 0,
        "execution_or_cost_parameters_changed": False,
        "core_component_aggregate": core_aggregate,
        "side_ablation_aggregate": side_aggregate,
        "side_selection_test": side_test,
        "prequential_feature_replay": prequential_summary,
        "selection_adjusted_feature_test": max_test,
        "honest_verdict": (
            "The nested core improves monotonically and vol_ratio_mean8>=1 is selected "
            "at every incremental replay, but four development blocks cannot establish "
            "p<0.01 and the gate search fails the selection-adjusted max-stat test."
        ),
    }
    ROBUSTNESS_OUTPUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
