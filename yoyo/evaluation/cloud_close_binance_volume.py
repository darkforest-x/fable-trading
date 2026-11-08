"""Test native confirmed key volume, relative to the contract's previous twenty 12H bars."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-binance-volume-20261109-v1'


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    files=sorted((venue.ARCHIVE/'results/symbols').glob('*/signals.csv.gz'))
    values=[]
    for file in files:
        rows=pd.read_csv(file,usecols=['signal_id','tf','log_vol_ratio'])
        values.append(rows[rows.tf.eq('12H')].set_index('signal_id').log_vol_ratio)
    ratio=np.exp(pd.concat(values))
    if not ratio.index.is_unique or len(files)!=632:
        raise ValueError('Changed frozen Binance signal feature identities')
    all_paths=pd.read_csv(venue.EXP/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False)
    paths=all_paths[all_paths.eligible].copy()
    parents=paths[~paths.is_control&paths.tp_r.eq(0)].copy()
    parents['volume_ratio']=parents.parent_id.map(ratio)
    paths=paths[paths.parent_id.isin(parents.parent_id)].copy()
    paths['volume_ratio']=paths.parent_id.map(parents.set_index('parent_id').volume_ratio)
    parent_metrics=pd.read_csv(venue.EXP/'account_metrics.csv')
    rows=parent_metrics[parent_metrics.policy.eq('partial75')].assign(policy='all').to_dict('records')
    curves,counts={},{}
    def arm(name):
        lo,hi=cfg['bins'][name]
        available=paths.volume_ratio.ge(lo)&np.isfinite(paths.volume_ratio)
        if hi is not None:
            available &= paths.volume_ratio.lt(hi)
        part=paths[available]
        return part[~part.is_control],part[part.is_control]
    for name in cfg['bins']:
        real,ctl=arm(name)
        counts[name]=len(real)//2
        (metrics,tr,eq),unquoted=venue.account(real,True)
        rows.extend(dict(policy=name,period=p,**v) for p,v in metrics.items())
        curves[name]=tr,eq
        gc.collect()
    missing=int((~np.isfinite(parents.volume_ratio)).sum())
    if sum(counts.values())+missing!=len(parents):
        raise ValueError('Causal ratios do not partition the fixed signal cohort')
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=eligible.policy.iloc[0] if len(eligible) else None
    checks={}
    if pick is not None and pick!='all':
        tr,eq=curves[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz')
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        real,ctl=arm(pick)
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            (checks[name],_,_),_=venue.account(events,True,slip)
            gc.collect()
    summary=dict(pick=pick,eligible=eligible.policy.tolist(),checks=checks,counts=counts,missing=missing)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
