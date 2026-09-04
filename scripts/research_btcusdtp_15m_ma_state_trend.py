#!/usr/bin/env python3
"""Research a causal BTCUSDT.P 15m MA-state launch and trend exit.

Signal columns use completed OHLCV through decision bar ``t`` only.  The
selected SMA/EMA(HL2), Pine/Wilder ATR14, the eight prior completed candles,
and the current candle form three explicit launch families: direct MA cross,
same-bar MA rejection, and near-MA coil release.  Entry is the next bar open.

The experiment changes one categorical factor at a time in a committed order:
MA reference, entry-family union, then exit policy.  The MA-following policies
either wait for one/two completed closes on the wrong MA side and exit at the
next open, or ratchet a stop from the previous completed MA plus an ATR buffer.
Only outcome resolution reads future bars.  Development ends on 2024-12-31;
the 2025--2026-02-28 validation ledger cannot open until the selection receipt,
this script, and the preregistration are committed.  Repository holdout starts
2026-05-04 and is physically absent from the economic source.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    BAR_DELTAS,
    load_featured,
)
from scripts.research_two_key_candle_ma_retest_1h import (
    pine_rma,
    profit_factor,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-ma-state-trend-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BAR = "15m"
BAR_DELTA = BAR_DELTAS[BAR]

TEAL = "#17A297"
ORANGE = "#F59E0B"
RED = "#F23645"
INK = "#26323A"
MUTED = "#73808A"
GRID = "#D9DEE1"


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
    if isinstance(value, pd.Timestamp):
        return utc(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    else:
        frame.to_csv(path, index=False)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def parse_reference(reference: str) -> tuple[str, int]:
    if reference.startswith("SMA"):
        kind = "SMA"
    elif reference.startswith("EMA"):
        kind = "EMA"
    else:
        raise ValueError(f"unsupported reference: {reference}")
    period = int(reference[3:])
    if period < 2:
        raise ValueError("MA period must be at least two")
    return kind, period


def _reference_for_segment(segment: pd.DataFrame, reference: str) -> pd.DataFrame:
    """Add causal reference features within one contiguous segment.

    Inputs are current/prior ``open/high/low/close``, ATR14 and the eight-bar
    trailing window.  SMA/EMA is calculated from HL2; the longest read is the
    reference period plus the fixed prior lookback.  No value after row ``t``
    contributes to any feature at ``t``.
    """

    kind, period = parse_reference(reference)
    out = segment.copy()
    source = (out["high"].astype(float) + out["low"].astype(float)) / 2.0
    if kind == "SMA":
        ma = source.rolling(period, min_periods=period).mean()
    else:
        ma = source.ewm(span=period, adjust=False, min_periods=period).mean()
    atr = out["atr"].astype(float).replace(0.0, np.nan)
    out["reference_ma"] = ma
    out["reference_kind"] = kind
    out["reference_period"] = period
    out["reference_slope_atr_per_bar"] = (ma - ma.shift(4)) / (atr * 4.0)
    distance = (out["close"].astype(float) - ma) / atr
    out["close_minus_ma_atr"] = distance
    out["previous_close_minus_ma_atr"] = distance.shift(1)
    near = distance.abs().le(0.8).shift(1)
    out["prior_near_ma_share"] = near.rolling(8, min_periods=8).mean()
    prior_high = out["high"].shift(1).rolling(8, min_periods=8).max()
    prior_low = out["low"].shift(1).rolling(8, min_periods=8).min()
    out["prior_high_8"] = prior_high
    out["prior_low_8"] = prior_low
    out["prior_range_atr"] = (prior_high - prior_low) / atr
    return out


def add_reference_features(frame: pd.DataFrame, reference: str) -> pd.DataFrame:
    """Add one SMA/EMA reference without allowing features to cross data gaps."""

    required = {"open", "high", "low", "close", "atr", "segment_id"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing feature columns: {missing}")
    parts = [
        _reference_for_segment(part.copy(), reference)
        for _, part in frame.groupby("segment_id", sort=True)
    ]
    return pd.concat(parts, ignore_index=True) if parts else frame.copy()


def _side_masks(
    frame: pd.DataFrame,
    signal: Mapping[str, Any],
    direction: int,
) -> dict[str, pd.Series]:
    atr = frame["atr"].astype(float).replace(0.0, np.nan)
    ma = frame["reference_ma"].astype(float)
    ranges = frame["high"].astype(float) - frame["low"].astype(float)
    signed_close = direction * (frame["close"].astype(float) - ma) / atr
    previous_signed_close = direction * frame["previous_close_minus_ma_atr"].astype(float)
    signed_slope = direction * frame["reference_slope_atr_per_bar"].astype(float)
    signed_body = direction * (
        frame["close"].astype(float) - frame["open"].astype(float)
    ) / atr
    range_atr = ranges / atr
    close_location = (
        (frame["close"].astype(float) - frame["low"].astype(float)) / ranges
        if direction > 0
        else (frame["high"].astype(float) - frame["close"].astype(float)) / ranges
    )
    breakout = direction * (
        frame["close"].astype(float)
        - (frame["prior_high_8"] if direction > 0 else frame["prior_low_8"])
    ) / atr

    direct_cfg = signal["direct"]
    direct = (
        previous_signed_close.le(float(direct_cfg["previous_signed_close_atr_max"]))
        & signed_close.ge(float(direct_cfg["current_signed_close_atr_min"]))
        & signed_body.ge(float(direct_cfg["signed_body_atr_min"]))
        & range_atr.ge(float(direct_cfg["range_atr_min"]))
        & close_location.ge(float(direct_cfg["directional_close_location_min"]))
        & signed_slope.ge(float(direct_cfg["signed_ma_slope_atr_per_bar_min"]))
    )

    reject_cfg = signal["rejection"]
    touched = frame["low"].le(ma) if direction > 0 else frame["high"].ge(ma)
    rejection = (
        touched
        & signed_close.ge(float(reject_cfg["current_signed_close_atr_min"]))
        & signed_body.ge(float(reject_cfg["signed_body_atr_min"]))
        & close_location.ge(float(reject_cfg["directional_close_location_min"]))
        & signed_slope.ge(float(reject_cfg["signed_ma_slope_atr_per_bar_min"]))
    )

    coil_cfg = signal["coil"]
    coil = (
        signed_close.ge(float(coil_cfg["current_signed_close_atr_min"]))
        & breakout.ge(float(coil_cfg["prior_breakout_atr_min"]))
        & signed_body.ge(float(coil_cfg["signed_body_atr_min"]))
        & range_atr.ge(float(coil_cfg["range_atr_min"]))
        & close_location.ge(float(coil_cfg["directional_close_location_min"]))
        & frame["prior_near_ma_share"].ge(float(coil_cfg["prior_near_ma_share_min"]))
        & frame["prior_range_atr"].le(float(coil_cfg["prior_range_atr_max"]))
        & signed_slope.ge(float(coil_cfg["signed_ma_slope_atr_per_bar_min"]))
    )
    finite = np.logical_and.reduce(
        [
            np.isfinite(signed_close),
            np.isfinite(previous_signed_close),
            np.isfinite(signed_slope),
            np.isfinite(signed_body),
            np.isfinite(range_atr),
            np.isfinite(close_location),
        ]
    )
    direct &= finite
    rejection &= finite
    coil &= finite & np.isfinite(breakout)
    return {
        "direct": direct.fillna(False),
        "rejection": rejection.fillna(False),
        "coil": coil.fillna(False),
        "signed_close_atr": signed_close,
        "signed_slope_atr_per_bar": signed_slope,
        "signed_body_atr": signed_body,
        "range_atr": range_atr,
        "close_location": close_location,
        "breakout_atr": breakout,
    }


def build_raw_candidates(
    frame: pd.DataFrame,
    signal: Mapping[str, Any],
    family: str,
) -> pd.DataFrame:
    """Return causal decision bars for one registered entry-family union."""

    allowed = {
        "direct": ("direct",),
        "direct_rejection": ("direct", "rejection"),
        "direct_coil": ("direct", "coil"),
        "all": ("direct", "rejection", "coil"),
    }
    if family not in allowed:
        raise ValueError(f"unknown entry family: {family}")
    parts: list[pd.DataFrame] = []
    for direction in (1, -1):
        side = _side_masks(frame, signal, direction)
        selected_mask = pd.Series(False, index=frame.index)
        for name in allowed[family]:
            selected_mask |= side[name]
        indices = np.flatnonzero(selected_mask.to_numpy(dtype=bool))
        if not len(indices):
            continue
        labels: list[str] = []
        scores: list[float] = []
        for index in indices:
            hit_names = [name for name in ("direct", "rejection", "coil") if bool(side[name].iloc[index])]
            labels.append("+".join(hit_names))
            quality = np.mean(
                [
                    np.clip(float(side["signed_close_atr"].iloc[index]) / 0.75, 0.0, 1.0),
                    np.clip(float(side["signed_body_atr"].iloc[index]) / 1.25, 0.0, 1.0),
                    np.clip(float(side["close_location"].iloc[index]), 0.0, 1.0),
                    np.clip((float(side["signed_slope_atr_per_bar"].iloc[index]) + 0.04) / 0.14, 0.0, 1.0),
                ]
            )
            scores.append(float(quality))
        parts.append(
            pd.DataFrame(
                {
                    "signal_i": indices,
                    "direction": direction,
                    "signal_family": labels,
                    "signal_score": scores,
                    "signal_time": frame.loc[indices, "open_time"].to_numpy(),
                    "signal_atr": frame.loc[indices, "atr"].to_numpy(dtype=float),
                    "signal_ma": frame.loc[indices, "reference_ma"].to_numpy(dtype=float),
                    "signed_close_atr": side["signed_close_atr"].iloc[indices].to_numpy(dtype=float),
                    "signed_slope_atr_per_bar": side["signed_slope_atr_per_bar"].iloc[indices].to_numpy(dtype=float),
                    "signed_body_atr": side["signed_body_atr"].iloc[indices].to_numpy(dtype=float),
                    "breakout_atr": side["breakout_atr"].iloc[indices].to_numpy(dtype=float),
                    "prior_near_ma_share": frame.loc[indices, "prior_near_ma_share"].to_numpy(dtype=float),
                    "prior_range_atr": frame.loc[indices, "prior_range_atr"].to_numpy(dtype=float),
                }
            )
        )
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values(
        ["signal_i", "signal_score", "direction"],
        ascending=[True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def accept_candidates(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    cooldown_bars: int,
) -> pd.DataFrame:
    """Apply causal next-open availability and one global cooldown."""

    if candidates.empty:
        return candidates.copy()
    accepted: list[dict[str, Any]] = []
    last_signal = -10**12
    for signal_i, group in candidates.groupby("signal_i", sort=True):
        signal_i = int(signal_i)
        entry_i = signal_i + 1
        if entry_i >= len(frame) or signal_i - last_signal < cooldown_bars:
            continue
        if (
            int(frame.loc[entry_i, "segment_id"]) != int(frame.loc[signal_i, "segment_id"])
            or frame.loc[entry_i, "open_time"] - frame.loc[signal_i, "open_time"] != BAR_DELTA
        ):
            continue
        row = group.sort_values(
            ["signal_score", "direction"], ascending=[False, False], kind="mergesort"
        ).iloc[0].to_dict()
        direction = int(row["direction"])
        identity = (
            f"BTC-USDT-SWAP|15m|{direction}|"
            f"{utc(row['signal_time']).isoformat()}|{row['signal_family']}"
        )
        row.update(
            {
                "setup_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": float(frame.loc[entry_i, "open"]),
            }
        )
        accepted.append(row)
        last_signal = signal_i
    return pd.DataFrame(accepted).reset_index(drop=True)


def _stop_fill(open_price: float, active_stop: float, direction: int) -> float:
    if direction > 0 and open_price < active_stop:
        return open_price
    if direction < 0 and open_price > active_stop:
        return open_price
    return active_stop


def resolve_trade(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    *,
    policy: str,
    horizon: int,
    hard_stop_atr: float,
    fixed_target_atr: float,
    cost: float,
) -> dict[str, Any]:
    """Resolve one trade; only this outcome function reads bars after entry."""

    valid = {"fixed_5atr", "ma_close_1", "ma_close_2", "ma_trail_0_5", "ma_trail_1_0"}
    if policy not in valid:
        raise ValueError(f"unknown exit policy: {policy}")
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    hard_stop = entry - direction * hard_stop_atr * signal_atr
    target = entry + direction * fixed_target_atr * signal_atr
    active_stop = hard_stop
    wrong_closes = 0
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    stop_source = "hard"
    full_mfe = 0.0
    full_mae = 0.0
    exit_mfe = 0.0
    exit_mae = 0.0
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    entry_segment = int(frame.loc[entry_i, "segment_id"])
    if int(frame.loc[end_i, "segment_id"]) != entry_segment:
        return {"resolved": False, "reason": "horizon_crosses_gap"}

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
        exit_mfe = max(exit_mfe, favourable)
        exit_mae = max(exit_mae, adverse)
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

        if policy.startswith("ma_close_"):
            threshold = 1 if policy == "ma_close_1" else 2
            wrong = direction * (close - float(frame.loc[i, "reference_ma"])) <= 0.0
            wrong_closes = wrong_closes + 1 if wrong else 0
            if wrong_closes >= threshold and i + 1 <= end_i:
                exit_i = i + 1
                exit_price = float(frame.loc[i + 1, "open"])
                outcome = policy
                continue
        elif policy.startswith("ma_trail_"):
            buffer = 0.5 if policy == "ma_trail_0_5" else 1.0
            candidate = float(frame.loc[i, "reference_ma"]) - direction * buffer * float(
                frame.loc[i, "atr"]
            )
            if direction > 0 and candidate > active_stop:
                active_stop = candidate
                stop_source = policy
            elif direction < 0 and candidate < active_stop:
                active_stop = candidate
                stop_source = policy

    if exit_i is None:
        exit_i = end_i
        exit_price = float(frame.loc[end_i, "close"])
        outcome = "timeout"
    gross = direction * (float(exit_price) / entry - 1.0)
    risk_fraction = hard_stop_atr * signal_atr / entry
    return {
        "resolved": True,
        "exit_policy": policy,
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
        "exit_price": float(exit_price),
        "hard_stop_price": hard_stop,
        "final_active_stop": active_stop,
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "net_return": gross - cost,
        "risk_fraction": risk_fraction,
        "return_r": gross / risk_fraction if risk_fraction > 0 else np.nan,
        "net_return_r": (gross - cost) / risk_fraction if risk_fraction > 0 else np.nan,
        "mfe_at_exit_atr": exit_mfe / signal_atr,
        "mae_at_exit_atr": exit_mae / signal_atr,
        "horizon_mfe_atr": full_mfe / signal_atr,
        "horizon_mae_atr": full_mae / signal_atr,
        "capture_of_horizon_mfe": gross * entry / full_mfe if full_mfe > 0 else np.nan,
        "gave_back_atr": (full_mfe - direction * (float(exit_price) - entry)) / signal_atr,
    }


def resolve_period(
    accepted: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    policy: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if accepted.empty:
        return accepted.copy()
    execution = config["execution"]
    horizon = int(execution["horizon_bars"])
    selected: list[dict[str, Any]] = []
    for event in accepted.to_dict("records"):
        entry_i = int(event["entry_i"])
        last_i = entry_i + horizon - 1
        if not (start <= utc(event["entry_time"]) < end) or last_i >= len(frame):
            continue
        if frame.loc[last_i, "open_time"] + BAR_DELTA > end:
            continue
        result = resolve_trade(
            frame,
            event,
            policy=policy,
            horizon=horizon,
            hard_stop_atr=float(execution["initial_disaster_stop_atr"]),
            fixed_target_atr=float(execution["fixed_target_atr"]),
            cost=float(execution["round_trip_cost_fraction"]),
        )
        if result.get("resolved"):
            selected.append({**event, **result})
    return pd.DataFrame(selected)


def equity_metrics(values: Iterable[float]) -> tuple[float, float]:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return np.nan, np.nan
    equity = np.cumprod(1.0 + 0.01 * array)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))[:-1]
    drawdowns = equity / peaks - 1.0
    return float(equity[-1] - 1.0), float(drawdowns.min())


def metrics(events: pd.DataFrame) -> dict[str, Any]:
    if events.empty:
        return {
            "events": 0,
            "mean_gross_bp": np.nan,
            "mean_net_bp": np.nan,
            "median_net_bp": np.nan,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "equal_risk_1pct_return": np.nan,
            "max_drawdown": np.nan,
            "median_hold_bars": np.nan,
            "p90_net_bp": np.nan,
            "max_net_bp": np.nan,
            "mean_capture_of_horizon_mfe": np.nan,
        }
    equal_return, drawdown = equity_metrics(events["net_return_r"])
    captures = events.loc[
        events["horizon_mfe_atr"].gt(0.0) & events["gross_return"].gt(0.0),
        "capture_of_horizon_mfe",
    ]
    return {
        "events": int(len(events)),
        "mean_gross_bp": float(events["gross_return"].mean() * 1e4),
        "mean_net_bp": float(events["net_return"].mean() * 1e4),
        "median_net_bp": float(events["net_return"].median() * 1e4),
        "win_rate": float(events["net_return"].gt(0.0).mean()),
        "profit_factor": float(profit_factor(events["net_return"])),
        "equal_risk_1pct_return": equal_return,
        "max_drawdown": drawdown,
        "median_hold_bars": float(events["hold_bars"].median()),
        "p90_net_bp": float(events["net_return"].quantile(0.90) * 1e4),
        "max_net_bp": float(events["net_return"].max() * 1e4),
        "mean_capture_of_horizon_mfe": float(captures.mean()) if len(captures) else np.nan,
    }


def fold_label(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    if stamp.year == 2026:
        return "2026P1"
    return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"


def fold_metrics(events: pd.DataFrame, folds: list[str]) -> pd.DataFrame:
    labels = events["entry_time"].map(fold_label) if len(events) else pd.Series(dtype=str)
    return pd.DataFrame(
        [
            {
                "fold": fold,
                **metrics(events.loc[labels.eq(fold)].copy() if len(events) else events.copy()),
            }
            for fold in folds
        ]
    )


def robust_metrics(
    events: pd.DataFrame,
    folds: list[str],
    *,
    minimum_total: int,
    minimum_per_fold: int,
) -> dict[str, Any]:
    table = fold_metrics(events, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(means) and np.isfinite(means).all())
    return {
        **metrics(events),
        "minimum_fold_events": int(counts.min()) if len(counts) else 0,
        "eligible": bool(
            len(events) >= minimum_total
            and len(counts)
            and np.all(counts >= minimum_per_fold)
            and finite
        ),
        "robust_score_bp": float(np.median(means) - 0.5 * np.std(means, ddof=0))
        if finite
        else np.nan,
        "worst_fold_net_bp": float(np.min(means)) if finite else np.nan,
        **{f"{row.fold}_events": int(row.events) for row in table.itertuples(index=False)},
        **{
            f"{row.fold}_mean_net_bp": float(row.mean_net_bp)
            for row in table.itertuples(index=False)
        },
    }


def evaluate_arm(
    base: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = add_reference_features(base, params["ma_reference"])
    raw = build_raw_candidates(frame, config["causal_signal"], params["entry_family"])
    accepted = accept_candidates(
        raw,
        frame,
        cooldown_bars=int(config["causal_signal"]["cooldown_bars"]),
    )
    events = resolve_period(
        accepted,
        frame,
        config,
        policy=params["exit_policy"],
        start=start,
        end=end,
    )
    return frame, events


def choose_factor(
    rows: list[dict[str, Any]],
    incumbent: dict[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    selection = config["coordinate_pass"]
    eligible = [row for row in rows if bool(row["eligible"])]
    if not eligible:
        return None, "retain_no_sample_eligible_arm"
    incumbent_score = float(incumbent.get("robust_score_bp", np.nan))
    incumbent_worst = float(incumbent.get("worst_fold_net_bp", np.nan))
    if not np.isfinite(incumbent_score) or not np.isfinite(incumbent_worst):
        return None, "retain_incumbent_not_comparable"
    passing = [
        row
        for row in eligible
        if float(row["robust_score_bp"])
        >= incumbent_score + float(selection["minimum_robust_improvement_bp"])
        and float(row["worst_fold_net_bp"])
        >= incumbent_worst - float(selection["maximum_worst_fold_degradation_bp"])
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


def development_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    base, quality = load_featured(config, BAR)
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    selection = config["coordinate_pass"]
    params = deepcopy(selection["initial"])
    cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}

    def evaluate(current: Mapping[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        key = json.dumps(dict(current), sort_keys=True)
        if key not in cache:
            frame, events = evaluate_arm(base, config, current, start, end)
            result = robust_metrics(
                events,
                folds,
                minimum_total=int(selection["minimum_events_total"]),
                minimum_per_fold=int(selection["minimum_events_per_fold"]),
            )
            cache[key] = frame, events, result
        return cache[key]

    _, initial_events, initial_metrics = evaluate(params)
    trace: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for step, factor_row in enumerate(selection["ordered_factors"], 1):
        factor = str(factor_row["factor"])
        _, _, incumbent_metrics = evaluate(params)
        factor_rows: list[dict[str, Any]] = []
        for grid_index, value in enumerate(factor_row["values"]):
            arm = deepcopy(params)
            arm[factor] = str(value)
            _, events, result = evaluate(arm)
            row = {
                "step": step,
                "factor": factor,
                "value": str(value),
                "grid_index": grid_index,
                **result,
            }
            factor_rows.append(row)
            trace.append(row)
        chosen, reason = choose_factor(factor_rows, incumbent_metrics, config)
        before = deepcopy(params)
        if chosen is not None:
            params[factor] = str(chosen["value"])
        _, selected_events, selected_metrics = evaluate(params)
        steps.append(
            {
                "step": step,
                "factor": factor,
                "before": before,
                "after": deepcopy(params),
                "reason": reason,
                "incumbent_metrics": incumbent_metrics,
                "selected_metrics": selected_metrics,
            }
        )
        print(
            f"{factor}: {reason}; {before[factor]} -> {params[factor]}; "
            f"robust={selected_metrics['robust_score_bp']:.2f}bp n={selected_metrics['events']}",
            flush=True,
        )

    selected_frame, selected_events, selected_metrics = evaluate(params)
    write_csv(pd.DataFrame(trace), RESULTS / "development_selection_trace.csv")
    write_csv(initial_events, RESULTS / "development_initial_trades.csv.gz")
    write_csv(selected_events, RESULTS / "development_selected_trades.csv.gz")
    write_csv(fold_metrics(selected_events, folds), RESULTS / "development_selected_folds.csv")
    receipt = {
        "phase": "development_complete_validation_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "source": quality,
        "holdout_rows_read": 0,
        "initial_params": selection["initial"],
        "initial_metrics": initial_metrics,
        "selected_params": params,
        "selected_metrics": selected_metrics,
        "steps": steps,
        "selected_reference_rows": len(selected_frame),
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


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
        raise RuntimeError(f"selection/config/script must be committed: {dirty}")
    if selection.get("phase") != "development_complete_validation_unopened":
        raise RuntimeError("selection phase drift")
    if selection.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("selection config SHA drift")
    if selection.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("selection script SHA drift")


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
    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    horizon = int(config["execution"]["horizon_bars"])
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
            and np.isfinite(float(frame.loc[signal_i, "reference_ma"]))
        )
    excluded = np.zeros(len(frame), dtype=bool)
    radius = int(config["matched_control"]["exclude_radius_bars"])
    for signal_i in events["signal_i"].astype(int):
        excluded[max(0, signal_i - radius) : min(len(frame), signal_i + radius + 1)] = True
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    for i in np.flatnonzero(eligible & ~excluded & (buckets >= 0)):
        pool.setdefault((str(months[i]), int(blocks[i]), int(buckets[i])), []).append(int(i))
    required = int(config["matched_control"]["controls_per_event"])
    seed = str(config["matched_control"]["seed"])
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
            result = resolve_trade(
                frame,
                control_event,
                policy=policy,
                horizon=horizon,
                hard_stop_atr=float(config["execution"]["initial_disaster_stop_atr"]),
                fixed_target_atr=float(config["execution"]["fixed_target_atr"]),
                cost=float(config["execution"]["round_trip_cost_fraction"]),
            )
            if not result.get("resolved"):
                continue
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
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        if float(event["net_return"]) > 0.0:
            category = (
                "winner_large_giveback"
                if float(event["gave_back_atr"]) >= 2.0
                else "winner_retained"
            )
        elif str(event["outcome"]) == "hard_stop" and float(event["mfe_at_exit_atr"]) < 0.5:
            category = "false_launch_early_hard_stop"
        elif str(event["outcome"]) == "hard_stop" and float(event["horizon_mfe_atr"]) >= 2.0:
            category = "stopped_then_direction_recovered"
        elif str(event["outcome"]).startswith("ma_"):
            category = "ma_whipsaw_or_reversal"
        elif str(event["outcome"]) == "timeout":
            category = "timeout_negative"
        else:
            category = "other_loss"
        rows.append({**event, "failure_category": category})
    return pd.DataFrame(rows)


def validation_phase(config: dict[str, Any]) -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_selection_committed(selection)
    base, quality = load_featured(config, BAR)
    start = utc(config["window"]["validation_start_inclusive"])
    end = utc(config["window"]["validation_end_exclusive"])
    params = selection["selected_params"]
    frame, events = evaluate_arm(base, config, params, start, end)
    controls, pairs = matched_controls(
        events,
        frame,
        config,
        policy=params["exit_policy"],
        start=start,
        end=end,
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    result = {
        **metrics(events),
        "matched_events": len(matched),
        "matched_control_excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
        "paired_signflip_p_one_sided": float(
            signflip_p(excess, resamples=100_000, seed=20260904)
        )
        if len(excess)
        else np.nan,
    }
    assignments = _assignment_metrics(controls)
    slice_table = fold_metrics(events, list(config["window"]["validation_slices"]))
    complete_slice_positive = all(
        int(row.events) < 12 or float(row.mean_net_bp) > 0.0
        for row in slice_table.itertuples(index=False)
    )
    gates = {
        "mean_net_positive": bool(float(result["mean_net_bp"]) > 0.0),
        "all_control_assignments_beaten": bool(
            len(assignments) == int(config["matched_control"]["controls_per_event"])
            and all(
                float(result["mean_net_bp"]) > float(row["mean_net_bp"])
                for row in assignments
            )
        ),
        "paired_p_lt_0_01": bool(
            np.isfinite(float(result["paired_signflip_p_one_sided"]))
            and float(result["paired_signflip_p_one_sided"]) < 0.01
        ),
        "complete_slices_positive": complete_slice_positive,
    }
    gates["all_pass"] = all(gates.values())
    mechanics = failure_mechanics(events)
    write_csv(events, RESULTS / "validation_trades.csv.gz")
    write_csv(controls, RESULTS / "validation_controls.csv.gz")
    write_csv(pairs, RESULTS / "validation_control_pairs.csv")
    write_csv(slice_table, RESULTS / "validation_slices.csv")
    write_csv(mechanics, RESULTS / "validation_failure_mechanics.csv.gz")
    write_json(
        RESULTS / "validation_summary.json",
        {
            "phase": "frozen_validation_complete",
            "selected_params": params,
            "source": quality,
            "metrics": result,
            "control_assignments": assignments,
            "gates": gates,
            "holdout_rows_read": 0,
        },
    )
    print(json.dumps(json_value({"params": params, "metrics": result, "gates": gates}), ensure_ascii=False, indent=2))


def _read_owner_window(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            stamp = utc(raw["open_time"])
            if stamp < start or stamp > end:
                continue
            rows.append(
                {
                    "open_time": stamp,
                    **{
                        column: float(raw[column])
                        for column in ("open", "high", "low", "close", "volume")
                    },
                }
            )
    return pd.DataFrame(rows).sort_values("open_time", kind="mergesort").drop_duplicates(
        "open_time", keep="last"
    )


def owner_diagnostic_phase(config: dict[str, Any]) -> None:
    """Inspect only the exact Owner-provided holdout chart windows."""

    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    params = selection["selected_params"]
    rows: list[dict[str, Any]] = []
    chart_frames: dict[str, pd.DataFrame] = {}
    for example in config["owner_examples"]["rows"]:
        anchor = utc(example["anchor_time_utc"])
        start = anchor - pd.Timedelta(hours=48)
        end = anchor + pd.Timedelta(hours=12)
        sources = {
            ROOT / str(example["source"]),
            ROOT / "data/kline_fetched/okx_BTC_USDT_SWAP_15m_42007.csv",
            ROOT
            / "analysis/output/owner_short_gold_center_recent15d_top10_20260821"
            / "kline_snapshot/BTC_USDT_SWAP.csv",
        }
        pieces = [
            _read_owner_window(source, start, end)
            for source in sorted(sources)
            if source.exists()
        ]
        raw = (
            pd.concat([piece for piece in pieces if len(piece)], ignore_index=True)
            .sort_values("open_time", kind="mergesort")
            .drop_duplicates("open_time", keep="last")
            .reset_index(drop=True)
            if any(len(piece) for piece in pieces)
            else pd.DataFrame()
        )
        if raw.empty:
            raise RuntimeError(f"owner window missing: {example['id']}")
        raw["segment_id"] = raw["open_time"].diff().ne(BAR_DELTA).cumsum().astype(int)
        previous_close = raw["close"].shift(1)
        true_range = np.maximum(
            raw["high"] - raw["low"],
            np.maximum((raw["high"] - previous_close).abs(), (raw["low"] - previous_close).abs()),
        )
        raw["atr"] = pine_rma(true_range.to_numpy(dtype=float), 14)
        frame = add_reference_features(raw, params["ma_reference"])
        candidates = build_raw_candidates(frame, config["causal_signal"], "all")
        accepted = accept_candidates(
            candidates,
            frame,
            cooldown_bars=int(config["causal_signal"]["cooldown_bars"]),
        )
        anchor_i = int(frame.index[frame["open_time"].eq(anchor)][0])
        direction = 1 if example["direction"] == "LONG" else -1
        same_side = (
            accepted[accepted["direction"].eq(direction)].copy()
            if "direction" in accepted.columns
            else pd.DataFrame(columns=["signal_i", "signal_time", "signal_family"])
        )
        same_side["offset_bars"] = same_side["signal_i"].astype(int) - anchor_i
        visible = same_side[same_side["offset_bars"].between(-8, 24)].copy()
        if len(visible):
            nearest = visible.iloc[(visible["offset_bars"].abs()).argsort(kind="mergesort")].iloc[0]
            nearest_time = utc(nearest["signal_time"])
            nearest_offset: int | None = int(nearest["offset_bars"])
            nearest_family: str | None = str(nearest["signal_family"])
        else:
            nearest_time = None
            nearest_offset = None
            nearest_family = None
        rows.append(
            {
                "example_id": example["id"],
                "direction": example["direction"],
                "anchor_time_utc": anchor,
                "selected_reference": params["ma_reference"],
                "nearest_signal_time_utc": nearest_time,
                "nearest_signal_offset_bars": nearest_offset,
                "nearest_signal_family": nearest_family,
                "descriptive_match": nearest_offset is not None,
                "holdout_use_for_configuration": int(
                    config["owner_examples"]["holdout_use_for_this_configuration"]
                ),
            }
        )
        frame["example_anchor"] = frame.index == anchor_i
        frame["example_direction"] = direction
        frame["example_signal"] = False
        if len(visible):
            frame.loc[visible["signal_i"].astype(int), "example_signal"] = True
        chart_frames[str(example["id"])] = frame
    output = pd.DataFrame(rows)
    write_csv(output, RESULTS / "owner_examples_diagnostic.csv")
    render_owner_examples(chart_frames, output, RESULTS / "owner_examples_selected_reference.png")
    write_json(
        RESULTS / "owner_examples_receipt.json",
        {
            "role": config["owner_examples"]["role"],
            "holdout_use_for_this_configuration": config["owner_examples"]["holdout_use_for_this_configuration"],
            "owner_examples": len(output),
            "descriptive_matches": int(output["descriptive_match"].sum()),
            "economic_selection_use": False,
            "generalization_claim": False,
            "chart": str((RESULTS / "owner_examples_selected_reference.png").relative_to(ROOT)),
        },
    )
    print(output.to_string(index=False))


def render_owner_examples(
    frames: Mapping[str, pd.DataFrame],
    diagnostics: pd.DataFrame,
    path: Path,
) -> None:
    fig, axes = plt.subplots(len(frames), 1, figsize=(13, 12), constrained_layout=True)
    lookup = diagnostics.set_index("example_id")
    for ax, (example_id, frame) in zip(np.atleast_1d(axes), frames.items()):
        row = lookup.loc[example_id]
        anchor = utc(row["anchor_time_utc"])
        window = frame[frame["open_time"].between(anchor - pd.Timedelta(hours=8), anchor + pd.Timedelta(hours=8))].copy()
        x = np.arange(len(window))
        colours = np.where(window["close"].ge(window["reference_ma"]), TEAL, ORANGE)
        for j, (_, candle) in enumerate(window.iterrows()):
            ax.vlines(j, candle["low"], candle["high"], color=colours[j], lw=0.8)
            bottom = min(candle["open"], candle["close"])
            height = max(abs(candle["close"] - candle["open"]), 0.01)
            ax.add_patch(plt.Rectangle((j - 0.3, bottom), 0.6, height, color=colours[j], alpha=0.95))
        ax.plot(x, window["reference_ma"], color=INK, lw=1.0, label=str(row["selected_reference"]))
        anchor_positions = np.flatnonzero(window["open_time"].eq(anchor).to_numpy())
        if len(anchor_positions):
            ax.axvline(anchor_positions[0], color=MUTED, lw=0.8, ls="--")
        for j in np.flatnonzero(window["example_signal"].to_numpy(dtype=bool)):
            direction = int(window.iloc[j]["example_direction"])
            y = window.iloc[j]["low"] if direction > 0 else window.iloc[j]["high"]
            ax.scatter(j, y, marker="^" if direction > 0 else "v", s=45, color=TEAL if direction > 0 else ORANGE, zorder=5)
        ax.set_title(
            f"{example_id} · {row['direction']} · nearest signal offset {row['nearest_signal_offset_bars']} bars",
            loc="left",
            fontsize=10,
        )
        ax.grid(color=GRID, alpha=0.35, lw=0.5)
        ax.set_xticks([])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def validation_plots() -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    trace = pd.read_csv(RESULTS / "development_selection_trace.csv")
    events = pd.read_csv(RESULTS / "validation_trades.csv.gz", parse_dates=["entry_time"])
    slices = pd.read_csv(RESULTS / "validation_slices.csv")
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    selected_values = {
        step["factor"]: step["after"][step["factor"]] for step in selection["steps"]
    }
    for factor, group in trace.groupby("factor", sort=False):
        axes[0].plot(group["value"], group["robust_score_bp"], marker="o", lw=1.0, label=factor)
    axes[0].axhline(0.0, color=INK, lw=0.8)
    axes[0].tick_params(axis="x", rotation=60, labelsize=7)
    axes[0].set_title("Development one-factor traces")
    axes[0].set_ylabel("robust net bp / trade")
    axes[0].legend(frameon=False, fontsize=8)
    axes[1].bar(slices["fold"], slices["mean_net_bp"], color=np.where(slices["mean_net_bp"].ge(0), TEAL, RED))
    axes[1].axhline(0.0, color=INK, lw=0.8)
    axes[1].set_title("Frozen validation by time slice")
    axes[1].set_ylabel("net bp / trade")
    ordered = events.sort_values("entry_time", kind="mergesort")
    equity = (1.0 + 0.01 * ordered["net_return_r"].astype(float)).cumprod() - 1.0
    axes[2].plot(ordered["entry_time"], equity * 100.0, color=TEAL, lw=1.2)
    axes[2].axhline(0.0, color=INK, lw=0.8)
    axes[2].set_title("Frozen validation · 1% equal-risk equity")
    axes[2].set_ylabel("return, %")
    for ax in axes:
        ax.grid(color=GRID, alpha=0.4, lw=0.5)
    fig.suptitle(
        "BTCUSDT.P 15m MA-state trend system · "
        + " / ".join(f"{key}={value}" for key, value in selected_values.items()),
        fontsize=12,
    )
    fig.savefig(RESULTS / "validation_overview.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def verify_phase(config: dict[str, Any]) -> None:
    required = [
        SELECTION_PATH,
        RESULTS / "validation_summary.json",
        RESULTS / "validation_trades.csv.gz",
        RESULTS / "validation_controls.csv.gz",
        RESULTS / "validation_control_pairs.csv",
        RESULTS / "validation_slices.csv",
        RESULTS / "owner_examples_diagnostic.csv",
        RESULTS / "owner_examples_receipt.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"missing verification artifacts: {missing}")
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    summary = json.loads((RESULTS / "validation_summary.json").read_text(encoding="utf-8"))
    owner = json.loads((RESULTS / "owner_examples_receipt.json").read_text(encoding="utf-8"))
    checks = {
        "config_hash_matches": selection["config_sha256"] == sha256_file(CONFIG_PATH),
        "script_hash_matches": selection["script_sha256"] == sha256_file(SCRIPT_PATH),
        "economic_holdout_rows_zero": int(summary["holdout_rows_read"]) == 0,
        "validation_params_frozen": summary["selected_params"] == selection["selected_params"],
        "owner_examples_not_economic_selection": owner["economic_selection_use"] is False,
        "owner_examples_no_generalization_claim": owner["generalization_claim"] is False,
        "owner_holdout_use_recorded": int(owner["holdout_use_for_this_configuration"]) == 1,
        "production_false": config["gates"]["production_eligible"] is False,
        "automatic_promote_false": config["gates"]["automatic_promote"] is False,
    }
    validation_plots()
    checks["validation_overview_exists"] = (RESULTS / "validation_overview.png").exists()
    payload = {
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts": {
            str(path.relative_to(ROOT)): {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in [*required, RESULTS / "validation_overview.png", RESULTS / "owner_examples_selected_reference.png"]
        },
    }
    write_json(RESULTS / "verification.json", payload)
    if not payload["passed"]:
        raise RuntimeError(f"verification failed: {checks}")
    print(json.dumps(json_value(payload), ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("development", "validation", "owner-diagnostic", "verify"),
    )
    args = parser.parse_args()
    config = load_config()
    if args.phase == "development":
        development_phase(config)
    elif args.phase == "validation":
        validation_phase(config)
    elif args.phase == "owner-diagnostic":
        owner_diagnostic_phase(config)
    else:
        verify_phase(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
