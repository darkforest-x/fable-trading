#!/usr/bin/env python3
"""Select and audit candle-color profit harvesting for ETHUSDT.P 15m.

Favourable excursions at +2/+4/+8/+12 signal ATR earn release slots. One slot
is scheduled after each completed adverse-color candle and filled at the next
bar open. A release never changes the disaster or SMA60/ATR runner stop.
Selection changes only fraction per slot on 2023--2024. Repository holdout rows
are never parsed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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
    build_frozen_setups,
    load_eth_frame,
    resolve_baseline,
)
from scripts.research_ethusdtp_15m_progressive_scaleout_v2 import (
    fold_table,
    robust_summary,
    window,
)
from scripts.research_ethusdtp_15m_weakness_harvest_v7 import (
    _candidate_row,
    _matched_controls,
    _passes,
    resolve_weakness_harvest,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-color-harvest-preholdout-20260905-v9"
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


def color_exit_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Map the V7 adverse-side predicate onto causal candle body color.

    The resolver checks ``direction * (close - reference_ma) <= 0``. Replacing
    the exit-only copy's reference with current ``open`` makes that predicate a
    completed adverse candle without changing the EMA30 used by entry logic.
    """

    out = frame.copy()
    out["reference_ma"] = out["open"].astype(float)
    return out


def replay(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    fraction_per_stage: float | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        result = (
            resolve_baseline(frame, event, config)
            if fraction_per_stage is None
            else resolve_weakness_harvest(
                frame, event, config, fraction_per_stage=fraction_per_stage
            )
        )
        if result.get("resolved"):
            if fraction_per_stage is not None:
                result["policy"] = "adverse_candle_color_harvest_sma60_runner"
            rows.append(result)
    return pd.DataFrame(rows)


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
    exit_frame = color_exit_frame(frame)
    baseline = replay(setups, exit_frame, config, fraction_per_stage=None)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    for fraction in map(float, config["selection"]["fraction_per_stage_candidates"]):
        events = replay(setups, exit_frame, config, fraction_per_stage=fraction)
        ledgers[fraction] = events
        rows.append(_candidate_row(events, baseline_summary, folds, config, fraction))
    passing = [row for row in rows if _passes(row, config)]
    winner = (
        max(
            passing,
            key=lambda row: (
                float(row["robust_score_bp"]),
                -float(row["fraction_per_stage"]),
            ),
        )
        if passing
        else max(rows, key=lambda row: float(row["robust_score_bp"]))
    )
    fraction = float(winner["fraction_per_stage"])
    selected = ledgers[fraction]
    selected_summary = robust_summary(selected, folds, config)
    all_pass = _passes(winner, config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_fraction_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="color_harvest_v9"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_params": {
            "fraction_per_stage": fraction,
            "maximum_bank_fraction": 4.0 * fraction,
            "earned_slot_levels_atr": config["weakness_harvest"][
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
            "grid_sha256": sha256_file(RESULTS / "selection_fraction_grid.csv"),
        },
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    selection = _assert_selection_committed()
    fraction = float(selection["selected_params"]["fraction_per_stage"])
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    exit_frame = color_exit_frame(frame)
    baseline = replay(setups, exit_frame, config, fraction_per_stage=None)
    candidate = replay(setups, exit_frame, config, fraction_per_stage=fraction)
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
        exit_frame,
        config,
        fraction=fraction,
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
                signflip_p(comparison["delta"], resamples=100_000, seed=90571)
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
                float(signflip_p(excess, resamples=100_000, seed=90572))
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
        fold_table(candidate, folds).assign(policy="color_harvest_v9"),
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
