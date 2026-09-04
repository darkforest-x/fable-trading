#!/usr/bin/env python3
"""Test one causal exit factor: the close-based fee-cover trigger in R.

Signal and entry construction use completed data through K2+1 open. Only
outcome resolution reads the registered 12-hour future path. Each arm changes
the protection trigger and nothing else. The physical source ends before the
repository holdout at 2026-05-04.
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
    "experiments/active/"
    "exp-btcusdtp-k1k2-protection-trigger-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
DISABLED_TRIGGER_R = 1e9


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def effective_trigger_r(arm: dict[str, Any]) -> float:
    value = arm.get("trigger_r")
    return DISABLED_TRIGGER_R if value is None else float(value)


def run_trigger_arm(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    arm: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    arm_config = deepcopy(config)
    arm_config["execution_frozen"][
        "profit_protection_trigger_close_r"
    ] = effective_trigger_r(arm)
    decisions, events = run_immediate_arm(
        candidates, frame, arm_config, bar, params, 0.0
    )
    for table in (decisions, events):
        if len(table):
            table["protection_arm"] = str(arm["label"])
            table["protection_trigger_r"] = (
                np.nan if arm.get("trigger_r") is None else float(arm["trigger_r"])
            )
            table["protection_disabled"] = arm.get("trigger_r") is None
    return decisions, events, arm_config


def select_trigger(
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
        return baseline, "retain_1.50r_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            float(row["distance_from_1.50r"]),
            bool(row["protection_disabled"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


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
        base, quality = load_base_frame(config, bar)
        params = signal_params(config, bar)
        frame = with_reference_features(base, int(params["ma_period"]))
        universe = build_core_pairs(
            frame,
            ma_period=int(params["ma_period"]),
            maximum_gap_bars=int(
                config["signal_frozen"][bar]["maximum_pair_gap_bars"]
            ),
        )
        candidates = period_candidates(
            filter_candidates(universe, params), frame, config, bar, start, end
        )
        rows: list[dict[str, Any]] = []
        ledgers: dict[
            str,
            tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame],
        ] = {}
        for arm in config["factor"]["arms"]:
            label = str(arm["label"])
            decisions, events, arm_config = run_trigger_arm(
                candidates, frame, config, bar, params, arm
            )
            metrics = robust_metrics(
                events,
                folds,
                int(config["timeframe_fixed"][bar]["minimum_events_total"]),
                int(
                    config["timeframe_fixed"][bar][
                        "minimum_events_per_development_fold"
                    ]
                ),
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
            trigger = arm.get("trigger_r")
            row = {
                "bar": bar,
                "protection_arm": label,
                "protection_trigger_r": np.nan if trigger is None else float(trigger),
                "protection_disabled": trigger is None,
                "distance_from_1.50r": (
                    DISABLED_TRIGGER_R
                    if trigger is None
                    else abs(float(trigger) - 1.5)
                ),
                "candidate_rows": len(candidates),
                "decision_rows": len(decisions),
                "all_folds_gross_positive": gross_positive_in_all_folds(
                    events, folds
                ),
                **metrics,
                **add_control_metrics({}, pairs),
            }
            rows.append(row)
            ledgers[label] = decisions, events, controls, pairs
            write_csv(
                events,
                RESULTS / f"development_{bar}_{label.replace('_', '-')}_trades.csv.gz",
            )
            print(
                f"[{bar}] {label}: robust={metrics['robust_score_bp']:.2f}bp "
                f"net={metrics['mean_net_bp']:.2f}bp n={metrics['events']}",
                flush=True,
            )
        baseline = next(
            row
            for row in rows
            if row["protection_arm"] == config["factor"]["initial_label"]
        )
        selected, reason = select_trigger(rows, baseline)
        label = str(selected["protection_arm"])
        decisions, events, controls, pairs = ledgers[label]
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
            "selected_protection_arm": label,
            "selected_trigger_r": selected["protection_trigger_r"],
            "selected_protection_disabled": bool(selected["protection_disabled"]),
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
        label = str(selection["timeframes"][bar]["selected_protection_arm"])
        arm = next(row for row in config["factor"]["arms"] if row["label"] == label)
        decisions, events, arm_config = run_trigger_arm(
            candidates, frame, config, bar, params, arm
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
        slices = fold_table(events, list(config["window"]["audit_slices"]), labeler=audit_slice_label)
        complete = slices[slices["fold"].isin(["2025H1", "2025H2"])]
        passed = bool(
            float(metrics["mean_net_bp"]) > 0.0
            and float(metrics["matched_control_excess_bp"]) > 0.0
            and float(metrics["paired_signflip_p_one_sided"]) < 0.01
            and len(complete) == 2
            and complete["mean_net_bp"].gt(0.0).all()
        )
        rows.append({"bar": bar, "protection_arm": label, **metrics, "success_gate_passed": passed})
        write_csv(events, RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        write_csv(decisions, RESULTS / f"audit_{bar}_selected_decisions.csv.gz")
        write_csv(slices, RESULTS / f"audit_{bar}_selected_slices.csv")
        write_csv(controls, RESULTS / f"audit_{bar}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"audit_{bar}_matched_pairs.csv")
        summary["timeframes"][bar] = {
            "selected_protection_arm": label,
            "metrics": metrics,
            "slices": slices.to_dict("records"),
            "success_gate_passed": passed,
            "source": quality,
        }
    write_csv(pd.DataFrame(rows), RESULTS / "audit_metrics.csv")
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "audit"), required=True)
    args = parser.parse_args()
    config = load_config()
    if utc(config["window"]["audit_end_exclusive"]) >= utc(
        config["window"]["holdout_start"]
    ):
        raise RuntimeError("configured audit boundary reaches repository holdout")
    if args.phase == "development":
        development_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
