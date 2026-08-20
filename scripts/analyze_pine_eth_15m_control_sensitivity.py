#!/usr/bin/env python3
"""Audit matched-control assignment-seed sensitivity for ETH 15m candidates.

Exact controls are sampled without replacement inside month x HK-six-hour x
previous-UTC-month-ATR-quintile strata. A single deterministic seed is reproducible but
can still be a noisy estimate.  This script reruns 64 predeclared assignment
seeds for V9, V10, and the post-selection V11 long-only hypothesis, preserving
each candidate ledger, direction, holding horizon, barriers, and 20 bp cost.

All market rows are bounded before 2026-03-01; repository holdout is refused.
The audit changes no strategy, model, threshold, barrier, or production state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    SPLITS,
    Variant,
    block_signflip,
    build_feature_frame,
    build_matched_controls,
    load_config,
    load_research_frame,
    pair_controls,
    simulate_period,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CSV_OUTPUT = RESULTS / "control_seed_sensitivity.csv"
JSON_OUTPUT = RESULTS / "control_seed_sensitivity.json"
N_ASSIGNMENT_SEEDS = 64
N_SIGNFLIP_RESAMPLES = 2_000


def _candidate_specs() -> tuple[Variant, ...]:
    return (
        Variant("v9_locked", "v9_long", "v9_short"),
        Variant("v10_volume_hypothesis", "v10_volume_long", "v10_volume_short"),
        Variant(
            "v11_long_only_postselection",
            "v9_long",
            "v9_short",
            entry_directions=(1,),
            final_evaluated=True,
            selection_status="post_final_selection_forward_hypothesis",
        ),
    )


def _quantiles(values: pd.Series) -> dict[str, float]:
    return {
        "min": float(values.min()),
        "q05": float(values.quantile(0.05)),
        "median": float(values.median()),
        "q95": float(values.quantile(0.95)),
        "max": float(values.max()),
    }


def main() -> None:
    config = load_config()
    raw, quality = load_research_frame(config)
    frame = build_feature_frame(raw)
    period = SPLITS[-1]
    rows: list[dict[str, Any]] = []
    expected_trades = {
        "v9_locked": 110,
        "v10_volume_hypothesis": 77,
        "v11_long_only_postselection": 56,
    }
    for spec_index, spec in enumerate(_candidate_specs()):
        trades, _ = simulate_period(frame, spec, period)
        if len(trades) != expected_trades[spec.name]:
            raise RuntimeError(f"{spec.name} candidate ledger changed: {len(trades)}")
        candidate_bp = float(trades["project_net_return"].mean() * 10_000.0)
        for seed_index in range(N_ASSIGNMENT_SEEDS):
            assignment_seed = f"pine-eth15m-control-sensitivity-{seed_index:03d}"
            controls = build_matched_controls(
                frame,
                trades,
                period,
                seed=assignment_seed,
            )
            pairs = pair_controls(trades, controls)
            inference = block_signflip(
                pairs,
                n_resamples=N_SIGNFLIP_RESAMPLES,
                seed=20260900 + spec_index * N_ASSIGNMENT_SEEDS + seed_index,
            )
            rows.append(
                {
                    "variant": spec.name,
                    "assignment_seed": assignment_seed,
                    "candidate_trades": int(len(trades)),
                    "controls": int(len(controls)),
                    "candidate_net_bp": candidate_bp,
                    "control_net_bp": float(
                        pairs["control_mean_project_net"].mean() * 10_000.0
                    ),
                    "candidate_minus_control_bp": float(
                        pairs["excess_return"].mean() * 10_000.0
                    ),
                    "week_equal_weight_excess_bp": float(
                        inference["statistic_mean_excess_bp"]
                    ),
                    "week_signflip_p": float(inference["p_value"]),
                    "week_blocks": int(inference["n_blocks"]),
                    "unique_control_starts": bool(controls["control_signal_i"].is_unique),
                    "exact_three_controls_each": bool(
                        controls.groupby("trade_id")["control_signal_i"].size().eq(3).all()
                    ),
                }
            )
    result = pd.DataFrame(rows)
    result.to_csv(CSV_OUTPUT, index=False)

    summaries = []
    for variant, group in result.groupby("variant", sort=False):
        summaries.append(
            {
                "variant": variant,
                "assignment_seeds": int(len(group)),
                "candidate_trades": int(group["candidate_trades"].iloc[0]),
                "candidate_net_bp": float(group["candidate_net_bp"].iloc[0]),
                "control_net_bp": _quantiles(group["control_net_bp"]),
                "candidate_minus_control_bp": _quantiles(
                    group["candidate_minus_control_bp"]
                ),
                "week_signflip_p": _quantiles(group["week_signflip_p"]),
                "fraction_assignment_seeds_with_positive_excess": float(
                    group["candidate_minus_control_bp"].gt(0.0).mean()
                ),
                "fraction_assignment_seeds_with_p_below_0p01": float(
                    group["week_signflip_p"].lt(0.01).mean()
                ),
            }
        )
    payload = {
        "audit": "exact matched-control assignment-seed sensitivity",
        "assignment_seeds": N_ASSIGNMENT_SEEDS,
        "signflip_resamples_per_seed": N_SIGNFLIP_RESAMPLES,
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "strategy_or_evaluation_contract_changed": False,
        "all_control_sets_exact": bool(
            result["unique_control_starts"].all()
            and result["exact_three_controls_each"].all()
        ),
        "variants": summaries,
        "honest_verdict": (
            "One control seed is not an uncertainty interval. Candidate excess must be "
            "reported across exact assignment seeds, while the p<0.01 gate remains based "
            "on time-clustered inference and is not rescued by choosing a favorable seed."
        ),
    }
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("control sensitivity must not read repository holdout")
    if len(result) != len(_candidate_specs()) * N_ASSIGNMENT_SEEDS:
        raise RuntimeError("control seed sensitivity row count changed")
    if not payload["all_control_sets_exact"]:
        raise RuntimeError("at least one control seed violated exact coverage")
    JSON_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
