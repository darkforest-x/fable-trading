"""Closed-bar six-MA morphology proposal, distinct from admitted gold labels.

Candidate A is preregistered in exp-gold-ma-indicator-20260914-v2/PROJECT_PLAN.
Inputs: OHLC/open_time; SMA/EMA close20/60/120 and Wilder ATR14 supplied by the
V1 parity reference. Compression/cross topology use t-12..t-1, body contact
t-8..t-1, slopes/displacement t-3..t. Confirmation uses only a frozen five-bar
level from the earlier marker and the current completed bar. No outcome labels,
future prices, source files, production presets or orders are accessed here.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from yoyo.evaluation.ma_drift_v1_reference import replay as replay_v1

MA_COLUMNS = ("s20", "e20", "s60", "e60", "s120", "e120")


def replay(frame: pd.DataFrame, *, bar_minutes: int = 15) -> pd.DataFrame:
    """Return V1 and candidate-A predicates/events for the same closed prefix.

    Every rolling window is trailing; state resets whenever ``ready`` is false.
    Shape marker timestamps are actual decision bars. No retrospective offset.
    """
    out = replay_v1(frame, bar_minutes=bar_minutes)
    atr = out["atr"].shift(1).where(lambda x: x > 0)
    mas = out.loc[:, list(MA_COLUMNS)]
    upper, lower = mas.max(axis=1), mas.min(axis=1)
    width = upper - lower
    compact = width.div(atr).le(2.0) & mas.notna().all(axis=1)
    compact_run = compact.astype(float).rolling(3, min_periods=3).sum().eq(3)
    out["a_compact"] = compact_run.shift(1).rolling(10, min_periods=10).max().eq(1)
    body_low = out[["open", "close"]].min(axis=1)
    body_high = out[["open", "close"]].max(axis=1)
    contact = body_high.ge(lower - 0.25 * atr) & body_low.le(upper + 0.25 * atr)
    out["a_contact"] = contact.astype(float).shift(1).rolling(8, min_periods=8).max().eq(1)
    flips = pd.Series(0.0, index=out.index)
    for a, b in combinations(MA_COLUMNS, 2):
        difference = mas[a] - mas[b]
        sign = np.sign(difference).replace(0, np.nan).ffill()
        valid = difference.notna() & difference.shift(1).notna()
        flips += (sign.mul(sign.shift(1)).lt(0) & valid).astype(float)
    prior_flips = flips.shift(1).rolling(12, min_periods=12).sum()
    out["a_topology"] = prior_flips.ge(2) | (width.shift(9).gt(0) & width.shift(1).le(0.90 * width.shift(9)))
    slopes = mas - mas.shift(3)
    fast_high = mas[["s20", "e20"]].max(axis=1)
    fast_low = mas[["s20", "e20"]].min(axis=1)
    out["a_near"] = (out["close"] - upper).clip(lower=0).add(
        (lower - out["close"]).clip(lower=0)).div(atr).le(2.5)
    tr = pd.concat([out["high"]-out["low"], (out["high"]-out["close"].shift(1)).abs(),
                    (out["low"]-out["close"].shift(1)).abs()], axis=1).max(axis=1)
    out["a_range"] = tr.div(atr).le(3.0)
    common = out["ready"] & out["a_compact"] & out["a_contact"] & out["a_topology"] & out["a_near"] & out["a_range"]
    for side, sign in (("long", 1), ("short", -1)):
        out[f"a_{side}_slope"] = (slopes["s20"] * sign > 0) & (slopes["e20"] * sign > 0) & (slopes * sign > 0).sum(axis=1).ge(3)
        out[f"a_{side}_progress"] = ((out["close"] - out["close"].shift(3)) * sign).div(atr).ge(0.4)
        out[f"a_{side}_outside"] = out["close"].gt(fast_high) if sign == 1 else out["close"].lt(fast_low)
        out[f"a_{side}_setup"] = common & out[f"a_{side}_slope"] & out[f"a_{side}_progress"] & out[f"a_{side}_outside"]
    levels = {"long": out["high"].rolling(5, min_periods=5).max(),
              "short": out["low"].rolling(5, min_periods=5).min()}
    ready_values = out["ready"].to_numpy(bool)
    close_values = out["close"].to_numpy(float)
    fast_high_values = fast_high.to_numpy(float)
    fast_low_values = fast_low.to_numpy(float)
    for side, sign in (("long", 1), ("short", -1)):
        setup = out[f"a_{side}_setup"].to_numpy(bool)
        level_values = levels[side].to_numpy(float)
        markers = np.zeros(len(out), dtype=bool)
        confirms = np.zeros(len(out), dtype=bool)
        eligible = True
        off = 3
        last = -100000
        pending = None
        for i in range(len(out)):
            if not ready_values[i]:
                eligible, off, last, pending = True, 3, -100000, None
                continue
            if pending is not None:
                started, level = pending
                age = i - started
                reclaimed = close_values[i] < fast_low_values[i] if sign == 1 else close_values[i] > fast_high_values[i]
                if age > 5 or reclaimed:
                    pending = None
                elif age > 0 and sign * (close_values[i] - level) > 0:
                    confirms[i] = True
                    pending = None
            if setup[i]:
                if eligible and i - last >= 6:
                    markers[i], eligible, last = True, False, i
                    pending = (i, float(level_values[i]))
                off = 0
            else:
                off += 1
                if off >= 3:
                    eligible = True
        out[f"a_{side}_marker"] = markers
        out[f"a_{side}_confirmation"] = confirms
    return out
