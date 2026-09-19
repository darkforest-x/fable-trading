"""Causal higher-timeframe SMA direction evidence for the V9 study.

Owner request 2026-09-20: compare higher-timeframe SMA lengths, permitting
longs above and shorts below the SMA. Features read OHLCV from complete 5m
buckets only. SMA(L) uses L consecutive completed higher bars, visible at
the CHART BAR OPEN, matching Pine SMA[1] with lookahead_on. The chart close
is compared at its later confirmation; no future higher close is exposed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def confirmed_sma(base5m, chart_index, htf_minutes, lengths):
    """Align complete, contiguous HTF SMA windows without carrying stale bars.

    Inputs: five-minute open/high/low/close/volume, UTC bar-open timestamps;
    requested SMA windows contain exactly L valid consecutive HTF candles.
    Missing/partial/invalid higher candles invalidate their rolling windows.
    """
    for index in (base5m.index, chart_index):
        if not isinstance(index, pd.DatetimeIndex) or index.tz is None or not index.is_monotonic_increasing or not index.is_unique:
            raise ValueError("ordered unique timezone-aware clocks required")
    if htf_minutes < 5 or htf_minutes % 5 or not lengths or min(lengths) < 1:
        raise ValueError("invalid HTF duration or SMA lengths")
    result = pd.DataFrame(index=chart_index)
    result["htf_open"] = pd.NaT
    result["htf_close_time"] = pd.NaT
    result["htf_valid_rows"] = 0
    for length in lengths:
        result[f"sma_{length}"] = np.nan
    if base5m.empty or len(chart_index) == 0:
        return result
    if (base5m.index.asi8 % pd.Timedelta(minutes=5).value != 0).any():
        raise ValueError("5m timestamps are not aligned")
    values = base5m[["open", "high", "low", "close", "volume"]]
    valid = (np.isfinite(values).all(axis=1) & values.low.gt(0)
             & values.high.ge(values[["open", "close", "low"]].max(axis=1))
             & values.low.le(values[["open", "close", "high"]].min(axis=1))
             & values.volume.ge(0))
    clean = pd.DataFrame({"close": base5m.close, "valid": valid.astype(int)}, index=base5m.index)
    buckets = clean.resample(f"{htf_minutes}min", origin="epoch", label="left", closed="left")
    higher = buckets.agg(close=("close", "last"), count=("close", "size"), valid=("valid", "sum"))
    complete = higher["count"].eq(htf_minutes // 5) & higher.valid.eq(htf_minutes // 5)
    close = higher.close.where(complete)
    delta = pd.Timedelta(minutes=htf_minutes)
    closes = higher.index + delta
    pos = closes.searchsorted(chart_index, side="right") - 1
    safe = np.maximum(pos, 0)
    age = chart_index.asi8 - closes.asi8[safe]
    available = (pos >= 0) & (age >= 0) & (age < delta.value)
    result["htf_open"] = pd.Series(higher.index.take(safe), index=chart_index).where(available)
    result["htf_close_time"] = pd.Series(closes.take(safe), index=chart_index).where(available)
    result["htf_valid_rows"] = np.where(available, higher.valid.to_numpy()[safe], 0)
    for length in lengths:
        ma = close.rolling(length, min_periods=length).mean().to_numpy()
        result[f"sma_{length}"] = np.where(available, ma[safe], np.nan)
    return result


def side_gate(close, side, sma):
    """Strict signal-close direction, with equality/unknown/non-signal rejected."""
    close, side, sma = np.asarray(close, float), np.asarray(side, int), np.asarray(sma, float)
    return (np.isfinite(close) & np.isfinite(sma) & (close > 0) & (sma > 0)
            & (((side == 1) & (close > sma)) | ((side == -1) & (close < sma))))
