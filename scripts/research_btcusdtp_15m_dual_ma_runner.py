#!/usr/bin/env python3
"""Test a dual-reference BTCUSDT.P 15m launch plus delayed MA runner.

This follow-up freezes the parent experiment's numeric launch morphology.  It
uses one fast trigger reference for direct/rejection/coil timing and a separate
SMA60 trend reference for position management.  Selection uses only 2023 and
changes one registered categorical factor at a time: trigger reference, signal
dedupe state machine, then runner policy.  The selection receipt must be
committed before exact 2024 confirmation can run.  The 2025--2026 audit stays
closed unless every confirmation gate passes.

All signal and state-transition features use bars through completed decision
bar ``t``.  Entry is ``open[t+1]``.  Delayed runner activation and MA-close
signals use completed closes and fill at the following open; MA-trailing stops
calculated at close ``t`` can act only from ``t+1``.  Only outcome resolution
reads future bars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import load_featured
from scripts.research_btcusdtp_15m_ma_state_trend import (
    BAR_DELTA,
    _read_owner_window,
    accept_candidates,
    add_reference_features,
    build_raw_candidates,
    fold_label,
    json_value,
    metrics,
    robust_metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-dual-ma-runner-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PARENT_CONFIG_PATH = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-ma-state-trend-preholdout-20260904-v1/config.json"
)
PARENT_SELECTION_PATH = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-ma-state-trend-preholdout-20260904-v1/results/selection_receipt.json"
)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def parent_signal_config() -> dict[str, Any]:
    return json.loads(PARENT_CONFIG_PATH.read_text(encoding="utf-8"))["causal_signal"]


def load_base(config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    compatibility = {
        "source": config["source"],
        "window": {
            "holdout_start": config["window"]["holdout_start"],
            "validation_end_exclusive": config["window"]["audit_end_exclusive"],
        },
    }
    frame, quality = load_featured(compatibility, "15m")
    if int(quality["holdout_rows_read"]) != 0:
        raise RuntimeError("dual-MA loader materialized repository holdout")
    return frame, quality


def add_dual_references(
    frame: pd.DataFrame,
    trigger_reference: str,
    trend_reference: str,
) -> pd.DataFrame:
    """Add separate causal trigger and trend MAs on the same OHLC prefix."""

    trigger = add_reference_features(frame, trigger_reference)
    trend = add_reference_features(frame, trend_reference)
    trigger["trigger_reference"] = trigger_reference
    trigger["trend_reference"] = trend_reference
    trigger["trend_ma"] = trend["reference_ma"].to_numpy(dtype=float)
    trigger["trend_slope_atr_per_bar"] = trend[
        "reference_slope_atr_per_bar"
    ].to_numpy(dtype=float)
    return trigger


def _with_entry_fields(
    row: Mapping[str, Any], frame: pd.DataFrame, policy: str
) -> dict[str, Any] | None:
    signal_i = int(row["signal_i"])
    entry_i = signal_i + 1
    if entry_i >= len(frame):
        return None
    if (
        int(frame.loc[entry_i, "segment_id"]) != int(frame.loc[signal_i, "segment_id"])
        or frame.loc[entry_i, "open_time"] - frame.loc[signal_i, "open_time"]
        != BAR_DELTA
    ):
        return None
    direction = int(row["direction"])
    identity = (
        f"BTC-USDT-SWAP|15m|{direction}|{utc(row['signal_time']).isoformat()}|"
        f"{row['signal_family']}|{policy}"
    )
    return {
        **dict(row),
        "setup_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "dedupe_policy": policy,
        "entry_i": entry_i,
        "entry_time": frame.loc[entry_i, "open_time"],
        "entry_price": float(frame.loc[entry_i, "open"]),
    }


def accept_with_policy(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    policy: str,
) -> pd.DataFrame:
    """Turn raw launch states into events using one explicit dedupe state machine."""

    if policy == "global24":
        return accept_candidates(candidates, frame, cooldown_bars=24).assign(
            dedupe_policy=policy
        )
    if policy not in {"direction8", "state_reset3"}:
        raise ValueError(f"unknown dedupe policy: {policy}")
    if candidates.empty:
        return candidates.copy()

    provisional: list[dict[str, Any]] = []
    if policy == "direction8":
        last = {1: -10**12, -1: -10**12}
        for row in candidates.sort_values(
            ["signal_i", "signal_score"], ascending=[True, False], kind="mergesort"
        ).to_dict("records"):
            direction = int(row["direction"])
            signal_i = int(row["signal_i"])
            if signal_i - last[direction] < 8:
                continue
            enriched = _with_entry_fields(row, frame, policy)
            if enriched is not None:
                provisional.append(enriched)
                last[direction] = signal_i
    else:
        raw_indices = {
            direction: set(
                candidates.loc[candidates["direction"].eq(direction), "signal_i"].astype(int)
            )
            for direction in (1, -1)
        }
        for row in candidates.sort_values(
            ["signal_i", "signal_score"], ascending=[True, False], kind="mergesort"
        ).to_dict("records"):
            direction = int(row["direction"])
            signal_i = int(row["signal_i"])
            if any(signal_i - lag in raw_indices[direction] for lag in (1, 2, 3)):
                continue
            enriched = _with_entry_fields(row, frame, policy)
            if enriched is not None:
                provisional.append(enriched)

    if not provisional:
        return pd.DataFrame()
    accepted = pd.DataFrame(provisional).sort_values(
        ["signal_i", "signal_score", "direction"],
        ascending=[True, False, False],
        kind="mergesort",
    )
    return accepted.drop_duplicates("signal_i", keep="first").reset_index(drop=True)


def _stop_fill(open_price: float, stop: float, direction: int) -> float:
    if direction > 0 and open_price < stop:
        return open_price
    if direction < 0 and open_price > stop:
        return open_price
    return stop


def _resolve_leg(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    policy: str,
    horizon: int,
    hard_stop_atr: float,
    target_atr: float,
) -> dict[str, Any]:
    valid = {
        "fixed_5atr",
        "ma_close2_after_1atr",
        "ma_close2_after_2atr",
        "ma_trail1_after_1atr",
        "ma_trail1_after_2atr",
    }
    if policy not in valid:
        raise ValueError(f"unknown runner leg: {policy}")
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    hard_stop = entry - direction * hard_stop_atr * signal_atr
    target = entry + direction * target_atr * signal_atr
    active_stop = hard_stop
    wrong_closes = 0
    runner_armed = policy == "fixed_5atr"
    arm_i: int | None = None
    stop_source = "hard"
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    mfe_until_exit = 0.0
    mae_until_exit = 0.0
    horizon_mfe = 0.0
    horizon_mae = 0.0
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    if int(frame.loc[end_i, "segment_id"]) != int(frame.loc[entry_i, "segment_id"]):
        return {"resolved": False, "reason": "horizon_crosses_gap"}

    activation = 0.0
    if "after_1atr" in policy:
        activation = 1.0
    elif "after_2atr" in policy:
        activation = 2.0

    for i in range(entry_i, end_i + 1):
        open_price = float(frame.loc[i, "open"])
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        close = float(frame.loc[i, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        horizon_mfe = max(horizon_mfe, favourable)
        horizon_mae = max(horizon_mae, adverse)
        if exit_i is not None:
            continue
        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)

        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        hit_target = (
            policy == "fixed_5atr"
            and (high >= target if direction > 0 else low <= target)
        )
        if hit_stop:
            exit_i = i
            exit_price = _stop_fill(open_price, active_stop, direction)
            outcome = f"{stop_source}_stop"
            continue
        if hit_target:
            exit_i = i
            exit_price = target
            outcome = "fixed_target"
            continue

        signed_close_atr = direction * (close - entry) / signal_atr
        if not runner_armed and signed_close_atr >= activation:
            runner_armed = True
            arm_i = i
            wrong_closes = 0

        if runner_armed and policy.startswith("ma_close2"):
            wrong = direction * (close - float(frame.loc[i, "trend_ma"])) <= 0.0
            wrong_closes = wrong_closes + 1 if wrong else 0
            if wrong_closes >= 2 and i + 1 <= end_i:
                exit_i = i + 1
                exit_price = float(frame.loc[i + 1, "open"])
                outcome = "trend_ma_close2"
                continue
        elif runner_armed and policy.startswith("ma_trail1"):
            candidate = float(frame.loc[i, "trend_ma"]) - direction * float(
                frame.loc[i, "atr"]
            )
            if direction > 0 and candidate > active_stop:
                active_stop = candidate
                stop_source = "trend_ma_trail1"
            elif direction < 0 and candidate < active_stop:
                active_stop = candidate
                stop_source = "trend_ma_trail1"

    if exit_i is None:
        exit_i = end_i
        exit_price = float(frame.loc[end_i, "close"])
        outcome = "timeout"
    gross = direction * (float(exit_price) / entry - 1.0)
    return {
        "resolved": True,
        "leg_policy": policy,
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "runner_armed": runner_armed,
        "runner_arm_i": arm_i,
        "mfe_at_exit_atr": mfe_until_exit / signal_atr,
        "mae_at_exit_atr": mae_until_exit / signal_atr,
        "horizon_mfe_atr": horizon_mfe / signal_atr,
        "horizon_mae_atr": horizon_mae / signal_atr,
        "capture_of_horizon_mfe": gross * entry / horizon_mfe if horizon_mfe > 0 else np.nan,
        "gave_back_atr": (
            horizon_mfe - direction * (float(exit_price) - entry)
        )
        / signal_atr,
    }


def resolve_runner(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    policy: str,
    horizon: int,
    hard_stop_atr: float,
    target_atr: float,
) -> dict[str, Any]:
    """Resolve a full-position or 50/50 fixed-target plus MA runner."""

    prefix = "half_fixed5_half_"
    if not policy.startswith(prefix):
        return _resolve_leg(frame, event, policy, horizon, hard_stop_atr, target_atr)
    runner_policy = policy[len(prefix) :]
    fixed = _resolve_leg(
        frame, event, "fixed_5atr", horizon, hard_stop_atr, target_atr
    )
    runner = _resolve_leg(
        frame, event, runner_policy, horizon, hard_stop_atr, target_atr
    )
    if not fixed.get("resolved") or not runner.get("resolved"):
        return {"resolved": False, "reason": "one_split_leg_unresolved"}
    gross = 0.5 * (float(fixed["gross_return"]) + float(runner["gross_return"]))
    return {
        "resolved": True,
        "leg_policy": policy,
        "outcome": f"split:{fixed['outcome']}|{runner['outcome']}",
        "exit_i": max(int(fixed["exit_i"]), int(runner["exit_i"])),
        "exit_time": max(utc(fixed["exit_time"]), utc(runner["exit_time"])),
        "exit_price": np.nan,
        "hold_bars": max(int(fixed["hold_bars"]), int(runner["hold_bars"])),
        "gross_return": gross,
        "runner_armed": bool(runner["runner_armed"]),
        "runner_arm_i": runner["runner_arm_i"],
        "mfe_at_exit_atr": float(runner["mfe_at_exit_atr"]),
        "mae_at_exit_atr": float(runner["mae_at_exit_atr"]),
        "horizon_mfe_atr": float(runner["horizon_mfe_atr"]),
        "horizon_mae_atr": float(runner["horizon_mae_atr"]),
        "capture_of_horizon_mfe": 0.5
        * (
            float(fixed["capture_of_horizon_mfe"])
            + float(runner["capture_of_horizon_mfe"])
        ),
        "gave_back_atr": 0.5
        * (float(fixed["gave_back_atr"]) + float(runner["gave_back_atr"])),
        "fixed_leg_gross_return": fixed["gross_return"],
        "runner_leg_gross_return": runner["gross_return"],
        "fixed_leg_exit_i": fixed["exit_i"],
        "runner_leg_exit_i": runner["exit_i"],
    }


def resolve_events(
    accepted: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    policy: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if accepted.empty:
        return accepted.copy()
    contract = config["signal_contract"]
    horizon = int(contract["horizon_bars"])
    cost = float(contract["round_trip_cost_fraction"])
    selected: list[dict[str, Any]] = []
    for event in accepted.to_dict("records"):
        entry_i = int(event["entry_i"])
        last_i = entry_i + horizon - 1
        if not (start <= utc(event["entry_time"]) < end) or last_i >= len(frame):
            continue
        if frame.loc[last_i, "open_time"] + BAR_DELTA > end:
            continue
        result = resolve_runner(
            frame,
            event,
            policy,
            horizon,
            float(contract["initial_disaster_stop_atr"]),
            float(contract["fixed_target_atr"]),
        )
        if not result.get("resolved"):
            continue
        risk_fraction = (
            float(contract["initial_disaster_stop_atr"])
            * float(event["signal_atr"])
            / float(event["entry_price"])
        )
        gross = float(result["gross_return"])
        selected.append(
            {
                **event,
                **result,
                "runner_policy": policy,
                "net_return": gross - cost,
                "risk_fraction": risk_fraction,
                "return_r": gross / risk_fraction,
                "net_return_r": (gross - cost) / risk_fraction,
            }
        )
    return pd.DataFrame(selected)


def evaluate(
    base: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    contract = config["signal_contract"]
    frame = add_dual_references(
        base, params["trigger_reference"], str(contract["trend_reference"])
    )
    raw = build_raw_candidates(frame, parent_signal_config(), str(contract["entry_family"]))
    accepted = accept_with_policy(raw, frame, params["dedupe_policy"])
    events = resolve_events(
        accepted, frame, config, params["runner_policy"], start, end
    )
    return frame, events


def initial_params(config: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(row["factor"]): str(row["initial"])
        for row in config["ordered_factors"]
    }


def choose(
    rows: list[dict[str, Any]],
    incumbent: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    gate = config["selection_gate"]
    candidates = [row for row in rows if bool(row["eligible"])]
    if not candidates:
        return None, "retain_no_sample_eligible_arm"
    score = float(incumbent["robust_score_bp"])
    worst = float(incumbent["worst_fold_net_bp"])
    passing = [
        row
        for row in candidates
        if float(row["robust_score_bp"])
        >= score + float(gate["minimum_robust_improvement_bp"])
        and float(row["worst_fold_net_bp"])
        >= worst - float(gate["maximum_worst_fold_degradation_bp"])
    ]
    if not passing:
        return None, "retain_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            int(row["grid_index"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def selection_phase(config: dict[str, Any]) -> None:
    base, quality = load_base(config)
    start = utc(config["window"]["selection_start_inclusive"])
    end = utc(config["window"]["selection_end_exclusive"])
    folds = list(config["window"]["selection_folds"])
    gate = config["selection_gate"]
    params = initial_params(config)
    cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}

    def run(current: Mapping[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        key = json.dumps(dict(current), sort_keys=True)
        if key not in cache:
            frame, events = evaluate(base, config, current, start, end)
            result = robust_metrics(
                events,
                folds,
                minimum_total=int(gate["minimum_events_total"]),
                minimum_per_fold=int(gate["minimum_events_per_halfyear"]),
            )
            cache[key] = frame, events, result
        return cache[key]

    _, baseline_events, baseline_metrics = run(params)
    trace: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for step, factor_row in enumerate(config["ordered_factors"], 1):
        factor = str(factor_row["factor"])
        _, _, incumbent = run(params)
        current_rows: list[dict[str, Any]] = []
        for grid_index, value in enumerate(factor_row["values"]):
            arm = deepcopy(params)
            arm[factor] = str(value)
            _, _, result = run(arm)
            row = {
                "step": step,
                "factor": factor,
                "value": str(value),
                "grid_index": grid_index,
                **result,
            }
            trace.append(row)
            current_rows.append(row)
        selected, reason = choose(current_rows, incumbent, config)
        before = deepcopy(params)
        if selected is not None:
            params[factor] = str(selected["value"])
        _, _, after_metrics = run(params)
        steps.append(
            {
                "step": step,
                "factor": factor,
                "before": before,
                "after": deepcopy(params),
                "reason": reason,
                "incumbent_metrics": incumbent,
                "selected_metrics": after_metrics,
            }
        )
        print(
            f"{factor}: {before[factor]} -> {params[factor]} ({reason}); "
            f"robust={after_metrics['robust_score_bp']:.2f}bp",
            flush=True,
        )
    _, selected_events, selected_metrics = run(params)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pd.DataFrame(trace), RESULTS / "selection_trace.csv")
    write_csv(baseline_events, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected_events, RESULTS / "selection_selected_trades.csv.gz")
    write_json(
        SELECTION_PATH,
        {
            "phase": "selection_complete_confirmation_unopened",
            "config_sha256": sha256_file(CONFIG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "parent_config_sha256": sha256_file(PARENT_CONFIG_PATH),
            "parent_selection_sha256": sha256_file(PARENT_SELECTION_PATH),
            "source": quality,
            "holdout_rows_read": 0,
            "initial_params": initial_params(config),
            "baseline_metrics": baseline_metrics,
            "selected_params": params,
            "selected_metrics": selected_metrics,
            "steps": steps,
        },
    )


def assert_selection_committed(selection: Mapping[str, Any]) -> None:
    paths = [
        str(SELECTION_PATH.relative_to(ROOT)),
        str(SCRIPT_PATH.relative_to(ROOT)),
        str(CONFIG_PATH.relative_to(ROOT)),
    ]
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *paths],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"selection inputs must be committed: {dirty}")
    checks = {
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "parent_config_sha256": sha256_file(PARENT_CONFIG_PATH),
        "parent_selection_sha256": sha256_file(PARENT_SELECTION_PATH),
    }
    for key, expected in checks.items():
        if selection.get(key) != expected:
            raise RuntimeError(f"selection {key} drift")


def _monthly_comparison(
    candidate: pd.DataFrame, baseline: pd.DataFrame
) -> tuple[pd.DataFrame, float]:
    rows: list[dict[str, Any]] = []
    for month in sorted(
        set(candidate["entry_time"].dt.strftime("%Y-%m"))
        | set(baseline["entry_time"].dt.strftime("%Y-%m"))
    ):
        cand = candidate[candidate["entry_time"].dt.strftime("%Y-%m").eq(month)]
        base = baseline[baseline["entry_time"].dt.strftime("%Y-%m").eq(month)]
        if not len(cand) or not len(base):
            continue
        rows.append(
            {
                "month": month,
                "candidate_events": len(cand),
                "baseline_events": len(base),
                "candidate_mean_net_bp": float(cand["net_return"].mean() * 1e4),
                "baseline_mean_net_bp": float(base["net_return"].mean() * 1e4),
                "difference_bp": float(
                    (cand["net_return"].mean() - base["net_return"].mean()) * 1e4
                ),
            }
        )
    table = pd.DataFrame(rows)
    p = (
        float(
            signflip_p(
                table["difference_bp"].astype(float),
                resamples=100_000,
                seed=20260904,
            )
        )
        if len(table)
        else np.nan
    )
    return table, p


def _atr_buckets(frame: pd.DataFrame, eligible: np.ndarray) -> np.ndarray:
    buckets = np.full(len(frame), -1, dtype=int)
    helper = pd.DataFrame(
        {
            "i": np.arange(len(frame)),
            "month": frame["open_time"].dt.strftime("%Y-%m"),
            "atr": frame["atr"],
            "eligible": eligible,
        }
    )
    for _, group in helper[helper["eligible"] & helper["atr"].notna()].groupby(
        "month", sort=True
    ):
        labels = pd.qcut(
            group["atr"].rank(method="first"), q=min(5, len(group)), labels=False
        ).fillna(0)
        buckets[group["i"].to_numpy(dtype=int)] = labels.to_numpy(dtype=int)
    return buckets


def matched_controls(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    policy: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match random entries by month, UTC block, ATR quintile, and direction."""

    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    contract = config["signal_contract"]
    match = config["matched_control"]
    horizon = int(contract["horizon_bars"])
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= frame.loc[entry_i, "open_time"] < end
            and frame.loc[last_i, "open_time"] + BAR_DELTA <= end
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
            and np.isfinite(float(frame.loc[signal_i, "trend_ma"]))
        )
    excluded = np.zeros(len(frame), dtype=bool)
    radius = int(match["exclude_radius_bars"])
    for signal_i in events["signal_i"].astype(int):
        excluded[max(0, signal_i - radius) : min(len(frame), signal_i + radius + 1)] = True
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    for i in np.flatnonzero(eligible & ~excluded & (buckets >= 0)):
        pool.setdefault((str(months[i]), int(blocks[i]), int(buckets[i])), []).append(int(i))

    required = int(match["controls_per_event"])
    seed = str(match["seed"])
    cost = float(contract["round_trip_cost_fraction"])
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            pool.get(key, []),
            key=lambda i: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{i}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched",
                    "matched_control_count": len(choices),
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": np.nan,
                    "paired_excess_return": np.nan,
                }
            )
            continue
        current: list[float] = []
        for assignment, control_i in enumerate(choices[:required]):
            control_event = {
                "entry_i": control_i + 1,
                "entry_price": float(frame.loc[control_i + 1, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[control_i, "atr"]),
            }
            result = resolve_runner(
                frame,
                control_event,
                policy,
                horizon,
                float(contract["initial_disaster_stop_atr"]),
                float(contract["fixed_target_atr"]),
            )
            if not result.get("resolved"):
                continue
            result = dict(result)
            gross = float(result["gross_return"])
            risk_fraction = (
                float(contract["initial_disaster_stop_atr"])
                * float(control_event["signal_atr"])
                / float(control_event["entry_price"])
            )
            result["net_return"] = gross - cost
            result["risk_fraction"] = risk_fraction
            result["return_r"] = gross / risk_fraction
            result["net_return_r"] = (gross - cost) / risk_fraction
            current.append(float(result["net_return"]))
            controls.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "control_i": control_i,
                    "control_time": frame.loc[control_i, "open_time"],
                    "direction": int(event["direction"]),
                    "calendar_month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    **result,
                }
            )
        if len(current) != required:
            continue
        mean = float(np.mean(current))
        pairs.append(
            {
                "setup_id": event["setup_id"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_net_return": event["net_return"],
                "control_mean_net_return": mean,
                "paired_excess_return": float(event["net_return"]) - mean,
            }
        )
    return pd.DataFrame(controls), pd.DataFrame(pairs)


def _assignment_metrics(controls: pd.DataFrame) -> list[dict[str, Any]]:
    if controls.empty:
        return []
    return [
        {"assignment": int(assignment), **metrics(group)}
        for assignment, group in controls.groupby("assignment", sort=True)
    ]


def failure_mechanics(events: pd.DataFrame) -> pd.DataFrame:
    """Classify losses using only recorded path mechanics, not hindsight labels."""

    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        net = float(event["net_return"])
        gross = float(event["gross_return"])
        horizon_mfe = float(event["horizon_mfe_atr"])
        gave_back = float(event["gave_back_atr"])
        outcome = str(event["outcome"])
        if net > 0.0:
            category = "winner_large_giveback" if gave_back >= 2.0 else "winner_retained"
        elif gross > 0.0:
            category = "gross_win_erased_by_cost"
        elif horizon_mfe >= 2.0 and gave_back >= 2.0:
            category = "profitable_excursion_given_back"
        elif "hard_stop" in outcome and horizon_mfe < 0.5:
            category = "false_launch_early_hard_stop"
        elif "hard_stop" in outcome and horizon_mfe >= 2.0:
            category = "hard_stop_then_direction_recovered"
        elif "trend_ma" in outcome:
            category = "trend_ma_whipsaw_or_reversal"
        elif outcome == "timeout" or outcome.startswith("split:"):
            category = "timeout_or_split_negative"
        else:
            category = "other_loss"
        rows.append({**event, "failure_category": category})
    return pd.DataFrame(rows)


def confirmation_phase(config: dict[str, Any]) -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_selection_committed(selection)
    base, quality = load_base(config)
    start = utc(config["window"]["confirmation_start_inclusive"])
    end = utc(config["window"]["confirmation_end_exclusive"])
    candidate_params = selection["selected_params"]
    baseline_params = selection["initial_params"]
    candidate_frame, candidate = evaluate(base, config, candidate_params, start, end)
    _, baseline = evaluate(base, config, baseline_params, start, end)
    candidate_metrics = metrics(candidate)
    baseline_metrics = metrics(baseline)
    controls, pairs = matched_controls(
        candidate,
        candidate_frame,
        config,
        policy=str(candidate_params["runner_policy"]),
        start=start,
        end=end,
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    control_excess_bp = float(excess.mean() * 1e4) if len(excess) else np.nan
    control_p = (
        float(signflip_p(excess, resamples=100_000, seed=20260904))
        if len(excess)
        else np.nan
    )
    assignments = _assignment_metrics(controls)
    months, monthly_p = _monthly_comparison(candidate, baseline)
    folds = list(config["window"]["confirmation_folds"])
    candidate_fold = pd.DataFrame(
        [
            {
                "fold": fold,
                **metrics(candidate[candidate["entry_time"].map(fold_label).eq(fold)]),
            }
            for fold in folds
        ]
    )
    gate = config["confirmation_gate"]
    difference = float(candidate_metrics["mean_net_bp"] - baseline_metrics["mean_net_bp"])
    gates = {
        "candidate_net_positive": bool(
            float(candidate_metrics["mean_net_bp"])
            > float(gate["candidate_mean_net_bp_gt"])
        ),
        "candidate_improves_baseline": bool(
            difference
            > float(gate["candidate_minus_baseline_mean_net_bp_gt"])
        ),
        "monthly_p_pass": bool(
            np.isfinite(monthly_p)
            and monthly_p < float(gate["candidate_minus_baseline_p_lt"])
        ),
        "both_halfyears_positive": bool(
            candidate_fold["mean_net_bp"].gt(0.0).all()
        ),
        "minimum_events": bool(
            len(candidate) >= int(gate["minimum_events_total"])
        ),
        "paired_control_excess_positive": bool(
            np.isfinite(control_excess_bp) and control_excess_bp > 0.0
        ),
        "paired_control_p_lt_0_01": bool(
            np.isfinite(control_p) and control_p < 0.01
        ),
        "all_control_assignments_beaten": bool(
            len(assignments) == int(config["matched_control"]["controls_per_event"])
            and all(
                float(candidate_metrics["mean_net_bp"]) > float(row["mean_net_bp"])
                for row in assignments
            )
        ),
    }
    gates["all_pass"] = all(gates.values())
    mechanics = failure_mechanics(candidate)
    write_csv(candidate, RESULTS / "confirmation_candidate_trades.csv.gz")
    write_csv(baseline, RESULTS / "confirmation_baseline_trades.csv.gz")
    write_csv(controls, RESULTS / "confirmation_controls.csv.gz")
    write_csv(pairs, RESULTS / "confirmation_control_pairs.csv")
    write_csv(mechanics, RESULTS / "confirmation_failure_mechanics.csv.gz")
    write_csv(candidate_fold, RESULTS / "confirmation_candidate_folds.csv")
    write_csv(months, RESULTS / "confirmation_monthly_comparison.csv")
    write_json(
        RESULTS / "confirmation_summary.json",
        {
            "phase": "exact_2024_confirmation_complete",
            "selected_params": candidate_params,
            "baseline_params": baseline_params,
            "candidate_metrics": candidate_metrics,
            "baseline_metrics": baseline_metrics,
            "candidate_minus_baseline_mean_net_bp": difference,
            "monthly_signflip_p_one_sided": monthly_p,
            "matched_events": len(matched),
            "matched_control_excess_bp": control_excess_bp,
            "paired_control_signflip_p_one_sided": control_p,
            "control_assignments": assignments,
            "gates": gates,
            "source": quality,
            "holdout_rows_read": 0,
            "audit_opened": False,
        },
    )
    print(
        json.dumps(
            json_value(
                {
                    "selected_params": candidate_params,
                    "candidate": candidate_metrics,
                    "baseline": baseline_metrics,
                    "difference_bp": difference,
                    "monthly_p": monthly_p,
                    "gates": gates,
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


def _owner_frame(example: Mapping[str, Any]) -> pd.DataFrame:
    anchor = utc(example["anchor_time_utc"])
    sources = {
        ROOT / str(example["source"]),
        ROOT / "data/kline_fetched/okx_BTC_USDT_SWAP_15m_42007.csv",
        ROOT
        / "analysis/output/owner_short_gold_center_recent15d_top10_20260821"
        / "kline_snapshot/BTC_USDT_SWAP.csv",
    }
    pieces = [
        _read_owner_window(path, anchor - pd.Timedelta(hours=48), anchor + pd.Timedelta(hours=12))
        for path in sorted(sources)
        if path.exists()
    ]
    raw = (
        pd.concat([part for part in pieces if len(part)], ignore_index=True)
        .sort_values("open_time", kind="mergesort")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )
    raw["segment_id"] = raw["open_time"].diff().ne(BAR_DELTA).cumsum().astype(int)
    prior = raw["close"].shift(1)
    true_range = np.maximum(
        raw["high"] - raw["low"],
        np.maximum((raw["high"] - prior).abs(), (raw["low"] - prior).abs()),
    )
    raw["atr"] = pine_rma(true_range.to_numpy(dtype=float), 14)
    return raw


def owner_diagnostic_phase(config: dict[str, Any]) -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    parent = json.loads(PARENT_CONFIG_PATH.read_text(encoding="utf-8"))
    signal = parent["causal_signal"]
    params = selection["selected_params"]
    baseline_params = selection["initial_params"]
    output: list[dict[str, Any]] = []
    for example in parent["owner_examples"]["rows"]:
        anchor = utc(example["anchor_time_utc"])
        direction = 1 if example["direction"] == "LONG" else -1
        raw = _owner_frame(example)
        for label, current in (("baseline", baseline_params), ("selected", params)):
            frame = add_dual_references(
                raw,
                current["trigger_reference"],
                config["signal_contract"]["trend_reference"],
            )
            candidates = build_raw_candidates(frame, signal, "all")
            accepted = accept_with_policy(candidates, frame, current["dedupe_policy"])
            anchor_i = int(frame.index[frame["open_time"].eq(anchor)][0])
            raw_side = candidates[candidates["direction"].eq(direction)].copy()
            accepted_side = (
                accepted[accepted["direction"].eq(direction)].copy()
                if "direction" in accepted.columns
                else pd.DataFrame()
            )
            for kind, rows in (("raw", raw_side), ("accepted", accepted_side)):
                if len(rows):
                    rows["offset"] = rows["signal_i"].astype(int) - anchor_i
                    visible = rows[rows["offset"].between(-8, 24)].copy()
                else:
                    visible = pd.DataFrame()
                nearest = (
                    visible.iloc[visible["offset"].abs().argsort(kind="mergesort")].iloc[0]
                    if len(visible)
                    else None
                )
                output.append(
                    {
                        "example_id": example["id"],
                        "direction": example["direction"],
                        "arm": label,
                        "surface": kind,
                        "trigger_reference": current["trigger_reference"],
                        "dedupe_policy": current["dedupe_policy"],
                        "nearest_signal_time_utc": utc(nearest["signal_time"])
                        if nearest is not None
                        else None,
                        "nearest_offset_bars": int(nearest["offset"])
                        if nearest is not None
                        else None,
                        "nearest_family": str(nearest["signal_family"])
                        if nearest is not None
                        else None,
                        "descriptive_match": nearest is not None,
                        "holdout_use_for_configuration": 1,
                    }
                )
    table = pd.DataFrame(output)
    write_csv(table, RESULTS / "owner_examples_diagnostic.csv")
    write_json(
        RESULTS / "owner_examples_receipt.json",
        {
            "role": config["owner_examples"]["role"],
            "holdout_use_for_this_configuration": 1,
            "rows": len(table),
            "economic_selection_use": False,
            "generalization_claim": False,
            "selected_raw_matches": int(
                table[
                    table["arm"].eq("selected") & table["surface"].eq("raw")
                ]["descriptive_match"].sum()
            ),
            "selected_accepted_matches": int(
                table[
                    table["arm"].eq("selected") & table["surface"].eq("accepted")
                ]["descriptive_match"].sum()
            ),
        },
    )
    print(table.to_string(index=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", required=True, choices=("selection", "confirmation", "owner-diagnostic")
    )
    args = parser.parse_args()
    config = load_config()
    if args.phase == "selection":
        selection_phase(config)
    elif args.phase == "confirmation":
        confirmation_phase(config)
    else:
        owner_diagnostic_phase(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
