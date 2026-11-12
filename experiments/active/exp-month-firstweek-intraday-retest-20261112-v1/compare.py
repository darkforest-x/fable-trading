"""Summarize observable confirmation paths and compare on common signal IDs and price progress."""
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[2]/'data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/intraday_retest'
SPLIT = pd.Timestamp('2025-01-01T00:00Z').value//1_000_000
DAY = 86_400_000


def pair_controls(read):
    # Equal NY month, direction and elapsed-time band. Progress is measured before
    # the forward path starts. Each first-failure control can be used once.
    read = read.assign(delay_band=pd.cut(read.delay_min,[0,60,180,360,720,1440],include_lowest=True))
    paired = []
    for _, group in read.groupby(['month','side','delay_band'],observed=True):
        ok = group[group.state.eq('held')]
        failed = group[group.state.eq('failed')]
        if ok.empty or failed.empty:
            continue
        gap = np.abs(ok.move_before_pct.to_numpy()[:,None] - failed.move_before_pct.to_numpy())
        rows, cols = np.nonzero(gap<=.5)
        used_ok, used_failed = set(), set()
        for k in np.argsort(gap[rows,cols],kind='stable'):
            i,j = rows[k],cols[k]
            if i in used_ok or j in used_failed:
                continue
            used_ok.add(i)
            used_failed.add(j)
            a,b = ok.iloc[i],failed.iloc[j]
            paired.append(dict(month=a.month,minutes=a.minutes,side=a.side,held_id=a.id,failed_id=b.id,
                               diff_progress=a.move_before_pct-b.move_before_pct,
                               diff_delay_min=a.delay_min-b.delay_min,
                               held_close24=a.close24,failed_close24=b.close24,
                               diff_close24=a.close24-b.close24))
    return pd.DataFrame(paired)


def main():
    events=pd.read_csv(OUT/'events.csv.gz')
    same=events[events.coverage.eq('complete')].copy()
    funnel=same.groupby(['minutes','state']).size().rename('n').reset_index()
    funnel.to_csv(HERE/'funnel.csv', index=False)
    ready=same[same.state.isin(['held','failed'])].copy()
    rows=[]
    for period,frame in [('all',ready),('through_2024',ready[ready.read_ms+DAY<=SPLIT]),
                         ('from_2025',ready[ready.touch_ms>=SPLIT])]:
        for side,data in [('both',frame),*frame.groupby('side')]:
            for (minutes,state),g in data.groupby(['minutes','state']):
                metrics=dict(period=period,side=side,minutes=int(minutes),state=state,n=len(g),
                             distinct_opens=len(g.drop_duplicates(['symbol','side','read_ms'])),
                             delay_med=g.delay_min.median(),move_before_med=g.move_before_pct.median(),
                             room_to_edge_med=g.room_to_edge_pct.median())
                for h in (4,24):
                    metrics.update({f'mean{h}':g[f'close{h}'].mean(),f'median{h}':g[f'close{h}'].median(),
                                    f'positive{h}':100*g[f'close{h}'].gt(0).mean(),
                                    f'med_favorable{h}':g[f'favorable{h}'].median(),
                                    f'med_adverse{h}':g[f'adverse{h}'].median()})
                rows.append(metrics)
    pd.DataFrame(rows).to_csv(HERE/'outcomes.csv',index=False,float_format='%.6f')
    controls=pd.concat([pair_controls(g) for _,g in ready.groupby('minutes')],ignore_index=True)
    controls.to_csv(OUT/'controls.csv.gz',index=False)
    rows=[]
    for tf,g in controls.groupby('minutes'):
        monthly=g.groupby('month').diff_close24.agg(['sum','size']).to_numpy()
        months=len(monthly)
        draws=np.random.default_rng(1618).integers(0,months,size=(3000,months))
        sums=monthly[draws].sum(axis=1)
        lo,hi=np.quantile(sums[:,0]/sums[:,1],[.025,.975])
        rows.append(dict(minutes=int(tf),pairs=len(g),months=months,held=g.held_close24.mean(),
                         failed=g.failed_close24.mean(),diff=g.diff_close24.mean(),
                         ci_low=lo,ci_high=hi,median_progress_gap=g.diff_progress.abs().median()))
    pd.DataFrame(rows).to_csv(HERE/'matched.csv',index=False,float_format='%.6f')
    held=ready[ready.state.eq('held')]
    both=held[held.minutes.eq(5)].merge(held[held.minutes.eq(15)],on='id',suffixes=('_5','_15'),validate='one_to_one')
    result=dict(both=len(both),only5=len(held[held.minutes.eq(5)])-len(both),
                only15=len(held[held.minutes.eq(15)])-len(both),faster5=int((both.read_ms_5<both.read_ms_15).sum()),
                tied=int((both.read_ms_5==both.read_ms_15).sum()),
                median_minutes_earlier5=(both.delay_min_15-both.delay_min_5).median(),
                gross24_5=both.close24_5.mean(),gross24_15=both.close24_15.mean())
    pd.DataFrame([result]).to_csv(HERE/'overlap.csv',index=False,float_format='%.6f')


if __name__=='__main__':
    main()
