#!/usr/bin/env python3
"""Select and audit a convex ETHUSDT.P 15m profit-taking ladder.

The frozen V5 entry ledger, +2/+4/+6/+8 ATR milestones, 40% total bank,
SMA60/ATR runner, disaster stop, horizon and cost do not change. Selection
changes only the exponent that redistributes the fixed bank budget toward later
milestones. A fill never changes the stop. Future bars are used only by the
bounded exit resolver; repository holdout rows are never parsed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    BAR_DELTA,
    _atr_buckets,
    _stop_fill,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    utc,
    write_csv,
    write_json,
)
from scripts.research_ethusdtp_15m_bank_only_runner_v4 import _common_result
from scripts.research_ethusdtp_15m_progressive_scaleout import (
    build_frozen_setups,
    load_eth_frame,
    resolve_baseline,
)
from scripts.research_ethusdtp_15m_progressive_scaleout_v2 import (
    fold_table,
    robust_summary,
    window,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-convex-profit-ladder-preholdout-20260905-v5"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _assert_head_frozen(path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    expected = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(expected).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_selection_committed() -> dict[str, Any]:
    _assert_head_frozen(SELECTION_PATH)
    receipt = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    if receipt.get("status") != "frozen_for_audit" or not receipt.get(
        "all_registered_gates_pass"
    ):
        raise RuntimeError(
            "selection is not committed or did not pass all audit-opening gates"
        )
    return receipt


def convex_stage_fractions(
    levels_atr: list[float], bank_total_fraction: float, weight_power: float
) -> list[float]:
    """Return normalized stage fractions using only fixed levels and exponent."""

    if not 0.0 < bank_total_fraction < 1.0:
        raise ValueError("bank_total_fraction must be strictly between zero and one")
    if weight_power < 0.0:
        raise ValueError("weight_power must be nonnegative")
    levels = np.asarray(levels_atr, dtype=float)
    if not len(levels) or np.any(levels <= 0.0) or np.any(np.diff(levels) <= 0.0):
        raise ValueError("levels_atr must be positive and strictly increasing")
    raw = levels**weight_power
    return (bank_total_fraction * raw / raw.sum()).tolist()


def resolve_convex_ladder(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    weight_power: float,
) -> dict[str, Any]:
    """Bank convex tranches while leaving the baseline runner stop untouched."""

    execution = config["frozen_execution"]
    ladder = config["profit_ladder"]
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    horizon = int(execution["horizon_bars"])
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    if int(frame.loc[end_i, "segment_id"]) != int(frame.loc[entry_i, "segment_id"]):
        return {"resolved": False, "reason": "horizon_crosses_gap"}

    levels = list(map(float, ladder["levels_atr"]))
    bank = float(ladder["bank_total_fraction"])
    fractions = convex_stage_fractions(levels, bank, weight_power)
    targets = [entry + direction * level * signal_atr for level in levels]
    hard_stop = (
        entry - direction * float(execution["initial_disaster_stop_atr"]) * signal_atr
    )
    remaining = 1.0
    realized_gross = 0.0
    partial_hits = 0
    active_stop = hard_stop
    stop_source = "hard"
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
        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)

        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        if hit_stop:
            exit_i = i
            exit_price = _stop_fill(open_price, active_stop, direction)
            outcome = f"{stop_source}_stop"
            continue

        while partial_hits < len(targets):
            target = targets[partial_hits]
            hit = high >= target if direction > 0 else low <= target
            if not hit:
                break
            fraction = fractions[partial_hits]
            realized_gross += fraction * direction * (target / entry - 1.0)
            remaining -= fraction
            partial_hits += 1

        signed_close_atr = direction * (close - entry) / signal_atr
        if not runner_armed and signed_close_atr >= float(
            execution["runner_arm_on_completed_close_atr"]
        ):
            runner_armed = True
            runner_arm_i = i
        if runner_armed:
            candidate = float(frame.loc[i, "trend_ma"]) - direction * float(
                execution["runner_buffer_atr"]
            ) * float(frame.loc[i, "atr"])
            improves = (direction > 0 and candidate > active_stop) or (
                direction < 0 and candidate < active_stop
            )
            if improves:
                active_stop = candidate
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
        "policy": "convex_four_stage_sma60_runner",
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
        "bank_total_fraction": bank,
        "weight_power": weight_power,
        "stage_fractions_json": json.dumps(fractions),
        "partial_hits": partial_hits,
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "final_active_stop": active_stop,
        "final_profit_floor": np.nan,
        "profit_floor_gap_breach": False,
    }
    return _common_result(event, result, float(execution["round_trip_cost_fraction"]))


def replay(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    weight_power: float | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        result = (
            resolve_baseline(frame, event, config)
            if weight_power is None
            else resolve_convex_ladder(frame, event, config, weight_power=weight_power)
        )
        if result.get("resolved"):
            rows.append(result)
    return pd.DataFrame(rows)


def _candidate_row(
    events: pd.DataFrame,
    baseline_summary: Mapping[str, Any],
    folds: list[str],
    config: Mapping[str, Any],
    power: float,
) -> dict[str, Any]:
    summary = robust_summary(events, folds, config)
    baseline_loss = float(baseline_summary["runner_armed_to_nonpositive_share"])
    candidate_loss = float(summary["runner_armed_to_nonpositive_share"])
    reduction = (
        (baseline_loss - candidate_loss) / baseline_loss
        if baseline_loss > 0.0
        else np.nan
    )
    return {
        "weight_power": power,
        "stage_fractions_json": events["stage_fractions_json"].iloc[0],
        **summary,
        "runner_loss_relative_reduction": reduction,
        "mean_net_delta_bp": float(
            summary["mean_net_bp"] - baseline_summary["mean_net_bp"]
        ),
        "worst_fold_degradation_bp": float(
            baseline_summary["worst_fold_net_bp"] - summary["worst_fold_net_bp"]
        ),
        "p95_net_retention": float(
            summary["p95_net_bp"] / baseline_summary["p95_net_bp"]
        ),
    }


def _passes(row: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    gates = config["selection"]["success_gates"]
    return bool(
        row["eligible"]
        and row["runner_loss_relative_reduction"]
        >= float(gates["runner_armed_to_nonpositive_relative_reduction_min"])
        and row["mean_net_delta_bp"]
        >= float(gates["candidate_minus_baseline_mean_net_bp_min"])
        and row["worst_fold_degradation_bp"]
        <= float(gates["candidate_worst_fold_degradation_bp_max"])
        and row["p95_net_retention"] >= float(gates["candidate_p95_net_retention_min"])
    )


def selection_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    split = config["splits"]
    frame, quality = load_eth_frame(
        config, end_exclusive=utc(split["development_end_exclusive"])
    )
    pairs, setups = build_frozen_setups(frame, config)
    setups = window(
        setups,
        split["development_start_inclusive"],
        split["development_end_exclusive"],
    )
    baseline = replay(setups, frame, config, weight_power=None)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    for power in map(float, config["selection"]["weight_power_candidates"]):
        events = replay(setups, frame, config, weight_power=power)
        ledgers[power] = events
        rows.append(_candidate_row(events, baseline_summary, folds, config, power))
    passing = [row for row in rows if _passes(row, config)]
    winner = (
        max(
            passing,
            key=lambda row: (
                float(row["robust_score_bp"]),
                -float(row["weight_power"]),
            ),
        )
        if passing
        else max(rows, key=lambda row: float(row["robust_score_bp"]))
    )
    power = float(winner["weight_power"])
    selected = ledgers[power]
    selected_summary = robust_summary(selected, folds, config)
    all_pass = _passes(winner, config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_power_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="convex_v5"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_params": {
            "weight_power": power,
            "stage_fractions": json.loads(str(winner["stage_fractions_json"])),
        },
        "source": quality,
        "raw_pairs": len(pairs),
        "frozen_setups": len(setups),
        "baseline": baseline_summary,
        "candidate": selected_summary,
        "selected_comparison": winner,
        "all_registered_gates_pass": all_pass,
        "audit_rows_read": 0,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(RESULTS / "selection_power_grid.csv"),
        },
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def _matched_controls(
    candidate: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    weight_power: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    horizon = int(config["frozen_execution"]["horizon_bars"])
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= utc(frame.loc[entry_i, "open_time"]) < end
            and utc(frame.loc[last_i, "open_time"] + BAR_DELTA) <= end
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
            and np.isfinite(float(frame.loc[signal_i, "trend_ma"]))
        )
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    signals = set(candidate["signal_i"].astype(int))
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & (buckets >= 0)):
        if int(index) not in signals:
            pool.setdefault(
                (str(months[index]), int(blocks[index]), int(buckets[index])), []
            ).append(int(index))
    match = config["matched_control"]
    required = int(match["controls_per_event"])
    radius = int(match["exclude_radius_bars"])
    seed = str(match["seed"])
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in candidate.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            (index for index in pool.get(key, []) if abs(index - signal_i) > radius),
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        values: list[float] = []
        for assignment, control_i in enumerate(choices[:required]):
            control_event = {
                "setup_id": f"control-{control_i}",
                "signal_i": control_i,
                "entry_i": control_i + 1,
                "entry_time": frame.loc[control_i + 1, "open_time"],
                "entry_price": float(frame.loc[control_i + 1, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[control_i, "atr"]),
            }
            result = resolve_convex_ladder(
                frame, control_event, config, weight_power=weight_power
            )
            if not result.get("resolved"):
                continue
            values.append(float(result["net_return"]))
            controls.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "control_i": control_i,
                    "control_time": frame.loc[control_i, "open_time"],
                    "direction": int(event["direction"]),
                    "calendar_month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    "net_return": result["net_return"],
                }
            )
        if len(values) == required:
            mean = float(np.mean(values))
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "matched_exact",
                    "matched_control_count": required,
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": mean,
                    "paired_excess_return": event["net_return"] - mean,
                }
            )
        else:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched",
                    "matched_control_count": len(values),
                }
            )
    return pd.DataFrame(controls), pd.DataFrame(pairs)


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    selection = _assert_selection_committed()
    power = float(selection["selected_params"]["weight_power"])
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    baseline = replay(setups, frame, config, weight_power=None)
    candidate = replay(setups, frame, config, weight_power=power)
    folds = list(map(str, split["audit_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    candidate_summary = robust_summary(candidate, folds, config)
    comparison = candidate[["setup_id", "net_return"]].merge(
        baseline[["setup_id", "net_return"]],
        on="setup_id",
        suffixes=("_candidate", "_baseline"),
    )
    comparison["delta"] = (
        comparison["net_return_candidate"] - comparison["net_return_baseline"]
    )
    controls, pairs = _matched_controls(
        candidate,
        frame,
        config,
        weight_power=power,
        start=start,
        end=end,
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "audit",
        "status": "research_only",
        "selected_params": selection["selected_params"],
        "source": quality,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "setups": len(setups),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "paired_candidate_minus_baseline": {
            "mean_delta_bp": float(comparison["delta"].mean() * 1e4),
            "median_delta_bp": float(comparison["delta"].median() * 1e4),
            "positive_delta_share": float(comparison["delta"].gt(0.0).mean()),
            "signflip_p": float(
                signflip_p(comparison["delta"], resamples=100_000, seed=90531)
            ),
        },
        "matched_random": {
            "matched_events": len(matched),
            "candidate_mean_net_bp": (
                float(matched["candidate_net_return"].mean() * 1e4)
                if len(matched)
                else np.nan
            ),
            "control_mean_net_bp": (
                float(matched["control_mean_net_return"].mean() * 1e4)
                if len(matched)
                else np.nan
            ),
            "excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
            "signflip_p": (
                float(signflip_p(excess, resamples=100_000, seed=90532))
                if len(excess)
                else np.nan
            ),
        },
        "production_or_live_changed": False,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(baseline, RESULTS / "audit_baseline_trades.csv.gz")
    write_csv(candidate, RESULTS / "audit_candidate_trades.csv.gz")
    write_csv(comparison, RESULTS / "audit_paired_exit_deltas.csv")
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "audit_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(candidate, folds).assign(policy="convex_v5"),
        RESULTS / "audit_candidate_fold_metrics.csv",
    )
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("selection", "audit"))
    args = parser.parse_args()
    config = load_config()
    if args.phase == "selection":
        selection_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
