#!/usr/bin/env python3
"""Evaluate one natural, single-variable Pine V9 judgment gate.

The sole change is ``atr_pct_ratio96 >= 1.0`` at the confirmed signal bar:
current ATR percentage must be at least its causal trailing-96-bar mean.  The
threshold is semantic (volatility expansion versus contraction), not searched.
All other signal, stop, break-even, reversal, cooldown, risk and 20 bp cost
semantics remain frozen.  Evaluation is dynamic because rejecting an entry
changes later state transitions.

Only the 2023 and 2024 development blocks are loaded.  Each arm is reported
beside exact ETH x month x HK-6h x prior-month ATR-quintile copied-horizon
controls.  This is an exploratory rejection test, not model training or a
production gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.audit_pine_eth_15m_start_labels import validate_fifteen_minute_cadence
from scripts.prepare_pine_eth_15m_gate_surface import build_candidate_surface
from scripts.prepare_pine_eth_15m_judgment_research import load_development_features
from scripts.replay_pine_eth_15m_judgment_gate import run_dynamic_replay
from scripts.research_pine_eth_15m import (
    Period,
    block_signflip,
    build_matched_controls,
    pair_controls,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import max_drawdown, profit_factor


PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / "experiments/active/exp-pine-eth-15m-v1/results"
SUMMARY_OUTPUT = RESULTS / "judgment_atr_expansion_gate.json"
TRADES_OUTPUT = RESULTS / "judgment_atr_expansion_gate_trades.csv"
CONTROLS_OUTPUT = RESULTS / "judgment_atr_expansion_gate_controls.csv"
PAIRS_OUTPUT = RESULTS / "judgment_atr_expansion_gate_pairs.csv"
CONTROL_SENSITIVITY_OUTPUT = RESULTS / "judgment_atr_expansion_gate_control_sensitivity.csv"
ATR_EXPANSION_THRESHOLD = 1.0
CONTROL_COUNT = 3
CONTROL_SEED_COUNT = 64
RESAMPLES = 20_000


def summarize_arm(
    trades: pd.DataFrame,
    marked: pd.DataFrame,
    pairs: pd.DataFrame,
    *,
    signflip: dict[str, Any],
) -> dict[str, Any]:
    """Return economic, matched-control, and tail-concentration diagnostics."""

    if trades.empty:
        return {
            "trades": 0,
            "project_net_bp_per_trade": None,
            "compounded_return_percent": 0.0,
            "monetary_profit_factor": None,
            "net_win_rate": None,
            "max_drawdown_percent": None,
            "matched_control_net_bp_per_trade": None,
            "candidate_minus_control_bp_per_trade": None,
            "week_block_signflip_p_one_sided": None,
        }
    ordered = trades.sort_values("project_net_return", ascending=False)
    total = float(trades["project_net_return"].sum())
    top = float(ordered.iloc[0]["project_net_return"])
    without_top = ordered.iloc[1:]["project_net_return"]
    return {
        "trades": int(len(trades)),
        "project_net_bp_per_trade": float(trades["project_net_return"].mean() * 10_000.0),
        "compounded_return_percent": float(
            (trades["exit_equity"].iloc[-1] / 500.0 - 1.0) * 100.0
        ),
        "monetary_profit_factor": float(profit_factor(trades["pnl"])),
        "net_win_rate": float(trades["project_net_return"].gt(0.0).mean()),
        "max_drawdown_percent": float(
            max_drawdown(marked["normalized_equity"].to_numpy(dtype=float)) * 100.0
        ),
        "matched_control_net_bp_per_trade": float(
            pairs["control_mean_project_net"].mean() * 10_000.0
        ),
        "candidate_minus_control_bp_per_trade": float(
            pairs["excess_return"].mean() * 10_000.0
        ),
        "week_block_signflip_p_one_sided": float(signflip["p_value"]),
        "week_blocks": int(signflip["n_blocks"]),
        "largest_trade_share_of_total_project_net": (
            None if np.isclose(total, 0.0) else float(top / total)
        ),
        "without_largest_trade_net_bp_per_trade": (
            None if without_top.empty else float(without_top.mean() * 10_000.0)
        ),
    }


def main() -> None:
    featured, quality = load_development_features()
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError("ATR gate evaluation crossed a protected boundary")
    quality = {**quality, "cadence": validate_fifteen_minute_cadence(featured)}
    surface = build_candidate_surface(featured)
    surface_times = pd.to_datetime(surface["signal_time"], utc=True)
    arms = {
        "allow_all": np.ones(len(surface), dtype=bool),
        "atr_pct_ratio96_gte_1": surface["atr_pct_ratio96"].ge(
            ATR_EXPANSION_THRESHOLD
        ).to_numpy(dtype=bool),
    }
    periods = {
        "2023": Period(
            "discovery_2023",
            pd.Timestamp("2023-01-01T00:00:00Z"),
            pd.Timestamp("2024-01-01T00:00:00Z"),
        ),
        "2024": Period(
            "confirmation_2024_already_inspected",
            pd.Timestamp("2024-01-01T00:00:00Z"),
            pd.Timestamp("2025-01-01T00:00:00Z"),
        ),
    }

    summary_rows: dict[str, dict[str, Any]] = {}
    all_trades = []
    all_controls = []
    all_pairs = []
    sensitivity_rows = []
    for arm_name, gate_pass in arms.items():
        joined = surface[["candidate_id", "side", "signal_i", "signal_time"]].copy()
        joined["score"] = np.clip(
            surface["atr_pct_ratio96"].to_numpy(dtype=float) / 2.0,
            0.0,
            1.0,
        )
        joined["gate_pass"] = gate_pass
        for period_name, period in periods.items():
            trades, marked = run_dynamic_replay(
                featured,
                joined,
                start=period.start,
                end=period.end,
            )
            trades = trades.copy()
            trades["trade_id"] = [
                f"{arm_name}|{period_name}|{index}" for index in range(len(trades))
            ]
            trades["gate_arm"] = arm_name
            trades["period"] = period_name
            controls = pd.DataFrame()
            pairs = pd.DataFrame()
            for control_seed in range(CONTROL_SEED_COUNT):
                seed_controls = build_matched_controls(
                    featured,
                    trades,
                    period,
                    controls_per_trade=CONTROL_COUNT,
                    seed=(
                        f"pine-atr-expansion-v1|{period_name}|"
                        f"seed-{control_seed:02d}"
                    ),
                )
                seed_pairs = pair_controls(trades, seed_controls)
                sensitivity_rows.append(
                    {
                        "gate_arm": arm_name,
                        "period": period_name,
                        "control_seed": control_seed,
                        "matched_control_net_bp_per_trade": float(
                            seed_pairs["control_mean_project_net"].mean() * 10_000.0
                        ),
                        "candidate_minus_control_bp_per_trade": float(
                            seed_pairs["excess_return"].mean() * 10_000.0
                        ),
                    }
                )
                if control_seed == 0:
                    controls = seed_controls
                    pairs = seed_pairs
            controls["gate_arm"] = arm_name
            controls["period"] = period_name
            pairs["gate_arm"] = arm_name
            pairs["period"] = period_name
            signflip = block_signflip(
                pairs,
                n_resamples=RESAMPLES,
                seed=20_260_821 + int(period_name),
            )
            accepted = int(
                (
                    gate_pass
                    & surface_times.ge(period.start).to_numpy(dtype=bool)
                    & surface_times.lt(period.end).to_numpy(dtype=bool)
                ).sum()
            )
            sensitivity = pd.DataFrame(sensitivity_rows)
            sensitivity = sensitivity.loc[
                sensitivity["gate_arm"].eq(arm_name)
                & sensitivity["period"].eq(period_name)
            ]
            excess_values = sensitivity["candidate_minus_control_bp_per_trade"]
            summary_rows[f"{arm_name}|{period_name}"] = {
                "arm": arm_name,
                "period": period_name,
                "raw_candidates": int(
                    (surface_times.ge(period.start) & surface_times.lt(period.end)).sum()
                ),
                "accepted_candidates": accepted,
                **summarize_arm(trades, marked, pairs, signflip=signflip),
                "control_seed_sensitivity": {
                    "seeds": CONTROL_SEED_COUNT,
                    "excess_bp_median": float(excess_values.median()),
                    "excess_bp_p05": float(excess_values.quantile(0.05)),
                    "excess_bp_p95": float(excess_values.quantile(0.95)),
                    "positive_excess_seed_fraction": float(excess_values.gt(0.0).mean()),
                },
            }
            all_trades.append(trades)
            all_controls.append(controls)
            all_pairs.append(pairs)

    table = list(summary_rows.values())
    comparisons = {}
    for period_name in periods:
        baseline = summary_rows[f"allow_all|{period_name}"]
        gated = summary_rows[f"atr_pct_ratio96_gte_1|{period_name}"]
        comparisons[period_name] = {
            "return_delta_percentage_points": float(
                gated["compounded_return_percent"] - baseline["compounded_return_percent"]
            ),
            "drawdown_delta_percentage_points": float(
                gated["max_drawdown_percent"] - baseline["max_drawdown_percent"]
            ),
            "net_bp_per_trade_delta": float(
                gated["project_net_bp_per_trade"] - baseline["project_net_bp_per_trade"]
            ),
            "matched_excess_bp_delta": float(
                gated["candidate_minus_control_bp_per_trade"]
                - baseline["candidate_minus_control_bp_per_trade"]
            ),
        }

    payload = {
        "artifact": "single-variable ATR expansion dynamic gate rejection test",
        "decision": "reject atr_pct_ratio96 >= 1.0 as a Pine entry gate",
        "reason": (
            "it collapses 2023 compounded return and cuts 2024 return while increasing "
            "drawdown; improved first-touch prevalence did not survive the stateful ledger"
        ),
        "single_variable": "atr_pct_ratio96 >= 1.0",
        "threshold_selection": "semantic expansion boundary; no threshold grid",
        "data_quality": quality,
        "round_trip_cost": 0.002,
        "risk_per_trade_percent": 1.0,
        "matched_control": {
            "contract": "ETH x UTC month x HK 6h x prior-month ATR quintile x copied holding horizon",
            "controls_per_trade": CONTROL_COUNT,
            "assignment_seeds": CONTROL_SEED_COUNT,
            "resamples": RESAMPLES,
        },
        "results": table,
        "gate_minus_allow_all": comparisons,
        "confirmation_status": (
            "exploratory only: 2024 labels/features were already inspected in the preceding "
            "audit, so this is not a fresh confirmatory result"
        ),
        "model_trained_or_scored": False,
        "barrier_parameters_changed": False,
        "consumed_final_rows_read": 0,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.concat(all_trades, ignore_index=True).to_csv(TRADES_OUTPUT, index=False)
    pd.concat(all_controls, ignore_index=True).to_csv(CONTROLS_OUTPUT, index=False)
    pd.concat(all_pairs, ignore_index=True).to_csv(PAIRS_OUTPUT, index=False)
    pd.DataFrame(sensitivity_rows).to_csv(CONTROL_SENSITIVITY_OUTPUT, index=False)
    SUMMARY_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
