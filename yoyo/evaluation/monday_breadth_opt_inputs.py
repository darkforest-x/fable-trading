"""Read bounded per-duration slices of the frozen breadth feature and outcome tables."""
from functools import lru_cache
from pathlib import Path
import numpy as np
import pandas as pd

OUT=Path(__file__).resolve().parents[2]/'experiments/active/exp-monday-breadth-optimization-20261108-v1'
HOLDS=(6,12,24,48,72,144)
ASSETS=('BTC','ETH','SOL','ALT')


@lru_cache(maxsize=1)
def features():
    files=[OUT/'features'/name for name in ('signals.parquet','signals.csv.gz')]
    p=next(p for p in files if p.is_file())
    df=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p)
    df=df.rename(columns={'wait':'wait_min'}) if 'wait_min' not in df else df
    df['known']=pd.to_datetime(df.known,utc=True)
    df['decision']=pd.to_datetime(df.decision,utc=True)
    return df


def col(frame,*names):
    for n in names:
        if n in frame:return frame[n]
    raise ValueError(f'Missing canonical field {names}; available: {list(frame)}')


def partition(w,x):
    feats=features()
    feats=feats[(feats.minutes==w)&(feats.wait_min==x)].sort_values('known').reset_index(drop=True)
    files=[OUT/'payoffs'/f'event_outcomes_minutes{w}_wait{x}.{ext}' for ext in ('parquet','csv.gz')]
    p=next(p for p in files if p.is_file())
    pf=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p)
    pf['known']=pd.to_datetime(pf.known,utc=True)
    keys=['minutes','known','wait_min']
    assert not feats.duplicated(keys).any() and not pf.duplicated(keys+['hold_hours']).any()
    if not len(feats):raise ValueError('Empty feature partition')
    base=pf[pf.hold_hours==HOLDS[0]].set_index('known').reindex(feats.known)
    fr=feats.copy()
    fr['alt_move']=col(base,'alt_member_median_observed_return_pct').to_numpy()
    fr['btc_move']=col(base,'btc_observed_return_pct').to_numpy()
    fr['reclaim_fraction']=col(base,'reclaim_fraction').to_numpy()
    fr['market_share_pct']=col(fr,'share28_pct','market_share_pct')
    fr['prior_shock_age_min']=col(fr,'since_shock_min','prior_shock_age_min','since_previous_shock_min')
    fr['monitor_fraction']=col(fr,'monitor_fraction')
    fr['first_half_new']=col(fr,'first_half_new')
    fr['last_half_new']=col(fr,'last_half_new')
    ret=np.full((len(fr),len(HOLDS),len(ASSETS)),np.nan)
    for j,h in enumerate(HOLDS):
        part=pf[pf.hold_hours==h].set_index('known').reindex(fr.known)
        if len(pf[pf.hold_hours==h])!=len(fr):raise ValueError(f'Missing outcome clocks W={w}, X={x}, H={h}')
        for k,asset in enumerate(ASSETS):
            name='basket' if asset=='ALT' else asset.lower()
            ret[:,j,k]=col(part,name+'_net_return_pct',name+'_net_pct').to_numpy()
        member_n=col(part,'member_n','eligible_n').to_numpy()
        valid_n=col(part,'endpoint_n','valid_n').to_numpy()
        ret[valid_n!=member_n,j,3]=np.nan
    return fr,ret


cached_partition=lru_cache(maxsize=8)(partition)
