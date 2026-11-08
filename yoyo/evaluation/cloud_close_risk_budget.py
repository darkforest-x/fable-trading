"""Size each confirmed 12h position using the entry open and its known absolute stop."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-risk-budget-20261109-v1'


def reservations(data, exponent):
    """Entry open and V1.7 original stop determine size before either exit fills."""
    items=data['selected']
    positions=items[items.tp_r.eq(0)]
    risk=positions.side*(positions.entry_price-positions.stop_price)/positions.entry_price
    if not np.isfinite(risk).all() or risk.le(0).any():
        raise ValueError('Nonpositive initial risk')
    shape=(data['actions'].shape[0]//3,data['actions'].shape[1]//2)
    matrix=np.ones(shape)
    hours=(positions.entry_ms-data['start'])//wallet.HOUR
    matrix[hours,positions.slot//2]=np.clip((.05/risk)**exponent,.25,2.)
    return matrix


def prepare(pool):
    return split.pair_data(pool[pool.tp_r.eq(0)],pool[pool.tp_r.eq(2)],9)


def run(data, exponent,slip=0.):
    return split.simulate(data,.75,slip,slot_weights=reservations(data,exponent))


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    parent=json.loads((leader.EXP/'summary.json').read_text())
    if parent['pick']!=1.:
        raise ValueError('Expected the coverage-only parent')
    paths=wallet.read_paths()
    paths=paths[paths.tp_r.isin([0,2])].copy()
    rankable=leader.leadership().index
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(rankable)].copy()
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    src.index=pd.RangeIndex(1,len(src)+1)
    paths['entry_order']=paths.source_row_number.map(src.prior_atr_pct).fillna(np.inf)
    real=paths[~paths.is_control]
    ctl=paths[paths.is_control&paths.parent_id.isin(real.parent_id)]
    if len(real)!=2*parent['coverage']['1.0']['eligible_real'] or len(ctl)!=2*parent['coverage']['1.0']['eligible_paired']:
        raise ValueError('Inherited signal or control membership differs')
    baseline=pd.read_csv(leader.EXP/'account_metrics.csv')
    rows=baseline[baseline.top_fraction.eq(1.)].drop(columns='top_fraction').assign(exponent=0).to_dict('records')
    data=prepare(real)
    curves={}
    for exponent in cfg['exponents'][1:]:
        metrics,positions,equity=run(data,exponent)
        rows.extend(dict(exponent=exponent,period=period,**values) for period,values in metrics.items())
        curves[exponent]=positions,equity
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick=float(eligible.exponent.iloc[0]) if len(eligible) else 0.
    best=float(dev.exponent.iloc[0])
    checks={}
    new=sorted({pick,best}-{0.})
    for e in new:
        positions,equity=curves[e]
        positions.to_csv(EXP/f'funded_{e}.csv.gz')
        equity.to_csv(EXP/f'hourly_{e}.csv.gz',index_label='hour_end_utc')
        checks[e]={'slippage_5bp':run(data,e,.0005)[0]}
    del data
    gc.collect()
    if new:
        for name,events in [('control',ctl),('paired_signals',real[real.parent_id.isin(ctl.parent_id)])]:
            d=prepare(events)
            for e in new:
                checks[e][name]=run(d,e)[0]
            del d
            gc.collect()
    result=dict(pick=pick,best_dev_growth=best,eligible=eligible.exponent.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
