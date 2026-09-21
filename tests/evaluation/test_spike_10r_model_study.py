"""Held-out threshold, maturity, selection and receipt guards for model research."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_10r_model_study as m
from yoyo.evaluation import spike_10r_search as s


def test_quarter_blocks_purge_at_fit_end_and_never_train_calibration():
    t=pd.to_datetime(['2024-09-30','2024-10-01','2025-06-30','2025-07-01','2025-09-30','2025-10-01'],utc=True)
    c=pd.DataFrame(dict(available_at=t,entry_time=t,exit_time=t,timeframe_min=60,valid_entry=True,censored=False,net_r=12.))
    c.loc[2,'exit_time']=pd.Timestamp('2025-07-01',tz='UTC')
    tr,ca,te,cutoff=m.quarterly_blocks(c,'2025-10-01')
    assert tr.tolist()==[False,True,False,False,False,False]
    assert ca.tolist()==[False,False,False,True,True,False]
    assert te.tolist()==[False,False,False,False,False,True]
    assert cutoff==pd.Timestamp('2025-07-01',tz='UTC')
    c.loc[~tr,'net_r']=-1e8
    assert m.quarterly_blocks(c,'2025-10-01')[0].equals(tr)


def test_unlabelled_threshold_does_not_recompute_on_target_distribution():
    past=np.linspace(0,1,100)
    a,cut=m.score_gate(past,[.5,.95,np.nan],.1)
    b,changed=m.score_gate(past,[1e5,-1e5,np.nan],.1)
    assert cut==changed and cut==pytest.approx(.9)
    assert a.tolist()==[False,True,False]
    assert b.tolist()==[True,False,False]
    # Ties are admitted explicitly; nominal tail fraction is not a quota.
    tie,t=m.score_gate(np.ones(100),[1.,.9],.01)
    assert tie.tolist()==[True,False] and t==1
    unknown,t=m.score_gate(np.ones(99),[100.],.1)
    assert not unknown.any() and np.isnan(t)


def test_serial_trigger_replays_all_predeclared_models_not_only_best():
    rows=[dict(period='oof',rule='original_all',precision=.01,gt10=100,recall=1.)]
    rows.extend(dict(period='oof',rule=name,precision=.009,gt10=5,recall=.05) for name in m.model_policies())
    table=pd.DataFrame(rows)
    assert not m.serial_trigger(table)['needed']
    table.loc[1,['precision','gt10','recall']]=[.02,10,.1]
    result=m.serial_trigger(table)
    assert result['needed'] and result['triggered_by']==[m.model_policies()[0]]
    assert result['replay_policies']==m.all_policies()


def test_receipt_rejects_changed_prediction_or_builder(tmp_path):
    p=tmp_path/'scores.csv';p.write_text('a,b\n1,2\n')
    code=tmp_path/'code.py';code.write_text('x=1\n')
    s.dump(tmp_path/'receipt.json',dict(files={'scores.csv':s.digest(p)},dependencies={str(code):s.digest(code)}))
    assert m.verify_output(tmp_path,'receipt.json')['files']['scores.csv']==s.digest(p)
    code.write_text('x=2\n')
    with pytest.raises(ValueError,match='builder drift'):
        m.verify_output(tmp_path,'receipt.json')
    code.write_text('x=1\n');p.write_text('a,b\n1,99\n')
    with pytest.raises(ValueError,match='frozen output drift'):
        m.verify_output(tmp_path,'receipt.json')


def test_oof_clock_has_same_first_quarter_for_every_policy():
    c=pd.DataFrame({'available_at':pd.to_datetime(['2025-09-30','2025-10-01','2026-03-31','2026-04-01'],utc=True)})
    clocks=dict(m.clocks(c))
    assert clocks['oof'].tolist()==[False,True,True,True]
    assert clocks['2025Q4'].tolist()==[False,True,False,False]
    assert clocks['last_two_quarters'].tolist()==[False,False,False,True]


def test_admission_reconstruction_rejects_future_calibration_and_changed_entry(tmp_path):
    times=pd.date_range('2024-10-01','2026-09-10',freq='12h',inclusive='left',tz='UTC')
    c=pd.DataFrame(dict(event_key=[str(i) for i in range(len(times))],stream_key='s',available_at=times,
        entry_time=times,exit_time=times+pd.Timedelta(hours=1),timeframe_min=60,valid_entry=True,censored=False))
    # Prefix prevents CSV's integer inference from changing event-key identity.
    c.event_key='s:'+c.event_key
    for j,f in enumerate(s.FEATURES):c[f]=1+(np.arange(len(c))%37)/37+j
    keys=['event_key','stream_key','available_at','timeframe_min']
    d=c[keys].copy();scores=c[keys].copy();scores['quarter']=''
    for name in (*m.MODEL_NAMES,'risk'):scores[name]=np.nan
    for name in m.all_policies():d[name]=c.available_at.lt(m.START)
    histories=[];thresholds=[]
    for date in m.QUARTERS:
        q=pd.Timestamp(date,tz='UTC');_,cal,target,cutoff=m.quarterly_blocks(c,q)
        tag=f'{q.year}Q{q.quarter}'
        history=c.loc[cal,keys].copy();history['quarter']=tag
        for name in (*m.MODEL_NAMES,'risk'):
            value=-c.reference_risk_fraction.to_numpy()
            history[name]=value[cal];scores.loc[target,name]=value[target]
            for coverage in m.COVERAGES:
                policy=m.policy_name(name,coverage)
                admitted,threshold=m.score_gate(value[cal],value[target],coverage)
                d.loc[target,policy]=admitted
                thresholds.append(dict(quarter=tag,rule=policy,threshold=threshold,calibration_start=cutoff,calibration_end=q))
        histories.append(history)
    matrix,_,_=s.calibrate(c);d.loc[c.available_at.ge(m.START),'prior_v21_risk_decile']=matrix[c.available_at.ge(m.START),0]
    d.to_csv(tmp_path/'decisions.csv.gz',index=False);scores.to_csv(tmp_path/'scores.csv.gz',index=False)
    history=pd.concat(histories);history.to_csv(tmp_path/'calibration_scores.csv.gz',index=False)
    pd.DataFrame(thresholds).to_csv(tmp_path/'thresholds.csv',index=False)
    m.verify_admissions(c,tmp_path)
    idx=np.flatnonzero(c.available_at.ge(m.START))[0]
    d.loc[idx,'logistic_top1']=~d.loc[idx,'logistic_top1']
    d.to_csv(tmp_path/'decisions.csv.gz',index=False)
    with pytest.raises(ValueError,match='admission differs'):
        m.verify_admissions(c,tmp_path)
    d.loc[idx,'logistic_top1']=~d.loc[idx,'logistic_top1'];d.to_csv(tmp_path/'decisions.csv.gz',index=False)
    history.iloc[0,history.columns.get_loc('event_key')]=c.loc[idx,'event_key']
    history.to_csv(tmp_path/'calibration_scores.csv.gz',index=False)
    with pytest.raises(ValueError,match='earlier three-month cohort'):
        m.verify_admissions(c,tmp_path)
