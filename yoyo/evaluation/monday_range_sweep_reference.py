"""Require a new prior20-bar extreme before V2.31 touch pairing.

Only completed OHLC are read: touch[j] against high/low[j-window:j].
The touch itself is excluded from history; equality does not sweep. The
existing selector retains key priority, body gates and earliest-close entry.
No reclaim of the previous extreme is added: the original key-wick rejection
already remains required. Long/short masks are independent.
"""
import numpy as np

from yoyo.evaluation.monday_range_v2_31_reference import select_matches


def sweep_mask(quarter, side, *, window=20, tick=.1):
    """A full preceding window is required; no current/future candle in it."""
    if side not in (-1, 1) or window < 1 or tick <= 0:
        raise ValueError('Invalid direction, lookback or tick')
    x = quarter.low if side == 1 else quarter.high
    history = x.shift(1).rolling(window, min_periods=window)
    extreme = history.min() if side == 1 else history.max()
    return (x < extreme-tick*1e-6 if side == 1 else x > extreme+tick*1e-6).to_numpy()


def select_sweep_matches(quarter, arrows, prices, labels, weeks, *, window=20, **kwargs):
    """Reject ineligible touch rows BEFORE selecting a key/arrow pair."""
    selected = []
    arrows = np.asarray(arrows)
    for side in (-1, 1):
        eligible = sweep_mask(quarter, side, window=window, tick=kwargs.get('tick', .1))
        grid = np.asarray(prices, dtype=float).copy()
        grid[~eligible] = np.nan
        side_arrows = np.where(arrows == side, arrows, 0)
        selected.extend(select_matches(quarter, side_arrows, grid, labels, weeks, **kwargs))
    return sorted(selected, key=lambda r: (r['entry_i'], r['signal_i']))
