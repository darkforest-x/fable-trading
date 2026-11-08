"""A one-factor account capacity comparison on unchanged confirmed V1.7 fills."""
import gc
import json
import pandas as pd
from yoyo.evaluation import cloud_close_reward_account as w
from yoyo.evaluation import cloud_close_scaleout as split

EXP=w.ROOT/'experiments/active/exp-cloud-close-slots-20261109-v1'


def run(pool, slots, fraction, slip=0.):
    if fraction in (0.,1.):
        metrics,_,trades,eq=w.evaluate(pool[pool.tp_r.eq(2*fraction)],slots,slip=slip)
        return metrics,trades,eq
    data=split.pair_data(pool[pool.tp_r.eq(0)],pool[pool.tp_r.eq(2)],slots)
    return split.simulate(data,fraction,slip)


def main():
    fraction=json.loads((split.EXP/'summary.json').read_text())['pick']
    metrics=pd.read_csv(split.EXP/'account_metrics.csv')
    rows=metrics[metrics.fraction.eq(fraction)].drop(columns='fraction').assign(slots=12).to_dict('records')
    paths=w.read_paths()
    paths=paths[paths.tp_r.isin([0,2])]
    real,ctl=paths[~paths.is_control],paths[paths.is_control]
    samples={}
    for slots in (1,3,6,9,18,24):
        print('Evaluating capacity',slots,flush=True)
        m,trades,eq=run(real,slots,fraction)
        samples[slots]=(trades,eq)
        rows.extend(dict(slots=slots,period=p,**x) for p,x in m.items())
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(w.qualifies,axis=1)&dev.trades.ge(100)]
    pick=int(eligible.slots.iloc[0]) if len(eligible) else 12
    best=int(dev.slots.iloc[0])
    checks={}
    for slots in sorted({pick,best}-{12}):
        trades,eq=samples[slots]
        trades.to_csv(EXP/f'funded_{slots}.csv.gz')
        eq.to_csv(EXP/f'hourly_{slots}.csv.gz',index_label='hour_end_utc')
        checks[slots]={}
        for label,pool,slip in [('control',ctl,0.),('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                ('slippage_5bp',real,.0005)]:
            checks[slots][label]=run(pool,slots,fraction,slip)[0]
            gc.collect()
    summary=dict(parent_fraction=fraction,pick=pick,best_dev_growth=best,eligible=eligible.slots.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
