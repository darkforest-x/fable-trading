#!/usr/bin/env python3
"""Select and audit causal pullback-curve harvesting for ETHUSDT.P 15m.

The V5 entry ledger, disaster stop, SMA60/ATR runner, horizon and 0.2% cost
stay frozen.  This experiment changes the release trigger from candle colour
to completed-close drawdown from the causally observed favourable extreme.
Selection changes only the base pullback depth on 2023--2024.  Repository
holdout rows are never parsed.
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
from scripts.research_btcusdtp_15m_dual_ma_runner import BAR_DELTA, _atr_buckets
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
from scripts.research_ethusdtp_15m_weakness_harvest_v7 import _passes
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-pullback-curve-harvest-preholdout-20260905-v14"
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
        raise RuntimeError("selection is not committed or did not pass every gate")
    return receipt


def resolve_pullback_curve_harvest(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    pullback_step_atr: float,
) -> dict[str, Any]:
    """Bank partial profit on progressively deeper completed-close pullbacks.

    Reads only OHLC, ATR, SMA60 ``trend_ma`` and ``segment_id`` from the entry
    bar through the current decision bar.  Favourable peak and peak-to-close
    pullback are measured in the signal ATR frozen before entry.  A completed
    close schedules at most one partial fill for the next open.  Profit fills
    update only realized PnL and remaining size; they never update a stop.
    """

    if pullback_step_atr <= 0.0:
        raise ValueError("pullback_step_atr must be positive")
    execution = config["frozen_execution"]
    harvest = config["pullback_harvest"]
    stage_fractions = list(map(float, harvest["stage_fractions"]))
    multipliers = list(map(float, harvest["pullback_level_multipliers"]))
    if len(stage_fractions) != len(multipliers):
        raise ValueError("stage fractions and pullback multipliers must align")
    ordinary_cap = float(harvest["ordinary_total_bank_cap_fraction"])
    strong_cap = float(harvest["strong_total_bank_cap_fraction"])
    if not np.isclose(sum(stage_fractions), ordinary_cap):
        raise ValueError("stage fractions must sum to the ordinary bank cap")

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
    strong_observed = False
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
        strong_observed = strong_observed or peak_atr >= float(
            harvest["strong_trend_mfe_atr"]
        )

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
                cap = strong_cap if strong_observed else ordinary_cap
                available = max(0.0, cap - banked_fraction)
                fraction = min(stage_fractions[processed_stages], available, remaining)
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
        "policy": "pullback_curve_harvest_sma60_runner",
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
        "strong_observed": strong_observed,
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


def replay_candidate(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    pullback_step_atr: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        result = resolve_pullback_curve_harvest(
            frame, event, config, pullback_step_atr=pullback_step_atr
        )
        if result.get("resolved"):
            rows.append(result)
    return pd.DataFrame(rows)


def replay_baseline(
    setups: pd.DataFrame, frame: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        result = resolve_baseline(frame, event, config)
        if result.get("resolved"):
            rows.append(result)
    return pd.DataFrame(rows)


def _candidate_row(
    events: pd.DataFrame,
    baseline_summary: Mapping[str, Any],
    folds: list[str],
    config: Mapping[str, Any],
    pullback_step_atr: float,
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
        "pullback_step_atr": pullback_step_atr,
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
        "strong_cap_breaches": int(
            (
                events["strong_observed"].astype(bool)
                & events["banked_fraction"].gt(
                    float(config["pullback_harvest"]["strong_total_bank_cap_fraction"])
                    + 1e-12
                )
            ).sum()
        ),
    }


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
    baseline = replay_baseline(setups, frame, config)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    for step in map(float, config["selection"]["pullback_step_atr_candidates"]):
        events = replay_candidate(setups, frame, config, pullback_step_atr=step)
        ledgers[step] = events
        rows.append(_candidate_row(events, baseline_summary, folds, config, step))
    passing = [row for row in rows if _passes(row, config)]
    winner = (
        max(
            passing,
            key=lambda row: (
                float(row["robust_score_bp"]),
                float(row["pullback_step_atr"]),
            ),
        )
        if passing
        else max(rows, key=lambda row: float(row["robust_score_bp"]))
    )
    step = float(winner["pullback_step_atr"])
    selected = ledgers[step]
    selected_summary = robust_summary(selected, folds, config)
    all_pass = _passes(winner, config) and int(winner["strong_cap_breaches"]) == 0
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_pullback_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="pullback_curve_v14"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_params": {
            "pullback_step_atr": step,
            "arm_mfe_atr": config["pullback_harvest"]["arm_mfe_atr"],
            "stage_fractions": config["pullback_harvest"]["stage_fractions"],
            "pullback_level_multipliers": config["pullback_harvest"][
                "pullback_level_multipliers"
            ],
            "strong_trend_mfe_atr": config["pullback_harvest"][
                "strong_trend_mfe_atr"
            ],
            "strong_total_bank_cap_fraction": config["pullback_harvest"][
                "strong_total_bank_cap_fraction"
            ],
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
            "grid_sha256": sha256_file(RESULTS / "selection_pullback_grid.csv"),
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
    pullback_step_atr: float,
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
        direction = int(event["direction"])
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
                "direction": direction,
                "signal_atr": float(frame.loc[control_i, "atr"]),
            }
            result = resolve_pullback_curve_harvest(
                frame,
                control_event,
                config,
                pullback_step_atr=pullback_step_atr,
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
                    "direction": direction,
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
    step = float(selection["selected_params"]["pullback_step_atr"])
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    baseline = replay_baseline(setups, frame, config)
    candidate = replay_candidate(setups, frame, config, pullback_step_atr=step)
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
    controls, matched_pairs = _matched_controls(
        candidate,
        frame,
        config,
        pullback_step_atr=step,
        start=start,
        end=end,
    )
    matched = matched_pairs[matched_pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    strong = candidate["strong_observed"].astype(bool)
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "audit",
        "status": "research_only",
        "audit_is_not_pristine": True,
        "selected_params": selection["selected_params"],
        "source": quality,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "setups": len(setups),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "strong_trend_contract": {
            "events": int(strong.sum()),
            "maximum_banked_fraction": (
                float(candidate.loc[strong, "banked_fraction"].max())
                if strong.any()
                else np.nan
            ),
            "cap_breaches": int(
                candidate.loc[strong, "banked_fraction"]
                .gt(
                    float(config["pullback_harvest"]["strong_total_bank_cap_fraction"])
                    + 1e-12
                )
                .sum()
            ),
        },
        "paired_candidate_minus_baseline": {
            "mean_delta_bp": float(comparison["delta"].mean() * 1e4),
            "median_delta_bp": float(comparison["delta"].median() * 1e4),
            "positive_delta_share": float(comparison["delta"].gt(0.0).mean()),
            "signflip_p": float(
                signflip_p(comparison["delta"], resamples=100_000, seed=90621)
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
                float(signflip_p(excess, resamples=100_000, seed=90622))
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
    write_csv(matched_pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "audit_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(candidate, folds).assign(policy="pullback_curve_v14"),
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
