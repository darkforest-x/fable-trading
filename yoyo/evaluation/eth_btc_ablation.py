"""ETH BTC-context input ablation with frozen history, targets and netR gate.

Input groups reuse the already audited pre-touch5m features. Explicit beta
and correlation are separated from BTC4h return and ETH4h beta residual;
the residual still contains beta internally, so groups are not orthogonal.
No new market data, label calculation or native OHLC replay is performed.
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_eth_repeat_data import dump

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-eth-btc-context-ablation-20261031-v1'
SOURCE = ROOT/'experiments/active/exp-eth-1618-features-training-20261031-v1'
ORIGINAL = ROOT/'experiments/active/exp-btc-eth-xau-1618-models-20261031-v2'


def config():
    return json.loads((EXP/'config.json').read_text())


def read(name, source=EXP):
    return pd.read_csv(source/name, float_precision='round_trip')


def model_path(arm, target, fold):
    if arm == 'original_model':
        return ORIGINAL/'models/ETH'/target/fold/'fresh_tree_net_noordinal.pkl'
    if arm == 'full_context':
        return SOURCE/'models'/target/fold/'add_btc_context.pkl'
    return EXP/'models'/target/fold/f'{arm}.pkl'


def feature_columns(base, arm):
    """Only change the declared input group; no new computation or cutoff."""
    return list(base)+config()['feature_groups'][arm]


def fit():
    from lightgbm import LGBMRegressor
    from yoyo.evaluation.eth_1618_feature_models import training_rows, training_weights
    cfg=config(); oldcfg=json.loads((SOURCE/'config.json').read_text()); data=read('data.csv.gz',SOURCE)
    old=read('predictions.csv.gz',SOURCE)
    reuse=old[old.arm.isin(['original_model','add_btc_context'])].copy()
    reuse['arm']=reuse.arm.replace({'add_btc_context':'full_context'})
    outputs=[reuse]; records=[]
    for target in cfg['targets']:
        cohort=data[data.target.eq(target)]
        for fold in cfg['folds']:
            start=pd.Period(fold,freq='Q').start_time.tz_localize('America/New_York')
            week=pd.to_datetime(cohort.week_start,utc=True)
            train=training_rows(cohort,start,'original_model')
            test=cohort[(week>=start)&(week<start+pd.DateOffset(months=3))]
            with model_path('original_model',target,fold).open('rb') as handle: original=pickle.load(handle)
            with model_path('full_context',target,fold).open('rb') as handle: full=pickle.load(handle)
            assert train.event_id.tolist()==full['record']['train_event_ids']
            assert set(train.event_id)==set(original['record']['train_event_ids'])
            assert not set(train.week)&set(test.week)
            w=training_weights(train,start,'original_model')
            for arm in cfg['arms']:
                path=model_path(arm,target,fold);path.parent.mkdir(parents=True,exist_ok=True)
                cache=path.with_name(f'{arm}_predictions.csv.gz')
                if path.exists() and cache.exists():
                    with path.open('rb') as handle: bundle=pickle.load(handle)
                    outputs.append(pd.read_csv(cache,float_precision='round_trip'));records.append(bundle['record']);continue
                columns=feature_columns(original['features'],arm)
                assert len(columns)==22 and set(columns)<=set(full['features'])
                model=LGBMRegressor(**oldcfg['lightgbm'])
                model.fit(train[columns].replace([np.inf,-np.inf],np.nan),train.net_r,sample_weight=w)
                score=model.predict(test[columns].replace([np.inf,-np.inf],np.nan))
                assert np.isfinite(score).all()
                result=test.copy();result['arm']=arm;result['fold']=fold;result['score']=score
                result['score_kind']='net_r';result['admit']=score>0
                record=dict(arm=arm,target=target,fold=fold,features=columns,train_n=len(train),
                    train_weeks=int(train.week.nunique()),train_positive=float(train.label.mean()),
                    train_net_r_mean=float(train.net_r.mean()),train_event_ids=train.event_id.tolist(),
                    label_expiry_max=str(pd.to_datetime(train.expiry_dt,utc=True).max()),
                    weight_sum=float(w.sum()),weights_min=float(w.min()),weights_max=float(w.max()),
                    params=oldcfg['lightgbm'],test_n=len(test),test_complete_n=int(test.complete.sum()),
                    feature_gain=model.booster_.feature_importance(importance_type='gain').tolist())
                with path.open('wb') as handle:pickle.dump(dict(model=model,features=columns,record=record),handle)
                result.to_csv(cache,index=False);outputs.append(result);records.append(record)
            print(target,fold,'two groups fitted',flush=True)
    pd.concat(outputs,ignore_index=True).to_csv(EXP/'predictions.csv.gz',index=False)
    dump(EXP/'model_receipts.json',[{k:v for k,v in r.items() if k!='train_event_ids'} for r in records])
    dump(EXP/'fit_receipt.json',dict(new_models=len(records),old_models_reused=48,new_native_positions=0,
        source_data=str(SOURCE/'data.csv.gz'),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))


def portfolios():
    from yoyo.evaluation.eth_score_calibration_vbt import cached_portfolios
    cfg=config();pred=read('predictions.csv.gz');complete=pred[pred.complete]
    previous=read('selected_trades.csv.gz',SOURCE)
    old=previous[previous.arm.isin(['original_model','add_btc_context','unscored','single_reward'])].copy()
    old['arm']=old.arm.replace({'add_btc_context':'full_context'})
    outputs=[old];audits=[]
    for (target,fold),group in complete.groupby(['target','fold']):
        reference=group[group.arm.eq('original_model')].copy()
        masks=np.column_stack([group[group.arm.eq(arm)].set_index('trade_id').loc[reference.trade_id].admit.to_numpy(bool) for arm in cfg['arms']])
        result,audit=cached_portfolios(reference,masks,cfg['arms'])
        for arm,rows in result.items():
            rows['score']=rows.trade_id.map(group[group.arm.eq(arm)].set_index('trade_id').score)
            rows['admit']=True;outputs.append(rows)
        audits.append(dict(target=target,fold=fold,**audit))
    pd.concat(outputs,ignore_index=True).to_csv(EXP/'selected_trades.csv.gz',index=False)
    pred[~pred.complete][['trade_id','target','fold','week','side','ordinal','arm','score','admit']].to_csv(EXP/'current_week.csv',index=False)
    dump(EXP/'engine_audit.json',audits)
    dump(EXP/'engine_receipt.json',dict(new_policy_runs=len(audits),columns_per_run=2,new_native_positions=0,
        OHLC_replays=0,existing_control_selections_reused=True,
        max_native_net_r_error=max(r['max_native_net_r_error'] for r in audits),
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(pd.concat(outputs).groupby(['target','arm']).net_r.agg(['count','sum']).to_string(),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['fit','portfolios'])
    args=parser.parse_args();fit() if args.stage=='fit' else portfolios()
