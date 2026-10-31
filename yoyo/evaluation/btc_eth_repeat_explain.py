"""Exact attribution of three saved BTC short rejections, without refitting.

Owner 2026-10-31 asks which features lowered the three current-week scores.
Explain the frozen entry_2026Q4 logistic pipeline and BTC sigmoid calibrator.
Reference is the fitted StandardScaler mean in imputed feature space (pooled
historical BTC/ETH training rows), not a causal or feasible counterfactual.
beta*z terms add in raw log-odds; calibrator_slope*beta*z terms add in final
log-odds here because none of these three raw probabilities is clipped.
One-feature-to-reference probability deltas are diagnostic, not additive.
Only saved labels/features/models and frozen pre-touch OHLC are read; no new
signals, trades, training, thresholds, fees, or paper settings are produced.
"""
from __future__ import annotations

import json
import pickle
import subprocess
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import roc_auc_score

from yoyo.evaluation.btc_eth_repeat_data import EXP, dump, read_frame
from yoyo.evaluation.btc_eth_repeat_fit import labels, read, weights


def main():
    path=EXP/'models/entry_2026Q4.pkl'
    with path.open('rb') as handle:
        bundle=pickle.load(handle)
    model=bundle['model']; calibration=bundle['calibrators']['BTC']['model']
    columns=bundle['features']
    predictions=read('entry_predictions.csv.gz')
    cases=predictions[predictions.symbol.eq('BTC') & predictions.week.eq('2026-10-26') & predictions.side.eq(-1)].sort_values('ordinal')
    assert list(cases.ordinal)==[1,2,3]
    assert (model[-1].classes_==[0,1]).all() and (calibration.classes_==[0,1]).all()
    imputed=model[0].transform(cases[columns])
    scaled=model[1].transform(imputed)
    beta=model[-1].coef_[0]; intercept=float(model[-1].intercept_[0])
    slope=float(calibration.coef_[0,0]); offset=float(calibration.intercept_[0])
    contributions=scaled*beta
    margins=intercept+contributions.sum(axis=1)
    raw=expit(margins)
    assert ((raw>1e-6)&(raw<1-1e-6)).all()
    final=expit(offset+slope*margins)
    np.testing.assert_allclose(raw,model.predict_proba(cases[columns])[:,1],rtol=0,atol=1e-12)
    np.testing.assert_allclose(final,calibration.predict_proba(logit(raw)[:,None])[:,1],rtol=0,atol=1e-12)
    np.testing.assert_allclose(final,cases.p,rtol=0,atol=1e-12)
    frame=read_frame('BTC')
    rows=[];details=[]
    for k,(_,case) in enumerate(cases.iterrows()):
        negative=[];positive=[]
        for j,name in enumerate(columns):
            raw_reference=float(expit(margins[k]-contributions[k,j]))
            cal_reference=float(expit(offset+slope*(margins[k]-contributions[k,j])))
            item=dict(event_id=case.event_id,ordinal=int(case.ordinal),feature=name,
                      observed=float(case[name]),reference=float(model[1].mean_[j]),
                      scale=float(model[1].scale_[j]),standardized=float(scaled[k,j]),
                      coefficient=float(beta[j]),raw_logit_contribution=float(contributions[k,j]),
                      calibrated_logit_contribution=float(slope*contributions[k,j]),
                      raw_pp_vs_single_feature_reference=float(100*(raw[k]-raw_reference)),
                      final_pp_vs_single_feature_reference=float(100*(final[k]-cal_reference)))
            rows.append(item)
            (negative if contributions[k,j]<0 else positive).append(item)
        i=int(case.submission_i)
        threshold=float(case.break_even_p)
        details.append(dict(event_id=case.event_id,ordinal=int(case.ordinal),
                            touch_shanghai=str(pd.Timestamp(case.touch_open).tz_convert('Asia/Shanghai')),
                            feature_bar_open_shanghai=str((frame.index[i]-pd.Timedelta(minutes=5)).tz_convert('Asia/Shanghai')),
                            feature_bar_close_shanghai=str(frame.index[i].tz_convert('Asia/Shanghai')),
                            feature_bar_ohlcv={c:float(v) for c,v in frame.iloc[i].items()},
                            raw_p=float(raw[k]),calibrated_p=float(final[k]),threshold=threshold,
                            passes_without_calibration=bool(raw[k]>threshold),
                            calibration_delta_pp=float(100*(final[k]-raw[k])),
                            feature_sum_raw_logit=float(contributions[k].sum()),
                            required_raw_probability=float(expit((logit(threshold)-offset)/slope)),
                            negative=sorted(negative,key=lambda r:r['raw_logit_contribution']),
                            positive=sorted(positive,key=lambda r:r['raw_logit_contribution'],reverse=True)))
    allrows,_=labels()
    near=allrows[allrows.target.eq('near')]
    expiry=pd.to_datetime(near.expiry,utc=True)
    starts=pd.to_datetime(near.week).dt.tz_localize('America/New_York')
    cal=near[near.complete & near.symbol.eq('BTC') &
             (starts>=pd.Timestamp('2026-07-01',tz='America/New_York')) &
             (expiry<=pd.Timestamp('2026-10-01',tz='America/New_York'))].copy()
    assert len(cal)==bundle['calibrators']['BTC']['cal_n']==53
    cal['weight']=weights(cal)
    cal['raw_p']=model.predict_proba(cal[columns])[:,1]
    cal['calibrated_p']=calibration.predict_proba(logit(np.clip(cal.raw_p.to_numpy(),1e-6,1-1e-6))[:,None])[:,1]
    weekly=cal.groupby('week').agg(n=('label','size'),positive=('label','sum'),positive_rate=('label','mean'),
                                  weight_sum=('weight','sum'),mean_raw=('raw_p','mean'),mean_calibrated=('calibrated_p','mean')).reset_index()
    q4=predictions[predictions.fold.eq('2026Q4') & predictions.symbol.eq('BTC')]
    out=EXP/'diagnostics'
    out.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(out/'three_btc_shorts_feature_contributions.csv',index=False)
    cal[['event_id','week','side','ordinal','label','weight','raw_p','calibrated_p']].to_csv(out/'btc_q4_calibration_rows.csv',index=False)
    result=dict(model_path=str(path),model_mtime=path.stat().st_mtime,model_size=path.stat().st_size,
                model_frozen_fold=bundle['receipt'],
                reference='StandardScaler mean after imputation, historical pooled BTC/ETH training rows; analytical attribution, not a feasible market intervention or SHAP estimate.',
                raw_intercept=intercept,raw_reference_probability=float(expit(intercept)),
                calibration_slope=slope,calibration_intercept=offset,
                final_reference_probability=float(expit(offset+slope*intercept)),
                contribution_units='log-odds are additive; single-feature-to-reference probability differences are not additive',
                cases=details,
                calibration=dict(rows=len(cal),weeks=int(cal.week.nunique()),positives=int(cal.label.sum()),
                                 positive_rate=float(cal.label.mean()),weighted_positive_rate=float(np.average(cal.label,weights=cal.weight)),
                                 raw_auc=float(roc_auc_score(cal.label,cal.raw_p)),
                                 raw_probability_range=[float(cal.raw_p.min()),float(cal.raw_p.max())],
                                 calibrated_probability_range=[float(cal.calibrated_p.min()),float(cal.calibrated_p.max())],
                                 weekly=weekly.to_dict('records')),
                q4_btc=dict(rows=len(q4),selected=int(q4.gate.sum()),weeks=int(q4.week.nunique())),
                validation=dict(saved_probability_reproduction_max_error=float(abs(final-cases.p).max()),
                                clipping_active=False,refit=False,replay=False,settings_changed=False),
                generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(out/'three_btc_shorts_explanation.json',result)
    print(json.dumps({k:result[k] for k in ['raw_reference_probability','calibration_slope','calibration_intercept','final_reference_probability','q4_btc','validation']},indent=2))
    print(pd.DataFrame(details)[['ordinal','touch_shanghai','raw_p','calibrated_p','threshold','calibration_delta_pp','passes_without_calibration']].to_string(index=False))


if __name__=='__main__':
    main()
