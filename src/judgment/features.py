"""Feature engineering for the judgment classifier.

All features are computed causally: at bar i only data from bars <= i is used
(rolling / shift / pct_change on past values only). Features are added as
columns on the enriched indicator frame from candidates.add_indicators, then
sampled at candidate positions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DENSE_SPREAD_MAX = 0.0028  # strict fast_spread threshold, reused for duration

FEATURE_COLUMNS = [
    # MA-spread level and convergence dynamics
    "ma_spread_pct",
    "full_spread",
    "fast_slow_gap",
    "full_ratio_min48",
    "spread_mean8",
    "spread_mean24",
    "spread_chg8",
    "spread_chg24",
    "spread_pos96",
    # dense-state persistence
    "dense_run_len",
    "dense_frac48",
    # price position relative to MAs
    "ext_up",
    "close_vs_ema55",
    "close_vs_ema200",
    "order_score",
    "slow_slope_12",
    # volume
    "volume_ratio",
    "volume_z",
    "vol_ratio_mean8",
    # volatility
    "atr_pct",
    "atr_pct_ratio96",
    "pre_range48",
    "pre_range168",
    "drawdown24",
    # recent momentum
    "ret_4",
    "ret_12",
    "ret_24",
    "ret_48",
]


def add_features(enriched: pd.DataFrame) -> pd.DataFrame:
    """Add FEATURE_COLUMNS to an indicator frame (causal only)."""
    out = enriched.copy()
    close = out["close"].replace(0, np.nan)
    spread = out["ma_spread_pct"]

    out["spread_mean8"] = spread.rolling(8).mean()
    out["spread_mean24"] = spread.rolling(24).mean()
    # convergence speed: negative = spread shrinking over the last N bars
    out["spread_chg8"] = spread - spread.shift(8)
    out["spread_chg24"] = spread - spread.shift(24)
    # where the current spread sits inside its trailing 96-bar range
    roll_min = spread.rolling(96, min_periods=48).min()
    roll_max = spread.rolling(96, min_periods=48).max()
    out["spread_pos96"] = (spread - roll_min) / (roll_max - roll_min).replace(0, np.nan)

    dense = (spread <= DENSE_SPREAD_MAX).astype(int)
    # consecutive dense bars ending at i
    grp = (dense == 0).cumsum()
    out["dense_run_len"] = dense.groupby(grp).cumsum()
    out["dense_frac48"] = dense.rolling(48, min_periods=24).mean()

    out["close_vs_ema55"] = close / out["ema55"].replace(0, np.nan) - 1
    out["close_vs_ema200"] = close / out["ema200"].replace(0, np.nan) - 1

    out["vol_ratio_mean8"] = out["volume_ratio"].rolling(8).mean()
    atr_mean96 = out["atr_pct"].rolling(96, min_periods=48).mean().replace(0, np.nan)
    out["atr_pct_ratio96"] = out["atr_pct"] / atr_mean96

    for n in (4, 12, 24, 48):
        out[f"ret_{n}"] = close.pct_change(n)

    return out.replace([np.inf, -np.inf], np.nan)


def extract_feature_rows(featured: pd.DataFrame, signal_indices: list[int]) -> pd.DataFrame:
    """Take feature vectors at the given candidate positions."""
    rows = featured.iloc[signal_indices]
    return rows[FEATURE_COLUMNS].reset_index(drop=True)
