"""Pay out less of a trade at 2R on the frozen once-confirmed EMA exit variant."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_slots as capacity


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-confirm-split-20261109-v1'
PARENT=EXP.parent/'exp-cloud-close-flip-confirm-20261109-v1'


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    setup=json.loads((PARENT/'summary.json').read_text())
    rule=setup['development_pick']['pick']
    if rule!='confirmed_1x12h':
        raise ValueError('Unexpected parent cadence')
    columns=['event_id','parent_id','source_row_number','symbol','side','tp_r','is_control','entry_ms',
             'exit_bar_ms','entry_price','exit_price','exit_phase','exit_fee_rate','entry_status',
             'cadence','stop_price','exit_reason','gap_flag']
    frame=pd.read_csv(PARENT/'paths.csv.gz',usecols=columns,dtype={'symbol':str},keep_default_na=False)
    paths=frame[frame.cadence.eq(rule)&frame.entry_status.eq('executed')].copy()
    for name in ['entry_ms','exit_bar_ms','source_row_number']:
        paths[name]=pd.to_numeric(paths[name],errors='raise').astype('int64')
    for name in ['entry_price','exit_price','stop_price','exit_fee_rate','tp_r']:
        paths[name]=pd.to_numeric(paths[name],errors='raise')
    if not paths.groupby('event_id').size().eq(2).all() or set(paths.tp_r)!={0.,2.}:
        raise ValueError('Target and runner coverage differs')
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    src.index=pd.RangeIndex(1,len(src)+1)
    paths['entry_order']=paths.source_row_number.map(src.prior_atr_pct).fillna(np.inf)
    real,ctl=paths[~paths.is_control],paths[paths.is_control]
    parent=pd.read_csv(PARENT/'account_metrics.csv')
    rows=parent[parent.cadence.eq(rule)].drop(columns=['cadence','confirmations']).assign(fraction=.75).to_dict('records')
    curves={}
    for weight in cfg['fractions']:
        if weight==.75:
            continue
        metrics,pos,eq=capacity.run(real,9,weight)
        rows.extend(dict(fraction=weight,period=p,**v) for p,v in metrics.items())
        curves[weight]=pos,eq
        gc.collect()
    result=pd.DataFrame(rows)
    result.to_csv(EXP/'account_metrics.csv',index=False)
    dev=result[result.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=float(eligible.fraction.iloc[0]) if len(eligible) else None
    checks={}
    if pick is not None:
        if pick==.75:
            raise ValueError('Unexpected change in frozen parent money payoff')
        pos,eq=curves[pick]
        pos.to_csv(EXP/'funded_dev_pick.csv.gz',index=pick not in (0.,1.))
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            checks[name]=capacity.run(events,9,pick,slip)[0]
            gc.collect()
    summary=dict(pick=pick,eligible=eligible.fraction.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(result[result.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
