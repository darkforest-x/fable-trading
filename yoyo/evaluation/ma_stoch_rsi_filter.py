"""Causal ChartPrime RSI-zone gate on the frozen MA/Stoch entry stream.

Source: ChartPrime Parabolic RSI, NI0Qhwy7.pine (MPL-2.0), lines 11-28.
Its RSI line is ta.rsi(close, 14); SAR flips/strong diamonds are separate
events and are not this filter. Reuse the existing Wilder seed convention.
No exit, price source, live configuration or historical engine is changed.
"""
from __future__ import annotations

import numpy as np
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder


def zone_admission(admission, rsi, lower=30., upper=70.):
    """Intersect same-closed-bar arrows/MA with strict RSI lower/upper zones.

    Inputs contain only the signal bar and its past. No remembered oversold
    state, future confirmation, extra arrow, or SAR direction is required.
    Undefined RSI and equality at either threshold do not pass.
    """
    admission = np.asarray(admission)
    rsi = np.asarray(rsi, dtype=float)
    if admission.shape != rsi.shape or not 0 <= lower < upper <= 100:
        raise ValueError("aligned values and ordered RSI thresholds required")
    if not np.isin(admission, [-1, 0, 1]).all():
        raise ValueError("admission must be -1, 0 or 1")
    passed = np.isfinite(rsi) & (((admission == 1) & (rsi < lower)) |
                               ((admission == -1) & (rsi > upper)))
    return np.where(passed, admission, 0).astype(int)


def add_filter(ctx, length=14, lower=30., upper=70.):
    """Copy context, gating admission with close-only RSI through each row.

    RSI uses the first `length` close differences to seed Wilder averages,
    then their complete causal recursion. The original arrays stay untouched.
    """
    if not isinstance(length, int) or length < 1:
        raise ValueError("positive integer RSI length required")
    filtered = dict(ctx)
    filtered['rsi'] = _rsi_wilder(ctx['frame'].close, length).to_numpy(float)
    filtered['admission'] = zone_admission(ctx['admission'], filtered['rsi'], lower, upper)
    return filtered
