"""Change only the realized 75% target distance, retaining the same funded runner."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-partial-r-20261109-v1'


def run(pool,target,slip=0.):
    legs=split.pair_data(pool[pool.tp_r.eq(0)],pool[pool.tp_r.eq(target)],9)
    return split.simulate(legs,.75,slip,target_r=target)


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=json.loads((leader.EXP/'summary.json').read_text())
    if parent['pick']!=1.:
        raise ValueError('Unexpected development parent')
    paths=wallet.read_paths()
    eligible_at=leader.leadership().dropna().index
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(eligible_at)].copy()
    source=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    source.index=pd.RangeIndex(1,len(source)+1)
    paths['entry_order']=paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    real=paths[~paths.is_control]
    ctl=paths[paths.is_control&paths.parent_id.isin(real.parent_id)]
    base=pd.read_csv(leader.EXP/'account_metrics.csv')
    rows=base[base.top_fraction.eq(1)].drop(columns='top_fraction').assign(target_r=2).to_dict('records')
    curves={}
    for target in cfg['grid']:
        if target==2:
            continue
        metrics,trades,equity=run(real,target)
        curves[target]=trades,equity
        rows.extend(dict(target_r=target,period=p,**v) for p,v in metrics.items())
        gc.collect()
    table=pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev=table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick=int(eligible.target_r.iloc[0]) if len(eligible) else 2
    best=int(dev.target_r.iloc[0])
    checks={}
    for target in sorted({pick,best}-{2}):
        tr,eq=curves[target]
        tr.to_csv(EXP/f'funded_{target}.csv.gz')
        eq.to_csv(EXP/f'hourly_{target}.csv.gz',index_label='hour_end_utc')
        checks[target]={}
        for name,events,slip in [('control',ctl,0.),('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('slippage_5bp',real,.0005)]:
            checks[target][name]=run(events,target,slip)[0]
            gc.collect()
    summary=dict(pick=pick,best_dev_growth=best,eligible=eligible.target_r.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
