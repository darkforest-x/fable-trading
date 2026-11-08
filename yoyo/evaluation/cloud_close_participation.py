"""Classify each V1.7 key by the number of eligible contracts confirming on its side."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_lock_allocation as allocation
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-participation-20261109-v1'


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=pd.read_csv(allocation.EXP/'account_metrics.csv')
    rows=parent[parent.allocation.eq(1.)].drop(columns='allocation').assign(policy='all').to_dict('records')
    paths=pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False)
    paths=paths[paths.arm_at_r.eq(2.)].copy()
    parents=paths[~paths.is_control].copy()
    if parents.duplicated(['entry_ms','symbol']).any():
        raise ValueError('More than one original key on a contract at the same time')
    rankable=leader.leadership().dropna()
    n_coins=rankable.groupby(level='entry_ms').size()
    parents['cohort']=parents.entry_ms.map(n_coins)
    parents['same_side']=parents.groupby(['entry_ms','side']).symbol.transform('size')
    parents['fraction']=parents.same_side/parents.cohort
    if parents.cohort.min()<10 or not parents.fraction.between(0,1).all():
        raise ValueError('Invalid market participation denominator')
    parents[['parent_id','symbol','entry_ms','side','cohort','same_side','fraction']].to_csv(EXP/'features.csv.gz',index=False)
    paths['fraction']=paths.parent_id.map(parents.set_index('parent_id').fraction)
    def arm(policy):
        low,high=cfg['bins'][policy]
        part=paths[paths.fraction.ge(low)&paths.fraction.lt(high)]
        return part[~part.is_control],part[part.is_control]
    curves,counts={},{}
    for policy in cfg['bins']:
        real,ctl=arm(policy)
        metrics,meta,tr,eq=wallet.evaluate(real,9)
        curves[policy]=tr,eq
        counts[policy]=len(real)
        rows.extend(dict(policy=policy,period=p,**v) for p,v in metrics.items())
        gc.collect()
    if sum(counts.values())!=len(parents):
        raise ValueError('Prescribed bins do not partition the signal cohort')
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=eligible.policy.iloc[0] if len(eligible) else None
    checks={}
    if pick is not None:
        tr,eq=curves[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        real,ctl=arm(pick)
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            checks[name]=wallet.evaluate(events,9,slip=slip)[0]
            gc.collect()
    summary=dict(pick=pick,eligible=eligible.policy.tolist(),counts=counts,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
