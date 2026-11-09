"""Use individual assets' observed minute extremes to bound portfolio drawdown without assuming co-occurrence."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_ratio_risk_minutes as risk

EXP=risk.EXP.parent/'exp-cloud-close-ratio-extreme-witness-20261109-v1'


def witness_vectors(units,hour,base):
    """Each actual minute high/low occurs; all other last-trade prices lie in their own padded range."""
    lo=[];hi=[];prior_lo=[];prior_hi=[];closes=[]
    for symbol,qty in units.items():
        bars,_=risk.source(symbol,hour)
        if bars is None:
            raise ValueError('Cannot add a witness without its verified native minute tape')
        delta=qty*(bars[risk.minute.FIELDS].to_numpy()-bars.open.iat[0])
        bottom=np.minimum(delta[:,1],delta[:,2])
        top=np.maximum(delta[:,1],delta[:,2])
        # Between a minute boundary and a symbol's first trade the last prior close may still mark it.
        times,q=risk.pair.wallet.quotes(symbol)
        i=times.searchsorted(hour)
        if i<1 or times[i-1]!=hour-risk.pair.H:
            raise ValueError('Missing initial carried quote')
        previous=np.r_[qty*(q[i-1,3]-bars.open.iat[0]),delta[:-1,3]]
        lo.append(bottom);hi.append(top);prior_lo.append(np.minimum(previous,bottom))
        prior_hi.append(np.maximum(previous,top));closes.append(delta[:,3])
    bottoms,tops=np.array(prior_lo),np.array(prior_hi)
    worst=base+bottoms.sum(axis=0)
    best=base+tops.sum(axis=0)
    # At the instant each asset reaches its own observed favorable extreme, grant every other asset
    # its worst admissible price. At its adverse extreme, grant the others their best price.
    must_reach_high=worst+np.max(np.array(hi)-bottoms,axis=0)
    must_reach_low=best+np.min(np.array(lo)-tops,axis=0)
    actual_close=base+np.array(closes).sum(axis=0)
    return np.maximum(must_reach_high,actual_close),np.minimum(must_reach_low,actual_close),actual_close,worst,best


def audit(label,evidence):
    curve=pd.read_csv(risk.cross.EXP/f'hourly_{label}.csv.gz',index_col=0,parse_dates=True)
    clocks={pd.Timestamp(r['hour_end']):r for r in evidence['hours'] if r.get('complete')}
    peak=upper_peak=1000.;dd=upper_dd=0.;receipt={}
    for row in curve.itertuples():
        for value in (row.open_nav,row.open_exit_nav):
            peak=max(peak,value);dd=max(dd,1-value/peak)
            upper_peak=max(upper_peak,value);upper_dd=max(upper_dd,1-value/upper_peak)
        if row.Index in clocks:
            r=clocks[row.Index]
            high,low,close,outer_low,outer_high=witness_vectors(r['signed_units'],r['hour'],row.open_exit_nav)
            if np.any(high>outer_high+.00001) or np.any(low<outer_low-.00001):
                raise ValueError('A guaranteed extremum lies outside the admissible price interval')
            # A trough may precede the peak in this minute. Only earlier peaks can be used for it.
            for h,l,c,potential_low,potential_high in zip(high,low,close,outer_low,outer_high):
                upper_peak=max(upper_peak,potential_high);upper_dd=max(upper_dd,1-potential_low/upper_peak)
                dd=max(dd,1-l/peak)
                peak=max(peak,h)
                dd=max(dd,1-c/peak)
            receipt[row.Index.isoformat()]=dict(guaranteed_high_usdt=float(high.max()),guaranteed_low_usdt=float(low.min()))
        else:
            upper_peak=max(upper_peak,row.upper_nav)
            upper_dd=max(upper_dd,1-row.lower_nav/upper_peak)
        upper_peak=max(upper_peak,row.nav);upper_dd=max(upper_dd,1-row.nav/upper_peak)
        peak=max(peak,row.nav);dd=max(dd,1-row.nav/peak)
    return dict(certified_lower_dd_pct=float(100*dd),padded_upper_dd_pct=float(100*upper_dd),previous_minute_observed_pct=evidence['observed_dd_pct'],
                prior_minute_model_upper_dd_pct=evidence['refined_bound_pct'],hours=receipt)


if __name__=='__main__':
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed portfolio extrema witness')
    d=json.loads((risk.EXP/'summary.json').read_text())
    output={label:audit(label,receipt) for label,receipt in d.items()}
    (EXP/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
