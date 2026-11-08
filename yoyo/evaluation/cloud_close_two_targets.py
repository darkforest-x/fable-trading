"""Pay out part at 1R and keep a second resting 10R limit plus the original cloud runner."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_partial_r as distance
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-two-targets-20261109-v1'


def prepare(pool):
    live,skips=wallet.admit(pool[pool.tp_r.eq(0)],9)
    chunks=[]
    for col,target in enumerate([1,10,0]):
        child=pool[pool.tp_r.eq(target)].set_index('event_id',drop=False).loc[live.event_id].copy()
        if np.any(child.exit_bar_ms.to_numpy()>live.exit_bar_ms.to_numpy()):
            raise ValueError('Resting target outlasts its original protective exit')
        child['slot']=3*live.slot.to_numpy()+col
        chunks.append(child)
    return wallet.prepare(None,27,assigned=(pd.concat(chunks,ignore_index=True),skips))


def run(data,early,slip=0.):
    weights=np.array([early*.75,(1-early)*.75,.25])
    legs,eq=wallet.account(data,weights=weights,slip=slip)
    positions=(legs.groupby('event_id',sort=False)
               .agg(parent_id=('parent_id','first'),symbol=('symbol','first'),side=('side','first'),
                    entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','max'),cash_pnl=('cash_pnl','sum'),
                    notional=('notional','sum')))
    amounts=legs.pivot(index='event_id',columns='tp_r',values='notional').loc[positions.index,[1,10,0]]
    if not np.allclose(amounts.div(positions.notional,axis=0),weights,rtol=1.e-9,atol=1.e-9):
        raise ValueError('Funded weights differ from preregistered target weights')
    positions['net_return']=positions.cash_pnl/positions.notional
    periods={'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
             '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}
    return {p:wallet.summarize(positions,eq,*bounds) for p,bounds in periods.items()},positions,eq


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    setup=json.loads((distance.EXP/'summary.json').read_text())
    if (setup['pick'],setup['best_dev_growth'])!=(2,10):
        raise ValueError('Preregistered comparison with 2R and 10R changed')
    paths=wallet.read_paths()
    paths=paths[paths.tp_r.isin([0,1,10])].copy()
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(leader.leadership().dropna().index)].copy()
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    src.index=pd.RangeIndex(1,len(src)+1)
    paths['entry_order']=paths.source_row_number.map(src.prior_atr_pct).fillna(np.inf)
    real=paths[~paths.is_control]
    ctl=paths[paths.is_control&paths.parent_id.isin(real.parent_id)]
    parent=pd.read_csv(distance.EXP/'account_metrics.csv')
    base=parent[parent.target_r.isin([1,2,10])].copy()
    base['early_within_partial']=base.target_r.map({1:1.,2:-1.,10:0.})
    rows=base.drop(columns='target_r').to_dict('records')
    data=prepare(real)
    curves={}
    for early in cfg['early_fraction_of_partial'][1:-1]:
        metrics,tr,eq=run(data,early)
        rows.extend(dict(early_within_partial=early,period=p,**v) for p,v in metrics.items())
        curves[early]=tr,eq
    table=pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev=table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick=float(eligible.early_within_partial.iloc[0]) if len(eligible) else -1.
    best=float(dev.early_within_partial.iloc[0])
    checks={}
    new=sorted({pick,best}&curves.keys())
    for e in new:
        tr,eq=curves[e]
        tr.to_csv(EXP/f'funded_{e}.csv.gz')
        eq.to_csv(EXP/f'hourly_{e}.csv.gz',index_label='hour_end_utc')
        checks[e]={'slippage_5bp':run(data,e,.0005)[0]}
    del data
    gc.collect()
    if new:
        for lab,events in [('control',ctl),('paired_signals',real[real.parent_id.isin(ctl.parent_id)])]:
            d=prepare(events)
            for e in new:
                checks[e][lab]=run(d,e)[0]
            del d
            gc.collect()
    summary=dict(pick=pick,best_dev_growth=best,eligible=eligible.early_within_partial.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
