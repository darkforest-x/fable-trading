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
    want={'minutes','known','wait','wait_min','decision','initial','new_coins','first_half_new','last_half_new',
          'share28_pct','since_prev_25_lower_shock_min','last_touch_age_min','monitor_fraction'}
    df=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p,usecols=lambda k:k in want)
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
    want={'minutes','known','wait_min','hold_hours','alt_member_median_observed_return_pct','btc_observed_return_pct',
          'reclaim_fraction','price_n','members_expected_n','endpoint_n','member_n','eligible_n','valid_n',
          *(n+'_net_return_pct' for n in ('btc','eth','sol','basket'))}
    pf=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p,usecols=lambda k:k in want)
    pf['known']=pd.to_datetime(pf.known,utc=True)
    keys=['minutes','known','wait_min']
    assert not feats.duplicated(keys).any() and not pf.duplicated(keys+['hold_hours']).any()
    if not len(feats):raise ValueError('Empty feature partition')
    base=pf[pf.hold_hours==HOLDS[0]].set_index('known').reindex(feats.known)
    fr=feats.copy()
    fr['alt_move']=col(base,'alt_member_median_observed_return_pct').to_numpy()
    fr['btc_move']=col(base,'btc_observed_return_pct').to_numpy()
    fr['reclaim_fraction']=col(base,'reclaim_fraction').to_numpy()
    complete=base.price_n.eq(base.members_expected_n)&base.members_expected_n.gt(0)
    fr.loc[~complete.to_numpy(),['alt_move','reclaim_fraction']]=np.nan
    fr['market_share_pct']=col(fr,'share28_pct','market_share_pct').where(
        fr.known>=features().known.min()+pd.Timedelta(days=28))
    fr['prior_shock_age_min']=col(fr,'since_prev_25_lower_shock_min','since_shock_min','prior_shock_age_min','since_previous_shock_min')
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
        member_n=col(part,'members_expected_n','member_n','eligible_n').to_numpy()
        valid_n=col(part,'endpoint_n','valid_n').to_numpy()
        ret[valid_n!=member_n,j,3]=np.nan
    return fr,ret


cached_partition=lru_cache(maxsize=10)(partition)
