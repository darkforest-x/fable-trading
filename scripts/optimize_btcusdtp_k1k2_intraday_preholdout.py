#!/usr/bin/env python3
"""Select independent BTCUSDT.P 15m and 5m K1->K2 parameters.

Signal features use completed OHLCV through K2 only: Pine/Wilder ATR14,
SMA40(HL2), public MA Shift candle/oscillator state, volume, K1/K2 geometry,
and the completed bars between K1 and K2. Entry and its fee-to-risk gate use
the next bar open. Outcome labels alone use the following frozen 12 clock
hours. No row on or after the repository holdout (2026-05-04) is read.

Development is a preregistered single coordinate pass over 2023--2024. The
selected receipt must be committed before ``--phase validation`` can open the
2025--2026-02-28 validation ledger. The K2-extreme stop, 3R target, 1.5R
fee-cover protection, and 20bp round-trip cost remain fixed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_two_key_candle_ma_retest_1h import (
    add_features,
    direction_columns,
    profit_factor,
    sha256_file,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v2"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BAR_DELTAS = {"15m": pd.Timedelta(minutes=15), "5m": pd.Timedelta(minutes=5)}
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


def source_for_bar(config: dict[str, Any], bar: str) -> tuple[Path, dict[str, Any]]:
    source_config = config["source"]
    audit_path = PROJECT / str(source_config["audit"])
    if not audit_path.exists():
        raise RuntimeError("safe 5m archive source is not materialized")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    contract = audit.get("contract", {})
    if contract.get("bar") != "5m":
        raise RuntimeError(f"5m archive bar contract drift: {contract}")
    if utc(contract.get("max_exclusive")) != utc("2026-03-01T00:00:00Z"):
        raise RuntimeError(f"5m archive boundary drift: {contract}")
    if int(audit.get("holdout_ohlcv_rows_materialized", -1)) != 0:
        raise RuntimeError("5m archive receipt does not prove zero holdout rows")
    source = PROJECT / str(source_config["path"])
    actual = sha256_file(source)
    expected = str(source_config["sha256"])
    if actual != expected or actual != str(audit.get("output_sha256")):
        raise RuntimeError("safe archive output SHA drift")
    return source, {
        "source_sha256": actual,
        "archive_audit": str(audit_path.relative_to(PROJECT)),
        "archive_contract": contract,
        "analysis_bar": bar,
        "derivation": "native 5m" if bar == "5m" else "complete UTC 3x5m aggregation",
    }


def load_featured(
    config: dict[str, Any], bar: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a physically pre-holdout native series and add causal features."""

    source, source_receipt = source_for_bar(config, bar)
    columns = ["open", "high", "low", "close", "volume", "open_time"]
    raw = pd.read_csv(source, usecols=columns)
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
    raw = raw.sort_values("open_time", kind="mergesort").drop_duplicates(
        "open_time", keep="last"
    )
    holdout_start = utc(config["window"]["holdout_start"])
    if raw.empty or raw["open_time"].max() >= holdout_start:
        raise RuntimeError(f"{bar} physical source reaches repository holdout")
    safe_end = utc(config["window"]["validation_end_exclusive"])
    raw = raw[raw["open_time"] < safe_end].copy().reset_index(drop=True)
    native_rows = len(raw)
    incomplete_15m = 0
    if bar == "15m":
        indexed = raw.set_index("open_time")
        indexed["source_time"] = indexed.index
        grouped = indexed.resample("15min", label="left", closed="left", origin="epoch")
        aggregated = grouped.agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            source_rows=("source_time", "size"),
            source_first=("source_time", "min"),
            source_last=("source_time", "max"),
        )
        complete = aggregated["source_rows"].eq(3) & (
            aggregated["source_last"] - aggregated["source_first"]
        ).eq(pd.Timedelta(minutes=10))
        incomplete_15m = int((aggregated["source_rows"].gt(0) & ~complete).sum())
        raw = (
            aggregated.loc[complete, ["open", "high", "low", "close", "volume"]]
            .dropna()
            .reset_index()
        )
    delta = BAR_DELTAS[bar]
    raw["segment_id"] = raw["open_time"].diff().ne(delta).cumsum().astype(int)
    parts: list[pd.DataFrame] = []
    for segment_id, segment in raw.groupby("segment_id", sort=True):
        featured = add_features(segment.drop(columns="segment_id").reset_index(drop=True))
        featured["segment_id"] = int(segment_id)
        parts.append(featured)
    frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    gaps = int(frame["open_time"].diff().ne(delta).sum() - (1 if len(frame) else 0))
    quality = {
        "bar": bar,
        "source_path": str(source.resolve().relative_to(PROJECT)),
        **source_receipt,
        "rows_read": int(len(raw)),
        "native_5m_rows_read": int(native_rows),
        "incomplete_15m_groups_dropped": incomplete_15m,
        "first_time": frame["open_time"].iloc[0] if len(frame) else None,
        "last_time": frame["open_time"].iloc[-1] if len(frame) else None,
        "gap_count": gaps,
        "segments": int(frame["segment_id"].nunique()) if len(frame) else 0,
        "holdout_start": holdout_start,
        "holdout_rows_read": 0,
        "safe_end_exclusive": safe_end,
    }
    return frame, quality


def _broad_limits(config: dict[str, Any], bar: str) -> dict[str, Any]:
    rows = config["coordinate_order_and_grids"]
    limits: dict[str, Any] = {}
    for row in rows:
        family = str(row["family"])
        values = row.get(bar, row.get("values"))
        if family == "gap_window":
            limits["gap_min"] = min(int(value[0]) for value in values)
            limits["gap_max"] = max(int(value[1]) for value in values)
        elif family in {
            "k1_min_body_ratio",
            "k1_min_range_atr",
            "k1_min_directional_close_location",
            "k1_min_sma40_cross_depth_atr",
            "k2_min_rejection_wick_share",
            "k2_min_rejection_close_location",
        }:
            limits[family] = min(float(value) for value in values)
        elif family in {"k2_max_body_ratio", "k2_touch_depth_atr_max"}:
            limits[family] = max(float(value) for value in values)
    return limits


def build_pair_universe(
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
) -> pd.DataFrame:
    """Build every broadly eligible K1/K2 pair using rows at or before K2."""

    limits = _broad_limits(config, bar)
    parts: list[pd.DataFrame] = []
    segment = frame["segment_id"].to_numpy(dtype=int)
    open_ = frame["open"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    sma = frame["sma40_hl2"].to_numpy(dtype=float)
    ma_side = frame["ma_shift_candle_side"].to_numpy(dtype=int)
    n = len(frame)
    for direction in (1, -1):
        side = direction_columns(frame, direction)
        k1_body = side["k1_body_ratio"].to_numpy(dtype=float)
        k1_range = side["k1_range_atr"].to_numpy(dtype=float)
        k1_close = side["k1_close_location"].to_numpy(dtype=float)
        k1_cross = side["k1_sma40_cross_depth_atr"].to_numpy(dtype=float)
        k1_colour = side["k1_ma_colour_aligned"].to_numpy(dtype=bool)
        directional_body = direction * (close - open_) > 0.0
        k1_mask = (
            directional_body
            & k1_colour
            & np.isfinite(k1_body)
            & np.isfinite(k1_range)
            & np.isfinite(k1_close)
            & np.isfinite(k1_cross)
            & (k1_body >= limits["k1_min_body_ratio"])
            & (k1_range >= limits["k1_min_range_atr"])
            & (k1_close >= limits["k1_min_directional_close_location"])
            & (k1_cross >= limits["k1_min_sma40_cross_depth_atr"])
        )
        k2_wick = side["k2_wick_share"].to_numpy(dtype=float)
        k2_body = side["k2_body_ratio"].to_numpy(dtype=float)
        k2_reject = side["k2_rejection_close_location"].to_numpy(dtype=float)
        k2_touch = side["k2_sma40_touch_depth_atr"].to_numpy(dtype=float)
        k2_close_side = side["k2_sma40_close_side_atr"].to_numpy(dtype=float)
        wick_only = (
            np.minimum(open_, close) >= sma
            if direction > 0
            else np.maximum(open_, close) <= sma
        )
        k2_mask = (
            np.isfinite(k2_wick)
            & np.isfinite(k2_body)
            & np.isfinite(k2_reject)
            & np.isfinite(k2_touch)
            & np.isfinite(k2_close_side)
            & (k2_wick >= limits["k2_min_rejection_wick_share"])
            & (k2_body <= limits["k2_max_body_ratio"])
            & (k2_reject >= limits["k2_min_rejection_close_location"])
            & (k2_touch >= 0.0)
            & (k2_touch <= limits["k2_touch_depth_atr_max"])
            & (k2_close_side >= 0.0)
            & wick_only
        )
        wrong_path = (
            ~np.isfinite(sma)
            | (direction * (close - sma) < 0.0)
            | (ma_side != direction)
        )
        prefix = np.concatenate(([0], np.cumsum(wrong_path.astype(np.int64))))
        for gap in range(int(limits["gap_min"]), int(limits["gap_max"]) + 1):
            k2_index = np.arange(gap, n, dtype=int)
            k1_index = k2_index - gap
            middle_bad = prefix[k2_index] - prefix[k1_index + 1]
            valid = (
                k2_mask[k2_index]
                & k1_mask[k1_index]
                & (segment[k2_index] == segment[k1_index])
                & (middle_bad == 0)
            )
            if not valid.any():
                continue
            k2_i = k2_index[valid]
            k1_i = k1_index[valid]
            quality = np.mean(
                np.column_stack(
                    [
                        np.clip(k1_body[k1_i], 0.0, 1.0),
                        np.clip(k1_range[k1_i] / 2.0, 0.0, 1.0),
                        np.clip(k1_close[k1_i], 0.0, 1.0),
                        np.clip((k1_cross[k1_i] + 0.05) / 0.50, 0.0, 1.0),
                    ]
                ),
                axis=1,
            )
            parts.append(
                pd.DataFrame(
                    {
                        "direction": direction,
                        "k1_i": k1_i,
                        "k2_i": k2_i,
                        "gap_bars": gap,
                        "k1_body_ratio": k1_body[k1_i],
                        "k1_range_atr": k1_range[k1_i],
                        "k1_close_location": k1_close[k1_i],
                        "k1_sma40_cross_depth_atr": k1_cross[k1_i],
                        "k1_volume_ratio_20": side["k1_volume_ratio"].to_numpy(dtype=float)[k1_i],
                        "k1_osc_sign_aligned": side["k1_osc_sign_aligned"].to_numpy(dtype=bool)[k1_i],
                        "k2_wick_share": k2_wick[k2_i],
                        "k2_body_ratio": k2_body[k2_i],
                        "k2_rejection_close_location": k2_reject[k2_i],
                        "k2_touch_depth_atr": k2_touch[k2_i],
                        "k2_osc_sign_aligned": side["k2_osc_sign_aligned"].to_numpy(dtype=bool)[k2_i],
                        "k2_osc_accel_aligned": side["k2_osc_accel_aligned"].to_numpy(dtype=bool)[k2_i],
                        "k1_quality": quality,
                    }
                )
            )
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values(
        ["k2_i", "direction", "gap_bars"],
        ascending=[True, False, True],
        kind="mergesort",
    ).reset_index(drop=True)


def initial_params(config: dict[str, Any], bar: str) -> dict[str, Any]:
    params = deepcopy(config["inherited_start"])
    gap = config["timeframe_fixed"][bar]["gap_window_start"]
    params["gap_min_bars"] = int(gap[0])
    params["gap_max_bars"] = int(gap[1])
    return params


def apply_family(params: dict[str, Any], family: str, value: Any) -> dict[str, Any]:
    output = deepcopy(params)
    if family == "gap_window":
        output["gap_min_bars"] = int(value[0])
        output["gap_max_bars"] = int(value[1])
    else:
        output[family] = value
    return output


def filter_pairs(universe: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    if universe.empty:
        return universe.copy()
    mask = (
        universe["gap_bars"].between(
            int(params["gap_min_bars"]), int(params["gap_max_bars"])
        )
        & universe["k1_body_ratio"].ge(float(params["k1_min_body_ratio"]))
        & universe["k1_range_atr"].ge(float(params["k1_min_range_atr"]))
        & universe["k1_close_location"].ge(
            float(params["k1_min_directional_close_location"])
        )
        & universe["k1_sma40_cross_depth_atr"].ge(
            float(params["k1_min_sma40_cross_depth_atr"])
        )
        & universe["k2_wick_share"].ge(float(params["k2_min_rejection_wick_share"]))
        & universe["k2_body_ratio"].le(float(params["k2_max_body_ratio"]))
        & universe["k2_rejection_close_location"].ge(
            float(params["k2_min_rejection_close_location"])
        )
        & universe["k2_touch_depth_atr"].le(float(params["k2_touch_depth_atr_max"]))
    )
    volume_floor = params.get("k1_min_volume_ratio_20")
    if volume_floor is not None:
        mask &= universe["k1_volume_ratio_20"].ge(float(volume_floor))
    oscillator = str(params.get("oscillator_gate", "none"))
    if oscillator == "k1_sign":
        mask &= universe["k1_osc_sign_aligned"].astype(bool)
    elif oscillator == "k2_sign":
        mask &= universe["k2_osc_sign_aligned"].astype(bool)
    elif oscillator == "both_sign":
        mask &= universe["k1_osc_sign_aligned"].astype(bool)
        mask &= universe["k2_osc_sign_aligned"].astype(bool)
    elif oscillator == "k2_sign_and_acceleration":
        mask &= universe["k2_osc_sign_aligned"].astype(bool)
        mask &= universe["k2_osc_accel_aligned"].astype(bool)
    elif oscillator != "none":
        raise ValueError(f"unknown oscillator gate: {oscillator}")
    selected = universe.loc[mask].sort_values(
        ["k2_i", "direction", "k1_quality", "gap_bars"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    return selected.drop_duplicates(["k2_i", "direction"], keep="first").reset_index(
        drop=True
    )


def accept_events(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
) -> pd.DataFrame:
    """Apply next-open risk/economics, global cooldown and K1 reuse."""

    if candidates.empty:
        return candidates.copy()
    fixed = config["timeframe_fixed"][bar]
    execution = config["execution_frozen"]
    cost = float(execution["round_trip_cost_fraction"])
    by_key = {
        (int(row.k2_i), int(row.direction)): row._asdict()
        for row in candidates.itertuples(index=False)
    }
    accepted: list[dict[str, Any]] = []
    last_entry = -10**12
    last_k1: dict[int, int | None] = {1: None, -1: None}
    delta = BAR_DELTAS[bar]
    for k2_i in sorted(candidates["k2_i"].astype(int).unique()):
        entry_i = k2_i + 1
        if entry_i >= len(frame):
            continue
        if (
            int(frame.loc[entry_i, "segment_id"]) != int(frame.loc[k2_i, "segment_id"])
            or frame.loc[entry_i, "open_time"] - frame.loc[k2_i, "open_time"] != delta
        ):
            continue
        for direction in (1, -1):
            base = by_key.get((k2_i, direction))
            if base is None:
                continue
            entry = float(frame.loc[entry_i, "open"])
            stop = float(frame.loc[k2_i, "low"] if direction > 0 else frame.loc[k2_i, "high"])
            risk = direction * (entry - stop)
            atr = float(frame.loc[k2_i, "atr"])
            risk_atr = risk / atr if atr > 0.0 else float("nan")
            risk_fraction = risk / entry if entry > 0.0 else float("nan")
            fee_to_risk = cost / risk_fraction if risk_fraction > 0.0 else float("inf")
            if not (
                np.isfinite(risk_atr)
                and float(execution["next_open_risk_atr_min"])
                <= risk_atr
                <= float(execution["next_open_risk_atr_max"])
                and fee_to_risk <= float(params["fee_to_risk_max"])
            ):
                continue
            if entry_i - last_entry < int(fixed["cooldown_bars"]):
                continue
            if last_k1[direction] is not None and int(base["k1_i"]) == last_k1[direction]:
                continue
            setup = (
                f"BTC-USDT-SWAP|{bar}|{direction}|"
                f"{frame.loc[k2_i, 'open_time'].isoformat()}|{int(base['k1_i'])}"
            )
            row = dict(base)
            row.update(
                {
                    "bar": bar,
                    "setup_id": hashlib.sha256(setup.encode()).hexdigest()[:16],
                    "entry_i": entry_i,
                    "entry_time": frame.loc[entry_i, "open_time"],
                    "entry_price": entry,
                    "stop_price": stop,
                    "risk_price": risk,
                    "risk_fraction": risk_fraction,
                    "stop_distance_atr": risk_atr,
                    "fee_to_risk": fee_to_risk,
                    "target_price": entry
                    + direction * risk * float(execution["target_r"]),
                }
            )
            accepted.append(row)
            last_entry = entry_i
            last_k1[direction] = int(base["k1_i"])
            break
    return pd.DataFrame(accepted).sort_values("entry_i", kind="mergesort").reset_index(
        drop=True
    )


def resolve_exit(
    frame: pd.DataFrame,
    event: dict[str, Any],
    config: dict[str, Any],
    bar: str,
) -> dict[str, Any]:
    """Resolve the frozen target/stop with next-bar causal protection."""

    execution = config["execution_frozen"]
    horizon = int(config["timeframe_fixed"][bar]["horizon_bars"])
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    risk = float(event["risk_price"])
    stop = float(event["stop_price"])
    target = entry + direction * risk * float(execution["target_r"])
    cost = float(execution["round_trip_cost_fraction"])
    trigger = float(execution["profit_protection_trigger_close_r"])
    fee_cover = entry * (1.0 + direction * cost)
    protection_active = False
    protection_armed_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
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
        hit_target = high >= target if direction > 0 else low <= target
        if hit_stop:
            exit_i = i
            exit_price = active_stop
            outcome = (
                "protected_stop_ambiguous"
                if protection_active and hit_target
                else "sl_ambiguous"
                if hit_target
                else "protected_stop"
                if protection_active
                else "sl"
            )
            continue
        if hit_target:
            exit_i = i
            exit_price = target
            outcome = "tp"
            continue
        if not protection_active and direction * (close - entry) / risk >= trigger:
            protection_active = True
            protection_armed_i = i
    if exit_i is None:
        exit_i = entry_i + horizon - 1
        exit_price = float(frame.loc[exit_i, "close"])
        outcome = "timeout"
    gross = direction * (float(exit_price) / entry - 1.0)
    return {
        "resolved": True,
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTAS[bar],
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "net_return": gross - cost,
        "return_r": direction * (float(exit_price) - entry) / risk,
        "net_return_r": (gross - cost) / float(event["risk_fraction"]),
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


def period_events(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    horizon = int(config["timeframe_fixed"][bar]["horizon_bars"])
    keep: list[bool] = []
    for event in events.itertuples(index=False):
        last = int(event.entry_i) + horizon - 1
        valid = bool(
            event.entry_time >= start
            and event.entry_time < end
            and last < len(frame)
            and int(frame.loc[last, "segment_id"])
            == int(frame.loc[int(event.entry_i), "segment_id"])
            and frame.loc[last, "open_time"] + BAR_DELTAS[bar] <= end
        )
        keep.append(valid)
    selected = events.loc[keep].copy().reset_index(drop=True)
    if selected.empty:
        return selected
    outcomes = [
        resolve_exit(frame, row, config, bar) for row in selected.to_dict("records")
    ]
    return pd.DataFrame(
        [{**event, **outcome} for event, outcome in zip(selected.to_dict("records"), outcomes)]
    )


def run_arm(
    universe: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    params: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = filter_pairs(universe, params)
    accepted = accept_events(candidates, frame, config, bar, params)
    return candidates, period_events(accepted, frame, config, bar, start, end)


def halfyear_label(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"


def equity_metrics(values: Iterable[float]) -> tuple[float, float]:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return float("nan"), float("nan")
    equity = np.cumprod(1.0 + 0.01 * array)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))[:-1]
    drawdowns = equity / peaks - 1.0
    return float(equity[-1] - 1.0), float(drawdowns.min())


def metric_row(events: pd.DataFrame) -> dict[str, Any]:
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
            "tp": 0,
            "sl": 0,
            "protected_stop": 0,
            "timeout": 0,
        }
    equal_return, drawdown = equity_metrics(events["net_return_r"])
    outcomes = events["outcome"].astype(str)
    return {
        "events": int(len(events)),
        "mean_gross_bp": float(events["gross_return"].mean() * 1e4),
        "mean_net_bp": float(events["net_return"].mean() * 1e4),
        "median_net_bp": float(events["net_return"].median() * 1e4),
        "win_rate": float(events["net_return"].gt(0.0).mean()),
        "profit_factor": float(profit_factor(events["net_return"])),
        "equal_risk_1pct_return": equal_return,
        "max_drawdown": drawdown,
        "tp": int(outcomes.eq("tp").sum()),
        "sl": int(outcomes.isin(["sl", "sl_ambiguous"]).sum()),
        "protected_stop": int(outcomes.str.startswith("protected_stop").sum()),
        "timeout": int(outcomes.eq("timeout").sum()),
    }


def fold_table(events: pd.DataFrame, folds: list[str]) -> pd.DataFrame:
    labels = events["entry_time"].map(halfyear_label) if len(events) else pd.Series(dtype=str)
    return pd.DataFrame(
        [
            {
                "fold": fold,
                **metric_row(events.loc[labels.eq(fold)].copy() if len(events) else events.copy()),
            }
            for fold in folds
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
    finite = np.isfinite(means).all()
    return {
        **metric_row(events),
        "minimum_fold_events": int(counts.min()) if len(counts) else 0,
        "eligible": bool(
            len(events) >= minimum_total
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


def cache_key(params: dict[str, Any]) -> str:
    return json.dumps(json_value(params), sort_keys=True, separators=(",", ":"))


def grid_values(config: dict[str, Any], bar: str, family_row: dict[str, Any]) -> list[Any]:
    return list(family_row.get(bar, family_row.get("values", [])))


def inherited_value(config: dict[str, Any], bar: str, family: str) -> Any:
    if family == "gap_window":
        return config["timeframe_fixed"][bar]["gap_window_start"]
    return config["inherited_start"][family]


def value_distance(value: Any, reference: Any) -> float:
    if isinstance(value, list):
        return float(sum(abs(float(a) - float(b)) for a, b in zip(value, reference)))
    if value is None or reference is None or isinstance(value, str):
        return 0.0 if value == reference else 1.0
    return abs(float(value) - float(reference))


def select_coordinate(
    rows: list[dict[str, Any]],
    incumbent: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    eligible = [row for row in rows if bool(row["eligible"])]
    if not eligible:
        return None, "retain_no_eligible_candidate"
    incumbent_score = float(incumbent["robust_score_bp"])
    incumbent_worst = float(incumbent["worst_fold_net_bp"])
    if not np.isfinite(incumbent_score) or not np.isfinite(incumbent_worst):
        return None, "retain_incumbent_has_no_comparable_fold_score"
    passing = [
        row
        for row in eligible
        if (
            float(row["robust_score_bp"]) >= incumbent_score + 2.0
            and float(row["worst_fold_net_bp"]) >= incumbent_worst - 3.0
        )
    ]
    if not passing:
        return None, "retain_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            float(row["distance_from_inherited"]),
            str(row["value_json"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def development_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    config_hash = sha256_file(CONFIG_PATH)
    script_hash = sha256_file(SCRIPT_PATH)
    receipt: dict[str, Any] = {
        "phase": "development_complete_validation_unopened",
        "config_sha256": config_hash,
        "script_sha256": script_hash,
        "holdout_rows_read": 0,
        "timeframes": {},
    }
    all_trace: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    for bar in ("15m", "5m"):
        print(f"[{bar}] loading safe source", flush=True)
        frame, quality = load_featured(config, bar)
        universe = build_pair_universe(frame, config, bar)
        print(f"[{bar}] broad pairs={len(universe):,}", flush=True)
        fixed = config["timeframe_fixed"][bar]
        cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}

        def evaluate(params: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
            key = cache_key(params)
            if key not in cache:
                candidates, events = run_arm(
                    universe, frame, config, bar, params, start, end
                )
                metrics = robust_metrics(
                    events,
                    folds,
                    int(fixed["minimum_events_total"]),
                    int(fixed["minimum_events_per_development_fold"]),
                )
                cache[key] = candidates, events, metrics
            return cache[key]

        params = initial_params(config, bar)
        _, initial_events, initial_metric = evaluate(params)
        steps: list[dict[str, Any]] = []
        trace_rows: list[dict[str, Any]] = []
        for step_index, family_row in enumerate(config["coordinate_order_and_grids"], 1):
            family = str(family_row["family"])
            _, _, incumbent_metric = evaluate(params)
            current_rows: list[dict[str, Any]] = []
            reference = inherited_value(config, bar, family)
            for value in grid_values(config, bar, family_row):
                arm_params = apply_family(params, family, value)
                _, _, metrics = evaluate(arm_params)
                row = {
                    "bar": bar,
                    "step": step_index,
                    "family": family,
                    "value_json": json.dumps(value, separators=(",", ":")),
                    "distance_from_inherited": value_distance(value, reference),
                    **metrics,
                }
                current_rows.append(row)
                trace_rows.append(row)
            chosen, reason = select_coordinate(current_rows, incumbent_metric)
            before = deepcopy(params)
            if chosen is not None:
                value = json.loads(str(chosen["value_json"]))
                params = apply_family(params, family, value)
            _, _, after_metric = evaluate(params)
            steps.append(
                {
                    "step": step_index,
                    "family": family,
                    "reason": reason,
                    "before": before,
                    "after": deepcopy(params),
                    "incumbent_metrics": incumbent_metric,
                    "selected_metrics": after_metric,
                }
            )
            print(
                f"[{bar}] {family}: {reason}; score "
                f"{after_metric['robust_score_bp']:.2f}bp, n={after_metric['events']}",
                flush=True,
            )
        final_candidates, final_events, final_metric = evaluate(params)
        prefix = RESULTS / f"development_{bar}"
        write_csv(pd.DataFrame(trace_rows), prefix.with_name(prefix.name + "_selection_trace.csv"))
        write_csv(final_events, prefix.with_name(prefix.name + "_selected_trades.csv.gz"))
        write_csv(
            fold_table(final_events, folds),
            prefix.with_name(prefix.name + "_selected_folds.csv"),
        )
        all_trace.append(pd.DataFrame(trace_rows))
        source_row = {**quality, "broad_pairs": len(universe)}
        sources.append(source_row)
        receipt["timeframes"][bar] = {
            "source": source_row,
            "initial_params": initial_params(config, bar),
            "initial_metrics": initial_metric,
            "selected_params": params,
            "selected_metrics": final_metric,
            "selected_candidate_rows": len(final_candidates),
            "steps": steps,
        }
    write_csv(pd.DataFrame(sources), RESULTS / "source_receipt.csv")
    write_csv(pd.concat(all_trace, ignore_index=True), RESULTS / "development_selection_trace.csv")
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt["timeframes"]), ensure_ascii=False, indent=2))


def assert_selection_committed(selection: dict[str, Any]) -> None:
    relative = str(SELECTION_PATH.relative_to(PROJECT))
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=PROJECT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", relative, str(SCRIPT_PATH.relative_to(PROJECT)), str(CONFIG_PATH.relative_to(PROJECT))],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"selection/config/script must be committed before validation: {dirty}")
    if selection.get("phase") != "development_complete_validation_unopened":
        raise RuntimeError("selection receipt phase drift")
    if selection.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("selection receipt config SHA drift")
    if selection.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("selection receipt script SHA drift")


def atr_quintiles(frame: pd.DataFrame, eligible: np.ndarray) -> np.ndarray:
    buckets = np.full(len(frame), -1, dtype=int)
    helper = pd.DataFrame(
        {
            "i": np.arange(len(frame)),
            "month": frame["open_time"].dt.strftime("%Y-%m"),
            "atr": frame["atr"],
            "eligible": eligible,
        }
    )
    valid = helper[helper["eligible"] & helper["atr"].notna()]
    for _, group in valid.groupby("month", sort=True):
        labels = pd.qcut(
            group["atr"].rank(method="first"),
            q=min(5, len(group)),
            labels=False,
            duplicates="drop",
        ).fillna(0)
        buckets[group["i"].to_numpy(dtype=int)] = labels.to_numpy(dtype=int)
    return buckets


def build_matched_controls(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    inherited_signal_indices: set[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
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
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last, "segment_id"])
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
        pool.setdefault(
            (str(months[index]), int(blocks[index]), int(buckets[index])), []
        ).append(int(index))
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
            outcome = resolve_exit(frame, control_event, config, bar)
            current.append(float(outcome["net_return"]))
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
                    **outcome,
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


def add_control_metrics(metrics: dict[str, Any], pairs: pd.DataFrame) -> dict[str, Any]:
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy() if len(pairs) else pairs
    if matched is None or matched.empty:
        return {
            **metrics,
            "matched_events": 0,
            "matched_control_excess_bp": np.nan,
            "paired_signflip_p_one_sided": np.nan,
        }
    excess = matched["paired_excess_return"].astype(float)
    return {
        **metrics,
        "matched_events": len(matched),
        "matched_control_excess_bp": float(excess.mean() * 1e4),
        "paired_signflip_p_one_sided": float(
            signflip_p(excess, resamples=100_000, seed=20260904)
        ),
    }


def validation_plots(metrics: pd.DataFrame, ledgers: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    x = np.arange(len(metrics))
    axes[0].bar(x, metrics["mean_gross_bp"], color=TEAL, alpha=0.75, label="gross")
    axes[0].bar(x, metrics["mean_net_bp"], color=ORANGE, alpha=0.85, label="net")
    axes[0].axhline(0.0, color=INK, lw=0.8)
    axes[0].set_xticks(x, metrics["label"], rotation=20, ha="right")
    axes[0].set_ylabel("bp / trade")
    axes[0].set_title("Frozen validation expectancy")
    axes[0].legend(frameon=False)
    for label, events in ledgers.items():
        if events.empty:
            continue
        equity = (1.0 + 0.01 * events["net_return_r"].astype(float)).cumprod() - 1.0
        axes[1].plot(events["entry_time"], equity * 100.0, lw=1.25, label=label)
    axes[1].axhline(0.0, color=INK, lw=0.8)
    axes[1].set_title("Equal-risk 1% equity")
    axes[1].set_ylabel("return, %")
    axes[1].legend(frameon=False, fontsize=8)
    for axis in axes:
        axis.grid(axis="y", color=GRID, lw=0.6, alpha=0.8)
        axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(RESULTS / "validation_summary.png", dpi=180)
    plt.close(fig)


def selection_plot(trace: pd.DataFrame) -> None:
    families = list(dict.fromkeys(trace["family"].astype(str)))
    fig, axes = plt.subplots(3, 4, figsize=(14, 9), sharey=False)
    for axis, family in zip(axes.flat, families):
        subset = trace[trace["family"].eq(family)].copy()
        for bar, colour in (("15m", TEAL), ("5m", ORANGE)):
            current = subset[subset["bar"].eq(bar)]
            axis.plot(
                np.arange(len(current)),
                current["robust_score_bp"],
                marker="o",
                ms=3,
                lw=1,
                color=colour,
                label=bar,
            )
        axis.axhline(0.0, color=INK, lw=0.6)
        axis.set_title(family, fontsize=9)
        axis.grid(axis="y", color=GRID, lw=0.5)
    for axis in axes.flat[len(families) :]:
        axis.set_visible(False)
    axes.flat[0].legend(frameon=False)
    fig.suptitle("Development coordinate traces (index follows preregistered grid)")
    fig.tight_layout()
    fig.savefig(RESULTS / "development_selection_trace.png", dpi=180)
    plt.close(fig)


def validation_phase(config: dict[str, Any]) -> None:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_selection_committed(selection)
    start = utc(config["window"]["validation_start_inclusive"])
    end = utc(config["window"]["validation_end_exclusive"])
    rows: list[dict[str, Any]] = []
    ledgers: dict[str, pd.DataFrame] = {}
    all_controls: list[pd.DataFrame] = []
    all_pairs: list[pd.DataFrame] = []
    slices = list(config["window"]["validation_slices"])
    for bar in ("15m", "5m"):
        print(f"[{bar}] opening frozen validation", flush=True)
        frame, quality = load_featured(config, bar)
        universe = build_pair_universe(frame, config, bar)
        inherited = initial_params(config, bar)
        selected = selection["timeframes"][bar]["selected_params"]
        inherited_candidates, inherited_events = run_arm(
            universe, frame, config, bar, inherited, start, end
        )
        selected_candidates, selected_events = run_arm(
            universe, frame, config, bar, selected, start, end
        )
        controls, pairs = build_matched_controls(
            selected_events,
            frame,
            config,
            bar,
            start,
            end,
            set(inherited_candidates["k2_i"].astype(int)) if len(inherited_candidates) else set(),
        )
        for arm, events in (("inherited", inherited_events), ("selected", selected_events)):
            metric = metric_row(events)
            if arm == "selected":
                metric = add_control_metrics(metric, pairs)
            rows.append({"bar": bar, "arm": arm, "label": f"{bar} {arm}", **metric})
            write_csv(events, RESULTS / f"validation_{bar}_{arm}_trades.csv.gz")
            write_csv(
                fold_table(events, slices), RESULTS / f"validation_{bar}_{arm}_slices.csv"
            )
            ledgers[f"{bar} {arm}"] = events
        write_csv(controls, RESULTS / f"validation_{bar}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"validation_{bar}_matched_pairs.csv")
        all_controls.append(controls)
        all_pairs.append(pairs)
        failure = selected_events.copy()
        if len(failure):
            failure["failure_stage"] = np.select(
                [
                    failure["outcome"].astype(str).str.startswith("sl")
                    & failure["mfe_r"].lt(0.5),
                    failure["outcome"].astype(str).str.startswith("sl")
                    & failure["mfe_r"].lt(1.5),
                    failure["outcome"].astype(str).str.startswith("sl"),
                    failure["outcome"].astype(str).str.startswith("protected_stop"),
                    failure["outcome"].eq("timeout") & failure["net_return"].le(0.0),
                ],
                [
                    "SL before 0.5R",
                    "SL after 0.5R before 1.5R",
                    "SL after 1.5R",
                    "protected giveback",
                    "nonpositive timeout",
                ],
                default="profitable/TP",
            )
            summary = (
                failure.groupby("failure_stage", sort=False)
                .agg(events=("setup_id", "size"), mean_net_bp=("net_return", lambda x: x.mean() * 1e4), median_mfe_r=("mfe_r", "median"))
                .reset_index()
            )
        else:
            summary = pd.DataFrame(columns=["failure_stage", "events", "mean_net_bp", "median_mfe_r"])
        write_csv(summary, RESULTS / f"validation_{bar}_failure_summary.csv")
        write_json(
            RESULTS / f"validation_{bar}_receipt.json",
            {
                "bar": bar,
                "source": quality,
                "inherited_params": inherited,
                "selected_params": selected,
                "broad_pairs": len(universe),
                "inherited_candidate_rows": len(inherited_candidates),
                "selected_candidate_rows": len(selected_candidates),
                "holdout_rows_read": 0,
            },
        )
    metrics = pd.DataFrame(rows)
    write_csv(metrics, RESULTS / "validation_metrics.csv")
    validation_plots(metrics, ledgers)
    trace = pd.read_csv(RESULTS / "development_selection_trace.csv")
    selection_plot(trace)
    summary: dict[str, Any] = {
        "phase": "frozen_validation_complete",
        "holdout_rows_read": 0,
        "metrics": rows,
        "success_gate": {},
    }
    for bar in ("15m", "5m"):
        selected_row = next(row for row in rows if row["bar"] == bar and row["arm"] == "selected")
        slice_frame = pd.read_csv(RESULTS / f"validation_{bar}_selected_slices.csv")
        complete_2025 = slice_frame[slice_frame["fold"].isin(["2025H1", "2025H2"])]
        passed = bool(
            float(selected_row["mean_net_bp"]) > 0.0
            and float(selected_row.get("matched_control_excess_bp", np.nan)) > 0.0
            and float(selected_row.get("paired_signflip_p_one_sided", np.nan)) < 0.01
            and len(complete_2025) == 2
            and complete_2025["mean_net_bp"].gt(0.0).all()
        )
        summary["success_gate"][bar] = passed
    write_json(RESULTS / "validation_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "validation"), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    safe_end = utc(config["window"]["validation_end_exclusive"])
    holdout = utc(config["window"]["holdout_start"])
    if safe_end >= holdout:
        raise RuntimeError("configured validation boundary reaches holdout")
    if args.phase == "development":
        development_phase(config)
    else:
        validation_phase(config)


if __name__ == "__main__":
    main()
