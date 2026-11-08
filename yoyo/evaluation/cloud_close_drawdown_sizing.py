"""Determine only new-entry size from the live marked account drawdown."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_lock_allocation as allocation
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-drawdown-sizing-20261109-v1'


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=json.loads((allocation.EXP/'summary.json').read_text())
    if parent['arm_at_r']!=2. or parent['protect_at_r']!=.5:
        raise ValueError('Initial profit protection changed')
    ledger=pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False)
    ledger=ledger[ledger.arm_at_r.eq(2.)]
    real,ctl=ledger[~ledger.is_control],ledger[ledger.is_control]
    data=wallet.prepare(real,9)
    base=pd.read_csv(allocation.EXP/'account_metrics.csv')
    rows=base[base.allocation.eq(1.)].drop(columns='allocation').assign(drawdown_power=0).to_dict('records')
    samples={}
    for power in cfg['powers'][1:]:
        metrics,tr,eq=allocation.simulate(data,1.,drawdown_power=power)
        rows.extend(dict(drawdown_power=power,period=p,**v) for p,v in metrics.items())
        samples[power]=tr,eq
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=float(eligible.drawdown_power.iloc[0]) if len(eligible) else None
    checks={}
    if pick is not None:
        tr,eq=samples[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        checks['slippage_5bp']=allocation.simulate(data,1.,.0005,drawdown_power=pick)[0]
        del data
        gc.collect()
        for name,events in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)]),('control',ctl)]:
            checks[name]=allocation.simulate(wallet.prepare(events,9),1.,drawdown_power=pick)[0]
            gc.collect()
    summary=dict(pick=pick,eligible=eligible.drawdown_power.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
