#!/usr/bin/env python3
"""Audit selection risk across already-run ETH 15m hyperparameter families.

This script reads development-only result tables; it does not load market data,
run a new parameter combination, alter a barrier, train a model, or inspect the
consumed final/holdout periods.  The known search ledger contains oscillator
thresholds, EMA200 slope lags, natural feature gates, direction policies and
the already-run trailing-stop family.

The primary null is an exact common-block max-stat sign flip across the four
fixed 2023/2024 half-years.  Applying one sign vector to every configuration
preserves cross-configuration dependence.  A small 2-by-2 combinatorial
selection diagnostic is also reported, but four blocks are far too few for a
stable probability-of-backtest-overfitting estimate; its purpose is to expose
rank reversal, not to manufacture precision.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
OUTPUT_JSON = RESULTS / "selection_risk_audit.json"
OUTPUT_CSV = RESULTS / "selection_risk_unique_paths.csv"
PERIODS = ("2023H1", "2023H2", "2024H1", "2024H2")

FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("threshold", "threshold_search.csv", "threshold"),
    ("slope", "slope_search.csv", "slope_lag"),
    ("feature", "feature_filter_search.csv", "feature_filter"),
    ("side", "side_ablation.csv", "variant"),
    ("trailing", "trailing_search.csv", "variant"),
)


def load_search_ledger(results: Path = RESULTS) -> pd.DataFrame:
    pieces = []
    for family, filename, key in FAMILIES:
        frame = pd.read_csv(results / filename)
        frame = frame.loc[frame["period"].isin(PERIODS)].copy()
        frame["family"] = family
        frame["configuration"] = family + ":" + frame[key].astype(str)
        pieces.append(
            frame[
                [
                    "family",
                    "configuration",
                    "period",
                    "trades",
                    "project_net_bp_per_trade",
                ]
            ]
        )
    ledger = pd.concat(pieces, ignore_index=True)
    counts = ledger.groupby("configuration")["period"].nunique()
    if not counts.eq(len(PERIODS)).all():
        raise RuntimeError("selection ledger does not have every fixed half-year")
    return ledger


def collapse_identical_paths(
    ledger: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[str]]]:
    """Collapse aliases whose four-block returns are numerically identical."""

    returns = ledger.pivot(
        index="configuration", columns="period", values="project_net_bp_per_trade"
    ).loc[:, PERIODS]
    trades = ledger.pivot(
        index="configuration", columns="period", values="trades"
    ).loc[:, PERIODS]
    representatives: dict[tuple[float, ...], str] = {}
    aliases: dict[str, list[str]] = {}
    keep = []
    for configuration, row in returns.sort_index().iterrows():
        key = tuple(np.round(row.to_numpy(dtype=float), 8))
        representative = representatives.get(key)
        if representative is None:
            representatives[key] = str(configuration)
            aliases[str(configuration)] = [str(configuration)]
            keep.append(str(configuration))
        else:
            aliases[representative].append(str(configuration))
    return returns.loc[keep], trades.loc[keep], aliases


def exact_common_max_stat(returns: pd.DataFrame) -> dict[str, Any]:
    """Exact max-mean sign-flip test across all configuration paths."""

    means = returns.mean(axis=1)
    selected = str(means.idxmax())
    observed = float(means.loc[selected])
    null = np.asarray(
        [
            float(
                returns.mul(np.asarray(signs, dtype=float), axis="columns")
                .mean(axis=1)
                .max()
            )
            for signs in itertools.product((-1, 1), repeat=returns.shape[1])
        ]
    )
    return {
        "selected_configuration": selected,
        "selected_equal_block_mean_net_bp": observed,
        "selected_block_net_bp": [float(value) for value in returns.loc[selected]],
        "selection_adjusted_p_value": float(np.mean(null >= observed - 1e-12)),
        "exact_common_sign_patterns": int(len(null)),
        "minimum_attainable_p_value": float(1.0 / len(null)),
        "null_max_q05_bp": float(np.quantile(null, 0.05)),
        "null_max_q95_bp": float(np.quantile(null, 0.95)),
    }


def pbo_rank_reversal(returns: pd.DataFrame) -> dict[str, Any]:
    """Expose rank reversal in all six directed 2-train/2-test partitions."""

    rows = []
    for train_indices in itertools.combinations(range(len(PERIODS)), 2):
        test_indices = tuple(index for index in range(len(PERIODS)) if index not in train_indices)
        train_mean = returns.iloc[:, list(train_indices)].mean(axis=1)
        selected = str(train_mean.idxmax())
        test_mean = returns.iloc[:, list(test_indices)].mean(axis=1)
        percentile = float(test_mean.le(test_mean.loc[selected]).mean())
        rows.append(
            {
                "train_periods": [PERIODS[index] for index in train_indices],
                "test_periods": [PERIODS[index] for index in test_indices],
                "selected_configuration": selected,
                "train_net_bp": float(train_mean.loc[selected]),
                "test_net_bp": float(test_mean.loc[selected]),
                "test_rank_percentile": percentile,
                "below_test_median": percentile < 0.5,
            }
        )
    return {
        "partitions": rows,
        "below_test_median_partitions": int(sum(row["below_test_median"] for row in rows)),
        "partition_count": int(len(rows)),
        "descriptive_pbo_fraction": float(
            np.mean([row["below_test_median"] for row in rows])
        ),
        "formal_pbo_claimed": False,
        "why_not_formal": (
            "only four half-year blocks; the result is discrete and sensitive to the "
            "configuration universe"
        ),
    }


def chronological_selection(
    returns: pd.DataFrame, trades: pd.DataFrame, v9_representative: str
) -> list[dict[str, Any]]:
    """Use only earlier half-years to select, then inspect the next block."""

    rows = []
    for test_index in range(1, len(PERIODS)):
        train_periods = list(PERIODS[:test_index])
        test_period = PERIODS[test_index]
        ranking = []
        for configuration in returns.index:
            values = returns.loc[configuration, train_periods]
            weights = trades.loc[configuration, train_periods]
            ranking.append(
                (
                    float(values.min()),
                    float(np.average(values, weights=weights)),
                    int(weights.sum()),
                    str(configuration),
                )
            )
        selected = max(ranking)[3]
        selected_test = float(returns.loc[selected, test_period])
        v9_test = float(returns.loc[v9_representative, test_period])
        rows.append(
            {
                "selected_on": train_periods,
                "test_period": test_period,
                "selected_configuration": selected,
                "selected_test_net_bp": selected_test,
                "v9_test_net_bp": v9_test,
                "selected_minus_v9_bp": selected_test - v9_test,
            }
        )
    return rows


def main() -> None:
    ledger = load_search_ledger()
    returns, trades, aliases = collapse_identical_paths(ledger)
    raw_configuration_count = int(ledger["configuration"].nunique())
    family_counts = {
        family: int(group["configuration"].nunique())
        for family, group in ledger.groupby("family", sort=True)
    }
    v9_candidates = [
        representative
        for representative, values in aliases.items()
        if "threshold:0.1" in values
    ]
    if len(v9_candidates) != 1:
        raise RuntimeError("could not identify one frozen V9 performance path")
    v9 = v9_candidates[0]
    max_stat = exact_common_max_stat(returns)
    pbo = pbo_rank_reversal(returns)
    prequential = chronological_selection(returns, trades, v9)

    output = pd.DataFrame(
        {
            "representative": returns.index,
            "aliases": ["|".join(aliases[index]) for index in returns.index],
            "mean_net_bp": returns.mean(axis=1).to_numpy(dtype=float),
            "minimum_block_net_bp": returns.min(axis=1).to_numpy(dtype=float),
            "total_trades": trades.sum(axis=1).to_numpy(dtype=int),
            **{
                f"{period}_net_bp": returns[period].to_numpy(dtype=float)
                for period in PERIODS
            },
        }
    ).sort_values(["mean_net_bp", "representative"], ascending=[False, True])
    output.to_csv(OUTPUT_CSV, index=False)
    v9_row = output.loc[output["representative"].eq(v9)].iloc[0]
    payload = {
        "audit": "known ETH 15m development selection-budget risk",
        "source": "generated development-only result tables; no market-data reload",
        "periods": list(PERIODS),
        "consumed_final_rows_read": 0,
        "holdout_rows_read": 0,
        "new_parameter_combinations_run": 0,
        "barrier_or_cost_changed": False,
        "training_or_model_scoring_performed": False,
        "known_family_configuration_counts": family_counts,
        "raw_known_configurations": raw_configuration_count,
        "unique_four_block_performance_paths": int(len(returns)),
        "selection_history_exhaustive": False,
        "selection_history_limit": (
            "covers the five durable search tables only; code iterations and unrecorded "
            "human choices cannot be reconstructed as extra configurations"
        ),
        "v9_performance_path": {
            "representative": v9,
            "aliases": aliases[v9],
            "equal_block_mean_net_bp": float(v9_row["mean_net_bp"]),
            "minimum_block_net_bp": float(v9_row["minimum_block_net_bp"]),
            "mean_rank_of_unique_paths": int(
                output["mean_net_bp"].rank(ascending=False, method="min").loc[v9_row.name]
            ),
            "minimum_block_rank_of_unique_paths": int(
                output["minimum_block_net_bp"]
                .rank(ascending=False, method="min")
                .loc[v9_row.name]
            ),
        },
        "exact_global_max_stat": max_stat,
        "chronological_selection": prequential,
        "two_by_two_rank_reversal": pbo,
        "decision": (
            "The best observed path does not survive the global max-stat gate. The known "
            "development search budget is already large enough that more in-sample tuning "
            "would deepen selection bias. Keep V9 frozen; treat V10/V11 as separate fresh "
            "forward hypotheses rather than continuing to mine 2023/2024."
        ),
        "training_eligible": False,
        "production_eligible": False,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
