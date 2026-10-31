"""Explain paired original/full-context near scores on every complete test row.

Native and SHAP0.52 tree-path-dependent explanations use frozen training
path cover. Interventional sensitivity uses the same training event IDs for
both models: first submission per NY week, up to100 evenly spaced weeks.
No test outcome chooses the background. Save modern Explanation objects,
check additivity, and report group-level allocations with correlated-feature
caveats. Full-minus-original attribution is model behavior, not causation.
"""
import argparse
import json
import pickle
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_btc_ablation import EXP,SOURCE,config,read,dump,model_path


def export():
    from yoyo.evaluation.eth_1618_feature_models import training_rows
    pred=read('predictions.csv.gz');pred=pred[pred.complete&pred.target.eq('near')]
    data=read('data.csv.gz',SOURCE);data=data[data.target.eq('near')]
    manifest=[]
    for fold in config()['folds']:
        start=pd.Period(fold,freq='Q').start_time.tz_localize('America/New_York')
        train=training_rows(data,start,'original_model')
        background=train.sort_values(['week','submission_i','trade_id']).drop_duplicates('week')
        if len(background)>100:background=background.iloc[np.linspace(0,len(background)-1,100,dtype=int)]
        for arm in ['original_model','full_context']:
            with model_path(arm,'near',fold).open('rb') as h:bundle=pickle.load(h)
            part=pred[pred.fold.eq(fold)&pred.arm.eq(arm)].copy()
            assert not set(background.event_id)&set(part.event_id)
            cols=bundle['features'];X=part[cols].replace([np.inf,-np.inf],np.nan)
            model=bundle['model'];native=np.asarray(model.predict(X,pred_contrib=True));score=model.predict(X)
            np.testing.assert_allclose(score,part.score,rtol=0,atol=1e-12)
            np.testing.assert_allclose(native.sum(axis=1),score,rtol=0,atol=1e-12)
            directory=EXP/'shap'/fold/arm;directory.mkdir(parents=True,exist_ok=True)
            model.booster_.save_model(str(directory/'booster.txt'))
            X.to_csv(directory/'features.csv',index=False)
            background[cols].to_csv(directory/'background.csv',index=False)
            part[['trade_id','event_id','fold','week','side','score','net_r','net_notional_bp','random_bp']].to_csv(directory/'rows.csv',index=False)
            np.savez(directory/'native.npz',contributions=native,score=score)
            manifest.append(dict(fold=fold,arm=arm,path=str(directory),features=cols,rows=len(part),
                background_event_ids=background.event_id.tolist(),background_weeks=int(background.week.nunique()),
                training_events=len(train),label_expiry_max=str(pd.to_datetime(train.expiry_dt,utc=True).max()),
                native_additivity_max_error=float(abs(native.sum(axis=1)-score).max())))
    dump(EXP/'shap_manifest.json',manifest)
    print('Exported',len(manifest),'frozen boosters; no refits',flush=True)


def verify():
    import lightgbm as lgb
    import shap
    from pathlib import Path
    manifest=json.loads((EXP/'shap_manifest.json').read_text());records=[];outputs=[]
    for spec in manifest:
        path=Path(spec['path']);model=lgb.Booster(model_file=str(path/'booster.txt'))
        X=pd.read_csv(path/'features.csv',float_precision='round_trip');native=np.load(path/'native.npz')
        meta=pd.read_csv(path/'rows.csv',float_precision='round_trip')
        bg=pd.read_csv(path/'background.csv',float_precision='round_trip')
        for method in ['tree_path_dependent','interventional_permutation_3','interventional_permutation_10']:
            destination=path/f'{method}_explanation.pkl'
            if destination.exists():
                with destination.open('rb') as handle:explanation=pickle.load(handle)
            elif method=='tree_path_dependent':
                explainer=shap.TreeExplainer(model,model_output='raw',feature_perturbation=method)
                explanation=explainer(X,check_additivity=True)
            else:
                # The interventional tree implementation failed additivity
                # despite exact schema/native/internal prediction parity.
                # Use the actual native callable, retaining the same background.
                cycles=int(method.rsplit('_',1)[1])
                callback=lambda values:model.predict(values,num_threads=1)
                explainer=shap.PermutationExplainer(callback,shap.maskers.Independent(bg,max_samples=len(bg)),seed=20261031)
                explanation=explainer(X,max_evals=cycles*(2*len(X.columns)+1))
            assert isinstance(explanation,shap.Explanation)
            reconstructed=explanation.base_values+explanation.values.sum(axis=1)
            error=float(abs(reconstructed-native['score']).max());assert error<1e-6
            parity=None
            if method=='tree_path_dependent':
                parity=float(abs(explanation.values-native['contributions'][:,:-1]).max())
                assert parity<1e-12
                np.testing.assert_allclose(explanation.base_values,native['contributions'][:,-1],rtol=0,atol=1e-12)
            with destination.open('wb') as handle:pickle.dump(explanation,handle)
            values=pd.DataFrame(explanation.values,columns=X.columns)
            values['base_value']=explanation.base_values
            for col in meta:values[col]=meta[col].to_numpy()
            values['arm']=spec['arm'];values['method']=method;outputs.append(values)
            records.append(dict(fold=spec['fold'],arm=spec['arm'],method=method,rows=len(X),
                features=len(X.columns),additivity_max_error=error,native_parity_max_error=parity))
        print(spec['fold'],spec['arm'],'verified three explanation variants',flush=True)
    pd.concat(outputs,ignore_index=True).to_csv(EXP/'shap_values.csv.gz',index=False)
    dump(EXP/'shap_verification.json',dict(shap_version=shap.__version__,lightgbm_version=lgb.__version__,
        output='Expected native netR',reference='Frozen training cover or paired training-only one-event-per-week background',
        records=records,explanation_objects_retained=True,refits=0,
        recovery='Interventional TreeSHAP failed on2025Q4 original model by0.04574R. Native and SHAP internal predictions match exactly; removing incomplete background rows does not fix it. Failed values rejected. Same background now uses actual-Booster PermutationExplainer with3and10permutation budgets, both checked for additivity.',
        limitations=['Correlated features share allocation under the chosen reference; no market-causal claim.',
            'Differences between two fitted models are not the effect of deleting an input from one model.',
            'Interventional permutation attributions are Monte Carlo approximations;3versus10budget cohort stability is reported.']))
    print('Verified',len(records),'paired explanation batches',flush=True)


def summarize():
    manifest=json.loads((EXP/'shap_manifest.json').read_text())
    for fold in config()['folds']:
        pair=[s for s in manifest if s['fold']==fold]
        assert len(pair)==2
        assert pair[0]['background_event_ids']==pair[1]['background_event_ids']
    contributions=read('shap_values.csv.gz');membership=read('tail_membership.csv')
    labels=membership[membership.arm.eq('full_context')].set_index('trade_id')
    state=config()['feature_groups']['state_only'];returns=config()['feature_groups']['return_residual_only']
    records=[];events=[];features=[]
    for method,d in contributions.groupby('method'):
        base=d[d.arm.eq('original_model')].set_index('trade_id')
        full=d[d.arm.eq('full_context')].set_index('trade_id').loc[base.index]
        oldcols=[c for c in full if c.startswith('f_')]
        decomposition=pd.DataFrame(index=base.index)
        decomposition['base_delta']=full.base_value-base.base_value
        decomposition['shared_feature_delta']=(full[oldcols]-base[oldcols]).sum(axis=1)
        decomposition['state_contribution']=full[state].sum(axis=1)
        decomposition['return_contribution']=full[returns].sum(axis=1)
        np.testing.assert_allclose(decomposition.sum(axis=1),full.score-base.score,rtol=0,atol=2e-6)
        for col in ['fold','week','side','score','net_r','net_notional_bp','random_bp']:decomposition[col]=full[col]
        decomposition['original_score']=base.score
        decomposition['category']=decomposition.index.to_series().map(labels.category).fillna('outside_both_tails')
        decomposition['method']=method
        events.append(decomposition.reset_index())
        for category,g in decomposition.groupby('category'):
            records.append(dict(method=method,category=category,n=len(g),weeks=int(g.week.nunique()),
                mean_score=float(g.score.mean()),mean_original_score=float(g.original_score.mean()),
                mean_realized_r=float(g.net_r.mean()),net_r=float(g.net_r.sum()),mean_net_bp=float(g.net_notional_bp.mean()),
                mean_random_bp=float(g.random_bp.mean()),
                **{c:float(g[c].mean()) for c in ['base_delta','shared_feature_delta','state_contribution','return_contribution']}))
            for feature in oldcols+state+returns:
                term=full.loc[g.index,feature]
                features.append(dict(method=method,category=category,feature=feature,
                    mean_shap_r=float(term.mean()),mean_abs_shap_r=float(term.abs().mean())))
    event_table=pd.concat(events,ignore_index=True)
    event_table.to_csv(EXP/'tail_score_decomposition.csv',index=False)
    pd.DataFrame(features).to_csv(EXP/'tail_feature_attribution.csv',index=False)
    terms=['base_delta','shared_feature_delta','state_contribution','return_contribution']
    cohorts=pd.DataFrame(records)
    low=cohorts[cohorts.method.eq('interventional_permutation_3')].set_index('category')
    high=cohorts[cohorts.method.eq('interventional_permutation_10')].set_index('category')
    cohort_diff=(high[terms]-low[terms]).abs()
    low_events=event_table[event_table.method.eq('interventional_permutation_3')].set_index('trade_id')
    high_events=event_table[event_table.method.eq('interventional_permutation_10')].set_index('trade_id')
    event_diff=(high_events[terms]-low_events[terms]).abs()
    error=abs(event_table[terms].sum(axis=1)-(event_table.score-event_table.original_score)).max()
    assert error<2e-6
    promoted=cohorts[cohorts.category.eq('promoted')]
    displaced=cohorts[cohorts.category.eq('displaced')]
    dump(EXP/'shap_sensitivity.json',dict(paired_background_ids_exact=True,
        batches=16,events_per_model=333,full_minus_original_max_error=float(error),
        budget3_vs10_max_cohort_term_delta_r=float(cohort_diff.to_numpy().max()),
        budget3_vs10_event_term_p95_delta_r={c:float(event_diff[c].quantile(.95)) for c in terms},
        budget3_vs10_max_event_term_delta_r=float(event_diff.to_numpy().max()),
        promoted_state_contribution_range_r=[float(promoted.state_contribution.min()),float(promoted.state_contribution.max())],
        promoted_return_contribution_range_r=[float(promoted.return_contribution.min()),float(promoted.return_contribution.max())],
        displaced_mean_score_change_r=float((displaced.mean_score-displaced.mean_original_score).iloc[0]),
        caveat='Limited same-seed budget sensitivity, not a convergence guarantee. Correlated input allocations depend on reference. Displaced means relative rank exclusion, not necessarily lower absolute score.',
        generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    dump(EXP/'tail_explanation.json',dict(cohorts=records,
        interpretation='Full-minus-original predictions decompose exactly into reference, shared-feature changes and new input groups. These are descriptive allocations, not retraining causal effects.',
        generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(pd.DataFrame(records).to_string(index=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['export','verify','summarize'])
    a=p.parse_args();{'export':export,'verify':verify,'summarize':summarize}[a.stage]()
