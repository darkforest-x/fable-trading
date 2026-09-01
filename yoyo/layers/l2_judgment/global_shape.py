"""Causal 128-bar global-shape features for the post-YOLO quality layer.

The feature row at ``decision_i`` reads only the 128 chronological OHLCV rows
ending at that index plus the six SMA/EMA 20/60/120 values that are visible on
the detector chart.  Those moving averages are computed causally from closes
at or before each bar; no row after ``decision_i`` is read.  Directional values
are multiplied by +1 for LONG and -1 for SHORT so both side-specific models use
the same favourable/adverse coordinate convention.

This module deliberately contains no outcome or label construction.  Labels
may look forward in an experiment builder, but the model inputs defined here
cannot.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


GLOBAL_CONTEXT_BARS = 128
MA_PERIODS = (20, 60, 120)
MA_COLUMNS = tuple(
    column
    for period in MA_PERIODS
    for column in (f"sma{period}", f"ema{period}")
)

GLOBAL_SHAPE_FEATURE_COLUMNS = (
    "confirmation_bars",
    "ma_spread_atr_end",
    "ma_spread_atr_mean12",
    "ma_spread_atr_mean24",
    "ma_spread_atr_mean48",
    "ma_spread_atr_min24",
    "ma_spread_atr_change8",
    "ma_spread_atr_change24",
    "dense_fraction24",
    "dense_fraction48",
    "price_bundle_gap_atr",
    "atr_pct",
    "atr_ratio96",
    "range_atr24",
    "range_atr48",
    "range_atr96",
    "path_efficiency24",
    "path_efficiency48",
    "volume_ratio4",
    "volume_ratio24",
    "aligned_ret2",
    "aligned_ret4",
    "aligned_ret8",
    "aligned_ret12",
    "aligned_ret24",
    "aligned_ret48",
    "aligned_ret96",
    "aligned_ma_slope12_atr",
    "aligned_ma_slope24_atr",
    "aligned_ma_slope48_atr",
    "aligned_position24",
    "aligned_position48",
    "aligned_core_to_decision_atr",
    "aligned_ma_order_score",
    "max_body24_atr",
)


def add_global_shape_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Add only causal columns required by :func:`extract_global_shape_features`.

    Source columns are ``open/high/low/close/volume``.  SMA/EMA 20/60/120,
    Wilder-style ATR14 and trailing volume means use only the current and prior
    rows.  The returned frame keeps the original row positions.
    """

    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"global-shape frame missing columns: {missing}")
    out = frame.copy()
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if out[list(required)].isna().any().any():
        raise ValueError("global-shape frame contains invalid OHLCV values")

    close = out["close"].replace(0, np.nan)
    for period in MA_PERIODS:
        out[f"sma{period}"] = out["close"].rolling(period).mean()
        out[f"ema{period}"] = out["close"].ewm(span=period, adjust=False).mean()
    previous_close = out["close"].shift(1)
    true_range = pd.concat(
        (
            out["high"] - out["low"],
            (out["high"] - previous_close).abs(),
            (out["low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    out["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    out["atr_pct"] = out["atr14"] / close
    out["volume_mean4"] = out["volume"].rolling(4, min_periods=1).mean()
    out["volume_mean24"] = out["volume"].rolling(24, min_periods=4).mean()
    out["volume_mean96"] = out["volume"].rolling(96, min_periods=24).mean()
    out["ma_max"] = out[list(MA_COLUMNS)].max(axis=1)
    out["ma_min"] = out[list(MA_COLUMNS)].min(axis=1)
    out["ma_center"] = out[list(MA_COLUMNS)].mean(axis=1)
    out["ma_spread_atr"] = (out["ma_max"] - out["ma_min"]) / out["atr14"].replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def _mean_tail(series: pd.Series, bars: int) -> float:
    return float(series.iloc[-bars:].mean())


def _range_atr(window: pd.DataFrame, bars: int, atr: float) -> float:
    tail = window.iloc[-bars:]
    return float((tail["high"].max() - tail["low"].min()) / atr)


def _path_efficiency(close: pd.Series, bars: int) -> float:
    tail = close.iloc[-bars:]
    path = float(tail.diff().abs().sum())
    return 0.0 if path <= 0 else float(abs(tail.iloc[-1] - tail.iloc[0]) / path)


def _aligned_return(close: pd.Series, bars: int, sign: float) -> float:
    start = float(close.iloc[-bars - 1])
    end = float(close.iloc[-1])
    if start == 0:
        return float("nan")
    return float(sign * (end / start - 1.0))


def _aligned_position(window: pd.DataFrame, bars: int, atr: float, sign: float) -> float:
    tail = window.iloc[-bars:]
    mid = 0.5 * (float(tail["high"].max()) + float(tail["low"].min()))
    return float(sign * (float(tail["close"].iloc[-1]) - mid) / atr)


def extract_global_shape_features(
    enriched: pd.DataFrame,
    *,
    decision_i: int,
    core_end_i: int,
    side: str,
    confirmation_bars: int,
) -> dict[str, float]:
    """Extract a fixed causal vector ending at ``decision_i``.

    Exactly 128 rows ``[decision_i-127, decision_i]`` are inspected.  The
    core-to-decision displacement reads the close at ``core_end_i`` and is
    legal only when that index falls inside the same 128-row window.
    """

    side_key = str(side).strip().lower()
    if side_key not in {"long", "short"}:
        raise ValueError(f"side must be long|short, got {side!r}")
    sign = 1.0 if side_key == "long" else -1.0
    decision = int(decision_i)
    core_end = int(core_end_i)
    start = decision - GLOBAL_CONTEXT_BARS + 1
    if start < 0 or decision >= len(enriched):
        raise ValueError("insufficient 128-bar global context")
    if not start <= core_end <= decision:
        raise ValueError("core_end_i must lie inside the causal global window")
    window = enriched.iloc[start : decision + 1]
    if len(window) != GLOBAL_CONTEXT_BARS:
        raise AssertionError("global context length drifted")
    required = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "atr14",
        "atr_pct",
        "volume_mean4",
        "volume_mean24",
        "volume_mean96",
        "ma_center",
        "ma_spread_atr",
        *MA_COLUMNS,
    }
    missing = sorted(required - set(window.columns))
    if missing:
        raise ValueError(f"enriched global frame missing columns: {missing}")
    atr = float(window["atr14"].iloc[-1])
    close = window["close"]
    if not np.isfinite(atr) or atr <= 0 or window[list(MA_COLUMNS)].iloc[-1].isna().any():
        raise ValueError("non-finite ATR or moving averages at decision")
    spread = window["ma_spread_atr"]
    current_close = float(close.iloc[-1])
    current_center = float(window["ma_center"].iloc[-1])
    atr96 = float(window["atr14"].iloc[-96:].mean())
    volume96 = float(window["volume_mean96"].iloc[-1])
    core_close = float(enriched["close"].iloc[core_end])

    order_pairs = 0
    ma_values = [float(window[f"ema{period}"].iloc[-1]) for period in MA_PERIODS]
    for left, right in zip(ma_values, ma_values[1:]):
        order_pairs += int(sign * (left - right) >= 0)
    body = (window["close"] - window["open"]).abs()

    values: Mapping[str, float] = {
        "confirmation_bars": float(confirmation_bars),
        "ma_spread_atr_end": float(spread.iloc[-1]),
        "ma_spread_atr_mean12": _mean_tail(spread, 12),
        "ma_spread_atr_mean24": _mean_tail(spread, 24),
        "ma_spread_atr_mean48": _mean_tail(spread, 48),
        "ma_spread_atr_min24": float(spread.iloc[-24:].min()),
        "ma_spread_atr_change8": float(spread.iloc[-1] - spread.iloc[-9]),
        "ma_spread_atr_change24": float(spread.iloc[-1] - spread.iloc[-25]),
        "dense_fraction24": float((spread.iloc[-24:] <= 1.10).mean()),
        "dense_fraction48": float((spread.iloc[-48:] <= 1.10).mean()),
        "price_bundle_gap_atr": float(abs(current_close - current_center) / atr),
        "atr_pct": float(window["atr_pct"].iloc[-1]),
        "atr_ratio96": float(atr / atr96) if atr96 > 0 else float("nan"),
        "range_atr24": _range_atr(window, 24, atr),
        "range_atr48": _range_atr(window, 48, atr),
        "range_atr96": _range_atr(window, 96, atr),
        "path_efficiency24": _path_efficiency(close, 24),
        "path_efficiency48": _path_efficiency(close, 48),
        "volume_ratio4": float(window["volume_mean4"].iloc[-1] / volume96) if volume96 > 0 else float("nan"),
        "volume_ratio24": float(window["volume_mean24"].iloc[-1] / volume96) if volume96 > 0 else float("nan"),
        "aligned_ret2": _aligned_return(close, 2, sign),
        "aligned_ret4": _aligned_return(close, 4, sign),
        "aligned_ret8": _aligned_return(close, 8, sign),
        "aligned_ret12": _aligned_return(close, 12, sign),
        "aligned_ret24": _aligned_return(close, 24, sign),
        "aligned_ret48": _aligned_return(close, 48, sign),
        "aligned_ret96": _aligned_return(close, 96, sign),
        "aligned_ma_slope12_atr": float(sign * (current_center - window["ma_center"].iloc[-13]) / atr),
        "aligned_ma_slope24_atr": float(sign * (current_center - window["ma_center"].iloc[-25]) / atr),
        "aligned_ma_slope48_atr": float(sign * (current_center - window["ma_center"].iloc[-49]) / atr),
        "aligned_position24": _aligned_position(window, 24, atr, sign),
        "aligned_position48": _aligned_position(window, 48, atr, sign),
        "aligned_core_to_decision_atr": float(sign * (current_close - core_close) / atr),
        "aligned_ma_order_score": float(order_pairs),
        "max_body24_atr": float(body.iloc[-24:].max() / atr),
    }
    result = dict(values)
    if tuple(result) != GLOBAL_SHAPE_FEATURE_COLUMNS:
        raise AssertionError("global-shape feature order drifted")
    if not all(np.isfinite(float(value)) for value in result.values()):
        bad = [key for key, value in result.items() if not np.isfinite(float(value))]
        raise ValueError(f"non-finite global-shape features: {bad}")
    return result

