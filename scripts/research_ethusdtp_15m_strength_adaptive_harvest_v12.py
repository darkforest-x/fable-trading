#!/usr/bin/env python3
"""Select and audit strength-adaptive ETHUSDT.P 15m profit harvesting.

The V11 two-adverse-candle trigger and 20/10/5/5% base release schedule stay
frozen. If +8 ATR has already been observed at a release decision, that stage
is multiplied by the selected strong-trend factor. The factor is frozen at the
completed close and filled next open. Profit releases never change the disaster
or SMA60/ATR runner stop. Repository holdout rows are never parsed.
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
from scripts.research_ethusdtp_15m_streak_harvest_v11 import streak_exit_frames
from scripts.research_ethusdtp_15m_weakness_harvest_v7 import _passes
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-strength-adaptive-harvest-preholdout-20260905-v12"
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


def resolve_strength_adaptive_harvest(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    strong_multiplier: float,
) -> dict[str, Any]:
    """Release less size after causally observing a +8 ATR super-trend.

    Reads OHLC, ATR, synthetic causal streak ``reference_ma``, and SMA60
    ``trend_ma`` from entry through the fixed horizon. Earned slots and strong
    status use only current/past highs or lows. A completed decision close
    freezes the next-open release fraction; later bars cannot revise it.
    """

    if not 0.0 <= strong_multiplier <= 1.0:
        raise ValueError("strong_multiplier must be between zero and one")
    execution = config["frozen_execution"]
    harvest = config["adaptive_harvest"]
    levels = list(map(float, harvest["earned_slot_levels_atr"]))
    base_fractions = list(map(float, harvest["base_stage_fractions"]))
    strong_slots = int(harvest["strong_trend_min_earned_slots"])
    if len(levels) != len(base_fractions) or not np.isclose(sum(base_fractions), 0.4):
        raise ValueError("adaptive levels and frozen 40% base schedule disagree")
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
    earned_slots = 0
    processed_slots = 0
    partial_hits = 0
    pending_fraction: float | None = None
    pending_was_discounted = False
    release_prices: list[float] = []
    release_fractions: list[float] = []
    discounted_releases = 0
    skipped_releases = 0
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
            continue

        if pending_fraction is not None:
            realized_gross += pending_fraction * direction * (open_price / entry - 1.0)
            remaining -= pending_fraction
            release_prices.append(open_price)
            release_fractions.append(pending_fraction)
            partial_hits += 1
            processed_slots += 1
            if pending_was_discounted:
                discounted_releases += 1
            pending_fraction = None
            pending_was_discounted = False

        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        if hit_stop:
            exit_i = i
            exit_price = active_stop
            outcome = f"{stop_source}_stop"
            continue

        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)
        excursion_atr = favourable / signal_atr
        while earned_slots < len(levels) and excursion_atr >= levels[earned_slots]:
            earned_slots += 1

        signed_close_atr = direction * (close - entry) / signal_atr
        if not runner_armed and signed_close_atr >= float(
            execution["runner_arm_on_completed_close_atr"]
        ):
            runner_armed = True
            runner_arm_i = i

        qualifying_streak = (
            direction * (close - float(frame.loc[i, "reference_ma"])) <= 0.0
        )
        close_is_net_profitable = direction * (close / entry - 1.0) > cost
        if (
            earned_slots > processed_slots
            and pending_fraction is None
            and qualifying_streak
            and close_is_net_profitable
            and i < end_i
        ):
            stage = processed_slots
            strong = earned_slots >= strong_slots
            fraction = base_fractions[stage] * (strong_multiplier if strong else 1.0)
            if fraction > 0.0:
                pending_fraction = fraction
                pending_was_discounted = strong and strong_multiplier < 1.0
            else:
                processed_slots += 1
                skipped_releases += 1

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
        "policy": "strength_adaptive_streak_harvest_sma60_runner",
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
        "bank_total_fraction": sum(base_fractions),
        "strong_multiplier": strong_multiplier,
        "step_atr": np.nan,
        "earned_slots": earned_slots,
        "processed_slots": processed_slots,
        "partial_hits": partial_hits,
        "discounted_releases": discounted_releases,
        "skipped_releases": skipped_releases,
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "release_prices_json": json.dumps(release_prices),
        "release_fractions_json": json.dumps(release_fractions),
        "final_active_stop": active_stop,
        "final_profit_floor": np.nan,
        "profit_floor_gap_breach": False,
    }
    return _common_result(event, result, cost)


def replay_candidate(
    setups: pd.DataFrame,
    frames: Mapping[int, pd.DataFrame],
    config: Mapping[str, Any],
    *,
    multiplier: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        result = resolve_strength_adaptive_harvest(
            frames[int(event["direction"])],
            event,
            config,
            strong_multiplier=multiplier,
        )
        if result.get("resolved"):
            rows.append(result)
    return pd.DataFrame(rows)


def replay_baseline(
    setups: pd.DataFrame, frame: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    rows = []
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
    multiplier: float,
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
        "strong_trend_release_multiplier": multiplier,
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
        "discounted_release_events": int(events["discounted_releases"].gt(0).sum()),
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
    streak = int(config["adaptive_harvest"]["adverse_color_streak_bars"])
    frames = streak_exit_frames(frame, streak)
    baseline = replay_baseline(setups, frame, config)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    candidates = config["selection"]["strong_trend_release_multiplier_candidates"]
    for multiplier in map(float, candidates):
        events = replay_candidate(setups, frames, config, multiplier=multiplier)
        ledgers[multiplier] = events
        rows.append(_candidate_row(events, baseline_summary, folds, config, multiplier))
    passing = [row for row in rows if _passes(row, config)]
    winner = (
        max(
            passing,
            key=lambda row: (
                float(row["robust_score_bp"]),
                -float(row["strong_trend_release_multiplier"]),
            ),
        )
        if passing
        else max(rows, key=lambda row: float(row["robust_score_bp"]))
    )
    multiplier = float(winner["strong_trend_release_multiplier"])
    selected = ledgers[multiplier]
    selected_summary = robust_summary(selected, folds, config)
    all_pass = _passes(winner, config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_multiplier_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="strength_adaptive_v12"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_params": {
            "strong_trend_release_multiplier": multiplier,
            "strong_trend_min_earned_slots": config["adaptive_harvest"][
                "strong_trend_min_earned_slots"
            ],
            "adverse_color_streak_bars": streak,
            "base_stage_fractions": config["adaptive_harvest"]["base_stage_fractions"],
            "earned_slot_levels_atr": config["adaptive_harvest"][
                "earned_slot_levels_atr"
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
            "grid_sha256": sha256_file(RESULTS / "selection_multiplier_grid.csv"),
        },
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def _matched_controls(
    candidate: pd.DataFrame,
    frame: pd.DataFrame,
    frames: Mapping[int, pd.DataFrame],
    config: Mapping[str, Any],
    *,
    multiplier: float,
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
            result = resolve_strength_adaptive_harvest(
                frames[direction],
                control_event,
                config,
                strong_multiplier=multiplier,
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
    multiplier = float(selection["selected_params"]["strong_trend_release_multiplier"])
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    streak = int(config["adaptive_harvest"]["adverse_color_streak_bars"])
    frames = streak_exit_frames(frame, streak)
    baseline = replay_baseline(setups, frame, config)
    candidate = replay_candidate(setups, frames, config, multiplier=multiplier)
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
        frames,
        config,
        multiplier=multiplier,
        start=start,
        end=end,
    )
    matched = matched_pairs[matched_pairs["match_status"].eq("matched_exact")].copy()
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
                signflip_p(comparison["delta"], resamples=100_000, seed=90601)
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
                float(signflip_p(excess, resamples=100_000, seed=90602))
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
        fold_table(candidate, folds).assign(policy="strength_adaptive_v12"),
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
