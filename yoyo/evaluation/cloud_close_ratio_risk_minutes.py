"""Tighten three-pair drawdown bounds using one simultaneous BTC price and matched native minute bars."""
from functools import lru_cache
import json
import time

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_double_minute_risk as minute
from yoyo.evaluation import cloud_close_ratio_cross_margin as cross

pair=cross.pair
EXP=cross.EXP.parent/'exp-cloud-close-ratio-risk-minutes-20261109-v1'
STAGES=['open_nav','open_exit_nav','nav']


@lru_cache(maxsize=None)
def source(symbol,hour):
    for attempt in range(3):
        try:
            return minute.minute_quote(symbol,hour,pair.wallet.quotes)
        except requests.RequestException:
            if attempt==2:
                return None,'network_unavailable_after_three_attempts'
            time.sleep(2**attempt)


def bound(curve):
    ratio=np.r_[1000.,curve.lower_nav]/np.maximum.accumulate(np.r_[1000.,curve.upper_nav])
    return float((1-ratio.min())*100)


def audit(label):
    legs=pd.read_csv(cross.EXP/f'legs_{label}.csv.gz')
    curve=pd.read_csv(cross.EXP/f'hourly_{label}.csv.gz',index_col=0,parse_dates=True)
    hours=minute.targets(curve,50.)
    original=bound(curve)
    register=[]
    for end in hours:
        hour=int(end.value//1_000_000)-pair.H
        active=legs[legs.entry_ms.le(hour)&(legs.exit_bar_ms.gt(hour)|
                      (legs.exit_bar_ms.eq(hour)&legs.exit_phase.eq('close')))].copy()
        active['qty']=active.side*active.quantity
        stage=curve.loc[end,STAGES]
        base=stage.open_exit_nav
        lo=hi=net_lo=net_hi=base
        for sym,group in active.groupby('symbol'):
            ts,q=pair.wallet.quotes(sym)
            i=ts.searchsorted(hour)
            if i==len(ts) or ts[i]!=hour:
                raise ValueError(f'Unquoted risk hour in {sym}')
            op,high,low=q[i,:3]
            qty=group.qty.to_numpy()
            lo+=float(np.minimum(qty*(high-op),qty*(low-op)).sum())
            hi+=float(np.maximum(qty*(high-op),qty*(low-op)).sum())
            net_lo+=min(qty.sum()*(high-op),qty.sum()*(low-op))
            net_hi+=max(qty.sum()*(high-op),qty.sum()*(low-op))
        expected=[min(lo,stage.min()),max(hi,stage.max())]
        if not np.allclose(expected,curve.loc[end,['lower_nav','upper_nav']],atol=.002,rtol=1e-8):
            raise ValueError('Original real-quantity hourly bounds failed reconciliation')
        curve.loc[end,['lower_nav','upper_nav']]=[min(net_lo,stage.min()),max(net_hi,stage.max())]
        register.append(dict(hour_end=end.isoformat(),hour=hour,active_legs=len(active),
                             signed_units=active.groupby('symbol').qty.sum().to_dict()))
    synchronized=bound(curve)
    need=set(minute.targets(curve,50.))
    minute_obs={}
    for rec in register:
        end=pd.Timestamp(rec['hour_end'])
        if end not in need:
            continue
        stage=curve.loc[end,STAGES]
        base=stage.open_exit_nav
        lo=np.full(60,base);hi=lo.copy();opens=lo.copy();closes=lo.copy()
        rec['sources']={}
        complete=True
        for symbol,qty in rec['signed_units'].items():
            bars,url=source(symbol,rec['hour'])
            rec['sources'][symbol]=url
            if bars is None:
                complete=False
                continue
            quotes=qty*(bars[minute.FIELDS].to_numpy()-bars.open.iat[0])
            lo+=np.minimum(quotes[:,1],quotes[:,2]);hi+=np.maximum(quotes[:,1],quotes[:,2])
            opens+=quotes[:,0];closes+=quotes[:,3]
        rec['complete']=complete
        if complete:
            low=min(stage.min(),lo.min());high=max(stage.max(),hi.max())
            if low<curve.at[end,'lower_nav']-.002 or high>curve.at[end,'upper_nav']+.002:
                raise ValueError('Synchronized minutes escape the matched native hourly range')
            curve.loc[end,['lower_nav','upper_nav']]=[low,high]
            minute_obs[end]=np.r_[stage.iloc[:2],np.column_stack([opens,closes]).ravel(),stage.nav]
    peak=1000.;observed=0.
    for end,row in curve[STAGES].iterrows():
        points=minute_obs[end] if end in minute_obs else row.to_numpy()
        peaks=np.maximum.accumulate(np.r_[peak,points])[1:]
        observed=max(observed,float((1.-points/peaks).max()))
        peak=max(peak,float(points.max()))
    return dict(original_bound_pct=original,net_same_symbol_bound_pct=synchronized,
                original_threshold_hours=len(hours),minute_threshold_hours=len(need),matched_minute_hours=len(minute_obs),
                observed_dd_pct=100*observed,refined_bound_pct=bound(curve),hours=register)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed three-pair drawdown refinement')
    cases={name:audit(name) for name in ['pair','pair_stress']}
    (EXP/'summary.json').write_text(json.dumps(cases,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
