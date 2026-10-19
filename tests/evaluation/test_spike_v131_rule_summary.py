"""Counterfactual queue occupancy, censoring and chronological state boundaries."""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_rule_facts import FEATURES
from yoyo.evaluation.spike_v131_rule_summary import serial_flags,effective,eligibility,cutpoints,infer,four_week_blocks,read,epoch,known


def test_feature_decimals_round_trip_at_observed_quantile_edge(tmp_path):
    value=1.8396688922272746
    f=pd.DataFrame(dict(cohort=['signal']*4,members=[1]*4,
        anchor_time=['2024-01-01T00:00Z']*4))
    for name in FEATURES:f[name]=[0.,value,3.,4.]
    f.to_csv(tmp_path/'15m.csv.gz',index=False)
    q=cutpoints([tmp_path],dict(timeframes=[15],analysis_start='2023-01-01',split='2025-01-01',feature_quantiles=[1/3,2/3]))
    decoded=read(tmp_path/'15m.csv.gz')
    assert decoded.advance20_atr.iloc[1]==value==q['15']['advance20_atr']['lower']
    assert eligibility(decoded,q['15'])['advance20_atr_low'].tolist()==[True,True,False,False]


def rows():
    return pd.DataFrame([
        dict(trade_key='a',anchor_i=0,decision_i=2,exit_i=5,filled=True,status='closed',net_r=1.,
             anchor_time='2024-01-01T00:00Z',decision_time='2024-01-01T02:00Z',mature_time='2024-01-01T05:00Z'),
        dict(trade_key='b',anchor_i=1,decision_i=3,exit_i=8,filled=True,status='closed',net_r=2.,
             anchor_time='2024-01-01T01:00Z',decision_time='2024-01-01T03:00Z',mature_time='2024-01-01T08:00Z'),
        dict(trade_key='c',anchor_i=4,decision_i=5,exit_i=9,filled=True,status='censored_boundary',net_r=np.nan,
             anchor_time='2024-01-01T04:00Z',decision_time='2024-01-01T05:00Z',mature_time='2024-01-01T09:00Z')])


def test_omitting_a_candidate_changes_fresh_occupancy():
    f=rows()
    assert serial_flags(f,np.array([True,True,True])).tolist()==[True,False,True]
    assert serial_flags(f,np.array([False,True,True])).tolist()==[False,True,False]
    order=f.sort_values(['decision_i','anchor_i','trade_key']).index.to_numpy()
    assert serial_flags(f,np.array([False,True,True]),order).tolist()==[False,True,False]


def test_required_censored_is_unknown_but_blocked_hypothetical_is_zero():
    f=rows();e=np.ones(3,bool)
    v,k,m=effective(f,e,serial_flags(f,e))
    assert v[1]==0 and k.tolist()==[True,True,False]
    cached=effective(f,e,serial_flags(f,e),tuple(epoch(f[x]) for x in ['mature_time','decision_time','anchor_time']),f.status.map(known).to_numpy(bool))
    for a,b in zip((v,k,m),cached):np.testing.assert_array_equal(a,b)
    e=np.array([False,True,True]);v,k,m=effective(f,e,serial_flags(f,e))
    assert v.tolist()==[0,2,0] and k.all()
    assert m[0]==int(pd.Timestamp(f.anchor_time[0]).timestamp())
    assert m[2]==int(pd.Timestamp(f.decision_time[2]).timestamp())


def test_feature_ties_and_unknown_never_get_jittered_or_borrow_target_feature():
    f=pd.DataFrame(dict(members=[1]*4,compression_run60=[3,3,7,np.nan],advance20_atr=[1,2,3,np.nan],relative_btc20_atr=[-1,0,1,np.nan]))
    thresholds={name:dict(lower=3,upper=3) for name in FEATURES}
    e=eligibility(f,thresholds)
    assert e['compression_run60_low'].tolist()==[True,True,False,False]
    assert not e['compression_run60_mid'].any()
    assert e['compression_run60_high'].tolist()==[False,False,True,False]


def test_cutpoints_ignore_later_and_control_values(tmp_path):
    f=pd.DataFrame(dict(cohort=['signal']*4+['control'],members=[1]*5,
        anchor_time=['2024-01-01T00:00Z']*3+['2025-02-01T00:00Z','2024-01-01T00:00Z']))
    for name in FEATURES:f[name]=[0,3,6,999,999]
    f.to_csv(tmp_path/'15m.csv.gz',index=False)
    q=cutpoints([tmp_path],dict(timeframes=[15],analysis_start='2023-01-01',split='2025-01-01',feature_quantiles=[1/3,2/3]))
    assert q['15']['advance20_atr']==dict(n=3,lower=2.,upper=4.)


def test_weekly_statistics_respect_replication_unit():
    cfg=dict(seed=4,bootstrap=100)
    b={'a':{'n':2,'x':1},'b':{'n':2,'x':-2},'c':{'n':2,'x':3}}
    doubled={k:{field:value*2 for field,value in r.items()} for k,r in b.items()}
    assert infer(b,'x',cfg)==infer(doubled,'x',cfg)


def test_longer_uncertainty_blocks_do_not_compress_missing_calendar_weeks():
    b={'2022-W44':dict(n=1,x=1),'2022-W45':dict(n=1,x=2),'2022-W48':dict(n=1,x=3)}
    assert four_week_blocks(b)=={0:dict(n=2.,x=3.),1:dict(n=1.,x=3.)}


def test_reuse_rejects_end_anchor_and_partial_streams(tmp_path):
    import json
    from yoyo.evaluation.spike_v131_rule_study import reuse_complete
    from yoyo.evaluation.spike_v131_excursions import provenance
    source=tmp_path/'source.csv';source.write_text('test input')
    prior=tmp_path/'prior';folder=prior/'streams'/'X';folder.mkdir(parents=True)
    cfg=dict(timeframes=[15],end='2026-09-23T04:00:00Z')
    receipt=dict(input=provenance(source),streams=[dict(timeframe_min=15,rows=1)])
    (folder/'receipt.json').write_text(json.dumps(receipt))
    f=pd.DataFrame(dict(cohort=['signal'],anchor_time=[cfg['end']]))
    f.to_csv(folder/'15m.csv.gz',index=False)
    assert reuse_complete(prior,tmp_path/'next',dict(symbol='X',path=str(source)),cfg) is None
    f['anchor_time']='2026-09-23T03:45:00Z';f.to_csv(folder/'15m.csv.gz',index=False)
    assert reuse_complete(prior,tmp_path/'next',dict(symbol='X',path=str(source)),cfg) is not None
    cfg['timeframes']=[15,60]
    assert reuse_complete(prior,tmp_path/'partial',dict(symbol='X',path=str(source)),cfg) is None


def test_independent_exit_interpreter_uses_old_stop_before_new_close_trail():
    from yoyo.evaluation.spike_v131_rule_extremes import exit_trace
    o=np.array([100.,104.,103.]);h=np.array([105.,105.,104.]);l=np.array([99.,100.,100.])
    c=np.array([104.,104.,101.]);a=np.ones(3);g=np.zeros(3,bool);raw=np.zeros(3,int)
    # Bar0 low99 does not touch original98; its close104 only activates stop102 for bar1.
    assert exit_trace(o,h,l,c,a,g,raw,1,100.,98.,0,.1,2)==(1,102.)
    raw[0]=-1
    assert exit_trace(o,h,l,c,a,g,raw,1,100.,98.,0,.1,2)==(1,104.)
    o[1]=97.
    assert exit_trace(o,h,l,c,a,g,raw,1,100.,98.,0,.1,2)==(1,97.)
