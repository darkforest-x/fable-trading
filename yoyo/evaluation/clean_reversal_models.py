"""Chronological exploratory scoring of the separately stored reversal labels.

Features end at each signal close. Training targets may read their29futurebars,
but entire NYweeks and label_end are purged before each test quarter. Reuse
fixed existing small LightGBM settings;no tuning,orders or economic claims.
Automatic clean candidates use a fixed initial-training quality percentile.
OOS percentiles are relative to past training predictions,not win probabilities.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr
from yoyo.evaluation.clean_reversal_labels import EXP,FEATURES,config,dump

COLUMNS=['feature_'+f for f in FEATURES]


def load():
    e=pd.read_csv(EXP/'events.csv');f=pd.read_csv(EXP/'features.csv');y=pd.read_csv(EXP/'outcomes.csv')
    d=e.merge(f,on='event_id',validate='one_to_one').merge(y,on='event_id',validate='one_to_one')
    d['week_start']=pd.to_datetime(d.week).dt.tz_localize('America/New_York').dt.tz_convert('UTC')
    d['week_expiry']=(pd.to_datetime(d.week)+pd.Timedelta(days=7)).dt.tz_localize('America/New_York').dt.tz_convert('UTC')
    d['label_dt']=pd.to_datetime(d.label_end,utc=True)
    initial=d[(d.week_expiry<=pd.Timestamp('2023-01-01',tz='UTC'))&(d.label_dt<=pd.Timestamp('2023-01-01',tz='UTC'))]
    assert len(initial)>=config()['minimum_history']['rows']
    threshold=float(initial.quality.quantile(.8));assert threshold>0
    d['clean_candidate']=d.quality.ge(threshold)&d.quality.gt(0)
    d['outcome_group']=np.where(d.clean_candidate,'clean_candidate',np.where(d.net_progress_tr.le(0),'reverse_failed','ambiguous'))
    d['tr_fraction']=d.tr_mean14/d.signal_close_price
    return d,threshold,initial


def weights(d):
    symbols=d.groupby('week').symbol.transform('nunique')
    episodes=d.groupby(['week','symbol']).episode_id.transform('nunique')
    siblings=d.groupby('episode_id').event_id.transform('size')
    w=1./(symbols*episodes*siblings);return w/w.mean()


def percentile(values,reference):
    reference=np.sort(reference)
    return 50.*(np.searchsorted(reference,values,side='left')+np.searchsorted(reference,values,side='right'))/len(reference)


def train_all(d):
    rows=[];receipts=[];gains=[];rng=np.random.default_rng(config()['lightgbm']['random_state'])
    for start in pd.date_range('2023-01-01','2026-10-01',freq='QS',tz='UTC'):
        end=start+pd.DateOffset(months=3);fold=f'{start.year}Q{start.quarter}'
        train=d[(d.week_expiry<=start)&(d.label_dt<=start)].copy()
        test=d[(d.week_start>=start)&(d.week_start<end)].copy()
        if len(train)<config()['minimum_history']['rows'] or train.week.nunique()<config()['minimum_history']['weeks'] or test.empty:continue
        assert not set(train.week)&set(test.week)
        assert not set(train.episode_id)&set(test.episode_id)
        assert train.label_dt.max()<=start<=test.week_start.min()
        folder=EXP/'models'/fold;folder.mkdir(parents=True,exist_ok=True)
        volcut=train.tr_fraction.quantile([1/3,2/3]).to_numpy()
        for arm in config()['arms']:
            cols=['feature_rejection_wick'] if arm=='single_rejection_wick' else COLUMNS
            target=train.quality.copy()
            if arm=='shuffled_training_labels':
                groups=train.groupby([train.week_start.dt.tz_localize(None).dt.to_period('Q').astype(str),train.side]).groups
                for idx in groups.values():target.loc[idx]=rng.permutation(target.loc[idx].to_numpy())
            model=LGBMRegressor(**config()['lightgbm']);model.fit(train[cols],target,sample_weight=weights(train))
            raw=model.predict(test[cols]);ref=model.predict(train[cols])
            model.booster_.save_model(str(folder/(arm+'.txt')))
            out=test.copy();out['fold']=fold;out['arm']=arm;out['predicted_quality']=raw;out['model_score']=percentile(raw,ref)
            out['vol_bucket']=np.searchsorted(volcut,test.tr_fraction.to_numpy(),side='right')
            # Top decile is a retrospective ranking diagnostic,not a trade policy.
            out['top_decile']=False;n=max(1,math.ceil(len(out)*.1))
            top=out.sort_values(['predicted_quality','event_id'],ascending=[False,True]).index[:n];out.loc[top,'top_decile']=True
            out['score_at_least90']=out.model_score.ge(90)
            if arm=='full':
                contrib=model.predict(test[cols],pred_contrib=True)
                assert np.allclose(contrib.sum(axis=1),raw,atol=1e-8,rtol=0)
                for j in range(3):
                    ranks=np.argsort(-np.abs(contrib[:,:-1]),axis=1)[:,j]
                    out[f'explanation_feature{j+1}']=[cols[k] for k in ranks]
                    out[f'explanation_delta{j+1}']=contrib[np.arange(len(test)),ranks]
                for feature,value in zip(cols,model.booster_.feature_importance(importance_type='gain')):gains.append(dict(fold=fold,feature=feature,gain=float(value)))
            rows.append(out)
            receipts.append(dict(fold=fold,arm=arm,train_rows=len(train),train_weeks=int(train.week.nunique()),test_rows=len(test),train_last_label=str(train.label_dt.max()),test_first_week=str(test.week_start.min()),training_event_ids=train.event_id.tolist(),features=cols,vol_bucket_cutoffs=volcut.tolist()))
        print(fold,'train',len(train),'test',len(test),flush=True)
    scores=pd.concat(rows,ignore_index=True);scores.to_csv(EXP/'walkforward_predictions.csv',index=False)
    dump(EXP/'folds.json',receipts);pd.DataFrame(gains).to_csv(EXP/'feature_gain.csv',index=False)
    # Save a final research-only model on complete past weeks; not used for OOS metrics.
    cutoff=pd.Timestamp('2026-10-31T15:00:00Z')
    tr=d[(d.week_expiry<=cutoff)&(d.label_dt<=cutoff)]
    latest=LGBMRegressor(**config()['lightgbm']);latest.fit(tr[COLUMNS],tr.quality,sample_weight=weights(tr))
    (EXP/'models/latest').mkdir(exist_ok=True)
    latest.booster_.save_model(str(EXP/'models/latest/research_only.txt'))
    dump(EXP/'models/latest/schema.json',dict(features=COLUMNS,training_rows=len(tr),cutoff=str(cutoff),reference_predictions=np.sort(latest.predict(tr[COLUMNS])).tolist(),score_kind='past_training_prediction_percentile_not_probability',production_eligible=False))
    return scores


def metric(d):
    selected=d[d.top_decile];p90=d[d.score_at_least90]
    rate=float(d.clean_candidate.mean());sr=float(selected.clean_candidate.mean()) if len(selected) else None
    return dict(n=len(d),clean_candidates=int(d.clean_candidate.sum()),clean_rate=rate,auc=float(roc_auc_score(d.clean_candidate,d.predicted_quality)) if d.clean_candidate.nunique()==2 else None,spearman=float(spearmanr(d.quality,d.predicted_quality).statistic) if d.predicted_quality.nunique()>1 else None,mean_quality=float(d.quality.mean()),median_progress_tr=float(d.net_progress_tr.median()),top_n=len(selected),top_clean_rate=sr,top_mean_quality=float(selected.quality.mean()) if len(selected) else None,top_median_progress_tr=float(selected.net_progress_tr.median()) if len(selected) else None,top_median_drawdown_tr=float(selected.max_close_drawdown_tr.median()) if len(selected) else None,score90_n=len(p90),score90_clean_rate=float(p90.clean_candidate.mean()) if len(p90) else None)


def statistics(full):
    """Conditional random-candidate null;week-cluster uncertainty,not tradePnL."""
    rng=np.random.default_rng(20261101);groups=list(full.groupby(['symbol','fold','vol_bucket']).indices.values())
    y=full.clean_candidate.to_numpy(dtype=float);quality=full.quality.to_numpy();chosen=full.top_decile.to_numpy(dtype=bool)
    expected=np.zeros(len(full));expected_quality=np.zeros(len(full))
    for idx in groups:expected[idx]=y[idx].mean();expected_quality[idx]=quality[idx].mean()
    lift=float((y[chosen]-expected[chosen]).mean());qlift=float((quality[chosen]-expected_quality[chosen]).mean())
    null=[]
    for _ in range(2000):
        picked=[]
        for idx in groups:
            k=int(chosen[idx].sum())
            if k:picked.extend(rng.choice(idx,size=k,replace=False))
        null.append(float(y[picked].mean()))
    observed=float(y[chosen].mean());p=(1+int((np.array(null)>=observed).sum()))/2001
    tmp=pd.DataFrame(dict(week=full.week,chosen=chosen,diff=(y-expected)*chosen,qdiff=(quality-expected_quality)*chosen))
    per=tmp.groupby('week')[['chosen','diff','qdiff']].sum().to_numpy();boot=[];qboot=[]
    for _ in range(2000):
        a=per[rng.integers(0,len(per),len(per))].sum(axis=0)
        if a[0]:boot.append(a[1]/a[0]);qboot.append(a[2]/a[0])
    return dict(primary='full_model_topdecile_clean_candidate_rate',observed_rate=observed,matched_random_expected_rate=float(expected[chosen].mean()),lift=lift,lift_95ci=np.quantile(boot,[.025,.975]).tolist(),conditional_permutation_p=p,quality_lift=qlift,quality_lift_95ci=np.quantile(qboot,[.025,.975]).tolist(),selected=int(chosen.sum()),selected_with_alternative_controls=sum(int(chosen[idx].sum()) for idx in groups if len(idx)>int(chosen[idx].sum())),null='Same symbol x OOSquarter x train-defined volatilitybucket candidate selection;includes self under the exchangeability null;no random-time trades.')


def run():
    d,threshold,initial=load();scores=train_all(d)
    summary=dict(events_with_features_and_labels=len(d),original_gallery=int(d.in_original_gallery.sum()),initial_train_rows=len(initial),candidate_quality_cutoff=threshold,total_clean_candidates=int(d.clean_candidate.sum()),gallery_clean_candidates=int((d.clean_candidate&d.in_original_gallery).sum()),groups=d.outcome_group.value_counts().to_dict(),arms={},years={},sides={},gallery={},statistics={},training_eligible=False,production_eligible=False)
    for arm,group in scores.groupby('arm'):
        summary['arms'][arm]=metric(group)
        summary['years'][arm]={str(year):metric(g) for year,g in group.groupby(group.week_start.dt.year)}
    full=scores[scores.arm.eq('full')].copy();summary['statistics']=statistics(full)
    summary['sides']={side:metric(g) for side,g in full.groupby('side')}
    summary['gallery']=metric(full[full.in_original_gallery])
    review=d.merge(full[['event_id','fold','predicted_quality','model_score','explanation_feature1','explanation_delta1','explanation_feature2','explanation_delta2','explanation_feature3','explanation_delta3']],on='event_id',how='left',validate='one_to_one')
    review['score_basis']=np.where(review.model_score.notna(),'walkforward_unseen_at_fit','initial_training_period_no_oos_score')
    review.sort_values(['quality','event_id'],ascending=[False,True]).to_csv(EXP/'all_labels_and_scores.csv',index=False)
    review[review.in_original_gallery].sort_values(['quality','event_id'],ascending=[False,True]).to_csv(EXP/'gallery_644_labels_and_scores.csv',index=False)
    review[review.in_original_gallery&review.clean_candidate].sort_values(['quality','event_id'],ascending=[False,True]).to_csv(EXP/'clean_gallery_candidates.csv',index=False)
    dump(EXP/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':run()
