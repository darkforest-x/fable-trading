#!/usr/bin/env python3
"""Select and audit adverse-candle streak harvesting for ETHUSDT.P 15m.

The +2/+4/+8/+12 ATR earned slots, 40% maximum bank budget, fixed 4:2:1:1
release sizes, runner, disaster stop, horizon and costs are frozen. Selection
changes only the completed adverse-color streak length on 2023--2024. Profit
releases never update the stop. Repository holdout rows are never parsed.
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
from scripts.research_ethusdtp_15m_decay_color_harvest_v10 import (
    resolve_decay_color_harvest,
    stage_fractions,
)
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
EXPERIMENT_ID = "exp-ethusdtp-15m-streak-harvest-preholdout-20260905-v11"
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


def _causal_run_lengths(mask: np.ndarray, segments: np.ndarray) -> np.ndarray:
    run = np.zeros(len(mask), dtype=int)
    previous_segment: int | None = None
    count = 0
    for index, (flag, segment) in enumerate(zip(mask, segments)):
        current_segment = int(segment)
        if previous_segment is None or current_segment != previous_segment:
            count = 0
        count = count + 1 if bool(flag) else 0
        run[index] = count
        previous_segment = current_segment
    return run


def streak_exit_frames(
    frame: pd.DataFrame, streak_bars: int
) -> dict[int, pd.DataFrame]:
    """Encode causal long/short adverse streaks into exit-only references.

    Uses only current/past ``open``, ``close``, and ``segment_id``. For each
    direction the synthetic reference makes the reused predicate true exactly
    when the completed adverse-colour run has reached ``streak_bars``. The
    original EMA30 reference used to form entries is never mutated.
    """

    if streak_bars < 1:
        raise ValueError("streak_bars must be positive")
    open_price = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    segments = frame["segment_id"].to_numpy(dtype=int)
    outputs: dict[int, pd.DataFrame] = {}
    for direction in (1, -1):
        adverse = close < open_price if direction > 0 else close > open_price
        run = _causal_run_lengths(adverse, segments)
        trigger = run >= streak_bars
        out = frame.copy()
        out["reference_ma"] = np.where(trigger, close, close - direction)
        out["adverse_color_run"] = run
        outputs[direction] = out
    return outputs


def replay_candidate(
    setups: pd.DataFrame,
    frames: Mapping[int, pd.DataFrame],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    bank = float(config["decay_color_harvest"]["maximum_bank_fraction"])
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        direction = int(event["direction"])
        result = resolve_decay_color_harvest(
            frames[direction], event, config, bank_total_fraction=bank
        )
        if result.get("resolved"):
            result["policy"] = "adverse_streak_decay_harvest_sma60_runner"
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
    streak: int,
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
        "adverse_color_streak_bars": streak,
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
    ledgers: dict[int, pd.DataFrame] = {}
    for streak in map(int, config["selection"]["adverse_color_streak_candidates"]):
        events = replay_candidate(setups, streak_exit_frames(frame, streak), config)
        ledgers[streak] = events
        rows.append(_candidate_row(events, baseline_summary, folds, config, streak))
    passing = [row for row in rows if _passes(row, config)]
    winner = (
        max(
            passing,
            key=lambda row: (
                float(row["robust_score_bp"]),
                -int(row["adverse_color_streak_bars"]),
            ),
        )
        if passing
        else max(rows, key=lambda row: float(row["robust_score_bp"]))
    )
    streak = int(winner["adverse_color_streak_bars"])
    selected = ledgers[streak]
    selected_summary = robust_summary(selected, folds, config)
    all_pass = _passes(winner, config)
    bank = float(config["decay_color_harvest"]["maximum_bank_fraction"])
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_streak_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="streak_harvest_v11"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_params": {
            "adverse_color_streak_bars": streak,
            "maximum_bank_fraction": bank,
            "stage_fractions": stage_fractions(config, bank),
            "earned_slot_levels_atr": config["decay_color_harvest"][
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
            "grid_sha256": sha256_file(RESULTS / "selection_streak_grid.csv"),
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
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    horizon = int(config["frozen_execution"]["horizon_bars"])
    bank = float(config["decay_color_harvest"]["maximum_bank_fraction"])
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
            result = resolve_decay_color_harvest(
                frames[direction], control_event, config, bank_total_fraction=bank
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
    streak = int(selection["selected_params"]["adverse_color_streak_bars"])
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    frames = streak_exit_frames(frame, streak)
    baseline = replay_baseline(setups, frame, config)
    candidate = replay_candidate(setups, frames, config)
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
        candidate, frame, frames, config, start=start, end=end
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
                signflip_p(comparison["delta"], resamples=100_000, seed=90591)
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
                float(signflip_p(excess, resamples=100_000, seed=90592))
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
        fold_table(candidate, folds).assign(policy="streak_harvest_v11"),
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
