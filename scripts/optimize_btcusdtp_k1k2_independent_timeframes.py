#!/usr/bin/env python3
"""Research independent BTCUSDT.P 15m and 5m K1->K2 systems.

All signal features are causal. They use completed OHLCV through K2 only:
ATR14, a timeframe-specific rolling SMA(HL2), K1/K2 candle geometry, the
selected-MA candle colour, and completed bars strictly between K1 and K2.
Entry economics use only the following bar's open. Outcome labels alone read
the frozen 12-clock-hour future path. The physical input ends before the
repository holdout at 2026-05-04.

The core visual semantics stay hard: K1 truly crosses the selected average and
K2 touches it with a wick while the full body remains on the directional side.
Twelve secondary properties form an equal-weight score. 15m and 5m separately
select reference period, gap window, and score floor by one preregistered
coordinate pass. Stop, target, protection, horizon, cooldown, and 20bp cost do
not change in this experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    BAR_DELTAS,
    add_control_metrics,
    build_matched_controls,
    metric_row,
    resolve_exit,
)
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    load_featured as load_legacy_featured,
)
from scripts.research_two_key_candle_ma_retest_1h import hull_ma, sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PAIR_COLUMNS = [
    "direction",
    "ma_period",
    "k1_i",
    "k2_i",
    "gap_bars",
    "k1_body_ratio",
    "k1_range_atr",
    "k1_close_location",
    "k1_cross_depth_atr",
    "k2_wick_share",
    "k2_body_ratio",
    "k2_rejection_close_location",
    "k2_touch_depth_atr",
    "path_close_share",
    "path_colour_share",
    "secondary_score",
    "score_k1_body",
    "score_k1_range",
    "score_k1_close",
    "score_k1_cross",
    "score_k2_wick",
    "score_k2_compact_body",
    "score_k2_rejection_close",
    "score_k2_touch",
    "score_k1_ma_colour",
    "score_k2_ma_colour",
    "score_path_close_share",
    "score_path_colour_share",
]


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


def load_base_frame(
    config: dict[str, Any], bar: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load physical pre-holdout OHLCV and causal base features.

    Columns used are open/high/low/close/volume/open_time. ATR14 and geometry
    are computed within contiguous segments. For 15m, exactly three completed
    contiguous 5m rows are aggregated before any feature is calculated.
    """

    compatibility = deepcopy(config)
    compatibility["window"]["validation_end_exclusive"] = config["window"][
        "audit_end_exclusive"
    ]
    frame, quality = load_legacy_featured(compatibility, bar)
    if int(quality.get("holdout_rows_read", -1)) != 0:
        raise RuntimeError("base loader did not prove zero holdout rows")
    return frame, quality


def with_reference_features(frame: pd.DataFrame, ma_period: int) -> pd.DataFrame:
    """Recompute the selected-MA features without future data.

    Uses high/low at the current and prior ``ma_period - 1`` bars for
    SMA(HL2). MA colour uses current HL2 versus that completed rolling mean.
    The diagnostic oscillator uses the current/prior 999 differences, a
    15-bar lag, and HMA10; it is retained for parity but is not scored.
    Existing ATR14 was calculated from current/prior OHLC only.
    """

    if ma_period < 2:
        raise ValueError("ma_period must be at least 2")
    out = frame.copy()
    hl2 = (out["high"].astype(float) + out["low"].astype(float)) / 2.0
    reference = hl2.rolling(ma_period, min_periods=ma_period).mean()
    out["sma40_hl2"] = reference
    difference = hl2 - reference
    percentile = difference.rolling(1000, min_periods=1000).quantile(
        0.99, interpolation="linear"
    )
    ratio = difference.div(percentile.replace(0.0, np.nan))
    out["ma_shift_osc"] = hull_ma(ratio - ratio.shift(15), 10)
    out["ma_shift_osc_delta"] = out["ma_shift_osc"].diff()
    out["ma_shift_candle_side"] = np.where(hl2.ge(reference), 1, -1)
    out["reference_ma_period"] = int(ma_period)
    return out.replace([np.inf, -np.inf], np.nan)


def clip01(values: np.ndarray | pd.Series) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), 0.0, 1.0)


def build_core_pairs(
    frame: pd.DataFrame,
    *,
    ma_period: int,
    maximum_gap_bars: int,
) -> pd.DataFrame:
    """Enumerate causal K1/K2 pairs that satisfy only the two owner core rules.

    K1 reads open/close/high/low, ATR14, and the selected rolling SMA(HL2) on
    K1. K2 reads the same columns on K2. Path shares read only completed rows
    K1+1 through K2-1. The function never reads K2+1 or any outcome column.
    """

    if maximum_gap_bars < 2:
        raise ValueError("maximum_gap_bars must be at least 2")
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    ma = frame["sma40_hl2"].to_numpy(dtype=float)
    colour = frame["ma_shift_candle_side"].to_numpy(dtype=int)
    segment = frame["segment_id"].to_numpy(dtype=int)
    bar_range = high - low
    body_ratio = np.abs(close - open_) / np.where(bar_range > 0.0, bar_range, np.nan)
    range_atr = bar_range / np.where(atr > 0.0, atr, np.nan)
    body_low = np.minimum(open_, close)
    body_high = np.maximum(open_, close)
    n = len(frame)
    parts: list[pd.DataFrame] = []

    for direction in (1, -1):
        if direction > 0:
            k1_entry = (ma - open_) / atr
            k1_exit = (close - ma) / atr
            k1_close_location = (close - low) / bar_range
            k2_wick_share = (body_low - low) / bar_range
            k2_rejection_close = (close - low) / bar_range
            k2_touch = (ma - low) / atr
            k2_close_side = (close - ma) / atr
            k2_body_side = body_low >= ma
        else:
            k1_entry = (open_ - ma) / atr
            k1_exit = (ma - close) / atr
            k1_close_location = (high - close) / bar_range
            k2_wick_share = (high - body_high) / bar_range
            k2_rejection_close = (high - close) / bar_range
            k2_touch = (high - ma) / atr
            k2_close_side = (ma - close) / atr
            k2_body_side = body_high <= ma
        k1_cross = np.minimum(k1_entry, k1_exit)
        k1_core = (
            (direction * (close - open_) > 0.0)
            & np.isfinite(k1_cross)
            & np.isfinite(body_ratio)
            & np.isfinite(range_atr)
            & np.isfinite(k1_close_location)
            & (k1_entry >= 0.0)
            & (k1_exit >= 0.0)
        )
        k2_core = (
            np.isfinite(k2_wick_share)
            & np.isfinite(k2_rejection_close)
            & np.isfinite(k2_touch)
            & np.isfinite(k2_close_side)
            & np.isfinite(body_ratio)
            & (k2_touch >= 0.0)
            & (k2_close_side >= 0.0)
            & k2_body_side
        )
        correct_close = np.isfinite(ma) & (direction * (close - ma) >= 0.0)
        correct_colour = colour == direction
        close_prefix = np.concatenate(
            ([0], np.cumsum(correct_close.astype(np.int64)))
        )
        colour_prefix = np.concatenate(
            ([0], np.cumsum(correct_colour.astype(np.int64)))
        )

        for gap in range(2, maximum_gap_bars + 1):
            k2_index = np.arange(gap, n, dtype=int)
            k1_index = k2_index - gap
            valid = (
                k1_core[k1_index]
                & k2_core[k2_index]
                & (segment[k1_index] == segment[k2_index])
            )
            if not valid.any():
                continue
            k1_i = k1_index[valid]
            k2_i = k2_index[valid]
            middle_count = float(gap - 1)
            path_close_share = (
                close_prefix[k2_i] - close_prefix[k1_i + 1]
            ) / middle_count
            path_colour_share = (
                colour_prefix[k2_i] - colour_prefix[k1_i + 1]
            ) / middle_count
            components = {
                "score_k1_body": clip01((body_ratio[k1_i] - 0.35) / 0.45),
                "score_k1_range": clip01((range_atr[k1_i] - 0.50) / 1.50),
                "score_k1_close": clip01((k1_close_location[k1_i] - 0.50) / 0.40),
                "score_k1_cross": clip01(k1_cross[k1_i] / 0.50),
                "score_k2_wick": clip01((k2_wick_share[k2_i] - 0.10) / 0.50),
                "score_k2_compact_body": clip01((0.75 - body_ratio[k2_i]) / 0.60),
                "score_k2_rejection_close": clip01(
                    (k2_rejection_close[k2_i] - 0.50) / 0.40
                ),
                "score_k2_touch": clip01(1.0 - k2_touch[k2_i] / 2.00),
                "score_k1_ma_colour": correct_colour[k1_i].astype(float),
                "score_k2_ma_colour": correct_colour[k2_i].astype(float),
                "score_path_close_share": path_close_share,
                "score_path_colour_share": path_colour_share,
            }
            score = np.mean(np.column_stack(list(components.values())), axis=1)
            parts.append(
                pd.DataFrame(
                    {
                        "direction": direction,
                        "ma_period": int(ma_period),
                        "k1_i": k1_i,
                        "k2_i": k2_i,
                        "gap_bars": int(gap),
                        "k1_body_ratio": body_ratio[k1_i],
                        "k1_range_atr": range_atr[k1_i],
                        "k1_close_location": k1_close_location[k1_i],
                        "k1_cross_depth_atr": k1_cross[k1_i],
                        "k2_wick_share": k2_wick_share[k2_i],
                        "k2_body_ratio": body_ratio[k2_i],
                        "k2_rejection_close_location": k2_rejection_close[k2_i],
                        "k2_touch_depth_atr": k2_touch[k2_i],
                        "path_close_share": path_close_share,
                        "path_colour_share": path_colour_share,
                        "secondary_score": score,
                        **components,
                    }
                )
            )
    if not parts:
        return pd.DataFrame(columns=PAIR_COLUMNS)
    return pd.concat(parts, ignore_index=True).sort_values(
        ["k2_i", "direction", "secondary_score", "gap_bars"],
        ascending=[True, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)


def initial_params(config: dict[str, Any], bar: str) -> dict[str, Any]:
    start = config["timeframe_design"][bar]["initial"]
    return {
        "ma_period": int(start["ma_period"]),
        "gap_min_bars": int(start["gap_window"][0]),
        "gap_max_bars": int(start["gap_window"][1]),
        "score_floor": float(start["score_floor"]),
    }


def apply_family(params: dict[str, Any], family: str, value: Any) -> dict[str, Any]:
    output = deepcopy(params)
    if family == "ma_period":
        output["ma_period"] = int(value)
    elif family == "gap_window":
        output["gap_min_bars"] = int(value[0])
        output["gap_max_bars"] = int(value[1])
    elif family == "score_floor":
        output["score_floor"] = float(value)
    else:
        raise ValueError(f"unknown coordinate family: {family}")
    return output


def filter_candidates(universe: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    if universe.empty:
        return universe.copy()
    selected = universe.loc[
        universe["gap_bars"].between(
            int(params["gap_min_bars"]), int(params["gap_max_bars"])
        )
        & universe["secondary_score"].ge(float(params["score_floor"]))
    ].sort_values(
        ["k2_i", "direction", "secondary_score", "gap_bars"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    return selected.drop_duplicates(["k2_i", "direction"], keep="first").reset_index(
        drop=True
    )


def period_candidates(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Keep signals whose entry and complete label horizon stay in one period."""

    if candidates.empty:
        return candidates.copy()
    horizon = int(config["timeframe_fixed"][bar]["horizon_bars"])
    delta = BAR_DELTAS[bar]
    keep: list[bool] = []
    for row in candidates.itertuples(index=False):
        k2_i = int(row.k2_i)
        entry_i = k2_i + 1
        last_i = entry_i + horizon - 1
        valid = bool(
            last_i < len(frame)
            and frame.loc[entry_i, "open_time"] >= start
            and frame.loc[entry_i, "open_time"] < end
            and frame.loc[last_i, "open_time"] + delta <= end
            and int(frame.loc[k2_i, "segment_id"])
            == int(frame.loc[entry_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and frame.loc[entry_i, "open_time"] - frame.loc[k2_i, "open_time"]
            == delta
        )
        keep.append(valid)
    return candidates.loc[keep].copy().reset_index(drop=True)


def apply_execution_gates(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply frozen next-open economics, cooldown and same-K1 reuse.

    Signal fields use data through K2. Entry, stop distance and fee-to-risk use
    only K2+1 open. No later row is read here. Every filtered candidate receives
    one explicit decision reason for funnel diagnosis.
    """

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
            stop = float(
                frame.loc[k2_i, "low"]
                if direction > 0
                else frame.loc[k2_i, "high"]
            )
            risk = direction * (entry - stop)
            atr = float(frame.loc[k2_i, "atr"])
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
            elif last_k1[direction] is not None and int(base["k1_i"]) == last_k1[direction]:
                reason = "same_k1_reuse"
            decision = {
                **base,
                "bar": bar,
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
            decisions.append(decision)
            if reason != "accepted":
                continue
            setup = (
                f"BTC-USDT-SWAP|{bar}|ma{int(params['ma_period'])}|{direction}|"
                f"{frame.loc[k2_i, 'open_time'].isoformat()}|{int(base['k1_i'])}"
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
    universe: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    candidates = period_candidates(
        filter_candidates(universe, params), frame, config, bar, start, end
    )
    accepted, decisions = apply_execution_gates(candidates, frame, config, bar, params)
    if accepted.empty:
        return candidates, decisions, accepted
    outcomes = [
        resolve_exit(frame, row, config, bar) for row in accepted.to_dict("records")
    ]
    events = pd.DataFrame(
        [
            {**event, **outcome}
            for event, outcome in zip(accepted.to_dict("records"), outcomes)
        ]
    )
    return candidates, decisions, events


def development_fold_label(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"


def audit_slice_label(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    if stamp.year == 2026 and stamp.month <= 2:
        return "2026P1"
    return development_fold_label(stamp)


def fold_table(
    events: pd.DataFrame,
    labels: list[str],
    *,
    labeler: Any = development_fold_label,
) -> pd.DataFrame:
    assigned = (
        events["entry_time"].map(labeler) if len(events) else pd.Series(dtype=str)
    )
    return pd.DataFrame(
        [
            {
                "fold": label,
                **metric_row(
                    events.loc[assigned.eq(label)].copy()
                    if len(events)
                    else events.copy()
                ),
            }
            for label in labels
        ]
    )


def robust_metrics(
    events: pd.DataFrame,
    folds: list[str],
    minimum_total: int,
    minimum_per_fold: int,
) -> dict[str, Any]:
    table = fold_table(events, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(means) and np.isfinite(means).all())
    output = {
        **metric_row(events),
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
    }
    for row in table.itertuples(index=False):
        output[f"{row.fold}_events"] = int(row.events)
        output[f"{row.fold}_mean_net_bp"] = float(row.mean_net_bp)
    return output


def parameter_key(params: dict[str, Any]) -> str:
    return json.dumps(json_value(params), sort_keys=True, separators=(",", ":"))


def coordinate_values(config: dict[str, Any], bar: str, family: str) -> list[Any]:
    return list(config["timeframe_design"][bar]["grids"][family])


def initial_family_value(config: dict[str, Any], bar: str, family: str) -> Any:
    start = config["timeframe_design"][bar]["initial"]
    return start[family]


def value_distance(value: Any, reference: Any) -> float:
    if isinstance(value, list):
        return float(sum(abs(float(a) - float(b)) for a, b in zip(value, reference)))
    return abs(float(value) - float(reference))


def select_coordinate(
    rows: list[dict[str, Any]], incumbent: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    eligible = [row for row in rows if bool(row["eligible"])]
    if not eligible:
        return None, "retain_no_sample_eligible_candidate"
    incumbent_score = float(incumbent.get("robust_score_bp", np.nan))
    incumbent_worst = float(incumbent.get("worst_fold_net_bp", np.nan))
    if not np.isfinite(incumbent_score) or not np.isfinite(incumbent_worst):
        return None, "retain_incumbent_has_no_comparable_fold_score"
    passing = [
        row
        for row in eligible
        if float(row["robust_score_bp"]) >= incumbent_score + 2.0
        and float(row["worst_fold_net_bp"]) >= incumbent_worst - 3.0
    ]
    if not passing:
        return None, "retain_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            float(row["distance_from_initial"]),
            str(row["value_json"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def execution_funnel(
    universe: pd.DataFrame,
    candidates: pd.DataFrame,
    decisions: pd.DataFrame,
    events: pd.DataFrame,
) -> dict[str, Any]:
    counts = (
        decisions["decision"].value_counts().sort_index().to_dict()
        if len(decisions)
        else {}
    )
    return {
        "core_pair_rows": len(universe),
        "score_and_gap_candidate_rows": len(candidates),
        "execution_decision_counts": {str(k): int(v) for k, v in counts.items()},
        "resolved_events": len(events),
    }


def development_phase(config: dict[str, Any]) -> None:
    """Run the preregistered coordinate pass without opening the audit window."""

    RESULTS.mkdir(parents=True, exist_ok=True)
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    receipt: dict[str, Any] = {
        "phase": "development_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "holdout_rows_read": 0,
        "audit_rows_read": 0,
        "timeframes": {},
    }
    trace_parts: list[pd.DataFrame] = []
    source_rows: list[dict[str, Any]] = []

    for bar in ("15m", "5m"):
        print(f"[{bar}] loading physical pre-holdout source", flush=True)
        base, quality = load_base_frame(config, bar)
        design = config["timeframe_design"][bar]
        universes: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = {}
        cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}

        def universe_for(
            period: int,
            *,
            _universes: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = universes,
            _base: pd.DataFrame = base,
            _design: dict[str, Any] = design,
        ) -> tuple[pd.DataFrame, pd.DataFrame]:
            if period not in _universes:
                current_frame = with_reference_features(_base, period)
                universe = build_core_pairs(
                    current_frame,
                    ma_period=period,
                    maximum_gap_bars=int(_design["maximum_pair_gap_bars"]),
                )
                _universes[period] = current_frame, universe
            return _universes[period]

        def evaluate(
            params: dict[str, Any],
            *,
            _cache: dict[
                str,
                tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]],
            ] = cache,
            _bar: str = bar,
            _design: dict[str, Any] = design,
        ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
            key = parameter_key(params)
            if key not in _cache:
                frame, universe = universe_for(int(params["ma_period"]))
                candidates, decisions, events = run_arm(
                    universe, frame, config, _bar, params, start, end
                )
                metrics = robust_metrics(
                    events,
                    list(config["window"]["development_folds"]),
                    int(_design["minimum_events_total"]),
                    int(_design["minimum_events_per_development_fold"]),
                )
                _cache[key] = candidates, decisions, events, metrics
            return _cache[key]

        params = initial_params(config, bar)
        _, _, _, initial_metric = evaluate(params)
        steps: list[dict[str, Any]] = []
        trace_rows: list[dict[str, Any]] = []
        best_observed: dict[str, Any] | None = None
        for step_index, family in enumerate(design["coordinate_order"], 1):
            _, _, _, incumbent_metric = evaluate(params)
            current_rows: list[dict[str, Any]] = []
            reference = initial_family_value(config, bar, family)
            for value in coordinate_values(config, bar, family):
                arm_params = apply_family(params, family, value)
                candidates, decisions, _, metrics = evaluate(arm_params)
                row = {
                    "bar": bar,
                    "step": step_index,
                    "family": family,
                    "value_json": json.dumps(value, separators=(",", ":")),
                    "params_json": parameter_key(arm_params),
                    "distance_from_initial": value_distance(value, reference),
                    "candidate_rows": len(candidates),
                    "decision_rows": len(decisions),
                    **metrics,
                }
                current_rows.append(row)
                trace_rows.append(row)
                if best_observed is None or (
                    np.isfinite(float(row["robust_score_bp"]))
                    and float(row["robust_score_bp"])
                    > float(best_observed.get("robust_score_bp", -np.inf))
                ):
                    best_observed = deepcopy(row)
            chosen, reason = select_coordinate(current_rows, incumbent_metric)
            before = deepcopy(params)
            if chosen is not None:
                params = json.loads(str(chosen["params_json"]))
            _, _, _, after_metric = evaluate(params)
            steps.append(
                {
                    "step": step_index,
                    "family": family,
                    "reason": reason,
                    "before": before,
                    "after": deepcopy(params),
                    "incumbent_metrics": incumbent_metric,
                    "selected_metrics": after_metric,
                    "best_family_arm": max(
                        current_rows,
                        key=lambda row: float(row["robust_score_bp"])
                        if np.isfinite(float(row["robust_score_bp"]))
                        else -np.inf,
                    ),
                }
            )
            print(
                f"[{bar}] {family}: {reason}; robust="
                f"{after_metric['robust_score_bp']:.2f}bp n={after_metric['events']}",
                flush=True,
            )

        final_candidates, final_decisions, final_events, final_metric = evaluate(params)
        final_frame, final_universe = universe_for(int(params["ma_period"]))
        prefix = RESULTS / f"development_{bar}"
        write_csv(pd.DataFrame(trace_rows), prefix.with_name(prefix.name + "_trace.csv"))
        write_csv(final_events, prefix.with_name(prefix.name + "_selected_trades.csv.gz"))
        write_csv(
            final_decisions,
            prefix.with_name(prefix.name + "_selected_decisions.csv.gz"),
        )
        write_csv(
            fold_table(final_events, list(config["window"]["development_folds"])),
            prefix.with_name(prefix.name + "_selected_folds.csv"),
        )
        trace_parts.append(pd.DataFrame(trace_rows))
        source_row = {
            **quality,
            "bar": bar,
            "development_start": start,
            "development_end_exclusive": end,
            "holdout_rows_read": 0,
        }
        source_rows.append(source_row)
        development_success = bool(
            bool(final_metric["eligible"])
            and float(final_metric["mean_net_bp"]) > 0.0
            and float(final_metric["robust_score_bp"]) > 0.0
            and float(final_metric["worst_fold_net_bp"]) > -5.0
        )
        receipt["timeframes"][bar] = {
            "source": source_row,
            "initial_params": initial_params(config, bar),
            "initial_metrics": initial_metric,
            "selected_params": params,
            "selected_metrics": final_metric,
            "development_success": development_success,
            "steps": steps,
            "best_observed_trace_row": best_observed,
            "funnel": execution_funnel(
                final_universe, final_candidates, final_decisions, final_events
            ),
            "selected_frame_rows": len(final_frame),
        }
    write_csv(pd.DataFrame(source_rows), RESULTS / "source_receipt.csv")
    write_csv(pd.concat(trace_parts, ignore_index=True), RESULTS / "development_trace.csv")
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt["timeframes"]), ensure_ascii=False, indent=2))


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
        raise RuntimeError("selection receipt phase drift")
    if selection.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("selection receipt config SHA drift")
    if selection.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("selection receipt script SHA drift")


def binary_auc(scores: Iterable[float], labels: Iterable[bool]) -> float:
    score = pd.Series(list(scores), dtype=float)
    target = np.asarray(list(labels), dtype=bool)
    valid = score.notna().to_numpy() & np.isfinite(score.to_numpy())
    score = score.loc[valid].reset_index(drop=True)
    target = target[valid]
    positives = int(target.sum())
    negatives = int((~target).sum())
    if positives == 0 or negatives == 0:
        return float("nan")
    ranks = score.rank(method="average").to_numpy(dtype=float)
    rank_sum = float(ranks[target].sum())
    return (rank_sum - positives * (positives + 1) / 2.0) / (
        positives * negatives
    )


def ranking_permutation_p(
    values: Iterable[float], top_n: int, observed: float, *, seed: int, resamples: int
) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array) or top_n <= 0 or top_n > len(array) or not np.isfinite(observed):
        return float("nan")
    rng = np.random.default_rng(seed)
    exceed = 0
    remaining = int(resamples)
    batch_size = max(1, min(512, 2_000_000 // max(len(array), 1)))
    while remaining:
        batch = min(batch_size, remaining)
        keys = rng.random((batch, len(array)))
        chosen = np.argpartition(keys, top_n - 1, axis=1)[:, :top_n]
        means = array[chosen].mean(axis=1)
        exceed += int(np.count_nonzero(means >= observed - 1e-15))
        remaining -= batch
    return (exceed + 1.0) / (resamples + 1.0)


def ranking_metrics(events: pd.DataFrame, *, resamples: int = 100_000) -> dict[str, Any]:
    if events.empty:
        return {
            "ranking_events": 0,
            "score_auc_net_positive": np.nan,
            "top_decile_events": 0,
            "top_decile_mean_gross_bp": np.nan,
            "top_decile_mean_net_bp": np.nan,
            "top_decile_win_rate": np.nan,
            "ranking_permutation_p_one_sided": np.nan,
            "single_feature_top_decile_mean_net_bp": np.nan,
        }
    ordered = events.sort_values(
        ["secondary_score", "entry_time"], ascending=[False, True], kind="mergesort"
    )
    top_n = max(1, math.ceil(0.10 * len(ordered)))
    top = ordered.head(top_n)
    single = events.sort_values(
        ["k1_range_atr", "entry_time"], ascending=[False, True], kind="mergesort"
    ).head(top_n)
    observed = float(top["net_return"].mean())
    return {
        "ranking_events": len(events),
        "score_auc_net_positive": float(
            binary_auc(events["secondary_score"], events["net_return"].gt(0.0))
        ),
        "top_decile_events": int(top_n),
        "top_decile_mean_gross_bp": float(top["gross_return"].mean() * 1e4),
        "top_decile_mean_net_bp": float(observed * 1e4),
        "top_decile_win_rate": float(top["net_return"].gt(0.0).mean()),
        "ranking_permutation_p_one_sided": float(
            ranking_permutation_p(
                events["net_return"],
                top_n,
                observed,
                seed=20260904,
                resamples=resamples,
            )
        ),
        "single_feature_top_decile_mean_net_bp": float(
            single["net_return"].mean() * 1e4
        ),
    }


def audit_phase(config: dict[str, Any]) -> None:
    """Open the frozen but previously exposed pre-holdout audit window once."""

    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_selection_committed(selection)
    start = utc(config["window"]["audit_start_inclusive"])
    end = utc(config["window"]["audit_end_exclusive"])
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "phase": "exploratory_frozen_audit_complete",
        "audit_window_pristine": False,
        "holdout_rows_read": 0,
        "timeframes": {},
    }
    for bar in ("15m", "5m"):
        print(f"[{bar}] opening frozen exploratory audit", flush=True)
        base, quality = load_base_frame(config, bar)
        params = selection["timeframes"][bar]["selected_params"]
        frame = with_reference_features(base, int(params["ma_period"]))
        universe = build_core_pairs(
            frame,
            ma_period=int(params["ma_period"]),
            maximum_gap_bars=int(
                config["timeframe_design"][bar]["maximum_pair_gap_bars"]
            ),
        )
        candidates, decisions, events = run_arm(
            universe, frame, config, bar, params, start, end
        )
        score_pool_params = {**params, "score_floor": 0.0}
        _, score_pool_decisions, score_pool_events = run_arm(
            universe, frame, config, bar, score_pool_params, start, end
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
            **ranking_metrics(score_pool_events),
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
            and float(metrics["ranking_permutation_p_one_sided"]) < 0.01
            and len(complete_2025) == 2
            and complete_2025["mean_net_bp"].gt(0.0).all()
        )
        row = {
            "bar": bar,
            "arm": "independent_selected",
            "label": f"{bar} independent",
            **metrics,
        }
        rows.append(row)
        write_csv(events, RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        write_csv(decisions, RESULTS / f"audit_{bar}_selected_decisions.csv.gz")
        write_csv(slices, RESULTS / f"audit_{bar}_selected_slices.csv")
        write_csv(score_pool_events, RESULTS / f"audit_{bar}_score_pool_trades.csv.gz")
        write_csv(
            score_pool_decisions,
            RESULTS / f"audit_{bar}_score_pool_decisions.csv.gz",
        )
        write_csv(controls, RESULTS / f"audit_{bar}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"audit_{bar}_matched_pairs.csv")
        write_json(
            RESULTS / f"audit_{bar}_receipt.json",
            {
                "bar": bar,
                "params": params,
                "source": quality,
                "audit_window_pristine": False,
                "holdout_rows_read": 0,
                "success_gate_passed": passed,
                "metrics": metrics,
                "funnel": execution_funnel(
                    universe, candidates, decisions, events
                ),
                "score_pool_funnel": execution_funnel(
                    universe,
                    filter_candidates(universe, score_pool_params),
                    score_pool_decisions,
                    score_pool_events,
                ),
            },
        )
        summary["timeframes"][bar] = {
            "selected_params": params,
            "metrics": metrics,
            "success_gate_passed": passed,
            "slices": slices.to_dict("records"),
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
    audit_end = utc(config["window"]["audit_end_exclusive"])
    holdout_start = utc(config["window"]["holdout_start"])
    if audit_end >= holdout_start:
        raise RuntimeError("configured audit boundary reaches repository holdout")
    if args.phase == "development":
        development_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
