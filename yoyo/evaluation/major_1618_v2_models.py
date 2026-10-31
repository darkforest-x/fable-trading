"""Fixed sequential rebuilding of separate BTC/ETH/OKX-XAU research models.

No hyperparameter search. Same causal pre-touch features and native outcomes
through each comparison: asset split -> remove calibration -> fresh training
history -> small nonlinear classifier -> actual netR objective -> remove only
touch ordinal. Final candidate is predefined, not chosen from test profits.

Features are inherited from btc_eth_repeat_data with its documented completed
5m/15m/4h windows. Each target's reward_r uses its known target and initial stop.
Training only includes whole NY label weeks expiring before each test quarter.
Classifier probabilities use the frozen cost-adjusted break-even gate; netR
regressors admit positive expected netR. Current incomplete weeks never train.
"""
from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier,LGBMRegressor
from scipy.special import logit
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from yoyo.evaluation.btc_eth_repeat_data import FEATURES,dump
from yoyo.evaluation.btc_eth_repeat_fit import weights
from yoyo.evaluation.major_1618_v2_data import EXP,OLD,config,label_data

FINAL='fresh_tree_net_noordinal'


def split(data,start):
    """Purge by known week expiry; never split repeated touch siblings."""
    calstart=start-pd.DateOffset(months=3)
    end=start+pd.DateOffset(months=3)
    old=data[data.complete & (data.expiry_dt<=calstart)]
    cal=data[data.complete & (data.week_start>=calstart)&(data.expiry_dt<=start)]
    fresh=data[data.complete & (data.expiry_dt<=start)]
    test=data[(data.week_start>=start)&(data.week_start<end)]
    assert not set(old.week)&set(cal.week)
    assert not set(fresh.week)&set(test.week)
    return old,cal,fresh,test


def sufficient(data,classifier=True):
    cfg=config()['minimum_history']
    counts=data.label.value_counts()
    return (len(data)>=cfg['train_rows'] and data.week.nunique()>=cfg['train_weeks'] and
            (not classifier or (len(counts)==2 and counts.min()>=cfg['each_classifier_class'])))


def fit_model(train,columns,kind):
    X=train[columns].replace([np.inf,-np.inf],np.nan)
    w=weights(train)
    if kind=='lr':
        model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler(),
                            LogisticRegression(C=1.,solver='lbfgs',max_iter=2000,random_state=config()['seed']))
        model.fit(X,train.label,logisticregression__sample_weight=w)
    else:
        cls=LGBMRegressor if kind=='regressor' else LGBMClassifier
        model=cls(**config()['lightgbm'])
        model.fit(X,train.net_r if kind=='regressor' else train.label,sample_weight=w)
    return model


def score_model(model,data,columns,regression=False):
    X=data[columns].replace([np.inf,-np.inf],np.nan)
    result=model.predict(X) if regression else model.predict_proba(X)[:,1]
    assert np.isfinite(result).all()
    return result


def run_fold(data,start,symbol,target):
    old,cal,fresh,test=split(data,start)
    fold=start.strftime('%YQ')+str(start.quarter)
    directory=EXP/'models'/symbol/target/fold
    directory.mkdir(parents=True,exist_ok=True)
    cached=directory/'predictions.csv.gz'
    if cached.exists():
        return pd.read_csv(cached,float_precision='round_trip'),json.loads((directory/'receipt.json').read_text())
    features=['f_'+f for f in FEATURES if f!='is_eth']
    rows=[];records=[]
    base=dict(symbol=symbol,target=target,fold=fold,test_n=len(test),test_weeks=int(test.week.nunique()))

    def skipped(arm,reason,train):
        records.append(dict(base,arm=arm,status=reason,train_n=len(train),train_weeks=int(train.week.nunique())))

    def save(arm,model,train,columns,kind,calibrator=None):
        raw=score_model(model,test,columns,kind=='regressor')
        scores=calibrator.predict_proba(logit(np.clip(raw,1e-6,1-1e-6))[:,None])[:,1] if calibrator is not None else raw
        pred=test.copy()
        pred['arm']=arm;pred['fold']=fold;pred['score']=scores;pred['score_kind']='net_r' if kind=='regressor' else 'probability'
        pred['admit']=scores>0 if kind=='regressor' else scores>pred.break_even_p
        record=dict(base,arm=arm,status='fitted',train_n=len(train),train_weeks=int(train.week.nunique()),
                    train_positive=float(train.label.mean()),train_net_r_mean=float(train.net_r.mean()),
                    label_expiry_max=str(train.expiry_dt.max()),feature_columns=columns,
                    calibration_n=len(cal) if calibrator is not None else 0,
                    test_selected=int(pred.admit.sum()),score_kind=str(pred.score_kind.iloc[0]),
                    model_kind=kind,train_event_ids=train.event_id.tolist())
        if kind=='lr':
            record['coefficients']=model[-1].coef_[0].tolist()
        else:
            record['feature_gain']=model.booster_.feature_importance(importance_type='gain').tolist()
        if calibrator is not None:
            record['calibration_slope']=float(calibrator.coef_[0,0]);record['calibration_intercept']=float(calibrator.intercept_[0])
            record['calibration_expiry_max']=str(cal.expiry_dt.max())
        with (directory/f'{arm}.pkl').open('wb') as handle:
            pickle.dump(dict(model=model,calibrator=calibrator,features=columns,kind=kind,record=record),handle)
        records.append(record);rows.append(pred)

    if test.empty:
        return pd.DataFrame(),[dict(base,status='no_test_rows')]
    if target=='near':
        if sufficient(old):
            model=fit_model(old,features,'lr')
            save('asset_lr_raw',model,old,features,'lr')
            minimum=config()['minimum_history']
            if len(cal)>=minimum['cal_rows'] and cal.week.nunique()>=minimum['cal_weeks'] and cal.label.nunique()==2:
                raw=score_model(model,cal,features)
                calibrator=LogisticRegression(C=1.,max_iter=2000)
                calibrator.fit(logit(np.clip(raw,1e-6,1-1e-6))[:,None],cal.label,sample_weight=weights(cal))
                save('asset_lr_cal',model,old,features,'lr',calibrator)
            else:
                skipped('asset_lr_cal','insufficient_calibration',old)
        else:
            for arm in ['asset_lr_raw','asset_lr_cal']:
                skipped(arm,'insufficient_train',old)
        if sufficient(fresh):
            model=fit_model(fresh,features,'lr');save('fresh_lr_raw',model,fresh,features,'lr')
            model=fit_model(fresh,features,'classifier');save('fresh_tree_class',model,fresh,features,'classifier')
        else:
            for arm in ['fresh_lr_raw','fresh_tree_class']:
                skipped(arm,'insufficient_train',fresh)
        if sufficient(fresh,False):
            model=fit_model(fresh,features,'regressor');save('fresh_tree_net',model,fresh,features,'regressor')
        else:
            skipped('fresh_tree_net','insufficient_train',fresh)
    if sufficient(fresh,False):
        no_ordinal=[c for c in features if c!='f_ordinal_log']
        model=fit_model(fresh,no_ordinal,'regressor');save(FINAL,model,fresh,no_ordinal,'regressor')
    else:
        skipped(FINAL,'insufficient_train',fresh)
    # A fixed single-feature benchmark uses the same fresh training history.
    if sufficient(fresh):
        columns=['f_reward_r'];model=fit_model(fresh,columns,'lr')
        save('single_reward',model,fresh,columns,'lr')
    else:
        skipped('single_reward','insufficient_train',fresh)
    result=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()
    if len(result):
        result.to_csv(cached,index=False)
    dump(directory/'receipt.json',records)
    return result,records


def main():
    p=argparse.ArgumentParser();p.add_argument('--symbol',choices=['BTC','ETH','XAU'],required=True)
    args=p.parse_args();symbol=args.symbol
    if (EXP/f'{symbol}_fit_complete.json').exists():
        print('Reusing completed model outputs',symbol,flush=True);return
    data=label_data(include_xau=symbol=='XAU');data=data[data.symbol.eq(symbol)]
    predictions=[];receipts=[]
    for target in config()['targets']:
        cohort=data[data.target.eq(target)]
        for start in pd.date_range('2025-01-01','2026-10-01',freq='QS',tz='America/New_York'):
            pred,record=run_fold(cohort,start,symbol,target)
            if len(pred):predictions.append(pred)
            receipts.extend(record)
        print(symbol,target,'folds finished',flush=True)
    combined=pd.concat(predictions,ignore_index=True)
    combined.to_csv(EXP/f'{symbol}_predictions.csv.gz',index=False)
    # Compact ledger omits per-fold training IDs retained beside each model.
    compact=[{k:v for k,v in r.items() if k not in ['train_event_ids','coefficients','feature_gain']} for r in receipts]
    dump(EXP/f'{symbol}_folds.json',compact)
    latest=[]
    for target in config()['targets']:
        path=EXP/'models'/symbol/target/'2026Q4'/f'{FINAL}.pkl'
        latest.append(dict(symbol=symbol,target=target,path=str(path),available=path.exists(),production_eligible=False))
    dump(EXP/f'{symbol}_fit_complete.json',dict(symbol=symbol,models_fitted=sum(r.get('status')=='fitted' for r in receipts),
         rows=len(combined),latest_models=latest,builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),config=config()))
    print(symbol,'complete',len(combined),'prediction rows',flush=True)


if __name__=='__main__':
    main()
