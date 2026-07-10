"""Rule-based SMA/EMA 20/60/120 dense-cluster candidates on 15m bars.

The judgment layer shares one MA definition with the detection layer:
SMA/EMA 20 and 60 form the fast bundle, while SMA/EMA 120 are the slow
anchors. Every metric is causal and uses only the signal bar or earlier bars.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

MA_PERIODS = (20, 60, 120)
SMA_COLS = tuple(f"sma{period}" for period in MA_PERIODS)
EMA_COLS = tuple(f"ema{period}" for period in MA_PERIODS)
FAST_MA_COLS = ("sma20", "ema20", "sma60", "ema60")
SLOW_MA_COLS = ("sma120", "ema120")
ALL_MA_COLS = (*FAST_MA_COLS, *SLOW_MA_COLS)

WARMUP_BARS = 288
MIN_GAP_BARS = 18
MIN_DENSE_BARS = 5

STRICT_THRESHOLDS = {
    "fast_spread_max": 0.0028,
    "full_spread_max": 0.0055,
    "fast_slow_gap_max": 0.0035,
    "full_ratio_min48_max": 1.45,
    "pre_range48_max": 0.032,
    "pre_range168_max": 0.075,
    "drawdown24_max": 0.007,
    "ext_up_min": -0.0015,
    "ext_up_max": 0.0075,
    "order_score_min": 3,
    "slow_slope_abs_max": 0.0009,
    "zero_volume96_max": 0.02,
    "volume_ratio_min": 0.7,
}

EXPANDED_THRESHOLDS = {
    "fast_spread_max": 0.00448,
    "full_spread_max": 0.0088,
    "fast_slow_gap_max": 0.0056,
    "full_ratio_min48_max": 1.80,
    "pre_range48_max": 0.048,
    "pre_range168_max": 0.110,
    "drawdown24_max": 0.012,
    "ext_up_min": -0.0030,
    "ext_up_max": 0.0120,
    "order_score_min": 3,
    "slow_slope_abs_max": 0.0015,
    "zero_volume96_max": 0.02,
    "volume_ratio_min": 0.5,
}

THRESHOLD_PRESETS = {"strict": STRICT_THRESHOLDS, "expanded": EXPANDED_THRESHOLDS}


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal SMA/EMA 20/60/120, ATR, volume and dense-rule metrics."""
    out = frame.copy()
    close = out["close"].replace(0, np.nan)
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    for period in MA_PERIODS:
        out[f"sma{period}"] = out["close"].rolling(period, min_periods=period).mean()
        out[f"ema{period}"] = out["close"].ewm(span=period, adjust=False).mean()

    previous_close = out["close"].shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    out["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    out["atr_pct"] = out["atr14"] / close

    volume_mean = volume.rolling(96, min_periods=20).mean()
    volume_std = volume.rolling(96, min_periods=20).std().replace(0, np.nan)
    out["volume_z"] = ((volume - volume_mean) / volume_std).fillna(0)
    volume_base = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    out["volume_ratio"] = (volume / volume_base).fillna(0)

    out["cluster_max"] = out[list(FAST_MA_COLS)].max(axis=1)
    out["cluster_min"] = out[list(FAST_MA_COLS)].min(axis=1)
    out["ma_spread_pct"] = (out["cluster_max"] - out["cluster_min"]) / close
    out["fast_spread"] = out["ma_spread_pct"]
    out["full_spread"] = (out[list(ALL_MA_COLS)].max(axis=1) - out[list(ALL_MA_COLS)].min(axis=1)) / close
    fast_mid = (out["cluster_max"] + out["cluster_min"]) / 2
    slow_mid = (out["sma120"] + out["ema120"]) / 2
    out["fast_slow_gap"] = (fast_mid - slow_mid).abs() / close
    out["full_min48"] = out["full_spread"].rolling(48, min_periods=24).min()
    out["full_ratio_min48"] = out["full_spread"] / out["full_min48"].replace(0, np.nan)
    out["pre_range48"] = (high.rolling(48).max() - low.rolling(48).min()) / close
    out["pre_range168"] = (high.rolling(168).max() - low.rolling(168).min()) / close
    out["drawdown24"] = high.rolling(24).max() / close - 1
    out["runup24"] = close / low.rolling(24).min().replace(0, np.nan) - 1
    out["ext_up"] = close / out["cluster_max"].replace(0, np.nan) - 1
    out["ext_down"] = out["cluster_min"] / close - 1
    out["slow_slope_12"] = out["ema120"].pct_change(12)
    out["slow_slope_abs"] = out["slow_slope_12"].abs()
    out["zero_volume96"] = (volume <= 0).rolling(96).mean()
    out["order_score"] = (
        (out["sma20"] >= out["sma60"]).astype(int)
        + (out["ema20"] >= out["ema60"]).astype(int)
        + (out["sma60"] >= out["sma120"]).astype(int)
        + (out["ema60"] >= out["ema120"]).astype(int)
    )
    out["down_order_score"] = (
        (out["sma20"] <= out["sma60"]).astype(int)
        + (out["ema20"] <= out["ema60"]).astype(int)
        + (out["sma60"] <= out["sma120"]).astype(int)
        + (out["ema60"] <= out["ema120"]).astype(int)
    )
    out["trend_order_score"] = out[["order_score", "down_order_score"]].max(axis=1)

    for mode, thresholds in THRESHOLD_PRESETS.items():
        dense = (
            (out["fast_spread"] <= thresholds["fast_spread_max"])
            & (out["full_spread"] <= thresholds["full_spread_max"])
        ).astype(int)
        out[f"is_dense_{mode}"] = dense
        dense_group = (dense == 0).cumsum()
        out[f"dense_run_len_{mode}"] = dense.groupby(dense_group).cumsum()

    out["shape_score"] = _shape_score(out)
    out["short_shape_score"] = out["shape_score"]
    return out.replace([np.inf, -np.inf], np.nan)


def _shape_score(enriched: pd.DataFrame) -> pd.Series:
    return (
        (EXPANDED_THRESHOLDS["fast_spread_max"] - enriched["fast_spread"]).clip(lower=0) * 9000
        + (EXPANDED_THRESHOLDS["full_spread_max"] - enriched["full_spread"]).clip(lower=0) * 6500
        + (STRICT_THRESHOLDS["full_ratio_min48_max"] - enriched["full_ratio_min48"]).clip(lower=0) * 8
        + enriched["volume_ratio"].clip(upper=4)
    )


def strict_mask(enriched: pd.DataFrame, mode: str = "strict") -> pd.Series:
    """Return the long-candidate mask for a named frozen threshold preset."""
    thresholds = THRESHOLD_PRESETS[mode]
    return (
        (enriched[f"dense_run_len_{mode}"] >= MIN_DENSE_BARS)
        & (enriched["full_ratio_min48"] <= thresholds["full_ratio_min48_max"])
        & (enriched["pre_range48"] <= thresholds["pre_range48_max"])
        & (enriched["pre_range168"] <= thresholds["pre_range168_max"])
        & (enriched["drawdown24"] <= thresholds["drawdown24_max"])
        & enriched["ext_up"].between(thresholds["ext_up_min"], thresholds["ext_up_max"])
        & (enriched["order_score"] >= thresholds["order_score_min"])
        & (enriched["slow_slope_abs"] <= thresholds["slow_slope_abs_max"])
        & (enriched["zero_volume96"] <= thresholds["zero_volume96_max"])
        & (enriched["volume_ratio"] >= thresholds["volume_ratio_min"])
    )


def short_mask(enriched: pd.DataFrame, mode: str = "strict") -> pd.Series:
    """Return the short-candidate mask for a named frozen threshold preset."""
    thresholds = THRESHOLD_PRESETS[mode]
    return (
        (enriched[f"dense_run_len_{mode}"] >= MIN_DENSE_BARS)
        & (enriched["full_ratio_min48"] <= thresholds["full_ratio_min48_max"])
        & (enriched["pre_range48"] <= thresholds["pre_range48_max"])
        & (enriched["pre_range168"] <= thresholds["pre_range168_max"])
        & (enriched["runup24"] <= thresholds["drawdown24_max"])
        & enriched["ext_down"].between(thresholds["ext_up_min"], thresholds["ext_up_max"])
        & (enriched["down_order_score"] >= thresholds["order_score_min"])
        & (enriched["slow_slope_abs"] <= thresholds["slow_slope_abs_max"])
        & (enriched["zero_volume96"] <= thresholds["zero_volume96_max"])
        & (enriched["volume_ratio"] >= thresholds["volume_ratio_min"])
    )


def scan_candidates(
    enriched: pd.DataFrame, *, horizon_bars: int = 72, mode: str = "strict"
) -> list[int]:
    """Return deduplicated long candidate positions with a complete label horizon."""
    return _scan(enriched, strict_mask(enriched, mode), horizon_bars)


def scan_short_candidates(
    enriched: pd.DataFrame, *, horizon_bars: int = 72, mode: str = "strict"
) -> list[int]:
    """Return deduplicated short candidate positions with a complete label horizon."""
    return _scan(enriched, short_mask(enriched, mode), horizon_bars)


def causal_gap_dedupe(
    indices: Iterable[int],
    *,
    min_gap_bars: int = MIN_GAP_BARS,
) -> list[int]:
    """Keep the earliest signal in each gap so future rows cannot rewrite history."""
    selected: list[int] = []
    for index in sorted(int(item) for item in indices):
        if not selected or index - selected[-1] >= min_gap_bars:
            selected.append(index)
    return selected


def _scan(enriched: pd.DataFrame, mask: pd.Series, horizon_bars: int) -> list[int]:
    if len(enriched) < WARMUP_BARS + horizon_bars + 2:
        return []
    indices = np.flatnonzero(mask.fillna(False).to_numpy())
    indices = indices[(indices >= WARMUP_BARS) & (indices + horizon_bars + 1 < len(enriched))]
    if len(indices) == 0:
        return []
    return causal_gap_dedupe(indices)
