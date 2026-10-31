"""Frozen Tree SHAP attribution for the three owner-selected BTC near shorts.

Explain the existing 2026Q4 netR/noordinal regressor in raw expected netR,
using LightGBM's exact tree-path-dependent contributions. The reference is
the frozen trees' training path cover, not this week's rows or future labels.
Only the saved pre-touch feature allowlist enters inference. No fitting,
price replay, candidate selection, feature changes or gate changes occur.

The verify stage runs in an isolated SHAP0.52 environment, consumes only an
exported native booster and feature table, retains a shap.Explanation, and
checks both native contributions and stored scores independently.
"""
from __future__ import annotations

import argparse
import json
import pickle
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-btc-eth-xau-1618-models-20261031-v2'
OUT=EXP/'diagnostics_three_btc_shorts'
ARM='fresh_tree_net_noordinal'


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def explain():
    from yoyo.evaluation.major_1618_v2_data import read,label_data
    path=EXP/'models/BTC/near/2026Q4'/f'{ARM}.pkl'
    with path.open('rb') as handle:
        bundle=pickle.load(handle)
    model=bundle['model'];columns=bundle['features'];receipt=bundle['record']
    assert bundle['kind']=='regressor' and bundle['calibrator'] is None
    assert 'f_ordinal_log' not in columns
    pred=read('BTC_predictions.csv.gz')
    cases=pred[pred.target.eq('near')&pred.arm.eq(ARM)&pred.week.eq('2026-10-26')&pred.side.eq(-1)].sort_values('ordinal')
    assert list(cases.ordinal)==[1,2,3] and not cases.complete.any()
    X=cases[columns].replace([np.inf,-np.inf],np.nan)
    native=np.asarray(model.predict(X,pred_contrib=True))
    score=model.predict(X)
    np.testing.assert_allclose(native.sum(axis=1),score,rtol=0,atol=1e-12)
    np.testing.assert_allclose(score,cases.score,rtol=0,atol=1e-12)
    np.testing.assert_array_equal(score>0,cases.admit)
    training=label_data(include_xau=False)
    training=training[training.symbol.eq('BTC')&training.target.eq('near')&training.event_id.isin(receipt['train_event_ids'])]
    assert len(training)==receipt['train_n'] and training.complete.all()
    assert not set(training.event_id)&set(cases.event_id)
    assert training.expiry_dt.max()<=pd.Timestamp('2026-10-01',tz='America/New_York')
    details=[];rows=[];contrasts=[]
    for k,(_,case) in enumerate(cases.iterrows()):
        terms=[];diffs=[]
        for j,name in enumerate(columns):
            item=dict(event_id=case.event_id,ordinal=int(case.ordinal),feature=name,observed=float(X.iloc[k,j]),
                      shap_r=float(native[k,j]),shap_bp=float(native[k,j]*case.risk/case.entry*10000),
                      training_q05=float(training[name].quantile(.05)),training_median=float(training[name].median()),
                      training_q95=float(training[name].quantile(.95)))
            terms.append(item);rows.append(item)
            difference=dict(event_id=case.event_id,ordinal=int(case.ordinal),feature=name,
                            observed=float(X.iloc[k,j]),accepted_observed=float(X.iloc[1,j]),
                            shap_difference_vs_second_r=float(native[k,j]-native[1,j]))
            diffs.append(difference);contrasts.append(difference)
        details.append(dict(event_id=case.event_id,ordinal=int(case.ordinal),
            touch_shanghai=str(pd.Timestamp(case.touch_open).tz_convert('Asia/Shanghai')),
            feature_bar_close_shanghai=str(pd.Timestamp(case.submission_time).tz_convert('Asia/Shanghai')),
            baseline_r=float(native[k,-1]),positive_sum_r=float(native[k,:-1][native[k,:-1]>0].sum()),
            negative_sum_r=float(native[k,:-1][native[k,:-1]<0].sum()),
            predicted_net_r=float(score[k]),predicted_net_bp=float(score[k]*case.risk/case.entry*10000),
            gate_net_r=0.,admitted=bool(score[k]>0),
            negative=sorted([r for r in terms if r['shap_r']<0],key=lambda r:r['shap_r']),
            positive=sorted([r for r in terms if r['shap_r']>0],key=lambda r:r['shap_r'],reverse=True),
            difference_vs_second_r=float(score[k]-score[1]),
            negative_differences_vs_second=sorted([r for r in diffs if r['shap_difference_vs_second_r']<0],key=lambda r:r['shap_difference_vs_second_r'])))
    OUT.mkdir(exist_ok=True)
    X.to_csv(OUT/'features.csv',index=False)
    pd.DataFrame(rows).to_csv(OUT/'feature_contributions.csv',index=False)
    pd.DataFrame(contrasts).to_csv(OUT/'differences_vs_accepted_second.csv',index=False)
    np.savez(OUT/'native_contributions.npz',contributions=native,scores=score)
    model.booster_.save_model(str(OUT/'frozen_booster.txt'))
    result=dict(model_path=str(path),model_size=path.stat().st_size,model_mtime=path.stat().st_mtime,
         feature_names=columns,units='Expected netR; not probability or percentage points. Gate is strictly >0.',
         method='Exact native LightGBM TreeSHAP, tree-path-dependent; background is frozen historical training path cover.',
         reference=dict(train_n=len(training),train_weeks=int(training.week.nunique()),
                        first_week=str(training.week.min()),last_week=str(training.week.max()),
                        label_expiry_max=str(training.expiry_dt.max()),baseline_r=float(native[0,-1])),
         cases=details,validation=dict(prediction_reproduction_max_error=float(abs(score-cases.score.to_numpy()).max()),
             additivity_max_error=float(abs(native.sum(axis=1)-score).max()),no_ordinal=True,no_calibrator=True,
             refit=False,replay=False,settings_changed=False),
         limitations=['Local description of this frozen model, not a market-causal explanation.',
                      'Correlated features share attribution under path-dependent assumptions.',
                      'Positive/negative SHAP contributions are not the effect of deleting a feature or retraining.',
                      'Three owner-selected known winners,not an unbiased performance evaluation.'],
         sources=['https://lightgbm.readthedocs.io/en/v4.6.0/pythonapi/lightgbm.Booster.html',
                  'https://shap.readthedocs.io/en/stable/generated/shap.TreeExplainer.html'],
         reproduction=['.venv/bin/python -m yoyo.evaluation.major_1618_v2_explain',
                       '/Users/zhangzc/.local/share/fable-trading/venvs/shap-052/bin/python -m yoyo.evaluation.major_1618_v2_explain --verify'],
         generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    write_json(OUT/'explanation.json',result)
    for case in details:
        print(json.dumps({k:case[k] for k in ['ordinal','baseline_r','positive_sum_r','negative_sum_r','predicted_net_r','predicted_net_bp','admitted']},ensure_ascii=False))
        print(pd.DataFrame(case['negative'])[['feature','observed','shap_r']].head(6).to_string(index=False))
        print('Positive',[(r['feature'],r['shap_r']) for r in case['positive'][:4]],flush=True)


def verify():
    import lightgbm as lgb
    import shap
    model=lgb.Booster(model_file=str(OUT/'frozen_booster.txt'))
    X=pd.read_csv(OUT/'features.csv',float_precision='round_trip')
    native=np.load(OUT/'native_contributions.npz')
    explainer=shap.TreeExplainer(model,model_output='raw',feature_perturbation='tree_path_dependent')
    explanation=explainer(X,check_additivity=True)
    assert isinstance(explanation,shap.Explanation)
    np.testing.assert_allclose(explanation.values,native['contributions'][:,:-1],rtol=0,atol=1e-12)
    np.testing.assert_allclose(explanation.base_values,native['contributions'][:,-1],rtol=0,atol=1e-12)
    error=float(abs(explanation.base_values+explanation.values.sum(axis=1)-native['scores']).max())
    assert error<1e-12
    with (OUT/'shap_explanation.pkl').open('wb') as handle:pickle.dump(explanation,handle)
    result=dict(shap_version=shap.__version__,lightgbm_version=lgb.__version__,rows=len(X),features=len(X.columns),
                shap_explanation_retained=True,background='tree_path_dependent frozen training cover',
                native_parity_max_error=float(abs(explanation.values-native['contributions'][:,:-1]).max()),
                additivity_max_error=error,output='regression netR')
    write_json(OUT/'shap_verification.json',result);print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    verify() if args.verify else explain()
