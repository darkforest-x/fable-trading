"""Causal signal preparation for the owner's fifth-color RSI hypothesis.

Source: owner-supplied ChartPrime Parabolic RSI Pine (2026-09-20), using the
existing exact RSI14/SAR ports. Input columns are open/high/low/close/volume
on a continuous UTC five-minute grid. Resampling uses complete 1h or 4h UTC
groups only. RSI and SAR use current and prior closed aggregate candles;
run lengths use only current/past SAR state and its finite display mask.

This preparation module exposes both visible-circle and state-bar counts.
It does not choose the pending owner entry/exit interpretation or score PnL.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_rsi_sixma import _validate_frame
from yoyo.evaluation.parabolic_rsi_sar import diamonds, pine_sar
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder


def color_counts(sar: np.ndarray, below: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Count finite state bars and circles; a flip bar has no visible circle.

    This mirrors ``sar := isBelow != isBelow[1] ? na : sar_rsi``. Ordinary
    flips reset both runs even when no strong diamond is present.
    """
    sar = np.asarray(sar, dtype=float)
    below = np.asarray(below, dtype=bool)
    if sar.ndim != 1 or sar.shape != below.shape:
        raise ValueError("aligned one-dimensional SAR and state required")
    state = np.zeros(len(sar), dtype=int)
    dots = np.zeros(len(sar), dtype=int)
    for i in range(len(sar)):
        if not np.isfinite(sar[i]):
            continue
        flip = i > 0 and below[i] != below[i - 1]
        state[i] = 1 if i == 0 or flip or state[i - 1] == 0 else state[i - 1] + 1
        dots[i] = 0 if flip else (dots[i - 1] + 1 if i else 1)
    return state, dots


def prepare_signals(frame5: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Return complete close-labelled UTC candles and causal source signals.

    Exact 12/48 input rows are required per aggregate. Incomplete boundary
    groups are omitted, but an incomplete interior group is an error. A row
    labelled 04:00 describes [00:00,04:00), never the following four hours.
    """
    if hours not in (1, 4):
        raise ValueError("only the owner-requested 1h and 4h are supported")
    frame = _validate_frame(frame5)
    rule = f"{hours}h"
    groups = frame.resample(rule, origin="epoch", label="left", closed="left")
    bars = groups.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = groups.close.count()
    complete = counts.eq(hours * 12)
    if not complete.iloc[1:-1].all():
        raise ValueError("incomplete interior aggregate candle")
    bars = bars.loc[complete].copy()
    bars.index = bars.index + pd.Timedelta(hours=hours)
    bars.index.name = "signal_close_time"
    bars["rsi"] = _rsi_wilder(bars.close, 14)
    sar, below = pine_sar(bars.rsi.to_numpy(float))
    signals = diamonds(sar, below)
    state_run, dot_run = color_counts(sar, below)
    bars["sar"] = sar
    bars["side"] = np.where(below, 1, -1)
    bars["state_run"] = state_run
    bars["visible_dot_run"] = dot_run
    bars["strong_side"] = np.where(signals["strong_up"], 1, np.where(signals["strong_dn"], -1, 0))
    bars["flip_side"] = np.where(signals["flip_up"], 1, np.where(signals["flip_dn"], -1, 0))
    return bars
