#!/usr/bin/env python3
"""Test one causal factor: position fraction retained after a 3R first take.

Candidate construction and execution gates use completed data through K2+1
open. Only ``resolve_runner_exit`` reads the registered 12-hour future path.
The physical source ends before repository holdout at 2026-05-04.
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
    BAR_DELTAS,
    add_control_metrics,
    atr_quintiles,
    metric_row,
)
from scripts.research_btcusdtp_k1k2_stop_buffer import (
    apply_execution_gates,
    gross_positive_in_all_folds,
    signal_params,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/exp-btcusdtp-k1k2-partial-runner-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_runner_exit(
    frame: pd.DataFrame,
    event: dict[str, Any],
    config: dict[str, Any],
    bar: str,
    runner_fraction: float,
) -> dict[str, Any]:
    """Resolve weighted partial exits with causal next-bar protection.

    Reads only OHLC rows from ``entry_i`` through the registered horizon.
    Stop/target collisions are conservative stop-first. The 3R and 8R prices
    are barrier fills; aggregate cost is charged exactly once after weighting.
    """

    if not 0.0 <= runner_fraction <= 1.0:
        raise ValueError("runner_fraction must be in [0, 1]")
    execution = config["execution_frozen"]
    factor = config["factor"]
    horizon = int(config["timeframe_fixed"][bar]["horizon_bars"])
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    risk = float(event["risk_price"])
    risk_fraction = float(event["risk_fraction"])
    stop = float(event["stop_price"])
    first_r = float(factor["first_take_r"])
    runner_r = float(factor["runner_target_r"])
    first_target = entry + direction * risk * first_r
    runner_target = entry + direction * risk * runner_r
    cost = float(execution["round_trip_cost_fraction"])
    trigger = float(execution["profit_protection_trigger_close_r"])
    fee_cover = entry * (1.0 + direction * cost)
    protection_active = False
    protection_armed_i: int | None = None
    first_take_i: int | None = None
    first_take_gross = 0.0
    exit_i: int | None = None
    last_exit_price: float | None = None
    aggregate_gross: float | None = None
    outcome = ""
    runner_outcome = ""
    mfe = 0.0
    mae = 0.0
    horizon_mfe = 0.0
    horizon_mae = 0.0

    for i in range(entry_i, entry_i + horizon):
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        close = float(frame.loc[i, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        horizon_mfe = max(horizon_mfe, favourable)
        horizon_mae = max(horizon_mae, adverse)
        if exit_i is not None:
            continue
        mfe = max(mfe, favourable)
        mae = max(mae, adverse)
        active_stop = fee_cover if protection_active else stop
        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        hit_first = high >= first_target if direction > 0 else low <= first_target
        hit_runner = high >= runner_target if direction > 0 else low <= runner_target

        if hit_stop:
            exit_i = i
            last_exit_price = active_stop
            final_leg_gross = direction * (active_stop / entry - 1.0)
            target_hit_is_execution_relevant = (
                hit_runner if runner_fraction >= 1.0 else hit_first
            )
            if first_take_i is None:
                aggregate_gross = final_leg_gross
                outcome = (
                    "protected_stop_ambiguous"
                    if protection_active and target_hit_is_execution_relevant
                    else "sl_ambiguous"
                    if target_hit_is_execution_relevant
                    else "protected_stop"
                    if protection_active
                    else "sl"
                )
                runner_outcome = "pre_take_protected_stop" if protection_active else "pre_take_sl"
            else:
                aggregate_gross = first_take_gross + runner_fraction * final_leg_gross
                outcome = "protected_stop" if protection_active else "sl"
                runner_outcome = "runner_protected_stop" if protection_active else "runner_sl"
            if target_hit_is_execution_relevant:
                runner_outcome += "_ambiguous_stop_first"
            continue

        if first_take_i is None and hit_first:
            first_take_i = i
            first_leg_fraction = 1.0 - runner_fraction
            first_take_gross = first_leg_fraction * first_r * risk_fraction
            if runner_fraction <= 0.0:
                exit_i = i
                last_exit_price = first_target
                aggregate_gross = first_take_gross
                outcome = "tp"
                runner_outcome = "full_exit_at_first_take"
                continue
            if hit_runner:
                exit_i = i
                last_exit_price = runner_target
                aggregate_gross = (
                    first_take_gross + runner_fraction * runner_r * risk_fraction
                )
                outcome = "tp"
                runner_outcome = "runner_tp_same_bar"
                continue
        elif first_take_i is not None and hit_runner:
            exit_i = i
            last_exit_price = runner_target
            aggregate_gross = (
                first_take_gross + runner_fraction * runner_r * risk_fraction
            )
            outcome = "tp"
            runner_outcome = "runner_tp"
            continue

        if not protection_active and direction * (close - entry) / risk >= trigger:
            protection_active = True
            protection_armed_i = i

    if exit_i is None:
        exit_i = entry_i + horizon - 1
        last_exit_price = float(frame.loc[exit_i, "close"])
        final_leg_gross = direction * (last_exit_price / entry - 1.0)
        if first_take_i is None:
            aggregate_gross = final_leg_gross
            runner_outcome = "pre_take_timeout"
        else:
            aggregate_gross = first_take_gross + runner_fraction * final_leg_gross
            runner_outcome = "runner_timeout"
        outcome = "timeout"

    assert aggregate_gross is not None and last_exit_price is not None
    effective_exit = entry * (1.0 + direction * aggregate_gross)
    return {
        "resolved": True,
        "outcome": outcome,
        "runner_outcome": runner_outcome,
        "runner_fraction": float(runner_fraction),
        "first_take_r": first_r,
        "runner_target_r": runner_r,
        "first_take_i": first_take_i,
        "first_take_time": (
            frame.loc[first_take_i, "open_time"] + BAR_DELTAS[bar]
            if first_take_i is not None
            else pd.NaT
        ),
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTAS[bar],
        "exit_price": float(effective_exit),
        "last_exit_price": float(last_exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": float(aggregate_gross),
        "net_return": float(aggregate_gross - cost),
        "return_r": float(aggregate_gross / risk_fraction),
        "net_return_r": float((aggregate_gross - cost) / risk_fraction),
        "mfe_r": mfe / risk,
        "mae_r": mae / risk,
        "horizon_mfe_r": horizon_mfe / risk,
        "horizon_mae_r": horizon_mae / risk,
        "horizon_hit_4r": bool(horizon_mfe >= 4.0 * risk),
        "horizon_hit_5r": bool(horizon_mfe >= 5.0 * risk),
        "horizon_hit_6r": bool(horizon_mfe >= 6.0 * risk),
        "protection_armed": protection_armed_i is not None,
        "protection_armed_i": protection_armed_i,
    }


def run_runner_arm(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    runner_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    accepted, decisions = apply_execution_gates(
        candidates, frame, config, bar, params, 0.0
    )
    for table in (accepted, decisions):
        if len(table):
            table["runner_fraction"] = float(runner_fraction)
    if accepted.empty:
        return decisions, accepted
    outcomes = [
        resolve_runner_exit(frame, row, config, bar, runner_fraction)
        for row in accepted.to_dict("records")
    ]
    events = pd.DataFrame(
        [{**event, **outcome} for event, outcome in zip(accepted.to_dict("records"), outcomes)]
    )
    return decisions, events


def build_runner_controls(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    inherited_signal_indices: set[int],
    runner_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build exact-stratum controls resolved with the same runner state machine."""

    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    horizon = int(config["timeframe_fixed"][bar]["horizon_bars"])
    n = len(frame)
    eligible = np.zeros(n, dtype=bool)
    for signal_i in range(n - horizon - 1):
        entry_i = signal_i + 1
        last = entry_i + horizon - 1
        eligible[signal_i] = bool(
            frame.loc[entry_i, "open_time"] >= start
            and frame.loc[entry_i, "open_time"] < end
            and frame.loc[last, "open_time"] + BAR_DELTAS[bar] <= end
            and int(frame.loc[signal_i, "segment_id"]) == int(frame.loc[last, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
        )
    excluded = np.zeros(n, dtype=bool)
    radius = horizon + 1
    for index in inherited_signal_indices:
        excluded[max(0, index - radius) : min(n, index + radius + 1)] = True
    buckets = atr_quintiles(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & ~excluded & (buckets >= 0)):
        pool.setdefault((str(months[index]), int(blocks[index]), int(buckets[index])), []).append(int(index))
    required = int(config["matched_control"]["controls_per_trade"])
    seed = str(config["matched_control"]["seed"])
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        signal_i = int(event["k2_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            pool.get(key, []),
            key=lambda index: hashlib.sha256(
                f"{seed}|{bar}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pairs.append(
                {
                    "bar": bar,
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched_insufficient_exact_stratum",
                    "matched_control_count": len(choices),
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": np.nan,
                    "paired_excess_return": np.nan,
                }
            )
            continue
        current: list[float] = []
        for rank, control_i in enumerate(choices[:required]):
            entry_i = control_i + 1
            entry = float(frame.loc[entry_i, "open"])
            direction = int(event["direction"])
            risk = float(event["stop_distance_atr"]) * float(frame.loc[control_i, "atr"])
            control_event = {
                "entry_i": entry_i,
                "direction": direction,
                "entry_price": entry,
                "risk_price": risk,
                "risk_fraction": risk / entry,
                "stop_price": entry - direction * risk,
            }
            result = resolve_runner_exit(
                frame, control_event, config, bar, runner_fraction
            )
            current.append(float(result["net_return"]))
            controls.append(
                {
                    "bar": bar,
                    "candidate_setup_id": event["setup_id"],
                    "control_rank": rank,
                    "control_i": control_i,
                    "control_time": frame.loc[control_i, "open_time"],
                    "direction": direction,
                    "month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    "copied_stop_distance_atr": event["stop_distance_atr"],
                    **result,
                }
            )
        mean = float(np.mean(current))
        pairs.append(
            {
                "bar": bar,
                "setup_id": event["setup_id"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_net_return": event["net_return"],
                "control_mean_net_return": mean,
                "paired_excess_return": float(event["net_return"]) - mean,
            }
        )
    return pd.DataFrame(controls), pd.DataFrame(pairs)


def select_runner(
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
        return baseline, "retain_full_exit_at_3r_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            float(row["runner_fraction"]),
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
            fraction = float(value)
            decisions, events = run_runner_arm(
                candidates, frame, config, bar, params, fraction
            )
            metrics = robust_metrics(
                events,
                folds,
                int(config["timeframe_fixed"][bar]["minimum_events_total"]),
                int(config["timeframe_fixed"][bar]["minimum_events_per_development_fold"]),
            )
            controls, pairs = build_runner_controls(
                events,
                frame,
                config,
                bar,
                start,
                end,
                set(events["k2_i"].astype(int)) if len(events) else set(),
                fraction,
            )
            outcomes = events["runner_outcome"].astype(str) if len(events) else pd.Series(dtype=str)
            row = {
                "bar": bar,
                "runner_fraction": fraction,
                "first_take_fraction": 1.0 - fraction,
                "candidate_rows": len(candidates),
                "decision_rows": len(decisions),
                "runner_activated": int(events["first_take_i"].notna().sum()) if len(events) else 0,
                "runner_tp": int(outcomes.str.startswith("runner_tp").sum()),
                "all_folds_gross_positive": gross_positive_in_all_folds(events, folds),
                **metrics,
                **add_control_metrics({}, pairs),
            }
            rows.append(row)
            ledgers[fraction] = decisions, events, controls, pairs
            write_csv(events, RESULTS / f"development_{bar}_runner-{fraction:g}_trades.csv.gz")
            print(
                f"[{bar}] runner={fraction:.0%}: robust={metrics['robust_score_bp']:.2f}bp "
                f"net={metrics['mean_net_bp']:.2f}bp n={metrics['events']}",
                flush=True,
            )
        baseline = next(row for row in rows if np.isclose(float(row["runner_fraction"]), 0.0))
        selected, reason = select_runner(rows, baseline)
        fraction = float(selected["runner_fraction"])
        decisions, events, controls, pairs = ledgers[fraction]
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
            "selected_runner_fraction": fraction,
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
        frame, universe, candidates, params, quality = load_candidates(config, bar, start, end)
        fraction = float(selection["timeframes"][bar]["selected_runner_fraction"])
        decisions, events = run_runner_arm(candidates, frame, config, bar, params, fraction)
        controls, pairs = build_runner_controls(
            events,
            frame,
            config,
            bar,
            start,
            end,
            set(events["k2_i"].astype(int)) if len(events) else set(),
            fraction,
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
        rows.append({"bar": bar, "runner_fraction": fraction, **metrics, "success_gate_passed": passed})
        write_csv(events, RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        write_csv(decisions, RESULTS / f"audit_{bar}_selected_decisions.csv.gz")
        write_csv(slices, RESULTS / f"audit_{bar}_selected_slices.csv")
        write_csv(controls, RESULTS / f"audit_{bar}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"audit_{bar}_matched_pairs.csv")
        summary["timeframes"][bar] = {
            "selected_runner_fraction": fraction,
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
