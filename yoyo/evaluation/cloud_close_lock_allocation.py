"""Keep the fixed +0.5R profit lock; scale down the cash at risk per fresh 12h entry."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-lock-allocation-20261109-v1'


def simulate(data,allocation,slip=0.,drawdown_power=0.):
    pos,equity=wallet.account(data,exposure=allocation,slip=slip,drawdown_power=drawdown_power)
    periods={'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
             '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}
    return {p:wallet.summarize(pos,equity,*bounds) for p,bounds in periods.items()},pos,equity


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=pd.read_csv(lock.EXP/'account_metrics.csv')
    dev=parent[parent.period.eq('dev')]
    choices=dev[dev.win_pct.ge(30)&dev.payoff.ge(2.5)&dev.payoff_usdt.ge(2.5)]
    if choices.arm_at_r.tolist()!=[2.]:
        raise ValueError('Expected unique 2R qualifying activation in the development period')
    paths=pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False)
    paths=paths[paths.arm_at_r.eq(2.)]
    real,ctl=paths[~paths.is_control],paths[paths.is_control]
    base=parent[parent.arm_at_r.eq(2.)]
    rows=base.drop(columns='arm_at_r').assign(allocation=1.).to_dict('records')
    data=wallet.prepare(real,9)
    samples={}
    for allocation in cfg['allocation_multiples'][:-1]:
        metrics,tr,eq=simulate(data,allocation)
        rows.extend(dict(allocation=allocation,period=p,**v) for p,v in metrics.items())
        samples[allocation]=tr,eq
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=float(eligible.allocation.iloc[0]) if len(eligible) else None
    checks={}
    if pick is not None:
        if pick not in samples:
            raise ValueError('Unexpected change to 1x baseline development drawdown')
        tr,eq=samples[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        checks['slippage_5bp']=simulate(data,pick,.0005)[0]
        del data
        gc.collect()
        for lab,events in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)]),('control',ctl)]:
            checks[lab]=simulate(wallet.prepare(events,9),pick)[0]
            gc.collect()
    result=dict(pick=pick,eligible=eligible.allocation.tolist(),arm_at_r=2.,protect_at_r=.5,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
