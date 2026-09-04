#!/usr/bin/env python3
"""Audit progressive ETHUSDT.P 15m profit banking on frozen V5 setups.

Signal features use only completed OHLCV bars through K2: Pine/Wilder ATR14,
EMA30(HL2), SMA60(HL2), four-bar EMA30 slope, and the frozen V5 K1/K2 trend
regime state. Entry is the next 15-minute open. The exit resolver alone reads
future bars, up to 96 bars.

Selection reads 2023--2024 only and changes one scalar at a time: total banked
fraction, then ATR spacing. Audit reads 2025 through February 2026 only after
the selection receipt is committed. The chunked loader stops before the
repository holdout boundary and refuses any parsed holdout row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import scripts.research_btcusdtp_15m_trend_regime_episode as parent
from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    BAR_DELTA,
    _atr_buckets,
    _resolve_leg,
    _stop_fill,
    add_dual_references,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    fold_label,
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_pine_eth_15m import (
    load_development_frame,
    sha256_bounded_frame,
)
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-progressive-scaleout-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PARENT_CONFIG_PATH = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-trend-regime-episode-preholdout-20260904-v1/config.json"
)


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
    head_bytes = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(head_bytes).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_selection_committed() -> dict[str, Any]:
    _assert_head_frozen(SELECTION_PATH)
    receipt = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    if receipt.get("phase") != "selection" or receipt.get("status") != "frozen_for_audit":
        raise RuntimeError("selection receipt is not frozen for audit")
    return receipt


def _true_range(segment: pd.DataFrame) -> np.ndarray:
    high = segment["high"].to_numpy(dtype=float)
    low = segment["low"].to_numpy(dtype=float)
    close = segment["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    return np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )


def load_eth_frame(
    config: Mapping[str, Any], *, end_exclusive: pd.Timestamp
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a physically bounded 15m prefix and derive causal V5 references."""

    source = config["source_contract"]
    holdout = utc(source["holdout_start"])
    raw = load_development_frame(
        ROOT / str(config["instrument"]["data_path"]),
        safe_end=end_exclusive,
        holdout_start=holdout,
        chunksize=int(source["parser_chunksize"]),
    )
    raw = raw.copy().reset_index(drop=True)
    raw["segment_id"] = raw["open_time"].diff().ne(BAR_DELTA).cumsum().astype(int)
    atr = np.full(len(raw), np.nan, dtype=float)
    for _, segment in raw.groupby("segment_id", sort=True):
        atr[segment.index.to_numpy(dtype=int)] = pine_rma(_true_range(segment), 14)
    raw["atr"] = atr
    frame = add_dual_references(raw, "EMA30", "SMA60")
    safe_atr = frame["atr"].astype(float).replace(0.0, np.nan)
    frame["fast_slow_spread_atr"] = (
        frame["reference_ma"].astype(float) - frame["trend_ma"].astype(float)
    ) / safe_atr
    frame["fast_slope4_atr_per_bar"] = frame[
        "reference_slope_atr_per_bar"
    ].astype(float)
    side = np.sign(
        ((frame["high"] + frame["low"]) / 2.0) - frame["reference_ma"]
    )
    flips = side.ne(side.groupby(frame["segment_id"], sort=False).shift(1)).astype(int)
    frame["ma_side_flips_24"] = (
        flips.groupby(frame["segment_id"], sort=False)
        .rolling(24, min_periods=24)
        .sum()
        .reset_index(level=0, drop=True)
    )
    changes = frame.groupby("segment_id", sort=False)["close"].diff().abs()
    signed_move = (
        frame["close"]
        - frame.groupby("segment_id", sort=False)["close"].shift(24)
    ).abs()
    path = (
        changes.groupby(frame["segment_id"], sort=False)
        .rolling(24, min_periods=24)
        .sum()
        .reset_index(level=0, drop=True)
    )
    frame["efficiency24"] = signed_move / path.replace(0.0, np.nan)
    quality = {
        "path": str(config["instrument"]["data_path"]),
        "bounded_prefix_sha256": sha256_bounded_frame(raw),
        "rows_read": len(raw),
        "first_bar": raw["open_time"].iloc[0],
        "last_bar": raw["open_time"].iloc[-1],
        "bounded_end_exclusive": end_exclusive,
        "holdout_start": holdout,
        "holdout_rows_read": int(raw["open_time"].ge(holdout).sum()),
        "segments": int(raw["segment_id"].nunique()),
    }
    if quality["holdout_rows_read"] != int(source["repository_holdout_rows_allowed"]):
        raise RuntimeError("ETH loader materialized repository holdout")
    return frame, quality


def build_frozen_setups(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay the exact V5 entry ledger before changing position management."""

    pconfig = json.loads(PARENT_CONFIG_PATH.read_text(encoding="utf-8"))
    pairs = parent.build_v3_pairs(frame, pconfig)
    live = pairs[
        pairs["signed_fast_slow_spread_atr"].gt(
            float(config["frozen_entry"]["current_signed_spread_min_exclusive"])
        )
        & pairs["signed_fast_slope4_atr_per_bar"].ge(
            float(config["frozen_entry"]["current_signed_slope_min_inclusive"])
        )
    ].copy()
    params = {
        key: config["frozen_entry"][key]
        for key in (
            "entry_spread_atr",
            "entry_slope_atr_per_bar",
            "strong_dwell_bars",
            "neutral_dwell_bars",
        )
    }
    setups = parent.simulate_regime(live, frame, pconfig, params).copy()
    if setups.empty:
        return pairs, setups
    horizon = int(config["frozen_execution"]["horizon_bars"])
    setups = setups[setups["entry_i"].astype(int).add(horizon - 1).lt(len(frame))].copy()
    setups["setup_id"] = setups.apply(
        lambda row: hashlib.sha256(
            (
                f"ETH-USDT-SWAP|15m|{int(row.direction)}|"
                f"{utc(row.signal_time).isoformat()}|{int(row.k1_i)}|v5"
            ).encode()
        ).hexdigest()[:16],
        axis=1,
    )
    return pairs, setups.reset_index(drop=True)


def _result_common(
    event: Mapping[str, Any], result: Mapping[str, Any], cost: float
) -> dict[str, Any]:
    gross = float(result["gross_return"])
    risk_fraction = (
        2.0 * float(event["signal_atr"]) / float(event["entry_price"])
    )
    return {
        **dict(event),
        **dict(result),
        "net_return": gross - cost,
        "risk_fraction": risk_fraction,
        "return_r": gross / risk_fraction,
        "net_return_r": (gross - cost) / risk_fraction,
    }


def resolve_baseline(
    frame: pd.DataFrame, event: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    execution = config["frozen_execution"]
    result = _resolve_leg(
        frame,
        event,
        "ma_trail1_after_2atr",
        int(execution["horizon_bars"]),
        float(execution["initial_disaster_stop_atr"]),
        5.0,
    )
    if not result.get("resolved"):
        return dict(result)
    common = _result_common(
        event, result, float(execution["round_trip_cost_fraction"])
    )
    common.update(
        {
            "policy": "full_sma60_runner",
            "bank_total_fraction": 0.0,
            "step_atr": np.nan,
            "partial_hits": 0,
            "banked_gross_return": 0.0,
            "remaining_fraction": 1.0,
            "profit_floor_gap_breach": False,
        }
    )
    return common


def resolve_progressive(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    bank_total_fraction: float,
    step_atr: float,
) -> dict[str, Any]:
    """Resolve three bank tranches plus a residual SMA runner.

    Only columns from bars ``entry_i`` through the bounded horizon are read.
    The active stop is checked before new milestones on each bar. Milestones
    and close-derived stop updates become active only on the following bar.
    """

    execution = config["frozen_execution"]
    stage_count = int(config["progressive_scaleout"]["stage_count"])
    if not 0.0 < bank_total_fraction < 1.0:
        raise ValueError("bank_total_fraction must be strictly between zero and one")
    if step_atr <= 0.0:
        raise ValueError("step_atr must be positive")
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    horizon = int(execution["horizon_bars"])
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    if int(frame.loc[end_i, "segment_id"]) != int(frame.loc[entry_i, "segment_id"]):
        return {"resolved": False, "reason": "horizon_crosses_gap"}

    cost = float(execution["round_trip_cost_fraction"])
    hard_stop = entry - direction * float(
        execution["initial_disaster_stop_atr"]
    ) * signal_atr
    targets = [
        entry + direction * step_atr * stage * signal_atr
        for stage in range(1, stage_count + 1)
    ]
    tranche = bank_total_fraction / stage_count
    remaining = 1.0
    realized_gross = 0.0
    partial_hits = 0
    active_stop = hard_stop
    stop_source = "hard"
    runner_armed = False
    runner_arm_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    mfe_until_exit = 0.0
    mae_until_exit = 0.0
    full_mfe = 0.0
    full_mae = 0.0
    floor_gap_breach = False
    floor_price: float | None = None

    for i in range(entry_i, end_i + 1):
        open_price = float(frame.loc[i, "open"])
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        close = float(frame.loc[i, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        full_mfe = max(full_mfe, favourable)
        full_mae = max(full_mae, adverse)
        if exit_i is not None:
            continue
        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)

        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        if hit_stop:
            exit_i = i
            exit_price = _stop_fill(open_price, active_stop, direction)
            if floor_price is not None:
                floor_gap_breach = bool(
                    (direction > 0 and open_price < active_stop)
                    or (direction < 0 and open_price > active_stop)
                )
            outcome = f"{stop_source}_stop"
            continue

        while partial_hits < stage_count:
            target = targets[partial_hits]
            target_hit = high >= target if direction > 0 else low <= target
            if not target_hit:
                break
            signed_target_return = direction * (target / entry - 1.0)
            realized_gross += tranche * signed_target_return
            remaining -= tranche
            partial_hits += 1

        signed_close_atr = direction * (close - entry) / signal_atr
        if (
            not runner_armed
            and signed_close_atr
            >= float(execution["runner_arm_on_completed_close_atr"])
        ):
            runner_armed = True
            runner_arm_i = i

        candidates: list[tuple[float, str]] = []
        if partial_hits:
            floor_signed_return = cost / remaining
            floor_price = entry * (1.0 + direction * floor_signed_return)
            candidates.append((floor_price, "banked_profit_floor"))
        if runner_armed:
            trail = float(frame.loc[i, "trend_ma"]) - direction * float(
                execution["runner_buffer_atr"]
            ) * float(frame.loc[i, "atr"])
            candidates.append((trail, "sma60_runner"))
        for candidate, source in candidates:
            improves_stop = (direction > 0 and candidate > active_stop) or (
                direction < 0 and candidate < active_stop
            )
            if improves_stop:
                active_stop = candidate
                stop_source = source

    if exit_i is None:
        exit_i = end_i
        exit_price = float(frame.loc[end_i, "close"])
        outcome = "timeout"
    signed_remainder_return = direction * (float(exit_price) / entry - 1.0)
    gross = realized_gross + remaining * signed_remainder_return
    captured_atr = gross * entry / signal_atr
    result = {
        "resolved": True,
        "policy": "progressive_three_stage_sma60_runner",
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "runner_armed": runner_armed,
        "runner_arm_i": runner_arm_i,
        "mfe_at_exit_atr": mfe_until_exit / signal_atr,
        "mae_at_exit_atr": mae_until_exit / signal_atr,
        "horizon_mfe_atr": full_mfe / signal_atr,
        "horizon_mae_atr": full_mae / signal_atr,
        "capture_of_horizon_mfe": (
            captured_atr / (full_mfe / signal_atr) if full_mfe > 0.0 else np.nan
        ),
        "gave_back_atr": full_mfe / signal_atr - captured_atr,
        "bank_total_fraction": bank_total_fraction,
        "step_atr": step_atr,
        "partial_hits": partial_hits,
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "final_active_stop": active_stop,
        "final_profit_floor": floor_price,
        "profit_floor_gap_breach": floor_gap_breach,
    }
    return _result_common(event, result, cost)


def replay(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    bank_total_fraction: float | None,
    step_atr: float | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        if bank_total_fraction is None or step_atr is None:
            result = resolve_baseline(frame, event, config)
        else:
            result = resolve_progressive(
                frame,
                event,
                config,
                bank_total_fraction=bank_total_fraction,
                step_atr=step_atr,
            )
        if result.get("resolved"):
            rows.append(result)
    return pd.DataFrame(rows)


def window(frame: pd.DataFrame, start: object, end: object) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    times = frame["entry_time"].map(utc)
    return frame.loc[times.ge(utc(start)) & times.lt(utc(end))].copy()


def policy_metrics(events: pd.DataFrame, *, first_target_atr: float) -> dict[str, Any]:
    base = metrics(events)
    if events.empty:
        return {
            **base,
            "first_target_excursions": 0,
            "profit_to_nonpositive_events": 0,
            "profit_to_nonpositive_share": np.nan,
            "mean_banked_gross_bp": np.nan,
            "median_giveback_atr": np.nan,
            "p95_net_bp": np.nan,
            "profit_floor_gap_breaches": 0,
        }
    excursions = events["horizon_mfe_atr"].ge(first_target_atr)
    reversed_ = excursions & events["net_return"].le(0.0)
    count = int(excursions.sum())
    return {
        **base,
        "first_target_excursions": count,
        "profit_to_nonpositive_events": int(reversed_.sum()),
        "profit_to_nonpositive_share": float(reversed_.sum() / count) if count else np.nan,
        "mean_banked_gross_bp": float(events["banked_gross_return"].mean() * 1e4),
        "median_giveback_atr": float(events["gave_back_atr"].median()),
        "p95_net_bp": float(events["net_return"].quantile(0.95) * 1e4),
        "profit_floor_gap_breaches": int(events["profit_floor_gap_breach"].sum()),
    }


def fold_table(
    events: pd.DataFrame, folds: list[str], *, first_target_atr: float
) -> pd.DataFrame:
    labels = events["entry_time"].map(fold_label) if len(events) else pd.Series(dtype=str)
    return pd.DataFrame(
        [
            {
                "fold": fold,
                **policy_metrics(
                    events.loc[labels.eq(fold)].copy() if len(events) else events.copy(),
                    first_target_atr=first_target_atr,
                ),
            }
            for fold in folds
        ]
    )


def robust_summary(
    events: pd.DataFrame,
    folds: list[str],
    config: Mapping[str, Any],
    *,
    first_target_atr: float,
) -> dict[str, Any]:
    table = fold_table(events, folds, first_target_atr=first_target_atr)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(means) and np.isfinite(means).all())
    selection = config["selection"]
    return {
        **policy_metrics(events, first_target_atr=first_target_atr),
        "minimum_fold_events": int(counts.min()) if len(counts) else 0,
        "eligible": bool(
            len(events) >= int(selection["minimum_events_total"])
            and len(counts)
            and np.all(counts >= int(selection["minimum_events_per_fold"]))
            and finite
        ),
        "robust_score_bp": (
            float(np.median(means) - 0.5 * np.std(means, ddof=0))
            if finite
            else np.nan
        ),
        "worst_fold_net_bp": float(np.min(means)) if finite else np.nan,
    }


def _candidate_values(config: Mapping[str, Any], factor: str) -> list[float]:
    return list(map(float, config["selection"][f"{factor}_candidates"]))


def _rank(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
    if not bool(row["eligible"]):
        return (1.0, float("inf"), float("inf"), float("inf"))
    return (
        0.0,
        float(row["profit_to_nonpositive_share"]),
        -float(row["robust_score_bp"]),
        float(row["bank_total_fraction"]),
    )


def selection_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    split = config["splits"]
    dev_end = utc(split["development_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=dev_end)
    pairs, setups = build_frozen_setups(frame, config)
    dev = window(
        setups,
        split["development_start_inclusive"],
        split["development_end_exclusive"],
    )
    baseline = replay(dev, frame, config, bank_total_fraction=None, step_atr=None)
    params = deepcopy(config["selection"]["initial"])
    grid: list[dict[str, Any]] = []
    for stage, factor in enumerate(config["selection"]["ordered_single_factors"], start=1):
        stage_rows: list[dict[str, Any]] = []
        for value in _candidate_values(config, str(factor)):
            candidate_params = deepcopy(params)
            candidate_params[str(factor)] = value
            events = replay(dev, frame, config, **candidate_params)
            summary = robust_summary(
                events,
                list(map(str, split["development_folds"])),
                config,
                first_target_atr=float(candidate_params["step_atr"]),
            )
            row = {"stage": stage, "factor": factor, "value": value, **candidate_params, **summary}
            stage_rows.append(row)
            grid.append(row)
        winner = min(stage_rows, key=_rank)
        if not bool(winner["eligible"]):
            raise RuntimeError(f"no sample-eligible arm for {factor}")
        params[str(factor)] = float(winner[str(factor)])

    selected = replay(dev, frame, config, **params)
    first_target = float(params["step_atr"])
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(
        baseline, folds, config, first_target_atr=first_target
    )
    selected_summary = robust_summary(
        selected, folds, config, first_target_atr=first_target
    )
    gates = config["selection"]["success_gates"]
    baseline_reversal = float(baseline_summary["profit_to_nonpositive_share"])
    selected_reversal = float(selected_summary["profit_to_nonpositive_share"])
    relative_reduction = (
        (baseline_reversal - selected_reversal) / baseline_reversal
        if baseline_reversal > 0.0
        else np.nan
    )
    p95_retention = (
        float(selected_summary["p95_net_bp"]) / float(baseline_summary["p95_net_bp"])
        if float(baseline_summary["p95_net_bp"]) > 0.0
        else np.nan
    )
    checks = {
        "reversal_relative_reduction": relative_reduction,
        "reversal_gate": bool(
            np.isfinite(relative_reduction)
            and relative_reduction
            >= float(gates["profit_to_nonpositive_relative_reduction_min"])
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
        "p95_net_retention": p95_retention,
        "p95_gate": bool(
            np.isfinite(p95_retention)
            and p95_retention >= float(gates["candidate_p95_net_retention_min"])
        ),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(grid), RESULTS / "selection_coordinate_grid.csv")
    write_csv(
        fold_table(baseline, folds, first_target_atr=first_target).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds, first_target_atr=first_target).assign(policy="progressive"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit",
        "selected_params": params,
        "source": quality,
        "raw_pairs": len(pairs),
        "frozen_setups": len(dev),
        "baseline": baseline_summary,
        "candidate": selected_summary,
        "gates": checks,
        "all_behavior_and_transport_gates_pass": bool(
            checks["reversal_gate"]
            and checks["worst_fold_gate"]
            and checks["p95_gate"]
        ),
        "all_economic_gates_pass": bool(
            checks["mean_net_gate"] and checks["worst_fold_gate"]
        ),
        "audit_rows_read": 0,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(RESULTS / "selection_coordinate_grid.csv"),
        },
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def _matched_controls(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    params: Mapping[str, float],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidate.empty:
        return pd.DataFrame(), pd.DataFrame()
    execution = config["frozen_execution"]
    match = config["matched_control"]
    horizon = int(execution["horizon_bars"])
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= utc(frame.loc[entry_i, "open_time"]) < end
            and utc(frame.loc[last_i, "open_time"] + BAR_DELTA) <= end
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
            and np.isfinite(float(frame.loc[signal_i, "trend_ma"]))
        )
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    signal_indices = set(candidate["signal_i"].astype(int))
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & (buckets >= 0)):
        if int(index) in signal_indices:
            continue
        key = (str(months[index]), int(blocks[index]), int(buckets[index]))
        pool.setdefault(key, []).append(int(index))
    baseline_map = baseline.set_index("setup_id")
    required = int(match["controls_per_event"])
    radius = int(match["exclude_radius_bars"])
    seed = str(match["seed"])
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in candidate.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            (index for index in pool.get(key, []) if abs(index - signal_i) > radius),
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched",
                    "matched_control_count": len(choices),
                }
            )
            continue
        progressive_values: list[float] = []
        baseline_values: list[float] = []
        for assignment, control_i in enumerate(choices[:required]):
            control_event = {
                "setup_id": f"control-{control_i}",
                "signal_i": control_i,
                "entry_i": control_i + 1,
                "entry_time": frame.loc[control_i + 1, "open_time"],
                "entry_price": float(frame.loc[control_i + 1, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[control_i, "atr"]),
            }
            baseline_result = resolve_baseline(frame, control_event, config)
            progressive_result = resolve_progressive(
                frame,
                control_event,
                config,
                bank_total_fraction=float(params["bank_total_fraction"]),
                step_atr=float(params["step_atr"]),
            )
            if not baseline_result.get("resolved") or not progressive_result.get("resolved"):
                continue
            baseline_values.append(float(baseline_result["net_return"]))
            progressive_values.append(float(progressive_result["net_return"]))
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
                    "baseline_net_return": baseline_result["net_return"],
                    "progressive_net_return": progressive_result["net_return"],
                }
            )
        if len(progressive_values) != required:
            continue
        baseline_event = baseline_map.loc[event["setup_id"]]
        progressive_mean = float(np.mean(progressive_values))
        baseline_mean = float(np.mean(baseline_values))
        pairs.append(
            {
                "setup_id": event["setup_id"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_progressive_net_return": event["net_return"],
                "candidate_baseline_net_return": baseline_event["net_return"],
                "control_progressive_mean_net_return": progressive_mean,
                "control_baseline_mean_net_return": baseline_mean,
                "progressive_paired_excess": event["net_return"] - progressive_mean,
                "baseline_paired_excess": baseline_event["net_return"] - baseline_mean,
            }
        )
    return pd.DataFrame(controls), pd.DataFrame(pairs)


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    selection = _assert_selection_committed()
    params = {key: float(value) for key, value in selection["selected_params"].items()}
    split = config["splits"]
    audit_start = utc(split["audit_start_inclusive"])
    audit_end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=audit_end)
    _, setups = build_frozen_setups(frame, config)
    audit_setups = window(setups, audit_start, audit_end)
    baseline = replay(audit_setups, frame, config, bank_total_fraction=None, step_atr=None)
    candidate = replay(audit_setups, frame, config, **params)
    first_target = float(params["step_atr"])
    folds = list(map(str, split["audit_folds"]))
    baseline_summary = robust_summary(
        baseline, folds, config, first_target_atr=first_target
    )
    candidate_summary = robust_summary(
        candidate, folds, config, first_target_atr=first_target
    )
    comparison = candidate[["setup_id", "net_return"]].merge(
        baseline[["setup_id", "net_return"]], on="setup_id", suffixes=("_candidate", "_baseline")
    )
    comparison["delta"] = comparison["net_return_candidate"] - comparison["net_return_baseline"]
    controls, pairs = _matched_controls(
        baseline,
        candidate,
        frame,
        config,
        params=params,
        start=audit_start,
        end=audit_end,
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    matched_excess = matched["progressive_paired_excess"].astype(float)
    matched_summary = {
        "matched_events": len(matched),
        "candidate_progressive_mean_net_bp": float(
            matched["candidate_progressive_net_return"].mean() * 1e4
        ) if len(matched) else np.nan,
        "control_progressive_mean_net_bp": float(
            matched["control_progressive_mean_net_return"].mean() * 1e4
        ) if len(matched) else np.nan,
        "progressive_excess_bp": float(matched_excess.mean() * 1e4) if len(matched) else np.nan,
        "progressive_excess_signflip_p": float(
            signflip_p(matched_excess, resamples=100_000, seed=20260904)
        ) if len(matched) else np.nan,
    }
    first_target_baseline = float(baseline_summary["profit_to_nonpositive_share"])
    first_target_candidate = float(candidate_summary["profit_to_nonpositive_share"])
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "audit",
        "status": "research_only",
        "selected_params": params,
        "source": quality,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "setups": len(audit_setups),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "paired_candidate_minus_baseline": {
            "mean_delta_bp": float(comparison["delta"].mean() * 1e4),
            "median_delta_bp": float(comparison["delta"].median() * 1e4),
            "positive_delta_share": float(comparison["delta"].gt(0.0).mean()),
            "signflip_p": float(signflip_p(comparison["delta"], resamples=100_000, seed=90427)),
        },
        "profit_to_nonpositive_relative_reduction": (
            (first_target_baseline - first_target_candidate) / first_target_baseline
            if first_target_baseline > 0.0
            else np.nan
        ),
        "matched_random": matched_summary,
        "selection_receipt_sha256": sha256_file(SELECTION_PATH),
        "production_or_live_changed": False,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(baseline, RESULTS / "audit_baseline_trades.csv.gz")
    write_csv(candidate, RESULTS / "audit_candidate_trades.csv.gz")
    write_csv(comparison, RESULTS / "audit_paired_exit_deltas.csv")
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(
        fold_table(baseline, folds, first_target_atr=first_target).assign(policy="baseline"),
        RESULTS / "audit_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(candidate, folds, first_target_atr=first_target).assign(policy="progressive"),
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
