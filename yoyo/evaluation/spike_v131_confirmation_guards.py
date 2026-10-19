"""Preserve original draws while checking the signal's UTC-Sunday anchor exclusion.

The inherited raw control archive allows Sunday anchors, whereas the later 15m
summary removes them. Keep the first all-draw analysis unchanged and provide the
same time-guard sensitivity for both timeframes. Do not redraw or require random
confirmation success. No signal policy or economic outcome changes.
"""
import argparse
import json
import math
from pathlib import Path
import pandas as pd
from yoyo.evaluation.spike_v131_confirmation_cost import paired, temporal_period


def run(root):
    root=Path(root);original=json.loads((root/'result.json').read_text());cfg=original['config']
    f=pd.read_csv(root/'events.csv.gz');cut=pd.Timestamp(cfg['split'])
    for k in ['anchor_time','mature_time']:f[k]=pd.to_datetime(f[k],utc=True)
    f['period']=[temporal_period(a,m,cut) for a,m in zip(f.anchor_time,f.mature_time)]
    signals=f[f.cohort.eq('signal')];controls=f[f.cohort.eq('control')]
    assert signals.anchor_time.dt.dayofweek.ne(6).all()
    excluded=controls[controls.anchor_time.dt.dayofweek.eq(6)]
    controls=controls[controls.anchor_time.dt.dayofweek.ne(6)]
    rows=[];checks=0
    for minutes in [15,60]:
        for side in [0,1,-1]:
            for period in ['all','early','later']:
                def sub(g):
                    return g[g.timeframe_min.eq(minutes) & (True if side==0 else g.side.eq(side)) &
                             (True if period=='all' else g.period.eq(period))]
                t,c=sub(signals),sub(controls)
                metrics=paired(t,c,cfg,['parent_r','delayed_r'])
                # Independent scalar join checks the final comparison sums.
                cd={r.trade_key:r for r in c.itertuples()}
                pairs=[(r,cd[r.trade_key]) for r in t.itertuples() if r.trade_key in cd]
                assert len(pairs)==metrics['n']
                for key in ['parent_r','delayed_r']:
                    total=math.fsum(getattr(a,key)-getattr(b,key) for a,b in pairs)
                    assert math.isclose(total,metrics[key]['excess']['sum'],abs_tol=1e-7)
                    checks+=1
                rows.append(dict(timeframe_min=minutes,side=side,period=period,matched=metrics))
    out=dict(status='passed',definition='Original control anchor UTC weekday != Sunday, no redraw; known cancellation/expiry still zero.',
        excluded_by_timeframe={str(k):int(v) for k,v in excluded.groupby('timeframe_min').size().items()},
        scalar_sum_checks=checks,comparisons=rows,
        limitations='Source raw matching and inherited Sunday-filtered summary differ; both are retained. This checks the time guard only, not all possible market-state confounding.')
    (root/'guard_sensitivity.json').write_text(json.dumps(out,indent=2,allow_nan=False))
    for r in rows:
        if r['side']==0:print(json.dumps(r))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);run(p.parse_args().root)
