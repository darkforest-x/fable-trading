"""Causal ChartArt-style BB200 squeeze admissions for existing V6 events.

No V6 signal is reconstructed here.  All bands use current/past closes, while
the squeeze threshold and three-bar memory are explicitly prior-bar only.
RSI6 is Wilder RSI: its first value seeds from the first six close differences,
then uses Wilder's recursive average gain/loss.  This matches Pine ``ta.rsi``'s
RMA convention after its initial seed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BB_LENGTH, WIDTH_HISTORY, SQUEEZE_QUANTILE, MEMORY_BARS, RUN_LENGTH, RSI_LENGTH = 200, 500, .10, 12, 3, 6


def _segments(frame: pd.DataFrame, data_gap: pd.Series | None) -> pd.Series:
    gap = pd.Series(False, index=frame.index) if data_gap is None else pd.Series(data_gap, index=frame.index).fillna(True).astype(bool)
    invalid = ~np.isfinite(frame[["open", "high", "low", "close"]].to_numpy(float)).all(axis=1)
    return (gap | pd.Series(invalid, index=frame.index)).cumsum()


def _rsi_wilder(close: pd.Series, period: int = 6) -> pd.Series:
    delta = close.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    out = np.full(len(close), np.nan); avg_gain = avg_loss = np.nan
    for i in range(len(close)):
        if i == period:
            avg_gain, avg_loss = gain.iloc[1:period + 1].mean(), loss.iloc[1:period + 1].mean()
        elif i > period:
            avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
        if i >= period:
            out[i] = 100.0 if avg_loss == 0 and avg_gain > 0 else 0.0 if avg_gain == 0 and avg_loss > 0 else np.nan if avg_gain == avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return pd.Series(out, index=close.index)


def recent_compression(compressed: pd.Series) -> pd.Series:
    """True when a full three-bar run lies wholly in the preceding 12 bars."""
    values = compressed.fillna(False).to_numpy(bool); out = np.zeros(len(values), dtype=bool)
    for i in range(len(values)):
        history = values[max(0, i - MEMORY_BARS):i]  # never current bar
        out[i] = any(history[j:j + RUN_LENGTH].all() for j in range(max(0, len(history) - RUN_LENGTH + 1)))
    return pd.Series(out, index=compressed.index)


def features(frame: pd.DataFrame, *, data_gap: pd.Series | None = None) -> pd.DataFrame:
    """Return segmented BB200/RSI6 and prior-only squeeze diagnostics."""
    if not {"open", "high", "low", "close"}.issubset(frame):
        raise ValueError("OHLC is required")
    out = pd.DataFrame(index=frame.index); out["segment_id"] = _segments(frame, data_gap)
    parts = []
    for _, ids in out.groupby("segment_id", sort=False):
        x = frame.loc[ids.index, "close"].astype(float)
        basis = x.rolling(BB_LENGTH, min_periods=BB_LENGTH).mean(); std = x.rolling(BB_LENGTH, min_periods=BB_LENGTH).std(ddof=0)
        width = (4 * std / basis.abs()).where(basis.ne(0))
        threshold = width.shift(1).rolling(WIDTH_HISTORY, min_periods=WIDTH_HISTORY).quantile(SQUEEZE_QUANTILE)
        compressed = width.le(threshold).fillna(False)
        prior_run3, rsi = recent_compression(compressed), _rsi_wilder(x, RSI_LENGTH)
        part = pd.DataFrame({"bb_basis": basis, "bb_std_ddof0": std, "bb_width": width,
                             "bb_width_p10_prior500": threshold, "bb_compressed": compressed,
                             "bb_prior_run3_in12": prior_run3, "rsi6": rsi}, index=x.index)
        part["ready"] = threshold.notna() & rsi.notna()
        parts.append(part)
    return out.join(pd.concat(parts))


def admissions(signals: pd.DataFrame, diagnostic: pd.DataFrame) -> pd.DataFrame:
    """Classify every raw V6 event under pre-registered A/B/C/D rules."""
    if not signals.index.equals(diagnostic.index) or not {"long_signal", "short_signal"}.issubset(signals):
        raise ValueError("aligned long_signal/short_signal inputs are required")
    side = np.where(signals.long_signal.fillna(False), 1, np.where(signals.short_signal.fillna(False), -1, 0))
    if ((signals.long_signal.fillna(False)) & (signals.short_signal.fillna(False))).any():
        raise ValueError("ambiguous V6 event")
    result = pd.DataFrame(index=signals.index)
    result["side"] = side; result["raw_v6"] = side != 0
    result["ready"] = diagnostic.ready.astype(bool); result["prior_squeeze_run3"] = diagnostic.bb_prior_run3_in12.astype(bool)
    result["width_expanding"] = diagnostic.bb_width.gt(diagnostic.bb_width.shift(1)).fillna(False)
    result["rsi_direction_ok"] = ((side == 1) & diagnostic.rsi6.gt(50) | (side == -1) & diagnostic.rsi6.lt(50)).fillna(False)
    result["A0"] = result.raw_v6
    result["A"] = result.raw_v6 & result.ready
    result["B"] = result.raw_v6 & result.ready & result.prior_squeeze_run3
    result["C"] = result.B & result.width_expanding
    result["D"] = result.C & result.rsi_direction_ok
    result["rejection_reason"] = np.where(~result.raw_v6, "not_v6", np.where(~result.ready, "cold_or_gap", np.where(~result.prior_squeeze_run3, "no_prior_run3", np.where(~result.width_expanding, "width_not_expanding", np.where(~result.rsi_direction_ok, "rsi_direction", "admitted")))))
    return result
