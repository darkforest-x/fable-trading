"""Bank a fixed fraction of the V1.7 position at a resting 2R limit."""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation import cloud_close_reward_account as w

EXP = w.ROOT / 'experiments/active/exp-cloud-close-scaleout-20261109-v1'


def pair_data(runner, target, slots=12):
    if not np.array_equal(np.sort(runner.event_id),np.sort(target.event_id)):
        raise ValueError('Split legs have different event sets')
    first, skips = w.admit(runner,slots)
    second = target.set_index('event_id',drop=False).loc[first.event_id].copy()
    if (second.exit_bar_ms.to_numpy()>first.exit_bar_ms.to_numpy()).any():
        raise ValueError('Target leg outlasts runner')
    second['slot'] = first.slot.to_numpy()*2
    first['slot'] = first.slot*2+1
    merged = pd.concat([second,first],ignore_index=True)
    return w.prepare(None,slots*2,assigned=(merged,skips))


def simulate(data, fraction, slip=0., full_slots=0, slot_weights=None):
    legs, eq = w.account(data, weights=(fraction,1-fraction), slip=slip,
                         full_slots=full_slots,slot_weights=slot_weights)
    if not legs.groupby('event_id').size().eq(2).all():
        raise ValueError('Unpaired funded legs')
    g = legs.groupby('event_id',sort=False)
    pos = g.agg(parent_id=('parent_id','first'),symbol=('symbol','first'),side=('side','first'),
                entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','max'),
                cash_pnl=('cash_pnl','sum'),notional=('notional','sum'))
    pos['net_return'] = pos.cash_pnl/pos.notional
    fraction_filled = legs[legs.tp_r.eq(2)].set_index('event_id').notional/pos.notional
    if not np.allclose(fraction_filled,fraction,atol=1.e-9):
        raise ValueError('Funded weights did not match the intended split')
    limits = {'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
              '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}
    return {k:w.summarize(pos,eq,*v) for k,v in limits.items()},pos,eq


def main():
    import gc
    paths = w.read_paths()
    real = paths[~paths.is_control]
    ctl = paths[paths.is_control]
    base = pd.read_csv(w.EXP/'account_metrics.csv')
    rows = base[base.tp_r.isin([0,2])].copy()
    rows['fraction'] = rows.tp_r/2
    results = rows.drop(columns='tp_r').to_dict('records')
    def data(pool):
        return pair_data(pool[pool.tp_r.eq(0)],pool[pool.tp_r.eq(2)])
    prepared = data(real)
    curves = {}
    for fraction in (.25,.5,.75):
        m,trades,eq = simulate(prepared,fraction)
        results.extend(dict(period=p,fraction=fraction,**v) for p,v in m.items())
        curves[fraction]=(trades,eq)
    frame = pd.DataFrame(results)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev = frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(w.qualifies,axis=1)&dev.trades.ge(100)]
    pick = eligible.fraction.iloc[0] if len(eligible) else 0.
    best = dev.fraction.iloc[0]
    partials = sorted({pick,best} & curves.keys())
    checks = {}
    for fraction in partials:
        trades,eq = curves[fraction]
        trades.to_csv(EXP/f'funded_{fraction}.csv.gz',index_label='event_id')
        eq.to_csv(EXP/f'hourly_{fraction}.csv.gz',index_label='hour_end_utc')
        checks[fraction]={'slippage_5bp':simulate(prepared,fraction,slip=.0005)[0]}
    del prepared
    gc.collect()
    if partials:
        for name,pool in [('control',ctl),('paired_signals',real[real.parent_id.isin(ctl.parent_id)])]:
            prep=data(pool)
            for fraction in partials:
                checks[fraction][name]=simulate(prep,fraction)[0]
            del prep
            gc.collect()
    summary=dict(pick=pick,best_dev_growth=best,eligible=eligible.fraction.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False),flush=True)


if __name__=='__main__':
    main()
