"""Causal wick-R exits using VectorBT native first-hit and portfolio kernels.

Owner 2026-10-26: keep the existing limit fill, use a percentage backstop
until confirmation, and learn the touch candle's adverse extreme only at its
close. Targets start at that close. The immediately next 5m candle confirms
strictly inside the level or exits at its close. Successful confirmation
installs entry protection, then fee protection at the first completed close
with room. Native OHLC stops conservatively precede targets, except that an
observed open always precedes both extremes. No custom per-bar fill loop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit
import vectorbt as vbt
from vectorbt.signals.nb import ohlc_stop_choice_nb
from vectorbt.signals.enums import StopType
from vectorbt.portfolio.nb import get_stop_price_nb


@njit(cache=True)
def first_hit(o, h, l, c, start, end, reference, stop, target, side, po, ty, scratch):
    """Rebase native static barriers to a known preceding close (one sentinel).

Arrays include a leading sentinel, permitting an entry at source index zero.
The sentinel open is restored after each call; all actual matching bars are
unchanged. Prices of gapped stops come from VectorBT get_stop_price_nb.
"""
    if start > end:
        return -1, np.nan, 0, 0
    old = o[start-1, 0]
    o[start-1, 0] = reference
    sl = side*(reference-stop)/reference if np.isfinite(stop) else np.nan
    tp = side*(target-reference)/reference if np.isfinite(target) else np.nan
    eps = 4*np.finfo(np.float64).eps
    sl = max(0., sl-eps) if np.isfinite(sl) else sl
    tp = max(0., tp-eps) if np.isfinite(tp) else tp
    hits = ohlc_stop_choice_nb(start, end+1, 0, o, h, l, c, po, ty,
                              sl, False, tp, side < 0, True, 1, True, scratch, True)
    o[start-1, 0] = old
    if not len(hits):
        return -1, np.nan, 0, 0
    i = hits[0]
    kind = 1 if ty[i, 0] == StopType.StopLoss else 4
    price = stop if kind == 1 else target
    stop_open = np.isfinite(stop) and side*(o[i, 0]-stop) <= 0
    target_open = np.isfinite(target) and side*(o[i, 0]-target) >= 0
    both = (np.isfinite(stop) and np.isfinite(target)
            and l[i, 0] <= min(stop, target) and h[i, 0] >= max(stop, target)
            and not stop_open and not target_open)
    if target_open and not stop_open:
        return i, target, 4, 0
    if kind == 1:
        exact_sl = side*(reference-stop)/reference
        gap = get_stop_price_nb(side, reference, exact_sl, o[i, 0], l[i, 0], h[i, 0], True)
        price = gap if np.isfinite(gap) and stop_open else stop
    return i, price, kind, int(both)


@njit(cache=True)
def confirmed_exit(o, h, l, c, confirm, end, entry, target, side, fee_be, po, ty, scratch):
    """Native barriers between close-known protection changes, never retroactive."""
    # The first qualifying completed close is an event time, not a feature
    # available earlier. The old protection remains live through that close.
    closes = c[confirm:end+1, 0]
    eligible = np.flatnonzero(side*(closes-fee_be) > 1e-12*entry)
    arm = confirm+eligible[0] if len(eligible) else -1
    old_end = arm if arm >= 0 else end
    if old_end > confirm:
        i, price, why, ambiguous = first_hit(o,h,l,c,confirm+1,old_end,c[confirm,0],entry,target,side,po,ty,scratch)
        if why:
            return i, price, 6 if why == 1 else 4, 0, ambiguous
    if arm >= 0:
        i, price, why, ambiguous = first_hit(o,h,l,c,arm+1,end,c[arm,0],fee_be,target,side,po,ty,scratch)
        if why:
            return i, price, 5 if why == 1 else 4, 1, ambiguous
        return end, c[end,0], 0, 1, 0
    return end, c[end,0], 0, 0, 0


@njit(cache=True)
def dispatch(prices, entries, touch_ends, confirms, ends, entry_prices, sides, rs,
             stops, targets, maker, taker):
    """Broadcast event/parameter columns to native kernels in bounded memory.

Inputs use only frozen fills, complete touch OHLC at touch close, and the
immediate confirmation close. Final touch R on already stopped rows is an
ex-post measurement and never determines the initial order or quantity.
"""
    n = len(prices)
    o=np.ascontiguousarray(prices[:,0:1]);h=np.ascontiguousarray(prices[:,1:2])
    l=np.ascontiguousarray(prices[:,2:3]);c=np.ascontiguousarray(prices[:,3:4])
    po = np.empty((n,1)); ty = np.empty((n,1),dtype=np.int64); scratch=np.empty(1,dtype=np.int64)
    out = np.empty((len(entries)*len(stops)*len(targets),9))
    row=0
    for k in range(len(entries)):
        a,b,q,z = entries[k],touch_ends[k],confirms[k],ends[k]
        entry,side,r = entry_prices[k],sides[k],rs[k]
        be = entry*(1+side*maker)/(1-side*taker)
        post = np.empty((len(targets),5))
        for it in range(len(targets)):
            target = entry+side*targets[it]*r if r > 0 else np.nan
            if target <= 0: target=np.nan
            if q <= z and side*(c[q,0]-entry)>0 and not (np.isfinite(target) and side*(c[q,0]-target)>=0):
                post[it,:] = np.array(confirmed_exit(o,h,l,c,q,z,entry,target,side,be,po,ty,scratch))
            else:
                post[it,:] = np.array([z,c[z,0],0,0,0])
        for js in range(len(stops)):
            stop=entry*(1-side*stops[js]) if np.isfinite(stops[js]) else np.nan
            # No TP is known during the touch candle. Its adverse extreme is
            # necessarily after crossing the entry; favorable highs are ignored.
            ei,ep,why,amb=first_hit(o,h,l,c,a,min(b,z),entry,stop,np.nan,side,po,ty,scratch)
            for it in range(len(targets)):
                armed=0;confirmed=0; ambiguous=amb
                x,p,reason=ei,ep,12 if why else 0
                target=entry+side*targets[it]*r if r>0 else np.nan
                if target<=0:target=np.nan
                if not why:
                    if b>z:
                        x,p,reason=z,c[z,0],0
                    elif np.isfinite(target) and side*(c[b,0]-target)>=0:
                        # Newly installed target is marketable now, taker.
                        x,p,reason=b,c[b,0],14
                    else:
                        x,p,reason,ambiguous=first_hit(o,h,l,c,b+1,min(q,z),c[b,0],stop,target,side,po,ty,scratch)
                        if reason==1:reason=12
                        if not reason:
                            if q>z:x,p,reason=z,c[z,0],0
                            elif side*(c[q,0]-entry)<=0:x,p,reason=q,c[q,0],11
                            else:
                                confirmed=1
                                x,p,reason,armed,ambiguous=post[it,:]
                out[row,:]=np.array([k,js,it,x,p,reason,armed,confirmed,ambiguous])
                row+=1
    return out


def execute(frame, events, stops, targets, maker=.0002, taker=.0005, slip_bp=10.):
    """Produce actual VectorBT fill/position accounting, independent one-unit trades."""
    if events.empty:return pd.DataFrame(),pd.DataFrame()
    p=frame[['open','high','low','close']].to_numpy(float)
    prices=np.vstack((p[0],p))
    raw=dispatch(prices,events.entry_i.to_numpy(int)+1,events.touch_end_i.to_numpy(int)+1,
                 events.confirm_i.to_numpy(int)+1,events.end_i.to_numpy(int)+1,
                 events.entry.to_numpy(float),events.side.to_numpy(float),events.wick_r.to_numpy(float),
                 np.asarray(stops,float),np.asarray(targets,float),maker,taker)
    e=events.iloc[raw[:,0].astype(int)].reset_index(drop=True).copy()
    e['stop_pct']=np.asarray(stops)[raw[:,1].astype(int)]*100
    e['target_r']=np.asarray(targets)[raw[:,2].astype(int)]
    e['exit_i']=raw[:,3].astype(int)-1; e['exit_price']=raw[:,4]
    e['reason']=raw[:,5].astype(int);e['fee_be_armed']=raw[:,6].astype(bool)
    e['confirmed']=raw[:,7].astype(bool);e['ambiguous']=raw[:,8].astype(bool)
    e['exit_ns']=frame.index.as_unit('ns').asi8[e.exit_i]+events.attrs.get('step_minutes',5)*60_000_000_000
    e['closed']=e.reason.ne(0)
    fees=np.vstack((np.full(len(e),maker),np.where(e.reason.eq(4),maker,taker)))
    ep=np.vstack((e.entry,e.exit_price));s=e.side.to_numpy(float)
    pf=vbt.Portfolio.from_orders(ep,size=np.vstack((s,-s)),price=ep,size_type='amount',direction='both',
        fees=fees,init_cash=np.max(ep,axis=0)*4,cash_sharing=False,allow_partial=False,raise_reject=True,max_orders=2*len(e))
    rec=pf.trades.records.sort_values('col')
    assert len(rec)==len(e) and rec.status.eq(1).all()
    charged=rec.entry_fees.to_numpy()+rec.exit_fees.to_numpy()
    e['net_bp']=rec.pnl.to_numpy()/e.entry*10000
    e['fee_bp']=charged/e.entry*10000
    e['gross_bp']=e.net_bp+e.fee_bp
    e['slippage_bp']=np.where(e.reason.eq(4),0.,slip_bp)
    e['net_after_slip_bp']=e.net_bp-e.slippage_bp
    e['net_r']=rec.pnl.to_numpy()/e.wick_r.where(e.wick_r>0)
    e['net_after_slip_r']=e.net_after_slip_bp/10000*e.entry/e.wick_r.where(e.wick_r>0)
    e['fee_exit_rate']=fees[1]
    # Censored valuations are retained separately, not booked as closed profits.
    e['censored_mark_bp']=e.net_after_slip_bp.where(~e.closed)
    e.loc[~e.closed,['net_bp','gross_bp','net_after_slip_bp','net_r','net_after_slip_r']]=np.nan
    fills=pf.orders.records.copy()
    fills['trade_row']=fills.col
    return e,fills
