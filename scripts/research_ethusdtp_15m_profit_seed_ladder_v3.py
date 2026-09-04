#!/usr/bin/env python3
"""Select the bank fraction for an ETHUSDT.P 15m seed-profit ladder.

Entry, ATR levels (+2/+4/+6/+8), four stages, profit floor, SMA60 remainder,
cost, and horizon are fixed. Selection changes only the total banked fraction
on 2023--2024. Audit loads the chosen fraction from a committed receipt. All
giveback labels use only the position's observed path before exit.
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
    json_value,
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
from scripts.research_ethusdtp_15m_progressive_scaleout_v2 import (
    fold_table,
    robust_summary,
    window,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-profit-seed-ladder-preholdout-20260905-v3"
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


def _rank(
    row: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[float, float, float]:
    if not bool(row["eligible"]):
        return (2.0, float("inf"), float("inf"))
    gates = config["selection"]["success_gates"]
    safe = bool(
        float(row["banked_to_nonpositive_share"])
        <= float(gates["banked_to_nonpositive_share_max"])
        and float(row["runner_armed_to_nonpositive_share"])
        <= float(gates["runner_armed_to_nonpositive_share_max"])
        and float(row["mean_net_delta_bp"])
        >= float(gates["candidate_minus_baseline_mean_net_bp_min"])
        and float(row["p95_net_retention"])
        >= float(gates["candidate_p95_net_retention_min"])
    )
    return (
        0.0 if safe else 1.0,
        -float(row["robust_score_bp"])
        if safe
        else float(row["runner_armed_to_nonpositive_share"]),
        float(row["bank_total_fraction"]),
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
        setups, split["development_start_inclusive"], split["development_end_exclusive"]
    )
    baseline = replay(setups, frame, config, bank_total_fraction=None, step_atr=None)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    step = float(config["progressive_scaleout"]["step_atr"])
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    for bank in map(float, config["selection"]["bank_total_fraction_candidates"]):
        events = replay(setups, frame, config, bank_total_fraction=bank, step_atr=step)
        ledgers[bank] = events
        summary = robust_summary(events, folds, config)
        rows.append(
            {
                "bank_total_fraction": bank,
                "runner_fraction": 1.0 - bank,
                "step_atr": step,
                **summary,
                "mean_net_delta_bp": float(
                    summary["mean_net_bp"] - baseline_summary["mean_net_bp"]
                ),
                "p95_net_retention": float(
                    summary["p95_net_bp"] / baseline_summary["p95_net_bp"]
                ),
            }
        )
    winner = min(rows, key=lambda row: _rank(row, config))
    if not bool(winner["eligible"]):
        raise RuntimeError("no sample-eligible bank fraction")
    bank = float(winner["bank_total_fraction"])
    selected = ledgers[bank]
    selected_summary = robust_summary(selected, folds, config)
    gates = config["selection"]["success_gates"]
    checks = {
        "banked_to_nonpositive_gate": bool(
            selected_summary["banked_to_nonpositive_share"]
            <= float(gates["banked_to_nonpositive_share_max"])
        ),
        "runner_armed_to_nonpositive_gate": bool(
            selected_summary["runner_armed_to_nonpositive_share"]
            <= float(gates["runner_armed_to_nonpositive_share_max"])
        ),
        "mean_net_delta_bp": float(
            selected_summary["mean_net_bp"] - baseline_summary["mean_net_bp"]
        ),
        "mean_net_gate": bool(
            selected_summary["mean_net_bp"] - baseline_summary["mean_net_bp"]
            >= float(gates["candidate_minus_baseline_mean_net_bp_min"])
        ),
        "worst_fold_degradation_bp": float(
            baseline_summary["worst_fold_net_bp"]
            - selected_summary["worst_fold_net_bp"]
        ),
        "worst_fold_gate": bool(
            baseline_summary["worst_fold_net_bp"]
            - selected_summary["worst_fold_net_bp"]
            <= float(gates["candidate_worst_fold_degradation_bp_max"])
        ),
        "p95_net_retention": float(winner["p95_net_retention"]),
        "p95_gate": bool(
            winner["p95_net_retention"]
            >= float(gates["candidate_p95_net_retention_min"])
        ),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_bank_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="seed_ladder_v3"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
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
        "all_registered_gates_pass": bool(
            all(value for key, value in checks.items() if key.endswith("_gate"))
        ),
        "audit_rows_read": 0,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(RESULTS / "selection_bank_grid.csv"),
        },
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
        baseline[["setup_id", "net_return"]],
        on="setup_id",
        suffixes=("_candidate", "_baseline"),
    )
    comparison["delta"] = (
        comparison["net_return_candidate"] - comparison["net_return_baseline"]
    )
    controls, pairs = _matched_controls(
        baseline, candidate, frame, config, params=params, start=start, end=end
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["progressive_paired_excess"].astype(float)
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
        "paired_candidate_minus_baseline": {
            "mean_delta_bp": float(comparison["delta"].mean() * 1e4),
            "median_delta_bp": float(comparison["delta"].median() * 1e4),
            "positive_delta_share": float(comparison["delta"].gt(0.0).mean()),
            "signflip_p": float(
                signflip_p(comparison["delta"], resamples=100_000, seed=90511)
            ),
        },
        "matched_random": {
            "matched_events": len(matched),
            "candidate_mean_net_bp": float(
                matched["candidate_progressive_net_return"].mean() * 1e4
            )
            if len(matched)
            else np.nan,
            "control_mean_net_bp": float(
                matched["control_progressive_mean_net_return"].mean() * 1e4
            )
            if len(matched)
            else np.nan,
            "excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
            "signflip_p": float(signflip_p(excess, resamples=100_000, seed=90512))
            if len(excess)
            else np.nan,
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
        fold_table(candidate, folds).assign(policy="seed_ladder_v3"),
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
