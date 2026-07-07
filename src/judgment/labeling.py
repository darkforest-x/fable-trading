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
) -> BarrierOutcome | None:
    """Label one candidate at position `signal_i` of an indicator frame.

    Requires columns open/high/low/close and atr14. Returns None when there
    are not enough future bars for entry + full horizon, or when atr_pct at
    the signal bar is below `atr_pct_min` (barriers too narrow to trade).
    """
    entry_i = signal_i + 1
    last_i = entry_i + HORIZON_BARS - 1
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
    up_first = int(np.argmax(hit_up)) if hit_up.any() else HORIZON_BARS
    dn_first = int(np.argmax(hit_dn)) if hit_dn.any() else HORIZON_BARS

    if up_first < dn_first:
        return BarrierOutcome(1, "tp", up_first + 1, entry, upper / entry - 1)
    if dn_first < up_first:
        return BarrierOutcome(0, "sl", dn_first + 1, entry, lower / entry - 1)
    if up_first == dn_first < HORIZON_BARS:
        # both barriers inside the same bar: order unknown, assume worst case
        return BarrierOutcome(0, "sl_ambiguous", dn_first + 1, entry, lower / entry - 1)
    timeout_close = float(frame["close"].iloc[last_i])
    return BarrierOutcome(0, "timeout", HORIZON_BARS, entry, timeout_close / entry - 1)
