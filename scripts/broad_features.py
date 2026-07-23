"""Broad causal feature bank (Alpha158-style) from raw OHLCV — free factor
discovery, NOT limited to the 28 hand-picked judgment features.

Owner critique (2026-07-23): analysing the manual boxes through only the 28
pre-defined features assumes those features span the owner's edge. If the edge
lives in a dimension the 28 miss, that analysis can't see it. This bank
generates ~130 causal features across returns/vol/volume/range/position/MA/
candle structure at many windows, so a model can discover what the owner's
boxes actually have in common — the data picks, not us.

All features use only bar i and earlier (causal).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = (3, 5, 10, 20, 30, 60, 120)


def add_broad_features(df: pd.DataFrame) -> pd.DataFrame:
    o = df["open"].astype(float); h = df["high"].astype(float)
    lo = df["low"].astype(float); c = df["close"].astype(float)
    v = df["volume"].astype(float)
    out: dict[str, pd.Series] = {}
    ret1 = c.pct_change()
    rng = (h - lo).replace(0, np.nan)

    for k in (1, 2, 3, 5, 8, 12, 24, 48, 96):
        out[f"ret_{k}"] = c.pct_change(k)
    for k in WINDOWS:
        out[f"vol_{k}"] = ret1.rolling(k).std()
        out[f"skew_{k}"] = ret1.rolling(k).skew()
        out[f"maxret_{k}"] = ret1.rolling(k).max()
        out[f"minret_{k}"] = ret1.rolling(k).min()
        # price position in recent channel
        hh = h.rolling(k).max(); ll = lo.rolling(k).min()
        out[f"pos_{k}"] = (c - ll) / (hh - ll).replace(0, np.nan)
        # distance to MA
        ma = c.rolling(k).mean()
        out[f"cma_{k}"] = c / ma - 1
        ema = c.ewm(span=k, adjust=False).mean()
        out[f"cema_{k}"] = c / ema - 1
        # volume
        out[f"volr_{k}"] = v / v.rolling(k).mean().replace(0, np.nan)
        out[f"volstd_{k}"] = v.rolling(k).std() / v.rolling(k).mean().replace(0, np.nan)
        # range compression
        out[f"rng_{k}"] = rng.rolling(k).mean() / c
        out[f"rngz_{k}"] = (rng - rng.rolling(k).mean()) / rng.rolling(k).std().replace(0, np.nan)
        # trend slope of close (normalised)
        out[f"slope_{k}"] = (c - c.shift(k)) / (k * c)
    # MA-bundle spread (the dense-cluster idea, at several period sets)
    for a, b in ((5, 20), (10, 30), (20, 60), (10, 60), (20, 120)):
        ma_a = c.ewm(span=a, adjust=False).mean(); ma_b = c.ewm(span=b, adjust=False).mean()
        out[f"spread_{a}_{b}"] = (ma_a - ma_b).abs() / c
    # candle structure (current bar)
    body = (c - o).abs()
    out["body_frac"] = body / rng
    out["upwick"] = (h - np.maximum(o, c)) / rng
    out["dnwick"] = (np.minimum(o, c) - lo) / rng
    out["updown"] = np.sign(c - o)
    # multi-bar consolidation: how tight the last k closes are
    for k in (5, 8, 12):
        out[f"close_tight_{k}"] = c.rolling(k).std() / c
    frame = pd.DataFrame(out, index=df.index)
    return frame.replace([np.inf, -np.inf], np.nan)


BROAD_COLUMNS = None  # filled on first call


def broad_columns(df_sample: pd.DataFrame) -> list[str]:
    return list(add_broad_features(df_sample).columns)
