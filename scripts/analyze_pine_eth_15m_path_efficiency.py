#!/usr/bin/env python3
"""Audit one preregistered causal path-efficiency feature on Pine development data.

The feature reads only ``close[t-33:t-1]`` for the fixed 32-change window
defined in :mod:`yoyo.layers.l2_judgment.pine_path_efficiency`.  Market loading
is bounded to 2023-01-01 through 2024-12-31 and reports zero rows from both the
consumed final-preholdout period and repository holdout.  The 335-row raw
candidate output is feature-only; outcome diagnostics use the frozen V9
on-policy ledger and therefore cannot estimate a counterfactual entry gate.

No threshold is selected, no model is fitted, and no Pine/execution parameter
is changed.  The one-sided permutation null asks whether the fixed high-feature
decile has mean net return no better than a random same-size subset of the same
on-policy ledger.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from scripts.prepare_pine_eth_15m_gate_surface import build_candidate_surface
from scripts.prepare_pine_eth_15m_judgment_research import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    load_development_features,
)
from scripts.research_pine_eth_15m import Period, Variant, simulate_period
from yoyo.layers.l2_judgment.pine_path_efficiency import (
    DEFAULT_PRE_CROSS_PATH_EFFICIENCY_LOOKBACK,
    add_pre_cross_path_efficiency,
    path_efficiency_column,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-path-efficiency-v1"
RESULTS = EXPERIMENT / "results"
FEATURE_OUTPUT = RESULTS / "candidate_path_efficiency.csv"
ON_POLICY_OUTPUT = RESULTS / "on_policy_path_efficiency_outcomes.csv"
SUMMARY_OUTPUT = RESULTS / "path_efficiency_diagnostic.json"
PERMUTATIONS = 20_000
SEED = 20_260_821
FEATURE_COLUMN = path_efficiency_column(DEFAULT_PRE_CROSS_PATH_EFFICIENCY_LOOKBACK)
HALF_YEAR_WINDOWS = (
    ("2023H1", "2023-01-01", "2023-07-01"),
    ("2023H2", "2023-07-01", "2024-01-01"),
    ("2024H1", "2024-01-01", "2024-07-01"),
    ("2024H2", "2024-07-01", "2025-01-01"),
)


def attach_path_efficiency(
    surface: pd.DataFrame,
    featured: pd.DataFrame,
) -> pd.DataFrame:
    """Attach the one prior-only feature to an already identified signal surface."""

    required = {"candidate_id", "side", "signal_i", "signal_time", "earliest_entry_time"}
    missing = sorted(required - set(surface.columns))
    if missing:
        raise ValueError(f"candidate surface missing identity columns: {missing}")
    if surface["candidate_id"].duplicated().any():
        raise RuntimeError("candidate surface contains duplicate ids")

    enriched = add_pre_cross_path_efficiency(featured)
    indices = surface["signal_i"].astype(int).to_numpy()
    if len(indices) and (indices.min() < 0 or indices.max() >= len(enriched)):
        raise RuntimeError("candidate signal index is outside the feature frame")
    rows = surface[
        [
            "candidate_id",
            "side",
            "signal_i",
            "signal_time",
            "features_available_at",
            "earliest_entry_time",
            "candidate_policy",
        ]
    ].copy()
    rows[FEATURE_COLUMN] = enriched.iloc[indices][FEATURE_COLUMN].to_numpy(dtype=float)
    if not np.isfinite(rows[FEATURE_COLUMN].to_numpy(dtype=float)).all():
        raise RuntimeError("candidate path-efficiency surface has missing/non-finite values")
    if not rows[FEATURE_COLUMN].between(0.0, 1.0, inclusive="both").all():
        raise RuntimeError("candidate path efficiency escaped [0, 1]")
    rows["feature_contract"] = "pine_pre_cross_path_efficiency_32_v1"
    rows["training_eligible"] = False
    return rows


def rank_diagnostic(
    rows: pd.DataFrame,
    *,
    permutations: int = PERMUTATIONS,
    seed: int = SEED,
) -> dict[str, Any]:
    """Compute fixed-orientation descriptive ranks and a one-sided shuffle null."""

    if not isinstance(permutations, int) or isinstance(permutations, bool) or permutations <= 0:
        raise ValueError("permutations must be a positive integer")
    required = {FEATURE_COLUMN, "project_net_return", "net_positive"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"diagnostic rows missing columns: {missing}")
    if rows.empty:
        raise ValueError("diagnostic rows are empty")

    feature = rows[FEATURE_COLUMN].to_numpy(dtype=float)
    returns = rows["project_net_return"].to_numpy(dtype=float)
    positives = rows["net_positive"].astype(bool).to_numpy()
    if not np.isfinite(feature).all() or not np.isfinite(returns).all():
        raise ValueError("diagnostic inputs must be finite")
    if len(np.unique(positives)) != 2:
        raise ValueError("diagnostic needs both positive and non-positive outcomes")

    top_n = max(1, math.ceil(len(rows) * 0.10))
    order = np.argsort(-feature, kind="stable")
    top_returns = returns[order[:top_n]]
    observed = float(top_returns.mean())
    rng = np.random.default_rng(seed)
    null = np.empty(permutations, dtype=float)
    for index in range(permutations):
        null[index] = returns[rng.permutation(len(returns))[:top_n]].mean()
    permutation_p = float((1 + np.count_nonzero(null >= observed)) / (permutations + 1))
    spearman = spearmanr(feature, returns)
    return {
        "rows": int(len(rows)),
        "positive_rows": int(positives.sum()),
        "positive_rate": float(positives.mean()),
        "feature_min": float(feature.min()),
        "feature_q10": float(np.quantile(feature, 0.10)),
        "feature_median": float(np.median(feature)),
        "feature_q90": float(np.quantile(feature, 0.90)),
        "feature_max": float(feature.max()),
        "spearman_return": float(spearman.statistic),
        "spearman_p_two_sided": float(spearman.pvalue),
        "auc_net_positive": float(roc_auc_score(positives, feature)),
        "top_decile_rows": int(top_n),
        "top_decile_net_bp_per_trade": observed * 10_000.0,
        "top_decile_win_rate": float(positives[order[:top_n]].mean()),
        "unconditional_net_bp_per_trade": float(returns.mean() * 10_000.0),
        "top_decile_excess_bp_per_trade": float((observed - returns.mean()) * 10_000.0),
        "top_decile_permutation_p_one_sided": permutation_p,
        "permutations": permutations,
        "seed": seed,
    }


def half_year_diagnostics(rows: pd.DataFrame) -> list[dict[str, Any]]:
    """Return point estimates by fixed calendar half without extra hypothesis tests."""

    signal_time = pd.to_datetime(rows["signal_time"], utc=True)
    output: list[dict[str, Any]] = []
    for name, start, end in HALF_YEAR_WINDOWS:
        mask = signal_time.ge(pd.Timestamp(start, tz="UTC")) & signal_time.lt(
            pd.Timestamp(end, tz="UTC")
        )
        part = rows.loc[mask].copy()
        feature = part[FEATURE_COLUMN].to_numpy(dtype=float)
        returns = part["project_net_return"].to_numpy(dtype=float)
        positives = part["net_positive"].astype(bool).to_numpy()
        top_n = max(1, math.ceil(len(part) * 0.10))
        order = np.argsort(-feature, kind="stable")
        output.append(
            {
                "period": name,
                "rows": int(len(part)),
                "positive_rows": int(positives.sum()),
                "spearman_return": float(spearmanr(feature, returns).statistic),
                "auc_net_positive": float(roc_auc_score(positives, feature)),
                "top_decile_rows": int(top_n),
                "top_decile_net_bp_per_trade": float(returns[order[:top_n]].mean() * 10_000.0),
                "unconditional_net_bp_per_trade": float(returns.mean() * 10_000.0),
            }
        )
    return output


def build_on_policy_rows(feature_rows: pd.DataFrame, featured: pd.DataFrame) -> pd.DataFrame:
    """Join the feature to V9's frozen 2023--2024 on-policy dynamic ledger."""

    period = Period("development_continuous_2023_2024", DEVELOPMENT_START, DEVELOPMENT_END)
    spec = Variant("v9_path_efficiency_lineage", "v9_long", "v9_short")
    trades, _ = simulate_period(featured, spec, period)
    left = trades.rename(columns={"direction": "side"}).copy()
    joined = left.merge(
        feature_rows[["candidate_id", "side", "signal_i", FEATURE_COLUMN]],
        on=["side", "signal_i"],
        how="left",
        validate="one_to_one",
    )
    if joined[FEATURE_COLUMN].isna().any():
        raise RuntimeError("on-policy trade is missing from the raw feature surface")
    joined["net_positive"] = joined["project_net_return"].gt(0.0)
    joined["training_eligible"] = False
    return joined[
        [
            "trade_id",
            "candidate_id",
            "side",
            "signal_i",
            "signal_time",
            "entry_time",
            "exit_time",
            "exit_reason",
            FEATURE_COLUMN,
            "project_net_return",
            "net_positive",
            "training_eligible",
        ]
    ]


def main() -> None:
    featured, quality = load_development_features()
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError("path-efficiency audit crossed a protected boundary")
    surface = build_candidate_surface(featured)
    feature_rows = attach_path_efficiency(surface, featured)
    on_policy = build_on_policy_rows(feature_rows, featured)
    diagnostic = rank_diagnostic(on_policy)
    payload = {
        "artifact": "Pine pre-cross path-efficiency single-feature diagnostic",
        "feature_contract": {
            "column": FEATURE_COLUMN,
            "lookback_price_changes": DEFAULT_PRE_CROSS_PATH_EFFICIENCY_LOOKBACK,
            "right_edge": "t-1",
            "formula": "abs(close[t-1]-close[t-33]) / sum(abs(diff(close[t-33:t-1])))",
            "orientation_fixed_before_outcomes": "higher means more one-way / less choppy",
        },
        "data_quality": quality,
        "raw_surface": {
            "rows": int(len(feature_rows)),
            "long_rows": int(feature_rows["side"].eq("long").sum()),
            "short_rows": int(feature_rows["side"].eq("short").sum()),
            "missing_feature_cells": int(feature_rows[FEATURE_COLUMN].isna().sum()),
        },
        "on_policy_diagnostic": diagnostic,
        "half_year_point_estimates": half_year_diagnostics(on_policy),
        "matched_random_control": {
            "applicable": False,
            "reason": (
                "No entry rule or strategy arm changed; this is a continuous-feature ranking "
                "audit on one frozen on-policy ledger. The explicit null is outcome permutation."
            ),
        },
        "counterfactual_limit": (
            "The 166 executed rows cover only part of the 335 raw candidate surface. "
            "Top-decile diagnostics cannot be interpreted as gate returns; any future threshold "
            "must score all candidates and replay the state machine."
        ),
        "threshold_selected": False,
        "model_fitted": False,
        "pine_changed": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
        "consumed_final_rows_read": 0,
        "holdout_rows_read": 0,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    feature_rows.to_csv(FEATURE_OUTPUT, index=False)
    on_policy.to_csv(ON_POLICY_OUTPUT, index=False)
    SUMMARY_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
