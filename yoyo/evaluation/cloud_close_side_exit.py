"""Use a 2R partial on only one direction and let the other side run in full."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-side-exit-20261109-v1'


def account(pool,partial_side,slip=0.):
    original=pool[pool.tp_r.eq(0)]
    final,skips=wallet.admit(original,9)
    limits=pool[pool.tp_r.eq(2)].set_index('event_id',drop=False).loc[final.event_id].copy()
    enable=limits.side.eq(partial_side).to_numpy()
    if np.any(limits.exit_bar_ms.to_numpy()>final.exit_bar_ms.to_numpy()):
        raise ValueError('2R limit outlasts original runner exit')
    primary=pd.concat([limits.iloc[np.flatnonzero(enable)],final.iloc[np.flatnonzero(~enable)]],ignore_index=True)
    slots=final.set_index('event_id').slot
    primary['slot']=primary.event_id.map(slots)*2
    final['slot']=final.slot*2+1
    data=wallet.prepare(None,18,assigned=(pd.concat([primary,final],ignore_index=True),skips))
    legs,eq=wallet.account(data,slip=slip,weights=(.75,.25))
    pos=legs.groupby('event_id',sort=False).agg(parent_id=('parent_id','first'),symbol=('symbol','first'),
        side=('side','first'),entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','max'),
        cash_pnl=('cash_pnl','sum'),notional=('notional','sum'))
    pos['net_return']=pos.cash_pnl/pos.notional
    first=legs[legs.slot.mod(2).eq(0)].set_index('event_id')
    if not np.allclose(first.notional/pos.notional,.75) or not np.array_equal(first.tp_r.eq(2),first.side.eq(partial_side)):
        raise ValueError('Direction or dollar allocation mismatch')
    periods={'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
             '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}
    return {p:wallet.summarize(pos,eq,*bounds) for p,bounds in periods.items()},pos,eq


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    paths=wallet.read_paths()
    paths=paths[paths.tp_r.isin([0,2])]
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(leader.leadership().dropna().index)].copy()
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    src.index=pd.RangeIndex(1,len(src)+1)
    paths['entry_order']=paths.source_row_number.map(src.prior_atr_pct).fillna(np.inf)
    real=paths[~paths.is_control]
    ctl=paths[paths.is_control&paths.parent_id.isin(real.parent_id)]
    both=pd.read_csv(leader.EXP/'account_metrics.csv')
    neither=pd.read_csv(lock.EXP/'account_metrics.csv')
    rows=both[both.top_fraction.eq(1.)].drop(columns='top_fraction').assign(policy='both').to_dict('records')
    rows+=neither[neither.arm_at_r.eq(0)].drop(columns='arm_at_r').assign(policy='neither').to_dict('records')
    samples={}
    sides={'longs_only':1,'shorts_only':-1}
    for name in cfg['policies'][1:3]:
        metrics,tr,eq=account(real,sides[name])
        rows.extend(dict(policy=name,period=p,**v) for p,v in metrics.items())
        samples[name]=tr,eq
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=eligible.policy.iloc[0] if len(eligible) else None
    best=dev.policy.iloc[0]
    checks={}
    for policy in sorted({pick,best}&sides.keys()):
        tr,eq=samples[policy]
        tr.to_csv(EXP/f'funded_{policy}.csv.gz')
        eq.to_csv(EXP/f'hourly_{policy}.csv.gz',index_label='hour_end_utc')
        checks[policy]={}
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            checks[policy][name]=account(events,sides[policy],slip)[0]
            gc.collect()
    summary=dict(pick=pick,best_dev_growth=best,eligible=eligible.policy.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
