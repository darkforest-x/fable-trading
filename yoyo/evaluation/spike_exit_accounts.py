"""Independent marked accounts for fixed SPIKE exit-policy ledgers.

The only price inputs are source bar open/close and actual fill prices. Each
quantity is frozen from entry equity and initial risk, never from future PnL.
Marks use each bar close after its executed fills; future returns are used only
for evaluation. Entries/partials cost 10bp of original entry notional per filled
fraction at the corresponding side. Unexecuted residuals reserve their exit fee.
Accounts are separate per source market/timeframe, not a synthetic shared pool.
Uncapped sizing is explicitly mathematical stress: maintenance margin, funding,
mark prices, capacity and actual exchange liquidation are not modeled.
"""
from __future__ import annotations

import math
from typing import Optional
import numpy as np
import pandas as pd


def _utc(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError('explicit timezone required')
    return stamp.tz_convert('UTC')


def marked_account(bars: pd.DataFrame, trades: pd.DataFrame, fills: pd.DataFrame, *,
                   risk_fraction: float, notional_cap: Optional[float] = 1.0,
                   initial: float = 10000.0, roundtrip_cost: float = .002,
                   start='2024-09-10T00:00:00Z', end='2026-09-10T00:00:00Z',
                   minutes: int = 60):
    """Price a nonoverlapping stream; return close NAV, allocations and metadata.

    Required trade fields: trade_id,entry_time,entry_price,initial_risk,side.
    Fill fields: trade_id,kind,bar_open,execution_phase,qty_fraction,price.
    Censor at a known boundary close is estimated closeout; an unpriced gap
    invalidates the account instead of dropping that position's loss.
    """
    if not 0 < risk_fraction < 1 or initial <= 0 or roundtrip_cost < 0:
        raise ValueError('invalid account settings')
    if notional_cap is not None and notional_cap <= 0:
        raise ValueError('positive cap or None required')
    start,end=_utc(start),_utc(end)
    if not isinstance(bars.index,pd.DatetimeIndex) or bars.index.tz is None or not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError('unique sorted UTC source index required')
    bar_index=bars.index.tz_convert('UTC')
    step=pd.Timedelta(minutes=minutes)
    sel=(bar_index>=start)&(bar_index<end)
    frame=bars.loc[sel].copy(); frame.index=bar_index[sel]
    opens=frame.open.to_numpy(float); closes=frame.close.to_numpy(float)
    index=frame.index; nav=np.full(len(frame),float(initial)); cash=float(initial)
    half=roundtrip_cost/2; cursor=0; allocations=[]; ruined=False; invalid=False
    max_leverage=0.; boundary_marks=0; max_arithmetic_error=0.
    if len(trades)==0:
        return pd.DataFrame({'time':index+step,'equity':nav}),pd.DataFrame(),dict(ruined=False,invalid=False,boundary_marks=0,max_observed_close_leverage=0.,max_net_return_error=0.)
    records=trades.sort_values(['entry_time','trade_id']).to_dict('records')
    if trades.trade_id.duplicated().any():raise ValueError('duplicate trade id')
    groups={key:part for key,part in fills.groupby('trade_id',sort=False)}
    last_close_time=None
    for row in records:
        if ruined or invalid:break
        t=_utc(row['entry_time'])
        if not start<=t<end:continue
        ei=index.get_indexer([t])[0]
        if ei<0:raise ValueError('entry has no source open')
        if last_close_time is not None and t<last_close_time:raise ValueError('overlapping stream positions')
        entry=float(row['entry_price']);risk=float(row['initial_risk']);side=int(row['side'])
        if side not in(-1,1) or not all(math.isfinite(x) and x>0 for x in(entry,risk)):
            raise ValueError('invalid position risk/price/side')
        if not np.isclose(opens[ei],entry,rtol=1e-10,atol=0):raise ValueError('entry/source price mismatch')
        nav[cursor:ei]=cash
        if row['trade_id'] not in groups:raise ValueError('missing fills')
        legs=groups[row['trade_id']]
        exits=legs.loc[legs.kind.ne('entry')].copy()
        if len(exits)==0:raise ValueError('unvalued open position')
        if not np.isfinite(pd.to_numeric(exits.price,errors='coerce')).all():
            invalid=True;nav[ei:]=np.nan;break
        fractions=exits.qty_fraction.to_numpy(float)
        if np.any(fractions<=0) or not np.isclose(fractions.sum(),1.,atol=1e-9):raise ValueError('original quantity not conserved')
        if not exits.execution_phase.isin(['open','intrabar','close']).all():raise ValueError('unknown execution phase')
        bar_times=pd.DatetimeIndex(pd.to_datetime(exits.bar_open,utc=True))
        positions=index.get_indexer(bar_times)
        if np.any(positions<ei):raise ValueError('exit before entry or missing source bar')
        order=np.lexsort((exits.execution_phase.map({'open':0,'intrabar':1,'close':2}).to_numpy(),positions))
        exits=exits.iloc[order];positions=positions[order];fractions=fractions[order]
        final_i=int(positions[-1]);final_phase=str(exits.execution_phase.iloc[-1])
        final_time=index[final_i]+(step if final_phase!='open' else pd.Timedelta(0))
        last_close_time=final_time
        q=cash*risk_fraction/risk
        if notional_cap is not None:q=min(q,cash*notional_cap/(entry*(1+half)))
        nominal=q*entry;entry_equity=cash
        cash-=nominal*half
        n=final_i-ei+1
        qty_change=np.zeros(n);realized_change=np.zeros(n)
        for k,frac,price in zip(positions,fractions,exits.price.to_numpy(float)):
            qty_change[k-ei]+=frac
            realized_change[k-ei]+=frac*(side*(price-entry)-entry*half)
        remaining=1.-np.cumsum(qty_change)
        if remaining.min() < -1e-8:raise ValueError('overfilled position')
        realized=q*np.cumsum(realized_change)
        marked=cash+realized+q*remaining*(side*(closes[ei:final_i+1]-entry)-entry*half)
        # A next-open terminal fill is not evidence for that bar's close NAV;
        # a reversal may enter at that same open and owns that close instead.
        stop=final_i+(final_phase!='open')
        endmark=marked[:stop-ei]
        nav[ei:stop]=endmark
        positive=endmark>0
        if positive.any():
            gross=q*remaining[:len(endmark)]*closes[ei:stop]
            max_leverage=max(max_leverage,float((gross[positive]/endmark[positive]).max(initial=0.)))
        ending=float(cash+realized[-1])
        gross_ret=float(np.sum(fractions*side*(exits.price.to_numpy(float)/entry-1)))
        net_ret=gross_ret-roundtrip_cost
        if 'net_return' in row and math.isfinite(float(row['net_return'])):
            err=abs(net_ret-float(row['net_return']));max_arithmetic_error=max(max_arithmetic_error,err)
            if err>1e-8:raise ValueError('fill net return does not reconcile to trade ledger')
        if exits.kind.eq('censor').any():boundary_marks+=1
        allocations.append(dict(trade_id=row['trade_id'],entry_time=t,entry_equity=entry_equity,quantity=q,
                                notional=nominal,target_risk=risk_fraction,effective_gross_stop_risk=q*risk/entry_equity,
                                entry_notional_ratio=nominal/entry_equity,ending_equity=ending,
                                realized_or_marked_net_pnl=ending-entry_equity,boundary_estimate=bool(exits.kind.eq('censor').any())))
        bad=np.flatnonzero(endmark<=0)
        if len(bad) or ending<=0:
            ruined=True; ruin_i=ei+int(bad[0]) if len(bad) else stop
            nav[min(ruin_i,len(nav)):]=0.;cash=0.;cursor=len(nav);break
        cash=ending;cursor=stop
    if not invalid and not ruined:nav[cursor:]=cash
    return pd.DataFrame({'time':index+step,'equity':nav}),pd.DataFrame(allocations),dict(
        ruined=ruined,invalid=invalid,boundary_marks=boundary_marks,
        max_observed_close_leverage=max_leverage,max_net_return_error=max_arithmetic_error)


def period_summary(path:pd.DataFrame, *, initial=10000.,
                   start='2024-09-10T00:00:00Z',split='2025-09-10T00:00:00Z',end='2026-09-10T00:00:00Z'):
    """Calendar close NAV returns; holdings and unrealized PnL cross the split."""
    out=[]
    for name,a,b in [('full',start,end),('development',start,split),('validation',split,end)]:
        a,b=_utc(a),_utc(b);before=path.loc[path.time.le(a),'equity']
        opening=float(before.iloc[-1]) if len(before) else float(initial)
        sub=path.loc[path.time.gt(a)&path.time.le(b),'equity'].to_numpy(float)
        vals=np.r_[opening,sub];valid=bool(np.isfinite(vals).all())
        if not valid or opening<=0:
            out.append(dict(period=name,opening_equity=opening,ending_equity=float(vals[-1]),net_return=math.nan,max_close_drawdown=math.nan,valid=False));continue
        peaks=np.maximum.accumulate(vals);dd=1.-np.divide(vals,peaks,out=np.zeros_like(vals),where=peaks>0)
        out.append(dict(period=name,opening_equity=opening,ending_equity=float(vals[-1]),net_return=float(vals[-1]/opening-1),max_close_drawdown=float(dd.max()),valid=True))
    return out


def marked_account_grid(bars,trades,fills,*, settings=((.01,1.),(.03,1.),(.05,1.),(.10,1.),(.01,None),(.03,None),(.05,None),(.10,None)),initial=10000.,minutes=60,roundtrip_cost=.002,start='2024-09-10T00:00:00Z',end='2026-09-10T00:00:00Z'):
    """Vectorize capital scenarios across an identical unit-position path.

    This is algebraically the same frozen-quantity cashbook as marked_account;
    no scenario is allowed to borrow another scenario's realized equity.
    """
    start,end=_utc(start),_utc(end);step=pd.Timedelta(minutes=minutes)
    b=bars.loc[(bars.index>=start)&(bars.index<end)]
    ix=b.index; closes=b.close.to_numpy(float);opens=b.open.to_numpy(float)
    width=len(settings);risks=np.array([s[0] for s in settings]);caps=np.array([np.inf if s[1] is None else s[1] for s in settings])
    if (risks<=0).any() or (risks>=1).any() or (caps<=0).any():raise ValueError('invalid capital grid')
    cash=np.full(width,initial);nav=np.full((len(b),width),initial);cursor=0
    ruined=np.zeros(width,bool);invalid=False;maxlev=np.zeros(width);effective_sum=np.zeros(width);allocations=np.zeros(width,int)
    half=roundtrip_cost/2;boundary=0;max_error=0.;last_close_time=None
    t=trades.sort_values(['entry_time','trade_id']).copy() if len(trades) else trades
    legs={}
    if len(fills):
        f=fills.loc[fills.kind.ne('entry')].copy()
        f['bar_i']=ix.get_indexer(pd.DatetimeIndex(pd.to_datetime(f.bar_open,utc=True)))
        f['phase_i']=f.execution_phase.map({'open':0,'intrabar':1,'close':2})
        if f.phase_i.isna().any():raise ValueError('unknown fill timing')
        f=f.sort_values(['bar_i','phase_i','leg_no'] if 'leg_no' in f else ['bar_i','phase_i'])
        for leg in f.itertuples(index=False):
            legs.setdefault(leg.trade_id,[]).append((int(leg.bar_i),int(leg.phase_i),float(leg.qty_fraction),float(leg.price),leg.kind))
    for row in t.itertuples(index=False):
        stamp=_utc(row.entry_time)
        if not start<=stamp<end:continue
        ei=ix.get_indexer([stamp])[0]
        if ei<0 or (last_close_time is not None and stamp<last_close_time):raise ValueError('missing entry or overlapping positions')
        entry,risk,side=float(row.entry_price),float(row.initial_risk),int(row.side)
        if side not in (-1,1) or entry<=0 or risk<=0 or not np.isclose(opens[ei],entry,rtol=1e-10,atol=0):raise ValueError('invalid entry')
        nav[cursor:ei,:]=cash
        ls=legs.get(row.trade_id)
        if not ls:raise ValueError('missing final valuation')
        arr=np.array([v[:4] for v in ls],float)
        if not np.isfinite(arr).all():invalid=True;nav[ei:,:]=np.nan;break
        positions=arr[:,0].astype(int);fractions=arr[:,2]
        if (positions<ei).any() or (fractions<=0).any() or not np.isclose(fractions.sum(),1.,atol=1e-9):raise ValueError('fill chronology or quantity error')
        final_i=int(positions[-1]);phase=int(arr[-1,1]);last_close_time=ix[final_i]+(step if phase else pd.Timedelta(0))
        stop=final_i+int(phase!=0);n=final_i-ei+1
        qr=np.zeros(n);pr=np.zeros(n)
        np.add.at(qr,positions-ei,fractions)
        np.add.at(pr,positions-ei,fractions*(side*(arr[:,3]/entry-1)-half))
        remaining=1-np.cumsum(qr);realized=np.cumsum(pr)
        net_ret=float(realized[-1]-half)
        if hasattr(row,'net_return') and np.isfinite(float(row.net_return)):
            error=abs(net_ret-float(row.net_return));max_error=max(max_error,error)
            if error>1e-8:raise ValueError('fill return does not reconcile')
        unit=-half+realized+remaining*(side*(closes[ei:final_i+1]/entry-1)-half)
        lev=np.minimum(risks/(risk/entry),caps/(1+half));lev[ruined]=0
        active=~ruined;allocations[active]+=1;effective_sum[active]+=lev[active]*risk/entry
        values=cash[None,:]*(1+unit[:stop-ei,None]*lev[None,:])
        nav[ei:stop,:]=values
        if len(values):
            exposure=cash[None,:]*lev[None,:]*remaining[:stop-ei,None]*(closes[ei:stop,None]/entry)
            observed=np.divide(exposure,values,out=np.zeros_like(values),where=values>0)
            maxlev=np.maximum(maxlev,observed.max(axis=0))
        cash=cash*(1+lev*net_ret)
        for j in np.flatnonzero(~ruined):
            bad=np.flatnonzero(values[:,j]<=0)
            if len(bad) or cash[j]<=0:
                ruined[j]=True;at=ei+int(bad[0]) if len(bad) else stop
                nav[at:,j]=0;cash[j]=0
        for j in np.flatnonzero(ruined):
            # Earlier ruin remains absorbing even if a hypothetical later trade recovers.
            zeros=np.flatnonzero(nav[:stop,j]<=0)
            if len(zeros):nav[int(zeros[0]):,j]=0
        boundary+=int(any(v[4]=='censor' for v in ls))
        cursor=stop
    if not invalid:nav[cursor:,:]=cash
    meta=[]
    for j,(risk,cap) in enumerate(settings):
        meta.append(dict(risk_fraction=risk,notional_cap='uncapped_stress' if cap is None else str(cap),
                         ruined=bool(ruined[j]),invalid=invalid,boundary_marks=boundary,
                         allocated_trades=int(allocations[j]),mean_effective_stop_risk=float(effective_sum[j]/allocations[j]) if allocations[j] else 0.,
                         max_observed_close_leverage=float(maxlev[j]),max_net_return_error=max_error))
    return ix+step,nav,meta
