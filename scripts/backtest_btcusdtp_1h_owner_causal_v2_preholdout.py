#!/usr/bin/env python3
"""Develop and validate the owner-causal BTCUSDT.P 1h K1->K2 v2 rule.

Signal construction is causal. At K2 bar ``t`` it uses only completed OHLCV
through ``t`` plus Pine/Wilder ATR14, SMA40(HL2), MA-side candle colour, K1/K2
geometry, and bars strictly between K1 and K2. Entry is ``open[t+1]``. The
fee-to-risk gate uses that observable entry open and the completed K2 extreme.

Exit labels alone read future rows: exact K2 stop, frozen 3R target, twelve 1h
bars, stop-first same-bar collisions, 0.2% round-trip cost, and an optional
profit-protection stop. Protection is activated only after a completed path bar
closes beyond its trigger and can first act on the following bar.

The only source is a SHA-pinned OKX 15m file whose physical end is
2026-02-28T23:45:00Z. It is aggregated to complete UTC clock hours. Development
is 2023--2024; validation is 2025--2026-02-28. The repository holdout begins on
2026-05-04 and is never read by this experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import (
    _candidate_row,
    _causal_flags,
    accept_pine_events as reference_accept_events,
    detect_raw_candidates as reference_detect_candidates,
    signflip_p,
)
from scripts.research_two_key_candle_ma_retest_1h import (
    add_features,
    direction_columns,
    profit_factor,
    resample_hourly,
    sha256_file,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    PROJECT
    / "experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SOURCE_PATH = PROJECT / "data/kline_deep/okx_BTC_USDT_SWAP_15m_158499.csv"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()

TEAL = "#17A297"
ORANGE = "#F59E0B"
RED = "#F23645"
INK = "#26323A"
MUTED = "#73808A"
GRID = "#D9DEE1"


@dataclass(frozen=True)
class ArmSpec:
    """One causal morphology arm; fields control only pre-entry information."""

    name: str
    require_k1_colour: bool = False
    require_path: bool = False
    require_k2_wick_only: bool = False
    require_k1_body_065: bool = False


BASE_ARMS: tuple[ArmSpec, ...] = (
    ArmSpec("baseline"),
    ArmSpec("k1_colour_only", require_k1_colour=True),
    ArmSpec("path_only", require_path=True),
    ArmSpec("k2_wick_only", require_k2_wick_only=True),
    ArmSpec("k1_body_065_only", require_k1_body_065=True),
    ArmSpec(
        "fixed_bundle",
        require_k1_colour=True,
        require_path=True,
        require_k2_wick_only=True,
        require_k1_body_065=True,
    ),
    ArmSpec(
        "fixed_bundle_without_k1_colour",
        require_path=True,
        require_k2_wick_only=True,
        require_k1_body_065=True,
    ),
    ArmSpec(
        "fixed_bundle_without_path",
        require_k1_colour=True,
        require_k2_wick_only=True,
        require_k1_body_065=True,
    ),
    ArmSpec(
        "fixed_bundle_without_k2_wick",
        require_k1_colour=True,
        require_path=True,
        require_k1_body_065=True,
    ),
    ArmSpec(
        "fixed_bundle_without_k1_body_065",
        require_k1_colour=True,
        require_path=True,
        require_k2_wick_only=True,
    ),
)
FIXED_SPEC = next(spec for spec in BASE_ARMS if spec.name == "fixed_bundle")


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def json_value(value: Any) -> Any:
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return utc(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    else:
        frame.to_csv(path, index=False)


def baseline_adapter(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal": dict(config["baseline_signal"]),
        "execution": dict(config["execution"]),
    }


def load_featured(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    expected_hash = str(config["source"]["sha256"])
    actual_hash = sha256_file(SOURCE_PATH)
    if actual_hash != expected_hash:
        raise RuntimeError(f"source SHA drift: expected {expected_hash}, got {actual_hash}")
    safe_end = utc(config["window"]["validation_end_exclusive"])
    holdout_start = utc(config["window"]["holdout_start"])
    if safe_end >= holdout_start:
        raise RuntimeError("configured safe end reaches repository holdout")
    hourly, quality = resample_hourly(SOURCE_PATH, safe_end)
    if hourly.empty or utc(hourly["open_time"].max()) >= holdout_start:
        raise RuntimeError("source or aggregation reached repository holdout")
    if int(quality["hourly_gap_count"]) != 0:
        raise RuntimeError(f"hourly source has gaps: {quality}")
    quality["holdout_start"] = holdout_start
    quality["holdout_rows_read"] = 0
    quality["source_hash_matches_preregistration"] = True
    return add_features(hourly), quality


def k2_wick_only_pass(featured: pd.DataFrame, k2_i: int, direction: int) -> bool:
    """Require a physical SMA40 touch in the wick while body stays trend-side."""

    open_ = float(featured.loc[k2_i, "open"])
    close = float(featured.loc[k2_i, "close"])
    sma40 = float(featured.loc[k2_i, "sma40_hl2"])
    if not all(np.isfinite(value) for value in (open_, close, sma40)):
        return False
    if direction > 0:
        return min(open_, close) >= sma40
    return max(open_, close) <= sma40


def path_pass(row: dict[str, Any]) -> bool:
    return bool(
        int(row["wrong_sma40_close_count"]) == 0
        and float(row["intermediate_ma_colour_share"]) >= 1.0 - 1e-12
    )


def fee_to_risk_ratio(cost: float, risk_price: float, entry_price: float) -> float:
    """Return round-trip cost expressed in units of observable initial risk."""

    risk_fraction = risk_price / entry_price if entry_price > 0.0 else float("nan")
    return cost / risk_fraction if risk_fraction > 0.0 else float("inf")


def detect_candidates(
    featured: pd.DataFrame,
    config: dict[str, Any],
    spec: ArmSpec,
) -> pd.DataFrame:
    """Detect best-K1 candidates with arm gates applied before K1 selection."""

    signal = config["baseline_signal"]
    output: list[dict[str, Any]] = []
    for direction in (1, -1):
        side = direction_columns(featured, direction)
        for k2_i in range(int(signal["gap_max_bars"]), len(featured) - 1):
            k2_values = (
                side.loc[k2_i, "k2_wick_share"],
                side.loc[k2_i, "k2_body_ratio"],
                side.loc[k2_i, "k2_rejection_close_location"],
                side.loc[k2_i, "k2_sma40_touch_depth_atr"],
                side.loc[k2_i, "k2_sma40_close_side_atr"],
            )
            if not all(np.isfinite(float(value)) for value in k2_values):
                continue
            k2_ok = bool(
                float(k2_values[0]) >= float(signal["k2_min_rejection_wick_share"])
                and float(k2_values[1]) <= float(signal["k2_max_body_ratio"])
                and float(k2_values[2]) >= float(signal["k2_min_rejection_close_location"])
                and float(signal["k2_touch_depth_atr_min"])
                <= float(k2_values[3])
                <= float(signal["k2_touch_depth_atr_max"])
                and float(k2_values[4]) >= float(signal["k2_min_close_back_depth_atr"])
            )
            if spec.require_k2_wick_only:
                k2_ok = bool(
                    k2_ok
                    and float(k2_values[3]) >= 0.0
                    and k2_wick_only_pass(featured, k2_i, direction)
                )
            if not k2_ok:
                continue

            best: dict[str, Any] | None = None
            for gap in range(int(signal["gap_min_bars"]), int(signal["gap_max_bars"]) + 1):
                k1_i = k2_i - gap
                values = (
                    side.loc[k1_i, "k1_body_ratio"],
                    side.loc[k1_i, "k1_range_atr"],
                    side.loc[k1_i, "k1_close_location"],
                    side.loc[k1_i, "k1_sma40_cross_depth_atr"],
                    featured.loc[k1_i, "rope_high"],
                    featured.loc[k1_i, "rope_low"],
                )
                if not all(np.isfinite(float(value)) for value in values):
                    continue
                directional_body = direction * (
                    float(featured.loc[k1_i, "close"]) - float(featured.loc[k1_i, "open"])
                ) > 0.0
                body_min = 0.65 if spec.require_k1_body_065 else float(signal["k1_min_body_ratio"])
                k1_ok = bool(
                    directional_body
                    and float(values[0]) >= body_min
                    and float(values[1]) >= float(signal["k1_min_range_atr"])
                    and float(values[2]) >= float(signal["k1_min_directional_close_location"])
                    and float(values[3]) >= float(signal["k1_min_sma40_cross_depth_atr"])
                )
                if spec.require_k1_colour:
                    k1_ok = bool(k1_ok and bool(side.loc[k1_i, "k1_ma_colour_aligned"]))
                if not k1_ok:
                    continue
                row = _candidate_row(
                    featured,
                    side,
                    direction=direction,
                    k1_i=k1_i,
                    k2_i=k2_i,
                    gap=gap,
                )
                if spec.require_path and not path_pass(row):
                    continue
                if best is None or float(row["k1_quality"]) > float(best["k1_quality"]):
                    best = row
            if best is not None:
                output.append(best)
    if not output:
        return pd.DataFrame()
    return pd.DataFrame(output).sort_values(
        ["k2_i", "direction"], ascending=[True, False], kind="mergesort"
    ).reset_index(drop=True)


def accept_events(
    candidates: pd.DataFrame,
    featured: pd.DataFrame,
    config: dict[str, Any],
    *,
    fee_to_risk_max: float | None,
) -> pd.DataFrame:
    """Apply observable next-open risk/economics, K1 memory, and cooldown."""

    if candidates.empty:
        return candidates.copy()
    signal = config["baseline_signal"]
    cost = float(config["execution"]["round_trip_cost_fraction"])
    by_bar_side = {
        (int(row.k2_i), int(row.direction)): row._asdict()
        for row in candidates.itertuples(index=False)
    }
    accepted: list[dict[str, Any]] = []
    last_accepted_entry = -10**12
    last_k1 = {1: None, -1: None}
    for k2_i in sorted(candidates["k2_i"].astype(int).unique()):
        entry_i = k2_i + 1
        for direction in (1, -1):
            base = by_bar_side.get((k2_i, direction))
            if base is None:
                continue
            entry_price = float(featured.loc[entry_i, "open"])
            stop_price = float(
                featured.loc[k2_i, "low"] if direction > 0 else featured.loc[k2_i, "high"]
            )
            risk = direction * (entry_price - stop_price)
            atr = float(featured.loc[k2_i, "atr"])
            risk_atr = risk / atr if atr > 0.0 else float("nan")
            risk_fraction = risk / entry_price if entry_price > 0.0 else float("nan")
            fee_to_risk = fee_to_risk_ratio(cost, risk, entry_price)
            if not (
                np.isfinite(risk_atr)
                and float(signal["next_open_risk_atr_min"])
                <= risk_atr
                <= float(signal["next_open_risk_atr_max"])
            ):
                continue
            if fee_to_risk_max is not None and fee_to_risk > float(fee_to_risk_max):
                continue
            cooldown_ready = entry_i - last_accepted_entry >= int(signal["cooldown_bars"])
            k1_unused = last_k1[direction] is None or int(base["k1_i"]) != int(last_k1[direction])
            if not cooldown_ready or not k1_unused:
                continue
            row = dict(base)
            setup_key = (
                f"BTC-USDT-SWAP|1H|{direction}|"
                f"{featured.loc[k2_i, 'open_time'].isoformat()}|{row['k1_i']}"
            )
            row.update(
                {
                    "entry_i": entry_i,
                    "entry_time": featured.loc[entry_i, "open_time"],
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "risk_price": risk,
                    "risk_fraction": risk_fraction,
                    "fee_to_risk": fee_to_risk,
                    "stop_distance_atr": risk_atr,
                    "target_price": entry_price
                    + direction * risk * float(config["execution"]["target_r"]),
                    "setup_id": hashlib.sha256(setup_key.encode()).hexdigest()[:16],
                }
            )
            risk_quality = 1.0 if 0.25 <= risk_atr <= 2.00 else 0.0
            k2_score = (
                float(row["anchor_k2_score_pre_risk"]) / 100.0 * 5.0 + risk_quality
            ) / 6.0
            row["anchor_k2_score"] = k2_score * 100.0
            row["anchor_score"] = 0.25 * (
                float(row["anchor_k1_score"])
                + float(row["anchor_k2_score"])
                + float(row["anchor_path_score"])
                + float(row["anchor_state_score"])
            )
            flags = _causal_flags(row)
            row["causal_flag_count"] = len(flags)
            row["causal_flags"] = "|".join(flags)
            accepted.append(row)
            last_accepted_entry = entry_i
            last_k1[direction] = int(row["k1_i"])
            break
    return pd.DataFrame(accepted).sort_values("entry_i", kind="mergesort").reset_index(drop=True)


def resolve_exit(
    featured: pd.DataFrame,
    event: dict[str, Any],
    config: dict[str, Any],
    *,
    protection_trigger_r: float | None,
) -> dict[str, Any]:
    """Resolve frozen 3R execution with next-bar causal protection activation."""

    execution = config["execution"]
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    risk = float(event["risk_price"])
    initial_stop = float(event["stop_price"])
    target = entry + direction * risk * float(execution["target_r"])
    horizon = int(execution["horizon_bars"])
    cost = float(execution["round_trip_cost_fraction"])
    available = min(horizon, len(featured) - entry_i)
    if available <= 0:
        return {"resolved": False, "outcome": "unresolved", "hold_bars": 0}

    fee_cover_stop = entry * (1.0 + direction * cost)
    protection_active = False
    protection_armed_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    mfe = 0.0
    mae = 0.0
    for i in range(entry_i, entry_i + available):
        high = float(featured.loc[i, "high"])
        low = float(featured.loc[i, "low"])
        close = float(featured.loc[i, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        mfe = max(mfe, favourable)
        mae = max(mae, adverse)
        active_stop = fee_cover_stop if protection_active else initial_stop
        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        hit_target = high >= target if direction > 0 else low <= target
        if hit_stop:
            exit_i = i
            exit_price = active_stop
            if hit_target:
                outcome = "protected_stop_ambiguous" if protection_active else "sl_ambiguous"
            else:
                outcome = "protected_stop" if protection_active else "sl"
            break
        if hit_target:
            exit_i = i
            exit_price = target
            outcome = "tp"
            break
        if protection_trigger_r is not None and not protection_active:
            close_r = direction * (close - entry) / risk
            if close_r >= float(protection_trigger_r):
                protection_active = True
                protection_armed_i = i

    if exit_i is None and available < horizon:
        return {
            "resolved": False,
            "outcome": "unresolved",
            "hold_bars": available,
            "mfe_r": mfe / risk,
            "mae_r": mae / risk,
            "protection_armed": protection_armed_i is not None,
            "protection_armed_i": protection_armed_i,
        }
    if exit_i is None:
        exit_i = entry_i + horizon - 1
        exit_price = float(featured.loc[exit_i, "close"])
        outcome = "timeout"
    gross = direction * (float(exit_price) / entry - 1.0)
    net = gross - cost
    return {
        "resolved": True,
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": featured.loc[exit_i, "open_time"] + pd.Timedelta(hours=1),
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "net_return": net,
        "return_r": direction * (float(exit_price) - entry) / risk,
        "net_return_r": net / float(event["risk_fraction"]),
        "mfe_r": mfe / risk,
        "mae_r": mae / risk,
        "protection_trigger_r": protection_trigger_r,
        "protection_armed": protection_armed_i is not None,
        "protection_armed_i": protection_armed_i,
        "fee_cover_stop": fee_cover_stop if protection_trigger_r is not None else np.nan,
    }


def attach_outcomes(
    events: pd.DataFrame,
    featured: pd.DataFrame,
    config: dict[str, Any],
    *,
    protection_trigger_r: float | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        result = resolve_exit(
            featured,
            event,
            config,
            protection_trigger_r=protection_trigger_r,
        )
        rows.append({**event, **result})
    return pd.DataFrame(rows)


def period_events(
    events: pd.DataFrame,
    featured: pd.DataFrame,
    config: dict[str, Any],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Keep entries whose entire frozen horizon remains inside one split."""

    if events.empty:
        return events.copy()
    horizon = int(config["execution"]["horizon_bars"])
    last_indices = events["entry_i"].astype(int) + horizon - 1
    last_times = last_indices.map(
        lambda index: featured.loc[index, "open_time"] if index < len(featured) else pd.NaT
    )
    mask = (
        events["entry_time"].ge(start)
        & events["entry_time"].lt(end)
        & last_times.notna()
        & last_times.lt(end)
    )
    return events.loc[mask].copy().reset_index(drop=True)


def halfyear_label(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"


def equity_metrics(net_r: Iterable[float]) -> tuple[float, float]:
    array = np.asarray(list(net_r), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return float("nan"), float("nan")
    equity = np.cumprod(1.0 + 0.01 * array)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))[:-1]
    drawdowns = equity / peaks - 1.0
    return float(equity[-1] - 1.0), float(drawdowns.min())


def metric_row(events: pd.DataFrame) -> dict[str, Any]:
    scored = events[events.get("resolved", False).astype(bool)].copy() if len(events) else events.copy()
    if scored.empty:
        return {
            "events": 0,
            "mean_net_bp": np.nan,
            "median_net_bp": np.nan,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "equal_risk_1pct_return": np.nan,
            "max_drawdown": np.nan,
            "tp": 0,
            "sl": 0,
            "protected_stop": 0,
            "timeout": 0,
        }
    equal_return, max_drawdown = equity_metrics(scored["net_return_r"])
    outcomes = scored["outcome"].astype(str)
    return {
        "events": len(scored),
        "mean_net_bp": float(scored["net_return"].mean() * 1e4),
        "median_net_bp": float(scored["net_return"].median() * 1e4),
        "win_rate": float(scored["net_return"].gt(0.0).mean()),
        "profit_factor": float(profit_factor(scored["net_return"])),
        "equal_risk_1pct_return": equal_return,
        "max_drawdown": max_drawdown,
        "tp": int(outcomes.eq("tp").sum()),
        "sl": int(outcomes.isin(["sl", "sl_ambiguous"]).sum()),
        "protected_stop": int(outcomes.str.startswith("protected_stop").sum()),
        "timeout": int(outcomes.eq("timeout").sum()),
    }


def fold_table(events: pd.DataFrame, folds: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    labels = events["entry_time"].map(halfyear_label) if len(events) else pd.Series(dtype=str)
    for fold in folds:
        subset = events.loc[labels.eq(fold)].copy() if len(events) else events.copy()
        rows.append({"fold": fold, **metric_row(subset)})
    return pd.DataFrame(rows)


def robust_score(
    events: pd.DataFrame,
    folds: list[str],
    *,
    minimum_total: int,
    minimum_per_fold: int,
) -> dict[str, Any]:
    table = fold_table(events, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    eligible = bool(
        len(events) >= minimum_total
        and np.all(counts >= minimum_per_fold)
        and np.isfinite(means).all()
    )
    score = float(np.median(means) - 0.5 * np.std(means, ddof=0)) if np.isfinite(means).all() else np.nan
    return {
        "events": len(events),
        "minimum_fold_events": int(counts.min()) if len(counts) else 0,
        "robust_score_bp": score,
        "worst_fold_net_bp": float(np.min(means)) if np.isfinite(means).all() else np.nan,
        "eligible": eligible,
        **{f"{row.fold}_events": int(row.events) for row in table.itertuples(index=False)},
        **{f"{row.fold}_mean_net_bp": float(row.mean_net_bp) for row in table.itertuples(index=False)},
    }


def atr_quintiles(featured: pd.DataFrame, eligible: pd.Series) -> np.ndarray:
    buckets = np.full(len(featured), -1, dtype=int)
    helper = pd.DataFrame(
        {
            "i": np.arange(len(featured)),
            "month": featured["open_time"].dt.strftime("%Y-%m"),
            "atr": featured["atr"],
            "eligible": eligible.to_numpy(dtype=bool),
        }
    )
    valid = helper[helper["eligible"] & helper["atr"].notna()]
    for _, group in valid.groupby("month", sort=True):
        ranks = group["atr"].rank(method="first")
        quantiles = min(5, len(group))
        labels = pd.qcut(ranks, q=quantiles, labels=False, duplicates="drop")
        labels = labels.fillna(0).astype(int)
        buckets[group["i"].to_numpy(dtype=int)] = labels.to_numpy(dtype=int)
    return buckets


def build_matched_controls(
    events: pd.DataFrame,
    featured: pd.DataFrame,
    baseline_candidate_indices: set[int],
    config: dict[str, Any],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    protection_trigger_r: float | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match random bars on month, UTC block and ATR quintile without relaxation."""

    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    horizon = int(config["execution"]["horizon_bars"])
    indices = pd.Series(np.arange(len(featured)), index=featured.index)
    entry_times = featured["open_time"].shift(-1)
    end_indices = indices + horizon
    eligible = (
        entry_times.ge(start)
        & entry_times.lt(end)
        & end_indices.lt(len(featured))
        & end_indices.map(lambda i: featured.loc[int(i), "open_time"] if i < len(featured) else pd.NaT).lt(end)
        & featured["atr"].notna()
    )
    exclusion = np.zeros(len(featured), dtype=bool)
    radius = int(config["matched_control"]["exclude_within_bars_of_any_baseline_candidate"])
    for index in baseline_candidate_indices:
        exclusion[max(0, index - radius) : min(len(featured), index + radius + 1)] = True
    buckets = atr_quintiles(featured, eligible)
    months = featured["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (featured["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    valid_indices = np.flatnonzero(eligible.to_numpy(dtype=bool) & ~exclusion & (buckets >= 0))
    for index in valid_indices:
        pool.setdefault((str(months[index]), int(blocks[index]), int(buckets[index])), []).append(int(index))

    required = int(config["matched_control"]["controls_per_trade"])
    seed = str(config["matched_control"]["seed"])
    control_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        signal_i = int(event["k2_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        ranked = sorted(
            pool.get(key, []),
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        if len(ranked) < required:
            pair_rows.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched_insufficient_exact_stratum",
                    "matched_control_count": len(ranked),
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": np.nan,
                    "paired_excess_return": np.nan,
                }
            )
            continue
        current: list[float] = []
        for rank, control_i in enumerate(ranked[:required]):
            entry_i = control_i + 1
            entry = float(featured.loc[entry_i, "open"])
            direction = int(event["direction"])
            risk = float(event["stop_distance_atr"]) * float(featured.loc[control_i, "atr"])
            control_event = {
                "entry_i": entry_i,
                "direction": direction,
                "entry_price": entry,
                "risk_price": risk,
                "risk_fraction": risk / entry,
                "stop_price": entry - direction * risk,
            }
            result = resolve_exit(
                featured,
                control_event,
                config,
                protection_trigger_r=protection_trigger_r,
            )
            if not result.get("resolved"):
                raise RuntimeError("eligible exact-stratum control was unresolved")
            current.append(float(result["net_return"]))
            control_rows.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "control_rank": rank,
                    "control_i": control_i,
                    "control_time": featured.loc[control_i, "open_time"],
                    "direction": direction,
                    "month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    "copied_stop_distance_atr": event["stop_distance_atr"],
                    **result,
                }
            )
        control_mean = float(np.mean(current))
        pair_rows.append(
            {
                "setup_id": event["setup_id"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_net_return": event["net_return"],
                "control_mean_net_return": control_mean,
                "paired_excess_return": float(event["net_return"]) - control_mean,
            }
        )
    return pd.DataFrame(control_rows), pd.DataFrame(pair_rows)


def add_control_metrics(row: dict[str, Any], pairs: pd.DataFrame) -> dict[str, Any]:
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy() if len(pairs) else pairs.copy()
    if matched.empty:
        return {
            **row,
            "matched_events": 0,
            "matched_control_excess_bp": np.nan,
            "paired_signflip_p_one_sided": np.nan,
        }
    excess = matched["paired_excess_return"].astype(float)
    return {
        **row,
        "matched_events": len(matched),
        "matched_control_excess_bp": float(excess.mean() * 1e4),
        "paired_signflip_p_one_sided": float(signflip_p(excess, resamples=100000, seed=20260904)),
    }


def assert_baseline_parity(
    featured: pd.DataFrame,
    config: dict[str, Any],
    own_candidates: pd.DataFrame,
    own_events: pd.DataFrame,
) -> dict[str, Any]:
    adapter = baseline_adapter(config)
    reference_candidates = reference_detect_candidates(featured, adapter)
    reference_events = reference_accept_events(reference_candidates, featured, adapter)
    candidate_cols = ["direction", "k1_i", "k2_i", "gap_bars"]
    event_cols = ["direction", "k1_i", "k2_i", "entry_i"]
    pd.testing.assert_frame_equal(
        own_candidates[candidate_cols].reset_index(drop=True),
        reference_candidates[candidate_cols].reset_index(drop=True),
        check_dtype=False,
    )
    pd.testing.assert_frame_equal(
        own_events[event_cols].reset_index(drop=True),
        reference_events[event_cols].reset_index(drop=True),
        check_dtype=False,
    )
    return {
        "candidate_rows": len(own_candidates),
        "accepted_rows": len(own_events),
        "candidate_keys_exact": True,
        "accepted_keys_exact": True,
    }


def arm_run(
    featured: pd.DataFrame,
    config: dict[str, Any],
    spec: ArmSpec,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    fee_to_risk_max: float | None = None,
    protection_trigger_r: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = detect_candidates(featured, config, spec)
    events = accept_events(
        candidates,
        featured,
        config,
        fee_to_risk_max=fee_to_risk_max,
    )
    events = period_events(events, featured, config, start=start, end=end)
    events = attach_outcomes(
        events,
        featured,
        config,
        protection_trigger_r=protection_trigger_r,
    )
    return candidates, events


def choose_fee_threshold(trace: pd.DataFrame, config: dict[str, Any]) -> tuple[float, str]:
    finite = trace[trace["fee_to_risk_max"].notna()].copy()
    eligible = finite[finite["eligible"].astype(bool)].copy()
    if eligible.empty:
        return float(max(config["fee_to_risk_selection"]["maximum_grid"])), "insufficient_fallback"
    eligible = eligible.sort_values(
        ["robust_score_bp", "worst_fold_net_bp", "events", "fee_to_risk_max"],
        ascending=[False, False, False, False],
        kind="mergesort",
    )
    return float(eligible.iloc[0]["fee_to_risk_max"]), "selected_by_preregistered_robust_score"


def choose_protection_trigger(trace: pd.DataFrame) -> tuple[float | None, str]:
    reference = trace[trace["protection_trigger_r"].isna()].iloc[0]
    finite = trace[trace["protection_trigger_r"].notna() & trace["eligible"].astype(bool)].copy()
    if not bool(reference["eligible"]) or finite.empty:
        return None, "disabled_insufficient_evidence"
    passing = finite[
        finite["robust_score_bp"].ge(float(reference["robust_score_bp"]) + 1.0)
        & finite["worst_fold_net_bp"].ge(float(reference["worst_fold_net_bp"]))
    ].copy()
    if passing.empty:
        return None, "disabled_no_preregistered_improvement"
    passing = passing.sort_values(
        ["robust_score_bp", "worst_fold_net_bp", "protection_trigger_r"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    return float(passing.iloc[0]["protection_trigger_r"]), "enabled_by_preregistered_improvement"


def selection_plot(fee_trace: pd.DataFrame, protection_trace: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.2), constrained_layout=True)
    fee_labels = ["none" if pd.isna(value) else f"≤{value:g}R" for value in fee_trace["fee_to_risk_max"]]
    axes[0].plot(fee_labels, fee_trace["robust_score_bp"], marker="o", color=TEAL, lw=2)
    axes[0].axhline(0.0, color=GRID, lw=1)
    axes[0].set_title("Fee-to-risk development selection")
    axes[0].set_ylabel("robust half-year score (bp)")
    axes[0].tick_params(axis="x", rotation=30)
    protection_labels = [
        "off" if pd.isna(value) else f"{value:g}R close"
        for value in protection_trace["protection_trigger_r"]
    ]
    axes[1].plot(
        protection_labels,
        protection_trace["robust_score_bp"],
        marker="o",
        color=ORANGE,
        lw=2,
    )
    axes[1].axhline(0.0, color=GRID, lw=1)
    axes[1].set_title("Profit-protection development selection")
    axes[1].tick_params(axis="x", rotation=30)
    for axis in axes:
        axis.grid(axis="y", color=GRID, alpha=0.7)
        axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def component_plot(metrics: pd.DataFrame, output: Path, title: str) -> None:
    shown = metrics.sort_values("mean_net_bp", na_position="first").copy()
    colours = [TEAL if value >= 0 else RED for value in shown["mean_net_bp"].fillna(0.0)]
    fig, axis = plt.subplots(figsize=(10.5, 6.5), constrained_layout=True)
    bars = axis.barh(shown["arm"], shown["mean_net_bp"], color=colours, alpha=0.88)
    axis.axvline(0.0, color=INK, lw=1)
    axis.set_xlabel("mean net return per trade (bp, after 20 bp cost)")
    axis.set_title(title, loc="left", fontweight="bold")
    axis.grid(axis="x", color=GRID, alpha=0.65)
    axis.spines[["top", "right", "left"]].set_visible(False)
    for bar, count in zip(bars, shown["events"]):
        value = bar.get_width()
        axis.text(
            value + (0.8 if value >= 0 else -0.8),
            bar.get_y() + bar.get_height() / 2,
            f"n={int(count)}",
            va="center",
            ha="left" if value >= 0 else "right",
            fontsize=8,
            color=MUTED,
        )
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def equity_plot(events_by_arm: dict[str, pd.DataFrame], output: Path) -> None:
    fig, axis = plt.subplots(figsize=(11.0, 4.8), constrained_layout=True)
    styles = {"baseline": (MUTED, "--"), "fixed_bundle": (ORANGE, "-"), "final": (TEAL, "-")}
    for arm, (colour, linestyle) in styles.items():
        events = events_by_arm.get(arm, pd.DataFrame()).sort_values("entry_time")
        if events.empty:
            continue
        equity = np.cumprod(1.0 + 0.01 * events["net_return_r"].to_numpy(dtype=float))
        axis.step(events["entry_time"], equity, where="post", label=arm, color=colour, ls=linestyle, lw=2)
    axis.axhline(1.0, color=GRID, lw=1)
    axis.set_title("Validation equal-risk equity (1% initial risk per trade)", loc="left", fontweight="bold")
    axis.set_ylabel("equity multiple")
    axis.legend(frameon=False, ncol=3)
    axis.grid(axis="y", color=GRID, alpha=0.65)
    axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def failure_plot(events: pd.DataFrame, output: Path) -> None:
    fig, axis = plt.subplots(figsize=(7.2, 5.4), constrained_layout=True)
    colours = events["net_return"].gt(0.0).map({True: TEAL, False: RED})
    axis.scatter(events["mfe_r"], events["mae_r"], c=colours, alpha=0.82, edgecolor="white", lw=0.5)
    axis.axvline(1.0, color=GRID, lw=1, ls="--")
    axis.axvline(2.0, color=GRID, lw=1, ls="--")
    axis.axhline(1.0, color=GRID, lw=1, ls="--")
    axis.set_xlabel("maximum favourable excursion (R)")
    axis.set_ylabel("maximum adverse excursion (R)")
    axis.set_title("Final-rule validation paths", loc="left", fontweight="bold")
    axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def tracked_receipt_matches_head() -> None:
    relative = SELECTION_PATH.relative_to(PROJECT).as_posix()
    try:
        committed = subprocess.check_output(
            ["git", "show", f"HEAD:{relative}"], cwd=PROJECT, stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "selection receipt must be committed before validation"
        ) from exc
    if committed != SELECTION_PATH.read_bytes():
        raise RuntimeError("working selection receipt differs from committed HEAD")


def run_development(config: dict[str, Any], featured: pd.DataFrame, quality: dict[str, Any]) -> None:
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    min_total = int(config["fee_to_risk_selection"]["minimum_events_total"])
    min_fold = int(config["fee_to_risk_selection"]["minimum_events_per_development_fold"])

    baseline_candidates, baseline_events = arm_run(
        featured, config, BASE_ARMS[0], start=start, end=end
    )
    own_all = accept_events(
        baseline_candidates, featured, config, fee_to_risk_max=None
    )
    parity = assert_baseline_parity(featured, config, baseline_candidates, own_all)
    baseline_indices = set(baseline_candidates["k2_i"].astype(int))

    arm_events: dict[str, pd.DataFrame] = {}
    arm_candidates: dict[str, pd.DataFrame] = {"baseline": baseline_candidates}
    for spec in BASE_ARMS:
        candidates, events = (
            (baseline_candidates, baseline_events)
            if spec.name == "baseline"
            else arm_run(featured, config, spec, start=start, end=end)
        )
        arm_candidates[spec.name] = candidates
        arm_events[spec.name] = events

    fixed_candidates = arm_candidates["fixed_bundle"]
    fee_rows: list[dict[str, Any]] = []
    fee_events: dict[float | None, pd.DataFrame] = {}
    for threshold in [None, *config["fee_to_risk_selection"]["maximum_grid"]]:
        accepted = accept_events(
            fixed_candidates,
            featured,
            config,
            fee_to_risk_max=None if threshold is None else float(threshold),
        )
        accepted = period_events(accepted, featured, config, start=start, end=end)
        outcome = attach_outcomes(
            accepted, featured, config, protection_trigger_r=None
        )
        fee_events[None if threshold is None else float(threshold)] = outcome
        fee_rows.append(
            {
                "fee_to_risk_max": np.nan if threshold is None else float(threshold),
                **metric_row(outcome),
                **robust_score(
                    outcome,
                    folds,
                    minimum_total=min_total,
                    minimum_per_fold=min_fold,
                ),
            }
        )
    fee_trace = pd.DataFrame(fee_rows)
    selected_fee, fee_status = choose_fee_threshold(fee_trace, config)

    selected_fee_events = fee_events[selected_fee]
    protection_rows: list[dict[str, Any]] = []
    protection_events: dict[float | None, pd.DataFrame] = {}
    for trigger in [None, *config["profit_protection_selection"]["trigger_close_r_grid"]]:
        outcome = attach_outcomes(
            selected_fee_events.drop(
                columns=[
                    column
                    for column in (
                        "resolved", "outcome", "exit_i", "exit_time", "exit_price", "hold_bars",
                        "gross_return", "net_return", "return_r", "net_return_r", "mfe_r", "mae_r",
                        "protection_trigger_r", "protection_armed", "protection_armed_i", "fee_cover_stop",
                    )
                    if column in selected_fee_events.columns
                ]
            ),
            featured,
            config,
            protection_trigger_r=None if trigger is None else float(trigger),
        )
        protection_events[None if trigger is None else float(trigger)] = outcome
        protection_rows.append(
            {
                "protection_trigger_r": np.nan if trigger is None else float(trigger),
                **metric_row(outcome),
                **robust_score(
                    outcome,
                    folds,
                    minimum_total=min_total,
                    minimum_per_fold=min_fold,
                ),
            }
        )
    protection_trace = pd.DataFrame(protection_rows)
    selected_protection, protection_status = choose_protection_trigger(protection_trace)

    arm_events["fixed_plus_fee"] = protection_events[None]
    arm_events["final"] = protection_events[selected_protection]
    metric_rows: list[dict[str, Any]] = []
    control_frames: list[pd.DataFrame] = []
    pair_frames: list[pd.DataFrame] = []
    ledger_frames: list[pd.DataFrame] = []
    for arm, events in arm_events.items():
        trigger = selected_protection if arm == "final" else None
        controls, pairs = build_matched_controls(
            events,
            featured,
            baseline_indices,
            config,
            start=start,
            end=end,
            protection_trigger_r=trigger,
        )
        metric_rows.append(add_control_metrics({"arm": arm, **metric_row(events)}, pairs))
        if len(events):
            ledger_frames.append(events.assign(arm=arm))
        if len(controls):
            control_frames.append(controls.assign(arm=arm))
        if len(pairs):
            pair_frames.append(pairs.assign(arm=arm))

    metrics = pd.DataFrame(metric_rows)
    write_csv(fee_trace, RESULTS / "development_fee_grid.csv")
    write_csv(protection_trace, RESULTS / "development_protection_grid.csv")
    write_csv(metrics, RESULTS / "development_arm_metrics.csv")
    write_csv(pd.concat(ledger_frames, ignore_index=True), RESULTS / "development_events.csv.gz")
    write_csv(pd.concat(control_frames, ignore_index=True), RESULTS / "development_controls.csv.gz")
    write_csv(pd.concat(pair_frames, ignore_index=True), RESULTS / "development_pairs.csv.gz")
    write_json(RESULTS / "data_quality.json", quality)
    selection_plot(fee_trace, protection_trace, RESULTS / "development_selection.png")
    component_plot(metrics, RESULTS / "development_components.png", "Development component and bundle results")

    receipt = {
        "experiment_id": config["experiment_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "development_selection_frozen",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "source_sha256": sha256_file(SOURCE_PATH),
        "source_last_hour": quality["last_hour"],
        "holdout_rows_read": 0,
        "baseline_parity": parity,
        "selected_fee_to_risk_max": selected_fee,
        "fee_selection_status": fee_status,
        "selected_protection_trigger_r": selected_protection,
        "protection_selection_status": protection_status,
        "development_fee_grid_sha256": sha256_file(RESULTS / "development_fee_grid.csv"),
        "development_protection_grid_sha256": sha256_file(
            RESULTS / "development_protection_grid.csv"
        ),
        "validation_read": false,
        "production_mutation": false,
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def run_validation(config: dict[str, Any], featured: pd.DataFrame, quality: dict[str, Any]) -> None:
    tracked_receipt_matches_head()
    receipt = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    for key, actual in (
        ("config_sha256", sha256_file(CONFIG_PATH)),
        ("script_sha256", sha256_file(SCRIPT_PATH)),
        ("source_sha256", sha256_file(SOURCE_PATH)),
    ):
        if receipt[key] != actual:
            raise RuntimeError(f"frozen selection dependency drifted: {key}")
    start = utc(config["window"]["validation_start_inclusive"])
    end = utc(config["window"]["validation_end_exclusive"])
    selected_fee = float(receipt["selected_fee_to_risk_max"])
    selected_protection = receipt["selected_protection_trigger_r"]
    selected_protection = None if selected_protection is None else float(selected_protection)

    baseline_candidates = detect_candidates(featured, config, BASE_ARMS[0])
    own_all = accept_events(baseline_candidates, featured, config, fee_to_risk_max=None)
    parity = assert_baseline_parity(featured, config, baseline_candidates, own_all)
    baseline_indices = set(baseline_candidates["k2_i"].astype(int))

    events_by_arm: dict[str, pd.DataFrame] = {}
    for spec in BASE_ARMS:
        _, events = arm_run(featured, config, spec, start=start, end=end)
        events_by_arm[spec.name] = events
    _, fixed_fee_events = arm_run(
        featured,
        config,
        FIXED_SPEC,
        start=start,
        end=end,
        fee_to_risk_max=selected_fee,
        protection_trigger_r=None,
    )
    _, final_events = arm_run(
        featured,
        config,
        FIXED_SPEC,
        start=start,
        end=end,
        fee_to_risk_max=selected_fee,
        protection_trigger_r=selected_protection,
    )
    events_by_arm["fixed_plus_fee"] = fixed_fee_events
    events_by_arm["final"] = final_events

    metric_rows: list[dict[str, Any]] = []
    ledger_frames: list[pd.DataFrame] = []
    control_frames: list[pd.DataFrame] = []
    pair_frames: list[pd.DataFrame] = []
    halfyear_frames: list[pd.DataFrame] = []
    direction_rows: list[dict[str, Any]] = []
    for arm, events in events_by_arm.items():
        trigger = selected_protection if arm == "final" else None
        controls, pairs = build_matched_controls(
            events,
            featured,
            baseline_indices,
            config,
            start=start,
            end=end,
            protection_trigger_r=trigger,
        )
        metric_rows.append(add_control_metrics({"arm": arm, **metric_row(events)}, pairs))
        halves = sorted(events["entry_time"].map(halfyear_label).unique()) if len(events) else []
        if halves:
            halfyear_frames.append(fold_table(events, halves).assign(arm=arm))
        for direction, label in ((1, "long"), (-1, "short")):
            direction_rows.append(
                {"arm": arm, "side": label, **metric_row(events[events["direction"].eq(direction)])}
            )
        if len(events):
            ledger_frames.append(events.assign(arm=arm))
        if len(controls):
            control_frames.append(controls.assign(arm=arm))
        if len(pairs):
            pair_frames.append(pairs.assign(arm=arm))

    metrics = pd.DataFrame(metric_rows)
    all_events = pd.concat(ledger_frames, ignore_index=True)
    all_controls = pd.concat(control_frames, ignore_index=True) if control_frames else pd.DataFrame()
    all_pairs = pd.concat(pair_frames, ignore_index=True) if pair_frames else pd.DataFrame()
    halfyears = pd.concat(halfyear_frames, ignore_index=True) if halfyear_frames else pd.DataFrame()
    directions = pd.DataFrame(direction_rows)
    write_csv(metrics, RESULTS / "validation_arm_metrics.csv")
    write_csv(all_events, RESULTS / "validation_events.csv.gz")
    write_csv(all_controls, RESULTS / "validation_controls.csv.gz")
    write_csv(all_pairs, RESULTS / "validation_pairs.csv.gz")
    write_csv(halfyears, RESULTS / "validation_halfyears.csv")
    write_csv(directions, RESULTS / "validation_directions.csv")

    baseline = events_by_arm["baseline"]
    final = events_by_arm["final"]
    baseline_keys = set(baseline["setup_id"]) if len(baseline) else set()
    final_keys = set(final["setup_id"]) if len(final) else set()
    overlap = {
        "baseline_events": len(baseline),
        "final_events": len(final),
        "retained_exact_setups": len(baseline_keys & final_keys),
        "removed_baseline_setups": len(baseline_keys - final_keys),
        "new_due_to_k1_reselection_or_dedupe": len(final_keys - baseline_keys),
    }
    comparison = baseline[["setup_id", "net_return", "outcome", "mfe_r", "mae_r"]].merge(
        final[["setup_id", "net_return", "outcome", "mfe_r", "mae_r"]],
        on="setup_id",
        how="outer",
        suffixes=("_baseline", "_final"),
        indicator=True,
    )
    write_csv(comparison, RESULTS / "validation_setup_overlap.csv")

    final_metric = metrics[metrics["arm"].eq("final")].iloc[0]
    final_halves = halfyears[halfyears["arm"].eq("final")]
    full_2025 = final_halves[final_halves["fold"].isin(["2025H1", "2025H2"])]
    success = bool(
        float(final_metric["mean_net_bp"]) > 0.0
        and float(final_metric["matched_control_excess_bp"]) > 0.0
        and float(final_metric["paired_signflip_p_one_sided"]) < 0.01
        and len(full_2025) == 2
        and full_2025["mean_net_bp"].gt(0.0).all()
    )
    summary = {
        "experiment_id": config["experiment_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "frozen_preholdout_validation",
        "source_quality": quality,
        "holdout_rows_read": 0,
        "selection_receipt_sha256": sha256_file(SELECTION_PATH),
        "selected_fee_to_risk_max": selected_fee,
        "selected_protection_trigger_r": selected_protection,
        "baseline_parity": parity,
        "overlap": overlap,
        "success_gate_passed": success,
        "final_metrics": final_metric.to_dict(),
        "production_mutation": false,
    }
    write_json(RESULTS / "validation_summary.json", summary)
    component_plot(metrics, RESULTS / "validation_components.png", "Frozen validation: component and bundle results")
    equity_plot(events_by_arm, RESULTS / "validation_equity.png")
    if len(final):
        failure_plot(final, RESULTS / "validation_failure_map.png")
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("development", "validation"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    RESULTS.mkdir(parents=True, exist_ok=True)
    featured, quality = load_featured(config)
    if args.stage == "development":
        run_development(config, featured, quality)
    else:
        run_validation(config, featured, quality)


if __name__ == "__main__":
    main()
