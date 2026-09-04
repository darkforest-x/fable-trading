#!/usr/bin/env python3
"""Repair daily altcoin K1->K2 exits without changing the signal.

The parent v1 universe, complete-UTC-day aggregation, EMA13/SMA34 signal,
neutral episode, K1/K2 morphology, transition votes and next-day entry are
hash-pinned.  This script changes only four preregistered execution factors,
one at a time: structure/runner stop confirmation, gradual bank schedule,
trail reference and ATR buffer.

Every stop update uses a completed daily close and becomes executable no earlier
than the next daily open. Intraday hard-stop and bank checks use only that day's
OHLC. Development ends before the one-shot confirmation begins, and the parent
bounded loader cannot materialize repository holdout rows.
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
from scripts.research_btcusdtp_15m_dual_ma_runner import _stop_fill
from scripts.research_btcusdtp_15m_ma_state_trend import json_value, write_csv, write_json
from scripts.research_two_key_candle_ma_retest_1h import sha256_file
from scripts import research_altcoin_1d_k1k2_episode_runner as parent

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
DAY = pd.Timedelta(days=1)


def utc(value: object) -> pd.Timestamp:
    return parent.utc(value)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _assert_parent(config: Mapping[str, Any]) -> dict[str, Any]:
    contract = config["parent"]
    paths = {
        "config": ROOT / str(contract["config_path"]),
        "selection_receipt": ROOT / str(contract["selection_receipt_path"]),
        "audit_receipt": ROOT / str(contract["audit_receipt_path"]),
    }
    for name, path in paths.items():
        _assert_head_frozen(path)
        expected = str(contract[f"{name}_sha256"])
        if sha256_file(path) != expected:
            raise RuntimeError(f"parent {name} hash drifted")
    parent_config = json.loads(paths["config"].read_text(encoding="utf-8"))
    selection = json.loads(paths["selection_receipt"].read_text(encoding="utf-8"))
    if dict(selection["selected_params"]) != {
        **dict(contract["fixed_signal_params"]),
        "trail_reference": "slow",
        "runner_buffer_atr": 1.25,
        "bank_total_fraction": 0.2,
    }:
        raise RuntimeError("parent selected parameters do not match fixed v2 lineage")
    return parent_config


def _assert_development_receipt(path: Path) -> dict[str, Any]:
    _assert_head_frozen(path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("phase") != "development" or not receipt.get("frozen", False):
        raise RuntimeError("development receipt is not frozen")
    return receipt


def signal_params(config: Mapping[str, Any]) -> dict[str, Any]:
    return dict(config["parent"]["fixed_signal_params"])


def build_phase_setups(
    universe: Mapping[str, pd.DataFrame],
    parent_config: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    phase: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """Build hash-pinned parent signals; no v2 execution parameter is consulted."""

    params = signal_params(config)
    spec = config["splits"][phase]
    start = utc(spec["start_inclusive"])
    end = utc(spec["end_exclusive"])
    frames: dict[str, pd.DataFrame] = {}
    setups_by_symbol: dict[str, pd.DataFrame] = {}
    attempts: list[pd.DataFrame] = []
    pairs: list[pd.DataFrame] = []
    for symbol, daily in sorted(universe.items()):
        frame = parent.build_profile(daily, parent_config, str(params["ma_profile"]))
        current_attempts, current_pairs = parent.build_episode_signals(
            frame, symbol, parent_config, params
        )
        setups = parent._setup_rows(current_pairs, frame, start, end, parent_config)
        frames[symbol] = frame
        setups_by_symbol[symbol] = setups
        if len(current_attempts):
            attempts.append(current_attempts)
        if len(current_pairs):
            pairs.append(current_pairs)
    return (
        frames,
        setups_by_symbol,
        pd.concat(attempts, ignore_index=True) if attempts else pd.DataFrame(),
        pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(),
    )


def resolve_trade(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    parent_config: Mapping[str, Any],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase_end: pd.Timestamp,
) -> dict[str, Any]:
    """Resolve one v2 trade using hard risk, close-confirmed exits and banks.

    The fixed hard stop is checked first on each daily bar. A close-confirmed
    structure or MA violation schedules an exit at the following contiguous
    day's open. MA trail levels calculated from close ``t`` never affect the
    intraday range of ``t``.
    """

    execution = config["execution"]
    policy = config["stop_policies"][str(params["stop_policy"])]
    bank = config["bank_schedules"][str(params["bank_schedule"])]
    entry_i = int(event["entry_i"])
    entry = float(event["entry_price"])
    direction = int(event["direction"])
    signal_atr = float(event["signal_atr"])
    if entry <= 0 or signal_atr <= 0 or not np.isfinite([entry, signal_atr]).all():
        return {"resolved": False, "reason": "invalid_entry_or_atr"}
    segment_id = int(frame.loc[entry_i, "segment_id"])
    same_segment = frame.index[frame["segment_id"].eq(segment_id)]
    phase_rows = frame.index[frame["open_time"].lt(phase_end)]
    horizon_end = min(
        entry_i + int(execution["maximum_horizon_bars"]) - 1,
        int(same_segment.max()),
        int(phase_rows.max()),
    )
    if horizon_end - entry_i + 1 < int(execution["minimum_phase_remaining_bars"]):
        return {"resolved": False, "reason": "insufficient_phase_horizon"}

    structure_level = (
        float(event["k2_low"]) - float(execution["stop_buffer_atr"]) * signal_atr
        if direction > 0
        else float(event["k2_high"]) + float(execution["stop_buffer_atr"]) * signal_atr
    )
    structure_risk = direction * (entry - structure_level)
    floor_risk = float(policy["hard_stop_floor_atr"]) * signal_atr
    hard_risk = max(structure_risk, floor_risk)
    risk_atr = hard_risk / signal_atr
    if (
        hard_risk <= 0
        or risk_atr < float(execution["minimum_initial_risk_atr"])
        or risk_atr > float(execution["maximum_initial_risk_atr"])
    ):
        return {"resolved": False, "reason": "hard_risk_out_of_bounds"}
    hard_stop = entry - direction * hard_risk
    active_intraday_stop = hard_stop
    active_runner_level: float | None = None
    structure_is_intraday = policy["structure_trigger"] == "intraday_wick"
    runner_is_intraday = policy["runner_trigger"] == "intraday_wick"
    wrong_required = int(policy["wrong_side_closes_required"])

    levels = np.asarray(bank["levels_r"], dtype=float)
    fractions = np.asarray(bank["fractions"], dtype=float)
    if len(levels) != len(fractions) or fractions.sum() >= 1.0:
        raise RuntimeError("invalid preregistered bank schedule")
    targets = entry + direction * levels * hard_risk
    remaining = 1.0
    realized_gross = 0.0
    bank_hits = 0
    runner_armed = False
    runner_arm_i: int | None = None
    structure_wrong_closes = 0
    runner_wrong_closes = 0
    pending_open_exit: str | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    exit_at_open = False
    outcome = ""
    mfe_until_exit = 0.0
    mae_until_exit = 0.0
    horizon_mfe = 0.0
    horizon_mae = 0.0

    for index in range(entry_i, horizon_end + 1):
        open_price = float(frame.loc[index, "open"])
        high = float(frame.loc[index, "high"])
        low = float(frame.loc[index, "low"])
        close = float(frame.loc[index, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        horizon_mfe = max(horizon_mfe, favourable)
        horizon_mae = max(horizon_mae, adverse)
        if exit_i is not None:
            continue
        if pending_open_exit is not None:
            exit_i = index
            exit_price = open_price
            exit_at_open = True
            outcome = pending_open_exit
            continue
        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)

        hit_hard = low <= active_intraday_stop if direction > 0 else high >= active_intraday_stop
        if hit_hard:
            exit_i = index
            exit_price = _stop_fill(open_price, active_intraday_stop, direction)
            outcome = "runner_intraday_stop" if active_intraday_stop != hard_stop else "hard_disaster_stop"
            continue
        while bank_hits < len(targets):
            target = float(targets[bank_hits])
            hit = high >= target if direction > 0 else low <= target
            if not hit:
                break
            fraction = float(fractions[bank_hits])
            realized_gross += fraction * direction * (target / entry - 1.0)
            remaining -= fraction
            bank_hits += 1

        signed_close_r = direction * (close - entry) / hard_risk
        if (
            not runner_armed
            and signed_close_r >= float(execution["runner_arm_on_completed_close_r"])
        ):
            runner_armed = True
            runner_arm_i = index

        if not runner_armed and not structure_is_intraday:
            wrong = direction * (close - structure_level) <= 0
            structure_wrong_closes = structure_wrong_closes + 1 if wrong else 0
            if structure_wrong_closes >= wrong_required:
                pending_open_exit = f"structure_{wrong_required}close_next_open"

        if runner_armed:
            reference_column = "slow_ma" if params["trail_reference"] == "slow" else "fast_ma"
            candidate = float(frame.loc[index, reference_column]) - (
                direction * float(params["runner_buffer_atr"]) * float(frame.loc[index, "atr"])
            )
            improves = bool(
                active_runner_level is None
                or (direction > 0 and candidate > active_runner_level)
                or (direction < 0 and candidate < active_runner_level)
            )
            sane = bool(
                np.isfinite(candidate)
                and ((direction > 0 and candidate < close) or (direction < 0 and candidate > close))
            )
            if improves and sane:
                active_runner_level = candidate
                if runner_is_intraday:
                    active_intraday_stop = (
                        max(active_intraday_stop, candidate)
                        if direction > 0
                        else min(active_intraday_stop, candidate)
                    )
            if not runner_is_intraday and active_runner_level is not None:
                wrong = direction * (close - active_runner_level) <= 0
                runner_wrong_closes = runner_wrong_closes + 1 if wrong else 0
                if runner_wrong_closes >= wrong_required:
                    pending_open_exit = f"runner_{wrong_required}close_next_open"

    if exit_i is None:
        exit_i = horizon_end
        exit_price = float(frame.loc[exit_i, "close"])
        limited = horizon_end < entry_i + int(execution["maximum_horizon_bars"]) - 1
        outcome = "phase_end_timeout" if limited else "horizon_timeout"
    gross = realized_gross + remaining * direction * (float(exit_price) / entry - 1.0)
    net = gross - float(execution["round_trip_cost_fraction"])
    risk_fraction = hard_risk / entry
    gross_r = gross / risk_fraction
    return {
        **dict(event),
        "resolved": True,
        "policy": str(params["stop_policy"]),
        "bank_schedule": str(params["bank_schedule"]),
        "outcome": outcome,
        "exit_i": int(exit_i),
        "exit_time": utc(frame.loc[exit_i, "open_time"])
        if exit_at_open
        else utc(frame.loc[exit_i, "open_time"]) + DAY,
        "exit_price": float(exit_price),
        "hold_bars": int(exit_i - entry_i if exit_at_open else exit_i - entry_i + 1),
        "structure_level": structure_level,
        "hard_stop": hard_stop,
        "risk_distance": hard_risk,
        "risk_atr": risk_atr,
        "risk_fraction": risk_fraction,
        "gross_return": gross,
        "net_return": net,
        "gross_return_r": gross_r,
        "net_return_r": net / risk_fraction,
        "capped_net_return_r": float(np.clip(net / risk_fraction, -1.5, 10.0)),
        "bank_hits": bank_hits,
        "banked_fraction": float(fractions[:bank_hits].sum()),
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "runner_armed": runner_armed,
        "runner_arm_i": runner_arm_i,
        "trail_reference": str(params["trail_reference"]),
        "runner_buffer_atr": float(params["runner_buffer_atr"]),
        "final_runner_level": active_runner_level,
        "final_intraday_stop": active_intraday_stop,
        "mfe_at_exit_r": mfe_until_exit / hard_risk,
        "mae_at_exit_r": mae_until_exit / hard_risk,
        "horizon_mfe_r": horizon_mfe / hard_risk,
        "horizon_mae_r": horizon_mae / hard_risk,
        "captured_gross_r": gross_r,
        "gave_back_r": mfe_until_exit / hard_risk - gross_r,
    }


def _resolve_symbol(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    parent_config: Mapping[str, Any],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if setups.empty:
        return pd.DataFrame(), pd.DataFrame()
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    occupied_through = -1
    for event in setups.sort_values("entry_i", kind="mergesort").to_dict("records"):
        if int(event["entry_i"]) <= occupied_through:
            rejected.append({**event, "rejection_reason": "symbol_position_lock"})
            continue
        result = resolve_trade(
            frame, event, parent_config, config, params, phase_end=phase_end
        )
        if not result.get("resolved"):
            rejected.append({**event, "rejection_reason": result.get("reason")})
            continue
        trades.append(result)
        occupied_through = int(result["exit_i"])
    return pd.DataFrame(trades), pd.DataFrame(rejected)


def _base_metrics(trades: pd.DataFrame, p_seed: int) -> dict[str, Any]:
    metrics = parent.trade_metrics(trades, p_seed=p_seed)
    if trades.empty:
        return {
            **metrics,
            "mean_capped_net_r": np.nan,
            "median_net_r": np.nan,
            "p95_raw_net_r": np.nan,
        }
    return {
        **metrics,
        "mean_capped_net_r": float(trades["capped_net_return_r"].mean()),
        "median_net_r": float(trades["net_return_r"].median()),
        "p95_raw_net_r": float(trades["net_return_r"].quantile(0.95)),
    }


def _fold_table(
    trades: pd.DataFrame,
    folds: list[Mapping[str, Any]],
    *,
    p_seed: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    times = pd.to_datetime(trades["entry_time"], utc=True) if len(trades) else pd.Series(dtype="datetime64[ns, UTC]")
    for offset, fold in enumerate(folds):
        mask = times.ge(utc(fold["start_inclusive"])) & times.lt(utc(fold["end_exclusive"])) if len(trades) else np.zeros(0, dtype=bool)
        rows.append(
            {
                "fold": str(fold["id"]),
                **_base_metrics(trades.loc[mask].copy(), p_seed + offset),
            }
        )
    return pd.DataFrame(rows)


def evaluate(
    frames: Mapping[str, pd.DataFrame],
    setups_by_symbol: Mapping[str, pd.DataFrame],
    parent_config: Mapping[str, Any],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    end = utc(config["splits"][phase]["end_exclusive"])
    trades: list[pd.DataFrame] = []
    rejections: list[pd.DataFrame] = []
    for symbol, frame in sorted(frames.items()):
        current, rejected = _resolve_symbol(
            setups_by_symbol[symbol],
            frame,
            parent_config,
            config,
            params,
            phase_end=end,
        )
        if len(current):
            trades.append(current)
        if len(rejected):
            rejections.append(rejected)
    trade_frame = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    rejected_frame = pd.concat(rejections, ignore_index=True) if rejections else pd.DataFrame()
    folds = _fold_table(
        trade_frame,
        list(config["splits"][phase]["folds"]),
        p_seed=int(config["matched_control"]["p_seed"]),
    )
    metrics = _base_metrics(trade_frame, int(config["matched_control"]["p_seed"]))
    fold_scores = folds["mean_capped_net_r"].to_numpy(dtype=float)
    fold_counts = folds["events"].to_numpy(dtype=int)
    finite = bool(len(fold_scores) and np.isfinite(fold_scores).all())
    if phase == "development":
        minimums = config["selection"]["minimums"]
    else:
        minimums = config["confirmation_minimums"]
    summary = {
        **metrics,
        "positive_folds": int(np.sum(folds["mean_net_bp"].to_numpy(dtype=float) > 0)) if finite else 0,
        "total_folds": int(len(folds)),
        "minimum_fold_events": int(fold_counts.min()) if len(fold_counts) else 0,
        "robust_score_r": float(np.median(fold_scores) - 0.5 * np.std(fold_scores, ddof=0)) if finite else np.nan,
        "eligible": bool(
            len(trade_frame) >= int(minimums["events_total"])
            and int(metrics["symbols"]) >= int(minimums["symbols_total"])
            and len(fold_counts)
            and np.all(fold_counts >= int(minimums["events_per_fold"]))
            and finite
        ),
    }
    portfolio_trades, portfolio_curve, portfolio_summary = parent._portfolio(
        trade_frame, parent_config
    )
    return {
        "trades": trade_frame,
        "rejections": rejected_frame,
        "folds": folds,
        "summary": summary,
        "portfolio_trades": portfolio_trades,
        "portfolio_curve": portfolio_curve,
        "portfolio_summary": portfolio_summary,
    }


def _rank_candidate(
    summary: Mapping[str, Any], incumbent: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[int, float, float, float]:
    incumbent_tail = float(incumbent["p95_raw_net_r"])
    candidate_tail = float(summary["p95_raw_net_r"])
    tail_ok = bool(
        not np.isfinite(incumbent_tail)
        or incumbent_tail <= 0
        or candidate_tail >= float(config["selection"]["p95_raw_net_r_retention_min"]) * incumbent_tail
    )
    eligible = bool(summary["eligible"] and tail_ok)
    return (
        1 if eligible else 0,
        float(summary["robust_score_r"]) if eligible else -np.inf,
        float(summary["mean_capped_net_r"]) if eligible else -np.inf,
        float(summary["mean_net_bp"]) if eligible else -np.inf,
    )


def _params_key(params: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def matched_random(
    trades: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
    parent_config: Mapping[str, Any],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    empty = {
        "matched_events": 0,
        "candidate_mean_net_bp": np.nan,
        "control_mean_net_bp": np.nan,
        "excess_bp": np.nan,
        "week_clusters": 0,
        "week_cluster_signflip_p": np.nan,
        "control_reuse_count": 0,
    }
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), empty
    spec = config["splits"][phase]
    start = utc(spec["start_inclusive"])
    end = utc(spec["end_exclusive"])
    required = int(config["matched_control"]["controls_per_event"])
    radius = int(config["matched_control"]["exclude_radius_bars"])
    seed = str(config["matched_control"]["seed"])
    p_seed = int(config["matched_control"]["p_seed"])
    pools: dict[str, dict[tuple[str, int], list[int]]] = {}
    buckets: dict[str, np.ndarray] = {}
    protected = {
        symbol: group["signal_i"].astype(int).tolist()
        for symbol, group in trades.groupby("symbol", sort=True)
    }
    for symbol, frame in frames.items():
        eligible = parent._eligible_control_indices(frame, start, end, parent_config)
        current_buckets = parent._atr_quintiles(frame, eligible)
        buckets[symbol] = current_buckets
        pool: dict[tuple[str, int], list[int]] = {}
        for index in np.flatnonzero(eligible & (current_buckets >= 0)):
            if any(abs(int(index) - signal_i) <= radius for signal_i in protected.get(symbol, [])):
                continue
            key = (parent._halfyear(frame.loc[index, "open_time"]), int(current_buckets[index]))
            pool.setdefault(key, []).append(int(index))
        pools[symbol] = pool
    used: set[tuple[str, int]] = set()
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in trades.sort_values(["entry_time", "setup_id"], kind="mergesort").to_dict("records"):
        symbol = str(event["symbol"])
        frame = frames[symbol]
        bucket = int(buckets[symbol][int(event["signal_i"])])
        key = (parent._halfyear(utc(event["signal_time"])), bucket)
        choices = sorted(
            [index for index in pools[symbol].get(key, []) if (symbol, index) not in used],
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{symbol}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "symbol": symbol,
                    "entry_time": event["entry_time"],
                    "match_status": "unmatched",
                    "available_controls": len(choices),
                }
            )
            continue
        resolved: list[dict[str, Any]] = []
        for assignment, signal_i in enumerate(choices[:required]):
            used.add((symbol, signal_i))
            entry_i = signal_i + 1
            control_event = {
                "setup_id": f"control-{symbol}-{signal_i}-{int(event['direction'])}",
                "symbol": symbol,
                "direction": int(event["direction"]),
                "signal_i": signal_i,
                "signal_time": frame.loc[signal_i, "open_time"],
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": float(frame.loc[entry_i, "open"]),
                "signal_atr": float(frame.loc[signal_i, "atr"]),
                "k2_low": float(frame.loc[signal_i, "open"] - int(event["direction"]) * float(event["risk_atr"]) * float(frame.loc[signal_i, "atr"])),
                "k2_high": float(frame.loc[signal_i, "open"] - int(event["direction"]) * float(event["risk_atr"]) * float(frame.loc[signal_i, "atr"])),
                "transition_votes": np.nan,
                "signal_score": np.nan,
            }
            # Preserve the candidate's hard risk in ATR units.  The artificial
            # K2 extreme is chosen so the same resolver reconstructs that risk.
            target_structure = float(frame.loc[entry_i, "open"]) - (
                int(event["direction"]) * float(event["risk_atr"]) * float(frame.loc[signal_i, "atr"])
            )
            if int(event["direction"]) > 0:
                control_event["k2_low"] = target_structure + float(config["execution"]["stop_buffer_atr"]) * float(frame.loc[signal_i, "atr"])
            else:
                control_event["k2_high"] = target_structure - float(config["execution"]["stop_buffer_atr"]) * float(frame.loc[signal_i, "atr"])
            result = resolve_trade(
                frame, control_event, parent_config, config, params, phase_end=end
            )
            if not result.get("resolved"):
                continue
            resolved.append(result)
            controls.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "symbol": symbol,
                    "control_signal_i": signal_i,
                    "control_entry_time": result["entry_time"],
                    "direction": int(event["direction"]),
                    "calendar_halfyear": key[0],
                    "atr_quintile": key[1],
                    "copied_risk_atr": float(event["risk_atr"]),
                    "control_net_return": float(result["net_return"]),
                }
            )
        if len(resolved) != required:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "symbol": symbol,
                    "entry_time": event["entry_time"],
                    "match_status": "resolution_failed",
                    "available_controls": len(resolved),
                }
            )
            continue
        control_mean = float(np.mean([row["net_return"] for row in resolved]))
        pairs.append(
            {
                "setup_id": event["setup_id"],
                "symbol": symbol,
                "entry_time": event["entry_time"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_net_return": float(event["net_return"]),
                "control_mean_net_return": control_mean,
                "paired_excess_return": float(event["net_return"]) - control_mean,
            }
        )
    controls_frame = pd.DataFrame(controls)
    pairs_frame = pd.DataFrame(pairs)
    matched = pairs_frame[pairs_frame.get("match_status", pd.Series(dtype=str)).eq("matched_exact")].copy()
    if not len(matched):
        return controls_frame, pairs_frame, empty
    week = pd.to_datetime(matched["entry_time"], utc=True).dt.strftime("%G-W%V")
    weekly = matched.assign(_week=week).groupby("_week")["paired_excess_return"].mean()
    return controls_frame, pairs_frame, {
        "matched_events": int(len(matched)),
        "candidate_mean_net_bp": float(matched["candidate_net_return"].mean() * 1e4),
        "control_mean_net_bp": float(matched["control_mean_net_return"].mean() * 1e4),
        "excess_bp": float(matched["paired_excess_return"].mean() * 1e4),
        "week_clusters": int(len(weekly)),
        "week_cluster_signflip_p": float(signflip_p(weekly, resamples=100_000, seed=p_seed)),
        "control_reuse_count": 0,
    }


def failure_diagnostics(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame()
    detail = trades.copy()

    def classify(row: pd.Series) -> str:
        if row["net_return"] > 0 and row["net_return_r"] >= 5:
            return "large_trend_winner"
        if row["net_return"] > 0 and row["gave_back_r"] >= 3:
            return "winner_large_giveback"
        if row["bank_hits"] > 0 and row["net_return"] <= 0:
            return "banked_then_net_loss"
        if row["runner_armed"] and row["net_return"] <= 0:
            return "runner_armed_then_net_loss"
        if row["net_return"] <= 0 and row["horizon_mfe_r"] >= 2:
            return "early_exit_then_two_r_recovery"
        if row["net_return"] <= 0:
            return "false_launch_or_no_followthrough"
        return "ordinary_winner"

    detail["failure_mode"] = detail.apply(classify, axis=1)
    summary = (
        detail.groupby("failure_mode", as_index=False)
        .agg(
            events=("setup_id", "size"),
            symbols=("symbol", "nunique"),
            mean_net_bp=("net_return", lambda values: float(values.mean() * 1e4)),
            total_net_bp=("net_return", lambda values: float(values.sum() * 1e4)),
            mean_net_r=("net_return_r", "mean"),
            mean_mfe_r=("horizon_mfe_r", "mean"),
            mean_giveback_r=("gave_back_r", "mean"),
        )
        .sort_values("total_net_bp", kind="mergesort")
    )
    return detail, summary


def development_phase(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    prereg = config_path.with_name("preregistration.json")
    for path in (config_path, prereg, SCRIPT_PATH):
        _assert_head_frozen(path)
    parent_config = _assert_parent(config)
    end = utc(config["splits"]["development"]["end_exclusive"])
    universe, source_quality, source_summary = parent.load_universe(
        parent_config, end_exclusive=end
    )
    frames, setups, attempts, pairs = build_phase_setups(
        universe, parent_config, config, phase="development"
    )
    params = deepcopy(config["selection"]["initial"])
    rows: list[dict[str, Any]] = []
    minimum_gain = float(config["selection"]["minimum_robust_gain_r"])
    for stage, factor in enumerate(config["selection"]["ordered_factors"], start=1):
        incumbent = evaluate(
            frames, setups, parent_config, config, params, phase="development"
        )
        candidates: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
        for value in config["selection"]["candidates"][factor]:
            trial = deepcopy(params)
            trial[str(factor)] = value
            result = evaluate(
                frames, setups, parent_config, config, trial, phase="development"
            )
            candidates.append((value, trial, result))
            rows.append(
                {
                    "stage": stage,
                    "factor": factor,
                    "value": value,
                    "params_key": _params_key(trial),
                    **trial,
                    **result["summary"],
                    **{f"portfolio_{key}": val for key, val in result["portfolio_summary"].items()},
                }
            )
        best_value, best_params, best = max(
            candidates,
            key=lambda item: _rank_candidate(item[2]["summary"], incumbent["summary"], config),
        )
        incumbent_rank = _rank_candidate(incumbent["summary"], incumbent["summary"], config)
        best_rank = _rank_candidate(best["summary"], incumbent["summary"], config)
        improvement = best_rank[1] - incumbent_rank[1]
        selected_value = params[factor]
        if best_rank[0] == 1 and (
            incumbent_rank[0] == 0
            or best_value == params[factor]
            or improvement >= minimum_gain
        ):
            params = best_params
            selected_value = best_value
        for row in rows:
            if row["stage"] == stage:
                row["stage_selected"] = bool(row["value"] == selected_value)
    final = evaluate(frames, setups, parent_config, config, params, phase="development")
    baseline_params = deepcopy(config["selection"]["initial"])
    baseline = evaluate(
        frames, setups, parent_config, config, baseline_params, phase="development"
    )
    experiment = ROOT / "experiments" / "active" / str(config["experiment_id"])
    results = experiment / "results"
    results.mkdir(parents=True, exist_ok=True)
    write_csv(source_quality, results / "development_source_quality.csv")
    write_csv(attempts, results / "development_signal_attempts.csv.gz")
    write_csv(pairs, results / "development_signal_pairs.csv.gz")
    write_csv(pd.concat([value for value in setups.values() if len(value)], ignore_index=True), results / "development_signal_setups.csv.gz")
    write_csv(pd.DataFrame(rows), results / "development_coordinate_grid.csv")
    write_csv(final["trades"], results / "development_candidate_trades.csv.gz")
    write_csv(final["rejections"], results / "development_candidate_rejections.csv.gz")
    write_csv(final["folds"], results / "development_candidate_folds.csv")
    write_csv(final["portfolio_trades"], results / "development_portfolio_trades.csv.gz")
    write_csv(final["portfolio_curve"], results / "development_portfolio_equity.csv")
    write_csv(baseline["trades"], results / "development_baseline_trades.csv.gz")
    write_csv(baseline["folds"], results / "development_baseline_folds.csv")
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "development",
        "frozen": True,
        "status": "frozen_for_confirmation" if final["summary"]["eligible"] else "research_only_sample_gate_failed",
        "selected_params": params,
        "baseline_params": baseline_params,
        "source": source_summary,
        "baseline": baseline["summary"],
        "candidate": final["summary"],
        "portfolio": final["portfolio_summary"],
        "parent_confirmation_rows_read": 0,
        "repository_holdout_rows_read": int(source_summary["repository_holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(config_path),
            "preregistration_sha256": sha256_file(prereg),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(results / "development_coordinate_grid.csv"),
            "source_quality_sha256": sha256_file(results / "development_source_quality.csv"),
        },
    }
    write_json(results / "development_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def confirmation_phase(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    experiment = ROOT / "experiments" / "active" / str(config["experiment_id"])
    results = experiment / "results"
    development = _assert_development_receipt(results / "development_receipt.json")
    for path in (config_path, config_path.with_name("preregistration.json"), SCRIPT_PATH):
        _assert_head_frozen(path)
    parent_config = _assert_parent(config)
    end = utc(config["splits"]["confirmation"]["end_exclusive"])
    universe, source_quality, source_summary = parent.load_universe(
        parent_config, end_exclusive=end
    )
    frames, setups, attempts, pairs = build_phase_setups(
        universe, parent_config, config, phase="confirmation"
    )
    params = dict(development["selected_params"])
    candidate = evaluate(
        frames, setups, parent_config, config, params, phase="confirmation"
    )
    baseline_params = dict(development["baseline_params"])
    baseline = evaluate(
        frames, setups, parent_config, config, baseline_params, phase="confirmation"
    )
    controls, matched_pairs, matched = matched_random(
        candidate["trades"], frames, parent_config, config, params, phase="confirmation"
    )
    failure_detail, failure_summary = failure_diagnostics(candidate["trades"])
    symbol_metrics = pd.DataFrame(
        [
            {"symbol": symbol, **_base_metrics(group, int(config["matched_control"]["p_seed"]))}
            for symbol, group in candidate["trades"].groupby("symbol", sort=True)
        ]
    )
    write_csv(source_quality, results / "confirmation_source_quality.csv")
    write_csv(attempts, results / "confirmation_signal_attempts.csv.gz")
    write_csv(pairs, results / "confirmation_signal_pairs.csv.gz")
    write_csv(pd.concat([value for value in setups.values() if len(value)], ignore_index=True), results / "confirmation_signal_setups.csv.gz")
    write_csv(candidate["trades"], results / "confirmation_candidate_trades.csv.gz")
    write_csv(candidate["rejections"], results / "confirmation_candidate_rejections.csv.gz")
    write_csv(candidate["folds"], results / "confirmation_candidate_folds.csv")
    write_csv(candidate["portfolio_trades"], results / "confirmation_portfolio_trades.csv.gz")
    write_csv(candidate["portfolio_curve"], results / "confirmation_portfolio_equity.csv")
    write_csv(baseline["trades"], results / "confirmation_baseline_trades.csv.gz")
    write_csv(baseline["folds"], results / "confirmation_baseline_folds.csv")
    write_csv(controls, results / "confirmation_matched_controls.csv.gz")
    write_csv(matched_pairs, results / "confirmation_matched_pairs.csv")
    write_csv(failure_detail, results / "confirmation_failure_detail.csv.gz")
    write_csv(failure_summary, results / "confirmation_failure_modes.csv")
    write_csv(symbol_metrics, results / "confirmation_symbol_metrics.csv")

    summary = candidate["summary"]
    gates = config["acceptance_gates"]
    required_positive = int(
        np.ceil(float(gates["positive_fold_share_min"]) * int(summary["total_folds"]))
    )
    checks = {
        "sample_eligible": bool(summary["eligible"]),
        "mean_net_positive": bool(float(summary["mean_net_bp"]) > 0),
        "capped_mean_net_r_positive": bool(float(summary["mean_capped_net_r"]) > 0),
        "profit_factor_above_one": bool(float(summary["profit_factor"]) > 1),
        "positive_fold_share": bool(int(summary["positive_folds"]) >= required_positive),
        "positive_symbol_share": bool(
            float(summary["positive_symbol_share"]) >= float(gates["positive_symbol_share_min"])
        ),
        "week_cluster_signflip_p": bool(
            float(summary["week_cluster_signflip_p"]) < float(gates["week_cluster_signflip_p_max"])
        ),
        "matched_excess_positive": bool(float(matched["excess_bp"]) > 0),
        "matched_random_p": bool(
            float(matched["week_cluster_signflip_p"]) < float(gates["matched_random_p_max"])
        ),
        "portfolio_total_return_positive": bool(
            float(candidate["portfolio_summary"]["total_return"]) > 0
        ),
        "portfolio_drawdown": bool(
            abs(float(candidate["portfolio_summary"]["closed_equity_max_drawdown"]))
            <= float(gates["portfolio_closed_equity_max_drawdown_max"])
        ),
    }
    all_gates = bool(all(checks.values()))
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "confirmation",
        "frozen": True,
        "status": "passed_preholdout_research_gates" if all_gates else "research_only_failed_gates",
        "selected_params": params,
        "source": source_summary,
        "baseline": baseline["summary"],
        "candidate": summary,
        "portfolio": candidate["portfolio_summary"],
        "matched_random": matched,
        "gate_checks": checks,
        "all_registered_gates_pass": all_gates,
        "development_receipt_sha256": sha256_file(results / "development_receipt.json"),
        "repository_holdout_rows_read": int(source_summary["repository_holdout_rows_read"]),
        "production_or_live_changed": False,
    }
    write_json(results / "confirmation_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("development", "confirmation"))
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    if args.phase == "development":
        development_phase(config_path, config)
    else:
        confirmation_phase(config_path, config)


if __name__ == "__main__":
    main()
