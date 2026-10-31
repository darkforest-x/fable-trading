"""Native VectorBT primitives for the 12h reclaim strategy comparison.

Only VectorBT's crossing kernel locates limit/SL/TP hits. The four segments
per 15m candle are a point at the observed open followed by O-H-L-C or
O-L-H-C. They express ordering assumptions, never synthetic timestamps.
Removing the pre-entry part of a segment prevents a resting order from
receiving a target that happened before its fill. Static partial legs share
the original stop and can therefore reuse the same native crossing results.
Portfolio.from_orders owns positions, fractional legs, fees and PnL.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import vectorbt as vbt
from yoyo.evaluation.monday_timed_engine import first_limits


def path_segments(prices, path):
    """Return four monotone OHLC segments, including explicit opening quotes."""
    o, h, l, c = np.asarray(prices, float).T
    a, b = (h, l) if path == 'OHLC' else (l, h)
    starts = np.column_stack((o, o, a, b)).ravel()
    ends = np.column_stack((o, a, b, c)).ravel()
    return np.column_stack((starts, np.maximum(starts, ends),
                            np.minimum(starts, ends), ends))


def crossing(segments, start, end, level, side):
    """Native inclusive first crossing; end is exclusive."""
    if start >= end:
        return None
    result = first_limits(segments, np.array([start]), np.array([end]),
                          np.array([level]), np.array([side]))[0]
    return None if np.isnan(result[0]) else (int(result[0]), float(result[1]))


def after_entry(segments, entry_segment, end, price):
    """Clip only the entry segment, keeping its remaining monotone path."""
    tail = segments[entry_segment:end].copy()
    finish = tail[0, 3]
    tail[0] = [price, max(price, finish), min(price, finish), finish]
    return tail


def native_barriers(tail, stop, target, side):
    """Locate two native barriers once; no Python/Numba bar-by-bar fill loop."""
    sl = crossing(tail, 0, len(tail), stop, side)
    tp = crossing(tail, 0, len(tail), target, -side)
    if sl is not None and (tp is None or sl[0] <= tp[0]):
        return sl[0], sl[1], 'stop'
    if tp is not None:
        # Conservative resting target: no favorable gap-price improvement.
        return tp[0], target, 'target'
    return None


def account_legs(legs, cfg):
    """Execute unique legs; censored legs remain open and are never wins."""
    if not len(legs):
        return legs.copy(), pd.DataFrame()
    x = legs.reset_index(drop=True).copy()
    side = x.side.to_numpy(float)
    weight = x.weight.to_numpy(float)
    closed = x.closed.to_numpy(bool)
    prices = np.vstack((x.entry, x.exit_price)).astype(float)
    units = 100./prices[0]  # Stable $100 notional, including micro-priced coins.
    fees = np.vstack((x.entry_fee, np.where(x.reason.eq('target'),
                                           cfg['maker_fee'], cfg['taker_fee'])))
    amounts = np.vstack((side*weight*units, np.where(closed, -side*weight*units, 0.)))
    pf = vbt.Portfolio.from_orders(
        prices, price=prices, size=amounts, size_type='amount', direction='both',
        fees=fees, cash_sharing=False, init_cash=np.max(prices, axis=0)*units*4,
        allow_partial=False, raise_reject=True, max_orders=2*len(x))
    r = pf.trades.records.sort_values('col')
    assert len(r) == len(x) and np.array_equal(r.status.eq(1), closed)
    charged = r.entry_fees.to_numpy()+r.exit_fees.to_numpy()
    x['fee_r'] = charged/(x.risk*units)
    x['net_r'] = r.pnl.to_numpy()/(x.risk*units)
    x['gross_r'] = (r.pnl.to_numpy()+charged)/(x.risk*units)
    x['net_bp'] = r.pnl.to_numpy()/100.*1e4
    x['base_units'] = units*weight
    np.testing.assert_allclose(r.pnl, side*weight*units*(prices[1]-prices[0])-charged,
                               rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(r.entry_fees, prices[0]*fees[0]*weight*units, rtol=1e-9)
    np.testing.assert_allclose(r.exit_fees, prices[1]*fees[1]*closed*weight*units, rtol=1e-9)
    fills = pf.orders.records.copy()
    fills['leg_id'] = x.leg_id.to_numpy()[fills.col.to_numpy(int)]
    fills['interval_start'] = [x.entry_time.iloc[c] if i == 0 else x.exit_time.iloc[c]
                                for c, i in zip(fills.col, fills.idx)]
    fills['fee_type'] = [
        ('maker' if x.entry_fee.iloc[c] == cfg['maker_fee'] else 'taker') if i == 0
        else ('maker' if x.reason.iloc[c] == 'target' else 'taker')
        for c, i in zip(fills.col, fills.idx)]
    assert len(fills) == len(x)+int(closed.sum())
    return x, fills
