"""Triple-barrier labeling for dense-MA candidates (long side).

For each candidate confirmed at bar i (all rule inputs use data <= i):
- entry price  = open of bar i+1 (next bar open, no lookahead);
- upper barrier = entry + TP_ATR_MULT * ATR14(i);
- lower barrier = entry - SL_ATR_MULT * ATR14(i);
- timeout       = HORIZON_BARS bars after entry.

Label = 1 if the upper barrier is touched first, 0 if the lower barrier is
touched first or the position times out (owner-approved simple start).

Intra-bar ambiguity: if one bar touches both barriers we cannot know the order
from OHLC alone; we conservatively count it as a stop-loss (label 0).

`realized_ret` records the exit return actually achievable under this exit
rule (barrier price or timeout close), for later backtesting.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# v2 barriers (owner decision 2026-07-07): wider barriers so the median gross
# TP (~4 x atr_pct ~= 1.1%) clears the 0.2% round-trip cost by >5x. The v1
# barriers (TP 2xATR / SL 1xATR) produced labels dominated by micro noise
# (median exit 3 bars) and top-decile net returns below cost -- see
# analysis/p2b_judgment_report.md.
TP_ATR_MULT = 4.0
SL_ATR_MULT = 2.0
HORIZON_BARS = 72
# Candidates whose atr_pct is below this floor are skipped entirely: their
# barrier scale cannot cover trading costs no matter what the model says.
ATR_PCT_MIN = 0.0015


@dataclass(frozen=True)
class BarrierOutcome:
    label: int          # 1 = TP first, 0 = SL first or timeout
    outcome: str        # "tp" | "sl" | "timeout" | "sl_ambiguous"
    exit_offset: int    # bars after entry bar when exit happened (1-based)
    entry_price: float
    realized_ret: float  # exit_price / entry_price - 1


def label_candidate(
    frame: pd.DataFrame,
    signal_i: int,
    *,
    tp_mult: float = TP_ATR_MULT,
    sl_mult: float = SL_ATR_MULT,
    atr_pct_min: float = ATR_PCT_MIN,
    horizon: int = HORIZON_BARS,
) -> BarrierOutcome | None:
    """Label one candidate at position `signal_i` of an indicator frame.

    Requires columns open/high/low/close and atr14. Returns None when there
    are not enough future bars for entry + full horizon, or when atr_pct at
    the signal bar is below `atr_pct_min` (barriers too narrow to trade).
    """
    entry_i = signal_i + 1
    last_i = entry_i + horizon - 1
    if last_i >= len(frame):
        return None
    atr = float(frame["atr14"].iloc[signal_i])
    entry = float(frame["open"].iloc[entry_i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    atr_pct = float(frame["atr_pct"].iloc[signal_i])
    if atr_pct_min > 0 and (not np.isfinite(atr_pct) or atr_pct < atr_pct_min):
        return None
    upper = entry + tp_mult * atr
    lower = entry - sl_mult * atr

    highs = frame["high"].to_numpy()[entry_i : last_i + 1]
    lows = frame["low"].to_numpy()[entry_i : last_i + 1]
    hit_up = highs >= upper
    hit_dn = lows <= lower
    up_first = int(np.argmax(hit_up)) if hit_up.any() else horizon
    dn_first = int(np.argmax(hit_dn)) if hit_dn.any() else horizon

    if up_first < dn_first:
        return BarrierOutcome(1, "tp", up_first + 1, entry, upper / entry - 1)
    if dn_first < up_first:
        return BarrierOutcome(0, "sl", dn_first + 1, entry, lower / entry - 1)
    if up_first == dn_first < horizon:
        # both barriers inside the same bar: order unknown, assume worst case
        return BarrierOutcome(0, "sl_ambiguous", dn_first + 1, entry, lower / entry - 1)
    timeout_close = float(frame["close"].iloc[last_i])
    return BarrierOutcome(0, "timeout", horizon, entry, timeout_close / entry - 1)


def label_candidate_trailing(
    frame: pd.DataFrame,
    signal_i: int,
    *,
    trail_mult: float,
    atr_pct_min: float = ATR_PCT_MIN,
    horizon: int = HORIZON_BARS,
) -> BarrierOutcome | None:
    """Trend-following exit: no fixed TP, stop trails `trail_mult` x ATR14(i)
    below the running high (seeded at entry). Conservative intra-bar rule: the
    stop for bar j uses the running high up to bar j-1, so a new high and a
    stop-out inside the same bar never help the trade; a gap below the stop
    fills at the bar open. Timeout exits at the horizon close.

    Label = 1 iff realized_ret > 0 (no barrier order to encode here).
    """
    entry_i = signal_i + 1
    last_i = entry_i + horizon - 1
    if last_i >= len(frame):
        return None
    atr = float(frame["atr14"].iloc[signal_i])
    entry = float(frame["open"].iloc[entry_i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    atr_pct = float(frame["atr_pct"].iloc[signal_i])
    if atr_pct_min > 0 and (not np.isfinite(atr_pct) or atr_pct < atr_pct_min):
        return None

    highs = frame["high"].to_numpy()[entry_i : last_i + 1]
    lows = frame["low"].to_numpy()[entry_i : last_i + 1]
    opens = frame["open"].to_numpy()[entry_i : last_i + 1]
    run_max = entry
    for j in range(horizon):
        stop = run_max - trail_mult * atr
        if lows[j] <= stop:
            exit_price = min(stop, float(opens[j]))
            ret = exit_price / entry - 1
            return BarrierOutcome(int(ret > 0), "trail", j + 1, entry, ret)
        run_max = max(run_max, float(highs[j]))
    ret = float(frame["close"].iloc[last_i]) / entry - 1
    return BarrierOutcome(int(ret > 0), "timeout", horizon, entry, ret)


def _entry_context(frame: pd.DataFrame, signal_i: int, horizon: int, atr_pct_min: float):
    """Shared guards for exit-variant labelers; None when the trade is untakeable."""
    entry_i = signal_i + 1
    last_i = entry_i + horizon - 1
    if last_i >= len(frame):
        return None
    atr = float(frame["atr14"].iloc[signal_i])
    entry = float(frame["open"].iloc[entry_i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    atr_pct = float(frame["atr_pct"].iloc[signal_i])
    if atr_pct_min > 0 and (not np.isfinite(atr_pct) or atr_pct < atr_pct_min):
        return None
    sl = slice(entry_i, last_i + 1)
    return (entry, atr, frame["high"].to_numpy()[sl], frame["low"].to_numpy()[sl],
            frame["open"].to_numpy()[sl], float(frame["close"].iloc[last_i]))


def label_short_candidate(
    frame: pd.DataFrame,
    signal_i: int,
    *,
    tp_mult: float = TP_ATR_MULT,
    sl_mult: float = SL_ATR_MULT,
    atr_pct_min: float = ATR_PCT_MIN,
    horizon: int = HORIZON_BARS,
) -> BarrierOutcome | None:
    ctx = _entry_context(frame, signal_i, horizon, atr_pct_min)
    if ctx is None:
        return None
    entry, atr, highs, lows, _, timeout_close = ctx
    lower = entry - tp_mult * atr
    upper = entry + sl_mult * atr
    if lower <= 0:
        return None
    hit_dn = lows <= lower
    hit_up = highs >= upper
    dn_first = int(np.argmax(hit_dn)) if hit_dn.any() else horizon
    up_first = int(np.argmax(hit_up)) if hit_up.any() else horizon

    if dn_first < up_first:
        return BarrierOutcome(1, "tp", dn_first + 1, entry, entry / lower - 1)
    if up_first < dn_first:
        return BarrierOutcome(0, "sl", up_first + 1, entry, entry / upper - 1)
    if dn_first == up_first < horizon:
        return BarrierOutcome(0, "sl_ambiguous", up_first + 1, entry, entry / upper - 1)
    return BarrierOutcome(0, "timeout", horizon, entry, entry / timeout_close - 1)


def label_candidate_scaled(
    frame: pd.DataFrame,
    signal_i: int,
    *,
    tp1_mult: float = 2.5,
    trail_mult: float = 3.0,
    sl_mult: float = 2.0,
    horizon: int = HORIZON_BARS,
    atr_pct_min: float = ATR_PCT_MIN,
) -> BarrierOutcome | None:
    """H1 scaled exit: half the position banks at entry + tp1_mult x ATR; the
    remainder trails trail_mult x ATR below the running high (seeded at the
    TP1 price). Hard stop entry - sl_mult x ATR protects the whole position
    until TP1. Conservative intra-bar ordering: the stop is always checked
    BEFORE the target within a bar, and the trailing stop for bar j uses the
    running high up to bar j-1. realized_ret is the half-and-half blend.
    """
    ctx = _entry_context(frame, signal_i, horizon, atr_pct_min)
    if ctx is None:
        return None
    entry, atr, highs, lows, opens, timeout_close = ctx
    hard_stop = entry - sl_mult * atr
    tp1 = entry + tp1_mult * atr
    ret1: float | None = None  # booked half, set once TP1 fills
    run_max = tp1
    for j in range(horizon):
        if ret1 is None:
            if lows[j] <= hard_stop:  # stop first: conservative
                exit_price = min(hard_stop, float(opens[j]))
                ret = exit_price / entry - 1
                return BarrierOutcome(0, "sl", j + 1, entry, ret)
            if highs[j] >= tp1:
                ret1 = tp1 / entry - 1
            continue  # phase-2 trailing starts on the NEXT bar (conservative)
        stop = max(run_max - trail_mult * atr, hard_stop)
        if lows[j] <= stop:
            exit_price = min(stop, float(opens[j]))
            ret = 0.5 * ret1 + 0.5 * (exit_price / entry - 1)
            return BarrierOutcome(int(ret > 0), "scaled", j + 1, entry, ret)
        run_max = max(run_max, float(highs[j]))
    if ret1 is None:
        ret = timeout_close / entry - 1
        return BarrierOutcome(int(ret > 0), "timeout", horizon, entry, ret)
    ret = 0.5 * ret1 + 0.5 * (timeout_close / entry - 1)
    return BarrierOutcome(int(ret > 0), "scaled_timeout", horizon, entry, ret)


def label_candidate_breakeven(
    frame: pd.DataFrame,
    signal_i: int,
    *,
    tp_mult: float = 5.0,
    sl_mult: float = 2.0,
    be_trigger: float = 1.5,
    horizon: int = HORIZON_BARS,
    atr_pct_min: float = ATR_PCT_MIN,
) -> BarrierOutcome | None:
    """H2 breakeven shift: classic TP/SL, but once price has traded
    be_trigger x ATR in favor the stop moves to the entry price. Conservative
    intra-bar ordering: the CURRENT stop is checked before the trigger or the
    target inside the same bar, so a spike-up-then-down bar can't grant the
    breakeven protection retroactively.
    """
    ctx = _entry_context(frame, signal_i, horizon, atr_pct_min)
    if ctx is None:
        return None
    entry, atr, highs, lows, opens, timeout_close = ctx
    upper = entry + tp_mult * atr
    trigger = entry + be_trigger * atr
    stop = entry - sl_mult * atr
    armed = False
    for j in range(horizon):
        if lows[j] <= stop:  # current stop first: conservative
            exit_price = min(stop, float(opens[j]))
            ret = exit_price / entry - 1
            return BarrierOutcome(int(ret > 0), "be" if armed else "sl", j + 1, entry, ret)
        if highs[j] >= upper:
            return BarrierOutcome(1, "tp", j + 1, entry, upper / entry - 1)
        if not armed and highs[j] >= trigger:
            armed = True
            stop = entry
    ret = timeout_close / entry - 1
    return BarrierOutcome(int(ret > 0), "timeout", horizon, entry, ret)


def label_candidate_ma_exit(
    frame: pd.DataFrame,
    signal_i: int,
    *,
    ma_col: str = "ema20",
    atr_pct_min: float = ATR_PCT_MIN,
    horizon: int = HORIZON_BARS,
) -> BarrierOutcome | None:
    entry_i = signal_i + 1
    last_i = entry_i + horizon - 1
    if last_i >= len(frame) or ma_col not in frame.columns:
        return None
    entry = float(frame["open"].iloc[entry_i])
    atr = float(frame["atr14"].iloc[signal_i])
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(atr) or atr <= 0:
        return None
    atr_pct = float(frame["atr_pct"].iloc[signal_i])
    if atr_pct_min > 0 and (not np.isfinite(atr_pct) or atr_pct < atr_pct_min):
        return None

    closes = frame["close"].to_numpy()[entry_i : last_i + 1]
    ma_values = frame[ma_col].to_numpy()[entry_i : last_i + 1]
    for j in range(horizon):
        close = float(closes[j])
        ma_value = float(ma_values[j])
        if np.isfinite(close) and np.isfinite(ma_value) and close < ma_value:
            ret = close / entry - 1
            return BarrierOutcome(int(ret > 0), "ma_exit", j + 1, entry, ret)
    timeout_close = float(frame["close"].iloc[last_i])
    ret = timeout_close / entry - 1
    return BarrierOutcome(int(ret > 0), "timeout", horizon, entry, ret)
