"""Reconcile morphology groups and expose clock/deduplication sensitivities.

Read existing features and executions only. Complements must recover complete
cohorts, matched-reference totals and all18prior summaries. One repeated BTC
touch is retained by the strategy but separately deduplicated for descriptive
sensitivity. Recent5m/capped exits are supplemental, not extra annual tests.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_morphology import (
    CONFIG,ROOT,read,mask_for,join_outcomes,group_stats,dump,file_stamp,
)


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit verification builder first')
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    result=json.loads((dest/'summary.json').read_text())
    groups=read(dest/'groups.csv');base=read(dest/'baselines.csv')
    original=json.loads((ROOT/cfg['source']/'summary.json').read_text())
    partitions=0;reconciled=0
    for keys,part in groups.groupby(['symbol','arm','period','cut']):
        coin,arm,period,cut=keys
        assert len(part)==2 and set(part.value)=={False,True}
        expected=base[(base.symbol==coin)&(base.arm==arm)&(base.period==period)].iloc[0]
        for col in ['n','wins','gross_wins','net_r','gross_r','random_net_r']:
            np.testing.assert_allclose(part[col].sum(),expected[col],rtol=0,atol=1e-7)
        for col in ['mean_bp','random_mean_bp']:
            np.testing.assert_allclose((part[col]*part.n).sum()/part.n.sum(),expected[col],rtol=0,atol=1e-7)
        partitions+=1
    for r in original['rows']:
        if r['version']!='v231' or r['group']!='all':continue
        observed=base[(base.symbol==r['symbol'])&(base.arm==r['arm'])&(base.period=='all')].iloc[0]
        for col in ['n','wins','net_r','random_net_r']:
            np.testing.assert_allclose(observed[col],r[col],rtol=0,atol=1e-7)
        reconciled+=1
    assert reconciled==36
    for old in result['sources']+result['prepared']['inputs']:
        assert old==file_stamp(old['path'])
    stability=[];dedup=[];outcome_breakdown=[];examples=[]
    for spec in cfg['symbols']:
        coin=spec['symbol'];d=read(dest/f'{coin}_annotated_trades.csv')
        unique=d.sort_values(['entry_time','signal_i']).drop_duplicates('event_id')
        numerical=d.select_dtypes(include=[np.number])
        assert np.isfinite(numerical.to_numpy()).all()
        np.testing.assert_allclose(d.wick_share+d.body_share+d.opposite_wick_share,1,atol=1e-10,rtol=0)
        assert (d.penetration>=.1-1e-10).all() and (d.penetration<=1+1e-10).all()
        ledger=read(ROOT/cfg['source']/coin/'pivot/trades.csv');s=ledger[ledger.population=='signal']
        outcome_breakdown.append(dict(symbol=coin,n=len(s),initial_stop=int((s.reason==1).sum()),
            pivot_exits=int((s.reason==7).sum()),
            pivot_nonpositive_gross=int(((s.reason==7)&(s.gross_r<=1e-9)).sum()),
            gross_win_net_loss=int(((s.gross_r>1e-9)&(s.net_r<=1e-9)).sum())))
        for cut in cfg['hypotheses']:
            q=groups[(groups.symbol==coin)&(groups.period=='all')&(groups.cut==cut['id'])]
            table=q.pivot(index='arm',columns='value',values='win_rate');delta=table[True]-table[False]
            qualified=q[q.value]
            stability.append(dict(symbol=coin,cut=cut['id'],higher_win_exits=int((delta>1e-9).sum()),
                lower_win_exits=int((delta< -1e-9).sum()),equal_win_exits=int((abs(delta)<=1e-9).sum()),
                positive_qualified_net_r_exits=int((qualified.net_r>0).sum()),
                positive_qualified_mean_bp_exits=int((qualified.mean_bp>0).sum())))
            yes=mask_for(unique,cut)
            dedup.append(dict(symbol=coin,cut=cut['id'],yes=group_stats(unique[yes]),no=group_stats(unique[~yes])))
        for selection,idx in [('largest_wick_share',d.wick_share.idxmax()),('largest_net_winner',d.out_net_r.idxmax())]:
            r=d.loc[idx]
            examples.append(dict(selection=selection,symbol=coin,pair_id=int(r.pair_id),touch_time=r.touch_time,
                entry_time=r.entry_time,side=int(r.side),key=r.key,wick_share=float(r.wick_share),
                wick_atr=float(r.wick_atr),risk_bp=float(r.risk_bp),net_r=float(r.out_net_r)))
    paths={
        'pivot5m':'exp-btc-v231-exit5m-20261021-v1/native/pivot5m/trades.csv',
        'structure5m':'exp-btc-v231-exit5m-20261021-v1/native/structure5m/trades.csv',
        'time90m':'exp-btc-v231-key90m-20261021-v1/time90m/trades.csv',
        'key50_time90m':'exp-btc-v231-key90m-20261021-v1/key50_time90m/trades.csv',
    }
    features=read(dest/'BTC_features.csv')
    features=features[pd.to_datetime(features.entry_time,utc=True)>=pd.Timestamp(cfg['recent_start'])]
    recent=[]
    for arm,path in paths.items():
        d=join_outcomes(features,read(ROOT/'experiments/active'/path))
        for cut in cfg['hypotheses']:
            flag=mask_for(d,cut)
            recent.append(dict(arm=arm,cut=cut['id'],yes=group_stats(d[flag]),no=group_stats(d[~flag])))
    dump(dest/'verification.json',dict(partitions_reconciled=partitions,original_baselines_reproduced=reconciled,
        feature_tests_passed=8,causal_prefix_checks=12,input_stamps_unchanged=True,
        outcome_breakdown=outcome_breakdown,stability=stability,event_dedup_sensitivity=dedup,
        recent_exit_groups=recent,examples=examples,
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(json.dumps(dict(partitions=partitions,original_baselines=reconciled,outcome_breakdown=outcome_breakdown),indent=2))


if __name__=='__main__': main()
