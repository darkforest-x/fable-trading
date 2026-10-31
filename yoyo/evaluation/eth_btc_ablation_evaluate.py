"""Paired ETH input ablations and complete high-score-tail membership audit.

High-score cohorts are retrospective within-quarter diagnostics. They never
become a trade gate. Baseline/full-context selections are reused, while new
admissions come from VectorBT. Weekly matched and baseline comparisons retain
all failed arms and both target families; no target or model is promoted.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_btc_ablation import EXP,SOURCE,ORIGINAL,config,read,dump
from yoyo.evaluation.btc_eth_repeat_fit import metrics
from yoyo.evaluation.major_1618_v2_evaluate import diagnose,fold_week_permutation,policy_difference,holm


def top_rows(data):
    """Same within-fold ceil10percent diagnostic as prior study; stable ties."""
    return pd.concat([g.nlargest(max(1,int(np.ceil(len(g)*.1))),'score') for _,g in data.groupby('fold')])


def tail_audit(pred):
    arms=['original_model','full_context']+config()['arms']
    d=pred[pred.target.eq('near')&pred.complete]
    originals=d[d.arm.eq('original_model')]
    original_top=top_rows(originals)
    ids=set(original_top.trade_id);records=[];summary=[];folds=[];members=[]
    for arm in arms:
        cohort=d[d.arm.eq(arm)];top=top_rows(cohort);now=set(top.trade_id)
        top=top.copy();top['original_top']=top.trade_id.isin(ids)
        top['tail_group']=np.where(top.original_top,'shared','promoted')
        members.append(top)
        shared=top[top.original_top];added=top[~top.original_top]
        removed=original_top[~original_top.trade_id.isin(now)]
        rec=dict(arm=arm,top=metrics(top),top_mean_score_r=float(top.score.mean()),
                 shared=metrics(shared),promoted=metrics(added),displaced=metrics(removed),
                 delta_top_total_r=float(top.net_r.sum()-original_top.net_r.sum()))
        assert abs(added.net_r.sum()-removed.net_r.sum()-rec['delta_top_total_r'])<1e-9
        summary.append(rec)
        for fold,g in top.groupby('fold'):
            folds.append(dict(arm=arm,fold=fold,mean_score_r=float(g.score.mean()),**metrics(g)))
        comparison=cohort.set_index('trade_id').copy()
        for trade_id in sorted(ids|now):
            r=comparison.loc[trade_id]
            records.append(dict(arm=arm,trade_id=trade_id,fold=r.fold,week=r.week,side=int(r.side),
                score=float(r.score),original_score=float(originals.set_index('trade_id').loc[trade_id].score),
                original_top=trade_id in ids,arm_top=trade_id in now,
                category='shared' if trade_id in ids&now else ('promoted' if trade_id in now else 'displaced'),
                net_r=float(r.net_r),net_bp=float(r.net_notional_bp),random_bp=float(r.random_bp),reason=int(r.reason)))
    pd.DataFrame(records).to_csv(EXP/'tail_membership.csv',index=False)
    pd.DataFrame(folds).to_csv(EXP/'tail_quarters.csv',index=False)
    pd.concat(members).to_csv(EXP/'tail_rows.csv.gz',index=False)
    dump(EXP/'tail_summary.json',dict(method='Within-fold top10percent, diagnostic only; no execution gate',arms=summary))
    return summary


def main():
    cfg=config();pred=read('predictions.csv.gz');pred=pred[pred.complete].copy()
    chosen=read('selected_trades.csv.gz');chosen['net_r']=chosen.vbt_net_r
    single=read('ETH_predictions.csv.gz',ORIGINAL);single=single[single.complete&single.arm.eq('single_reward')]
    rows=[];details=[];tests=[];years=[];quarters=[];sides=[];concentration=[]
    for target,cohort in pred[pred.arm.eq('original_model')].groupby('target'):
        base=chosen[chosen.target.eq(target)&chosen.arm.eq('original_model')]
        full=chosen[chosen.target.eq(target)&chosen.arm.eq('full_context')]
        for arm in ['original_model','full_context','unscored','single_reward']+cfg['arms']:
            sel=chosen[chosen.target.eq(target)&chosen.arm.eq(arm)];stats=metrics(sel)
            scoredata=pred[pred.target.eq(target)&pred.arm.eq(arm)]
            if arm=='single_reward':scoredata=single[single.target.eq(target)]
            diag=diagnose(scoredata) if not scoredata.empty else {}
            diff=policy_difference(sel,base,cohort.week);vsfull=policy_difference(sel,full,cohort.week)
            added=sel[~sel.trade_id.isin(base.trade_id)];removed=base[~base.trade_id.isin(sel.trade_id)]
            rows.append(dict(target=target,arm=arm,candidates=len(cohort),candidate_weeks=int(cohort.week.nunique()),
                **{k:v for k,v in stats.items() if not isinstance(v,(list,dict))},auc=diag.get('target_auc'),
                delta_original_r=diff['delta_total_r'],delta_full_r=vsfull['delta_total_r'],
                added_n=len(added),added_r=float(added.net_r.sum()),removed_n=len(removed),removed_r=float(removed.net_r.sum())))
            detail=dict(target=target,arm=arm,selected=stats,diagnostics=diag,versus_original=diff,versus_full=vsfull)
            if arm in cfg['arms']:
                detail['matched_week_p']=stats.get('week_p')
                detail['rank_permutation_p']=fold_week_permutation(scoredata,'net_r');tests.append(detail)
            details.append(detail)
            for year,g in sel.groupby(sel.week.str[:4]):years.append(dict(target=target,arm=arm,year=year,**metrics(g)))
            for side,g in sel.groupby('side'):sides.append(dict(target=target,arm=arm,side=int(side),**metrics(g)))
            for fold,g in cohort.groupby('fold'):quarters.append(dict(target=target,arm=arm,fold=fold,**metrics(sel[sel.fold.eq(fold)])))
            w=sel.groupby('week').net_r.sum().sort_values(ascending=False)
            concentration.append(dict(target=target,arm=arm,net_r=float(sel.net_r.sum()),
                without_best_week=float(sel.net_r.sum()-w.iloc[:1].sum()),without_best_three_weeks=float(sel.net_r.sum()-w.iloc[:3].sum())))
    holm(tests)
    comparisons=[]
    for r in tests:
        for name in ['original','full']:
            comparisons.append(dict(target=r['target'],arm=r['arm'],control=name,matched_week_p=r[f'versus_{name}']['improvement_week_p']))
    holm(comparisons)
    for r in tests:
        for name in ['original','full']:
            match=next(c for c in comparisons if c['target']==r['target'] and c['arm']==r['arm'] and c['control']==name)
            r[f'improvement_{name}_holm_p']=match['holm_p']
        ci=r['selected'].get('net_bp_ci95',[None,None]);delta=r['versus_original']['delta_mean_week_ci95']
        r['historical_evidence_gate']=bool(ci[0] is not None and ci[0]>0 and delta[0]>0 and r['holm_p']<.01 and r['improvement_original_holm_p']<.01)
        r['production_eligible']=False
    for name,records in [('metrics',rows),('years',years),('quarters',quarters),('sides',sides),('concentration',concentration)]:
        pd.DataFrame(records).to_csv(EXP/f'{name}.csv',index=False)
    tails=tail_audit(pred);dump(EXP/'diagnostics.json',details)
    coverage=json.loads((SOURCE/'summary.json').read_text())['coverage']
    dump(EXP/'summary.json',dict(config=cfg,coverage=coverage,results=rows,policy_tests=tests,tail=tails,
        model_receipt=json.loads((EXP/'fit_receipt.json').read_text()),engine_receipt=json.loads((EXP/'engine_receipt.json').read_text()),
        limitations=['Known history; input pairs selected after prior screening. No untouched validation.',
            'Residual depends on beta internally; column groups are not orthogonal economic information.',
            'Topdecile is a retrospective cohort diagnostic, never a live admission rule.',
            'Independent R, unchanged native maker/taker costs, no added slippage/funding.',
            'Current incomplete week excluded from all training and evaluation.'],
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(pd.DataFrame(rows)[['target','arm','n','net_r','net_bp','random_bp','delta_original_r','delta_full_r']].to_string(index=False),flush=True)


if __name__=='__main__':main()
