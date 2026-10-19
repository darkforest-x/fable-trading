#!/usr/bin/env python3
"""Optimize two cross-count factor families and staged TP/SL for Pine ETH 15m.

Owner authorization: the 2026-08-21 conversation explicitly authorized a
development-only search for the strategy's best trend-style TP/SL and asked to
test recent golden/death-cross counts.  The search is deliberately staged:

1. independently audit side-aligned cross-imbalance gates for the project's
   seven-EMA judgment bundle and the owner's SMA/EMA 20/60/120 six-line bundle;
2. with the original stop fixed, select a fixed TP including ``None`` and 30%;
3. with TP fixed, select ATR stop multiple;
4. with both fixed, select the percentage stop cap.

Every selection uses only 2023H1/2023H2 and maximizes the worse half-year
compounded return (then worse unit expectancy and drawdown as tie-breakers).
2024H1/2024H2 is reported only after each selection is locked; it is an already
inspected development check, not fresh OOS.  No row at or after 2025-01-01 is
used, and the repository holdout (>=2026-05-04) is never read.

Fixed TP is active from next-open entry.  Same-15m stop/target collisions use
the replay's explicit conservative stop-first rule and are counted.  The
round-trip cost, ATR floor, break-even, reversal, cooldown and 1% risk sizing
remain frozen.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.prepare_pine_eth_15m_gate_surface import build_candidate_surface
from scripts.prepare_pine_eth_15m_judgment_research import load_development_features
from scripts.replay_pine_eth_15m_judgment_gate import apply_gate_to_frame
from scripts.research_pine_eth_15m import (
    INITIAL_CAPITAL,
    Period,
    block_signflip,
    build_matched_controls,
    exact_execution,
    pair_controls,
    summarize,
)
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l2_judgment.pine_cross_features import (
    add_cross_count_features,
    add_six_ma_cross_count_features,
    side_aligned_cross_frame,
    side_aligned_six_ma_cross_frame,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    SignalParameters,
    simulate_symbol,
)


PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / "experiments/active/exp-pine-eth-15m-v1/results"
SUMMARY_OUTPUT = RESULTS / "cross_count_tbsl_optimization.json"
CANDIDATE_OUTPUT = RESULTS / "cross_count_candidate_features.csv"
CROSS_SEARCH_OUTPUT = RESULTS / "cross_count_gate_search.csv"
TBSL_SEARCH_OUTPUT = RESULTS / "trend_tbsl_staged_search.csv"
FINAL_TRADES_OUTPUT = RESULTS / "cross_count_tbsl_final_trades.csv"
FINAL_CONTROLS_OUTPUT = RESULTS / "cross_count_tbsl_final_controls.csv"
FINAL_PAIRS_OUTPUT = RESULTS / "cross_count_tbsl_final_pairs.csv"
CONTROL_SENSITIVITY_OUTPUT = RESULTS / "cross_count_tbsl_control_sensitivity.csv"

WINDOWS = (8, 16, 32)
EMA7_CROSS_THRESHOLDS = {
    8: (1, 2, 3, 4),
    16: (0, 1, 2, 3),
    32: (0, 1, 2, 3),
}
MA6_CROSS_THRESHOLDS = {
    8: (0, 1, 2, 3, 4, 6),
    16: (0, 1, 2, 3, 4, 6),
    32: (0, 1, 2, 3, 4, 6),
}
CROSS_FAMILIES = {
    "ema7_adjacent": (side_aligned_cross_frame, EMA7_CROSS_THRESHOLDS),
    "ma6_directional_pairs": (side_aligned_six_ma_cross_frame, MA6_CROSS_THRESHOLDS),
}
TAKE_PROFIT_GRID = (None, 2.5, 5.0, 8.0, 12.0, 20.0, 30.0, 40.0, 50.0)
ATR_MULT_GRID = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0)
MAX_SL_GRID = (2.0, 3.0, 4.0, 5.0, 6.0)
CONTROL_SEEDS = 32
CONTROLS_PER_TRADE = 3
RESAMPLES = 20_000

BLOCKS = (
    Period("2023H1", pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2023-07-01", tz="UTC")),
    Period("2023H2", pd.Timestamp("2023-07-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    Period("2024H1", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-07-01", tz="UTC")),
    Period("2024H2", pd.Timestamp("2024-07-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
)
ANNUAL = (
    Period("2023", pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    Period("2024", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
)


def _standard_arm(name: str) -> Arm:
    return Arm(
        name=name,
        signal_kind="v7",
        sizing_kind="risk",
        risk_per_trade_percent=1.0,
        max_leverage=13.0,
        time_boosts=False,
        skip_logic=True,
        use_break_even=True,
        use_trailing_stop=False,
        opposite_signal_action="reverse",
        entry_directions=(-1, 1),
    )


def build_candidate_cross_features(
    featured: pd.DataFrame,
    surface: pd.DataFrame,
) -> pd.DataFrame:
    """Attach both side-aligned cross families at each confirmed signal bar."""

    rows = surface[["candidate_id", "side", "signal_i", "signal_time"]].copy()
    for family, (aligner, _) in CROSS_FAMILIES.items():
        for window in WINDOWS:
            for column in (
                "directional_cross_count",
                "opposite_cross_count",
                "cross_imbalance",
                "cross_churn",
                "directional_cross_breadth",
                "opposite_cross_breadth",
                "current_alignment",
            ):
                rows[f"{family}_{column}_{window}"] = np.nan
            for side in ("long", "short"):
                mask = rows["side"].eq(side)
                indices = rows.loc[mask, "signal_i"].astype(int).to_numpy()
                aligned = aligner(featured, window=window, side=side)
                for column in aligned.columns:
                    rows.loc[mask, f"{family}_{column}_{window}"] = (
                        aligned.iloc[indices][column].to_numpy()
                    )
    signal_indices = rows["signal_i"].astype(int).to_numpy()
    rows["ma6_bandwidth"] = featured.iloc[signal_indices]["six_ma_bandwidth"].to_numpy()
    rows["ma6_bandwidth_change_4"] = (
        featured.iloc[signal_indices]["six_ma_bandwidth_change_4"].to_numpy()
    )
    numeric = [column for column in rows if column not in {"candidate_id", "side", "signal_time"}]
    if not np.isfinite(rows[numeric].to_numpy(dtype=float)).all():
        raise RuntimeError("candidate cross-count surface contains non-finite values")
    return rows


def gate_spec(
    family: str | None,
    window: int | None,
    threshold: int | None,
) -> dict[str, Any]:
    if window is None:
        gate_id = "allow_all" if family is None else f"{family}_allow_all"
        return {
            "gate_id": gate_id,
            "family": family,
            "window": None,
            "threshold": None,
        }
    if family not in CROSS_FAMILIES:
        raise ValueError(f"unknown cross family: {family!r}")
    return {
        "gate_id": f"{family}_cross_imbalance_w{window}_gte_{threshold}",
        "family": family,
        "window": int(window),
        "threshold": int(threshold),
    }


def joined_for_gate(
    candidates: pd.DataFrame,
    spec: dict[str, Any],
) -> pd.DataFrame:
    """Return the raw guarded surface plus one deterministic gate decision."""

    joined = candidates.copy()
    if spec["window"] is None:
        joined["score"] = 1.0
        joined["gate_pass"] = True
    else:
        column = f"{spec['family']}_cross_imbalance_{int(spec['window'])}"
        joined["score"] = joined[column].astype(float)
        joined["gate_pass"] = joined[column].ge(int(spec["threshold"]))
    return joined


def run_replay(
    featured: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    spec: dict[str, Any],
    period: Period,
    params: SignalParameters,
    take_profit_percent: float | None,
    arm_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    """Run a gate and barrier configuration through the full V9 state machine."""

    joined = joined_for_gate(candidates, spec)
    gated = apply_gate_to_frame(featured, joined)
    execution = replace(
        exact_execution(equity_frequency=None),
        take_profit_percent=take_profit_percent,
        barrier_collision_policy="stop_first",
    )
    trades, marked = simulate_symbol(
        gated,
        symbol="ETH_USDT_SWAP",
        arm=_standard_arm(arm_name),
        start=period.start,
        end=period.end,
        params=params,
        round_trip_cost=0.002,
        initial_capital=INITIAL_CAPITAL,
        execution=execution,
        signal_columns=("judgment_gate_long", "judgment_gate_short", "judgment_gate_score"),
    )
    in_period = pd.to_datetime(joined["signal_time"], utc=True).ge(period.start) & pd.to_datetime(
        joined["signal_time"], utc=True
    ).lt(period.end)
    raw_candidates = int(in_period.sum())
    accepted_candidates = int((in_period & joined["gate_pass"].astype(bool)).sum())
    if not trades.empty:
        trades = trades.copy()
        trades["trade_id"] = [
            f"{arm_name}|{period.name}|{int(row.signal_i)}|{int(row.entry_i)}|{row.direction}"
            for row in trades.itertuples(index=False)
        ]
    return trades, marked, raw_candidates, accepted_candidates


def summarize_replay(
    trades: pd.DataFrame,
    marked: pd.DataFrame,
    *,
    arm_name: str,
    period: Period,
    raw_candidates: int,
    accepted_candidates: int,
) -> dict[str, Any]:
    row = summarize(
        trades,
        marked,
        variant=arm_name,
        period=period.name,
        risk_percent=1.0,
    )
    row["raw_candidates"] = raw_candidates
    row["accepted_candidates"] = accepted_candidates
    row["acceptance_rate"] = accepted_candidates / raw_candidates if raw_candidates else np.nan
    if trades.empty:
        row.update(
            {
                "intrabar_barrier_collisions": 0,
                "largest_trade_share_of_unit_net": np.nan,
                "without_largest_trade_net_bp": np.nan,
                "exit_reasons": {},
            }
        )
        return row
    values = trades["project_net_return"].sort_values(ascending=False)
    total = float(values.sum())
    row["intrabar_barrier_collisions"] = int(
        trades["intrabar_barrier_collision"].astype(bool).sum()
    )
    row["largest_trade_share_of_unit_net"] = (
        np.nan if np.isclose(total, 0.0) else float(values.iloc[0] / total)
    )
    row["without_largest_trade_net_bp"] = (
        np.nan if len(values) < 2 else float(values.iloc[1:].mean() * 10_000.0)
    )
    row["exit_reasons"] = {
        str(key): int(value) for key, value in trades["exit_reason"].value_counts().items()
    }
    return row


def _select_on_2023(table: pd.DataFrame, id_column: str) -> tuple[str, dict[str, Any]]:
    """Select by worst 2023 half-year return without consulting any 2024 row."""

    calibration = table.loc[table["period"].isin(["2023H1", "2023H2"])].copy()
    records: list[dict[str, Any]] = []
    for config_id, group in calibration.groupby(id_column, sort=False):
        if set(group["period"]) != {"2023H1", "2023H2"}:
            continue
        records.append(
            {
                id_column: config_id,
                "worst_2023_half_return_percent": float(group["return_percent"].min()),
                "worst_2023_half_net_bp": float(group["project_net_bp_per_trade"].min()),
                "worst_2023_half_pf": float(group["monetary_profit_factor"].min()),
                "max_2023_half_drawdown_percent": float(group["max_drawdown_15m_percent"].max()),
                "min_2023_half_trades": int(group["trades"].min()),
                "mean_2023_half_return_percent": float(group["return_percent"].mean()),
            }
        )
    ranking = pd.DataFrame(records)
    if ranking.empty:
        raise RuntimeError(f"no complete 2023 calibration rows for {id_column}")
    eligible = ranking.loc[ranking["min_2023_half_trades"].ge(15)].copy()
    if eligible.empty:
        raise RuntimeError(f"all {id_column} configurations fail the 15-trade calibration floor")
    eligible = eligible.sort_values(
        [
            "worst_2023_half_return_percent",
            "worst_2023_half_net_bp",
            "max_2023_half_drawdown_percent",
            "mean_2023_half_return_percent",
        ],
        ascending=[False, False, True, False],
        kind="stable",
    )
    winner = eligible.iloc[0].to_dict()
    return str(winner[id_column]), winner


def evaluate_config(
    featured: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    spec: dict[str, Any],
    params: SignalParameters,
    take_profit_percent: float | None,
    arm_name: str,
) -> list[dict[str, Any]]:
    rows = []
    for period in BLOCKS:
        trades, marked, raw_count, accepted_count = run_replay(
            featured,
            candidates,
            spec=spec,
            period=period,
            params=params,
            take_profit_percent=take_profit_percent,
            arm_name=arm_name,
        )
        rows.append(
            summarize_replay(
                trades,
                marked,
                arm_name=arm_name,
                period=period,
                raw_candidates=raw_count,
                accepted_candidates=accepted_count,
            )
        )
    return rows


def matched_final_evaluation(
    featured: pd.DataFrame,
    candidates: pd.DataFrame,
    final_configs: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Evaluate final arms with exact matched controls and assignment sensitivity."""

    all_trades: list[pd.DataFrame] = []
    all_controls: list[pd.DataFrame] = []
    all_pairs: list[pd.DataFrame] = []
    sensitivity_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for config in final_configs:
        for period in ANNUAL:
            trades, marked, raw_count, accepted_count = run_replay(
                featured,
                candidates,
                spec=config["gate"],
                period=period,
                params=config["params"],
                take_profit_percent=config["take_profit_percent"],
                arm_name=config["arm"],
            )
            anchor_controls = pd.DataFrame()
            anchor_pairs = pd.DataFrame()
            for seed in range(CONTROL_SEEDS):
                controls = build_matched_controls(
                    featured,
                    trades,
                    period,
                    controls_per_trade=CONTROLS_PER_TRADE,
                    seed=f"cross-count-tbsl-v1|{config['arm']}|{period.name}|{seed:02d}",
                    params=config["params"],
                    take_profit_percent=config["take_profit_percent"],
                )
                pairs = pair_controls(trades, controls)
                sensitivity_rows.append(
                    {
                        "arm": config["arm"],
                        "period": period.name,
                        "control_seed": seed,
                        "control_net_bp_per_trade": float(
                            pairs["control_mean_project_net"].mean() * 10_000.0
                        ),
                        "candidate_minus_control_bp_per_trade": float(
                            pairs["excess_return"].mean() * 10_000.0
                        ),
                    }
                )
                if seed == 0:
                    anchor_controls = controls
                    anchor_pairs = pairs
            anchor_controls = anchor_controls.copy()
            anchor_pairs = anchor_pairs.copy()
            anchor_controls["arm"] = config["arm"]
            anchor_controls["period"] = period.name
            anchor_pairs["arm"] = config["arm"]
            anchor_pairs["period"] = period.name
            trades = trades.copy()
            trades["final_arm"] = config["arm"]
            trades["period"] = period.name

            row = summarize_replay(
                trades,
                marked,
                arm_name=config["arm"],
                period=period,
                raw_candidates=raw_count,
                accepted_candidates=accepted_count,
            )
            signflip = block_signflip(
                anchor_pairs,
                n_resamples=RESAMPLES,
                seed=20_260_821 + int(period.name) + len(summaries),
            )
            local_sensitivity = pd.DataFrame(sensitivity_rows)
            local_sensitivity = local_sensitivity.loc[
                local_sensitivity["arm"].eq(config["arm"])
                & local_sensitivity["period"].eq(period.name)
            ]
            excess = local_sensitivity["candidate_minus_control_bp_per_trade"]
            row.update(
                {
                    "matched_control_net_bp_per_trade": float(
                        anchor_pairs["control_mean_project_net"].mean() * 10_000.0
                    ),
                    "candidate_minus_control_bp_per_trade": float(
                        anchor_pairs["excess_return"].mean() * 10_000.0
                    ),
                    "week_block_signflip_p_one_sided": float(signflip["p_value"]),
                    "control_seed_excess_median_bp": float(excess.median()),
                    "control_seed_excess_p05_bp": float(excess.quantile(0.05)),
                    "control_seed_excess_p95_bp": float(excess.quantile(0.95)),
                    "positive_excess_seed_fraction": float(excess.gt(0.0).mean()),
                    "take_profit_percent": config["take_profit_percent"],
                    "atr_mult": config["params"].atr_mult,
                    "max_sl_percent": config["params"].max_sl_percent,
                    "gate_id": config["gate"]["gate_id"],
                }
            )
            summaries.append(row)
            all_trades.append(trades)
            all_controls.append(anchor_controls)
            all_pairs.append(anchor_pairs)
    return (
        pd.concat(all_trades, ignore_index=True),
        pd.concat(all_controls, ignore_index=True),
        pd.concat(all_pairs, ignore_index=True),
        pd.DataFrame(sensitivity_rows),
        summaries,
    )


def main() -> None:
    featured, quality = load_development_features()
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError("cross-count/TP-SL optimizer crossed the development boundary")
    featured = add_six_mas(featured)
    featured = add_cross_count_features(featured, windows=WINDOWS)
    featured = add_six_ma_cross_count_features(featured, windows=WINDOWS)
    surface = build_candidate_surface(featured)
    candidates = build_candidate_cross_features(featured, surface)
    if len(candidates) != 335:
        raise RuntimeError(f"expected frozen 335-candidate surface, got {len(candidates)}")

    label_path = RESULTS / "judgment_start_label_rows.csv"
    if label_path.exists():
        labels = pd.read_csv(label_path, usecols=["candidate_id", "start_label"])
        candidates = candidates.merge(labels, on="candidate_id", how="left", validate="one_to_one")

    default_params = SignalParameters()

    cross_rows: list[dict[str, Any]] = []
    family_specs: dict[str, list[dict[str, Any]]] = {}
    for family, (_, thresholds) in CROSS_FAMILIES.items():
        specs = [gate_spec(family, None, None)] + [
            gate_spec(family, window, threshold)
            for window in WINDOWS
            for threshold in thresholds[window]
        ]
        family_specs[family] = specs
        for spec in specs:
            evaluated = evaluate_config(
                featured,
                candidates,
                spec=spec,
                params=default_params,
                take_profit_percent=None,
                arm_name=spec["gate_id"],
            )
            for row in evaluated:
                row.update(spec)
                cross_rows.append(row)
    cross_table = pd.DataFrame(cross_rows)
    selected_gates: dict[str, dict[str, Any]] = {}
    for family, specs in family_specs.items():
        family_table = cross_table.loc[cross_table["family"].eq(family)]
        winner_id, winner_audit = _select_on_2023(family_table, "gate_id")
        active_table = family_table.loc[family_table["window"].notna()]
        active_id, active_audit = _select_on_2023(active_table, "gate_id")
        runner_table = active_table.loc[~active_table["gate_id"].eq(active_id)]
        runner_id, runner_audit = _select_on_2023(runner_table, "gate_id")
        selected_gates[family] = {
            "winner": next(spec for spec in specs if spec["gate_id"] == winner_id),
            "winner_calibration": winner_audit,
            "best_active": next(spec for spec in specs if spec["gate_id"] == active_id),
            "best_active_calibration": active_audit,
            "runner_up_active": next(spec for spec in specs if spec["gate_id"] == runner_id),
            "runner_up_active_calibration": runner_audit,
        }

    tbsl_rows: list[dict[str, Any]] = []
    for take_profit in TAKE_PROFIT_GRID:
        config_id = "tp_none" if take_profit is None else f"tp_{take_profit:g}"
        evaluated = evaluate_config(
            featured,
            candidates,
            spec=gate_spec(None, None, None),
            params=default_params,
            take_profit_percent=take_profit,
            arm_name=config_id,
        )
        for row in evaluated:
            row.update(
                {
                    "stage": "take_profit",
                    "config_id": config_id,
                    "take_profit_percent": take_profit,
                    "atr_mult": default_params.atr_mult,
                    "max_sl_percent": default_params.max_sl_percent,
                }
            )
            tbsl_rows.append(row)
    tbsl_table = pd.DataFrame(tbsl_rows)
    selected_tp_id, selected_tp_audit = _select_on_2023(tbsl_table, "config_id")
    selected_tp_row = tbsl_table.loc[tbsl_table["config_id"].eq(selected_tp_id)].iloc[0]
    selected_tp = (
        None
        if selected_tp_id == "tp_none"
        else float(selected_tp_row["take_profit_percent"])
    )

    atr_rows: list[dict[str, Any]] = []
    for atr_mult in ATR_MULT_GRID:
        params = replace(default_params, atr_mult=atr_mult)
        config_id = f"atr_{atr_mult:g}"
        evaluated = evaluate_config(
            featured,
            candidates,
            spec=gate_spec(None, None, None),
            params=params,
            take_profit_percent=selected_tp,
            arm_name=config_id,
        )
        for row in evaluated:
            row.update(
                {
                    "stage": "atr_mult",
                    "config_id": config_id,
                    "take_profit_percent": selected_tp,
                    "atr_mult": atr_mult,
                    "max_sl_percent": default_params.max_sl_percent,
                }
            )
            atr_rows.append(row)
    atr_table = pd.DataFrame(atr_rows)
    selected_atr_id, selected_atr_audit = _select_on_2023(atr_table, "config_id")
    selected_atr = float(
        atr_table.loc[atr_table["config_id"].eq(selected_atr_id), "atr_mult"].iloc[0]
    )

    cap_rows: list[dict[str, Any]] = []
    for max_sl in MAX_SL_GRID:
        params = replace(default_params, atr_mult=selected_atr, max_sl_percent=max_sl)
        config_id = f"cap_{max_sl:g}"
        evaluated = evaluate_config(
            featured,
            candidates,
            spec=gate_spec(None, None, None),
            params=params,
            take_profit_percent=selected_tp,
            arm_name=config_id,
        )
        for row in evaluated:
            row.update(
                {
                    "stage": "max_sl_percent",
                    "config_id": config_id,
                    "take_profit_percent": selected_tp,
                    "atr_mult": selected_atr,
                    "max_sl_percent": max_sl,
                }
            )
            cap_rows.append(row)
    cap_table = pd.DataFrame(cap_rows)
    selected_cap_id, selected_cap_audit = _select_on_2023(cap_table, "config_id")
    selected_cap = float(
        cap_table.loc[cap_table["config_id"].eq(selected_cap_id), "max_sl_percent"].iloc[0]
    )
    selected_params = replace(
        default_params,
        atr_mult=selected_atr,
        max_sl_percent=selected_cap,
    )

    final_configs = [
        {
            "arm": "baseline_v9",
            "gate": gate_spec(None, None, None),
            "params": default_params,
            "take_profit_percent": None,
        },
        {
            "arm": "ema7_cross_gate_only",
            "gate": selected_gates["ema7_adjacent"]["best_active"],
            "params": default_params,
            "take_profit_percent": None,
        },
        {
            "arm": "ma6_cross_gate_only",
            "gate": selected_gates["ma6_directional_pairs"]["best_active"],
            "params": default_params,
            "take_profit_percent": None,
        },
        {
            "arm": "ma6_runner_up_gate_only",
            "gate": selected_gates["ma6_directional_pairs"]["runner_up_active"],
            "params": default_params,
            "take_profit_percent": None,
        },
        {
            "arm": "tbsl_only",
            "gate": gate_spec(None, None, None),
            "params": selected_params,
            "take_profit_percent": selected_tp,
        },
    ]
    trades, controls, pairs, sensitivity, final_summaries = matched_final_evaluation(
        featured,
        candidates,
        final_configs,
    )

    full_tbsl_table = pd.concat([tbsl_table, atr_table, cap_table], ignore_index=True)
    selection = {
        "cross_gates": selected_gates,
        "take_profit": {
            "selected_percent": selected_tp,
            "selected_config_id": selected_tp_id,
            "calibration": selected_tp_audit,
        },
        "atr_mult": {
            "selected": selected_atr,
            "selected_config_id": selected_atr_id,
            "calibration": selected_atr_audit,
        },
        "max_sl_percent": {
            "selected": selected_cap,
            "selected_config_id": selected_cap_id,
            "calibration": selected_cap_audit,
        },
    }
    payload = {
        "artifact": "Pine ETH 15m true-six-MA/adjacent-EMA crosses plus staged trend TP/SL",
        "status": "development-only exploratory optimization; owner-authorized barriers; not production eligible",
        "owner_approval": (
            "2026-08-21 conversation: owner asked Codex to find the best TP/SL for the trend strategy "
            "and directly test a golden/death-cross-count factor"
        ),
        "selection_contract": (
            "each stage maximizes worst 2023 half-year compounded return, then worst unit net bp, "
            "then lower max drawdown; minimum 15 trades per half; 2024 never selects"
        ),
        "cross_factor_contract": {
            "ema7_adjacent": {
                "bundle": [8, 13, 21, 34, 55, 144, 200],
                "relations": 6,
                "thresholds": {
                    str(key): list(value) for key, value in EMA7_CROSS_THRESHOLDS.items()
                },
            },
            "ma6_directional_pairs": {
                "bundle": ["SMA20", "EMA20", "SMA60", "EMA60", "SMA120", "EMA120"],
                "relations": 12,
                "excluded_relations": "same-period SMA/EMA pairs have no fast/slow direction",
                "thresholds": {
                    str(key): list(value) for key, value in MA6_CROSS_THRESHOLDS.items()
                },
            },
            "factor": "side-consistent cross events minus opposite cross events",
            "windows": list(WINDOWS),
            "existing_factor_distinction": (
                "existing order_score counts current ordering; these factors count actual recent crosses"
            ),
        },
        "tbsl_grids": {
            "take_profit_percent": list(TAKE_PROFIT_GRID),
            "atr_mult": list(ATR_MULT_GRID),
            "max_sl_percent": list(MAX_SL_GRID),
        },
        "frozen": {
            "bar_minutes": 15,
            "round_trip_cost": 0.002,
            "risk_per_trade_percent": 1.0,
            "break_even_trigger_percent": default_params.break_even_trigger_percent,
            "break_even_offset_percent": default_params.break_even_offset_percent,
            "cooldown": True,
            "opposite_signal_action": "reverse",
            "atr_floor_changed": False,
        },
        "barrier_ordering": (
            "next-open entry; stop/target live immediately; open gaps first; unresolved same-15m "
            "double touch is stop-first and counted; BE updates after a completed bar for the next bar"
        ),
        "data_quality": quality,
        "candidate_rows": int(len(candidates)),
        "selection": selection,
        "final_results": final_summaries,
        "matched_control": {
            "contract": "ETH x UTC month x HK 6h x prior-month ATR quintile x copied horizon",
            "controls_per_trade": CONTROLS_PER_TRADE,
            "assignment_seeds": CONTROL_SEEDS,
            "week_signflip_resamples": RESAMPLES,
        },
        "model_trained_or_scored": False,
        "consumed_final_rows_used": 0,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(CANDIDATE_OUTPUT, index=False)
    cross_table.to_csv(CROSS_SEARCH_OUTPUT, index=False)
    full_tbsl_table.to_csv(TBSL_SEARCH_OUTPUT, index=False)
    trades.to_csv(FINAL_TRADES_OUTPUT, index=False)
    controls.to_csv(FINAL_CONTROLS_OUTPUT, index=False)
    pairs.to_csv(FINAL_PAIRS_OUTPUT, index=False)
    sensitivity.to_csv(CONTROL_SENSITIVITY_OUTPUT, index=False)
    SUMMARY_OUTPUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
