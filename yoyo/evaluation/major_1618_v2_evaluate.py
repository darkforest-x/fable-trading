"""Evaluate fixed model stages using cached native trades, never replay prices.

Each model is compared to unscored entries over its exact eligible test weeks.
Sequential ablations additionally use common event coverage. NY-week blocks
retain correlated touches; independent risk units are not funded-account ROI.
Final candidate and target families were frozen before the first model fit.
"""
from __future__ import annotations

import json
import subprocess
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,brier_score_loss,mean_squared_error
from yoyo.evaluation.btc_eth_repeat_data import dump
from yoyo.evaluation.btc_eth_repeat_fit import no_overlap,metrics,permutation_rank
from yoyo.evaluation.major_1618_v2_data import EXP,OLD,config,read
from yoyo.evaluation.major_1618_v2_models import FINAL


def predictions():
    items=[read(f'{s}_predictions.csv.gz') for s in config()['symbols']]
    old=pd.read_csv(OLD/'entry_predictions.csv.gz',float_precision='round_trip')
    old['score']=old.pop('p');old['admit']=old.gate;old['arm']='old_pooled';old['score_kind']='probability'
    items.append(old)
    return pd.concat(items,ignore_index=True)


def policy_difference(chosen,base,weeks):
    ix=pd.Index(sorted(set(weeks)))
    a=chosen.groupby('week').net_r.sum().reindex(ix,fill_value=0)
    b=base.groupby('week').net_r.sum().reindex(ix,fill_value=0)
    delta=(a-b).to_numpy()
    if len(delta)==0:return {}
    rng=np.random.default_rng(config()['seed'])
    boot=delta[rng.integers(0,len(delta),size=(2000,len(delta)))].mean(axis=1)
    null=(rng.choice([-1.,1.],size=(10000,len(delta)))*delta).sum(axis=1)
    return dict(delta_total_r=float(delta.sum()),delta_mean_week_r=float(delta.mean()),
                delta_mean_week_ci95=np.quantile(boot,[.025,.975]).tolist(),
                improvement_week_p=float((1+int((null>=delta.sum()).sum()))/10001))


def diagnose(data):
    if data.empty:return {}
    out=dict(candidates=len(data),candidate_weeks=int(data.week.nunique()),
             first_week=str(data.week.min()),last_week=str(data.week.max()),
             target_auc=float(roc_auc_score(data.label,data.score)) if data.label.nunique()==2 else None,
             profit_auc=float(roc_auc_score(data.net_r>0,data.score)) if (data.net_r>0).nunique()==2 else None)
    if data.score_kind.eq('probability').all():
        out['brier']=float(brier_score_loss(data.label,data.score))
    else:
        out['net_r_rmse']=float(mean_squared_error(data.net_r,data.score)**.5)
    top=data.nlargest(max(1,int(np.ceil(len(data)*.1))),'score')
    out['retrospective_top_decile']=metrics(top)
    return out


def holm(values):
    # All nine final asset/target comparisons belong to one declared family.
    order=sorted(range(len(values)),key=lambda i:values[i]['matched_week_p'] if values[i]['matched_week_p'] is not None else 1.)
    last=0.
    for rank,i in enumerate(order):
        p=values[i]['matched_week_p'] if values[i]['matched_week_p'] is not None else 1.
        last=max(last,min(1.,(len(values)-rank)*p));values[i]['holm_p']=last


def main():
    allpred=predictions();data=allpred[allpred.complete].copy()
    # Saved native outcome column names must not accidentally activate old p-only metrics.
    if 'p' in data:data=data.drop(columns='p')
    allrows=[];diagnostics=[];selected=[];final=[]
    for (symbol,target,arm),g in data.groupby(['symbol','target','arm']):
        assert g.trade_id.is_unique
        chosen=no_overlap(g,g.admit)
        base=no_overlap(g)
        score=metrics(chosen);baseline=metrics(base);diag=diagnose(g)
        row=dict(symbol=symbol,target=target,arm=arm,**{k:v for k,v in score.items() if not isinstance(v,(dict,list))},
                 candidate_n=len(g),test_weeks=int(g.week.nunique()),first_week=str(g.week.min()),last_week=str(g.week.max()),
                 baseline_n=len(base),baseline_net_r=baseline.get('net_r'),baseline_net_bp=baseline.get('net_bp'),
                 auc=diag.get('target_auc'),**policy_difference(chosen,base,g.week))
        allrows.append(row)
        diagnostics.append(dict(symbol=symbol,target=target,arm=arm,**diag,selected=score,baseline=baseline))
        selected.append(chosen)
        if arm==FINAL:
            ranked=g.copy();ranked['p']=ranked.score
            p=permutation_rank(ranked)
            row['week_rank_permutation_p']=p
            final.append(dict(symbol=symbol,target=target,arm=arm,matched_week_p=score.get('week_p'),
                              candidate_n=len(g),test_weeks=int(g.week.nunique()),selected=score,baseline=baseline,
                              diagnostics=diag,week_rank_permutation_p=p,
                              policy_difference=policy_difference(chosen,base,g.week)))
    holm(final)
    for r in final:
        ci=r['selected'].get('net_bp_ci95',[None,None])
        top=r['diagnostics']['retrospective_top_decile']
        r['historical_evidence_gate']=bool(ci[0] is not None and ci[0]>0 and r['holm_p']<.01 and
            r['week_rank_permutation_p'] is not None and r['week_rank_permutation_p']<.01 and top.get('net_bp',-1)>0)
        r['production_eligible']=False
    metric_frame=pd.DataFrame(allrows)
    metric_frame.to_csv(EXP/'metrics.csv',index=False)
    chosen=pd.concat(selected,ignore_index=True)
    chosen.to_csv(EXP/'selected_trades.csv.gz',index=False)
    years=chosen.assign(year=chosen.week.str[:4]).groupby(['symbol','target','arm','year']).agg(
        n=('trade_id','size'),net_r=('net_r','sum'),mean_net_bp=('net_notional_bp','mean')).reset_index()
    years.to_csv(EXP/'years.csv',index=False)
    sides=chosen.groupby(['symbol','target','arm','side']).agg(n=('trade_id','size'),net_r=('net_r','sum'),mean_net_bp=('net_notional_bp','mean')).reset_index()
    sides.to_csv(EXP/'sides.csv',index=False)
    pairs=[('old_pooled','asset_lr_cal'),('asset_lr_cal','asset_lr_raw'),('asset_lr_raw','fresh_lr_raw'),
           ('fresh_lr_raw','fresh_tree_class'),('fresh_tree_class','fresh_tree_net'),('fresh_tree_net',FINAL)]
    comparisons=[]
    for symbol in config()['symbols']:
        source=data[data.symbol.eq(symbol)&data.target.eq('near')]
        for before,after in pairs:
            a=source[source.arm.eq(before)];b=source[source.arm.eq(after)]
            shared=set(a.trade_id)&set(b.trade_id)
            a=a[a.trade_id.isin(shared)];b=b[b.trade_id.isin(shared)]
            if a.empty:continue
            aa=no_overlap(a,a.admit);bb=no_overlap(b,b.admit)
            comparisons.append(dict(symbol=symbol,before=before,after=after,common_candidates=len(shared),
                                    before_n=len(aa),after_n=len(bb),before_net_r=float(aa.net_r.sum()),after_net_r=float(bb.net_r.sum()),
                                    **policy_difference(bb,aa,a.week)))
    pd.DataFrame(comparisons).to_csv(EXP/'sequential_comparisons.csv',index=False)
    current=allpred[~allpred.complete & allpred.arm.isin([FINAL,'old_pooled'])]
    current[[c for c in ['symbol','target','arm','event_id','week','side','ordinal','touch_open','score_kind','score','break_even_p','admit','reason','net_r'] if c in current]].to_csv(EXP/'current_week.csv',index=False)
    concentration=[]
    for (symbol,target),g in chosen[chosen.arm.eq(FINAL)].groupby(['symbol','target']):
        w=g.groupby('week').net_r.sum().sort_values(ascending=False)
        concentration.append(dict(symbol=symbol,target=target,best_week_r=float(w.iloc[0]),
                                  net_excluding_best_week=float(g.net_r.sum()-w.iloc[0]),
                                  net_excluding_best_three=float(g.net_r.sum()-w.iloc[:3].sum())))
    dump(EXP/'diagnostics.json',diagnostics)
    dump(EXP/'summary.json',dict(config=config(),final_candidates=final,sequential_comparisons=comparisons,
         concentration=concentration,latest_models=[json.loads((EXP/f'{s}_fit_complete.json').read_text())['latest_models'] for s in config()['symbols']],
         model_artifacts=sum(json.loads((EXP/f'{s}_fit_complete.json').read_text())['models_fitted'] for s in config()['symbols']),
         current_week=current[['symbol','target','arm','event_id','ordinal','side','score','admit','reason','net_r']].to_dict('records'),
         limitations=['Previously researched history; exploratory,not untouched confirmation.',
                      'Only pre-touch causal5m features; cannot use the later rejection wick.',
                      'Independent initial risk units,not compounded funded-account ROI.',
                      'Maker2bp/taker5bp perfill; funding and extra slippage excluded.',
                      'Matched control entries are taker markets,signal entries maker limits.',
                      'XAU history begins2025-04,so its evaluable model period is shorter.',
                      'Targets evaluated separately; no test-chosen adaptive target routing.'],
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(metric_frame[metric_frame.target.eq('near')][['symbol','arm','n','net_r','net_bp','random_bp','auc','baseline_net_r']].to_string(index=False),flush=True)
    print('FINAL ALL TARGETS')
    print(metric_frame[metric_frame.arm.eq(FINAL)][['symbol','target','n','net_r','net_bp','random_bp','auc','baseline_net_r']].to_string(index=False),flush=True)


if __name__=='__main__':
    main()
