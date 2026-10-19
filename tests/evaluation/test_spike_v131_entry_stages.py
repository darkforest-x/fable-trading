"""Decision causality, original stop geometry, and fresh occupancy for entry stages."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation.spike_v131_entry_stages import decision, attempt, mark_serial, paired_frame


def path(side=1):
    o=np.array([100.,101.,103.,102.,105.,106.])
    h=np.array([101.,104.,110.,106.,107.,107.])
    l=np.array([99.,100.,100.5,101.5,104.,105.])
    c=np.array([100.,103.,102.,105.,106.,106.])
    if side==-1:o,h,l,c=200-o,200-l,200-h,200-c
    return o,h,l,c,np.zeros(6,bool),np.zeros(6,int)


@pytest.mark.parametrize('side',[1,-1])
def test_each_stage_stops_before_future_failure_and_preserves_frozen_extreme(side):
    arrays=path(side);stop=98. if side==1 else 102.
    for policy,index in [('immediate',0),('breakout',1),('pullback',2),('rebreak',3)]:
        assert decision(*arrays,0,side,stop,policy)==('confirmed',index)
        assert decision(*[a[:index+1] for a in arrays],0,side,stop,policy)==('confirmed',index)
    arrays[-1][3]=-side
    assert decision(*arrays,0,side,stop,'pullback')==('confirmed',2)
    assert decision(*arrays,0,side,stop,'rebreak')==('cancel_raw_opposite',3)


def test_stages_cannot_collapse_into_the_same_candle():
    a=list(path());a[3][2]=109.
    assert decision(*a,0,1,98.,'pullback')==('confirmed',2)
    assert decision(*a,0,1,98.,'rebreak')==('confirmed',3)


@pytest.mark.parametrize('reason',['gap','opposite','stop','level'])
def test_cancel_priority_over_a_later_stage(reason):
    a=list(path())
    if reason=='gap':a[4][2]=True
    elif reason=='opposite':a[5][2]=-1
    elif reason=='stop':a[2][2]=97.
    else:a[3][2]=100.
    expect=dict(gap='cancel_gap',opposite='cancel_raw_opposite',stop='cancel_stop_touch',level='cancel_lost_level')[reason]
    assert decision(*a,0,1,98.,'pullback')==(expect,2)
    assert decision(*a,0,1,98.,'breakout')==('confirmed',1)


def test_last_allowed_bar_can_confirm_and_incomplete_history_is_unknown():
    a=path()
    assert decision(*a,0,1,98.,'rebreak',limit=3)==('confirmed',3)
    assert decision(*a,0,1,98.,'rebreak',limit=2)==('expired',2)
    assert decision(*[x[:3] for x in a],0,1,98.,'rebreak')==('pending_boundary',2)


def test_new_policy_releases_occupancy_at_its_actual_exit_and_oldest_wins():
    rows=[dict(trade_key='a',decision_i=1,anchor_i=0,filled=True,exit_i=3,status='closed'),
          dict(trade_key='b',decision_i=2,anchor_i=1,filled=True,exit_i=4,status='closed'),
          dict(trade_key='c',decision_i=3,anchor_i=2,filled=True,exit_i=6,status='closed'),
          dict(trade_key='d',decision_i=3,anchor_i=3,filled=True,exit_i=5,status='closed')]
    mark_serial(rows,'taken',10)
    assert [r['trade_key'] for r in rows if r['taken']]==['a','c']
    rows[0]['exit_i']=5
    mark_serial(rows,'taken',10)
    assert [r['trade_key'] for r in rows if r['taken']]==['a']


def test_entry_is_next_open_with_original_stop_and_actual_entry_r():
    idx=pd.date_range('2024-01-01',periods=10,freq='15min',tz='UTC')
    f=pd.DataFrame(dict(open=[100]*5+[101,103,102,105,105],high=[101]*5+[104,110,106,107,106],
        low=[99]*5+[100,100.5,101.5,104,104],close=[100]*5+[103,102,105,106,105],atr=[1.]*10),index=idx)
    p=parent.source.prepared_arm(f,np.zeros(10,bool),np.zeros(10,int),'test',{},15,.01)
    early=attempt(p,4,1,'immediate');late=attempt(p,4,1,'pullback')
    assert early['entry_i']==5 and late['entry_i']==7
    assert early['entry_price']==101 and late['entry_price']==102
    assert early['initial_stop']==late['initial_stop']==98
    assert early['initial_risk']==3 and late['initial_risk']==4
    assert late['status']=='censored_boundary'


def test_pairs_keep_no_fill_zero_but_not_missing_and_use_skipped_decision_maturity():
    rows=[]
    for key in ['ok','unknown']:
        for cohort in ['signal','control']:
            for policy in ['immediate','breakout','pullback','rebreak']:
                rows.append(dict(trade_key=key,cohort=cohort,policy=policy,pair_eligible=True,
                    status='closed',filled=True,net_r=2.,serial_pair=True,side=1,
                    anchor_time=pd.Timestamp('2024-01-01',tz='UTC'),
                    decision_time=pd.Timestamp('2024-01-02',tz='UTC'),
                    mature_time=pd.Timestamp('2024-01-03',tz='UTC')))
    rows[4].update(status='expired',filled=False,net_r=None,serial_pair=False)
    rows[8].update(status='censored_boundary',serial_pair=False,mature_time=pd.Timestamp('2026-01-01',tz='UTC'))
    f=pd.DataFrame(rows)
    independent=paired_frame(f,False);serial=paired_frame(f,True)
    assert list(independent.index)==['ok']
    assert independent.loc['ok','control_immediate']==0.
    assert serial.loc['unknown','signal_immediate']==0.
    assert serial.loc['unknown','last']==pd.Timestamp('2024-01-03',tz='UTC')
