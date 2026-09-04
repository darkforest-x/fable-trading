#!/usr/bin/env python3
"""Test fixed profit-target R for causal BTCUSDT.P 15m/5m K1→K2 trades.

Candidate construction and next-open execution read completed data only. This
experiment changes only ``target_r``; outcome resolution alone reads the
registered 12-hour path. The physical source ends before repository holdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import (
    audit_slice_label,
    build_core_pairs,
    execution_funnel,
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
from scripts.research_btcusdtp_k1k2_stop_buffer import (
    gross_positive_in_all_folds,
    run_arm as run_immediate_arm,
    signal_params,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/exp-btcusdtp-k1k2-fixed-target-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def run_target_arm(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    target_r: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if target_r <= 0.0:
        raise ValueError("target_r must be positive")
    arm_config = deepcopy(config)
    arm_config["execution_frozen"]["target_r"] = float(target_r)
    decisions, events = run_immediate_arm(
        candidates, frame, arm_config, bar, params, 0.0
    )
    for table in (decisions, events):
        if len(table):
            table["target_r"] = float(target_r)
    return decisions, events, arm_config


def select_target(
    rows: list[dict[str, Any]], baseline: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    passing = [
        row
        for row in rows
        if bool(row["eligible"])
        and float(row["robust_score_bp"])
        >= float(baseline["robust_score_bp"]) + 2.0
        and float(row["worst_fold_net_bp"])
        >= float(baseline["worst_fold_net_bp"]) - 3.0
    ]
    if not passing:
        return baseline, "retain_3r_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            abs(float(row["target_r"]) - 3.0),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def load_candidates(
    config: dict[str, Any], bar: str, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    base, quality = load_base_frame(config, bar)
    params = signal_params(config, bar)
    frame = with_reference_features(base, int(params["ma_period"]))
    universe = build_core_pairs(
        frame,
        ma_period=int(params["ma_period"]),
        maximum_gap_bars=int(config["signal_frozen"][bar]["maximum_pair_gap_bars"]),
    )
    candidates = period_candidates(
        filter_candidates(universe, params), frame, config, bar, start, end
    )
    return frame, universe, candidates, params, quality


def development_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    receipt: dict[str, Any] = {
        "phase": "development_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "holdout_rows_read": 0,
        "audit_rows_read": 0,
        "timeframes": {},
    }
    traces: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []

    for bar in ("15m", "5m"):
        print(f"[{bar}] loading physical pre-holdout source", flush=True)
        frame, universe, candidates, params, quality = load_candidates(
            config, bar, start, end
        )
        rows: list[dict[str, Any]] = []
        ledgers: dict[
            float,
            tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame],
        ] = {}
        for value in config["factor"]["grid"]:
            target_r = float(value)
            decisions, events, arm_config = run_target_arm(
                candidates, frame, config, bar, params, target_r
            )
            metrics = robust_metrics(
                events,
                folds,
                int(config["timeframe_fixed"][bar]["minimum_events_total"]),
                int(config["timeframe_fixed"][bar]["minimum_events_per_development_fold"]),
            )
            controls, pairs = build_matched_controls(
                events,
                frame,
                arm_config,
                bar,
                start,
                end,
                set(events["k2_i"].astype(int)) if len(events) else set(),
            )
            row = {
                "bar": bar,
                "target_r": target_r,
                "candidate_rows": len(candidates),
                "decision_rows": len(decisions),
                "all_folds_gross_positive": gross_positive_in_all_folds(events, folds),
                **metrics,
                **add_control_metrics({}, pairs),
            }
            rows.append(row)
            ledgers[target_r] = decisions, events, controls, pairs
            write_csv(events, RESULTS / f"development_{bar}_target-{target_r:g}r_trades.csv.gz")
            print(
                f"[{bar}] target={target_r:g}R: robust={metrics['robust_score_bp']:.2f}bp "
                f"net={metrics['mean_net_bp']:.2f}bp n={metrics['events']}",
                flush=True,
            )
        baseline = next(
            row
            for row in rows
            if np.isclose(float(row["target_r"]), float(config["factor"]["initial"]))
        )
        selected, reason = select_target(rows, baseline)
        selected_target = float(selected["target_r"])
        decisions, events, controls, pairs = ledgers[selected_target]
        success = bool(
            bool(selected["eligible"])
            and float(selected["mean_net_bp"]) > 0.0
            and float(selected["robust_score_bp"]) > 0.0
            and float(selected["worst_fold_net_bp"]) > -5.0
            and bool(selected["all_folds_gross_positive"])
        )
        prefix = RESULTS / f"development_{bar}"
        write_csv(pd.DataFrame(rows), prefix.with_name(prefix.name + "_trace.csv"))
        write_csv(events, prefix.with_name(prefix.name + "_selected_trades.csv.gz"))
        write_csv(decisions, prefix.with_name(prefix.name + "_selected_decisions.csv.gz"))
        write_csv(controls, prefix.with_name(prefix.name + "_selected_matched_controls.csv.gz"))
        write_csv(pairs, prefix.with_name(prefix.name + "_selected_matched_pairs.csv"))
        write_csv(fold_table(events, folds), prefix.with_name(prefix.name + "_selected_folds.csv"))
        traces.append(pd.DataFrame(rows))
        sources.append({**quality, "bar": bar, "holdout_rows_read": 0})
        receipt["timeframes"][bar] = {
            "source": {**quality, "holdout_rows_read": 0},
            "signal_params": params,
            "selection_reason": reason,
            "selected_target_r": selected_target,
            "baseline_metrics": baseline,
            "selected_metrics": selected,
            "best_observed_arm": max(
                rows,
                key=lambda row: float(row["robust_score_bp"])
                if np.isfinite(float(row["robust_score_bp"]))
                else -np.inf,
            ),
            "development_success": success,
            "audit_open_allowed": success,
            "funnel": execution_funnel(universe, candidates, decisions, events),
        }
        print(f"[{bar}] {reason}; audit_open_allowed={success}", flush=True)

    write_csv(pd.concat(traces, ignore_index=True), RESULTS / "development_trace.csv")
    write_csv(pd.DataFrame(sources), RESULTS / "source_receipt.csv")
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_selection_committed(selection: dict[str, Any]) -> None:
    paths = [
        str(SELECTION_PATH.relative_to(PROJECT)),
        str(SCRIPT_PATH.relative_to(PROJECT)),
        str(CONFIG_PATH.relative_to(PROJECT)),
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
        raise RuntimeError(f"selection/config/script must be committed before audit: {dirty}")
    if selection.get("phase") != "development_complete_audit_unopened":
        raise RuntimeError("selection phase drift")
    if selection.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("selection config SHA drift")
    if selection.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("selection script SHA drift")


def audit_phase(config: dict[str, Any]) -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_selection_committed(selection)
    qualified = [
        bar
        for bar in ("15m", "5m")
        if bool(selection["timeframes"][bar]["audit_open_allowed"])
    ]
    if not qualified:
        raise RuntimeError("futility gate: no timeframe qualified to open audit")
    start = utc(config["window"]["audit_start_inclusive"])
    end = utc(config["window"]["audit_end_exclusive"])
    summary: dict[str, Any] = {
        "phase": "qualified_frozen_audit_complete",
        "audit_window_pristine": False,
        "qualified_timeframes": qualified,
        "holdout_rows_read": 0,
        "timeframes": {},
    }
    rows: list[dict[str, Any]] = []
    for bar in qualified:
        frame, universe, candidates, params, quality = load_candidates(
            config, bar, start, end
        )
        target_r = float(selection["timeframes"][bar]["selected_target_r"])
        decisions, events, arm_config = run_target_arm(
            candidates, frame, config, bar, params, target_r
        )
        controls, pairs = build_matched_controls(
            events,
            frame,
            arm_config,
            bar,
            start,
            end,
            set(events["k2_i"].astype(int)) if len(events) else set(),
        )
        metrics = {**metric_row(events), **add_control_metrics({}, pairs), **ranking_metrics(events)}
        slices = fold_table(
            events, list(config["window"]["audit_slices"]), labeler=audit_slice_label
        )
        complete = slices[slices["fold"].isin(["2025H1", "2025H2"])]
        passed = bool(
            float(metrics["mean_net_bp"]) > 0.0
            and float(metrics["matched_control_excess_bp"]) > 0.0
            and float(metrics["paired_signflip_p_one_sided"]) < 0.01
            and len(complete) == 2
            and complete["mean_net_bp"].gt(0.0).all()
        )
        rows.append({"bar": bar, "target_r": target_r, **metrics, "success_gate_passed": passed})
        write_csv(events, RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        write_csv(decisions, RESULTS / f"audit_{bar}_selected_decisions.csv.gz")
        write_csv(slices, RESULTS / f"audit_{bar}_selected_slices.csv")
        write_csv(controls, RESULTS / f"audit_{bar}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"audit_{bar}_matched_pairs.csv")
        summary["timeframes"][bar] = {
            "selected_target_r": target_r,
            "metrics": metrics,
            "slices": slices.to_dict("records"),
            "success_gate_passed": passed,
            "source": quality,
            "funnel": execution_funnel(universe, candidates, decisions, events),
        }
    write_csv(pd.DataFrame(rows), RESULTS / "audit_metrics.csv")
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "audit"), required=True)
    args = parser.parse_args()
    config = load_config()
    if utc(config["window"]["audit_end_exclusive"]) >= utc(config["window"]["holdout_start"]):
        raise RuntimeError("configured audit boundary reaches repository holdout")
    if args.phase == "development":
        development_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
