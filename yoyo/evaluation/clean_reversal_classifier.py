"""Single-objective ablation after six owner-confirmed clean reversal examples.

Use original 24 closed-OHLC features (signal and preceding96bars), original
future29bar labels and exactly saved quarterly fold membership. Only change
continuous-quality L2 regression to existing-label binary log loss. No feature
builder, model search, trade simulation or production change. Review metadata
is separate from features/weights; six confirmations never imply all are gold.
"""
from __future__ import annotations
import json
import math
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm
from lightgbm import LGBMClassifier, Booster
from yoyo.evaluation.clean_reversal_labels import ROOT, EXP as BASE, dump
from yoyo.evaluation.clean_reversal_models import COLUMNS, load, weights, percentile, metric, statistics

EXP=ROOT/'experiments/active/exp-clean-reversal-classifier-20261101-v1'
REVIEW=ROOT/'experiments/active/exp-clean-reversal-audit-20261101-v1/owner_confirmation_20261101.json'
PROB='predicted_clean_probability_uncalibrated'


def training_target(train,arm,rng):
    """All frozen positives get target1 regardless of quality magnitude or review."""
    target=train.clean_candidate.astype(int).copy()
    if arm=='shuffled_training_labels':
        groups=train.groupby([train.week_start.dt.tz_localize(None).dt.to_period('Q').astype(str),train.side]).groups
        for idx in groups.values():target.loc[idx]=rng.permutation(target.loc[idx].to_numpy())
    return target


def metric_view(d):
    """Temporary legacy-name adapter only; persisted probability is never quality."""
    return d.rename(columns={PROB:'predicted_quality'})


def paired_comparison(new,old):
    """Paired NY-week bootstrap of raw and matched-adjusted admission hit rates."""
    old=old.set_index('event_id').loc[new.event_id].reset_index()
    assert new.event_id.tolist()==old.event_id.tolist()
    assert np.array_equal(new.clean_candidate,old.clean_candidate)
    y=new.clean_candidate.to_numpy(float);a=new.top_decile.to_numpy(bool);b=old.top_decile.to_numpy(bool)
    assert a.sum()==b.sum()
    expected=new.groupby(['symbol','fold','vol_bucket']).clean_candidate.transform('mean').to_numpy()
    t=pd.DataFrame(dict(week=new.week,an=a,ay=a*y,adj_a=a*(y-expected),bn=b,by=b*y,adj_b=b*(y-expected)))
    per=t.groupby('week')[['an','ay','adj_a','bn','by','adj_b']].sum().to_numpy()
    def effect(v):return np.array([v[1]/v[0]-v[4]/v[3],v[2]/v[0]-v[5]/v[3]])
    observed=effect(per.sum(axis=0));rng=np.random.default_rng(20261101);boot=[]
    for _ in range(2000):
        v=per[rng.integers(0,len(per),len(per))].sum(axis=0)
        if v[0] and v[3]:boot.append(effect(v))
    ci=np.quantile(boot,[.025,.975],axis=0)
    return dict(top_selected_each=int(a.sum()),shared_selected=int((a&b).sum()),new_only=int((a&~b).sum()),old_only=int((b&~a).sum()),hit_rate_difference=float(observed[0]),hit_rate_difference_95ci=ci[:,0].tolist(),matched_lift_difference=float(observed[1]),matched_lift_difference_95ci=ci[:,1].tolist(),cluster='global NY week',bootstrap_replicates=2000)


def run():
    cfg=json.loads((EXP/'config.json').read_text());bcfg=json.loads((BASE/'config.json').read_text())
    assert cfg['lightgbm']==bcfg['lightgbm'] and cfg['arms']==bcfg['arms']
    d,threshold,initial=load();idx=d.set_index('event_id',drop=False)
    original=pd.read_csv(BASE/'walkforward_predictions.csv');receipts=json.loads((BASE/'folds.json').read_text())
    original_summary=json.loads((BASE/'summary.json').read_text())
    assert threshold==original_summary['candidate_quality_cutoff']
    confirmed=json.loads(REVIEW.read_text())['confirmed_positive_event_ids']
    assert len(confirmed)==len(set(confirmed))==6 and idx.loc[confirmed,'clean_candidate'].all()
    assert len(d)==2256 and int(d.in_original_gallery.sum())==644
    raw_inputs=[BASE/n for n in ['events.csv','features.csv','outcomes.csv','config.json','folds.json','walkforward_predictions.csv','summary.json']]+[REVIEW]
    stamps=[dict(path=str(p),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in raw_inputs]
    rng=np.random.default_rng(cfg['lightgbm']['random_state']);outputs=[];saved_folds=[];gains=[];max_error=0.
    for receipt in receipts:
        fold=receipt['fold'];arm=receipt['arm'];train=idx.loc[receipt['training_event_ids']].copy()
        baseline_test=original[original.fold.eq(fold)&original.arm.eq(arm)]
        test=idx.loc[baseline_test.event_id].copy()
        start=pd.Timestamp(fold[:4]+'-'+str(1+3*(int(fold[-1])-1))+'-01',tz='UTC')
        assert train.event_id.tolist()==d[(d.week_expiry<=start)&(d.label_dt<=start)].event_id.tolist()
        assert not set(train.week)&set(test.week) and not set(train.episode_id)&set(test.episode_id)
        assert train.label_dt.max()<=start<=test.week_start.min()
        cols=receipt['features'];assert cols==(['feature_rejection_wick'] if arm=='single_rejection_wick' else COLUMNS)
        y=training_target(train,arm,rng)
        model=LGBMClassifier(objective='binary',class_weight=None,**cfg['lightgbm'])
        model.fit(train[cols],y,sample_weight=weights(train))
        prediction=model.predict_proba(test[cols])[:,1];reference=model.predict_proba(train[cols])[:,1]
        folder=EXP/'models'/fold;folder.mkdir(parents=True,exist_ok=True);path=folder/(arm+'.txt');model.booster_.save_model(str(path))
        restored=Booster(model_file=str(path));again=restored.predict(test[cols],num_threads=1)
        max_error=max(max_error,float(np.max(np.abs(again-prediction))));assert np.allclose(again,prediction,atol=1e-12,rtol=0)
        assert restored.feature_name()==cols and restored.params['objective']=='binary'
        out=test.reset_index(drop=True);out['fold']=fold;out['arm']=arm;out[PROB]=prediction;out['model_score']=percentile(prediction,reference)
        out['vol_bucket']=np.searchsorted(receipt['vol_bucket_cutoffs'],test.tr_fraction.to_numpy(),side='right')
        assert np.array_equal(out.vol_bucket,baseline_test.vol_bucket)
        out['top_decile']=False;n=max(1,math.ceil(len(out)*.1))
        chosen=out.sort_values([PROB,'event_id'],ascending=[False,True]).index[:n];out.loc[chosen,'top_decile']=True
        out['score_at_least90']=out.model_score.ge(90);out['owner_confirmed_positive']=out.event_id.isin(confirmed)
        outputs.append(out);saved_folds.append(dict(receipt,objective='binary',positive_train=int(train.clean_candidate.sum()),model_path=str(path.relative_to(EXP))))
        if arm=='full':
            for feature,value in zip(cols,model.booster_.feature_importance(importance_type='gain')):gains.append(dict(fold=fold,feature=feature,gain=float(value)))
            print(fold,'train',len(train),'test',len(test),flush=True)
    scores=pd.concat(outputs,ignore_index=True);assert len(scores)==len(original)==5703
    scores.to_csv(EXP/'walkforward_predictions.csv',index=False);dump(EXP/'folds.json',saved_folds)
    pd.DataFrame(gains).to_csv(EXP/'feature_gain.csv',index=False)
    latest_schema=json.loads((BASE/'models/latest/schema.json').read_text());cutoff=pd.Timestamp(latest_schema['cutoff'])
    train=d[(d.week_expiry<=cutoff)&(d.label_dt<=cutoff)];assert len(train)==latest_schema['training_rows']
    latest=LGBMClassifier(objective='binary',class_weight=None,**cfg['lightgbm']);latest.fit(train[COLUMNS],train.clean_candidate.astype(int),sample_weight=weights(train))
    folder=EXP/'models/latest';folder.mkdir(parents=True,exist_ok=True);latest.booster_.save_model(str(folder/'research_only.txt'))
    ref=latest.predict_proba(train[COLUMNS])[:,1];reloaded=Booster(model_file=str(folder/'research_only.txt')).predict(train[COLUMNS],num_threads=1)
    assert np.allclose(ref,reloaded,rtol=0,atol=1e-12)
    dump(folder/'schema.json',dict(features=COLUMNS,objective='binary',training_rows=len(train),cutoff=str(cutoff),reference_predictions=np.sort(ref).tolist(),score_kind='past_training_prediction_percentile_not_probability',production_eligible=False))
    summary=dict(experiment_id=cfg['experiment_id'],single_variable=cfg['single_variable'],events=2256,original_gallery=644,initial_train_rows=len(initial),clean_candidate_cutoff=threshold,positive_labels=int(d.clean_candidate.sum()),owner_confirmed_positive=6,automatic_labels_changed=0,baseline_reused=True,arms={},years={},statistics={},baseline=dict(arms=original_summary['arms'],years=original_summary['years'],statistics=original_summary['statistics']),training_eligible=False,production_eligible=False)
    for arm,g in scores.groupby('arm'):
        summary['arms'][arm]=metric(metric_view(g));summary['years'][arm]={str(year):metric(metric_view(t)) for year,t in g.groupby(g.week_start.dt.year)}
        summary['statistics'][arm]=statistics(g)
    full=scores[scores.arm.eq('full')].reset_index(drop=True);old=original[original.arm.eq('full')].copy()
    summary['paired_objective_comparison']=paired_comparison(full,old)
    summary['sides']={side:metric(metric_view(g)) for side,g in full.groupby('side')};summary['gallery']=metric(metric_view(full[full.in_original_gallery]))
    joined=d.merge(full[['event_id','fold',PROB,'model_score']],on='event_id',how='left',validate='one_to_one')
    joined['owner_confirmed_positive']=joined.event_id.isin(confirmed)
    joined['label_provenance']=np.where(joined.owner_confirmed_positive,'owner_confirmed_20261101','automatic_unconfirmed')
    joined['score_basis']=np.where(joined.model_score.notna(),'walkforward_unseen_at_fit','initial_history_or_boundary_no_oos_score')
    joined=joined.merge(old[['event_id','model_score']].rename(columns={'model_score':'regression_model_score'}),on='event_id',how='left',validate='one_to_one')
    joined.to_csv(EXP/'all_labels_and_scores.csv',index=False)
    joined[joined.in_original_gallery].to_csv(EXP/'gallery_644_labels_and_scores.csv',index=False)
    joined[joined.owner_confirmed_positive].to_csv(EXP/'owner_six_scores.csv',index=False)
    comparison=[dict(model='original_regression',**original_summary['arms']['full'],**original_summary['statistics'])]
    comparison.extend(dict(model='binary_'+arm,**summary['arms'][arm],**summary['statistics'][arm]) for arm in cfg['arms'])
    pd.DataFrame(comparison).to_csv(EXP/'comparison.csv',index=False)
    for receipt in stamps:
        path=Path(receipt['path']);assert path.stat().st_size==receipt['bytes'] and path.stat().st_mtime_ns==receipt['mtime_ns']
    validation=dict(all48_fold_train_test_ids_equal_baseline=True,all_whole_week_episode_purges=True,all_volatility_bins_equal=True,original2256_labels_preserved=True,owner_confirmed_count=6,other_labels_not_owner_confirmed=2250,prediction_rows=5703,saved_models=49,saved_oos_prediction_max_error=max_error,latest_prediction_parity=True,features=COLUMNS,source_stamps_unchanged=True,no_new_market_data=True,no_trades=True,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'validation.json',validation);dump(EXP/'input_receipt.json',dict(inputs=stamps,python=sys.version,lightgbm=lightgbm.__version__,numpy=np.__version__,pandas=pd.__version__,code_commit=validation['code_commit']))
    dump(EXP/'summary.json',summary)
    print(pd.DataFrame(comparison)[['model','n','within_fold_auc_pair_weighted','top_n','top_clean_rate','matched_random_expected_rate','lift','conditional_permutation_p']].to_string(index=False),flush=True)
    print(json.dumps(summary['paired_objective_comparison'],indent=2),flush=True)


if __name__=='__main__':run()
