"""Causal reconstruction of ChartArt's published Bollinger + RSI v1.1.

Source: TradingView script uCV8I4xA. RSI uses close changes and Wilder's
SMA-seeded RMA(6). Bands use current/past 200 closes and population standard
deviation, multiplier 2. Both crossings must occur on the same completed bar.
No future rows enter these features. Original orders are already activated
at creation because close crossed beyond their stop-entry level, so the
default historical broker emulator fills at the next open. These are entry
orders, not protective stop losses. Same-direction entries do not pyramid;
opposite entries reverse. There is no explicit take-profit or stop-loss.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rma(values, length):
    """SMA seed over first length non-NaNs, then Wilder recurrence."""
    values = np.asarray(values, float)
    result = np.full(len(values), np.nan)
    seed = []
    prev = np.nan
    for i, value in enumerate(values):
        if not np.isfinite(value):
            result[i] = prev
            continue
        if np.isnan(prev):
            seed.append(value)
            if len(seed) == length:
                prev = float(np.mean(seed))
        else:
            prev = (prev * (length - 1) + value) / length
        result[i] = prev
    return result


def features(frame):
    """Only close[t-199:t+1] and causal RSI state are observed at t."""
    result = frame.copy()
    close = result.close
    change = close.diff()
    gain = rma(change.clip(lower=0), 6)
    loss = rma(-change.clip(upper=0), 6)
    with np.errstate(divide='ignore', invalid='ignore'):
        result['rsi'] = np.where(loss == 0, 100., np.where(gain == 0, 0., 100 - 100 / (1 + gain / loss)))
    basis = close.rolling(200, min_periods=200).mean()
    dev = close.rolling(200, min_periods=200).std(ddof=0) * 2
    result['basis'], result['lower'], result['upper'] = basis, basis - dev, basis + dev
    result['long_signal'], result['short_signal'] = signal_masks(result)
    result['bandwidth'] = (result.upper - result.lower) / basis
    return result


def signal_masks(frame):
    """Both RSI and band crossings belong to this same closed bar."""
    r, close = frame.rsi, frame.close
    long = (r > 50) & (r.shift(1) <= 50) & (close > frame.lower) & (close.shift(1) <= frame.lower.shift(1))
    short = (r < 50) & (r.shift(1) >= 50) & (close < frame.upper) & (close.shift(1) >= frame.upper.shift(1))
    return long, short


TRADE_COLUMNS = ['entry_i', 'exit_i', 'signal_i', 'entry_time', 'exit_time', 'side',
                 'entry_price', 'exit_price', 'gross_return', 'net_return',
                 'mae_return', 'mfe_return', 'censored', 'exit_reason']


def trade_row(frame, entry_i, exit_i, side, *, censored=False, signal_i=None):
    """Outcome-only future inspection, including the entry bar's full range.

    A natural exit is at exit_i open: its later high/low is excluded. A
    boundary-valued position includes the last bar but is explicitly censored.
    """
    entry = float(frame.open.iloc[entry_i])
    exit_price = float(frame.close.iloc[exit_i] if censored else frame.open.iloc[exit_i])
    path = frame.iloc[entry_i:exit_i + int(censored)]
    high = max(float(path.high.max()), entry, exit_price)
    low = min(float(path.low.min()), entry, exit_price)
    gross = side * (exit_price / entry - 1)
    return dict(entry_i=int(entry_i), exit_i=int(exit_i), signal_i=signal_i,
                entry_time=str(frame.index[entry_i]), exit_time=str(frame.index[exit_i]), side=int(side),
                entry_price=entry, exit_price=exit_price, gross_return=gross, net_return=gross-.002,
                mae_return=(low/entry-1) if side == 1 else (1-high/entry),
                mfe_return=(high/entry-1) if side == 1 else (1-low/entry),
                censored=bool(censored), exit_reason='boundary_mark' if censored else 'opposite_entry')


def replay(frame, start, end, minutes):
    """Default close calculation / next-open fills; no date-warmup position."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    frame = frame.loc[frame.index + pd.Timedelta(minutes=minutes) <= end]
    if frame.empty or frame.index[-1] + pd.Timedelta(minutes=minutes) != end:
        raise ValueError('incomplete final bar boundary')
    rows = []
    position = None
    for i in np.flatnonzero((frame.long_signal | frame.short_signal).to_numpy()):
        next_i = int(i)+1
        if next_i >= len(frame) or not start <= frame.index[next_i] < end:
            continue
        side = 1 if frame.long_signal.iloc[i] else -1
        if position is not None and position['side'] == side:
            continue
        # The signal-close is beyond its own stop-entry level. At the next
        # tick it executes even if the next open gaps back through that level.
        if position is not None:
            rows.append(trade_row(frame, position['entry_i'], next_i, position['side'], signal_i=position['signal_i']))
        position = dict(entry_i=next_i, side=side, signal_i=int(i))
    if position is not None:
        rows.append(trade_row(frame, position['entry_i'], len(frame)-1, position['side'],
                              censored=True, signal_i=position['signal_i']))
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)
