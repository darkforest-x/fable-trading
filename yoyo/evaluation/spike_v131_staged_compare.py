"""Pool frozen selected entry policies and isolate original-risk-capped winners.

All selection is historical; full and pre2025-selected maps remain distinct.
Combining per-timeframe/side results preserves the original independent streams,
not an account portfolio. Weekly intervals describe a fixed map only, without
multiple-search adjustment. Source observations and costs are unchanged.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_staged_entry import candidate_returns, period_mask, stat

ROOT=Path('experiments/active/exp-spike-v131-staged-entry-search-20261019-v1/results_v2')


def run():
    result=json.loads((ROOT/'result.json').read_text())
    frame=pd.read_csv(ROOT/'leg_paths.csv.gz')
    grid=pd.read_csv(ROOT/'search.csv.gz')
    for k in ['entry_time','exit_time']:frame[k]=pd.to_datetime(frame[k],utc=True)
    cut=pd.Timestamp(result['config']['split'])
    tf_names=[f'{tf}m' for tf in [15,30,60,240]]
    side_names=[f'{tf}m_side{side}' for tf in [15,30,60,240] for side in [1,-1]]
    def group_mask(f,name):
        if name=='all':return np.ones(len(f),bool)
        parts=name.split('_side');m=f.timeframe_min.eq(int(parts[0][:-1]))
        return m if len(parts)==1 else m&f.side.eq(int(parts[1]))
    comparisons=[];choices=[];details=[]
    for selection_period,evaluation in [('full','full'),('early','later')]:
        for grouping,names in [('uniform',['all']),('timeframe',tf_names),('timeframe_side',side_names)]:
            for constrained in [False,True]:
                selected=[];chunks=[]
                for name in names:
                    candidates=grid[grid.group.eq(name)&grid.period.eq(selection_period)]
                    if constrained:candidates=candidates[candidates.method.ne('reclaim')]
                    p=candidates.sort_values(['sum_net_r','fraction','candidate_id'],ascending=[False,False,True]).iloc[0].to_dict()
                    selected.append(p)
                    part=frame[group_mask(frame,name)&period_mask(frame,evaluation,cut)].copy()
                    part['candidate_r'],filled=candidate_returns(part,p)
                    part['filled']=filled | (p['fraction']>0)
                    part['policy_group']=name
                    chunks.append(part)
                    evaluated=grid[grid.group.eq(name)&grid.period.eq(evaluation)&grid.candidate_id.eq(p['candidate_id'])].iloc[0].to_dict()
                    choices.append(dict(selection_period=selection_period,evaluation_period=evaluation,grouping=grouping,
                        risk_capped=constrained,selected=p,evaluated=evaluated))
                combined=pd.concat(chunks,ignore_index=True)
                t=combined[combined.cohort.eq('signal')]
                c=combined[combined.cohort.eq('control')&combined.trade_key.isin(t.trade_key)]
                pairs=t[['trade_key','entry_time','candidate_r']].merge(c[['trade_key','candidate_r']],on='trade_key',suffixes=('_t','_c'),validate='one_to_one')
                assert len(t)==(21547 if evaluation=='full' else 14647)
                rr=t.candidate_r.to_numpy();delta=rr-t.net_r.to_numpy()
                block=pd.DataFrame(dict(week=t.entry_time.dt.strftime('%G-W%V').to_numpy(),delta=delta,n=1)).groupby('week').sum()
                rng=np.random.default_rng(result['config']['seed']);ix=rng.integers(len(block),size=(3000,len(block)))
                sims=block.delta.to_numpy()[ix].sum(axis=1)/block.n.to_numpy()[ix].sum(axis=1)
                paired_block=pd.DataFrame(dict(week=pairs.entry_time.dt.strftime('%G-W%V').to_numpy(),
                    delta=(pairs.candidate_r_t-pairs.candidate_r_c).to_numpy(),n=1)).groupby('week').sum()
                paired_ix=rng.integers(len(paired_block),size=(3000,len(paired_block)))
                paired_sims=paired_block.delta.to_numpy()[paired_ix].sum(axis=1)/paired_block.n.to_numpy()[paired_ix].sum(axis=1)
                comparison=dict(selection_period=selection_period,evaluation_period=evaluation,grouping=grouping,
                    risk_capped=constrained,**stat(rr),baseline_sum_r=float(t.net_r.sum()),delta_sum_r=float(delta.sum()),
                    filled_opportunities=int(t.filled.sum()),win_rate_filled=float((rr>0).sum()/t.filled.sum()) if t.filled.sum() else None,
                    matched=len(pairs),matched_target_mean_r=float(pairs.candidate_r_t.mean()),
                    matched_control_mean_r=float(pairs.candidate_r_c.mean()),
                    matched_excess_mean_r=float((pairs.candidate_r_t-pairs.candidate_r_c).mean()),
                    weekly_delta_mean_95=np.quantile(sims,[.025,.975]).tolist(),
                    matched_weekly_mean_95=np.quantile(paired_sims,[.025,.975]).tolist())
                comparisons.append(comparison)
                if grouping=='timeframe_side' and constrained:
                    t=t.assign(delta_r=delta)
                    for year,part in t.groupby(t.entry_time.dt.year):
                        details.append(dict(selection_period=selection_period,year=int(year),**stat(part.candidate_r.to_numpy()),baseline_sum_r=float(part.net_r.sum())))
    output=dict(comparisons=comparisons,choices=choices,yearly_risk_capped_per_side=details,
        definitions={'risk_capped':'Exclude uncapped reclaim. Limits preserve original quantity; reclaim_capped reduces remainder quantity so exposure to original absolute stop is at most original planned R.',
          'selection':'Full-history maxima and early-selected/later maps separately. Refinement was informed by full history; neither is blind validation.',
          'interval':'Week-block bootstrap for fixed selected map, without search adjustment.'})
    (ROOT/'comparisons.json').write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(comparisons,ensure_ascii=False))


if __name__=='__main__':run()
