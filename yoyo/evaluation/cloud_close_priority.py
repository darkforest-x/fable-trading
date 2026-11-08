"""Simultaneous-signal priority on the development-selected funded V1.7 exit.

Feature windows: prior Wilder ATR14 / close, side-adjusted prior 14-bar log
return relative to BTC, confirmed key's reclaim in reference stop R, pinned
NY Monday width, confirmed EMA20/50 spread / prior ATR14, key wick/range,
and contiguous 12H bar count (data-history depth, not issuance age).
"""
import gc
import json
import numpy as np
import pandas as pd
from yoyo.evaluation import cloud_close_reward_account as w
from yoyo.evaluation import cloud_close_slots as capacity
from yoyo.evaluation import cloud_close_scaleout as split

EXP=w.ROOT/'experiments/active/exp-cloud-close-priority-20261109-v1'
POLICIES={'high_prior_atr':('prior_atr_pct',-1),'low_prior_atr':('prior_atr_pct',1),
          'strong_relative_momentum':('rel_strength_14',-1),'weak_relative_momentum':('rel_strength_14',1),
          'tight_reclaim':('close_distance_r',1),'strong_reclaim':('close_distance_r',-1),
          'wide_monday':('monday_width_pct',-1),'strong_cloud':('ema_spread_atr',-1),
          'large_wick':('wick_share',-1),'younger':('prior_observed_12h',1),'older':('prior_observed_12h',-1)}


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    if ['crc',*POLICIES]!=cfg['policies']:
        raise ValueError('Ranking policies diverge from registration')
    fraction=json.loads((split.EXP/'summary.json').read_text())['pick']
    slots=json.loads((capacity.EXP/'summary.json').read_text())['pick']
    source=pd.read_csv(w.SOURCE/'signals.csv')
    source.index=pd.RangeIndex(1,len(source)+1)
    paths=w.read_paths()
    paths=paths[paths.tp_r.isin([0,2])].copy()
    base=pd.read_csv(capacity.EXP/'account_metrics.csv')
    rows=base[base.slots.eq(slots)].drop(columns='slots').assign(policy='crc').to_dict('records')
    outputs={}
    def rank(label):
        field,sign=POLICIES[label]
        paths['entry_order']=(paths.source_row_number.map(source[field])*sign).fillna(np.inf)
    for name in POLICIES:
        rank(name)
        print('Comparing',name,flush=True)
        m,trades,eq=capacity.run(paths[~paths.is_control],slots,fraction)
        rows.extend(dict(policy=name,period=p,**v) for p,v in m.items())
        outputs[name]=trades,eq
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(w.qualifies,axis=1)&dev.trades.ge(100)]
    pick=eligible.policy.iloc[0] if len(eligible) else 'crc'
    best=dev.policy.iloc[0]
    checks={}
    for name in sorted({pick,best}-{'crc'}):
        trades,eq=outputs[name]
        trades.to_csv(EXP/f'funded_{name}.csv.gz')
        eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        rank(name)
        real,null=paths[~paths.is_control],paths[paths.is_control]
        checks[name]={}
        for lab,events,slip in [('control',null,0.),('paired_signals',real[real.parent_id.isin(null.parent_id)],0.),
                                ('slippage_5bp',real,.0005)]:
            checks[name][lab]=capacity.run(events,slots,fraction,slip)[0]
            gc.collect()
    result=dict(parent_fraction=fraction,slots=slots,pick=pick,best_dev_growth=best,
                eligible=eligible.policy.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
