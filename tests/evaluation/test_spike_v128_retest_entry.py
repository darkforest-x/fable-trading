"""Behavioral checks on delayed fills, unchanged exits and single-draw controls."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import spike_v128_retest_entry as study

CFG={'max_wait_bars':24,'diagnostic_wait_bars':3}

def prepared():
    idx=pd.date_range('2026-01-01',periods=12,freq='h',tz='UTC')
    frame=pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'atr':1.,'ready':True},index=idx)
    # anchor 4 high=101, breakout 5 -> retest 6 -> rebreak 7; fill 8 at 107.
    frame.iloc[5,:4]=[100,104,100,103]
    frame.iloc[6,:4]=[103,103,101,102]
    frame.iloc[7,:4]=[102,106,102,105]
    frame.iloc[8,:4]=[107,108,106,107]
    frame.iloc[9,:4]=[107,108,97,99]
    return study.parent.source.prepared_arm(frame,np.zeros(len(frame),bool),np.zeros(len(frame),int),
        'binance_um:X:60m',{'venue':'binance_um','symbol':'X','asset':'X','timeframe_min':60},60,.1)


def test_delayed_fill_uses_actual_open_old_stop_and_new_risk():
    p=prepared();status,r,tr=study.delayed_attempt(p,4,1,CFG)
    _,old=study.parent.attempt(p,4,1)
    assert status=='closed' and tr['confirmation_i']==7 and r['entry_i']==8
    assert r['entry_price']==107 and r['signal_i']==7
    assert r['initial_stop']==old['initial_stop']==98
    assert r['initial_risk']==9 and r['initial_risk']!=old['initial_risk']
    assert r['exit_reason']=='initial_stop' and r['close_peak_r']<2
    assert r['net_r_anchor']==pytest.approx(r['net_return']*107/2)
    assert r['chase_anchor_r']==3.5 and r['risk_ratio']==4.5


def test_confirmation_has_no_next_fill_or_gap_fill():
    p=prepared();p.gap[8]=True
    status,r,t=study.delayed_attempt(p,4,1,CFG)
    assert status=='next_bar_is_gap' and r is None and t['confirmation_status']=='confirmed'


def test_wait3_is_time_only_but_cancels_stop():
    p=prepared();p.close[5]=100
    st,r,t=study.delayed_attempt(p,4,1,CFG,'wait3')
    assert r is not None and r['entry_i']==8
    p.low[6]=97
    st,r,t=study.delayed_attempt(p,4,1,CFG,'wait3')
    assert st=='cancel_stop' and r is None and t['decision_i']==6


def event(anchor,confirm,end,key):
    return {'trade_key':key,'policy':'retest','anchor_i':anchor,'signal_i':confirm,'exit_i':end,'status':'closed'}


def test_serial_orders_confirmation_not_anchor_and_preserves_carry():
    rows=[event(1,9,15,'late'),event(2,5,8,'early'),event(3,5,7,'tie'),event(4,8,10,'freed')]
    trades,states=study.select_serial(rows,'retest',20,(5,'carry'))
    assert [r['trade_key'] for r in trades]==['early','freed']
    assert states[1]['blocking_trade']=='early' and states[-1]['blocking_trade']=='freed'
    t,s=study.select_serial(rows,'retest',20,(6,'carry'))
    assert s[0]['blocking_trade']==s[1]['blocking_trade']=='carry'


def test_failed_confirmation_control_is_not_redrawn(monkeypatch):
    p=prepared();calls=[]
    t={'trade_key':'x','policy':'baseline','signal_i':4,'side':1,'arm':'joint','symbol':'X','timeframe_min':60,
       'status':'closed','exit_i':9,'censored':False,'net_return':.02,'decision_time':p.frame.index[4]}
    rows=[t]+[t|{'policy':pol} for pol in ('wait3','retest')]
    original=pd.DataFrame([{'trade_key':'x','control_sig':6,'side':1,'matched':True,'reason':'matched',
                           'control_signal_close':p.frame.index[6]}])
    monkeypatch.setattr(study.parent,'matched_controls',lambda *a:original)
    monkeypatch.setattr(study.parent,'attempt',lambda *a: ('expired',None))
    def delayed(p,i,side,cfg,policy):
        calls.append((i,policy));return 'expired',None,{'decision_i':9,'confirmation_i':None}
    monkeypatch.setattr(study,'delayed_attempt',delayed)
    controls=study.controls_for(p,rows,CFG)
    assert calls==[(6,'wait3'),(6,'retest')]
    assert controls.loc[controls.control_pool=='retest','control_status'].item()=='expired'
    assert not controls.loc[controls.control_pool=='retest','matched'].item()


def test_controls_include_unfilled_anchor(monkeypatch):
    p=prepared();seen=[]
    rows=[{'trade_key':'nofill','policy':policy,'signal_i':4,'side':1,'arm':'joint','symbol':'X',
           'status':'risk_invalid','decision_time':p.frame.index[4]} for policy in study.POLICIES]
    def sample(p,targets,cfg):
        seen.extend(targets)
        return pd.DataFrame([{'trade_key':'nofill','control_sig':6,'side':1,'matched':True,'reason':'matched',
                              'control_signal_close':p.frame.index[6]}])
    monkeypatch.setattr(study.parent,'matched_controls',sample)
    monkeypatch.setattr(study.parent,'attempt',lambda *a:('closed',{}))
    monkeypatch.setattr(study,'delayed_attempt',lambda *a:('expired',None,{'decision_i':9,'confirmation_i':None}))
    c=study.controls_for(p,rows,CFG)
    assert len(seen)==1 and seen[0]['net_return']==0 and seen[0]['censored'] is False
    assert c.control_pool.tolist()==list(study.POLICIES) and not c.matched.any()


def test_execution_metadata_cannot_overwrite_policy_arm_or_event_key():
    event={'policy':'retest','arm':'joint','trade_key':'original-anchor','signal_i':7,'status':'closed'}
    raw={'policy':'baseline','arm':'v8','trade_key':'engine-fixed','signal_i':7,'close_peak_r':3.,'net_r':2.}
    result=study.attach_execution(event,raw)
    assert result['policy']=='retest' and result['arm']=='joint' and result['trade_key']=='original-anchor'
    assert result['net_r']==2 and result['trail_armed']
