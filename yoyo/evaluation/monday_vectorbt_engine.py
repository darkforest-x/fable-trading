"""VectorBT execution for independent, static-barrier Monday research trades.

All first-hit searches use VectorBT's native ohlc_stop_choice_nb; gap stop
prices use its native get_stop_price_nb. Portfolio.from_orders processes the
resulting entry/exit events, commissions and independent position accounting.
No custom per-bar fill loop and no second framework replay are used.

The established O-H-L-C hypothesis is encoded as three monotone OHLC segments
per observed five-minute candle. These are ordering assumptions, NOT observed
lower-timeframe candles. Entry is at the original open and a protective exit
can occur in that same candle. Each independent trade occupies a separate
portfolio column. The two-row event ledger is NOT a shared cash equity curve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.signals.nb import ohlc_stop_choice_nb
from vectorbt.signals.enums import StopType
from vectorbt.portfolio.nb import get_stop_price_nb


@njit(cache=True)
def native_exits(open_, high, low, close, starts, ends, sides, sl, tp):
    """Dispatch independent candidates to native kernels, never scan bars here."""
    n = len(starts)
    result = np.empty((n, 3))
    price_out = np.empty((len(open_), 1))
    type_out = np.empty((len(open_), 1), dtype=np.int64)
    scratch = np.empty(1, dtype=np.int64)
    for k in range(n):
        hit = ohlc_stop_choice_nb(
            starts[k], ends[k] + 1, 0, open_, high, low, close,
            price_out, type_out, sl[k], False, tp[k], sides[k] < 0,
            True, 0, True, scratch, True)
        if len(hit):
            i = hit[0]
            kind = type_out[i, 0]
            price = price_out[i, 0]
            # The observed open precedes the O-H segment. For a short whose
            # open gaps below TP and whose high later crosses SL, the native
            # OHLC stop-first default would otherwise select that later SL.
            if kind == StopType.StopLoss and i % 3 == 0:
                at_open = get_stop_price_nb(sides[k], open_[starts[k], 0], tp[k],
                                            open_[i, 0], open_[i, 0], open_[i, 0], False)
                if not np.isnan(at_open):
                    kind = StopType.TakeProfit
                    price = open_[starts[k], 0] * (1 + sides[k] * tp[k])
            if kind == StopType.StopLoss:
                price = get_stop_price_nb(sides[k], open_[starts[k], 0], sl[k],
                                          open_[i, 0], low[i, 0], high[i, 0], True)
            result[k, 0] = i
            result[k, 1] = price
            result[k, 2] = 1 if kind == StopType.StopLoss else 4
        else:
            result[k, 0] = ends[k]
            result[k, 1] = close[ends[k], 0]
            result[k, 2] = 0
    return result


def segments(prices):
    """Encode the baseline path without inventing a higher timestamp resolution."""
    o, h, l, c = prices.T
    starts = np.column_stack((o, h, l)).reshape(-1, 1)
    finishes = np.column_stack((h, l, c)).reshape(-1, 1)
    return starts, np.maximum(starts, finishes), np.minimum(starts, finishes), finishes


def execute(frame, entries, symbol, cfg):
    """Run new candidates once and return native accounting plus event receipts."""
    if entries.empty:
        return entries.copy(), pd.DataFrame()
    prices = frame[['open', 'high', 'low', 'close']].to_numpy(float)
    decimals = max(2, min(9, int(np.ceil(-np.log10(np.min(prices)))) + 7))
    prices = np.round(prices, decimals)
    e = entries.copy().reset_index(drop=True)
    starts = e.entry_i.to_numpy(int)
    ends = np.full(len(e), len(frame) - 1, dtype=int)
    if symbol != 'XAUUSD':
        gaps = np.flatnonzero(np.diff(frame.index.as_unit('ns').asi8) != 300_000_000_000)
        slots = np.searchsorted(gaps, starts)
        valid = slots < len(gaps)
        ends[valid] = gaps[slots[valid]]
    elif 'quality_segment' in frame:
        boundaries = np.flatnonzero(np.diff(frame.quality_segment.to_numpy()) != 0)
        slots = np.searchsorted(boundaries, starts)
        valid = slots < len(boundaries)
        ends[valid] = boundaries[slots[valid]]
    sides = e.side.to_numpy(float)
    entry = e.entry.to_numpy(float)
    sl = e.risk.to_numpy(float) / entry
    tp = sides * (e.target_price.to_numpy(float) - entry) / entry
    # Percentage roundtrips can reconstruct110 as110.00000000000001 and
    # miss an inclusive exact-price touch. Move only within floating precision,
    # strictly below a quarter of the baseline decimal quantum; fills are
    # restored to that same quantum below. This cannot move a price-grid level.
    epsilon = 4*np.finfo(float).eps
    if np.any(epsilon*entry >= (10.**(-decimals))/4):
        raise ValueError('Price precision cannot safely represent inclusive barriers')
    sl = np.maximum(np.nextafter(0.,1.), sl-epsilon)
    tp = np.maximum(np.nextafter(0.,1.), tp-epsilon)
    result = native_exits(*segments(prices), starts * 3, ends * 3 + 2, sides, sl, tp)
    e['exit_original_i'] = (result[:, 0] // 3).astype(int)
    e['exit_path_segment'] = (result[:, 0] % 3).astype(int)
    e['exit_price'] = np.round(result[:, 1], decimals)
    e['reason'] = result[:, 2].astype(int)
    e['closed'] = e.reason.ne(0)
    e['exit_interval_start'] = [str(frame.index[i]) for i in e.exit_original_i]
    e['exit_interval_end'] = [str(frame.index[i] + pd.Timedelta(minutes=5)) for i in e.exit_original_i]
    spot = symbol == 'XAUUSD'
    fees = np.vstack((np.full(len(e), 0 if spot else cfg['taker_fee']),
                      np.zeros(len(e)) if spot else np.where(e.reason.eq(4), cfg['maker_fee'], cfg['taker_fee'])))
    execution_prices = np.vstack((entry, e.exit_price.to_numpy()))
    # One base unit per independent trade; sufficient independent cash avoids
    # portfolio-level rejection or parameter columns sharing capital.
    portfolio = vbt.Portfolio.from_orders(
        execution_prices, size=np.vstack((sides, -sides)), price=execution_prices,
        size_type='amount', direction='both', fees=fees, init_cash=np.max(execution_prices, axis=0)*4,
        cash_sharing=False, allow_partial=False, raise_reject=True, max_orders=2*len(e),
    )
    records = portfolio.trades.records.sort_values('col')
    if len(records) != len(e) or not records.status.eq(1).all():
        raise AssertionError('Every independent framework position must have two successful fills')
    fill_records = portfolio.orders.records
    if len(fill_records) != 2*len(e):
        raise AssertionError('Rejected or missing framework fill')
    charged = records.entry_fees.to_numpy() + records.exit_fees.to_numpy()
    gross_pnl = records.pnl.to_numpy() + charged
    e['gross_r'] = gross_pnl/e.risk.to_numpy()
    e['gross_bp'] = gross_pnl/entry*10000
    e['fee_r'] = np.nan if spot else charged/e.risk.to_numpy()
    e['net_r'] = np.nan if spot else records.pnl.to_numpy()/e.risk.to_numpy()
    e['net_bp'] = np.nan if spot else records.pnl.to_numpy()/entry*10000
    e['fee_entry_rate'] = np.nan if spot else fees[0]
    e['fee_exit_rate'] = np.nan if spot else fees[1]
    e['engine_origin'] = 'vectorbt_1.2.0_native_stops_and_portfolio_open_priority'
    # Sensitivity uses the already-known first exit candle, not another path
    # search or simulation. It does not influence orders or candidate selection.
    last = prices[e.exit_original_i.to_numpy()]
    both = (last[:, 2] <= np.minimum(e.stop, e.target_price)) & (last[:, 1] >= np.maximum(e.stop, e.target_price))
    inside = (sides*(last[:, 0]-e.stop) > 0) & (sides*(last[:, 0]-e.target_price) < 0)
    e['ambiguous_bar'] = e.closed & both & inside
    alt_price = np.where(e.ambiguous_bar, e.stop, e.exit_price)
    e['stop_first_gross_r'] = sides*(alt_price-entry)/e.risk
    e['stop_first_net_r'] = np.nan if spot else e.stop_first_gross_r-(entry*fees[0]+alt_price*np.where(e.ambiguous_bar,cfg['taker_fee'],fees[1]))/e.risk
    fill_records = fill_records.copy()
    fill_records['trade_key'] = e.trade_key.to_numpy()[fill_records.col.to_numpy(int)]
    fill_records['event_time'] = [e.entry_time.iloc[col] if idx == 0 else e.exit_interval_start.iloc[col]
                                  for col, idx in zip(fill_records.col, fill_records.idx)]
    return e, fill_records
