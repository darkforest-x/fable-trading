#!/usr/bin/env python3
"""Run the causal path-state correction for ETHUSDT.P 15m harvesting.

The path is classified by the first causally observable event: +8 signal ATR
MFE locks a protected super-trend, while the third completed-close pullback
threshold unlocks an exhausted ordinary thrust.  Selection changes only the
base pullback depth on 2023--2024.  No profit action mutates the stop and no
repository holdout row is parsed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import scripts.research_ethusdtp_15m_pullback_curve_harvest_v14 as base
from scripts.research_btcusdtp_15m_dual_ma_runner import BAR_DELTA
from scripts.research_ethusdtp_15m_bank_only_runner_v4 import _common_result

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-causal-path-harvest-preholdout-20260905-v15"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_causal_path_harvest(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    pullback_step_atr: float,
) -> dict[str, Any]:
    """Release tranches under a causal strong-first versus pullback-first race.

    Uses OHLC, ATR, SMA60 ``trend_ma`` and ``segment_id`` from entry through the
    current completed bar.  The favourable extreme, close pullback and path
    state use only observations available at that close.  A decision schedules
    at most one next-open partial fill.  Partial fills only change realized PnL
    and remaining size; the independent disaster/SMA60 stop state is untouched.
    """

    if pullback_step_atr <= 0.0:
        raise ValueError("pullback_step_atr must be positive")
    execution = config["frozen_execution"]
    harvest = config["pullback_harvest"]
    fractions = list(map(float, harvest["stage_fractions"]))
    multipliers = list(map(float, harvest["pullback_level_multipliers"]))
    unlock_stage = int(harvest["ordinary_unlock_stage_index"])
    if len(fractions) != len(multipliers):
        raise ValueError("stage fractions and pullback multipliers must align")
    if not 0 <= unlock_stage < len(multipliers):
        raise ValueError("ordinary unlock stage index is out of range")
    ordinary_cap = float(harvest["ordinary_total_bank_cap_fraction"])
    protected_cap = float(harvest["strong_total_bank_cap_fraction"])
    if not np.isclose(sum(fractions), ordinary_cap):
        raise ValueError("stage fractions must sum to the ordinary cap")
    if not np.isclose(sum(fractions[:unlock_stage]), protected_cap):
        raise ValueError("shallow stages must exactly fill the protected cap")

    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    horizon = int(execution["horizon_bars"])
    cost = float(execution["round_trip_cost_fraction"])
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    if int(frame.loc[end_i, "segment_id"]) != int(frame.loc[entry_i, "segment_id"]):
        return {"resolved": False, "reason": "horizon_crosses_gap"}

    hard_stop = (
        entry - direction * float(execution["initial_disaster_stop_atr"]) * signal_atr
    )
    active_stop = hard_stop
    stop_source = "hard"
    remaining = 1.0
    realized_gross = 0.0
    banked_fraction = 0.0
    processed_stages = 0
    partial_hits = 0
    skipped_stages = 0
    pending_fraction: float | None = None
    pending_stage: int | None = None
    release_prices: list[float] = []
    release_fractions: list[float] = []
    release_decision_indices: list[int] = []
    path_state = "undecided"
    path_state_i: int | None = None
    runner_armed = False
    runner_arm_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    mfe_until_exit = 0.0
    mae_until_exit = 0.0
    full_mfe = 0.0
    full_mae = 0.0

    for i in range(entry_i, end_i + 1):
        open_price = float(frame.loc[i, "open"])
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        close = float(frame.loc[i, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        full_mfe = max(full_mfe, favourable)
        full_mae = max(full_mae, adverse)
        if exit_i is not None:
            continue

        gap_through_stop = (
            open_price <= active_stop if direction > 0 else open_price >= active_stop
        )
        if gap_through_stop:
            exit_i = i
            exit_price = open_price
            outcome = f"{stop_source}_gap_stop"
            pending_fraction = None
            pending_stage = None
            continue

        if pending_fraction is not None:
            realized_gross += pending_fraction * direction * (open_price / entry - 1.0)
            remaining -= pending_fraction
            banked_fraction += pending_fraction
            release_prices.append(open_price)
            release_fractions.append(pending_fraction)
            partial_hits += 1
            processed_stages += 1
            pending_fraction = None
            pending_stage = None

        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        if hit_stop:
            exit_i = i
            exit_price = active_stop
            outcome = f"{stop_source}_stop"
            continue

        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)
        peak_atr = mfe_until_exit / signal_atr
        signed_close_atr = direction * (close - entry) / signal_atr
        pullback_atr = peak_atr - signed_close_atr

        if path_state == "undecided":
            if peak_atr >= float(harvest["strong_trend_mfe_atr"]):
                path_state = "strong_locked"
                path_state_i = i
            elif pullback_atr >= pullback_step_atr * multipliers[unlock_stage]:
                path_state = "ordinary_unlocked"
                path_state_i = i

        if not runner_armed and signed_close_atr >= float(
            execution["runner_arm_on_completed_close_atr"]
        ):
            runner_armed = True
            runner_arm_i = i

        close_is_net_profitable = direction * (close / entry - 1.0) > cost
        harvest_armed = peak_atr >= float(harvest["arm_mfe_atr"])
        if (
            harvest_armed
            and processed_stages < len(multipliers)
            and pending_fraction is None
            and close_is_net_profitable
            and i < end_i
        ):
            threshold = pullback_step_atr * multipliers[processed_stages]
            if pullback_atr >= threshold:
                cap = ordinary_cap if path_state == "ordinary_unlocked" else protected_cap
                available = max(0.0, cap - banked_fraction)
                fraction = min(fractions[processed_stages], available, remaining)
                if fraction > 1e-12:
                    pending_fraction = fraction
                    pending_stage = processed_stages
                    release_decision_indices.append(i)
                else:
                    processed_stages += 1
                    skipped_stages += 1

        if runner_armed:
            candidate_stop = float(frame.loc[i, "trend_ma"]) - direction * float(
                execution["runner_buffer_atr"]
            ) * float(frame.loc[i, "atr"])
            improves = (direction > 0 and candidate_stop > active_stop) or (
                direction < 0 and candidate_stop < active_stop
            )
            if improves:
                active_stop = candidate_stop
                stop_source = "sma60_runner"

    if exit_i is None:
        exit_i = end_i
        exit_price = float(frame.loc[end_i, "close"])
        outcome = "timeout"
    signed_remainder_return = direction * (float(exit_price) / entry - 1.0)
    gross = realized_gross + remaining * signed_remainder_return
    captured_atr = gross * entry / signal_atr
    result = {
        "resolved": True,
        "policy": "causal_path_pullback_harvest_sma60_runner",
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "runner_armed": runner_armed,
        "runner_arm_i": runner_arm_i,
        "mfe_at_exit_atr": mfe_until_exit / signal_atr,
        "mae_at_exit_atr": mae_until_exit / signal_atr,
        "horizon_mfe_atr": full_mfe / signal_atr,
        "horizon_mae_atr": full_mae / signal_atr,
        "capture_of_horizon_mfe": (
            captured_atr / (full_mfe / signal_atr) if full_mfe > 0.0 else np.nan
        ),
        "capture_of_exit_mfe": (
            captured_atr / (mfe_until_exit / signal_atr)
            if mfe_until_exit > 0.0
            else np.nan
        ),
        "gave_back_atr": mfe_until_exit / signal_atr - captured_atr,
        "bank_total_fraction": ordinary_cap,
        "banked_fraction": banked_fraction,
        "strong_observed": path_state == "strong_locked",
        "strong_locked": path_state == "strong_locked",
        "ordinary_unlocked": path_state == "ordinary_unlocked",
        "path_state": path_state,
        "path_state_i": path_state_i,
        "pullback_step_atr": pullback_step_atr,
        "step_atr": pullback_step_atr,
        "processed_stages": processed_stages,
        "partial_hits": partial_hits,
        "skipped_stages": skipped_stages,
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "release_prices_json": json.dumps(release_prices),
        "release_fractions_json": json.dumps(release_fractions),
        "release_decision_indices_json": json.dumps(release_decision_indices),
        "pending_stage_at_exit": pending_stage,
        "final_active_stop": active_stop,
        "final_profit_floor": np.nan,
        "profit_floor_gap_breach": False,
        "stop_changed_by_harvest": False,
    }
    return _common_result(event, result, cost)


def _install_base_bindings() -> None:
    """Reuse V14's frozen selection/audit harness with this causal resolver."""

    base.EXPERIMENT_ID = EXPERIMENT_ID
    base.EXPERIMENT = EXPERIMENT
    base.CONFIG_PATH = CONFIG_PATH
    base.PREREG_PATH = PREREG_PATH
    base.RESULTS = RESULTS
    base.SELECTION_PATH = SELECTION_PATH
    base.SCRIPT_PATH = SCRIPT_PATH
    base.resolve_pullback_curve_harvest = resolve_causal_path_harvest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("selection", "audit"))
    args = parser.parse_args()
    _install_base_bindings()
    config = load_config()
    if args.phase == "selection":
        base.selection_phase(config)
    else:
        base.audit_phase(config)


if __name__ == "__main__":
    main()
