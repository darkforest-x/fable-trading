"""Native VectorBT primitives for standing-level fills and timed exits.

The OHLC crossing kernel is reused only to locate a pending limit's first
price crossing; it does not place a protective stop. Portfolio.from_orders
owns every position and per-fill commission. Each independent trade/scenario
has its own column, not shared cash. No synthetic intrabar timestamps exist.
Official primitives: https://vectorbt.dev/api/signals/nb/ and
https://vectorbt.dev/api/portfolio/base/ .
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.signals.nb import ohlc_stop_choice_nb
from vectorbt.portfolio.nb import get_stop_price_nb


@njit(cache=True)
def first_limits(prices,starts,ends,levels,sides):
    """Search predeclared pending windows using the native crossing kernel."""
    n=len(prices);hi=prices[:,1:2];lo=prices[:,2:3];cl=prices[:,3:4]
    px=np.empty((n,1));kind=np.empty((n,1),np.int64);scratch=np.empty(1,np.int64)
    out=np.full((len(starts),2),np.nan)
    for k in range(len(starts)):
        # Zero-distance barrier anchored at the known pending limit avoids
        # percentage reconstruction/rounding and retains inclusive touches.
        hit=ohlc_stop_choice_nb(starts[k],ends[k],0,np.asarray(levels[k]),hi,lo,cl,px,kind,
            0.,False,np.nan,sides[k]<0,True,0,True,scratch,True)
        if len(hit):
            i=hit[0];out[k,0]=i
            out[k,1]=get_stop_price_nb(sides[k],levels[k],0.,prices[i,0],prices[i,2],prices[i,1],True)
    return out


def account(events,symbol,cfg):
    """One execution for each distinct independent column, exact fill fees."""
    if events.empty:return events.copy(),pd.DataFrame()
    e=events.reset_index(drop=True).copy()
    # Bounds of opening fills and exact random clocks equal the grid result.
    key=['parent_id','horizon','entry','exit_price']
    u=e.drop_duplicates(key).copy().reset_index(drop=True);u['native_col']=np.arange(len(u))
    prices=np.vstack((u.entry.to_numpy(),u.exit_price.to_numpy()));side=u.side.to_numpy(float)
    fees=np.vstack((np.full(len(u),cfg['maker_fee']),np.full(len(u),cfg['taker_fee'])))
    if symbol=='XAUUSD':fees*=0
    pf=vbt.Portfolio.from_orders(prices,price=prices,size=np.vstack((side,-side)),
        size_type='amount',direction='both',fees=fees,init_cash=np.max(prices,axis=0)*4,
        cash_sharing=False,allow_partial=False,raise_reject=True,max_orders=2*len(u))
    trades=pf.trades.records.sort_values('col');fills=pf.orders.records.copy()
    assert len(trades)==len(u) and trades.status.eq(1).all() and len(fills)==2*len(u)
    charged=trades.entry_fees.to_numpy()+trades.exit_fees.to_numpy()
    u['fee_bp']=charged/u.entry.to_numpy()*1e4
    u['net_bp']=trades.pnl.to_numpy()/u.entry.to_numpy()*1e4
    u['gross_bp']=u.net_bp+u.fee_bp
    # Fill-level arithmetic verification is not a second execution engine.
    np.testing.assert_allclose(trades.entry_fees,u.entry*fees[0],atol=1e-10,rtol=1e-9)
    np.testing.assert_allclose(trades.exit_fees,u.exit_price*fees[1],atol=1e-10,rtol=1e-9)
    np.testing.assert_allclose(trades.pnl,side*(prices[1]-prices[0])-charged,atol=1e-10,rtol=1e-9)
    if symbol=='XAUUSD':u[['fee_bp','net_bp']]=np.nan
    e=e.merge(u[key+['native_col','fee_bp','net_bp','gross_bp']],on=key,validate='many_to_one',sort=False)
    fills['parent_id']=u.parent_id.to_numpy()[fills.col.to_numpy(int)]
    fills['horizon']=u.horizon.to_numpy()[fills.col.to_numpy(int)]
    fills['scenario']=u.scenario.to_numpy()[fills.col.to_numpy(int)]
    fills['event_interval_start']=[u.entry_time.iloc[c] if i==0 else u.exit_time.iloc[c] for c,i in zip(fills.col,fills.idx)]
    fills['fee_type']=np.where(fills.idx.eq(0),'maker_limit','taker_timed')
    e['engine_origin']='vectorbt_native_limit_crossing_and_portfolio'
    return e,fills
