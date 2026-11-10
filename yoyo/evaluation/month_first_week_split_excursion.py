"""Gross marked excursion bounds for a frozen pair of equal short positions.

Each value is a percent of original total notional and includes the locked-in
profit of any exited child. For an intrabar exit, an hour's wick can come before
or after the fill. Bound the value at that price under both possibilities.
"""
import numpy as np
import pandas as pd


def measure(parents, quotes):
    output = []
    for row in parents.itertuples(index=False):
        clock, prices = quotes(row.symbol)
        a, z = np.searchsorted(clock, [row.entry_ms, row.near_exit_ms])
        times = clock[a:z+1]
        if (times[0] != row.entry_ms or times[-1] != row.near_exit_ms or
                np.any(np.diff(times) != 3_600_000) or row.cap_exit_ms > row.near_exit_ms):
            raise ValueError(f'Incomplete marked path for {row.event_id}')
        opening, high, low, close = prices[a:z+1].T
        # Opening and closing marks are known. For the low and high of an
        # exit candle track an inner and an outer bound on marked profit.
        marks = np.zeros((6, len(times)))
        for leg in ('cap', 'near'):
            exit_ms = getattr(row, leg+'_exit_ms')
            exit_price = getattr(row, leg+'_exit_price')
            target = getattr(row, leg+'_target')
            at_open = getattr(row, leg+'_phase') == 'open'
            intrabar = (times == exit_ms) & (not at_open)
            settled = (times > exit_ms) | ((times == exit_ms) & at_open)
            # A resting target caps the usable favorable wick. A jump through
            # a short stop can settle above it; the frozen fill captures that.
            low_live = np.maximum(low, target)
            high_live = np.minimum(high, row.stop_price)
            possible_low = np.where(intrabar, np.minimum(low_live,exit_price), low_live)
            certain_low = np.where(intrabar, np.maximum(low_live,exit_price), low_live)
            possible_high = np.where(intrabar, np.maximum(high_live,exit_price), high_live)
            certain_high = np.where(intrabar, np.minimum(high_live,exit_price), high_live)
            price_marks = np.stack((opening, np.where(intrabar,exit_price,close),
                                    certain_low,possible_low,certain_high,possible_high))
            marks += 50 * (1 - np.where(settled,exit_price,price_marks) / row.entry_price)
        known_peak = max(0., marks[:2].max(), marks[2].max())
        known_trough = min(0., marks[:2].min(), marks[4].min())
        possible_peak = max(known_peak, marks[3].max())
        possible_trough = min(known_trough, marks[5].min())
        output.append(dict(event_id=row.event_id,
                           gross_mfe_pct_lower_bound=known_peak,
                           gross_mfe_pct_upper_bound=possible_peak,
                           gross_max_float_loss_pct_lower_bound=-known_trough,
                           gross_max_float_loss_pct_upper_bound=-possible_trough))
    return pd.DataFrame(output)
