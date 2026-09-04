#!/usr/bin/env python3
"""Select and audit a Pareto-balanced ETHUSDT.P 15m profit ladder.

The four-stage, 60%-bank / 40%-runner structure is fixed. Selection changes
only ATR milestone spacing on 2023--2024. Giveback metrics use path observed
before exit; post-exit horizon MFE remains a missed-opportunity diagnostic and
never labels a realised profit reversal. Audit parameters are loaded from a
committed selection receipt. No row at or after 2026-05-04 is parsed.
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
from scripts.research_btcusdtp_15m_ma_state_trend import (
    fold_label,
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_ethusdtp_15m_progressive_scaleout import (
    _matched_controls,
    build_frozen_setups,
    load_eth_frame,
    replay,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-progressive-scaleout-pareto-preholdout-20260904-v2"
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
    if receipt.get("status") != "frozen_for_audit":
        raise RuntimeError("selection receipt is not frozen")
    return receipt


def window(frame: pd.DataFrame, start: object, end: object) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    times = frame["entry_time"].map(utc)
    return frame.loc[times.ge(utc(start)) & times.lt(utc(end))].copy()


def corrected_metrics(events: pd.DataFrame) -> dict[str, Any]:
    base = metrics(events)
    if events.empty:
        return {
            **base,
            "banked_events": 0,
            "banked_to_nonpositive_events": 0,
            "banked_to_nonpositive_share": np.nan,
            "runner_armed_events": 0,
            "runner_armed_to_nonpositive_events": 0,
            "runner_armed_to_nonpositive_share": np.nan,
            "actual_mfe_2atr_events": 0,
            "actual_mfe_2atr_to_nonpositive_share": np.nan,
            "median_giveback_atr": np.nan,
            "p95_net_bp": np.nan,
            "mean_banked_gross_bp": np.nan,
            "profit_floor_gap_breaches": 0,
        }
    banked = events["partial_hits"].gt(0)
    armed = events["runner_armed"].astype(bool)
    mfe = events["mfe_at_exit_atr"].ge(2.0)
    nonpositive = events["net_return"].le(0.0)
    return {
        **base,
        "banked_events": int(banked.sum()),
        "banked_to_nonpositive_events": int((banked & nonpositive).sum()),
        "banked_to_nonpositive_share": float((banked & nonpositive).sum() / banked.sum()) if banked.sum() else np.nan,
        "runner_armed_events": int(armed.sum()),
        "runner_armed_to_nonpositive_events": int((armed & nonpositive).sum()),
        "runner_armed_to_nonpositive_share": float((armed & nonpositive).sum() / armed.sum()) if armed.sum() else np.nan,
        "actual_mfe_2atr_events": int(mfe.sum()),
        "actual_mfe_2atr_to_nonpositive_share": float((mfe & nonpositive).sum() / mfe.sum()) if mfe.sum() else np.nan,
        "median_giveback_atr": float(events["gave_back_atr"].median()),
        "p95_net_bp": float(events["net_return"].quantile(0.95) * 1e4),
        "mean_banked_gross_bp": float(events["banked_gross_return"].mean() * 1e4),
        "profit_floor_gap_breaches": int(events["profit_floor_gap_breach"].sum()),
    }


def fold_table(events: pd.DataFrame, folds: list[str]) -> pd.DataFrame:
    labels = events["entry_time"].map(fold_label) if len(events) else pd.Series(dtype=str)
    return pd.DataFrame(
        [
            {
                "fold": fold,
                **corrected_metrics(
                    events.loc[labels.eq(fold)].copy() if len(events) else events.copy()
                ),
            }
            for fold in folds
        ]
    )


def robust_summary(
    events: pd.DataFrame, folds: list[str], config: Mapping[str, Any]
) -> dict[str, Any]:
    table = fold_table(events, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(means) and np.isfinite(means).all())
    selection = config["selection"]
    return {
        **corrected_metrics(events),
        "minimum_fold_events": int(counts.min()) if len(counts) else 0,
        "eligible": bool(
            len(events) >= int(selection["minimum_events_total"])
            and len(counts)
            and np.all(counts >= int(selection["minimum_events_per_fold"]))
            and finite
        ),
        "robust_score_bp": float(np.median(means) - 0.5 * np.std(means, ddof=0)) if finite else np.nan,
        "worst_fold_net_bp": float(np.min(means)) if finite else np.nan,
    }


def _rank(row: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[float, float, float]:
    if not bool(row["eligible"]):
        return (2.0, float("inf"), float("inf"))
    gates = config["selection"]["success_gates"]
    safe = bool(
        float(row["banked_to_nonpositive_share"])
        <= float(gates["banked_to_nonpositive_share_max"])
        and float(row["p95_net_retention"])
        >= float(gates["candidate_p95_net_retention_min"])
    )
    return (
        0.0 if safe else 1.0,
        -float(row["robust_score_bp"]) if safe else float(row["banked_to_nonpositive_share"]),
        float(row["step_atr"]),
    )


def selection_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    split = config["splits"]
    frame, quality = load_eth_frame(
        config, end_exclusive=utc(split["development_end_exclusive"])
    )
    pairs, setups = build_frozen_setups(frame, config)
    setups = window(setups, split["development_start_inclusive"], split["development_end_exclusive"])
    baseline = replay(setups, frame, config, bank_total_fraction=None, step_atr=None)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    bank = float(config["progressive_scaleout"]["bank_total_fraction"])
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    for step in map(float, config["selection"]["step_atr_candidates"]):
        events = replay(setups, frame, config, bank_total_fraction=bank, step_atr=step)
        ledgers[step] = events
        summary = robust_summary(events, folds, config)
        p95_retention = (
            float(summary["p95_net_bp"]) / float(baseline_summary["p95_net_bp"])
            if float(baseline_summary["p95_net_bp"]) > 0.0
            else np.nan
        )
        rows.append({"step_atr": step, "bank_total_fraction": bank, **summary, "p95_net_retention": p95_retention})
    winner = min(rows, key=lambda row: _rank(row, config))
    if not bool(winner["eligible"]):
        raise RuntimeError("no sample-eligible spacing arm")
    step = float(winner["step_atr"])
    selected = ledgers[step]
    selected_summary = robust_summary(selected, folds, config)
    baseline_armed_loss = float(baseline_summary["runner_armed_to_nonpositive_share"])
    candidate_armed_loss = float(selected_summary["runner_armed_to_nonpositive_share"])
    relative_reduction = (
        (baseline_armed_loss - candidate_armed_loss) / baseline_armed_loss
        if baseline_armed_loss > 0.0
        else np.nan
    )
    gates = config["selection"]["success_gates"]
    checks = {
        "banked_to_nonpositive_gate": bool(
            selected_summary["banked_to_nonpositive_share"]
            <= float(gates["banked_to_nonpositive_share_max"])
        ),
        "confirmed_profit_relative_reduction": relative_reduction,
        "confirmed_profit_gate": bool(
            np.isfinite(relative_reduction)
            and relative_reduction
            >= float(gates["confirmed_profit_to_nonpositive_relative_reduction_min"])
        ),
        "mean_net_delta_bp": float(selected_summary["mean_net_bp"] - baseline_summary["mean_net_bp"]),
        "mean_net_gate": bool(
            selected_summary["mean_net_bp"] - baseline_summary["mean_net_bp"]
            >= float(gates["candidate_minus_baseline_mean_net_bp_min"])
        ),
        "worst_fold_degradation_bp": float(baseline_summary["worst_fold_net_bp"] - selected_summary["worst_fold_net_bp"]),
        "worst_fold_gate": bool(
            baseline_summary["worst_fold_net_bp"] - selected_summary["worst_fold_net_bp"]
            <= float(gates["candidate_worst_fold_degradation_bp_max"])
        ),
        "p95_net_retention": float(winner["p95_net_retention"]),
        "p95_gate": bool(
            winner["p95_net_retention"] >= float(gates["candidate_p95_net_retention_min"])
        ),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_spacing_grid.csv")
    write_csv(fold_table(baseline, folds).assign(policy="baseline"), RESULTS / "selection_baseline_fold_metrics.csv")
    write_csv(fold_table(selected, folds).assign(policy="progressive_v2"), RESULTS / "selection_candidate_fold_metrics.csv")
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit",
        "selected_params": {"bank_total_fraction": bank, "step_atr": step},
        "source": quality,
        "raw_pairs": len(pairs),
        "frozen_setups": len(setups),
        "baseline": baseline_summary,
        "candidate": selected_summary,
        "gates": checks,
        "behavior_gates_pass": bool(checks["banked_to_nonpositive_gate"] and checks["confirmed_profit_gate"]),
        "transport_gates_pass": bool(checks["mean_net_gate"] and checks["worst_fold_gate"] and checks["p95_gate"]),
        "audit_rows_read": 0,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(RESULTS / "selection_spacing_grid.csv")
        }
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    selection = _assert_selection_committed()
    params = {key: float(value) for key, value in selection["selected_params"].items()}
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    baseline = replay(setups, frame, config, bank_total_fraction=None, step_atr=None)
    candidate = replay(setups, frame, config, **params)
    folds = list(map(str, split["audit_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    candidate_summary = robust_summary(candidate, folds, config)
    comparison = candidate[["setup_id", "net_return"]].merge(
        baseline[["setup_id", "net_return"]], on="setup_id", suffixes=("_candidate", "_baseline")
    )
    comparison["delta"] = comparison["net_return_candidate"] - comparison["net_return_baseline"]
    controls, pairs = _matched_controls(
        baseline, candidate, frame, config, params=params, start=start, end=end
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["progressive_paired_excess"].astype(float)
    baseline_armed_loss = float(baseline_summary["runner_armed_to_nonpositive_share"])
    candidate_armed_loss = float(candidate_summary["runner_armed_to_nonpositive_share"])
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "audit",
        "status": "research_only",
        "selected_params": params,
        "source": quality,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "setups": len(setups),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "confirmed_profit_to_nonpositive_relative_reduction": (
            (baseline_armed_loss - candidate_armed_loss) / baseline_armed_loss
            if baseline_armed_loss > 0.0
            else np.nan
        ),
        "paired_candidate_minus_baseline": {
            "mean_delta_bp": float(comparison["delta"].mean() * 1e4),
            "median_delta_bp": float(comparison["delta"].median() * 1e4),
            "positive_delta_share": float(comparison["delta"].gt(0.0).mean()),
            "signflip_p": float(signflip_p(comparison["delta"], resamples=100_000, seed=90502))
        },
        "matched_random": {
            "matched_events": len(matched),
            "candidate_mean_net_bp": float(matched["candidate_progressive_net_return"].mean() * 1e4) if len(matched) else np.nan,
            "control_mean_net_bp": float(matched["control_progressive_mean_net_return"].mean() * 1e4) if len(matched) else np.nan,
            "excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
            "signflip_p": float(signflip_p(excess, resamples=100_000, seed=90503)) if len(excess) else np.nan
        },
        "production_or_live_changed": False
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(baseline, RESULTS / "audit_baseline_trades.csv.gz")
    write_csv(candidate, RESULTS / "audit_candidate_trades.csv.gz")
    write_csv(comparison, RESULTS / "audit_paired_exit_deltas.csv")
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(fold_table(baseline, folds).assign(policy="baseline"), RESULTS / "audit_baseline_fold_metrics.csv")
    write_csv(fold_table(candidate, folds).assign(policy="progressive_v2"), RESULTS / "audit_candidate_fold_metrics.csv")
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
