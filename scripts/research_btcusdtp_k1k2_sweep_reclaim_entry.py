#!/usr/bin/env python3
"""Test one causal entry family after a frozen K1-to-K2 signal.

Signal features use OHLCV through K2. A sweep-reclaim arm reads only completed
bars after K2 through its registered confirmation window and enters at the
next open. Outcome resolution alone reads the subsequent 12-hour OHLC path.
The physical source ends before the repository holdout at 2026-05-04.
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
    add_control_metrics,
    build_matched_controls,
    metric_row,
    resolve_exit,
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
    "exp-btcusdtp-k1k2-sweep-reclaim-entry-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BAR_DELTAS = {"15m": pd.Timedelta(minutes=15), "5m": pd.Timedelta(minutes=5)}


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def wait_bars(config: dict[str, Any], bar: str, max_wait_minutes: int) -> int:
    minutes_per_bar = int(config["timeframe_fixed"][bar]["minutes_per_bar"])
    if max_wait_minutes <= 0 or max_wait_minutes % minutes_per_bar:
        raise ValueError("positive wait must be an exact timeframe multiple")
    return max_wait_minutes // minutes_per_bar


def is_sweep_reclaim(
    row: pd.Series,
    direction: int,
    k2_extreme: float,
) -> bool:
    """Use only confirmation-bar OHLC and its contemporaneous causal MA."""

    if direction > 0:
        swept = float(row["low"]) < k2_extreme
        reclaimed = (
            float(row["close"]) > k2_extreme
            and float(row["close"]) > float(row["sma40_hl2"])
            and float(row["close"]) > float(row["open"])
        )
    else:
        swept = float(row["high"]) > k2_extreme
        reclaimed = (
            float(row["close"]) < k2_extreme
            and float(row["close"]) < float(row["sma40_hl2"])
            and float(row["close"]) < float(row["open"])
        )
    return bool(swept and reclaimed)


def is_direction_breakout(
    row: pd.Series,
    direction: int,
    k2_direction_extreme: float,
) -> bool:
    """Confirm continuation using only the completed confirmation candle."""

    if direction > 0:
        return bool(
            float(row["close"]) > k2_direction_extreme
            and float(row["close"]) > float(row["sma40_hl2"])
            and float(row["close"]) > float(row["open"])
        )
    return bool(
        float(row["close"]) < k2_direction_extreme
        and float(row["close"]) < float(row["sma40_hl2"])
        and float(row["close"]) < float(row["open"])
    )


def run_sweep_arm(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    arm: dict[str, Any],
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Delay a trade until a completed causal sweep-reclaim candle exists."""

    max_wait_minutes = int(arm["max_wait_minutes"])
    if max_wait_minutes == 0:
        decisions, events = run_immediate_arm(
            candidates, frame, config, bar, params, 0.0
        )
        for table in (decisions, events):
            if len(table):
                table["entry_rule"] = str(arm["label"])
                table["max_wait_minutes"] = 0
                table["confirmation_i"] = table["k2_i"]
                table["confirmation_time"] = table["entry_time"] - BAR_DELTAS[bar]
                table["confirmation_delay_bars"] = 0
        return decisions, events

    bars = wait_bars(config, bar, max_wait_minutes)
    confirmation_mode = str(
        config.get("factor", {}).get("confirmation_mode", "sweep_reclaim")
    )
    if confirmation_mode not in {"sweep_reclaim", "direction_breakout"}:
        raise ValueError(f"unsupported confirmation_mode: {confirmation_mode}")
    execution = config["execution_frozen"]
    fixed = config["timeframe_fixed"][bar]
    horizon = int(fixed["horizon_bars"])
    cost = float(execution["round_trip_cost_fraction"])
    proposals: list[dict[str, Any]] = []
    final_decisions: list[dict[str, Any]] = []

    for base in candidates.to_dict("records"):
        k2_i = int(base["k2_i"])
        direction = int(base["direction"])
        k2_extreme = float(
            frame.loc[k2_i, "low"] if direction > 0 else frame.loc[k2_i, "high"]
        )
        k2_direction_extreme = float(
            frame.loc[k2_i, "high"] if direction > 0 else frame.loc[k2_i, "low"]
        )
        trigger_extreme = (
            k2_extreme
            if confirmation_mode == "sweep_reclaim"
            else k2_direction_extreme
        )
        confirmation_i: int | None = None
        for index in range(k2_i + 1, min(k2_i + bars + 1, len(frame) - 1)):
            if int(frame.loc[index, "segment_id"]) != int(
                frame.loc[k2_i, "segment_id"]
            ):
                break
            confirmed = (
                is_sweep_reclaim(frame.loc[index], direction, trigger_extreme)
                if confirmation_mode == "sweep_reclaim"
                else is_direction_breakout(
                    frame.loc[index], direction, trigger_extreme
                )
            )
            if confirmed:
                confirmation_i = index
                break
        common = {
            **base,
            "bar": bar,
            "entry_rule": str(arm["label"]),
            "confirmation_mode": confirmation_mode,
            "max_wait_minutes": max_wait_minutes,
            "k2_extreme_price": k2_extreme,
            "k2_stop_buffer_atr": 0.0,
        }
        if confirmation_i is None:
            final_decisions.append(
                {
                    **common,
                    "confirmation_i": np.nan,
                    "confirmation_time": pd.NaT,
                    "confirmation_delay_bars": np.nan,
                    "decision": "no_sweep_reclaim_within_window",
                }
            )
            continue
        entry_i = confirmation_i + 1
        last_i = entry_i + horizon - 1
        if (
            last_i >= len(frame)
            or int(frame.loc[last_i, "segment_id"])
            != int(frame.loc[k2_i, "segment_id"])
            or frame.loc[last_i, "open_time"] + BAR_DELTAS[bar] > end
        ):
            final_decisions.append(
                {
                    **common,
                    "confirmation_i": confirmation_i,
                    "confirmation_time": frame.loc[confirmation_i, "open_time"],
                    "confirmation_delay_bars": confirmation_i - k2_i,
                    "decision": "insufficient_post_entry_horizon",
                }
            )
            continue
        entry = float(frame.loc[entry_i, "open"])
        atr = float(frame.loc[k2_i, "atr"])
        stop = k2_extreme
        risk = direction * (entry - stop)
        risk_atr = risk / atr if atr > 0.0 else float("nan")
        risk_fraction = risk / entry if entry > 0.0 else float("nan")
        fee_to_risk = (
            cost / risk_fraction if risk_fraction > 0.0 else float("inf")
        )
        reason = "proposed"
        if not np.isfinite(risk_atr) or risk <= 0.0:
            reason = "nonpositive_or_nonfinite_risk"
        elif risk_atr < float(execution["next_open_risk_atr_min"]):
            reason = "risk_atr_below_min"
        elif risk_atr > float(execution["next_open_risk_atr_max"]):
            reason = "risk_atr_above_max"
        elif fee_to_risk > float(execution["fee_to_risk_max"]):
            reason = "fee_to_risk_above_max"
        proposal = {
            **common,
            "confirmation_i": confirmation_i,
            "confirmation_time": frame.loc[confirmation_i, "open_time"],
            "confirmation_delay_bars": confirmation_i - k2_i,
            "entry_i": entry_i,
            "entry_time": frame.loc[entry_i, "open_time"],
            "entry_price": entry,
            "stop_price": stop,
            "risk_price": risk,
            "risk_fraction": risk_fraction,
            "stop_distance_atr": risk_atr,
            "fee_to_risk": fee_to_risk,
            "decision": reason,
        }
        if reason == "proposed":
            proposals.append(proposal)
        else:
            final_decisions.append(proposal)

    accepted: list[dict[str, Any]] = []
    last_entry = -10**12
    last_k1: dict[int, int | None] = {1: None, -1: None}
    ordered = sorted(
        proposals,
        key=lambda row: (
            int(row["entry_i"]),
            -float(row["secondary_score"]),
            -int(row["direction"]),
            int(row["gap_bars"]),
            int(row["k2_i"]),
        ),
    )
    accepted_on_entry: int | None = None
    for proposal in ordered:
        entry_i = int(proposal["entry_i"])
        direction = int(proposal["direction"])
        reason = "accepted"
        if accepted_on_entry == entry_i:
            reason = "same_entry_lower_rank"
        elif entry_i - last_entry < int(fixed["cooldown_bars"]):
            reason = "cooldown"
        elif (
            last_k1[direction] is not None
            and int(proposal["k1_i"]) == last_k1[direction]
        ):
            reason = "same_k1_reuse"
        decision = {**proposal, "decision": reason}
        final_decisions.append(decision)
        if reason != "accepted":
            continue
        setup = (
            f"BTC-USDT-SWAP|{bar}|ma{int(params['ma_period'])}|{direction}|"
            f"{frame.loc[int(proposal['k2_i']), 'open_time'].isoformat()}|"
            f"{int(proposal['k1_i'])}|{confirmation_mode}{max_wait_minutes}|"
            f"confirm{int(proposal['confirmation_i'])}"
        )
        event = {
            **decision,
            "setup_id": hashlib.sha256(setup.encode()).hexdigest()[:16],
            "target_price": float(proposal["entry_price"])
            + direction
            * float(proposal["risk_price"])
            * float(execution["target_r"]),
            "score_floor": float(params["score_floor"]),
            "gap_min_bars": int(params["gap_min_bars"]),
            "gap_max_bars": int(params["gap_max_bars"]),
        }
        accepted.append(event)
        accepted_on_entry = entry_i
        last_entry = entry_i
        last_k1[direction] = int(proposal["k1_i"])

    events = pd.DataFrame(accepted)
    if len(events):
        outcomes = [
            resolve_exit(frame, row, config, bar)
            for row in events.to_dict("records")
        ]
        events = pd.DataFrame(
            [
                {**event, **outcome}
                for event, outcome in zip(events.to_dict("records"), outcomes)
            ]
        ).sort_values("entry_i", kind="mergesort").reset_index(drop=True)
    decisions = pd.DataFrame(final_decisions)
    if len(decisions) and "entry_i" in decisions:
        decisions = decisions.sort_values(
            ["k2_i", "secondary_score"],
            ascending=[True, False],
            kind="mergesort",
        ).reset_index(drop=True)
    return decisions, events


def select_entry(
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
        return baseline, "retain_immediate_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            int(row["max_wait_minutes"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def development_phase(
    config: dict[str, Any],
    *,
    results: Path = RESULTS,
    selection_path: Path = SELECTION_PATH,
    config_path: Path = CONFIG_PATH,
    script_path: Path = SCRIPT_PATH,
    engine_path: Path = SCRIPT_PATH,
) -> None:
    results.mkdir(parents=True, exist_ok=True)
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    receipt: dict[str, Any] = {
        "phase": "development_complete_audit_unopened",
        "config_sha256": sha256_file(config_path),
        "script_sha256": sha256_file(script_path),
        "engine_sha256": sha256_file(engine_path),
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
        ledgers: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]] = {}
        for arm in config["factor"]["arms"]:
            label = str(arm["label"])
            decisions, events = run_sweep_arm(
                candidates, frame, config, bar, params, arm, end
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
                config,
                bar,
                start,
                end,
                set(events["k2_i"].astype(int)) if len(events) else set(),
            )
            row = {
                "bar": bar,
                "entry_rule": label,
                "max_wait_minutes": int(arm["max_wait_minutes"]),
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
            safe = label.replace("_", "-")
            write_csv(events, results / f"development_{bar}_{safe}_trades.csv.gz")
            print(
                f"[{bar}] {label}: robust={metrics['robust_score_bp']:.2f}bp "
                f"net={metrics['mean_net_bp']:.2f}bp n={metrics['events']}",
                flush=True,
            )
        baseline = next(
            row
            for row in rows
            if row["entry_rule"] == config["factor"]["initial"]["label"]
        )
        selected, reason = select_entry(rows, baseline)
        label = str(selected["entry_rule"])
        decisions, events, controls, pairs = ledgers[label]
        success = bool(
            bool(selected["eligible"])
            and float(selected["mean_net_bp"]) > 0.0
            and float(selected["robust_score_bp"]) > 0.0
            and float(selected["worst_fold_net_bp"]) > -5.0
            and bool(selected["all_folds_gross_positive"])
        )
        prefix = results / f"development_{bar}"
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
            "selected_entry_rule": label,
            "selected_max_wait_minutes": int(selected["max_wait_minutes"]),
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

    write_csv(pd.concat(traces, ignore_index=True), results / "development_trace.csv")
    write_csv(pd.DataFrame(sources), results / "source_receipt.csv")
    write_json(selection_path, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_selection_committed(
    selection: dict[str, Any],
    *,
    selection_path: Path = SELECTION_PATH,
    config_path: Path = CONFIG_PATH,
    script_path: Path = SCRIPT_PATH,
    engine_path: Path = SCRIPT_PATH,
) -> None:
    paths = [
        str(selection_path.relative_to(PROJECT)),
        str(script_path.relative_to(PROJECT)),
        str(config_path.relative_to(PROJECT)),
    ]
    if engine_path not in {selection_path, script_path, config_path}:
        paths.append(str(engine_path.relative_to(PROJECT)))
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
    if selection.get("config_sha256") != sha256_file(config_path):
        raise RuntimeError("selection config SHA drift")
    if selection.get("script_sha256") != sha256_file(script_path):
        raise RuntimeError("selection script SHA drift")
    if selection.get("engine_sha256") != sha256_file(engine_path):
        raise RuntimeError("selection engine SHA drift")


def audit_phase(
    config: dict[str, Any],
    *,
    results: Path = RESULTS,
    selection_path: Path = SELECTION_PATH,
    config_path: Path = CONFIG_PATH,
    script_path: Path = SCRIPT_PATH,
    engine_path: Path = SCRIPT_PATH,
) -> None:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert_selection_committed(
        selection,
        selection_path=selection_path,
        config_path=config_path,
        script_path=script_path,
        engine_path=engine_path,
    )
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
        label = str(selection["timeframes"][bar]["selected_entry_rule"])
        arm = next(row for row in config["factor"]["arms"] if row["label"] == label)
        decisions, events = run_sweep_arm(
            candidates, frame, config, bar, params, arm, end
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
        rows.append({"bar": bar, "entry_rule": label, **metrics, "success_gate_passed": passed})
        write_csv(events, results / f"audit_{bar}_selected_trades.csv.gz")
        write_csv(decisions, results / f"audit_{bar}_selected_decisions.csv.gz")
        write_csv(slices, results / f"audit_{bar}_selected_slices.csv")
        write_csv(controls, results / f"audit_{bar}_matched_controls.csv.gz")
        write_csv(pairs, results / f"audit_{bar}_matched_pairs.csv")
        summary["timeframes"][bar] = {
            "selected_entry_rule": label,
            "metrics": metrics,
            "slices": slices.to_dict("records"),
            "success_gate_passed": passed,
            "source": quality,
        }
    write_csv(pd.DataFrame(rows), results / "audit_metrics.csv")
    write_json(results / "audit_summary.json", summary)
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
