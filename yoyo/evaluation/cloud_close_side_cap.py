"""Limit simultaneous same-direction cloud exposure at admission only."""
import gc
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_lock_allocation as allocation
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-side-cap-20261109-v1'


def admit(pool,cap):
    ordered=pool.copy()
    ordered['priority']=ordered.symbol.map(lambda s:zlib.crc32(s.encode()))
    ordered=ordered.sort_values(['entry_ms','entry_order','priority','symbol','event_id'])
    ends=np.full(9,-1,dtype='int64')
    coins=np.full(9,'',dtype=object)
    sides=np.zeros(9)
    assigned=[]
    skips=dict(same_contract=0,all_slots_busy=0,direction_full=0)
    for row in ordered.itertuples():
        busy=ends>=row.entry_ms
        if np.any(busy&(coins==row.symbol)):
            skips['same_contract']+=1
            continue
        free=np.flatnonzero(~busy)
        if not len(free):
            skips['all_slots_busy']+=1
            continue
        if np.count_nonzero(busy&(sides==row.side))>=cap:
            skips['direction_full']+=1
            continue
        slot=int(free[0])
        ends[slot],coins[slot],sides[slot]=row.exit_bar_ms,row.symbol,row.side
        assigned.append((row.Index,slot))
    ids=pd.DataFrame(assigned,columns=['row','slot']).set_index('row')
    return ordered.loc[ids.index].assign(slot=ids.slot),skips


def run(pool,cap,slip=0.):
    picks,skips=admit(pool,cap)
    data=wallet.prepare(None,9,assigned=(picks,skips))
    return allocation.simulate(data,1.,slip),skips


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    frozen=json.loads((allocation.EXP/'summary.json').read_text())
    if frozen['arm_at_r']!=2. or frozen['protect_at_r']!=.5:
        raise ValueError('Unexpected frozen lock')
    paths=pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False)
    paths=paths[paths.arm_at_r.eq(2.)]
    real,ctl=paths[~paths.is_control],paths[paths.is_control]
    base=pd.read_csv(allocation.EXP/'account_metrics.csv')
    rows=base[base.allocation.eq(1.)].drop(columns='allocation').assign(side_cap=9).to_dict('records')
    parent,_=wallet.admit(real,9)
    no_cap,_=admit(real,9)
    if not np.array_equal(parent[['event_id','slot']],no_cap[['event_id','slot']]):
        raise ValueError('Uncapped roster changed')
    samples,audits={},{}
    for cap in cfg['caps'][:-1]:
        (metrics,tr,eq),audits[cap]=run(real,cap)
        rows.extend(dict(side_cap=cap,period=p,**v) for p,v in metrics.items())
        samples[cap]=tr,eq
        gc.collect()
    table=pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev=table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=int(eligible.side_cap.iloc[0]) if len(eligible) else None
    checks={}
    if pick is not None:
        tr,eq=samples[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            checks[name]=run(events,pick,slip)[0][0]
            gc.collect()
    summary=dict(pick=pick,eligible=eligible.side_cap.tolist(),audit=audits,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
