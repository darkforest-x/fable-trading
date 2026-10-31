"""Common-period ETH calibration evaluation from saved VectorBT selections.

Native trade prices and costs are unchanged. Test-quarter returns never enter
selection of that quarter's threshold. Report calibration performance apart
from later outcomes, matched-random comparisons and weekly uncertainty.
Ranking remains within each frozen score model, never pooled across folds.
"""
from __future__ import annotations

import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_score_calibration_data import EXP,PRIOR,config,read,dump
from yoyo.evaluation.btc_eth_repeat_fit import metrics
from yoyo.evaluation.major_1618_v2_evaluate import policy_difference,diagnose,fold_week_permutation,holm

ARMS=['fresh_raw_zero','lagged_raw_zero','unscored','single_reward','bias_calibrated_zero','past_selected_threshold']


def main():
    cfg=config();data=read('scores.csv.gz');test=data[data.role.eq('test')&data.complete].copy()
    chosen=read('selected_trades.csv.gz');chosen['native_net_r']=chosen.net_r;chosen['net_r']=chosen.vbt_net_r
    decisions=json.loads((EXP/'decisions.json').read_text())
    receipts=json.loads((EXP/'fold_receipts.json').read_text())
    single=pd.read_csv(PRIOR/'ETH_predictions.csv.gz',float_precision='round_trip')
    single=single[single.arm.eq('single_reward')].set_index('trade_id')
    rows=[];details=[];final=[];years=[];sides=[];quarter_rows=[];concentration=[]
    for target,g in test.groupby('target'):
        for arm in ARMS:
            selection=chosen[chosen.target.eq(target)&chosen.arm.eq(arm)]
            stats=metrics(selection);lag=chosen[chosen.target.eq(target)&chosen.arm.eq('lagged_raw_zero')]
            fresh=chosen[chosen.target.eq(target)&chosen.arm.eq('fresh_raw_zero')]
            scoredata=g.copy();scoredata['score_kind']='net_r'
            if arm=='fresh_raw_zero':scoredata['score']=scoredata.fresh_score
            if arm=='single_reward':
                scoredata['score']=scoredata.trade_id.map(single.score);scoredata=scoredata.dropna(subset=['score'])
                scoredata['score_kind']='probability'
            if arm=='bias_calibrated_zero':
                adjustments={r['fold']:r['bias_delta_r'] for r in decisions if r['target']==target}
                scoredata['score']+=scoredata.fold.map(adjustments)
            diag=diagnose(scoredata) if arm!='unscored' else {}
            row=dict(target=target,arm=arm,candidate_n=len(g),candidate_weeks=int(g.week.nunique()),
                     **{k:v for k,v in stats.items() if not isinstance(v,(list,dict))},auc=diag.get('target_auc'),
                     baseline_lagged_net_r=float(lag.net_r.sum()),baseline_fresh_net_r=float(fresh.net_r.sum()))
            if arm=='single_reward':row['unavailable_candidates']=int((~g.single_reward_available).sum())
            rows.append(row)
            detail=dict(target=target,arm=arm,selected=stats,diagnostics=diag,
                        versus_lagged=policy_difference(selection,lag,g.week),versus_fresh=policy_difference(selection,fresh,g.week))
            details.append(detail)
            if arm in ['bias_calibrated_zero','past_selected_threshold']:
                entry=dict(detail,matched_week_p=stats.get('week_p'))
                entry['week_net_rank_permutation_p']=fold_week_permutation(scoredata,'net_r')
                final.append(entry)
            for year,part in selection.groupby(selection.week.str[:4]):
                years.append(dict(target=target,arm=arm,year=year,**metrics(part)))
            for side,part in selection.groupby('side'):
                sides.append(dict(target=target,arm=arm,side=int(side),**metrics(part)))
            for fold,part in g.groupby('fold'):
                trades=selection[selection.fold.eq(fold)]
                quarter_rows.append(dict(target=target,arm=arm,fold=fold,candidates=len(part),**metrics(trades)))
            w=selection.groupby('week').net_r.sum().sort_values(ascending=False)
            concentration.append(dict(target=target,arm=arm,net_r=float(selection.net_r.sum()),
                 net_without_best_week=float(selection.net_r.sum()-w.iloc[:1].sum()),
                 net_without_best_three_weeks=float(selection.net_r.sum()-w.iloc[:3].sum())))
    holm(final)
    for row in final:
        ci=row['selected'].get('net_bp_ci95',[None,None]);diff=row['versus_lagged'].get('delta_mean_week_ci95',[None,None])
        row['historical_evidence_gate']=bool(ci[0] is not None and ci[0]>0 and row['holm_p']<.01 and diff[0] is not None and diff[0]>0)
        row['production_eligible']=False
    bins=[];calibration_error=[]
    for rec in decisions:
        g=data[data.target.eq(rec['target'])&data.fold.eq(rec['fold'])]
        cal=g[g.role.eq('calibration')]
        edges=np.unique(np.quantile(cal.score,[.25,.5,.75],method='linear'))
        for role,part in [('calibration',cal),('test',g[g.role.eq('test')&g.complete])]:
            part=part.copy();part['bin']=np.searchsorted(edges,part.score,side='left')
            part['adjusted']=part.score+rec['bias_delta_r']
            for bucket,b in part.groupby('bin'):
                bins.append(dict(target=rec['target'],fold=rec['fold'],role=role,bin=int(bucket),n=len(b),
                    weeks=int(b.week.nunique()),mean_raw=float(b.score.mean()),mean_adjusted=float(b.adjusted.mean()),
                    mean_net_r=float(b.net_r.mean()),net_win_rate=float(b.net_r.gt(0).mean()),
                    lower_past_score=float(edges[bucket-1]) if bucket else None,
                    upper_past_score=float(edges[bucket]) if bucket<len(edges) else None))
            calibration_error.append(dict(target=rec['target'],fold=rec['fold'],role=role,n=len(part),
                raw_rmse=float(np.mean((part.score-part.net_r)**2)**.5),adjusted_rmse=float(np.mean((part.adjusted-part.net_r)**2)**.5),
                raw_mean_error=float((part.score-part.net_r).mean()),adjusted_mean_error=float((part.adjusted-part.net_r).mean())))
    pd.DataFrame(rows).to_csv(EXP/'metrics.csv',index=False)
    pd.DataFrame(quarter_rows).to_csv(EXP/'quarters.csv',index=False)
    pd.DataFrame(years).to_csv(EXP/'years.csv',index=False);pd.DataFrame(sides).to_csv(EXP/'sides.csv',index=False)
    pd.DataFrame(bins).to_csv(EXP/'past_defined_calibration_bins.csv',index=False)
    pd.DataFrame(calibration_error).to_csv(EXP/'calibration_error.csv',index=False)
    dump(EXP/'diagnostics.json',details)
    dump(EXP/'summary.json',dict(config=cfg,results=rows,policy_tests=final,concentration=concentration,decisions=decisions,
         test_coverage=dict(first_week=str(test.week.min()),last_week=str(test.week.max()),quarters=int(test.fold.nunique()),
                            events_per_target=int(test[test.target.eq('near')].event_id.nunique()),
                            weeks=int(test.week.nunique())),
         model_reuse=dict(models=21,refits=0,new_native_positions=0),
         single_reward_coverage='Opposite1618 has no eligible single-feature classifier in2025Q2. It abstains for38test candidates; missing coverage is explicit in metrics and receipts.',
         limitations=['Previously researched history; exploratory and not untouched confirmation.',
                      'Score scale held fixed by reusing priorquarter model; lagged raw0 is the direct calibration control.',
                      'Calibration windows have5-10 observed NY weeks; threshold selection may be unstable.',
                      'One dollar initial risk per trade; independent R,not cash account return.',
                      'Unchanged maker2bp/taker5bp; signal entry maker,matched random entry taker; extra slippage/funding omitted.',
                      'Targets evaluated separately; no adaptive target routing or test-winning policy selection.',
                      'Current incomplete week appears only in current_week.csv and never evaluation/calibration.'],
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(pd.DataFrame(rows)[['target','arm','n','net_r','net_bp','random_bp','baseline_lagged_net_r','baseline_fresh_net_r']].to_string(index=False),flush=True)


if __name__=='__main__':main()
