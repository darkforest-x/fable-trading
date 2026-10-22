"""Reconcile saved native confirmation study; no trading replay or new signals.

Entry cohorts, economic win/breakeven separation, weighted fee cashflows,
chronological selection clocks and old-to-new outcomes are independently
checked. Recorded past data have already been researched, not unseen holdout.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_fib_confirmation import EXP,SOURCE,ARMS,aggregate,forming
from yoyo.evaluation.pepe_range_exits import signals
from yoyo.evaluation.range_framework import dump,file_stamp
from yoyo.evaluation import monday_fib_limits as base
from yoyo.evaluation.monday_range_v2_2_reference import atr


def run():
    summary=json.loads((EXP/'summary.json').read_text());cfg=summary['config']
    entry=pd.read_csv(EXP/'entries.csv',float_precision='round_trip')
    decisions=pd.read_csv(EXP/'decisions.csv',float_precision='round_trip')
    ledger=pd.read_csv(EXP/'all_parents.csv',float_precision='round_trip')
    frame=base.aligned(pd.read_csv(SOURCE/'candles.csv.gz',float_precision='round_trip'))
    assert frame.index.is_unique and (np.diff(frame.index.asi8)==60_000_000_000).all()
    controls=pd.read_csv(EXP/'controls.csv',float_precision='round_trip')
    quarter=aggregate(frame,15)
    volatility=(atr(quarter,14)/quarter.close).reindex(frame.index,method='ffill')
    buckets=np.digitize(volatility.to_numpy(),cfg['volatility_edges'])
    actual=entry.set_index(['arm','pair_id'])
    assert controls.groupby(['arm','pair_id']).size().eq(cfg['controls_per_signal']).all()
    for r in controls.itertuples():
        s=actual.loc[(r.arm,r.pair_id)]
        t=frame.index[r.entry_i].tz_convert(cfg['timezone'])
        week=(t.normalize()-pd.DateOffset(days=t.dayofweek)).strftime('%Y-%m-%d')
        assert r.week==s.week==week and r.side==s.side
        assert r.segment==s.segment==int(frame.index[r.entry_i]>=pd.Timestamp(cfg['split']))
        assert r.bucket==buckets[r.entry_i]==buckets[int(s.entry_i)]
        assert abs(r.entry-frame.close.iloc[r.entry_i])<1e-8
        for j in (1,2,3,4):
            target=base.rounded(r.entry*s[f'tp{j}']/s.entry,cfg['tick'],r.side==1)
            assert abs(getattr(r,f'tp{j}')-target)<1e-8
    split=int(frame.index.searchsorted(pd.Timestamp(cfg['split'])))
    assert not ((ledger.segment==0)&(ledger.exit_i>=split)).any(), 'Selection read post-split outcome'
    old=ledger[ledger.delay.eq(0)&ledger.population.eq('signal')&ledger.arm.ne('touch')].set_index('parent_id')
    transitions=[];cash=[];reconciliations=0
    for delay in cfg['be_minutes']:
        folder=EXP/f'native_market_be_{delay}'
        rows=pd.read_csv(folder/'trades.csv',float_precision='round_trip')
        fills=pd.read_csv(folder/'fills.csv',float_precision='round_trip')
        parents=ledger[ledger.delay.eq(delay)&ledger.arm.ne('touch')]
        assert len(rows)==4*len(parents)
        assert rows.groupby('parent_id').weight.sum().eq(1).all()
        for p,g in rows.groupby('parent_id'):
            original=g.iloc[0];actual=parents.set_index('parent_id').loc[p]
            assert g.entry_i.nunique()==1 and g.entry.nunique()==1
            assert abs((g.net_r*g.weight).sum()-actual.net_r)<1e-9
            assert abs((g.framework_commission/g.quantity*g.weight).sum()-cfg['round_trip_cost']*original.entry)<1e-7
            reconciliations+=1
        for arm in ARMS:
            selected=parents[parents.arm.eq(arm)&parents.population.eq('signal')]
            expected=entry[entry.arm.eq(arm)].set_index('trade_id')
            assert set(selected.parent_id)==set(expected.index)
            for r in selected.itertuples():
                assert r.entry_i==expected.loc[r.parent_id,'entry_i']
                assert abs(r.entry-expected.loc[r.parent_id,'entry'])<1e-8
            pair=selected[['parent_id','outcome','net_bp']].join(old[['outcome','net_bp']],on='parent_id',rsuffix='_old')
            transitions.append(dict(arm=arm,delay=delay,
                counts=[dict(old=a,new=b,n=int(n)) for (a,b),n in pair.groupby(['outcome_old','outcome']).size().items()],
                mean_change_bp=float((pair.net_bp-pair.net_bp_old).mean())))
            r=rows[rows.arm.eq(arm)&rows.population.eq('signal')].set_index('trade_id')
            f=fills[fills.trade_id.isin(r.index)].join(r[['side','entry','risk','quantity','weight']],on='trade_id',rsuffix='_initial')
            pnl=f.side*(f.price-f.entry)*f.quantity
            pnl=pnl.where(f.phase.ne('entry'),0)-f.commission
            flows=pnl/f.quantity_initial/f.risk*f.weight
            curve=flows.groupby(f.ts_ns).sum().sort_index().cumsum()
            peak=np.maximum.accumulate(np.r_[0.,curve.to_numpy()])[1:]
            dd=float(np.max(peak-curve.to_numpy()))
            assert abs(curve.iloc[-1]-selected.net_r.sum())<1e-8
            cash.append(dict(arm=arm,delay=delay,realized_cash_dd_r=dd,
                pf=float(selected.loc[selected.net_r>1e-9,'net_r'].sum()/-selected.loc[selected.net_r< -1e-9,'net_r'].sum())))
    # Rebuild the current partial higher candle directly from a short prefix.
    appearance_checks=0
    for row in entry.drop_duplicates(['arrow_i','arrow_minutes']).itertuples():
        i=int(row.arrow_i);m=int(row.arrow_minutes)
        start=max(0,i-20*m)
        raw=frame.iloc[start:i+1]
        g=raw.resample(f'{m}min',closed='right',label='right',origin='epoch')
        partial=g.agg(dict(open='first',high='max',low='min',close='last'))
        assert int(signals(partial).iloc[-1])==row.side
        appearance_checks+=1
    allfull=[r for r in summary['comparisons'] if r['segment']==-1 and r['arm']!='touch']
    p=np.array([r['comparison']['p_one_sided'] for r in allfull]);order=np.argsort(p)
    adjusted=np.empty(len(p));adjusted[order]=np.minimum(1,np.maximum.accumulate(p[order]*(len(p)-np.arange(len(p)))))
    ranked=[dict(arm=r['arm'],delay=r['delay'],p=float(v)) for r,v in zip(allfull,adjusted)]
    admission=[]
    for arm,g in entry.groupby('arm'):
        d=decisions[decisions.arm.eq(arm)]
        admission.append(dict(arm=arm,entries=len(g),statuses=d.status.value_counts().to_dict(),
            arrow_first=int((g.arrow_i<g.body_i).sum()),body_first=int((g.body_i<g.arrow_i).sum()),
            same_minute=int((g.body_i==g.arrow_i).sum()),
            entry_back_outside=int((g.side*(g.entry-g.level)<0).sum()),
            arrow_later_disappeared=int(g.arrow_disappeared_by_close.sum()),
            wait_median_minutes=float(g.wait_minutes.median())))
    dump(EXP/'review.json',dict(parent_cash_reconciliations=reconciliations,
        matched_control_checks=len(controls),minute_grid_rows=len(frame),
        no_post_split_development_outcomes=True,independent_partial_candle_checks=appearance_checks,
        admission=admission,transitions=transitions,cash_metrics=cash,holm_adjusted=ranked,
        multiplicity_comparisons=len(p),
        focused_and_layer_tests=107,
        broader_preflight=dict(passed=170,failed=16,skipped=12,
            explanation='Broad suite was accidentally run in isolated Nautilus runtime: missing training/render/YAML dependencies and different isolated numpy/pandas pins; existing docs/sibling-path failures also remain. No training environment, dependencies or unrelated files changed.'),
        initial_test_fixture_failure='Pandas3 refused assigning fractional prices into integer test fixture; explicit float fixture fixed before historical execution.',
        limitations='1m close samples miss sub-minute arrow flicker. Latches persist even after conditions cease;9any-arm entries are back outside key. Final scope uses immediate native TP-triggered protection only, no holding-time rule; same .2percent entry-notional cost. Independent R is not portfolio return.'))
    dump(EXP/'delivery_receipt.json',dict(experiment_id=EXP.name,
        source_builder=summary['source_commit'],engine='NautilusTrader1.231.0',
        native_positions=sum(x['positions'] for x in summary['native_audits']+summary['baseline_audits']),
        native_fills=sum(x['fills'] for x in summary['native_audits']+summary['baseline_audits']),
        native_oracle_checks=sum(x['oracle_checked'] for x in summary['native_audits']+summary['baseline_audits']),
        fee=cfg['round_trip_cost'],no_content_hash=True,
        files=[file_stamp(p) for p in sorted(EXP.rglob('*')) if p.is_file() and p.name!='delivery_receipt.json'],
        training_eligible=False,production_eligible=False))
    print(json.dumps(dict(admission=admission,checks=reconciliations,appearances=appearance_checks),ensure_ascii=False))


if __name__=='__main__':run()
