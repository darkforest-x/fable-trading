#!/usr/bin/env python3
"""Test one causal execution factor: ATR buffer outside the K2 extreme.

Signal candidates use completed OHLCV through K2 only and are imported from
the frozen independent-timeframe morphology implementation. Entry economics
read K2+1 open; the stop is K2 extreme plus a registered multiple of ATR14 at
K2. Only outcome resolution reads the later 12-hour path. The physical source
ends before the repository holdout at 2026-05-04.
"""
from __future__ import annotations

import argparse
import hashlib
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
    resolve_exit,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-stop-buffer-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def signal_params(config: dict[str, Any], bar: str) -> dict[str, Any]:
    frozen = config["signal_frozen"][bar]
    return {
        "ma_period": int(frozen["ma_period"]),
        "gap_min_bars": int(frozen["gap_min_bars"]),
        "gap_max_bars": int(frozen["gap_max_bars"]),
        "score_floor": float(frozen["score_floor"]),
    }


def apply_execution_gates(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    buffer_atr: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply next-open economics using only K2 ATR and K2+1 open.

    Columns used beyond the causal candidate record are K2 ``low``, ``high``,
    and ``atr`` plus K2+1 ``open``. No later OHLC row is accessed here.
    """

    if buffer_atr < 0.0:
        raise ValueError("buffer_atr must be non-negative")
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
    execution = config["execution_frozen"]
    fixed = config["timeframe_fixed"][bar]
    cost = float(execution["round_trip_cost_fraction"])
    cooldown = int(fixed["cooldown_bars"])
    risk_min = float(execution["next_open_risk_atr_min"])
    risk_max = float(execution["next_open_risk_atr_max"])
    fee_max = float(execution["fee_to_risk_max"])
    target_r = float(execution["target_r"])
    accepted: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    last_entry = -10**12
    last_k1: dict[int, int | None] = {1: None, -1: None}

    ordered = candidates.sort_values(
        ["k2_i", "secondary_score", "direction", "gap_bars"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    for k2_i, same_bar in ordered.groupby("k2_i", sort=True):
        accepted_on_bar = False
        for base in same_bar.to_dict("records"):
            direction = int(base["direction"])
            k2_i = int(k2_i)
            entry_i = k2_i + 1
            entry = float(frame.loc[entry_i, "open"])
            atr = float(frame.loc[k2_i, "atr"])
            k2_extreme = float(
                frame.loc[k2_i, "low"]
                if direction > 0
                else frame.loc[k2_i, "high"]
            )
            stop = k2_extreme - direction * buffer_atr * atr
            risk = direction * (entry - stop)
            risk_atr = risk / atr if atr > 0.0 else float("nan")
            risk_fraction = risk / entry if entry > 0.0 else float("nan")
            fee_to_risk = (
                cost / risk_fraction if risk_fraction > 0.0 else float("inf")
            )
            reason = "accepted"
            if accepted_on_bar:
                reason = "same_k2_lower_rank"
            elif not np.isfinite(risk_atr) or risk <= 0.0:
                reason = "nonpositive_or_nonfinite_risk"
            elif risk_atr < risk_min:
                reason = "risk_atr_below_min"
            elif risk_atr > risk_max:
                reason = "risk_atr_above_max"
            elif fee_to_risk > fee_max:
                reason = "fee_to_risk_above_max"
            elif entry_i - last_entry < cooldown:
                reason = "cooldown"
            elif (
                last_k1[direction] is not None
                and int(base["k1_i"]) == last_k1[direction]
            ):
                reason = "same_k1_reuse"
            decision = {
                **base,
                "bar": bar,
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": entry,
                "k2_extreme_price": k2_extreme,
                "k2_stop_buffer_atr": float(buffer_atr),
                "stop_price": stop,
                "risk_price": risk,
                "risk_fraction": risk_fraction,
                "stop_distance_atr": risk_atr,
                "fee_to_risk": fee_to_risk,
                "decision": reason,
            }
            decisions.append(decision)
            if reason != "accepted":
                continue
            setup = (
                f"BTC-USDT-SWAP|{bar}|ma{int(params['ma_period'])}|{direction}|"
                f"{frame.loc[k2_i, 'open_time'].isoformat()}|{int(base['k1_i'])}|"
                f"buffer{buffer_atr:.4f}"
            )
            event = {
                **decision,
                "setup_id": hashlib.sha256(setup.encode()).hexdigest()[:16],
                "target_price": entry + direction * risk * target_r,
                "score_floor": float(params["score_floor"]),
                "gap_min_bars": int(params["gap_min_bars"]),
                "gap_max_bars": int(params["gap_max_bars"]),
            }
            accepted.append(event)
            accepted_on_bar = True
            last_entry = entry_i
            last_k1[direction] = int(base["k1_i"])
    accepted_frame = pd.DataFrame(accepted)
    if len(accepted_frame):
        accepted_frame = accepted_frame.sort_values(
            "entry_i", kind="mergesort"
        ).reset_index(drop=True)
    return accepted_frame, pd.DataFrame(decisions)


def run_arm(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    buffer_atr: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    accepted, decisions = apply_execution_gates(
        candidates, frame, config, bar, params, buffer_atr
    )
    if accepted.empty:
        return decisions, accepted
    outcomes = [
        resolve_exit(frame, row, config, bar)
        for row in accepted.to_dict("records")
    ]
    events = pd.DataFrame(
        [
            {**event, **outcome}
            for event, outcome in zip(accepted.to_dict("records"), outcomes)
        ]
    )
    return decisions, events


def gross_positive_in_all_folds(events: pd.DataFrame, folds: list[str]) -> bool:
    table = fold_table(events, folds)
    return bool(
        len(table) == len(folds)
        and table["events"].gt(0).all()
        and table["mean_gross_bp"].gt(0.0).all()
    )


def select_buffer(
    rows: list[dict[str, Any]], baseline: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    eligible = [row for row in rows if bool(row["eligible"])]
    passing = [
        row
        for row in eligible
        if float(row["robust_score_bp"])
        >= float(baseline["robust_score_bp"]) + 2.0
        and float(row["worst_fold_net_bp"])
        >= float(baseline["worst_fold_net_bp"]) - 3.0
    ]
    if not passing:
        return baseline, "retain_zero_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            float(row["k2_stop_buffer_atr"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def development_phase(config: dict[str, Any]) -> None:
    """Evaluate the registered buffer grid without opening the audit period."""

    RESULTS.mkdir(parents=True, exist_ok=True)
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    all_trace: list[pd.DataFrame] = []
    source_rows: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {
        "phase": "development_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "holdout_rows_read": 0,
        "audit_rows_read": 0,
        "timeframes": {},
    }

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
        ledgers: dict[float, tuple[pd.DataFrame, pd.DataFrame]] = {}
        for value in config["factor"]["grid"]:
            buffer_atr = float(value)
            decisions, events = run_arm(
                candidates, frame, config, bar, params, buffer_atr
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
            row = {
                "bar": bar,
                "k2_stop_buffer_atr": buffer_atr,
                "candidate_rows": len(candidates),
                "decision_rows": len(decisions),
                "all_folds_gross_positive": gross_positive_in_all_folds(
                    events, folds
                ),
                **metrics,
            }
            rows.append(row)
            ledgers[buffer_atr] = decisions, events
            print(
                f"[{bar}] buffer={buffer_atr:.2f}: robust="
                f"{metrics['robust_score_bp']:.2f}bp net="
                f"{metrics['mean_net_bp']:.2f}bp n={metrics['events']}",
                flush=True,
            )
        baseline = next(
            row
            for row in rows
            if np.isclose(
                float(row["k2_stop_buffer_atr"]),
                float(config["factor"]["initial"]),
            )
        )
        selected, reason = select_buffer(rows, baseline)
        selected_buffer = float(selected["k2_stop_buffer_atr"])
        selected_decisions, selected_events = ledgers[selected_buffer]
        success = bool(
            bool(selected["eligible"])
            and float(selected["mean_net_bp"]) > 0.0
            and float(selected["robust_score_bp"]) > 0.0
            and float(selected["worst_fold_net_bp"]) > -5.0
            and bool(selected["all_folds_gross_positive"])
        )
        prefix = RESULTS / f"development_{bar}"
        write_csv(pd.DataFrame(rows), prefix.with_name(prefix.name + "_trace.csv"))
        write_csv(
            selected_events,
            prefix.with_name(prefix.name + "_selected_trades.csv.gz"),
        )
        write_csv(
            selected_decisions,
            prefix.with_name(prefix.name + "_selected_decisions.csv.gz"),
        )
        write_csv(
            fold_table(selected_events, folds),
            prefix.with_name(prefix.name + "_selected_folds.csv"),
        )
        all_trace.append(pd.DataFrame(rows))
        source_rows.append({**quality, "bar": bar, "holdout_rows_read": 0})
        receipt["timeframes"][bar] = {
            "source": {**quality, "holdout_rows_read": 0},
            "signal_params": params,
            "selection_reason": reason,
            "selected_buffer_atr": selected_buffer,
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
            "funnel": execution_funnel(
                universe, candidates, selected_decisions, selected_events
            ),
        }
        print(f"[{bar}] {reason}; audit_open_allowed={success}", flush=True)

    write_csv(pd.concat(all_trace, ignore_index=True), RESULTS / "development_trace.csv")
    write_csv(pd.DataFrame(source_rows), RESULTS / "source_receipt.csv")
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
    """Open only development-qualified timeframe arms after a committed freeze."""

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
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "phase": "qualified_frozen_audit_complete",
        "audit_window_pristine": False,
        "qualified_timeframes": qualified,
        "holdout_rows_read": 0,
        "timeframes": {},
    }
    for bar in qualified:
        base, quality = load_base_frame(config, bar)
        params = signal_params(config, bar)
        buffer_atr = float(selection["timeframes"][bar]["selected_buffer_atr"])
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
        decisions, events = run_arm(
            candidates, frame, config, bar, params, buffer_atr
        )
        controls, pairs = build_matched_controls(
            events,
            frame,
            config,
            bar,
            start,
            end,
            set(events["k2_i"].astype(int)) if len(events) else set(),
        )
        metrics = {
            **metric_row(events),
            **add_control_metrics({}, pairs),
            **ranking_metrics(events),
        }
        slices = fold_table(
            events,
            list(config["window"]["audit_slices"]),
            labeler=audit_slice_label,
        )
        complete_2025 = slices[slices["fold"].isin(["2025H1", "2025H2"])]
        passed = bool(
            float(metrics["mean_net_bp"]) > 0.0
            and float(metrics["matched_control_excess_bp"]) > 0.0
            and float(metrics["paired_signflip_p_one_sided"]) < 0.01
            and len(complete_2025) == 2
            and complete_2025["mean_net_bp"].gt(0.0).all()
        )
        rows.append(
            {
                "bar": bar,
                "k2_stop_buffer_atr": buffer_atr,
                **metrics,
                "success_gate_passed": passed,
            }
        )
        write_csv(events, RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        write_csv(decisions, RESULTS / f"audit_{bar}_selected_decisions.csv.gz")
        write_csv(slices, RESULTS / f"audit_{bar}_selected_slices.csv")
        write_csv(controls, RESULTS / f"audit_{bar}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"audit_{bar}_matched_pairs.csv")
        summary["timeframes"][bar] = {
            "selected_buffer_atr": buffer_atr,
            "metrics": metrics,
            "slices": slices.to_dict("records"),
            "success_gate_passed": passed,
            "source": quality,
        }
    write_csv(pd.DataFrame(rows), RESULTS / "audit_metrics.csv")
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "audit"), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
