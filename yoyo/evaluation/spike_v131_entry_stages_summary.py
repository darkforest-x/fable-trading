"""Pool stage comparisons without mixing full-ledger and matched denominators.

Pre-results audit addendum: compare the stage increment with the same increment
in random controls; cluster by original UTC week across symbols/timeframes.
Two-sided week sign flips are descriptive; Holm spans the twelve later-period
nonbaseline stage/timeframe incremental-excess tests. No new strategy is fitted.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_entry_stages import POLICIES, dump, interval, read, stats


def signflip(values,weeks):
    blocks=pd.DataFrame(dict(value=np.asarray(values),week=np.asarray(weeks))).groupby('week').value.sum().to_numpy()
    if len(blocks)<2:return None
    rng=np.random.default_rng(20261019);observed=abs(float(blocks.sum()));count=0
    for _ in range(20):
        signs=rng.integers(0,2,size=(500,len(blocks)),dtype=np.int8)*2-1
        simulated=signs @ blocks
        count+=int((np.abs(simulated)>=observed-1e-12).sum())
    return (count+1)/10001


def run(root):
    root=Path(root);original=json.loads((root/'result.json').read_text());cfg=original['config']
    start=pd.Timestamp(cfg['analysis_start']);cut=pd.Timestamp(cfg['split']);trades=[];pairs=[]
    for minutes in cfg['timeframes']:
        t=read(root/f'events_{minutes}m.csv.gz',['trade_key','cohort','policy','serial_all','status','entry_time','exit_time','mature_time','net_r','gross_r','cost_r','timeframe_min'])
        for col in ['entry_time','exit_time','mature_time']:t[col]=pd.to_datetime(t[col],utc=True)
        t=t[t.cohort.eq('signal') & t.serial_all & t.status.eq('closed') & (t.entry_time>=start)]
        t['period']=np.where(t.entry_time>=cut,'later',np.where(t.mature_time<cut,'early','crossing'))
        trades.append(t)
        p=read(root/f'pairs_{minutes}m_serial.csv.gz');p['timeframe_min']=minutes
        p['anchor_time']=pd.to_datetime(p.anchor_time,utc=True);pairs.append(p)
    trades=pd.concat(trades,ignore_index=True);pairs=pd.concat(pairs,ignore_index=True)
    serial=[];paired=[];increments=[]
    for period in ['all','early','later']:
        t=trades if period=='all' else trades[trades.period.eq(period)]
        p=pairs if period=='all' else pairs[pairs.period.eq(period)]
        weeks=p.anchor_time.dt.strftime('%G-W%V')
        for policy in POLICIES:
            g=t[t.policy.eq(policy)].sort_values(['exit_time','trade_key'])
            values=g.net_r.to_numpy();equity=np.cumsum(values)
            peak=np.maximum.accumulate(np.r_[0.,equity])[1:]
            top=np.sort(values)[::-1];remove=max(1,math.ceil(len(values)*.01))
            serial.append(dict(period=period,policy=policy)|stats(values)|dict(gross_r=float(g.gross_r.sum()),cost_r=float(g.cost_r.sum()),
                realized_r_drawdown=float((peak-equity).max()) if len(values) else None,
                remove_top_one_percent_r=float(top[remove:].sum()),top10_r=float(top[:10].sum())))
            a=p['signal_'+policy];b=p['control_'+policy]
            paired.append(dict(period=period,policy=policy,target=stats(a),random=stats(b),excess=stats(a-b),
                excess_week95=interval(a-b,weeks,cfg),excess_week_signflip_p=signflip(a-b,weeks)))
        for minutes in [None,*cfg['timeframes']]:
            q=p if minutes is None else p[p.timeframe_min.eq(minutes)]
            w=q.anchor_time.dt.strftime('%G-W%V')
            for policy in POLICIES[:-1]:
                td=q['signal_'+policy]-q.signal_rebreak
                rd=q['control_'+policy]-q.control_rebreak
                increments.append(dict(timeframe_min=minutes,period=period,policy=policy,
                    target_increment=stats(td),random_increment=stats(rd),excess_increment=stats(td-rd),
                    target_increment_week95=interval(td,w,cfg),excess_increment_week95=interval(td-rd,w,cfg),
                    excess_increment_signflip_p=signflip(td-rd,w)))
    family=[r for r in increments if r['period']=='later' and r['timeframe_min'] is not None]
    ranked=sorted(family,key=lambda r:r['excess_increment_signflip_p']);adjusted=0.
    assert len(ranked)==12
    for i,r in enumerate(ranked):
        adjusted=max(adjusted,min(1.,r['excess_increment_signflip_p']*(len(ranked)-i)))
        r['later_12_test_holm_p']=adjusted
    legacy=read('experiments/active/exp-spike-v131-excursions-20261019-v1/results_v1/trades.csv.gz')
    legacy=legacy[legacy.timeframe_min.eq(60) & (pd.to_datetime(legacy.entry_time,utc=True)>=start)].set_index('trade_key')
    fresh=trades[trades.timeframe_min.eq(60)&trades.policy.eq('rebreak')].set_index('trade_key')
    shared=fresh.index.intersection(legacy.index);added=fresh.index.difference(legacy.index);removed=legacy.index.difference(fresh.index)
    np.testing.assert_allclose(fresh.loc[shared,'net_r'],legacy.loc[shared,'net_r'],atol=1e-8,rtol=0)
    old_r=float(legacy.net_r.sum());new_r=float(fresh.net_r.sum());add_r=float(fresh.loc[added,'net_r'].sum());remove_r=float(legacy.loc[removed,'net_r'].sum())
    assert math.isclose(new_r-old_r,add_r-remove_r,abs_tol=1e-7)
    old_change=dict(legacy_n=len(legacy),fresh_n=len(fresh),shared=len(shared),added_n=len(added),removed_n=len(removed),
        legacy_r=old_r,fresh_r=new_r,added_r=add_r,removed_r=remove_r,delta_r=new_r-old_r,
        definition='Jan2023+ closed trades only; old fixed-entry2ATR versus fresh2ATR serial. Shared entries match.')
    out=dict(status='passed',serial=serial,paired_serial=paired,stage_increments=increments,
        one_hour_baseline_change=old_change,early_selection=original['selection'],
        limitations=['Serial and matched queue sums have different populations; compare target/control only within a matched row.',
            'Top1pct removal is descriptive sensitivity, not an executable rule.',
            'Week sign flips assume approximately symmetric independent weekly blocks; exploratory reused history, not forward confirmation.',
            'R drawdown marks realized closed trades only, not intratrade or account drawdown.'])
    dump(root/'comparisons.json',out)
    print(json.dumps(dict(serial=serial,one_hour_baseline_change=old_change)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);run(p.parse_args().root)
