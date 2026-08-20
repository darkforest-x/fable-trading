#!/usr/bin/env python3
"""Evaluate V9 long-only as an explicitly post-selection ETH 15m hypothesis.

The only strategy variable changed from frozen V9 is entry-direction
eligibility: short signals still close long positions but never open a short.
Signals, 15-minute timing, ATR stop, break-even, cooldown, risk, and 20 bp cost
remain fixed.  The 2025--2026-02 final-preholdout period was already consumed
before this hypothesis was formed, so its result is descriptive and may only
seed a fresh paper-forward V11 comparison.

The loader remains bounded at 2026-03-01 and refuses repository holdout rows.
No model is trained or scored, and no production/forward pointer is changed.
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
    concentration_diagnostics,
    load_config,
    load_research_frame,
    pair_controls,
    simulate_period,
    summarize,
    week_bootstrap_ci,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
SUMMARY_OUTPUT = RESULTS / "v11_long_only_summary.json"
SPLIT_OUTPUT = RESULTS / "v11_long_only_split_summary.csv"
TRADES_OUTPUT = RESULTS / "v11_long_only_trades.csv"
CONTROLS_OUTPUT = RESULTS / "v11_long_only_controls.csv"
PAIRS_OUTPUT = RESULTS / "v11_long_only_pairs.csv"


def main() -> None:
    config = load_config()
    raw, quality = load_research_frame(config)
    frame = build_feature_frame(raw)
    spec = Variant(
        "v11_long_only_postselection",
        "v9_long",
        "v9_short",
        entry_directions=(1,),
        final_evaluated=True,
        selection_status="post_final_selection_forward_hypothesis",
    )
    all_trades: list[pd.DataFrame] = []
    rows: list[dict[str, Any]] = []
    for period in SPLITS:
        trades, equity = simulate_period(frame, spec, period)
        rows.append(
            summarize(
                trades,
                equity,
                variant=spec.name,
                period=period.name,
                risk_percent=1.0,
            )
        )
        all_trades.append(trades)
    ledger = pd.concat(all_trades, ignore_index=True)
    split = pd.DataFrame(rows)
    split.to_csv(SPLIT_OUTPUT, index=False)
    ledger.to_csv(TRADES_OUTPUT, index=False)

    final_period = SPLITS[-1]
    final = ledger.loc[ledger["split"].eq(final_period.name)].copy()
    controls = build_matched_controls(
        frame,
        final,
        final_period,
        seed="pine-eth15m-v11-long-controls-v1",
    )
    pairs = pair_controls(final, controls)
    controls.to_csv(CONTROLS_OUTPUT, index=False)
    pairs.to_csv(PAIRS_OUTPUT, index=False)
    signflip = block_signflip(pairs, seed=20260831)
    absolute = week_bootstrap_ci(
        pairs,
        "project_net_return",
        seed=20260832,
    )
    excess = week_bootstrap_ci(
        pairs,
        "excess_return",
        seed=20260833,
    )
    selected = split.loc[split["period"].eq(final_period.name)].iloc[0].to_dict()
    concentration = concentration_diagnostics(final)
    summary = {
        "hypothesis": "V11 long-only entry eligibility on frozen V9",
        "status": "post-final-selection paper-forward hypothesis",
        "single_variable": "entry directions (-1, 1) -> (1,); short signals remain exits",
        "selection_source": "development-only side ablation",
        "final_preholdout_was_already_consumed": True,
        "holdout_consumed": False,
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "barrier_cost_or_risk_changed": False,
        "final_preholdout": selected,
        "matched_control": {
            "trades": int(len(final)),
            "controls": int(len(controls)),
            "mean_candidate_net_bp": float(pairs["project_net_return"].mean() * 10_000.0),
            "mean_control_net_bp": float(pairs["control_mean_project_net"].mean() * 10_000.0),
            "mean_excess_bp": float(pairs["excess_return"].mean() * 10_000.0),
            "unique_control_starts": bool(controls["control_signal_i"].is_unique),
        },
        "week_block_signflip": signflip,
        "week_bootstrap_absolute": absolute,
        "week_bootstrap_excess": excess,
        "profit_concentration": concentration,
        "eligibility": {
            "training": False,
            "production": False,
            "forward": False,
            "paper_ab_only": True,
        },
        "honest_verdict": (
            "Long-only improves the descriptive point estimate and drawdown, but the "
            "consumed final period is not OOS, week inference crosses zero, and removing "
            "the largest winner makes mean expectancy negative. It cannot replace V9."
        ),
    }
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("V11 hypothesis must not read repository holdout")
    if len(final) != 56 or len(controls) != 168:
        raise RuntimeError("V11 exact trade/control coverage changed unexpectedly")
    if not np.isfinite(float(selected["project_net_bp_per_trade"])):
        raise RuntimeError("V11 summary is not finite")
    SUMMARY_OUTPUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
