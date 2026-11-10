"""VectorBT static barriers on OKX H1, with an explicit open before each unordered range.

The engine picks the earliest exit, including entry-hour barriers. The full H1
range uses VectorBT's conservative stop-first convention on dual touches.
Trade excursions use prices observable while open, plus separately bounded
exit-hour uncertainty. Independent columns have no shared wallet.
"""
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.signals.enums import StopType
from vectorbt.signals.nb import ohlc_stop_choice_nb
from vectorbt.portfolio.nb import get_stop_price_nb


@njit(cache=True)
def _barriers(o, h, l, c, starts, ends, signs, targets, stop_pct):
    out_idx = np.full(len(starts), -1)
    kind = np.full(len(starts), -1)
    fills = np.full(len(starts), np.nan)
    scratch = np.empty(1, dtype=np.int64)
    prices = np.empty(o.shape)
    types = np.empty(o.shape, dtype=np.int64)
    for k in range(len(starts)):
        first = 2*starts[k]
        entry = o[first,0]
        target_distance = signs[k]*(targets[k]/entry-1)
        # Avoid rounding an exactly touched stop or target one ULP away.
        eps = 4*np.finfo(np.float64).eps
        hit = ohlc_stop_choice_nb(first, ends[k]+1, 0, o, h, l, c, prices, types,
            max(0.,stop_pct-eps), False, max(0.,target_distance-eps), signs[k]<0,
            True, 0, True, scratch, True)
        if len(hit):
            i = hit[0]
            out_idx[k] = i
            kind[k] = types[i,0]
            if kind[k] == StopType.TakeProfit:
                fills[k] = targets[k]
            else:
                px = get_stop_price_nb(signs[k],entry,stop_pct,o[i,0],l[i,0],h[i,0],True)
                fills[k] = entry*(1-signs[k]*stop_pct) if np.isnan(px) else px
    return out_idx, kind, fills


def _symbol(bars, candidates, taker_fee, maker_fee, timeout_hours, stop_pct):
    f=candidates.reset_index(drop=True).copy()
    index=bars.index
    opens,high,low,close=bars[['open','high','low','close']].to_numpy(float).T
    starts=index.get_indexer(pd.DatetimeIndex(f.entry_time))
    if (starts<0).any():
        raise ValueError('Entry hour has no source bar')
    s=np.where(f.side.eq('lower'),1.,-1.)
    entry=opens[starts]
    target=np.where(s>0,f.low,f.high)
    if (s*(target-entry)<=0).any():
        raise ValueError('Entries must precede the target')
    stop=entry*(1-s*stop_pct)
    # A gap during the horizon censors an open trade, never interpolate.
    gaps=np.r_[np.flatnonzero(np.diff(index.as_unit('ns').asi8)!=3_600_000_000_000), len(index)-1]
    last_observed=gaps[np.searchsorted(gaps, starts)]
    timeout=starts+timeout_hours
    can_timeout=last_observed>=timeout
    ends=np.where(can_timeout,2*timeout,2*last_observed+1)
    # The open-only observation has known precedence on every H1 bar. At
    # timeout include its open only; no extrema after the liquidation clock.
    op=np.repeat(opens,2).reshape(-1,1)
    def interleave(values):
        return np.column_stack((opens,values)).reshape(-1,1)
    hit,kind,exit_px=_barriers(op,interleave(high),interleave(low),interleave(close),
                               starts,ends,s,target,stop_pct)
    ix=np.where(hit>=0,hit//2,np.minimum(timeout,last_observed))
    live=(hit<0)&~can_timeout
    exit_px=np.where(hit>=0,exit_px,np.where(live,close[ix],opens[ix]))
    reason=np.where(hit>=0,np.where(kind==StopType.TakeProfit,'target','stop'),
                    np.where(live,'censored','timeout'))
    exit_fee=np.where(kind==StopType.TakeProfit,maker_fee,taker_fee)
    qty=np.vstack((s,np.where(live,np.nan,-s)))
    px=np.vstack((entry,exit_px))
    fees=np.vstack((np.full(len(f),taker_fee),exit_fee))
    portfolio=vbt.Portfolio.from_orders(px, size=qty, price=px, size_type='amount',
        direction='both',fees=fees,init_cash=4*px.max(axis=0),cash_sharing=False,
        allow_partial=False,raise_reject=True,max_orders=2*len(f))
    records=portfolio.trades.records.sort_values('col')
    if len(records)!=len(f) or not np.array_equal(records.status.to_numpy()==0,live):
        raise AssertionError('VectorBT position status mismatch')
    net=100*records.pnl.to_numpy()/entry
    charge=100*(records.entry_fees.to_numpy()+records.exit_fees.to_numpy())/entry
    gross=(exit_px/entry-1)*s*100
    if not np.allclose(gross,net+charge,atol=1e-9):
        raise AssertionError('VectorBT trade fees do not reconcile')
    f['entry_fill']=entry; f['stop_price']=stop; f['target_price']=target
    f['exit_reason']=reason; f['status']=np.where(live,'open_censored','closed')
    f['exit_time']=pd.Series(index[ix]).mask(live)
    f['exit_price']=np.where(live,np.nan,exit_px)
    f['mark_time']=pd.Series(index[ix]+pd.Timedelta(hours=1)).where(live)
    f['mark_price']=np.where(live,exit_px,np.nan)
    f['exit_bar_close_time']=pd.Series(index[ix]+pd.Timedelta(hours=1)).mask(live)
    intrabar=(hit>=0)&(hit%2==1)
    f['exit_time_resolution']=np.where(live,'unclosed',np.where(intrabar,'within_hour','hour_open'))
    f['gross_return_pct']=np.where(live,np.nan,gross);f['fees_pct']=charge
    f['net_return_pct']=np.where(live,np.nan,net)
    f['unrealized_gross_pct']=np.where(live,gross,np.nan)
    f['unrealized_net_pct']=np.where(live,net,np.nan)
    f['engine_exit_type']=kind; f['is_censored']=live
    f['censor_reason']=np.where(live,np.where(last_observed==len(index)-1,'history_end','source_gap'),'')
    f['ambiguous_sl_tp']=intrabar & (low[ix]<=np.minimum(target,stop)) & (high[ix]>=np.maximum(target,stop))
    values=[]
    for k,(a,z) in enumerate(zip(starts,ix)):
        # For an open censor all observed bars through the mark count. For a
        # closed position only fully held bars and the actual exit fill count.
        end=z+int(live[k])
        fav_series=(high if s[k]>0 else low)[a:end]
        adv_series=(low if s[k]>0 else high)[a:end]
        fee_free_return=lambda p: (p/entry[k]-1)*s[k]*100
        def bound(series,direction):
            observed=direction*fee_free_return(series)
            base=float(direction*fee_free_return(exit_px[k]))
            amount=max(0.,base); stamp=z if amount>0 else a
            resolution=('hour_open' if (not intrabar[k] and not live[k]) or amount==0 else 'within_hour')
            if len(observed):
                best=np.argmax(observed)
                if observed[best]>0 and observed[best]>=amount:
                    amount=float(observed[best]);stamp=a+best;resolution='within_hour'
            if intrabar[k]:
                open_move=direction*fee_free_return(opens[z])
                if open_move>amount:
                    amount=float(open_move);stamp=z;resolution='hour_open'
            return amount,index[stamp],resolution
        mfe,ft,fr=bound(fav_series,1); mae,at,ar=bound(adv_series,-1)
        mfe_hi,mae_hi=mfe,mae
        if intrabar[k]:
            favorable=high[z] if s[k]>0 else low[z]
            adverse=low[z] if s[k]>0 else high[z]
            mfe_hi=max(mfe,min(fee_free_return(target[k]),fee_free_return(favorable)))
            mae_hi=max(mae,min(-fee_free_return(stop[k]),-fee_free_return(adverse)))
        values.append((mfe,ft,fr,mae,at,ar,mfe_hi,mae_hi))
    columns=['mfe_pct','mfe_time','mfe_time_resolution','mae_pct','mae_time','mae_time_resolution',
             'mfe_pct_optimistic_upper_bound','mae_pct_optimistic_upper_bound']
    for col, vals in zip(columns,zip(*values)):
        f[col]=vals
    f['mfe_mae_bounds_reason']=np.where(intrabar,'exit_hour_high_low_order_unobserved','')
    return f


def run_month_first_week_alt_trades(bars_by_symbol,candidates,*,taker_fee=.0005,maker_fee=.0002,
                                    timeout_hours=168,stop_pct=.05):
    if not candidates.trade_id.is_unique:
        raise ValueError('Duplicate trade id')
    out=[]
    for symbol, group in candidates.groupby('symbol',sort=False):
        bars=bars_by_symbol[symbol]
        entry=bars.open.reindex(pd.DatetimeIndex(group.entry_time)).to_numpy()
        if np.isnan(entry).any():
            raise ValueError('Entry hour has no source bar')
        past=np.where(group.side.eq('lower'),entry>=group.low,entry<=group.high)
        if past.any():
            out.append(group.loc[past].assign(status='skipped',exit_reason='entry_at_or_past_target',
                                               entry_fill=np.nan,net_return_pct=np.nan,is_censored=False))
        if not past.all():
            out.append(_symbol(bars,group.loc[~past],taker_fee,maker_fee,timeout_hours,stop_pct))
    return pd.concat(out,ignore_index=True) if out else candidates.copy()
