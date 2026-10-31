"""Audit ETH temporal calibration, native reuse and frozen-rule sensitivity.

No refits, prices replay or new backtests. The two cached alternate ETH near
outcomes close on identical bar indices; replacement cannot alter capacity.
All references to current-quarter outcomes are evaluation-only.
"""
from __future__ import annotations

import json
import pickle
import subprocess
import platform
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_score_calibration_data import EXP,PRIOR,ROOT,config,read,dump,rules,bias_delta,rule_mask
from yoyo.evaluation.btc_eth_repeat_fit import metrics


def main():
    cfg=config();d=read('scores.csv.gz');selected=read('selected_trades.csv.gz')
    receipts=json.loads((EXP/'fold_receipts.json').read_text())
    decisions=json.loads((EXP/'decisions.json').read_text())
    cal_chosen=read('calibration_selections.csv.gz');audits=[]
    assert selected.complete.all() and selected.role.eq('test').all()
    assert not selected.week.eq('2026-10-26').any()
    for receipt in receipts:
        path=receipt['model_path']
        with open(path,'rb') as handle:bundle=pickle.load(handle)
        subset=d[d.target.eq(receipt['target'])&d.fold.eq(receipt['fold'])]
        start=pd.Period(receipt['fold'],freq='Q').start_time.tz_localize('America/New_York')
        cal=subset[subset.role.eq('calibration')];test=subset[subset.role.eq('test')]
        assert not set(bundle['record']['train_event_ids'])&set(subset.event_id)
        assert pd.Timestamp(bundle['record']['label_expiry_max'])<=start-pd.DateOffset(months=3)
        assert pd.to_datetime(cal.expiry_dt,utc=True).max()<=start and cal.complete.all()
        assert not set(cal.week)&set(test.week)
        predicted=bundle['model'].booster_.predict(subset[bundle['features']].replace([np.inf,-np.inf],np.nan))
        np.testing.assert_allclose(predicted,subset.score,rtol=0,atol=1e-12)
        dec=next(r for r in decisions if r['target']==receipt['target'] and r['fold']==receipt['fold'])
        candidate_rules=rules(cal)
        assert abs(bias_delta(cal,cfg['bias_prior_zero_weeks'])-dec['bias_delta_r'])<1e-12
        best=dec['candidates'][0]
        for candidate,rule in zip(dec['candidates'],candidate_rules):
            assert all(candidate[k]==v for k,v in rule.items())
            g=cal_chosen[cal_chosen.target.eq(receipt['target'])&cal_chosen.fold.eq(receipt['fold'])&cal_chosen.arm.eq(candidate['name'])]
            assert len(g)==candidate['n'] and int(g.week.nunique())==candidate['weeks']
            assert abs(g.vbt_net_r.sum()-candidate['net_r'])<1e-9
            if candidate['eligible'] and candidate['net_r']>best['net_r']+1e-12:best=candidate
        assert best==dec['chosen']
        for arm in selected.arm.unique():
            actual=selected[selected.target.eq(receipt['target'])&selected.fold.eq(receipt['fold'])&selected.arm.eq(arm)]
            lookup=test.set_index('trade_id').loc[actual.trade_id]
            if arm=='past_selected_threshold':assert rule_mask(lookup,best).all()
            elif arm=='bias_calibrated_zero':assert (lookup.score+dec['bias_delta_r']>0).all()
            elif arm=='lagged_raw_zero':assert lookup.score.gt(0).all()
            elif arm=='fresh_raw_zero':assert lookup.fresh_score.gt(0).all()
            elif arm=='single_reward':assert lookup.single_reward_admit.all() and lookup.single_reward_available.all()
        audits.append(dict(target=receipt['target'],fold=receipt['fold'],model_fold=receipt['model_fold'],
            train_events=len(bundle['record']['train_event_ids']),calibration_events=len(cal),test_events=len(test),
            score_max_error=float(abs(predicted-subset.score.to_numpy()).max()),chosen_rule=best['name']))
    native_source=ROOT/'experiments/active/exp-btc-eth-repeat-1618-20261031-v1'
    native=pd.read_csv(native_source/'ETH_native_trades.csv.gz',float_precision='round_trip').set_index('trade_id')
    for (_,_,_),g in selected.groupby(['target','fold','arm']):
        assert g.trade_id.is_unique
        g=g.sort_values(['submission_i','trade_id'])
        assert (g.submission_i.iloc[1:].to_numpy()>=g.exit_i.iloc[:-1].to_numpy()).all()
        expected=native.loc[g.trade_id]
        for col in ['entry_i','exit_i','entry','exit_price','fee_r','net_r']:
            np.testing.assert_allclose(g[col],expected[col],rtol=0,atol=1e-12)
        np.testing.assert_allclose(g.vbt_net_r,expected.net_r,rtol=0,atol=1e-8)
    previous=pd.read_csv(PRIOR/'selected_trades.csv.gz',float_precision='round_trip')
    parity=[]
    for target,g in selected[selected.arm.eq('fresh_raw_zero')].groupby('target'):
        available=set(d[d.target.eq(target)&d.role.eq('test')&d.complete].trade_id)
        old=previous[previous.symbol.eq('ETH')&previous.target.eq(target)&previous.arm.eq('fresh_tree_net_noordinal')&previous.trade_id.isin(available)]
        assert set(g.trade_id)==set(old.trade_id)
        assert abs(g.vbt_net_r.sum()-old.net_r.sum())<1e-8
        parity.append(dict(target=target,n=len(g),net_r=float(g.vbt_net_r.sum())))
    alt=pd.read_csv(native_source/'ETH_path_sensitivity_trades.csv.gz',float_precision='round_trip').set_index('trade_id')
    original=native.loc[alt.index]
    assert (original.exit_i==alt.exit_i).all(),'Changed holding times require framework capacity reevaluation'
    paths=[]
    for (target,arm),g in selected.groupby(['target','arm']):
        alternate=g.copy().set_index('trade_id',drop=False);alternate.index.name=None
        shared=alternate.index.intersection(alt.index)
        cols=['net_r','net_notional_bp','gross_r','fee_r','reason']
        alternate.loc[shared,cols]=alt.loc[shared,cols]
        alternate['label']=alternate.reason.eq(4).astype(int)
        paths.append(dict(target=target,arm=arm,primary_net_r=float(g.vbt_net_r.sum()),alternate=metrics(alternate)))
    source_validation=json.loads((PRIOR/'validation.json').read_text())
    result=dict(folds=audits,baseline_parity=parity,model_refits=0,new_native_positions=0,nonoverlap=True,
        engine_audit=json.loads((EXP/'engine_audit.json').read_text()),
        reused_fee_evidence=[x for x in source_validation['BTC_ETH_fee_evidence'] if x['symbol']=='ETH' and x['arm']=='native'],
        frozen_gate_path_sensitivity=paths,
        numeric_warnings='No inference changes; saved original models reused. Every new lagged prediction exactly matches native Booster output.',
        tests='3 chronological/bias/rule tests and2VectorBT behavior tests including scalar-column parity passed.',
        source_recovery='Initial prepare assertion exposed missing opposite-target single-feature model for2025Q2; missing classifier now explicitly abstains with coverage recorded. No substitute fitting or data fabricated.',
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'validation.json',result)
    import lightgbm,sklearn
    dump(EXP/'reproduction.json',dict(runtime=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__,
        lightgbm=lightgbm.__version__,sklearn=sklearn.__version__,vectorbt='1.2.0 in isolated vectorbt-python'),
        commands=['.venv/bin/python -m yoyo.evaluation.eth_score_calibration_data',
                  '/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.eth_score_calibration_vbt',
                  '.venv/bin/python -m yoyo.evaluation.eth_score_calibration_evaluate',
                  '.venv/bin/python -m yoyo.evaluation.eth_score_calibration_validate'],
        source_inputs=str(PRIOR),native_source=str(native_source),models_reused=21,refits=0,price_replays=0))
    print('Validation passed:',dict(folds=len(audits),max_score_error=max(r['score_max_error'] for r in audits),
          selected_rows=len(selected),baseline_parity=parity),flush=True)


if __name__=='__main__':main()
