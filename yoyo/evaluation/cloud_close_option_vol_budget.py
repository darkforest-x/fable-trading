"""Use the gap between observed BTC variability and official option-implied vol to reduce new 2x entries."""
import gc

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

EXP=cash.ROOT/'exp-cloud-close-option-vol-budget-20261109-v1'
IV=cash.ROOT/'exp-monday-1618-dvol-20261023-v1'/'inputs'/'dvol_BTC.csv'


def features(parents,quotes):
    """At clock t compare exactly 720 consecutive completed BTC hourly log returns with Deribit's t-1H DVOL bar."""
    h=cash.H
    stamps,px=quotes('BTC')
    close=pd.Series(px[:,3],index=stamps+h)
    valid=close.index.to_series().diff(720).eq(720*h)
    rv=np.log(close).diff().rolling(720).std().mul(100*np.sqrt(365*24)).where(valid)
    raw=pd.read_csv(IV)
    iv=pd.Series(raw.close.to_numpy(),index=pd.to_datetime(raw.ts,utc=True).astype('int64')//10**6+h)
    # pandas may preserve microsecond resolution for parsed strings in newer releases.
    iv.index=pd.DatetimeIndex(pd.to_datetime(raw.ts,utc=True)).as_unit('ms').asi8+h
    if not iv.index.is_unique or not iv.index.is_monotonic_increasing or not iv.gt(0).all():
        raise ValueError('Unusable native implied volatility hour')
    panel=pd.concat([rv.rename('rv'),iv.rename('iv')],axis=1)
    out=parents[['entry_ms','side','symbol']].copy()
    for name,offset in [('current',0),('stale',30*24*h)]:
        for key in ['rv','iv']:
            out[name+'_'+key]=(out.entry_ms-offset).map(panel[key])
    return out


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed option-implied risk test')
    data,market,quotes,parents,caps,ratios=cash.account()
    panel=features(parents,quotes)
    available=panel.filter(regex='_(iv|rv)$').notna().all(axis=1)
    panel['original_leverage']=ratios
    panel['common_coverage']=available
    panel['positive_first_capacity']=caps.gt(0)
    panel.to_csv(EXP/'decisions.csv.gz')
    counts={}
    cases={}
    for name,cut in [('realized_above_iv',panel.current_rv>panel.current_iv),
                     ('realized_below_iv',panel.current_rv<panel.current_iv),
                     ('stale_above_iv',panel.stale_rv>panel.stale_iv)]:
        reduced=available & cut & ratios.eq(2)
        leverage=ratios.mask(reduced,1.)
        counts[name]=int((reduced & caps.gt(0)).sum())
        for slip in [0.,.0005]:
            label=name+('_stress' if slip else '')
            case=cash.queue.engine.simulate(data,market,quotes,leverage,slip,EXP,label,entry_limits=caps)
            cases[label]=case
            print(label,case.get('metrics',{}).get('all',case.get('unresolved')),flush=True)
            gc.collect()
    observed=panel.loc[available&caps.gt(0),'entry_ms']
    cash.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=cases,reduced_funded=counts,
        positive_original_parents=int(caps.gt(0).sum()),fully_observable=len(observed),
        first_common_entry_ms=int(observed.min()),last_common_entry_ms=int(observed.max())))


if __name__=='__main__':
    main()
