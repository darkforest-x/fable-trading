"""Freeze ETH scores for same-model past calibration and later evaluation.

All features and native outcomes are reused from the registered v2 study.
For quarter Q, Q-1's already-fitted model scores both calibration and test.
Only complete NY label weeks expiring before Q can calibrate the mapping.
This avoids applying an older model's score scale to a newly refitted model.
No fitting, new features, prices, orders, barriers or costs are introduced.
"""
from __future__ import annotations

import json
import pickle
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-eth-1618-score-calibration-20261031-v1'
PRIOR=ROOT/'experiments/active/exp-btc-eth-xau-1618-models-20261031-v2'
ARM='fresh_tree_net_noordinal'


def config():return json.loads((EXP/'config.json').read_text())


def dump(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False,default=lambda x:x.item() if isinstance(x,np.generic) else str(x))+'\n')


def read(name):return pd.read_csv(EXP/name,float_precision='round_trip')


def clocks(data,start):
    prior=start-pd.DateOffset(months=3)
    end=start+pd.DateOffset(months=3)
    cal=data[data.complete&(data.week_start>=prior)&(data.expiry_dt<=start)]
    test=data[(data.week_start>=start)&(data.week_start<end)]
    assert not set(cal.week)&set(test.week)
    return cal,test


def bias_delta(cal,prior_weeks):
    """Week-weighted mean residual shrunk toward no score adjustment."""
    residual=(cal.net_r-cal.score).groupby(cal.week).mean()
    return float(residual.sum()/(len(residual)+prior_weeks))


def rule_mask(data,rule):
    if rule['mode']=='none':return np.zeros(len(data),dtype=bool)
    if rule['mode']=='all':return np.ones(len(data),dtype=bool)
    return data.score.to_numpy()>rule['threshold']


def rules(cal):
    cfg=config();result=[dict(name='none',mode='none'),dict(name='raw_zero',mode='threshold',threshold=0.)]
    for q in cfg['threshold_quantiles']:
        result.append(dict(name=f'q{int(q*100)}',mode='threshold',threshold=float(np.quantile(cal.score,q,method='linear'))))
    result.append(dict(name='all',mode='all'))
    assert [x['name'] for x in result]==cfg['threshold_candidates']
    return result


def main():
    from yoyo.evaluation.major_1618_v2_data import label_data
    from yoyo.evaluation.major_1618_v2_models import score_model
    cfg=config();ledger=label_data(include_xau=False);ledger=ledger[ledger.symbol.eq('ETH')]
    old=pd.read_csv(PRIOR/'ETH_predictions.csv.gz',float_precision='round_trip')
    output=[];receipts=[];policies=[]
    for target in cfg['targets']:
        cohort=ledger[ledger.target.eq(target)]
        for fold in cfg['test_quarters']:
            start=pd.Period(fold,freq='Q').start_time.tz_localize('America/New_York')
            prior_fold=str(pd.Period(fold,freq='Q')-1)
            path=PRIOR/'models/ETH'/target/prior_fold/f'{ARM}.pkl'
            with path.open('rb') as handle:bundle=pickle.load(handle)
            cal,test=clocks(cohort,start);cal=cal.copy();test=test.copy()
            rec=bundle['record'];train_ids=set(rec['train_event_ids'])
            assert not train_ids&set(cal.event_id) and not train_ids&set(test.event_id)
            assert pd.Timestamp(rec['label_expiry_max'])<=start-pd.DateOffset(months=3)
            reference=old[old.target.eq(target)&old.arm.eq(ARM)].set_index('trade_id')
            for role,d in [('calibration',cal),('test',test)]:
                d['score']=score_model(bundle['model'],d,bundle['features'],True)
                d['fresh_score']=d.trade_id.map(reference.score)
                d['role']=role;d['fold']=fold;d['score_model_fold']=prior_fold
                single=old[old.target.eq(target)&old.arm.eq('single_reward')].set_index('trade_id')
                d['single_reward_admit']=d.trade_id.map(single.admit)
                assert d.fresh_score.notna().all() and d.single_reward_admit.notna().all()
                output.append(d)
            np.testing.assert_allclose(cal.score,cal.fresh_score,rtol=0,atol=1e-12)
            adequate=len(cal)>=cfg['calibration_min_rows'] and cal.week.nunique()>=cfg['calibration_min_weeks']
            delta=bias_delta(cal,cfg['bias_prior_zero_weeks']) if adequate else 0.
            rule_list=rules(cal) if adequate else [dict(name='none',mode='none')]
            receipts.append(dict(target=target,fold=fold,model_fold=prior_fold,model_path=str(path),model_size=path.stat().st_size,
                 model_mtime=path.stat().st_mtime,train_n=rec['train_n'],train_weeks=rec['train_weeks'],train_label_expiry=rec['label_expiry_max'],
                 cal_n=len(cal),cal_weeks=int(cal.week.nunique()),cal_expiry_max=str(cal.expiry_dt.max()),
                 test_n=len(test),complete_test_n=int(test.complete.sum()),calibration_adequate=bool(adequate),
                 bias_delta_r=delta,cal_prediction_parity_max_error=float(abs(cal.score-cal.fresh_score).max())))
            policies.append(dict(target=target,fold=fold,adequate=bool(adequate),rules=rule_list,bias_delta_r=delta))
    pd.concat(output,ignore_index=True).to_csv(EXP/'scores.csv.gz',index=False)
    dump(EXP/'fold_receipts.json',receipts);dump(EXP/'frozen_candidates.json',policies)
    dump(EXP/'prepare_receipt.json',dict(models_reused=len(receipts),models_fitted=0,new_native_positions=0,
         data_source=str(PRIOR),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(pd.DataFrame(receipts)[['target','fold','train_n','cal_n','cal_weeks','complete_test_n','calibration_adequate']].to_string(index=False),flush=True)


if __name__=='__main__':main()
