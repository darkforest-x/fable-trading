"""Causal high-breakout entry gate for a frozen SPIKE V9 prepared arm.

``prepare_entry_v2`` accepts the output of the original V9 preparation and
changes only its entry ``allowed`` mask.  For each originally allowed long at
signal bar ``t``, it reads current ``close[t]`` and ``atr[t]`` plus completed
``high[t-20:t]``.  The current high and every later candle are excluded from
the threshold.  The 20 preceding bars and the signal bar must be a contiguous
fixed-duration clock with finite, positive OHLC values and a finite, positive
current ATR.  The close-confirmed decision is available at ``bar_open +
interval``.  Raw sides, gap flags, price arrays and the parent V9 exit engine
remain untouched, so a filtered raw opposite signal can still close a position.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v1_v8_be05 as engine


VERSION = POLICY = "high_r_entry_v2"
LOOKBACK = 20

_DECISION_COLUMNS = [
    "local_i", "signal_i", "stream_key", "signal_bar_open", "available_at", "side",
    "prior_high", "score", "gate_known", "gate_passed", "reason", "baseline_allowed",
]


def _window_is_contiguous(index: pd.DatetimeIndex, start: int, stop: int,
                          interval: pd.Timedelta) -> bool:
    """Return whether the inclusive [start, stop] source-clock slice has no hole."""
    return all(index[i] - index[i - 1] == interval for i in range(start + 1, stop + 1))


def prepare_entry_v2(prepared: engine.PreparedArm, lookback: int = LOOKBACK) -> tuple[engine.PreparedArm, pd.DataFrame]:
    """Apply the fixed high-breakout gate to one immutable original V9 arm.

    Decisions cover every already-allowed original V9 candidate, including
    shorts.  A long at local index ``t`` passes only when its current close is
    strictly greater than ``max(high[t-lookback:t])`` and its score is
    ``(close[t] - prior_high) / atr[t]``.  The current bar's high is excluded.
    The score and decision use no later bar; both are available at the next
    interval boundary.  Insufficient history, a clock/data gap, or nonfinite
    or nonpositive OHLC/current-ATR inputs leave a long rejected.  Shorts are
    explicitly exempt and retain their original V9 admission unchanged.
    """
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback <= 0:
        raise ValueError("lookback must be a positive integer")
    frame, context = prepared.frame, prepared.context
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("prepared frame requires a timezone-aware DatetimeIndex")
    interval = pd.Timedelta(minutes=context.minutes)
    if interval <= pd.Timedelta(0):
        raise ValueError("prepared context requires a positive interval")

    baseline_allowed = np.asarray(prepared.allowed, dtype=bool)
    allowed = baseline_allowed.copy()
    open_, high, low, close, atr = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    gap, raw_side = prepared.gap, prepared.raw_side
    rows: list[dict[str, object]] = []

    for i in np.flatnonzero(baseline_allowed):
        side = int(raw_side[i])
        stamp = pd.Timestamp(index[i])
        original_i = prepared.ordinal.get(stamp)
        if original_i is None:
            raise ValueError("original V9 candidate missing frozen ordinal")
        row: dict[str, object] = {
            "local_i": int(i), "signal_i": int(original_i), "stream_key": context.key,
            "signal_bar_open": stamp, "available_at": stamp + interval, "side": side,
            "prior_high": math.nan, "score": math.nan, "gate_known": True,
            "gate_passed": True, "reason": "short_unchanged" if side == -1 else "passed",
            "baseline_allowed": True,
        }
        if side != 1:
            rows.append(row)
            continue

        if i < lookback:
            row.update(gate_known=False, gate_passed=False, reason="insufficient_history")
        else:
            start = i - lookback
            # A gap is recorded on its later bar.  ``gap[start]`` therefore
            # describes only the edge before the rebuilt 20-bar window; it
            # cannot invalidate high[start:i].  Every internal/current edge
            # remains required for this close decision.
            if bool(np.asarray(gap[start + 1:i + 1], dtype=bool).any()) or not _window_is_contiguous(index, start, i, interval):
                row.update(gate_known=False, gate_passed=False, reason="history_gap")
            else:
                ohlc = np.column_stack((open_[start:i + 1], high[start:i + 1], low[start:i + 1], close[start:i + 1]))
                values_known = np.isfinite(ohlc).all() and (ohlc > 0).all() and math.isfinite(float(atr[i])) and float(atr[i]) > 0
                if not values_known:
                    row.update(gate_known=False, gate_passed=False, reason="nonfinite_or_nonpositive_input")
                else:
                    prior_high = float(np.max(high[start:i]))
                    score = (float(close[i]) - prior_high) / float(atr[i])
                    passed = bool(score > 0.0)
                    row.update(prior_high=prior_high, score=score, gate_passed=passed,
                               reason="passed" if passed else "close_not_above_prior_high")
        allowed[i] = bool(row["gate_passed"])
        rows.append(row)

    return replace(prepared, allowed=allowed), pd.DataFrame(rows, columns=_DECISION_COLUMNS)


def replay_entry_v2(prepared: engine.PreparedArm) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay the parent V9 serial engine with the causal high-breakout mask.

    The parent call is unchanged: it retains original stops, costs, fills,
    raw-side reversal exits and all serial state transitions.  The returned
    decisions are close-confirmed evidence available at each candidate's next
    bar boundary.
    """
    gated, decisions = prepare_entry_v2(prepared)
    trades, fills, events = engine.replay_serial(gated.context, arm="v8", enable_be=False, prepared=gated)
    return trades, fills, events, decisions
