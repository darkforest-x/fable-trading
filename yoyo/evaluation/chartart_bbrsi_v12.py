"""ChartArt Bollinger + RSI v1.2 (long-only) replay, on the v1.1 feature base.

Source: TradingView script by ChartArt, v1.2 "Long-Only". Entries are the same
same-bar double crossing as v1.1: RSI(6) crosses above 50 while close crosses
above the lower band of BB(200, 2). The published difference is the exit side.
v1.1 reverses into a short on `crossunder(rsi,50) and crossunder(close, upper)`;
v1.2 places no short at all and only calls strategy.close on that same
condition, so `chartart_bbrsi.signal_masks`' short mask is reused unchanged as
the flat signal. The `strategy.cancel` in the script's else branch cannot undo
a fill: the entry stop sits below a close that already crossed it, so the
emulator activates it immediately and fills at the next open, before the next
bar's close calculation runs the cancel.

Features, costs and trade accounting are imported, not re-implemented, so the
only variable between the two arms is the exit rule. No future rows enter any
entry or exit decision; only `trade_row` inspects the outcome window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.chartart_bbrsi import TRADE_COLUMNS, trade_row

COST = .002


def replay_long_only(frame, start, end, minutes):
    """Flat at window open; one unit long; exits only on the flat signal.

    `frame.short_signal` is v1.2's `close_long`. Same-direction entries while
    already long are ignored (pyramiding 0). A position still open at the last
    evaluated bar is marked at that bar's close and flagged censored.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    frame = frame.loc[frame.index + pd.Timedelta(minutes=minutes) <= end]
    if frame.empty or frame.index[-1] + pd.Timedelta(minutes=minutes) != end:
        raise ValueError('incomplete final bar boundary')
    entry_signal = frame.long_signal.to_numpy()
    flat_signal = frame.short_signal.to_numpy()
    rows, position = [], None
    for i in range(len(frame) - 1):
        next_i = i + 1
        if position is None:
            if entry_signal[i] and start <= frame.index[next_i] < end:
                position = dict(entry_i=next_i, signal_i=i)
        elif flat_signal[i]:
            rows.append(trade_row(frame, position['entry_i'], next_i, 1, signal_i=position['signal_i']))
            position = None
    if position is not None:
        rows.append(trade_row(frame, position['entry_i'], len(frame) - 1, 1,
                              censored=True, signal_i=position['signal_i']))
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def buy_and_hold(frame, start, end, minutes):
    """The long-beta control: one entry at the window's first open, held out.

    Charged the same round-trip cost as one strategy trade, which flatters it
    relative to any arm that pays that cost more than once.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    frame = frame.loc[(frame.index >= start) & (frame.index + pd.Timedelta(minutes=minutes) <= end)]
    if frame.empty:
        raise ValueError('no bars in window')
    entry, exit_price = float(frame.open.iloc[0]), float(frame.close.iloc[-1])
    gross = exit_price / entry - 1
    return dict(entry_time=str(frame.index[0]), exit_time=str(frame.index[-1]), bars=len(frame),
                entry_price=entry, exit_price=exit_price, gross_return=gross, net_return=gross - COST,
                max_drawdown=float((frame.close / frame.close.cummax() - 1).min()))


def compounded(trades):
    """Sequential full-size reinvestment; censored marks are included as-is."""
    if not len(trades):
        return 0.
    return float(np.prod(1 + trades.net_return.to_numpy()) - 1)


def exposure(trades, frame, start, end, minutes):
    """Fraction of the window's bars spent holding, the long-beta dosage."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    window = frame.loc[(frame.index >= start) & (frame.index + pd.Timedelta(minutes=minutes) <= end)]
    if not len(window):
        raise ValueError('no bars in window')
    held = int((trades.exit_i - trades.entry_i).sum()) if len(trades) else 0
    return dict(window_bars=len(window), held_bars=held, time_in_market=held / len(window))
