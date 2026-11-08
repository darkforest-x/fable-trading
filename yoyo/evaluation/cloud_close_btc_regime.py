"""Gate on a contiguous BTC return window ending before the 12H key opens."""
import gc
import json
import numpy as np
import pandas as pd
from yoyo.evaluation import cloud_close_priority as priority
from yoyo.evaluation import cloud_close_slots as capacity
from yoyo.evaluation import cloud_close_reward_account as w

EXP=w.ROOT/'experiments/active/exp-cloud-close-btc-regime-20261109-v1'
DAYS=(14,30,60,90,180)
BAR=12*w.HOUR


def regime(window, btc):
    """Indexed by signal time; only BTC closing prices known at key open enter the return."""
    n=window*2
    # BTC file is indexed by bar OPEN. Its close becomes available one bar
    # later, which is the candidate key open one further bar before confirmation.
    side=np.sign(btc.close/btc.close.shift(n)-1)
    contiguous=btc.time-btc.time.shift(n)==n*BAR
    return pd.Series(side.where(contiguous).to_numpy(),index=btc.time+2*BAR)


def main():
    setup=json.loads((priority.EXP/'summary.json').read_text())
    fraction,slots,rule=setup['parent_fraction'],setup['slots'],setup['pick']
    paths=w.read_paths()
    paths=paths[paths.tp_r.isin([0,2])].copy()
    src=pd.read_csv(w.SOURCE/'signals.csv')
    src.index=pd.RangeIndex(1,len(src)+1)
    if rule!='crc':
        feature,sign=priority.POLICIES[rule]
        paths['entry_order']=(sign*paths.source_row_number.map(src[feature])).fillna(np.inf)
    btc=pd.read_csv(w.TOP/'inputs/BTC/bars12h.csv.gz',usecols=['time','close'])
    baseline=pd.read_csv(priority.EXP/'account_metrics.csv')
    results=baseline[baseline.policy.eq(rule)].drop(columns='policy').assign(days=0).to_dict('records')
    samples={}
    missing={}
    def arms(days):
        bias=paths.entry_ms.map(regime(days,btc))
        ok=paths.side.eq(bias)
        real=paths[ok&~paths.is_control]
        ctl=paths[ok&paths.is_control&paths.parent_id.isin(real.parent_id)]
        return real,ctl,int((~paths.is_control&paths.tp_r.eq(0)&bias.isna()).sum())
    for days in DAYS:
        pool,_,missing[days]=arms(days)
        print('Comparing BTC lookback',days,flush=True)
        m,trades,eq=capacity.run(pool,slots,fraction)
        samples[days]=trades,eq
        results.extend(dict(days=days,period=p,**v) for p,v in m.items())
        gc.collect()
    frame=pd.DataFrame(results)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(w.qualifies,axis=1)&dev.trades.ge(100)]
    pick=int(eligible.days.iloc[0]) if len(eligible) else 0
    best=int(dev.days.iloc[0])
    checks={}
    for days in sorted({pick,best}-{0}):
        trades,eq=samples[days]
        trades.to_csv(EXP/f'funded_{days}.csv.gz')
        eq.to_csv(EXP/f'hourly_{days}.csv.gz',index_label='hour_end_utc')
        pool,ctl,_=arms(days)
        checks[days]={}
        for lab,events,slip in [('control',ctl,0.),('paired_signals',pool[pool.parent_id.isin(ctl.parent_id)],0.),
                                ('slippage_5bp',pool,.0005)]:
            checks[days][lab]=capacity.run(events,slots,fraction,slip)[0]
            gc.collect()
    res=dict(parent_priority=rule,slots=slots,parent_fraction=fraction,pick=pick,best_dev_growth=best,
             eligible=eligible.days.tolist(),missing_btc_window=missing,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(res,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
