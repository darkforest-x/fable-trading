#!/usr/bin/env python3
"""Select and validate a causal moving-average trend exit for BTCUSDT.P 15m.

The entry population is the broad EMA30(HL2) direct/rejection/coil surface with
the three-false-bar rearm rule.  Every entry feature uses the completed signal
bar or earlier and fills at the next open.  Exit stops derived from a completed
bar can first act on the following bar.  Only outcome resolution reads future
bars.

Development ends at the physical February source.  The March--May 3 source is
opened only by ``--phase validation`` after the selection receipt, config, and
this script are committed.  The direct 15m source is physically cut before the
repository holdout boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    BAR_DELTA,
    _assignment_metrics,
    accept_with_policy,
    add_dual_references,
    build_raw_candidates,
    parent_signal_config,
)
from scripts.research_btcusdtp_15m_high_recall_l2_runner import (
    add_context_features,
    load_base_until,
    load_config as load_parent_config,
    matrix,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    fold_label,
    fold_metrics,
    json_value,
    metrics,
    robust_metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import add_features, sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-ma-runner-grid-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PARENT = ROOT / "experiments/active/exp-btcusdtp-15m-high-recall-l2-trend-runner-preholdout-20260904-v1"
PARENT_RESULTS = PARENT / "results"
PARENT_MODEL = PARENT_RESULTS / "l2_huber_model.txt"
PARENT_CONTRACT = PARENT_RESULTS / "model_contract.json"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _safe_source(config: Mapping[str, Any]) -> Path:
    path = ROOT / str(config["source"]["fresh_validation"])
    if sha256_file(path) != str(config["source"]["fresh_validation_sha256"]):
        raise RuntimeError("fresh validation source SHA drift")
    return path


def add_exit_references(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal HL2 EMA20/30/40 and SMA40/60/90 within each segment."""

    parts: list[pd.DataFrame] = []
    for segment_id, part in frame.groupby("segment_id", sort=True):
        out = part.copy().reset_index(drop=True)
        hl2 = (out["high"] + out["low"]) / 2.0
        for length in (20, 30, 40):
            out[f"exit_EMA{length}"] = hl2.ewm(
                span=length, adjust=False, min_periods=length
            ).mean()
        for length in (40, 60, 90):
            out[f"exit_SMA{length}"] = hl2.rolling(
                length, min_periods=length
            ).mean()
        out["segment_id"] = int(segment_id)
        parts.append(out)
    return pd.concat(parts, ignore_index=True) if parts else frame.copy()


def load_fresh_frame(config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read only the physically pre-holdout direct-15m source and add features."""

    path = _safe_source(config)
    raw = pd.read_csv(
        path,
        usecols=["open", "high", "low", "close", "volume", "open_time"],
        parse_dates=["open_time"],
    )
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
    raw = raw.sort_values("open_time", kind="mergesort").drop_duplicates(
        "open_time", keep="last"
    )
    holdout = utc(config["source"]["holdout_start"])
    if raw.empty or utc(raw["open_time"].max()) >= holdout:
        raise RuntimeError("fresh source reaches repository holdout")
    raw["segment_id"] = raw["open_time"].diff().ne(BAR_DELTA).cumsum().astype(int)
    parts: list[pd.DataFrame] = []
    for segment_id, part in raw.groupby("segment_id", sort=True):
        featured = add_features(
            part.drop(columns="segment_id").reset_index(drop=True)
        )
        featured["segment_id"] = int(segment_id)
        parts.append(featured)
    frame = pd.concat(parts, ignore_index=True)
    return add_exit_references(frame), {
        "source_path": str(path.relative_to(ROOT)),
        "source_sha256": sha256_file(path),
        "rows_read": len(raw),
        "first_time": raw["open_time"].iloc[0],
        "last_time": raw["open_time"].iloc[-1],
        "holdout_rows_read": 0,
        "segments": int(raw["segment_id"].nunique()),
    }


def assert_direct_source_parity(
    fresh: pd.DataFrame, development: pd.DataFrame
) -> dict[str, Any]:
    start = utc("2026-01-01T00:00:00Z")
    end = utc("2026-02-28T16:00:00Z")
    columns = ["open", "high", "low", "close", "volume"]
    left = development.loc[
        development["open_time"].between(start, end, inclusive="left"),
        ["open_time", *columns],
    ]
    right = fresh.loc[
        fresh["open_time"].between(start, end, inclusive="left"),
        ["open_time", *columns],
    ]
    joined = left.merge(right, on="open_time", suffixes=("_5m", "_15m"))
    if len(joined) != len(left) or len(joined) != len(right):
        raise RuntimeError("direct/aggregated overlap timestamp mismatch")
    result: dict[str, Any] = {"overlap_rows": len(joined)}
    for column in columns:
        delta = (joined[f"{column}_5m"] - joined[f"{column}_15m"]).abs()
        tolerance = 1e-8 if column == "volume" else 0.0
        if bool((delta > tolerance).any()):
            raise RuntimeError(f"direct 15m parity failed for {column}")
        result[f"{column}_max_abs_delta"] = float(delta.max())
    return result


def development_events(config: Mapping[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for name in ("selection_scored_events.csv.gz", "validation_scored_pool.csv.gz"):
        rows.append(pd.read_csv(PARENT_RESULTS / name, parse_dates=["entry_time", "signal_time"]))
    events = pd.concat(rows, ignore_index=True)
    start = utc(config["splits"]["development_start_inclusive"])
    end = utc(config["splits"]["development_end_exclusive"])
    events = events[events["entry_time"].between(start, end, inclusive="left")].copy()
    contract = json.loads(PARENT_CONTRACT.read_text(encoding="utf-8"))
    events["parent_l2_selected"] = events["l2_score"].ge(
        float(contract["score_threshold"])
    )
    signal_threshold = float(
        events.loc[events["entry_time"].dt.year.eq(2024), "signal_score"].quantile(0.80)
    )
    events["native_top20"] = events["signal_score"].ge(signal_threshold)
    horizon = int(config["splits"]["analysis_horizon_bars"])
    keep: list[bool] = []
    for row in events.itertuples(index=False):
        entry_i = int(row.entry_i)
        last_i = entry_i + horizon - 1
        keep.append(
            last_i < len(frame)
            and utc(frame.loc[entry_i, "open_time"]) == utc(row.entry_time)
            and int(frame.loc[entry_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and utc(frame.loc[last_i, "open_time"] + BAR_DELTA) <= end
        )
    events = events.loc[keep].reset_index(drop=True)
    events.attrs["native_top20_threshold"] = signal_threshold
    return events


def _market_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    fields = ["open", "high", "low", "close", "atr", "segment_id"]
    arrays = {name: frame[name].to_numpy() for name in fields}
    for name in ("EMA20", "EMA30", "EMA40", "SMA40", "SMA60", "SMA90"):
        arrays[name] = frame[f"exit_{name}"].to_numpy(dtype=float)
    return arrays


def _stop_fill(open_price: float, stop: float, direction: int) -> float:
    if direction > 0 and open_price < stop:
        return open_price
    if direction < 0 and open_price > stop:
        return open_price
    return stop


def _path_extremes(
    arrays: Mapping[str, np.ndarray], event: Mapping[str, Any], horizon: int
) -> tuple[float, float]:
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    last = entry_i + horizon
    if direction > 0:
        favourable = float(np.max(arrays["high"][entry_i:last]) - entry)
        adverse = float(entry - np.min(arrays["low"][entry_i:last]))
    else:
        favourable = float(entry - np.min(arrays["low"][entry_i:last]))
        adverse = float(np.max(arrays["high"][entry_i:last]) - entry)
    return favourable / signal_atr, adverse / signal_atr


def _run_fixed_leg(
    arrays: Mapping[str, np.ndarray],
    event: Mapping[str, Any],
    *,
    target_atr: float,
    max_hold_bars: int,
    stop_atr: float,
) -> dict[str, Any]:
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    stop = entry - direction * stop_atr * signal_atr
    target = entry + direction * target_atr * signal_atr
    end_i = entry_i + max_hold_bars - 1
    mfe = 0.0
    for i in range(entry_i, end_i + 1):
        favourable = (
            float(arrays["high"][i]) - entry
            if direction > 0
            else entry - float(arrays["low"][i])
        )
        mfe = max(mfe, favourable)
        hit_stop = (
            float(arrays["low"][i]) <= stop
            if direction > 0
            else float(arrays["high"][i]) >= stop
        )
        if hit_stop:
            price = _stop_fill(float(arrays["open"][i]), stop, direction)
            return {"gross": direction * (price / entry - 1.0), "exit_i": i, "outcome": "hard_stop", "mfe_exit_atr": mfe / signal_atr, "armed": False}
        hit_target = (
            float(arrays["high"][i]) >= target
            if direction > 0
            else float(arrays["low"][i]) <= target
        )
        if hit_target:
            return {"gross": direction * (target / entry - 1.0), "exit_i": i, "outcome": f"fixed_{target_atr:g}atr", "mfe_exit_atr": mfe / signal_atr, "armed": True}
    price = float(arrays["close"][end_i])
    return {"gross": direction * (price / entry - 1.0), "exit_i": end_i, "outcome": "timeout", "mfe_exit_atr": mfe / signal_atr, "armed": False}


def _run_runner_leg(
    arrays: Mapping[str, np.ndarray],
    event: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    stop_atr: float,
) -> dict[str, Any]:
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    active_stop = entry - direction * stop_atr * signal_atr
    stop_source = "hard"
    armed = False
    arm_i: int | None = None
    wrong_closes = 0
    mfe = 0.0
    end_i = entry_i + int(params["max_hold_bars"]) - 1
    ma_values = arrays[str(params["trend_ma"])]
    for i in range(entry_i, end_i + 1):
        favourable = (
            float(arrays["high"][i]) - entry
            if direction > 0
            else entry - float(arrays["low"][i])
        )
        mfe = max(mfe, favourable)
        hit_stop = (
            float(arrays["low"][i]) <= active_stop
            if direction > 0
            else float(arrays["high"][i]) >= active_stop
        )
        if hit_stop:
            price = _stop_fill(float(arrays["open"][i]), active_stop, direction)
            return {"gross": direction * (price / entry - 1.0), "exit_i": i, "outcome": f"{stop_source}_stop", "mfe_exit_atr": mfe / signal_atr, "armed": armed, "arm_i": arm_i}

        close_profit = direction * (float(arrays["close"][i]) - entry) / signal_atr
        if not armed and close_profit >= float(params["arm_atr"]):
            armed = True
            arm_i = i
            wrong_closes = 0
        if not armed or not np.isfinite(float(ma_values[i])):
            continue
        buffer_price = float(params["buffer_atr"]) * float(arrays["atr"][i])
        if str(params["exit_style"]) == "trail":
            candidate = float(ma_values[i]) - direction * buffer_price
            if direction > 0 and candidate > active_stop:
                active_stop = candidate
                stop_source = "ma_trail"
            elif direction < 0 and candidate < active_stop:
                active_stop = candidate
                stop_source = "ma_trail"
        else:
            wrong = direction * (float(arrays["close"][i]) - float(ma_values[i])) <= -buffer_price
            wrong_closes = wrong_closes + 1 if wrong else 0
            if wrong_closes >= 2 and i < end_i:
                price = float(arrays["open"][i + 1])
                return {"gross": direction * (price / entry - 1.0), "exit_i": i + 1, "outcome": "ma_close2", "mfe_exit_atr": mfe / signal_atr, "armed": armed, "arm_i": arm_i}
    price = float(arrays["close"][end_i])
    return {"gross": direction * (price / entry - 1.0), "exit_i": end_i, "outcome": "timeout", "mfe_exit_atr": mfe / signal_atr, "armed": armed, "arm_i": arm_i}


def simulate_policy(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    params: Mapping[str, Any],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    arrays = _market_arrays(frame)
    cost = float(config["entry"]["round_trip_cost_fraction"])
    stop_atr = float(config["entry"]["initial_stop_atr"])
    analysis_horizon = int(config["splits"]["analysis_horizon_bars"])
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        runner = _run_runner_leg(arrays, event, params, stop_atr=stop_atr)
        if str(params["leg_mix"]) == "half_fixed3":
            fixed = _run_fixed_leg(
                arrays,
                event,
                target_atr=3.0,
                max_hold_bars=int(params["max_hold_bars"]),
                stop_atr=stop_atr,
            )
            gross = 0.5 * (float(runner["gross"]) + float(fixed["gross"]))
            exit_i = max(int(runner["exit_i"]), int(fixed["exit_i"]))
            outcome = f"half:{fixed['outcome']}|{runner['outcome']}"
        else:
            gross = float(runner["gross"])
            exit_i = int(runner["exit_i"])
            outcome = str(runner["outcome"])
        horizon_mfe, horizon_mae = _path_extremes(arrays, event, analysis_horizon)
        entry = float(event["entry_price"])
        signal_atr = float(event["signal_atr"])
        direction = int(event["direction"])
        realized_atr = gross * entry / signal_atr
        risk_fraction = stop_atr * signal_atr / entry
        rows.append(
            {
                **event,
                "exit_i": exit_i,
                "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
                "outcome": outcome,
                "hold_bars": exit_i - int(event["entry_i"]) + 1,
                "gross_return": gross,
                "net_return": gross - cost,
                "risk_fraction": risk_fraction,
                "return_r": gross / risk_fraction,
                "net_return_r": (gross - cost) / risk_fraction,
                "runner_armed": bool(runner["armed"]),
                "runner_arm_i": runner.get("arm_i"),
                "mfe_at_exit_atr": float(runner["mfe_exit_atr"]),
                "horizon_mfe_atr": horizon_mfe,
                "horizon_mae_atr": horizon_mae,
                "realized_atr": realized_atr,
                "gave_back_atr": horizon_mfe - realized_atr,
                "capture_of_horizon_mfe": realized_atr / horizon_mfe if horizon_mfe > 0 else np.nan,
                **{f"param_{key}": value for key, value in params.items()},
            }
        )
    return pd.DataFrame(rows)


def fixed_reference(
    frame: pd.DataFrame, events: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    arrays = _market_arrays(frame)
    cost = float(config["entry"]["round_trip_cost_fraction"])
    stop_atr = float(config["entry"]["initial_stop_atr"])
    horizon = int(config["splits"]["analysis_horizon_bars"])
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        result = _run_fixed_leg(
            arrays, event, target_atr=5.0, max_hold_bars=96, stop_atr=stop_atr
        )
        gross = float(result["gross"])
        entry = float(event["entry_price"])
        signal_atr = float(event["signal_atr"])
        horizon_mfe, horizon_mae = _path_extremes(arrays, event, horizon)
        realized_atr = gross * entry / signal_atr
        risk_fraction = stop_atr * signal_atr / entry
        rows.append(
            {
                **event,
                "exit_i": int(result["exit_i"]),
                "exit_time": frame.loc[int(result["exit_i"]), "open_time"] + BAR_DELTA,
                "outcome": result["outcome"],
                "hold_bars": int(result["exit_i"]) - int(event["entry_i"]) + 1,
                "gross_return": gross,
                "net_return": gross - cost,
                "risk_fraction": risk_fraction,
                "return_r": gross / risk_fraction,
                "net_return_r": (gross - cost) / risk_fraction,
                "runner_armed": bool(result["armed"]),
                "mfe_at_exit_atr": float(result["mfe_exit_atr"]),
                "horizon_mfe_atr": horizon_mfe,
                "horizon_mae_atr": horizon_mae,
                "realized_atr": realized_atr,
                "gave_back_atr": horizon_mfe - realized_atr,
                "capture_of_horizon_mfe": realized_atr / horizon_mfe if horizon_mfe > 0 else np.nan,
            }
        )
    return pd.DataFrame(rows)


def tail_metrics(events: pd.DataFrame) -> dict[str, Any]:
    big = events[events["horizon_mfe_atr"].ge(5.0)]
    armed = events[events["runner_armed"]]
    return {
        "p95_net_bp": float(events["net_return"].quantile(0.95) * 1e4),
        "p99_net_bp": float(events["net_return"].quantile(0.99) * 1e4),
        "big_trend_events": len(big),
        "big_trend_mean_realized_atr": float(big["realized_atr"].mean()) if len(big) else np.nan,
        "big_trend_median_capture": float(big["capture_of_horizon_mfe"].median()) if len(big) else np.nan,
        "big_trend_mean_giveback_atr": float(big["gave_back_atr"].mean()) if len(big) else np.nan,
        "armed_events": len(armed),
        "armed_finish_net_negative": int(armed["net_return"].le(0.0).sum()),
        "armed_finish_net_negative_rate": float(armed["net_return"].le(0.0).mean()) if len(armed) else np.nan,
    }


def policy_metrics(
    events: pd.DataFrame, config: Mapping[str, Any]
) -> dict[str, Any]:
    folds = list(map(str, config["splits"]["development_folds"]))
    result = robust_metrics(
        events,
        folds,
        minimum_total=int(config["selection"]["minimum_events_total"]),
        minimum_per_fold=int(config["selection"]["minimum_events_per_fold"]),
    )
    result.update(tail_metrics(events))
    return result


def choose_factor(
    candidates: list[dict[str, Any]],
    incumbent: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    gate = config["selection"]
    passing = [
        row
        for row in candidates
        if bool(row["eligible"])
        and float(row["robust_score_bp"])
        >= float(incumbent["robust_score_bp"])
        + float(gate["minimum_robust_improvement_bp"])
        and float(row["worst_fold_net_bp"])
        >= float(incumbent["worst_fold_net_bp"])
        - float(gate["maximum_worst_fold_degradation_bp"])
    ]
    if not passing:
        return None, "retain_no_robust_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -float(row["big_trend_mean_realized_atr"]),
            int(row["grid_index"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def selection_phase(config: dict[str, Any]) -> None:
    end = utc(config["splits"]["development_end_exclusive"])
    base, quality = load_base_until(load_parent_config(), end)
    frame = add_exit_references(base)
    events = development_events(config, frame)
    native_threshold = float(events.attrs["native_top20_threshold"])
    initial = deepcopy(config["selection"]["initial"])
    params = deepcopy(initial)
    cache: dict[str, tuple[pd.DataFrame, dict[str, Any]]] = {}

    def run(current: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
        key = json.dumps(dict(current), sort_keys=True)
        if key not in cache:
            trades = simulate_policy(frame, events, current, config)
            cache[key] = trades, policy_metrics(trades, config)
        return cache[key]

    initial_trades, initial_metrics = run(params)
    fixed = fixed_reference(frame, events, config)
    trace: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for step, factor_row in enumerate(config["selection"]["ordered_factors"], 1):
        factor = str(factor_row["factor"])
        _, incumbent = run(params)
        rows: list[dict[str, Any]] = []
        for grid_index, value in enumerate(factor_row["values"]):
            arm = deepcopy(params)
            arm[factor] = value
            _, result = run(arm)
            row = {
                "step": step,
                "factor": factor,
                "value": value,
                "grid_index": grid_index,
                **result,
            }
            trace.append(row)
            rows.append(row)
        selected, reason = choose_factor(rows, incumbent, config)
        before = deepcopy(params)
        if selected is not None:
            params[factor] = selected["value"]
        _, after = run(params)
        steps.append(
            {"step": step, "factor": factor, "before": before, "after": deepcopy(params), "reason": reason, "incumbent_metrics": incumbent, "selected_metrics": after}
        )
        print(f"{factor}: {before[factor]} -> {params[factor]} ({reason}); robust={after['robust_score_bp']:.2f}bp", flush=True)

    selected_trades, selected_metrics = run(params)
    paired = selected_trades[["setup_id", "entry_time", "net_return"]].merge(
        fixed[["setup_id", "net_return"]], on="setup_id", suffixes=("_selected", "_fixed5")
    )
    paired["delta_return"] = paired["net_return_selected"] - paired["net_return_fixed5"]
    monthly = paired.assign(month=paired["entry_time"].dt.strftime("%Y-%m")).groupby("month", as_index=False).agg(events=("setup_id", "size"), mean_delta_bp=("delta_return", lambda x: float(x.mean() * 1e4)))
    monthly_p = float(signflip_p(monthly["mean_delta_bp"], resamples=100_000, seed=20260904))
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pd.DataFrame(trace), RESULTS / "development_selection_trace.csv")
    write_csv(initial_trades, RESULTS / "development_initial_trades.csv.gz")
    write_csv(fixed, RESULTS / "development_fixed5_trades.csv.gz")
    write_csv(selected_trades, RESULTS / "development_selected_trades.csv.gz")
    write_csv(fold_metrics(selected_trades, list(config["splits"]["development_folds"])), RESULTS / "development_selected_folds.csv")
    write_csv(monthly, RESULTS / "development_paired_months.csv")
    receipt = {
        "phase": "development_complete_fresh_validation_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "parent_model_sha256": sha256_file(PARENT_MODEL),
        "parent_contract_sha256": sha256_file(PARENT_CONTRACT),
        "source": quality,
        "holdout_rows_read": 0,
        "events": len(events),
        "native_top20_threshold": native_threshold,
        "initial_params": initial,
        "initial_metrics": initial_metrics,
        "fixed5_metrics": {**metrics(fixed), **tail_metrics(fixed)},
        "selected_params": params,
        "selected_metrics": selected_metrics,
        "paired_vs_fixed5_mean_delta_bp": float(paired["delta_return"].mean() * 1e4),
        "paired_vs_fixed5_month_signflip_p_one_sided": monthly_p,
        "steps": steps,
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_selection_committed(selection: Mapping[str, Any]) -> None:
    paths = [CONFIG_PATH, SCRIPT_PATH, SELECTION_PATH]
    relative = [str(path.relative_to(ROOT)) for path in paths]
    subprocess.run(["git", "ls-files", "--error-unmatch", *relative], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *relative], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    if dirty:
        raise RuntimeError(f"selection artifacts must be committed: {dirty}")
    if selection.get("phase") != "development_complete_fresh_validation_unopened":
        raise RuntimeError("selection phase drift")
    if selection.get("config_sha256") != sha256_file(CONFIG_PATH) or selection.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("selection contract SHA drift")


def fresh_events(
    frame: pd.DataFrame, config: Mapping[str, Any], selection: Mapping[str, Any]
) -> pd.DataFrame:
    parent_config = load_parent_config()
    referenced = add_dual_references(frame, "EMA30", "SMA60")
    raw = build_raw_candidates(referenced, parent_signal_config(), "all")
    accepted = accept_with_policy(raw, referenced, "state_reset3")
    accepted["risk_fraction"] = float(config["entry"]["initial_stop_atr"]) * accepted["signal_atr"] / accepted["entry_price"]
    enriched = add_context_features(
        referenced,
        accepted,
        cost=float(config["entry"]["round_trip_cost_fraction"]),
    )
    contract = json.loads(PARENT_CONTRACT.read_text(encoding="utf-8"))
    features = list(map(str, contract["feature_names"]))
    x, _ = matrix(enriched, features, contract["training_medians"])
    booster = lgb.Booster(model_file=str(PARENT_MODEL))
    enriched["l2_score"] = booster.predict(x)
    enriched["parent_l2_selected"] = enriched["l2_score"].ge(float(contract["score_threshold"]))
    enriched["native_top20"] = enriched["signal_score"].ge(float(selection["native_top20_threshold"]))
    start = utc(config["splits"]["fresh_validation_start_inclusive"])
    end = utc(config["splits"]["fresh_validation_end_exclusive"])
    horizon = int(config["splits"]["analysis_horizon_bars"])
    keep: list[bool] = []
    for row in enriched.itertuples(index=False):
        entry_i = int(row.entry_i)
        last_i = entry_i + horizon - 1
        keep.append(
            start <= utc(row.entry_time) < end
            and last_i < len(referenced)
            and int(referenced.loc[entry_i, "segment_id"]) == int(referenced.loc[last_i, "segment_id"])
            and utc(referenced.loc[last_i, "open_time"] + BAR_DELTA) <= end
        )
    return enriched.loc[keep].reset_index(drop=True)


def _validation_block(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    return "2026-05P" if stamp.month == 5 else stamp.strftime("%Y-%m")


def paired_block_test(selected: pd.DataFrame, fixed: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    paired = selected[["setup_id", "entry_time", "net_return"]].merge(
        fixed[["setup_id", "net_return"]], on="setup_id", suffixes=("_selected", "_fixed5")
    )
    paired["delta_return"] = paired["net_return_selected"] - paired["net_return_fixed5"]
    paired["week"] = paired["entry_time"].dt.to_period("W-SUN").astype(str)
    blocks = paired.groupby("week", as_index=False).agg(events=("setup_id", "size"), mean_delta_bp=("delta_return", lambda x: float(x.mean() * 1e4)))
    p = float(signflip_p(blocks["mean_delta_bp"], resamples=100_000, seed=20260906)) if len(blocks) else np.nan
    return paired, p


def _atr_buckets(frame: pd.DataFrame, eligible: np.ndarray) -> np.ndarray:
    buckets = np.full(len(frame), -1, dtype=int)
    helper = pd.DataFrame({"i": np.arange(len(frame)), "month": frame["open_time"].dt.strftime("%Y-%m"), "atr": frame["atr"], "eligible": eligible})
    for _, group in helper[helper["eligible"] & helper["atr"].notna()].groupby("month", sort=True):
        labels = pd.qcut(group["atr"].rank(method="first"), q=min(5, len(group)), labels=False).fillna(0)
        buckets[group["i"].to_numpy(dtype=int)] = labels.to_numpy(dtype=int)
    return buckets


def matched_controls(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    params: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = utc(config["splits"]["fresh_validation_start_inclusive"])
    end = utc(config["splits"]["fresh_validation_end_exclusive"])
    horizon = int(config["splits"]["analysis_horizon_bars"])
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= utc(frame.loc[entry_i, "open_time"]) < end
            and utc(frame.loc[last_i, "open_time"] + BAR_DELTA) <= end
            and int(frame.loc[entry_i, "segment_id"]) == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
        )
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    event_indices = set(events["signal_i"].astype(int))
    for i in np.flatnonzero(eligible & (buckets >= 0)):
        if int(i) not in event_indices:
            pool.setdefault((str(months[i]), int(blocks[i]), int(buckets[i])), []).append(int(i))
    required = int(config["matched_control"]["controls_per_event"])
    radius = int(config["matched_control"]["exclude_radius_bars"])
    seed = str(config["matched_control"]["seed"])
    control_rows: list[pd.DataFrame] = []
    pairs: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            (i for i in pool.get(key, []) if abs(i - signal_i) > radius),
            key=lambda i: hashlib.sha256(f"{seed}|{event['setup_id']}|{i}".encode()).hexdigest(),
        )
        if len(choices) < required:
            pairs.append({"setup_id": event["setup_id"], "match_status": "unmatched", "matched_control_count": len(choices), "candidate_net_return": event["net_return"], "control_mean_net_return": np.nan, "paired_excess_return": np.nan})
            continue
        current: list[float] = []
        for assignment, signal_control_i in enumerate(choices[:required]):
            control_event = {
                "setup_id": f"{event['setup_id']}:c{assignment}",
                "signal_i": signal_control_i,
                "entry_i": signal_control_i + 1,
                "entry_time": frame.loc[signal_control_i + 1, "open_time"],
                "entry_price": float(frame.loc[signal_control_i + 1, "open"]),
                "signal_atr": float(frame.loc[signal_control_i, "atr"]),
                "direction": int(event["direction"]),
                "parent_l2_selected": False,
                "native_top20": False,
            }
            resolved = simulate_policy(frame, pd.DataFrame([control_event]), params, config)
            resolved["candidate_setup_id"] = event["setup_id"]
            resolved["assignment"] = assignment
            control_rows.append(resolved)
            current.append(float(resolved.iloc[0]["net_return"]))
        mean_control = float(np.mean(current))
        pairs.append({"setup_id": event["setup_id"], "match_status": "matched_exact", "matched_control_count": len(current), "candidate_net_return": event["net_return"], "control_mean_net_return": mean_control, "paired_excess_return": float(event["net_return"]) - mean_control})
    controls = pd.concat(control_rows, ignore_index=True) if control_rows else pd.DataFrame()
    return controls, pd.DataFrame(pairs)


def validation_phase(config: dict[str, Any]) -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_selection_committed(selection)
    development, _ = load_base_until(load_parent_config(), utc(config["splits"]["development_end_exclusive"]))
    fresh, quality = load_fresh_frame(config)
    parity = assert_direct_source_parity(fresh, development)
    frame = add_dual_references(fresh, "EMA30", "SMA60")
    frame = add_exit_references(frame)
    events = fresh_events(fresh, config, selection)
    selected = simulate_policy(frame, events, selection["selected_params"], config)
    fixed = fixed_reference(frame, events, config)
    paired, block_p = paired_block_test(selected, fixed)
    controls, control_pairs = matched_controls(frame, selected, selection["selected_params"], config)
    matched = control_pairs[control_pairs["match_status"].eq("matched_exact")]
    excess = matched["paired_excess_return"].astype(float)
    control_p = float(signflip_p(excess, resamples=100_000, seed=20260907)) if len(excess) else np.nan
    slices = []
    for label in config["splits"]["fresh_validation_blocks"]:
        part = selected[selected["entry_time"].map(_validation_block).eq(label)]
        slices.append({"block": label, **metrics(part), **tail_metrics(part)})
    slices_frame = pd.DataFrame(slices)
    paired_delta_bp = float(paired["delta_return"].mean() * 1e4)
    selected_metrics = {**metrics(selected), **tail_metrics(selected)}
    fixed_metrics = {**metrics(fixed), **tail_metrics(fixed)}
    assignments = _assignment_metrics(controls) if len(controls) else []
    gate = config["validation_gate"]
    complete_blocks_positive = all(
        int(row.events) == 0 or float(row.mean_net_bp) > 0.0
        for row in slices_frame.itertuples(index=False)
    )
    gates = {
        "paired_exit_improvement_positive": paired_delta_bp > float(gate["paired_exit_improvement_bp_gt"]),
        "weekly_block_signflip_p": bool(np.isfinite(block_p) and block_p < float(gate["weekly_block_signflip_p_lt"])),
        "mean_net_positive": float(selected_metrics["mean_net_bp"]) > float(gate["mean_net_bp_gt"]),
        "paired_random_control_p": bool(np.isfinite(control_p) and control_p < float(gate["paired_random_control_p_lt"])),
        "all_complete_month_blocks_positive": complete_blocks_positive,
    }
    gates["all_pass"] = all(gates.values())
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(events, RESULTS / "validation_signal_pool.csv.gz")
    write_csv(selected, RESULTS / "validation_selected_trades.csv.gz")
    write_csv(fixed, RESULTS / "validation_fixed5_trades.csv.gz")
    write_csv(paired, RESULTS / "validation_paired_exit_delta.csv.gz")
    write_csv(slices_frame, RESULTS / "validation_slices.csv")
    write_csv(controls, RESULTS / "validation_controls.csv.gz")
    write_csv(control_pairs, RESULTS / "validation_control_pairs.csv")
    summary = {
        "phase": "fresh_preholdout_validation_complete",
        "source": quality,
        "direct_15m_parity": parity,
        "holdout_rows_read": 0,
        "selected_params": selection["selected_params"],
        "events": len(events),
        "selected_metrics": selected_metrics,
        "fixed5_metrics": fixed_metrics,
        "paired_exit_improvement_bp": paired_delta_bp,
        "weekly_block_signflip_p_one_sided": block_p,
        "matched_events": len(matched),
        "matched_control_excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
        "matched_control_signflip_p_one_sided": control_p,
        "control_assignments": assignments,
        "cohorts": {
            "parent_l2_selected": {
                **metrics(selected[selected["parent_l2_selected"]]),
                **tail_metrics(selected[selected["parent_l2_selected"]]),
            },
            "native_top20": {
                **metrics(selected[selected["native_top20"]]),
                **tail_metrics(selected[selected["native_top20"]]),
            },
        },
        "gates": gates,
        "production_eligible": False,
    }
    write_json(RESULTS / "validation_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("selection", "validation"), required=True)
    args = parser.parse_args()
    config = load_config()
    if args.phase == "selection":
        selection_phase(config)
    else:
        validation_phase(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
