#!/usr/bin/env python3
"""Research a causal two-key-candle moving-average retest on OKX 1h bars.

Source columns are 15-minute ``open/high/low/close/volume/open_time`` rows from
the repository's OKX cache. Four complete UTC-aligned rows are aggregated into
one hour. Features at K2 use only bars through K2: close-derived SMA/EMA
20/60/120, SMA40(HL2), Pine/Wilder ATR14, the public ChartPrime MA Shift
formula (SMA40(HL2), 1000-bar 99th-percentile normalization, change lag 15,
HMA10), confirmed 10/10 pivots, candle geometry, volume and the path from K1.

Selection never reads the repository holdout. Non-BTC 2023-2024 half-years
select one semantic variable family at a time. The rule is frozen before the
2025--2026-02 validation and before BTC transfer. Entry is ``open[K2+1]``;
the stop is exactly the completed K2 extreme. Outcomes use a fixed 24-bar
horizon (12/48 only as sensitivity), conservative stop-first collision and the
project's unchanged 20bp round-trip cost. This script fits no model and changes
no production threshold, bundle, forward log or execution setting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-two-key-candle-ma-retest-1h-preholdout-v1"
CONFIG_PATH = EXPERIMENT / "config.json"
OUT = EXPERIMENT / "results"
SYMBOL_RE = re.compile(r"^okx_(?P<symbol>.+)_USDT_SWAP_15m_\d+\.csv$")
HORIZONS = (12, 24, 48)
MA_COLUMNS = ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")
MA_PAIRS = tuple(
    (fast, slow)
    for fast_period, slow_period in ((20, 60), (20, 120), (60, 120))
    for fast in (f"sma{fast_period}", f"ema{fast_period}")
    for slow in (f"sma{slow_period}", f"ema{slow_period}")
)
PERMUTATIONS = 10_000
BOOTSTRAPS = 2_000


@dataclass(frozen=True)
class Arm:
    """One threshold choice inside one semantic feature family."""

    family: str
    name: str
    description: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def symbol_from_path(path: Path) -> str:
    match = SYMBOL_RE.match(path.name)
    if match is None:
        raise ValueError(f"unexpected kline filename: {path.name}")
    return match.group("symbol")


def pine_rma(values: Sequence[float], length: int) -> np.ndarray:
    """Return Pine/Wilder RMA with an SMA seed and no future rows."""

    array = np.asarray(values, dtype=float)
    output = np.full(array.shape, np.nan, dtype=float)
    if length <= 0:
        raise ValueError("length must be positive")
    for start in range(max(0, len(array) - length + 1)):
        seed = array[start : start + length]
        if np.isfinite(seed).all():
            seed_i = start + length - 1
            output[seed_i] = float(seed.mean())
            for i in range(seed_i + 1, len(array)):
                value = array[i]
                output[i] = (
                    output[i - 1]
                    if not np.isfinite(value)
                    else (output[i - 1] * (length - 1) + value) / length
                )
            break
    return output


def weighted_ma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(weights.sum())
    return series.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / denominator), raw=True
    )


def hull_ma(series: pd.Series, length: int) -> pd.Series:
    half = max(1, length // 2)
    root = max(1, int(round(math.sqrt(length))))
    return weighted_ma(2.0 * weighted_ma(series, half) - weighted_ma(series, length), root)


def resample_hourly(path: Path, safe_end: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = pd.read_csv(path, usecols=["open", "high", "low", "close", "volume", "open_time"])
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
    raw = raw.sort_values("open_time").drop_duplicates("open_time", keep="last")
    raw = raw[raw["open_time"] < safe_end].copy()
    raw = raw.set_index("open_time")
    grouped = raw.resample("1h", label="left", closed="left", origin="epoch")
    hourly = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    hourly["source_rows"] = grouped["close"].count()
    incomplete = int(hourly["source_rows"].ne(4).sum())
    hourly = hourly[hourly["source_rows"].eq(4)].drop(columns="source_rows")
    hourly = hourly.dropna().reset_index()
    gaps = int((hourly["open_time"].diff().dropna() != pd.Timedelta(hours=1)).sum())
    quality = {
        "source_file": str(path.resolve().relative_to(PROJECT)),
        "source_sha256": sha256_file(path),
        "source_rows": int(len(raw)),
        "hourly_rows": int(len(hourly)),
        "incomplete_hours_dropped": incomplete,
        "hourly_gap_count": gaps,
        "first_hour": hourly["open_time"].iloc[0].isoformat() if len(hourly) else None,
        "last_hour": hourly["open_time"].iloc[-1].isoformat() if len(hourly) else None,
    }
    return hourly, quality


def add_market_break_state(frame: pd.DataFrame, left: int = 10, right: int = 10) -> pd.DataFrame:
    """Add a causal approximation of confirmed ChartPrime 10/10 pivots.

    A pivot centred at j becomes available only at j+right. Break state changes
    only when a completed close crosses the most recently confirmed level.
    """

    out = frame.copy()
    width = left + right + 1
    centre_high = out["high"].shift(right)
    centre_low = out["low"].shift(right)
    rolling_high = out["high"].rolling(width, min_periods=width).max()
    rolling_low = out["low"].rolling(width, min_periods=width).min()
    confirmed_high = centre_high.where(centre_high.eq(rolling_high))
    confirmed_low = centre_low.where(centre_low.eq(rolling_low))
    last_high = confirmed_high.ffill()
    last_low = confirmed_low.ffill()
    close = out["close"].astype(float)
    break_up = close.gt(last_high) & close.shift(1).le(last_high.shift(1))
    break_down = close.lt(last_low) & close.shift(1).ge(last_low.shift(1))
    state = np.zeros(len(out), dtype=int)
    active = 0
    for i in range(len(out)):
        if bool(break_up.iloc[i]):
            active = 1
        elif bool(break_down.iloc[i]):
            active = -1
        state[i] = active
    out["pivot_high_confirmed"] = last_high
    out["pivot_low_confirmed"] = last_low
    out["market_break_up"] = break_up.fillna(False)
    out["market_break_down"] = break_down.fillna(False)
    out["market_break_state"] = state
    return out


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal price, rope, colour, oscillator and structure features."""

    out = frame.copy()
    open_ = out["open"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    volume = out["volume"].astype(float)
    hl2 = (high + low) / 2.0
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    out["atr"] = pine_rma(true_range.to_numpy(dtype=float), 14)
    atr = out["atr"].replace(0.0, np.nan)

    for period in (20, 60, 120):
        out[f"sma{period}"] = close.rolling(period, min_periods=period).mean()
        out[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    out["sma40_hl2"] = hl2.rolling(40, min_periods=40).mean()
    bundle = out.loc[:, list(MA_COLUMNS)].astype(float)
    out["rope_low"] = bundle.min(axis=1)
    out["rope_high"] = bundle.max(axis=1)
    out["rope_mid"] = bundle.mean(axis=1)
    out["rope_width_atr"] = (out["rope_high"] - out["rope_low"]) / atr
    out["rope_slope_atr_4"] = (out["rope_mid"] - out["rope_mid"].shift(4)) / (atr * 4.0)
    out["prior_rope_width_atr_20"] = out["rope_width_atr"].shift(1).rolling(20, min_periods=20).mean()
    out["prior_range_atr_20"] = (
        high.shift(1).rolling(20, min_periods=20).max()
        - low.shift(1).rolling(20, min_periods=20).min()
    ) / atr
    out["ma_up_alignment"] = sum(out[fast].ge(out[slow]).astype(int) for fast, slow in MA_PAIRS)
    out["ma_down_alignment"] = sum(out[fast].le(out[slow]).astype(int) for fast, slow in MA_PAIRS)

    prior_volume = volume.shift(1).rolling(20, min_periods=20).mean().replace(0.0, np.nan)
    out["volume_ratio_20"] = volume / prior_volume
    out["volume_z_96"] = (
        (volume - volume.shift(1).rolling(96, min_periods=48).mean())
        / volume.shift(1).rolling(96, min_periods=48).std().replace(0.0, np.nan)
    )
    out["atr_release_24"] = atr / atr.shift(1).rolling(24, min_periods=24).mean()
    out["atr_pct"] = atr / close

    # Public MA Shift formula: candle colour is price relative to SMA40(HL2),
    # while the four oscillator colours encode sign and one-bar acceleration.
    difference = hl2 - out["sma40_hl2"]
    percentile = difference.rolling(1000, min_periods=1000).quantile(0.99, interpolation="linear")
    ratio = difference.div(percentile.replace(0.0, np.nan))
    out["ma_shift_osc"] = hull_ma(ratio - ratio.shift(15), 10)
    out["ma_shift_osc_delta"] = out["ma_shift_osc"].diff()
    out["ma_shift_candle_side"] = np.where(hl2.ge(out["sma40_hl2"]), 1, -1)
    osc = out["ma_shift_osc"]
    delta = out["ma_shift_osc_delta"]
    out["ma_shift_osc_colour"] = np.select(
        [osc.gt(0.0) & delta.gt(0.0), osc.gt(0.0), osc.lt(0.0) & delta.lt(0.0)],
        [2, 1, -2],
        default=-1,
    )

    native_up = close.gt(open_)
    green_volume = volume.where(native_up, 0.0).rolling(20, min_periods=5).sum()
    total_volume = volume.rolling(20, min_periods=5).sum().replace(0.0, np.nan)
    out["green_volume_share_20"] = green_volume / total_volume
    out["native_candle_side"] = np.where(close.ge(open_), 1, -1)
    out["bar_range"] = high - low
    out["body"] = (close - open_).abs()
    out["body_ratio"] = out["body"] / out["bar_range"].replace(0.0, np.nan)
    out["lower_wick"] = np.minimum(open_, close) - low
    out["upper_wick"] = high - np.maximum(open_, close)
    out["range_atr"] = out["bar_range"] / atr
    out = add_market_break_state(out)
    return out.replace([np.inf, -np.inf], np.nan)


def direction_columns(frame: pd.DataFrame, direction: int) -> pd.DataFrame:
    """Return side-aligned K1/K2 columns for long (+1) or short (-1)."""

    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    out = pd.DataFrame(index=frame.index)
    atr = frame["atr"].astype(float)
    open_ = frame["open"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    body_low = np.minimum(open_, close)
    body_high = np.maximum(open_, close)
    overlap = (
        np.minimum(body_high, frame["rope_high"])
        - np.maximum(body_low, frame["rope_low"])
    ).clip(lower=0.0)
    rope_width = (frame["rope_high"] - frame["rope_low"]).replace(0.0, np.nan)
    out["k1_rope_coverage"] = (overlap / rope_width).clip(upper=1.0).fillna(
        (body_low.le(frame["rope_mid"]) & body_high.ge(frame["rope_mid"])).astype(float)
    )
    if direction > 0:
        out["k1_entry_depth_atr"] = (frame["rope_low"] - open_) / atr
        out["k1_exit_depth_atr"] = (close - frame["rope_high"]) / atr
        out["k1_sma40_entry_depth_atr"] = (frame["sma40_hl2"] - open_) / atr
        out["k1_sma40_exit_depth_atr"] = (close - frame["sma40_hl2"]) / atr
        out["k1_close_location"] = (close - low) / frame["bar_range"].replace(0.0, np.nan)
        out["k2_wick_share"] = frame["lower_wick"] / frame["bar_range"].replace(0.0, np.nan)
        out["k2_rejection_close_location"] = (close - low) / frame["bar_range"].replace(0.0, np.nan)
        out["k2_touch_depth_atr"] = (frame["rope_high"] - low) / atr
        out["k2_close_side_atr"] = (close - frame["rope_mid"]) / atr
        out["k2_reclaim_atr"] = (close - frame["rope_high"]) / atr
        out["side_ma_alignment"] = frame["ma_up_alignment"]
        out["k2_side_break_event"] = frame["market_break_up"].astype(int)
    else:
        out["k1_entry_depth_atr"] = (open_ - frame["rope_high"]) / atr
        out["k1_exit_depth_atr"] = (frame["rope_low"] - close) / atr
        out["k1_sma40_entry_depth_atr"] = (open_ - frame["sma40_hl2"]) / atr
        out["k1_sma40_exit_depth_atr"] = (frame["sma40_hl2"] - close) / atr
        out["k1_close_location"] = (high - close) / frame["bar_range"].replace(0.0, np.nan)
        out["k2_wick_share"] = frame["upper_wick"] / frame["bar_range"].replace(0.0, np.nan)
        out["k2_rejection_close_location"] = (high - close) / frame["bar_range"].replace(0.0, np.nan)
        out["k2_touch_depth_atr"] = (high - frame["rope_low"]) / atr
        out["k2_close_side_atr"] = (frame["rope_mid"] - close) / atr
        out["k2_reclaim_atr"] = (frame["rope_low"] - close) / atr
        out["side_ma_alignment"] = frame["ma_down_alignment"]
        out["k2_side_break_event"] = frame["market_break_down"].astype(int)
    out["k1_cross_depth_atr"] = out[["k1_entry_depth_atr", "k1_exit_depth_atr"]].min(axis=1)
    out["k1_sma40_cross_depth_atr"] = out[
        ["k1_sma40_entry_depth_atr", "k1_sma40_exit_depth_atr"]
    ].min(axis=1)
    out["k1_body_ratio"] = frame["body_ratio"]
    out["k1_range_atr"] = frame["range_atr"]
    out["k1_volume_ratio"] = frame["volume_ratio_20"]
    out["k1_volume_z"] = frame["volume_z_96"]
    out["k1_ma_colour_aligned"] = frame["ma_shift_candle_side"].eq(direction)
    out["k1_osc_sign_aligned"] = frame["ma_shift_osc"].mul(direction).gt(0.0)
    out["k1_osc_accel_aligned"] = frame["ma_shift_osc_delta"].mul(direction).gt(0.0)
    out["k1_structure_aligned"] = frame["market_break_state"].eq(direction)
    out["k2_body_ratio"] = frame["body_ratio"]
    out["k2_range_atr"] = frame["range_atr"]
    out["k2_volume_ratio"] = frame["volume_ratio_20"]
    out["k2_volume_z"] = frame["volume_z_96"]
    out["k2_native_colour_aligned"] = frame["native_candle_side"].eq(direction)
    out["k2_ma_colour_aligned"] = frame["ma_shift_candle_side"].eq(direction)
    out["k2_osc_sign_aligned"] = frame["ma_shift_osc"].mul(direction).gt(0.0)
    out["k2_osc_accel_aligned"] = frame["ma_shift_osc_delta"].mul(direction).gt(0.0)
    out["k2_structure_aligned"] = frame["market_break_state"].eq(direction)
    out["k2_structure_not_opposite"] = frame["market_break_state"].ne(-direction)
    out["rope_width_atr"] = frame["rope_width_atr"]
    out["rope_slope_side_atr"] = frame["rope_slope_atr_4"] * direction
    out["prior_rope_width_atr_20"] = frame["prior_rope_width_atr_20"]
    out["prior_range_atr_20"] = frame["prior_range_atr_20"]
    out["side_ma_alignment"] = out["side_ma_alignment"].astype(float)
    out["atr_release_24"] = frame["atr_release_24"]
    out["atr_pct"] = frame["atr_pct"]
    out["green_volume_share_20"] = frame["green_volume_share_20"]
    out["ma_shift_osc"] = frame["ma_shift_osc"] * direction
    out["ma_shift_osc_delta"] = frame["ma_shift_osc_delta"] * direction
    return out


def broad_masks(side: pd.DataFrame, frame: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    broad = config["broad_candidate"]
    direction = int(side.attrs["direction"])
    directional_body = (frame["close"] - frame["open"]) * direction > 0.0
    k1 = (
        directional_body
        & side["k1_body_ratio"].ge(float(broad["k1_min_body_ratio"]))
        & side["k1_range_atr"].ge(float(broad["k1_min_range_atr"]))
        & side["k1_rope_coverage"].ge(float(broad["k1_min_rope_coverage"]))
        & side["k1_entry_depth_atr"].ge(float(broad["k1_min_entry_depth_atr"]))
        & side["k1_exit_depth_atr"].ge(float(broad["k1_min_exit_depth_atr"]))
    )
    k2 = (
        side["k2_wick_share"].ge(float(broad["k2_min_rejection_wick_share"]))
        & side["k2_touch_depth_atr"].ge(-float(broad["k2_max_touch_miss_atr"]))
        & side["k2_touch_depth_atr"].le(float(broad["k2_max_touch_depth_atr"]))
        & side["k2_close_side_atr"].ge(float(broad["k2_min_close_side_atr"]))
    )
    return k1.fillna(False), k2.fillna(False)


def simulate_outcome(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    direction: int,
    stop_distance_atr: float | None,
    horizon: int,
    round_trip_cost: float,
) -> dict[str, float | int | bool]:
    """Enter next open and apply a K2-extreme or copied-ATR stop."""

    entry_i = signal_i + 1
    end_i = entry_i + horizon - 1
    if entry_i >= len(frame) or end_i >= len(frame):
        return {"valid": False}
    entry = float(frame["open"].iloc[entry_i])
    atr = float(frame["atr"].iloc[signal_i])
    if stop_distance_atr is None:
        stop = float(frame["low"].iloc[signal_i] if direction > 0 else frame["high"].iloc[signal_i])
        risk = direction * (entry - stop)
        risk_atr = risk / atr if atr > 0.0 else float("nan")
    else:
        risk_atr = float(stop_distance_atr)
        risk = risk_atr * atr
        stop = entry - direction * risk
    if not np.isfinite(risk_atr) or not np.isfinite(risk) or risk <= 0.0:
        return {"valid": False}
    exit_price = float(frame["close"].iloc[end_i])
    exit_i = end_i
    stopped = False
    target_hits = {1: False, 2: False, 3: False, 5: False}
    active_targets = set(target_hits)
    max_favourable = 0.0
    max_adverse = 0.0
    for i in range(entry_i, end_i + 1):
        bar_open = float(frame["open"].iloc[i])
        bar_high = float(frame["high"].iloc[i])
        bar_low = float(frame["low"].iloc[i])
        favourable = (bar_high - entry) if direction > 0 else (entry - bar_low)
        adverse = (entry - bar_low) if direction > 0 else (bar_high - entry)
        max_favourable = max(max_favourable, favourable)
        max_adverse = max(max_adverse, adverse)
        stop_touched = bar_low <= stop if direction > 0 else bar_high >= stop
        if stop_touched:
            exit_price = min(bar_open, stop) if direction > 0 else max(bar_open, stop)
            exit_i = i
            stopped = True
            break
        for multiple in tuple(active_targets):
            target = entry + direction * risk * multiple
            touched = bar_high >= target if direction > 0 else bar_low <= target
            if touched:
                target_hits[multiple] = True
                active_targets.remove(multiple)
    gross = direction * (exit_price / entry - 1.0)
    return {
        "valid": True,
        "entry_i": entry_i,
        "exit_i": exit_i,
        "entry_price": entry,
        "stop_price": stop,
        "stop_distance_atr": risk_atr,
        "stopped": stopped,
        "gross_return": gross,
        "net_return": gross - round_trip_cost,
        "terminal_r": direction * (exit_price - entry) / risk,
        "mfe_r": max_favourable / risk,
        "mae_r": max_adverse / risk,
        **{f"hit_{multiple}r": bool(hit) for multiple, hit in target_hits.items()},
    }


def path_features(frame: pd.DataFrame, k1_i: int, k2_i: int, direction: int) -> dict[str, float | int]:
    atr = float(frame["atr"].iloc[k1_i])
    k1_close = float(frame["close"].iloc[k1_i])
    k2_close = float(frame["close"].iloc[k2_i])
    k2_extreme = float(frame["low"].iloc[k2_i] if direction > 0 else frame["high"].iloc[k2_i])
    k1_extreme = float(frame["low"].iloc[k1_i] if direction > 0 else frame["high"].iloc[k1_i])
    path = frame.iloc[k1_i : k2_i + 1]
    middle = frame.iloc[k1_i + 1 : k2_i]
    changes = path["close"].astype(float).diff().abs().dropna()
    variation = float(changes.sum() / atr) if atr > 0.0 else float("nan")
    signed_close_distance = direction * (k2_close - k1_close) / atr
    efficiency = signed_close_distance / variation if variation > 0.0 else 0.0
    if middle.empty:
        favourable_extension = 0.0
        extreme_before = k1_close
        wrong_closes = 0
        side_colour_share = 1.0
        native_side_share = 1.0
    else:
        if direction > 0:
            extreme_before = max(k1_close, float(middle["high"].max()))
            wrong_closes = int(middle["close"].lt(middle["rope_mid"]).sum())
        else:
            extreme_before = min(k1_close, float(middle["low"].min()))
            wrong_closes = int(middle["close"].gt(middle["rope_mid"]).sum())
        favourable_extension = direction * (extreme_before - k1_close) / atr
        side_colour_share = float(middle["ma_shift_candle_side"].eq(direction).mean())
        native_side_share = float(middle["native_candle_side"].eq(direction).mean())
    extension_price = direction * (extreme_before - k1_close)
    retrace_price = direction * (extreme_before - k2_extreme)
    retrace_fraction = retrace_price / extension_price if extension_price > 0.0 else float("nan")
    body_low_1 = min(float(frame["open"].iloc[k1_i]), k1_close)
    body_high_1 = max(float(frame["open"].iloc[k1_i]), k1_close)
    body_low_2 = min(float(frame["open"].iloc[k2_i]), k2_close)
    body_high_2 = max(float(frame["open"].iloc[k2_i]), k2_close)
    overlap = max(0.0, min(body_high_1, body_high_2) - max(body_low_1, body_low_2))
    body_union = max(body_high_1, body_high_2) - min(body_low_1, body_low_2)
    return {
        "close_distance_atr": signed_close_distance,
        "extreme_distance_atr": direction * (k2_extreme - k1_extreme) / atr,
        "path_variation_atr": variation,
        "path_efficiency": efficiency,
        "pre_retest_extension_atr": favourable_extension,
        "retrace_fraction": retrace_fraction,
        "wrong_side_close_count": wrong_closes,
        "intermediate_ma_colour_share": side_colour_share,
        "intermediate_native_side_share": native_side_share,
        "k1_k2_body_overlap_share": overlap / body_union if body_union > 0.0 else 0.0,
    }


def make_pair_rows(
    frame: pd.DataFrame,
    side: pd.DataFrame,
    *,
    symbol: str,
    direction: int,
    config: dict[str, Any],
) -> pd.DataFrame:
    side = side.copy()
    side.attrs["direction"] = direction
    k1_mask, k2_mask = broad_masks(side, frame, config)
    max_gap = int(config["max_k1_k2_gap_bars"])
    rows: list[dict[str, Any]] = []
    k1_columns = [column for column in side.columns if column.startswith("k1_")]
    k2_columns = [column for column in side.columns if column.startswith("k2_")]
    shared_columns = [
        "rope_width_atr",
        "rope_slope_side_atr",
        "prior_rope_width_atr_20",
        "prior_range_atr_20",
        "side_ma_alignment",
        "atr_release_24",
        "atr_pct",
        "green_volume_share_20",
        "ma_shift_osc",
        "ma_shift_osc_delta",
    ]
    for gap in range(1, max_gap + 1):
        valid = k2_mask & k1_mask.shift(gap, fill_value=False)
        for k2_i in np.flatnonzero(valid.to_numpy(dtype=bool)):
            k1_i = int(k2_i - gap)
            row: dict[str, Any] = {
                "symbol": symbol,
                "direction": direction,
                "side": "long" if direction > 0 else "short",
                "k1_i": k1_i,
                "k2_i": int(k2_i),
                "gap_bars": gap,
                "k1_time": frame["open_time"].iloc[k1_i],
                "k2_time": frame["open_time"].iloc[k2_i],
                "k1_open": float(frame["open"].iloc[k1_i]),
                "k1_high": float(frame["high"].iloc[k1_i]),
                "k1_low": float(frame["low"].iloc[k1_i]),
                "k1_close": float(frame["close"].iloc[k1_i]),
                "k2_open": float(frame["open"].iloc[k2_i]),
                "k2_high": float(frame["high"].iloc[k2_i]),
                "k2_low": float(frame["low"].iloc[k2_i]),
                "k2_close": float(frame["close"].iloc[k2_i]),
                "atr": float(frame["atr"].iloc[k2_i]),
                "utc_hour": int(frame["open_time"].iloc[k2_i].hour),
                "weekday": int(frame["open_time"].iloc[k2_i].weekday()),
            }
            for column in k1_columns:
                row[column] = side[column].iloc[k1_i]
            for column in k2_columns + shared_columns:
                row[column] = side[column].iloc[k2_i]
            row["k2_to_k1_volume_ratio"] = float(frame["volume"].iloc[k2_i]) / max(
                float(frame["volume"].iloc[k1_i]), 1e-12
            )
            row.update(path_features(frame, k1_i, int(k2_i), direction))
            row["k1_quality"] = float(
                np.nanmean(
                    [
                        min(1.0, float(row["k1_rope_coverage"])),
                        min(1.0, max(0.0, float(row["k1_body_ratio"]))),
                        min(1.0, max(0.0, float(row["k1_range_atr"]) / 2.0)),
                        min(1.0, max(0.0, (float(row["k1_cross_depth_atr"]) + 0.15) / 0.5)),
                    ]
                )
            )
            rows.append(row)
    return pd.DataFrame(rows)


def assign_atr_buckets(frame: pd.DataFrame) -> np.ndarray:
    """Return deterministic within-symbol-month ATR quintiles for controls."""

    result = np.full(len(frame), -1, dtype=int)
    month = frame["open_time"].dt.strftime("%Y-%m")
    helper = pd.DataFrame({"i": np.arange(len(frame)), "month": month, "atr": frame["atr"]})
    for _, group in helper.dropna(subset=["atr"]).groupby("month", sort=False):
        if len(group) < 5:
            result[group["i"].to_numpy(dtype=int)] = 0
            continue
        ranks = group["atr"].rank(method="first")
        buckets = pd.qcut(ranks, q=5, labels=False).to_numpy(dtype=int)
        result[group["i"].to_numpy(dtype=int)] = buckets
    return result


def deterministic_indices(seed: str, event_id: str, choices: np.ndarray, n: int) -> np.ndarray:
    if len(choices) == 0:
        return np.empty(0, dtype=int)
    scored = sorted(
        (
            hashlib.sha256(f"{seed}|{event_id}|{int(index)}".encode("utf-8")).hexdigest(),
            int(index),
        )
        for index in choices
    )
    return np.asarray([index for _, index in scored[:n]], dtype=int)


def attach_event_outcomes(
    frame: pd.DataFrame,
    pairs: pd.DataFrame,
    *,
    symbol: str,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach K2-extreme outcomes and exact-stratum copied-stop controls."""

    if pairs.empty:
        return pairs, pd.DataFrame()
    cost = float(config["round_trip_cost"])
    broad = config["broad_candidate"]
    max_horizon = max(HORIZONS)
    unique = pairs[["direction", "side", "k2_i", "k2_time", "atr"]].drop_duplicates().copy()
    unique = unique.sort_values(["k2_i", "direction"]).reset_index(drop=True)
    outcome_rows: list[dict[str, Any]] = []
    broad_signal_indices = set(unique["k2_i"].astype(int).tolist())
    buckets = assign_atr_buckets(frame)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    eligible = (
        frame["atr"].notna().to_numpy()
        & (np.arange(len(frame)) + 1 + max_horizon - 1 < len(frame))
        & (buckets >= 0)
    )
    pool_by_stratum: dict[tuple[str, int, int], np.ndarray] = {}
    pool_indices = np.flatnonzero(eligible)
    pool_helper = pd.DataFrame(
        {
            "i": pool_indices,
            "month": months[pool_indices],
            "block": blocks[pool_indices],
            "bucket": buckets[pool_indices],
        }
    )
    for key, group in pool_helper.groupby(["month", "block", "bucket"], sort=False):
        pool_by_stratum[(str(key[0]), int(key[1]), int(key[2]))] = group["i"].to_numpy(dtype=int)

    for event in unique.itertuples(index=False):
        signal_i = int(event.k2_i)
        direction = int(event.direction)
        event_id = f"{symbol}|{direction}|{signal_i}"
        primary = simulate_outcome(
            frame,
            signal_i=signal_i,
            direction=direction,
            stop_distance_atr=None,
            horizon=24,
            round_trip_cost=cost,
        )
        if not bool(primary.get("valid", False)):
            continue
        risk_atr = float(primary["stop_distance_atr"])
        if not (
            float(broad["min_stop_distance_atr"])
            <= risk_atr
            <= float(broad["max_stop_distance_atr"])
        ):
            continue
        signal_outcomes: dict[str, Any] = {}
        for horizon in HORIZONS:
            result = primary if horizon == 24 else simulate_outcome(
                frame,
                signal_i=signal_i,
                direction=direction,
                stop_distance_atr=None,
                horizon=horizon,
                round_trip_cost=cost,
            )
            if not bool(result.get("valid", False)):
                break
            for key, value in result.items():
                if key != "valid":
                    signal_outcomes[f"{key}_{horizon}"] = value
        else:
            stratum = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
            pool = pool_by_stratum.get(stratum, np.empty(0, dtype=int))
            pool = np.asarray(
                [
                    int(index)
                    for index in pool
                    if int(index) not in broad_signal_indices
                    and abs(int(index) - signal_i) > max_horizon + 1
                ],
                dtype=int,
            )
            controls = deterministic_indices(
                str(config["matched_control"]["seed"]),
                event_id,
                pool,
                int(config["matched_control"]["n_per_event"]),
            )
            if len(controls) != int(config["matched_control"]["n_per_event"]):
                # Exact matching is a hard contract. Do not silently widen a
                # sparse symbol×month×time-block×ATR stratum.
                continue
            control_payload: dict[str, Any] = {"n_controls": int(len(controls))}
            control_detail: list[dict[str, Any]] = []
            for horizon in HORIZONS:
                results = [
                    simulate_outcome(
                        frame,
                        signal_i=int(control_i),
                        direction=direction,
                        stop_distance_atr=risk_atr,
                        horizon=horizon,
                        round_trip_cost=cost,
                    )
                    for control_i in controls
                ]
                results = [item for item in results if bool(item.get("valid", False))]
                control_payload[f"control_net_return_{horizon}"] = (
                    float(np.mean([float(item["net_return"]) for item in results]))
                    if results
                    else float("nan")
                )
                control_payload[f"control_gross_return_{horizon}"] = (
                    float(np.mean([float(item["gross_return"]) for item in results]))
                    if results
                    else float("nan")
                )
                for rank, (control_i, result) in enumerate(zip(controls, results)):
                    control_detail.append(
                        {
                            "event_id": event_id,
                            "symbol": symbol,
                            "direction": direction,
                            "candidate_k2_i": signal_i,
                            "candidate_k2_time": event.k2_time,
                            "control_rank": rank,
                            "control_signal_i": int(control_i),
                            "control_signal_time": frame["open_time"].iloc[int(control_i)],
                            "horizon": horizon,
                            "copied_stop_distance_atr": risk_atr,
                            "control_gross_return": result.get("gross_return"),
                            "control_net_return": result.get("net_return"),
                        }
                    )
            outcome_rows.append(
                {
                    "event_id": event_id,
                    "symbol": symbol,
                    "direction": direction,
                    "side": event.side,
                    "k2_i": signal_i,
                    "k2_time": event.k2_time,
                    **signal_outcomes,
                    **control_payload,
                }
            )
            # A flat list is attached after the loop to avoid a second control replay.
            outcome_rows[-1]["_control_detail"] = control_detail
    outcomes = pd.DataFrame(outcome_rows)
    if outcomes.empty:
        return pairs.iloc[0:0].copy(), pd.DataFrame()
    control_rows = [row for details in outcomes.pop("_control_detail") for row in details]
    for horizon in HORIZONS:
        outcomes[f"paired_excess_{horizon}"] = (
            outcomes[f"net_return_{horizon}"] - outcomes[f"control_net_return_{horizon}"]
        )
    merged = pairs.merge(
        outcomes,
        on=["symbol", "direction", "side", "k2_i", "k2_time"],
        how="inner",
        validate="many_to_one",
    )
    return merged, pd.DataFrame(control_rows)


def process_symbol(path: Path, config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    safe_end = pd.Timestamp(config["safe_end_exclusive"])
    symbol = symbol_from_path(path)
    hourly, quality = resample_hourly(path, safe_end)
    quality["symbol"] = symbol
    if hourly.empty:
        return pd.DataFrame(), pd.DataFrame(), quality
    featured = add_features(hourly)
    pair_frames: list[pd.DataFrame] = []
    for direction in (1, -1):
        side = direction_columns(featured, direction)
        pair_frames.append(
            make_pair_rows(featured, side, symbol=symbol, direction=direction, config=config)
        )
    pairs = pd.concat(pair_frames, ignore_index=True) if pair_frames else pd.DataFrame()
    pairs, controls = attach_event_outcomes(featured, pairs, symbol=symbol, config=config)
    quality["broad_pairs"] = int(len(pairs))
    quality["broad_k2_events"] = int(
        pairs[["direction", "k2_i"]].drop_duplicates().shape[0] if not pairs.empty else 0
    )
    return pairs, controls, quality


def family_arms() -> list[list[Arm]]:
    """Return the preregistered semantic-family search order."""

    def ge(column: str, value: float) -> Callable[[pd.DataFrame], pd.Series]:
        return lambda frame, c=column, v=value: frame[c].ge(v)

    def le(column: str, value: float) -> Callable[[pd.DataFrame], pd.Series]:
        return lambda frame, c=column, v=value: frame[c].le(v)

    families: list[list[Arm]] = []
    families.append(
        [
            Arm("gap_window", name, label, lambda f, lo=lo, hi=hi: f["gap_bars"].between(lo, hi))
            for name, label, lo, hi in (
                ("gap_1_3", "K1→K2 1–3 bars", 1, 3),
                ("gap_1_5", "K1→K2 1–5 bars", 1, 5),
                ("gap_2_5", "K1→K2 2–5 bars", 2, 5),
                ("gap_2_8", "K1→K2 2–8 bars", 2, 8),
                ("gap_3_8", "K1→K2 3–8 bars", 3, 8),
                ("gap_1_8", "K1→K2 1–8 bars", 1, 8),
                ("gap_1_12", "K1→K2 1–12 bars", 1, 12),
            )
        ]
    )
    families.append(
        [Arm("k1_cross_depth", f"cross_{v:g}", f"K1 full-rope depth ≥ {v:g} ATR", ge("k1_cross_depth_atr", v)) for v in (0.0, 0.05, 0.10, 0.20)]
    )
    families.append(
        [Arm("k1_sma40_cross", f"sma40_{v:g}", f"K1 SMA40(HL2) cross depth ≥ {v:g} ATR", ge("k1_sma40_cross_depth_atr", v)) for v in (0.0, 0.05, 0.10)]
    )
    families.append(
        [Arm("k1_body_ratio", f"body_{v:g}", f"K1 body/range ≥ {v:g}", ge("k1_body_ratio", v)) for v in (0.35, 0.50, 0.65, 0.75)]
    )
    families.append(
        [Arm("k1_range_atr", f"range_{v:g}", f"K1 range ≥ {v:g} ATR", ge("k1_range_atr", v)) for v in (0.8, 1.0, 1.25, 1.5)]
    )
    families.append(
        [Arm("k1_close_location", f"close_{v:g}", f"K1 close near directional extreme ≥ {v:g}", ge("k1_close_location", v)) for v in (0.65, 0.75, 0.85, 0.90)]
    )
    families.append(
        [Arm("k1_volume", f"volume_{v:g}", f"K1 volume / prior20 ≥ {v:g}", ge("k1_volume_ratio", v)) for v in (0.8, 1.0, 1.25, 1.5, 2.0)]
    )
    families.append(
        [Arm("rope_width", f"rope_{v:g}", f"K2 six-MA rope width ≤ {v:g} ATR", le("rope_width_atr", v)) for v in (0.25, 0.50, 0.75, 1.0)]
    )
    families.append(
        [Arm("k2_wick_share", f"wick_{v:g}", f"K2 rejection wick/range ≥ {v:g}", ge("k2_wick_share", v)) for v in (0.30, 0.45, 0.60, 0.70)]
    )
    families.append(
        [Arm("k2_body_ratio", f"k2_body_{v:g}", f"K2 body/range ≤ {v:g}", le("k2_body_ratio", v)) for v in (0.50, 0.35, 0.20, 0.10)]
    )
    families.append(
        [Arm("k2_rejection", f"reject_{v:g}", f"K2 closes ≥ {v:g} of rejection range", ge("k2_rejection_close_location", v)) for v in (0.65, 0.75, 0.85, 0.90)]
    )
    families.append(
        [
            Arm("k2_touch_depth", "touch_near", "K2 touch depth −0.10…0.50 ATR", lambda f: f["k2_touch_depth_atr"].between(-0.10, 0.50)),
            Arm("k2_touch_depth", "touch_inside", "K2 penetrates rope 0…0.75 ATR", lambda f: f["k2_touch_depth_atr"].between(0.0, 0.75)),
            Arm("k2_touch_depth", "touch_moderate", "K2 touch depth −0.05…1.00 ATR", lambda f: f["k2_touch_depth_atr"].between(-0.05, 1.00)),
            Arm("k2_touch_depth", "touch_deep", "K2 penetrates rope 0.25…1.50 ATR", lambda f: f["k2_touch_depth_atr"].between(0.25, 1.50)),
        ]
    )
    families.append(
        [Arm("k2_reclaim", f"reclaim_{v:g}", f"K2 close beyond rope ≥ {v:g} ATR", ge("k2_reclaim_atr", v)) for v in (0.0, 0.05, 0.10, 0.20)]
    )
    families.append(
        [
            Arm("stop_distance", "risk_0.1_1", "entry-to-K2 stop 0.10–1.00 ATR", lambda f: f["stop_distance_atr_24"].between(0.10, 1.00)),
            Arm("stop_distance", "risk_0.15_1.5", "entry-to-K2 stop 0.15–1.50 ATR", lambda f: f["stop_distance_atr_24"].between(0.15, 1.50)),
            Arm("stop_distance", "risk_0.25_2", "entry-to-K2 stop 0.25–2.00 ATR", lambda f: f["stop_distance_atr_24"].between(0.25, 2.00)),
            Arm("stop_distance", "risk_0.5_2.5", "entry-to-K2 stop 0.50–2.50 ATR", lambda f: f["stop_distance_atr_24"].between(0.50, 2.50)),
        ]
    )
    families.append(
        [Arm("k2_vs_k1_extreme", f"extreme_{v:g}", f"K2 higher-low/lower-high distance ≥ {v:g} ATR", ge("extreme_distance_atr", v)) for v in (-0.50, 0.0, 0.25, 0.50)]
    )
    families.append(
        [Arm("close_distance", f"close_abs_{v:g}", f"|K2 close − K1 close| ≤ {v:g} ATR", lambda f, v=v: f["close_distance_atr"].abs().le(v)) for v in (0.50, 1.00, 1.50)]
    )
    families.append(
        [Arm("pre_retest_extension", f"extension_{v:g}", f"pre-retest continuation ≤ {v:g} ATR", le("pre_retest_extension_atr", v)) for v in (0.50, 1.00, 2.00, 3.00)]
    )
    families.append(
        [Arm("path_variation", f"path_{v:g}", f"K1→K2 path variation ≤ {v:g} ATR", le("path_variation_atr", v)) for v in (1.50, 3.00, 5.00, 8.00)]
    )
    families.append(
        [Arm("wrong_side_closes", f"wrong_{v}", f"intermediate wrong-side closes ≤ {v}", le("wrong_side_close_count", float(v))) for v in (0, 1, 2)]
    )
    families.append(
        [Arm("intermediate_colour", f"colour_share_{v:g}", f"intermediate MA-side colour share ≥ {v:g}", ge("intermediate_ma_colour_share", v)) for v in (0.50, 0.75, 1.00)]
    )
    families.append(
        [
            Arm("volume_relation", "k2_volume_lower", "K2 volume < K1 volume", le("k2_to_k1_volume_ratio", 1.0)),
            Arm("volume_relation", "k2_volume_similar", "K2/K1 volume 0.5–1.5", lambda f: f["k2_to_k1_volume_ratio"].between(0.5, 1.5)),
            Arm("volume_relation", "k2_volume_higher", "K2 volume ≥ K1 volume", ge("k2_to_k1_volume_ratio", 1.0)),
        ]
    )
    families.append(
        [
            Arm("ma_shift_candle_colour", "both_ma_colour", "K1 and K2 ChartPrime candle colours side-aligned", lambda f: f["k1_ma_colour_aligned"].astype(bool) & f["k2_ma_colour_aligned"].astype(bool)),
            Arm("ma_shift_candle_colour", "k2_ma_colour", "K2 ChartPrime candle colour side-aligned", lambda f: f["k2_ma_colour_aligned"].astype(bool)),
        ]
    )
    families.append(
        [
            Arm("ma_shift_oscillator", "k2_osc_sign", "K2 oscillator sign side-aligned", lambda f: f["k2_osc_sign_aligned"].astype(bool)),
            Arm("ma_shift_oscillator", "k2_osc_accel", "K2 oscillator acceleration side-aligned", lambda f: f["k2_osc_accel_aligned"].astype(bool)),
            Arm("ma_shift_oscillator", "both_osc_sign", "K1 and K2 oscillator signs side-aligned", lambda f: f["k1_osc_sign_aligned"].astype(bool) & f["k2_osc_sign_aligned"].astype(bool)),
        ]
    )
    families.append(
        [
            Arm("market_break", "structure_not_opposite", "confirmed 10/10 structure is not opposite at K2", lambda f: f["k2_structure_not_opposite"].astype(bool)),
            Arm("market_break", "structure_aligned", "confirmed 10/10 structure is side-aligned at K2", lambda f: f["k2_structure_aligned"].astype(bool)),
            Arm("market_break", "break_event", "K2 itself closes through confirmed pivot", lambda f: f["k2_side_break_event"].astype(bool)),
        ]
    )
    families.append(
        [Arm("ma_alignment", f"align_{v}", f"six-MA side alignment ≥ {v}/12", ge("side_ma_alignment", float(v))) for v in (6, 8, 10, 12)]
    )
    families.append(
        [Arm("rope_slope", f"slope_{v:g}", f"side-aligned rope slope ≥ {v:g} ATR/bar", ge("rope_slope_side_atr", v)) for v in (0.0, 0.01, 0.03, 0.05)]
    )
    families.append(
        [Arm("prior_compression", f"compression_{v:g}", f"prior20 mean rope width ≤ {v:g} ATR", le("prior_rope_width_atr_20", v)) for v in (0.25, 0.50, 0.75, 1.00)]
    )
    families.append(
        [
            Arm("time_block", f"utc_block_{start}_{start+6}", f"K2 UTC hour {start:02d}:00–{start+6:02d}:00", lambda f, start=start: f["utc_hour"].between(start, start + 5))
            for start in (0, 6, 12, 18)
        ]
    )
    families.append(
        [
            Arm("weekpart", "weekday", "K2 Monday–Friday", lambda f: f["weekday"].lt(5)),
            Arm("weekpart", "weekend", "K2 Saturday–Sunday", lambda f: f["weekday"].ge(5)),
        ]
    )
    return families


def combined_mask(frame: pd.DataFrame, selected: Sequence[Arm], extra: Arm | None = None) -> pd.Series:
    mask = pd.Series(True, index=frame.index, dtype=bool)
    for arm in selected:
        mask &= arm.predicate(frame).fillna(False).astype(bool)
    if extra is not None:
        mask &= extra.predicate(frame).fillna(False).astype(bool)
    return mask


def select_independent_events(frame: pd.DataFrame, mask: pd.Series, cooldown: int) -> pd.DataFrame:
    """Resolve multiple K1 matches and repeated K2s without future outcomes."""

    current = frame.loc[mask].copy()
    if current.empty:
        return current
    current = current.sort_values(
        ["symbol", "side", "k2_i", "k1_quality", "gap_bars"],
        ascending=[True, True, True, False, True],
    ).drop_duplicates(["symbol", "side", "k2_i"], keep="first")
    # The owner's wording is one later K2 for one K1: keep the earliest K2
    # surviving the current gate. This is known at the K2 decision bar.
    current = current.sort_values(["symbol", "side", "k1_i", "k2_i"]).drop_duplicates(
        ["symbol", "side", "k1_i"], keep="first"
    )
    kept: list[int] = []
    for _, group in current.sort_values(["symbol", "k2_i", "side"]).groupby("symbol", sort=False):
        last_i = -10**12
        for index, row in group.iterrows():
            if int(row["k2_i"]) - last_i >= cooldown:
                kept.append(int(index))
                last_i = int(row["k2_i"])
    return current.loc[kept].sort_values(["k2_time", "symbol", "side"]).reset_index(drop=True)


def half_label(times: pd.Series) -> pd.Series:
    values = pd.to_datetime(times, utc=True)
    return values.dt.year.astype(str) + "H" + np.where(values.dt.month.le(6), "1", "2")


def profit_factor(values: Iterable[float]) -> float:
    array = np.asarray(list(values), dtype=float)
    gains = float(array[array > 0.0].sum())
    losses = float(-array[array < 0.0].sum())
    if losses <= 0.0:
        return float("inf") if gains > 0.0 else float("nan")
    return gains / losses


def summarize_events(events: pd.DataFrame, folds: Sequence[str] | None = None) -> dict[str, Any]:
    if events.empty:
        return {
            "n_events": 0,
            "n_long": 0,
            "n_short": 0,
            "mean_net_bp": float("nan"),
            "control_net_bp": float("nan"),
            "paired_excess_bp": float("nan"),
            "win_rate": float("nan"),
            "profit_factor": float("nan"),
            "robust_score": float("-inf"),
            "worst_fold_excess_bp": float("-inf"),
            "positive_folds": 0,
            "fold_count": 0,
            "min_fold_n": 0,
        }
    current = events.copy()
    current["fold"] = half_label(current["k2_time"])
    fold_names = list(folds) if folds is not None else sorted(current["fold"].unique())
    fold_rows: list[dict[str, Any]] = []
    for fold in fold_names:
        group = current[current["fold"].eq(fold)]
        if group.empty:
            fold_rows.append({"fold": fold, "n": 0, "paired_excess_bp": float("nan")})
            continue
        fold_rows.append(
            {
                "fold": fold,
                "n": int(len(group)),
                "paired_excess_bp": float(group["paired_excess_24"].mean() * 1e4),
            }
        )
    valid_fold_excess = np.asarray(
        [row["paired_excess_bp"] for row in fold_rows if np.isfinite(row["paired_excess_bp"])],
        dtype=float,
    )
    robust = (
        float(np.median(valid_fold_excess) - 0.5 * np.std(valid_fold_excess, ddof=0))
        if len(valid_fold_excess)
        else float("-inf")
    )
    return {
        "n_events": int(len(current)),
        "n_long": int(current["side"].eq("long").sum()),
        "n_short": int(current["side"].eq("short").sum()),
        "mean_gross_bp": float(current["gross_return_24"].mean() * 1e4),
        "mean_net_bp": float(current["net_return_24"].mean() * 1e4),
        "control_net_bp": float(current["control_net_return_24"].mean() * 1e4),
        "paired_excess_bp": float(current["paired_excess_24"].mean() * 1e4),
        "win_rate": float(current["net_return_24"].gt(0.0).mean()),
        "stop_rate": float(current["stopped_24"].astype(bool).mean()),
        "profit_factor": float(profit_factor(current["net_return_24"])),
        "mean_terminal_r": float(current["terminal_r_24"].mean()),
        "median_mfe_r": float(current["mfe_r_24"].median()),
        "hit_1r_rate": float(current["hit_1r_24"].astype(bool).mean()),
        "hit_2r_rate": float(current["hit_2r_24"].astype(bool).mean()),
        "hit_3r_rate": float(current["hit_3r_24"].astype(bool).mean()),
        "hit_5r_rate": float(current["hit_5r_24"].astype(bool).mean()),
        "robust_score": robust,
        "worst_fold_excess_bp": float(np.min(valid_fold_excess)) if len(valid_fold_excess) else float("-inf"),
        "positive_folds": int(np.sum(valid_fold_excess > 0.0)),
        "fold_count": int(len(valid_fold_excess)),
        "min_fold_n": int(min(row["n"] for row in fold_rows)) if fold_rows else 0,
        "fold_metrics": fold_rows,
    }


def arm_is_eligible(summary: dict[str, Any], config: dict[str, Any]) -> bool:
    selection = config["selection"]
    return bool(
        int(summary["n_events"]) >= int(selection["minimum_events_total"])
        and int(summary["min_fold_n"]) >= int(selection["minimum_events_per_fold"])
        and int(summary["n_long"]) >= int(selection["minimum_events_per_side"])
        and int(summary["n_short"]) >= int(selection["minimum_events_per_side"])
        and int(summary["positive_folds"]) >= max(3, int(summary["fold_count"]) - 1)
    )


def greedy_search(
    discovery: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[list[Arm], pd.DataFrame, pd.DataFrame]:
    selected: list[Arm] = []
    trace_rows: list[dict[str, Any]] = []
    cooldown = int(config["event_cooldown_bars"])
    folds = list(config["selection"]["folds"])
    baseline_events = select_independent_events(
        discovery, combined_mask(discovery, selected), cooldown
    )
    current_summary = summarize_events(baseline_events, folds)
    trace_rows.append(
        {
            "step": 0,
            "family": "baseline",
            "arm": "broad_candidate",
            "description": "broad causal K1→K2 candidate",
            "eligible": arm_is_eligible(current_summary, config),
            "accepted": True,
            **{key: value for key, value in current_summary.items() if key != "fold_metrics"},
        }
    )
    min_improvement = float(config["selection"]["minimum_robust_score_improvement_bp"])
    for step, family in enumerate(family_arms(), start=1):
        candidates: list[tuple[Arm, dict[str, Any], pd.DataFrame]] = []
        for arm in family:
            events = select_independent_events(
                discovery, combined_mask(discovery, selected, arm), cooldown
            )
            summary = summarize_events(events, folds)
            eligible = arm_is_eligible(summary, config)
            candidates.append((arm, summary, events))
            trace_rows.append(
                {
                    "step": step,
                    "family": arm.family,
                    "arm": arm.name,
                    "description": arm.description,
                    "eligible": eligible,
                    "accepted": False,
                    **{key: value for key, value in summary.items() if key != "fold_metrics"},
                }
            )
        eligible_candidates = [item for item in candidates if arm_is_eligible(item[1], config)]
        if not eligible_candidates:
            continue
        best_arm, best_summary, _ = max(
            eligible_candidates,
            key=lambda item: (
                float(item[1]["robust_score"]),
                float(item[1]["worst_fold_excess_bp"]),
                int(item[1]["n_events"]),
            ),
        )
        improved = float(best_summary["robust_score"]) >= float(current_summary["robust_score"]) + min_improvement
        worst_not_materially_worse = float(best_summary["worst_fold_excess_bp"]) >= float(
            current_summary["worst_fold_excess_bp"]
        ) - 2.0
        if improved and worst_not_materially_worse:
            selected.append(best_arm)
            current_summary = best_summary
            for row in reversed(trace_rows):
                if row["family"] == best_arm.family and row["arm"] == best_arm.name:
                    row["accepted"] = True
                    break
    final_events = select_independent_events(
        discovery, combined_mask(discovery, selected), cooldown
    )
    return selected, pd.DataFrame(trace_rows), final_events


def quality_score(frame: pd.DataFrame) -> pd.Series:
    """Outcome-independent transparent ranking score for required decile tests."""

    components = pd.DataFrame(
        {
            "k1_body": frame["k1_body_ratio"].clip(0.0, 1.0),
            "k1_range": (frame["k1_range_atr"] / 2.0).clip(0.0, 1.0),
            "k1_cross": ((frame["k1_cross_depth_atr"] + 0.15) / 0.50).clip(0.0, 1.0),
            "k2_wick": frame["k2_wick_share"].clip(0.0, 1.0),
            "k2_reject": frame["k2_rejection_close_location"].clip(0.0, 1.0),
            "k2_reclaim": ((frame["k2_reclaim_atr"] + 0.10) / 0.60).clip(0.0, 1.0),
            "rope": (1.0 - frame["rope_width_atr"] / 1.5).clip(0.0, 1.0),
            "colour": (
                frame["k1_ma_colour_aligned"].astype(float)
                + frame["k2_ma_colour_aligned"].astype(float)
            )
            / 2.0,
            "path": np.exp(-frame["path_variation_atr"].clip(lower=0.0) / 4.0),
        }
    )
    return components.mean(axis=1)


def permutation_top_decile(scores: np.ndarray, gross: np.ndarray, seed: int) -> tuple[float, float, int]:
    valid = np.isfinite(scores) & np.isfinite(gross)
    scores = scores[valid]
    gross = gross[valid]
    if len(scores) < 10:
        return float("nan"), float("nan"), 0
    top_n = max(1, int(math.ceil(len(scores) * 0.10)))
    order = np.argsort(scores)[::-1]
    observed = float(np.mean(gross[order[:top_n]]))
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(PERMUTATIONS):
        shuffled = rng.permutation(gross)
        if float(np.mean(shuffled[order[:top_n]])) >= observed:
            extreme += 1
    return observed, (extreme + 1) / (PERMUTATIONS + 1), top_n


def signflip_p(values: Iterable[float], seed: int) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if len(array) < 2:
        return float("nan")
    observed = float(array.mean())
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(PERMUTATIONS):
        signs = rng.choice(np.asarray([-1.0, 1.0]), size=len(array))
        if float(np.mean(array * signs)) >= observed:
            extreme += 1
    return (extreme + 1) / (PERMUTATIONS + 1)


def clustered_ci(events: pd.DataFrame, column: str, seed: int) -> tuple[float, float]:
    if events.empty:
        return float("nan"), float("nan")
    work = events.copy()
    work["cluster"] = work["symbol"] + "|" + work["k2_time"].dt.strftime("%Y-%m")
    clusters = [group[column].to_numpy(dtype=float) for _, group in work.groupby("cluster")]
    if len(clusters) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    estimates = np.empty(BOOTSTRAPS, dtype=float)
    for i in range(BOOTSTRAPS):
        sampled = rng.integers(0, len(clusters), len(clusters))
        values = np.concatenate([clusters[index] for index in sampled])
        estimates[i] = float(np.nanmean(values))
    return tuple(float(value) for value in np.quantile(estimates, [0.025, 0.975]))


def extended_summary(events: pd.DataFrame, label: str, seed: int) -> dict[str, Any]:
    base = summarize_events(events)
    if events.empty:
        return {"segment": label, **base}
    work = events.copy()
    work["quality_score"] = quality_score(work)
    observed, permutation_p, top_n = permutation_top_decile(
        work["quality_score"].to_numpy(dtype=float),
        work["gross_return_24"].to_numpy(dtype=float),
        seed,
    )
    top = work.nlargest(top_n, "quality_score") if top_n else work.iloc[0:0]
    target = work["net_return_24"].gt(0.0).astype(int)
    auc = (
        float(roc_auc_score(target, work["quality_score"]))
        if target.nunique() == 2
        else float("nan")
    )
    ci_low, ci_high = clustered_ci(work, "paired_excess_24", seed + 100)
    single_observed, single_p, single_n = permutation_top_decile(
        work["k2_wick_share"].to_numpy(dtype=float),
        work["gross_return_24"].to_numpy(dtype=float),
        seed + 200,
    )
    return {
        "segment": label,
        **{key: value for key, value in base.items() if key != "fold_metrics"},
        "paired_signflip_p": signflip_p(work["paired_excess_24"], seed + 300),
        "paired_excess_ci95_low_bp": ci_low * 1e4,
        "paired_excess_ci95_high_bp": ci_high * 1e4,
        "quality_auc_net_positive": auc,
        "quality_top_decile_n": int(top_n),
        "quality_top_decile_gross_bp": observed * 1e4,
        "quality_top_decile_net_bp": (observed - 0.002) * 1e4,
        "quality_top_decile_control_net_bp": float(top["control_net_return_24"].mean() * 1e4)
        if len(top)
        else float("nan"),
        "quality_top_decile_permutation_p": permutation_p,
        "single_feature": "k2_wick_share",
        "single_feature_top_decile_n": int(single_n),
        "single_feature_top_decile_gross_bp": single_observed * 1e4,
        "single_feature_top_decile_net_bp": (single_observed - 0.002) * 1e4,
        "single_feature_top_decile_permutation_p": single_p,
    }


def describe_by_dimension(events: pd.DataFrame) -> pd.DataFrame:
    """Produce exact distance/colour/state response tables for the report."""

    rows: list[dict[str, Any]] = []
    if events.empty:
        return pd.DataFrame()
    work = events.copy()
    work["period"] = np.where(work["k2_time"].lt(pd.Timestamp("2025-01-01T00:00:00Z")), "development", "validation")
    dimension_values: dict[str, pd.Series] = {
        "gap_bars": work["gap_bars"].astype(str),
        "touch_depth_bin": pd.cut(work["k2_touch_depth_atr"], [-np.inf, 0, 0.5, 1.0, np.inf], labels=["miss/near", "0–0.5", "0.5–1.0", ">1.0"]).astype(str),
        "wick_share_bin": pd.cut(work["k2_wick_share"], [0, 0.3, 0.5, 0.7, 1.01], labels=["<0.3", "0.3–0.5", "0.5–0.7", ">=0.7"], include_lowest=True).astype(str),
        "path_variation_bin": pd.qcut(work["path_variation_atr"].rank(method="first"), 4, labels=["Q1", "Q2", "Q3", "Q4"]).astype(str),
        "ma_colour_pair": np.where(work["k1_ma_colour_aligned"].astype(bool) & work["k2_ma_colour_aligned"].astype(bool), "both aligned", "not both"),
        "osc_k2_sign": np.where(work["k2_osc_sign_aligned"].astype(bool), "aligned", "opposite/NA"),
        "market_break": np.where(work["k2_structure_aligned"].astype(bool), "aligned", np.where(work["k2_structure_not_opposite"].astype(bool), "neutral", "opposite")),
        "volume_relation": np.where(work["k2_to_k1_volume_ratio"].lt(0.8), "K2<K1×0.8", np.where(work["k2_to_k1_volume_ratio"].le(1.2), "similar", "K2>K1×1.2")),
    }
    for dimension, values in dimension_values.items():
        local = work.assign(_value=values)
        for (period, value), group in local.groupby(["period", "_value"], dropna=False, sort=False):
            rows.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "period": str(period),
                    "n": int(len(group)),
                    "net_bp": float(group["net_return_24"].mean() * 1e4),
                    "control_net_bp": float(group["control_net_return_24"].mean() * 1e4),
                    "paired_excess_bp": float(group["paired_excess_24"].mean() * 1e4),
                    "win_rate": float(group["net_return_24"].gt(0.0).mean()),
                    "hit_2r_rate": float(group["hit_2r_24"].astype(bool).mean()),
                }
            )
    return pd.DataFrame(rows)


def plot_gap_response(dimension: pd.DataFrame, output: Path) -> None:
    data = dimension[dimension["dimension"].eq("gap_bars")].copy()
    if data.empty:
        return
    data["gap"] = pd.to_numeric(data["value"], errors="coerce")
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True, constrained_layout=True)
    palette = {"development": "#315A7D", "validation": "#D18B36"}
    for period, group in data.groupby("period"):
        group = group.sort_values("gap")
        axes[0].plot(
            group["gap"], group["paired_excess_bp"], marker="o", linewidth=2,
            label=period, color=palette.get(str(period), "#555555"),
        )
        axes[1].plot(
            group["gap"], group["n"], marker="o", linewidth=2,
            label=period, color=palette.get(str(period), "#555555"),
        )
    axes[0].axhline(0.0, color="#333333", linewidth=1)
    axes[0].set_ylabel("paired excess (bp/event)")
    axes[0].set_title("K1→K2 exact bar distance and matched-control excess")
    axes[0].legend(frameon=False)
    axes[1].set_ylabel("events")
    axes[1].set_xlabel("gap bars (1h)")
    axes[1].set_title("Event count by exact distance")
    for axis in axes:
        axis.grid(axis="y", color="#D9D9D9", linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=170, facecolor="white")
    plt.close(fig)


def plot_search_trace(trace: pd.DataFrame, output: Path) -> None:
    eligible = trace[(trace["family"].ne("baseline")) & trace["eligible"].astype(bool)].copy()
    if eligible.empty:
        return
    best = (
        eligible.sort_values(["family", "robust_score", "worst_fold_excess_bp"], ascending=[True, False, False])
        .drop_duplicates("family", keep="first")
        .sort_values("robust_score")
    )
    colours = np.where(best["accepted"].astype(bool), "#315A7D", "#B8B8B8")
    fig, axis = plt.subplots(figsize=(10, max(7, len(best) * 0.34)), constrained_layout=True)
    axis.barh(best["family"], best["robust_score"], color=colours, edgecolor="#333333", linewidth=0.5)
    axis.axvline(0.0, color="#333333", linewidth=1)
    axis.set_xlabel("development robust score (bp/event)")
    axis.set_title("Best admissible arm in each preordered feature family")
    axis.grid(axis="x", color="#D9D9D9", linewidth=0.7)
    axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=170, facecolor="white")
    plt.close(fig)


def plot_period_comparison(events_by_segment: dict[str, pd.DataFrame], output: Path) -> None:
    rows: list[dict[str, Any]] = []
    for segment, events in events_by_segment.items():
        if events.empty:
            continue
        work = events.copy()
        work["half"] = half_label(work["k2_time"])
        for half, group in work.groupby("half"):
            rows.append(
                {
                    "segment": segment,
                    "half": half,
                    "signal": group["net_return_24"].mean() * 1e4,
                    "control": group["control_net_return_24"].mean() * 1e4,
                    "n": len(group),
                }
            )
    data = pd.DataFrame(rows)
    if data.empty:
        return
    order = sorted(data["half"].unique())
    fig, axes = plt.subplots(len(events_by_segment), 1, figsize=(11, 3.5 * len(events_by_segment)), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for axis, (segment, group) in zip(axes, data.groupby("segment", sort=False)):
        group = group.set_index("half").reindex(order).reset_index()
        x = np.arange(len(group))
        width = 0.36
        axis.bar(x - width / 2, group["signal"], width, label="signal", color="#315A7D")
        axis.bar(x + width / 2, group["control"], width, label="matched control", color="#D9B36C")
        axis.axhline(0.0, color="#333333", linewidth=1)
        axis.set_xticks(x, group["half"])
        axis.set_ylabel("net bp/event")
        axis.set_title(f"{segment}: frozen rule by half-year")
        axis.legend(frameon=False)
        axis.grid(axis="y", color="#D9D9D9", linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=170, facecolor="white")
    plt.close(fig)


def plot_feature_response(dimension: pd.DataFrame, output: Path) -> None:
    wanted = ["touch_depth_bin", "wick_share_bin", "ma_colour_pair", "market_break", "volume_relation"]
    data = dimension[dimension["dimension"].isin(wanted)].copy()
    if data.empty:
        return
    fig, axes = plt.subplots(len(wanted), 1, figsize=(11, 3.2 * len(wanted)), constrained_layout=True)
    colours = {"development": "#315A7D", "validation": "#D18B36"}
    for axis, dimension_name in zip(axes, wanted):
        group = data[data["dimension"].eq(dimension_name)].copy()
        values = list(dict.fromkeys(group["value"].tolist()))
        x = np.arange(len(values))
        width = 0.36
        for offset, period in enumerate(("development", "validation")):
            current = group[group["period"].eq(period)].set_index("value").reindex(values)
            axis.bar(
                x + (offset - 0.5) * width,
                current["paired_excess_bp"],
                width,
                label=period,
                color=colours[period],
            )
        axis.axhline(0.0, color="#333333", linewidth=1)
        axis.set_xticks(x, values)
        axis.set_ylabel("paired excess bp")
        axis.set_title(dimension_name)
        axis.grid(axis="y", color="#D9D9D9", linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False)
    fig.savefig(output, dpi=170, facecolor="white")
    plt.close(fig)


def serialize_arm(arm: Arm) -> dict[str, str]:
    return {"family": arm.family, "arm": arm.name, "description": arm.description}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-symbols", type=int, default=0, help="Debug-only alphabetical symbol limit")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    config = load_config()
    holdout = pd.Timestamp(config["holdout_start"])
    safe_end = pd.Timestamp(config["safe_end_exclusive"])
    if not safe_end < holdout:
        raise RuntimeError("safe_end_exclusive must remain before holdout_start")
    paths = sorted(PROJECT.glob(config["data_glob"]))
    if args.limit_symbols:
        paths = paths[: args.limit_symbols]
    if not paths:
        raise FileNotFoundError(f"no files match {config['data_glob']}")
    OUT.mkdir(parents=True, exist_ok=True)
    all_pairs: list[pd.DataFrame] = []
    all_controls: list[pd.DataFrame] = []
    quality_rows: list[dict[str, Any]] = []
    for position, path in enumerate(paths, start=1):
        symbol = symbol_from_path(path)
        pairs, controls, quality = process_symbol(path, config)
        all_pairs.append(pairs)
        all_controls.append(controls)
        quality_rows.append(quality)
        print(
            f"[{position:02d}/{len(paths):02d}] {symbol}: "
            f"{quality.get('hourly_rows', 0)} hourly, {quality.get('broad_pairs', 0)} pairs"
        )
    pairs = pd.concat(all_pairs, ignore_index=True) if all_pairs else pd.DataFrame()
    controls = pd.concat(all_controls, ignore_index=True) if all_controls else pd.DataFrame()
    quality = pd.DataFrame(quality_rows)
    if pairs.empty:
        raise RuntimeError("broad candidate generation returned no K1→K2 pairs")
    pairs["k1_time"] = pd.to_datetime(pairs["k1_time"], utc=True)
    pairs["k2_time"] = pd.to_datetime(pairs["k2_time"], utc=True)
    development_start = pd.Timestamp(config["development_start"])
    development_end = pd.Timestamp(config["development_end_exclusive"])
    validation_start = pd.Timestamp(config["validation_start"])
    transfer_symbol = str(config["transfer_symbol"])
    selection_universe = pairs["symbol"].ne(transfer_symbol)
    discovery = pairs[
        selection_universe
        & pairs["k2_time"].ge(development_start)
        & pairs["k2_time"].lt(development_end)
    ].copy()
    validation = pairs[
        selection_universe
        & pairs["k2_time"].ge(validation_start)
        & pairs["k2_time"].lt(safe_end)
    ].copy()
    btc_transfer = pairs[
        pairs["symbol"].eq(transfer_symbol)
        & pairs["k2_time"].ge(development_start)
        & pairs["k2_time"].lt(safe_end)
    ].copy()
    if discovery.empty or validation.empty or btc_transfer.empty:
        raise RuntimeError(
            f"required segment empty: discovery={len(discovery)}, "
            f"validation={len(validation)}, BTC={len(btc_transfer)}"
        )
    selected, trace, discovery_events = greedy_search(discovery, config)
    final_mask_validation = combined_mask(validation, selected)
    final_mask_btc = combined_mask(btc_transfer, selected)
    validation_events = select_independent_events(
        validation, final_mask_validation, int(config["event_cooldown_bars"])
    )
    btc_events = select_independent_events(
        btc_transfer, final_mask_btc, int(config["event_cooldown_bars"])
    )
    discovery_events["segment"] = "development_non_btc"
    validation_events["segment"] = "validation_non_btc"
    btc_events["segment"] = "btc_transfer_preholdout"
    selected_events = pd.concat(
        [discovery_events, validation_events, btc_events], ignore_index=True
    )

    summary_rows = [
        extended_summary(discovery_events, "development_non_btc", 2026090401),
        extended_summary(validation_events, "validation_non_btc", 2026090402),
        extended_summary(btc_events, "btc_transfer_preholdout", 2026090403),
    ]
    summary_table = pd.DataFrame(summary_rows)

    # Horizon sensitivity is descriptive only; it never changes selected arms.
    horizon_rows: list[dict[str, Any]] = []
    for segment, events in (
        ("development_non_btc", discovery_events),
        ("validation_non_btc", validation_events),
        ("btc_transfer_preholdout", btc_events),
    ):
        for horizon in HORIZONS:
            horizon_rows.append(
                {
                    "segment": segment,
                    "horizon_bars": horizon,
                    "n": int(len(events)),
                    "gross_bp": float(events[f"gross_return_{horizon}"].mean() * 1e4) if len(events) else float("nan"),
                    "net_bp": float(events[f"net_return_{horizon}"].mean() * 1e4) if len(events) else float("nan"),
                    "control_net_bp": float(events[f"control_net_return_{horizon}"].mean() * 1e4) if len(events) else float("nan"),
                    "paired_excess_bp": float(events[f"paired_excess_{horizon}"].mean() * 1e4) if len(events) else float("nan"),
                }
            )
    horizon_table = pd.DataFrame(horizon_rows)
    dimensions = describe_by_dimension(pd.concat([discovery_events, validation_events], ignore_index=True))

    # Leave-one-selected-gate-out on validation is a stability diagnostic, not retuning.
    perturb_rows: list[dict[str, Any]] = []
    validation_summary = summarize_events(validation_events)
    perturb_rows.append(
        {
            "variant": "frozen_final",
            "removed_family": None,
            **{key: value for key, value in validation_summary.items() if key != "fold_metrics"},
        }
    )
    for removed in selected:
        remaining = [arm for arm in selected if arm is not removed]
        events = select_independent_events(
            validation,
            combined_mask(validation, remaining),
            int(config["event_cooldown_bars"]),
        )
        diagnostic = summarize_events(events)
        perturb_rows.append(
            {
                "variant": f"remove_{removed.family}",
                "removed_family": removed.family,
                **{key: value for key, value in diagnostic.items() if key != "fold_metrics"},
            }
        )
    perturb = pd.DataFrame(perturb_rows)

    # Save auditable results. Candidate pairs stay compressed because they are
    # feature evidence, not a training dataset.
    quality.to_csv(OUT / "data_quality.csv", index=False)
    trace.to_csv(OUT / "search_trace.csv", index=False)
    selected_events.to_csv(OUT / "selected_events.csv.gz", index=False, compression="gzip")
    controls.to_csv(OUT / "matched_controls.csv.gz", index=False, compression="gzip")
    summary_table.to_csv(OUT / "summary.csv", index=False)
    horizon_table.to_csv(OUT / "horizon_sensitivity.csv", index=False)
    dimensions.to_csv(OUT / "dimension_response.csv", index=False)
    perturb.to_csv(OUT / "leave_one_gate_out.csv", index=False)
    pair_sample = pairs.sort_values(["k2_time", "symbol", "side", "k1_i"]).copy()
    pair_sample.to_csv(OUT / "broad_pairs.csv.gz", index=False, compression="gzip")
    if not args.no_plots:
        plot_gap_response(dimensions, OUT / "gap_response.png")
        plot_search_trace(trace, OUT / "search_trace.png")
        plot_period_comparison(
            {
                "non-BTC development": discovery_events,
                "non-BTC validation": validation_events,
                "BTC transfer": btc_events,
            },
            OUT / "halfyear_signal_vs_control.png",
        )
        plot_feature_response(dimensions, OUT / "feature_response.png")

    payload = {
        "experiment_id": config["experiment_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "preholdout_rule_research",
        "holdout_consumed": False,
        "training_eligible": False,
        "production_eligible": False,
        "script": {
            "path": str(Path(__file__).resolve().relative_to(PROJECT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "config": {"path": str(CONFIG_PATH.relative_to(PROJECT)), "sha256": sha256_file(CONFIG_PATH)},
        "data": {
            "files": int(len(paths)),
            "symbols": int(quality["symbol"].nunique()),
            "hourly_rows": int(quality["hourly_rows"].sum()),
            "first_hour": quality["first_hour"].dropna().min(),
            "last_hour": quality["last_hour"].dropna().max(),
            "hourly_gap_count": int(quality["hourly_gap_count"].sum()),
            "broad_pairs": int(len(pairs)),
            "broad_k2_events": int(pairs[["symbol", "direction", "k2_i"]].drop_duplicates().shape[0]),
            "safe_end_exclusive": config["safe_end_exclusive"],
            "holdout_start": config["holdout_start"],
        },
        "selection": {
            "selected_arms": [serialize_arm(arm) for arm in selected],
            "selection_rows": int(len(discovery)),
            "frozen_before_validation": True,
            "validation_retuning": False,
        },
        "summary": summary_rows,
        "horizon_sensitivity": horizon_rows,
        "caveats": [
            "The 54-symbol cache is a current survival universe, not a historical listing snapshot.",
            "A 1h resample is an auditable OKX reconstruction, not TradingView feed parity.",
            "Matched controls copy stop distance in ATR; they do not manufacture a random K2 wick.",
            "The ChartPrime Market Break state is a causal public-formula approximation; indicator plot order can still change visible candle colour.",
            "Validation is pre-holdout research and BTC transfer, not final holdout or fresh-forward evidence.",
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps({"selected": payload["selection"], "summary": summary_rows}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
