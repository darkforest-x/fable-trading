"""20/60/120 MA candidate pool for the P0-3 comparison experiment.

This module is additive: it does not change the validated 8/13/21/34/55
judgment path. The dense rule is aligned with the detection layer's
SMA/EMA 20/60/120 setup:
  - fast_spread uses SMA/EMA 20 and 60,
  - full_spread uses SMA/EMA 20/60/120,
  - a signal bar must be inside a dense run of at least five bars.

All indicators and features use only bars at or before the signal bar. Labels
remain owned by src.judgment.labeling and may look forward by design.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.judgment.candidates import (
    EXPANDED_THRESHOLDS,
    MIN_GAP_BARS,
    STRICT_THRESHOLDS,
    WARMUP_BARS,
)
from src.judgment.features import FEATURE_COLUMNS

MA_PERIODS = (20, 60, 120)
FAST_MA_COLS = ("sma20", "ema20", "sma60", "ema60")
SLOW_MA_COLS = ("sma120", "ema120")
ALL_MA_COLS = (*FAST_MA_COLS, *SLOW_MA_COLS)
MIN_DENSE_BARS = 5

V206_THRESHOLDS = {
    **EXPANDED_THRESHOLDS,
    "fast_spread_max": STRICT_THRESHOLDS["fast_spread_max"] * 1.6,
    "full_spread_max": STRICT_THRESHOLDS["full_spread_max"] * 1.6,
}


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Add SMA/EMA 20/60/120 metrics plus reused causal market filters.

    Columns used: open/high/low/close/volume. Rolling windows are 20/48/96/120/168
    bars; all are trailing windows ending at the current bar.
    """
    out = frame.copy()
    close = out["close"].replace(0, np.nan)
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    for period in MA_PERIODS:
        out[f"sma{period}"] = out["close"].rolling(period, min_periods=period).mean()
        out[f"ema{period}"] = out["close"].ewm(span=period, adjust=False).mean()

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    out["atr_pct"] = out["atr14"] / close

    vol_mean = volume.rolling(96, min_periods=20).mean()
    vol_std = volume.rolling(96, min_periods=20).std().replace(0, np.nan)
    out["volume_z"] = ((volume - vol_mean) / vol_std).fillna(0)
    vol_ratio_base = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    out["volume_ratio"] = (volume / vol_ratio_base).fillna(0)

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
    out["ext_up"] = close / out["cluster_max"].replace(0, np.nan) - 1
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
    out["is_dense_v206"] = (
        (out["fast_spread"] <= V206_THRESHOLDS["fast_spread_max"])
        & (out["full_spread"] <= V206_THRESHOLDS["full_spread_max"])
    ).astype(int)
    dense_group = (out["is_dense_v206"] == 0).cumsum()
    out["dense_run_len_v206"] = out["is_dense_v206"].groupby(dense_group).cumsum()
    out["shape_score"] = (
        (V206_THRESHOLDS["fast_spread_max"] - out["fast_spread"]).clip(lower=0) * 9000
        + (V206_THRESHOLDS["full_spread_max"] - out["full_spread"]).clip(lower=0) * 6500
        + (1.45 - out["full_ratio_min48"]).clip(lower=0) * 8
        + out["volume_ratio"].clip(upper=4)
    )
    return out.replace([np.inf, -np.inf], np.nan)


def candidate_mask(enriched: pd.DataFrame) -> pd.Series:
    t = V206_THRESHOLDS
    return (
        (enriched["dense_run_len_v206"] >= MIN_DENSE_BARS)
        & (enriched["full_ratio_min48"] <= t["full_ratio_min48_max"])
        & (enriched["pre_range48"] <= t["pre_range48_max"])
        & (enriched["pre_range168"] <= t["pre_range168_max"])
        & (enriched["drawdown24"] <= t["drawdown24_max"])
        & (enriched["ext_up"].between(t["ext_up_min"], t["ext_up_max"]))
        & (enriched["order_score"] >= t["order_score_min"])
        & (enriched["slow_slope_abs"] <= t["slow_slope_abs_max"])
        & (enriched["zero_volume96"] <= t["zero_volume96_max"])
        & (enriched["volume_ratio"] >= t["volume_ratio_min"])
    )


def scan_candidates(enriched: pd.DataFrame, *, horizon_bars: int = 72) -> list[int]:
    if len(enriched) < WARMUP_BARS + horizon_bars + 2:
        return []
    mask = candidate_mask(enriched).fillna(False)
    idx = np.flatnonzero(mask.to_numpy())
    idx = idx[(idx >= WARMUP_BARS) & (idx + horizon_bars + 1 < len(enriched))]
    if len(idx) == 0:
        return []
    scores = enriched["shape_score"].to_numpy()
    selected: list[int] = []
    for i in sorted(idx, key=lambda j: scores[j], reverse=True):
        if all(abs(i - prev) >= MIN_GAP_BARS for prev in selected):
            selected.append(int(i))
    return sorted(selected)


def add_features(enriched: pd.DataFrame) -> pd.DataFrame:
    """Add train.py-compatible features from causal MA, volume, ATR and return windows.

    Columns used: close/volume plus SMA/EMA 20/60/120 indicators. Windows are
    trailing 4/8/12/24/48/96 bars and never include future bars.
    """
    out = enriched.copy()
    close = out["close"].replace(0, np.nan)
    spread = out["ma_spread_pct"]

    out["spread_mean8"] = spread.rolling(8).mean()
    out["spread_mean24"] = spread.rolling(24).mean()
    out["spread_chg8"] = spread - spread.shift(8)
    out["spread_chg24"] = spread - spread.shift(24)
    roll_min = spread.rolling(96, min_periods=48).min()
    roll_max = spread.rolling(96, min_periods=48).max()
    out["spread_pos96"] = (spread - roll_min) / (roll_max - roll_min).replace(0, np.nan)

    dense = out["is_dense_v206"]
    dense_group = (dense == 0).cumsum()
    out["dense_run_len"] = dense.groupby(dense_group).cumsum()
    out["dense_frac48"] = dense.rolling(48, min_periods=24).mean()

    out["close_vs_ema55"] = close / out["ema60"].replace(0, np.nan) - 1
    out["close_vs_ema200"] = close / out["ema120"].replace(0, np.nan) - 1
    out["vol_ratio_mean8"] = out["volume_ratio"].rolling(8).mean()
    atr_mean96 = out["atr_pct"].rolling(96, min_periods=48).mean().replace(0, np.nan)
    out["atr_pct_ratio96"] = out["atr_pct"] / atr_mean96
    for n in (4, 12, 24, 48):
        out[f"ret_{n}"] = close.pct_change(n)
    return out.replace([np.inf, -np.inf], np.nan)


def extract_feature_rows(featured: pd.DataFrame, signal_indices: list[int]) -> pd.DataFrame:
    rows = featured.iloc[signal_indices]
    return rows[FEATURE_COLUMNS].reset_index(drop=True)
