#!/usr/bin/env python3
"""Confirm one 15m K1->K2 factor: minimum pair distance 2 versus 5 bars.

Candidate construction uses completed OHLCV through K2. The filter is applied
before same-K2 ranking and cooldown. Entry economics use only K2+1 open, and
only the frozen outcome resolver reads the later 12-hour path. Confirmation
uses 2024; audit cannot open unless all registered confirmation gates pass.
The physical source ends before repository holdout at 2026-05-04.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import (
    audit_slice_label,
    build_core_pairs,
    filter_candidates,
    fold_table,
    json_value,
    load_base_frame,
    period_candidates,
    ranking_metrics,
    robust_metrics,
    utc,
    with_reference_features,
    write_csv,
    write_json,
)
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    add_control_metrics,
    build_matched_controls,
    metric_row,
)
from scripts.research_btcusdtp_k1k2_stop_buffer import run_arm
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/exp-btcusdtp-k1k2-15m-gap-min-confirmation-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
RECEIPT_PATH = RESULTS / "confirmation_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BAR = "15m"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def params_for(config: dict[str, Any], minimum_gap: int) -> dict[str, Any]:
    frozen = config["signal_frozen"][BAR]
    maximum = int(config["factor"]["gap_max_bars_frozen"])
    if minimum_gap > maximum:
        raise ValueError("minimum gap cannot exceed frozen maximum")
    return {
        "ma_period": int(frozen["ma_period"]),
        "gap_min_bars": int(minimum_gap),
        "gap_max_bars": maximum,
        "score_floor": float(frozen["score_floor"]),
    }


def load_period(
    config: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    base, quality = load_base_frame(config, BAR)
    ma_period = int(config["signal_frozen"][BAR]["ma_period"])
    frame = with_reference_features(base, ma_period)
    universe = build_core_pairs(
        frame,
        ma_period=ma_period,
        maximum_gap_bars=int(config["signal_frozen"][BAR]["maximum_pair_gap_bars"]),
    )
    return frame, universe, quality


def candidate_pool(
    universe: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    params: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Filter gap before same-K2 dedup and bound the full label to the period."""

    return period_candidates(
        filter_candidates(universe, params), frame, config, BAR, start, end
    )


def evaluate(
    universe: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    minimum_gap: int,
    start: pd.Timestamp,
    end: pd.Timestamp,
    folds: list[str],
    *,
    labeler: Any = None,
) -> dict[str, Any]:
    params = params_for(config, minimum_gap)
    candidates = candidate_pool(universe, frame, config, params, start, end)
    decisions, events = run_arm(candidates, frame, config, BAR, params, 0.0)
    controls, pairs = build_matched_controls(
        events,
        frame,
        config,
        BAR,
        start,
        end,
        set(events["k2_i"].astype(int)) if len(events) else set(),
    )
    fixed = config["timeframe_fixed"][BAR]
    metrics = robust_metrics(
        events,
        folds,
        int(fixed["minimum_events_total"]),
        int(fixed["minimum_events_per_confirmation_fold"]),
    )
    metrics = {
        **metrics,
        **add_control_metrics({}, pairs),
        **ranking_metrics(events, resamples=20_000),
    }
    slices = fold_table(events, folds, labeler=labeler) if labeler else fold_table(events, folds)
    return {
        "params": params,
        "candidates": candidates,
        "decisions": decisions,
        "events": events,
        "controls": controls,
        "pairs": pairs,
        "metrics": metrics,
        "folds": slices,
    }


def sequence_changes(baseline: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    columns = ["k1_i", "k2_i", "direction"]
    left = baseline[columns + ["entry_time", "gap_bars", "net_return"]].copy()
    right = candidate[columns + ["entry_time", "gap_bars", "net_return"]].copy()
    merged = left.merge(right, on=columns, how="outer", suffixes=("_baseline", "_candidate"), indicator=True)
    merged["sequence_change"] = merged["_merge"].map(
        {"left_only": "removed", "right_only": "added_after_replay", "both": "retained"}
    )
    return merged.drop(columns="_merge")


def confirmation_passed(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[bool, list[str]]:
    metrics = candidate["metrics"]
    folds = candidate["folds"]
    failures: list[str] = []
    if not bool(metrics["eligible"]):
        failures.append("sample_ineligible")
    if not float(metrics["mean_net_bp"]) > 0.0:
        failures.append("mean_net_not_positive")
    if not float(metrics["robust_score_bp"]) > 0.0:
        failures.append("robust_score_not_positive")
    if not float(metrics["worst_fold_net_bp"]) > -5.0:
        failures.append("worst_fold_below_minus_5bp")
    if not folds["mean_net_bp"].gt(0.0).all():
        failures.append("not_every_confirmation_half_positive")
    improvement = float(metrics["robust_score_bp"]) - float(
        baseline["metrics"]["robust_score_bp"]
    )
    if improvement < 5.0:
        failures.append("robust_improvement_below_5bp")
    if not float(metrics["matched_control_excess_bp"]) > 0.0:
        failures.append("matched_control_excess_not_positive")
    if not float(metrics["paired_signflip_p_one_sided"]) < 0.01:
        failures.append("paired_signflip_p_not_below_0.01")
    return not failures, failures


def write_evaluation(prefix: str, result: dict[str, Any]) -> None:
    write_csv(result["candidates"], RESULTS / f"{prefix}_candidates.csv.gz")
    write_csv(result["decisions"], RESULTS / f"{prefix}_decisions.csv.gz")
    write_csv(result["events"], RESULTS / f"{prefix}_trades.csv.gz")
    write_csv(result["controls"], RESULTS / f"{prefix}_matched_controls.csv.gz")
    write_csv(result["pairs"], RESULTS / f"{prefix}_matched_pairs.csv")
    write_csv(result["folds"], RESULTS / f"{prefix}_folds.csv")


def confirmation_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    nomination = PROJECT / str(config["nomination_receipt"]["path"])
    if sha256_file(nomination) != str(config["nomination_receipt"]["sha256"]):
        raise RuntimeError("causal nomination receipt SHA drift")
    start = utc(config["window"]["confirmation_start_inclusive"])
    end = utc(config["window"]["confirmation_end_exclusive"])
    folds = list(config["window"]["confirmation_folds"])
    frame, universe, quality = load_period(config, start, end)
    baseline_gap = int(config["factor"]["baseline"])
    candidate_gap = int(config["factor"]["candidate"])
    baseline = evaluate(universe, frame, config, baseline_gap, start, end, folds)
    candidate = evaluate(universe, frame, config, candidate_gap, start, end, folds)
    passed, failures = confirmation_passed(baseline, candidate)
    changes = sequence_changes(baseline["events"], candidate["events"])
    write_evaluation("confirmation_baseline_gap2", baseline)
    write_evaluation("confirmation_candidate_gap5", candidate)
    write_csv(changes, RESULTS / "confirmation_sequence_changes.csv")
    comparison = pd.DataFrame(
        [
            {"arm": "baseline_gap2", "gap_min_bars": baseline_gap, **baseline["metrics"]},
            {"arm": "candidate_gap5", "gap_min_bars": candidate_gap, **candidate["metrics"]},
        ]
    )
    write_csv(comparison, RESULTS / "confirmation_metrics.csv")
    receipt = {
        "phase": "confirmation_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "nomination_receipt_sha256": sha256_file(nomination),
        "source": {**quality, "holdout_rows_read": 0},
        "confirmation_window": [start, end],
        "confirmation_rows_read": len(baseline["events"]) + len(candidate["events"]),
        "audit_rows_read": 0,
        "holdout_rows_read": 0,
        "baseline": {"params": baseline["params"], "metrics": baseline["metrics"]},
        "candidate": {"params": candidate["params"], "metrics": candidate["metrics"]},
        "sequence_changes": changes["sequence_change"].value_counts().to_dict(),
        "robust_improvement_bp": float(candidate["metrics"]["robust_score_bp"])
        - float(baseline["metrics"]["robust_score_bp"]),
        "confirmation_gate_passed": passed,
        "confirmation_gate_failures": failures,
        "audit_open_allowed": passed,
    }
    write_json(RECEIPT_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_confirmation_committed(receipt: dict[str, Any]) -> None:
    paths = [
        str(RECEIPT_PATH.relative_to(PROJECT)),
        str(CONFIG_PATH.relative_to(PROJECT)),
        str(SCRIPT_PATH.relative_to(PROJECT)),
    ]
    for relative in paths:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=PROJECT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"confirmation receipt/config/script must be committed: {dirty}")
    if receipt.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("confirmation config SHA drift")
    if receipt.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("confirmation script SHA drift")


def audit_phase(config: dict[str, Any]) -> None:
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    assert_confirmation_committed(receipt)
    if not bool(receipt.get("audit_open_allowed")):
        raise RuntimeError("confirmation futility gate: audit remains closed")
    start = utc(config["window"]["audit_start_inclusive"])
    end = utc(config["window"]["audit_end_exclusive"])
    slices = list(config["window"]["audit_slices"])
    frame, universe, quality = load_period(config, start, end)
    candidate = evaluate(
        universe,
        frame,
        config,
        int(config["factor"]["candidate"]),
        start,
        end,
        slices,
        labeler=audit_slice_label,
    )
    complete = candidate["folds"].loc[
        candidate["folds"]["fold"].isin(["2025H1", "2025H2"])
    ]
    passed = bool(
        float(candidate["metrics"]["mean_net_bp"]) > 0.0
        and float(candidate["metrics"]["matched_control_excess_bp"]) > 0.0
        and float(candidate["metrics"]["paired_signflip_p_one_sided"]) < 0.01
        and len(complete) == 2
        and complete["mean_net_bp"].gt(0.0).all()
    )
    write_evaluation("audit_candidate_gap5", candidate)
    summary = {
        "phase": "qualified_frozen_audit_complete",
        "source": quality,
        "candidate_metrics": candidate["metrics"],
        "audit_slices": candidate["folds"].to_dict("records"),
        "audit_success_gate_passed": passed,
        "holdout_rows_read": 0,
    }
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["confirmation", "audit"], required=True)
    args = parser.parse_args()
    config = load_config()
    if args.phase == "confirmation":
        confirmation_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
