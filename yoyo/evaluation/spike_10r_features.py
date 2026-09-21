"""Causal continuous features for the receipt-bound SPIKE 10R candidate set.

Every value is available at the V9 signal-bar close.  The extractor reads
only the frozen ``PreparedArm.frame`` through that bar and returns one row for
every supplied candidate, including candidates whose required history is
unknown.  A missing candle in a feature's stated lookback makes that feature
``NaN`` rather than silently joining observations across the gap.

Core columns are ``open, high, low, close, atr``.  When available, the
extractor also uses ``volume, tr, rv, pastWidth,
ropeHigh, ropeLow, s20, e20, s60, e60, s120, e120``.  The feature windows are:
the current signal bar for OHLC shape; V9's exact five-bar stop reference;
prior 20 or 60 bars for breakouts and momentum; prior 100 bars for relative
ATR and volatility; and prior 5/20 bars for volume trend.  Frozen-cache RV,
rope, width, and moving-average values use a conservative uninterrupted prior
120-bar guard (140 for the 20-bar slope), so a recent internal cache gap
marks these cached indicator features unknown. Cached ATR uses a 14-bar guard.
Optional or unavailable columns produce
``NaN`` and are never substituted with zero.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import risk_reference


FEATURE_COLUMNS = (
    "reference_risk_fraction", "atr_fraction", "risk_atr", "volume_ratio",
    "expansion", "body_fraction", "close_location", "rope_width_atr",
    "past_width_atr", "rope_distance_atr", "breakout20_atr", "momentum20_atr",
    "momentum60_atr", "trend_slope20_atr", "trend_alignment", "atr_relative100",
    "volatility_ratio20_100", "efficiency20", "prior_range20_atr", "volume_trend20",
)


def normalized_gap(prepared: object) -> np.ndarray:
    """Return cached and observed cadence gaps, recognized at the later bar."""
    frame = prepared.frame
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("features require a UTC DatetimeIndex")
    observed = frame.index.to_series().diff().ne(pd.Timedelta(minutes=prepared.context.minutes)).to_numpy(bool)
    if len(observed):
        observed[0] = False
    return np.asarray(prepared.gap, dtype=bool) | observed


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _window_complete(gaps: np.ndarray, start: int, end: int) -> bool:
    """Require an inclusive existing window; out-of-range history is unknown."""
    if start < 0 or end >= len(gaps):
        return False
    # A gap is marked at the later bar.  A gap at the first historical edge
    # predates the requested observations; all later gaps break this window.
    if start == end:
        return not bool(gaps[end])
    return not bool(gaps[start + 1:end + 1].any())


def _number(frame: pd.DataFrame, name: str, i: int) -> float:
    if name not in frame or i < 0 or i >= len(frame):
        return math.nan
    value = frame[name].iloc[i]
    return float(value) if _finite(value) else math.nan


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if _finite(numerator) and _finite(denominator) and denominator != 0 else math.nan


def _reference_risk(prepared: object, i: int, side: int, gaps: np.ndarray) -> tuple[float, float]:
    """Return confirmation-close risk fraction and risk/ATR with V9's 5-bar rule."""
    if side not in (1, -1) or i < 4 or not _window_complete(gaps, i - 4, i):
        return math.nan, math.nan
    close, atr = _number(prepared.frame, "close", i), _number(prepared.frame, "atr", i)
    if not (_finite(close) and close > 0 and _finite(atr) and atr > 0):
        return math.nan, math.nan
    extreme_name = "low" if side == 1 else "high"
    values = prepared.frame[extreme_name].iloc[i - 4:i + 1].to_numpy(float)
    if not np.isfinite(values).all():
        return math.nan, math.nan
    extreme = float(values.min() if side == 1 else values.max())
    ref = risk_reference(side, close, extreme, atr, tick=float(prepared.spec.tick))
    if not ref.valid or not _finite(ref.risk):
        return math.nan, math.nan
    return float(ref.risk) / close, float(ref.risk) / atr


def _one(prepared: object, i: int, side: int, gaps: np.ndarray) -> dict[str, float]:
    """Compute the declared features for one candidate without reading later bars."""
    frame = prepared.frame
    out = {name: math.nan for name in FEATURE_COLUMNS}
    if not _window_complete(gaps, i, i):
        return out
    open_, high, low, close, atr = (_number(frame, n, i) for n in ("open", "high", "low", "close", "atr"))
    range_ = high - low if _finite(high) and _finite(low) else math.nan
    reference_fraction, risk_atr = _reference_risk(prepared, i, side, gaps)
    out.update(reference_risk_fraction=reference_fraction, risk_atr=risk_atr,
               body_fraction=_ratio(close - open_, range_), close_location=_ratio(close - low, range_))
    if _window_complete(gaps, i - 14, i):
        out["atr_fraction"] = _ratio(atr, close)
        out["expansion"] = _ratio(_number(frame, "tr", i), _number(frame, "atr", i - 1))
    if _window_complete(gaps, i - 120, i):
        out["volume_ratio"] = _number(frame, "rv", i)
        out["rope_width_atr"] = _ratio(_number(frame, "ropeHigh", i) - _number(frame, "ropeLow", i), atr)
        out["past_width_atr"] = _number(frame, "pastWidth", i)
        out["rope_distance_atr"] = _ratio(close - _number(frame, "ropeHigh", i), atr)
    if _window_complete(gaps, i - 20, i):
        highs = frame["high"].iloc[i - 20:i].to_numpy(float) if "high" in frame else np.array([])
        c20, e60now, e60old = _number(frame, "close", i - 20), _number(frame, "e60", i), _number(frame, "e60", i - 20)
        if len(highs) == 20 and np.isfinite(highs).all():
            out["breakout20_atr"] = _ratio(close - float(highs.max()), atr)
        out["momentum20_atr"] = _ratio(close - c20, atr)
        if _window_complete(gaps, i - 140, i):
            out["trend_slope20_atr"] = _ratio(e60now - e60old, atr)
        returns = np.diff(np.log(frame["close"].iloc[i - 20:i + 1].to_numpy(float))) if "close" in frame else np.array([])
        if len(returns) == 20 and np.isfinite(returns).all():
            denominator = np.abs(np.diff(frame["close"].iloc[i - 20:i + 1].to_numpy(float))).sum()
            out["efficiency20"] = _ratio(abs(close - c20), denominator)
    if _window_complete(gaps, i - 60, i):
        out["momentum60_atr"] = _ratio(close - _number(frame, "close", i - 60), atr)
    comparisons = (("e20", "e60"), ("e60", "e120"), ("s20", "s60"), ("s60", "s120"))
    if _window_complete(gaps, i - 120, i) and all(_finite(_number(frame, a, i)) and _finite(_number(frame, b, i)) for a, b in comparisons):
        out["trend_alignment"] = float(np.mean([_number(frame, a, i) > _number(frame, b, i) for a, b in comparisons]))
    if _window_complete(gaps, i - 100, i):
        prior_atr = frame["atr"].iloc[i - 100:i].to_numpy(float) if "atr" in frame else np.array([])
        if len(prior_atr) == 100 and np.isfinite(prior_atr).all():
            out["atr_relative100"] = _ratio(atr, float(np.median(prior_atr)))
    if _window_complete(gaps, i - 101, i) and "close" in frame:
        # ``prior`` excludes the signal-bar return: 101 prior closes yield
        # 100 returns, and the last observed close only closes that history.
        closes = frame["close"].iloc[i - 101:i + 1].to_numpy(float)
        prior_returns = np.diff(np.log(closes))[:-1]
        if len(prior_returns) == 100 and np.isfinite(prior_returns).all():
            out["volatility_ratio20_100"] = _ratio(float(np.std(prior_returns[-20:], ddof=0)), float(np.std(prior_returns, ddof=0)))
    if _window_complete(gaps, i - 20, i):
        highs = frame["high"].iloc[i - 20:i].to_numpy(float) if "high" in frame else np.array([])
        lows = frame["low"].iloc[i - 20:i].to_numpy(float) if "low" in frame else np.array([])
        if len(highs) == len(lows) == 20 and np.isfinite(highs).all() and np.isfinite(lows).all():
            out["prior_range20_atr"] = _ratio(float(highs.max() - lows.min()), atr)
        volumes = frame["volume"].iloc[i - 20:i].to_numpy(float) if "volume" in frame else np.array([])
        if len(volumes) == 20 and np.isfinite(volumes).all():
            out["volume_trend20"] = _ratio(float(np.median(volumes[-5:])), float(np.median(volumes)))
    return out


def candidate_features(prepared: object, candidates: pd.DataFrame) -> pd.DataFrame:
    """Attach all 20 causal fields to candidate rows without filtering any row.

    ``candidates`` must contain ``local_i`` and ``side``.  It may contain any
    metadata (notably immutable ``event_key`` and original ``signal_i``), which
    is preserved exactly.  ``local_i`` addresses the authenticated cache and
    must not be confused with the frozen full-frame ordinal in ``signal_i``.
    """
    if not {"local_i", "side"}.issubset(candidates):
        raise ValueError("candidates require local_i and side")
    gaps = normalized_gap(prepared)
    metadata_columns = [name for name in candidates.columns if name not in FEATURE_COLUMNS]
    rows = []
    for row in candidates.itertuples(index=False):
        item = row._asdict()
        i, side = int(item["local_i"]), int(item["side"])
        if i < 0 or i >= len(prepared.frame):
            raise ValueError(f"candidate local_i outside frame: {i}")
        item = {name: item[name] for name in metadata_columns}
        item.update(_one(prepared, i, side, gaps))
        rows.append(item)
    return pd.DataFrame(rows, columns=[*metadata_columns, *FEATURE_COLUMNS])
