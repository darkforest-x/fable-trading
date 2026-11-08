"""Measure only the twelve closed hourly candles that form the V1.7 signal key."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_slots as capacity


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-wick-timing-20261109-v1'


def elapsed(symbol, timestamp, side, extension):
    """Completed H1 lows (long) or highs (short) from [confirmation-12h,confirmation)."""
    clock,prices=wallet.quotes(symbol)
    start,end=clock.searchsorted([timestamp-12*wallet.HOUR,timestamp])
    if end-start!=12 or not np.array_equal(clock[start:end],timestamp+np.arange(-12,0)*wallet.HOUR):
        return -2
    wick=prices[start:end,2 if side>0 else 1]
    crosses=np.flatnonzero(side*(wick-extension)<0)
    return int(11-crosses[-1]) if len(crosses) else -1


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=json.loads((leader.EXP/'summary.json').read_text())
    if parent['pick']!=1:
        raise ValueError('Parent eligibility changed')
    paths=wallet.read_paths()
    paths=paths[paths.tp_r.isin([0,2])]
    rankable=leader.leadership().dropna().index
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(rankable)].copy()
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct','reference_entry'],float_precision='round_trip')
    src.index=pd.RangeIndex(1,len(src)+1)
    paths['entry_order']=paths.source_row_number.map(src.prior_atr_pct).fillna(np.inf)
    parents=paths[~paths.is_control & paths.tp_r.eq(0)].copy()
    parents['extension']=parents.source_row_number.map(src.reference_entry)
    parents['elapsed_hours']=[elapsed(r.symbol,r.entry_ms,r.side,r.extension) for r in parents.itertuples()]
    values=parents.set_index('parent_id').elapsed_hours
    paths['elapsed_hours']=paths.parent_id.map(values)
    if len(parents)!=parent['coverage']['1.0']['eligible_real']:
        raise ValueError('Parent eligibility mismatched')
    cols=['parent_id','symbol','side','entry_ms','extension','elapsed_hours']
    parents[cols].to_csv(EXP/'confirmation_features.csv.gz',index=False)
    base=pd.read_csv(leader.EXP/'account_metrics.csv')
    rows=base[base.top_fraction.eq(1)].drop(columns='top_fraction').assign(policy='parent').to_dict('records')
    samples={}
    def pool(name):
        lo,hi=cfg['policies'][name]
        a=paths[paths.elapsed_hours.between(lo,hi)]
        return a[~a.is_control],a[a.is_control]
    for name in cfg['policies']:
        real,control=pool(name)
        m,tr,eq=capacity.run(real,9,.75)
        rows.extend(dict(policy=name,period=p,**v) for p,v in m.items())
        samples[name]=tr,eq
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick=eligible.policy.iloc[0] if len(eligible) else 'parent'
    best=dev.policy.iloc[0]
    checks={}
    for policy in sorted({pick,best}-{'parent'}):
        tr,eq=samples[policy]
        tr.to_csv(EXP/f'funded_{policy}.csv.gz')
        eq.to_csv(EXP/f'hourly_{policy}.csv.gz',index_label='hour_end_utc')
        real,ctl=pool(policy)
        checks[policy]={}
        for label,events,slip in [('control',ctl,0.),('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                  ('slippage_5bp',real,.0005)]:
            checks[policy][label]=capacity.run(events,9,.75,slip)[0]
            gc.collect()
    result=dict(pick=pick,best_dev_growth=best,eligible=eligible.policy.tolist(),checks=checks,
                hours=parents.elapsed_hours.value_counts().sort_index().to_dict())
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
