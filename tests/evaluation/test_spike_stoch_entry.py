"""Causal waiting, fixed-risk geometry and actual-exit excursion regressions."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_stoch_entry import timed_decision, timed_attempt, excursions
from yoyo.evaluation.spike_v131_entry_stages import parent, attempt


def path():
    return SimpleNamespace(high=np.array([101.,104.,110.,106.,107.,108.]),
        low=np.array([99.,100.,100.5,101.5,104.,105.]),
        close=np.array([100.,103.,102.,105.,106.,106.]),
        gap=np.zeros(6,bool),raw_side=np.zeros(6,int))


def test_trigger_before_pullback_does_not_count_and_same_pullback_bar_can_enter():
    p=path();t=np.array([True,True,False,False,True,False])
    assert timed_decision(p,0,98.,t)==('confirmed',4)
    t[2]=True
    assert timed_decision(p,0,98.,t)==('confirmed',2)
    # A future opposite cannot revoke that known decision.
    p.raw_side[3]=-1
    assert timed_decision(p,0,98.,t)==('confirmed',2)


@pytest.mark.parametrize('reason',['gap','opposite','stop','level'])
def test_failure_wins_over_a_same_bar_cross(reason):
    p=path();t=np.ones(6,bool)
    if reason=='gap':p.gap[2]=True
    elif reason=='opposite':p.raw_side[2]=-1
    elif reason=='stop':p.low[2]=98.
    else:p.close[2]=101.
    expected=dict(gap='cancel_gap',opposite='cancel_raw_opposite',stop='cancel_stop_touch',level='cancel_lost_level')
    assert timed_decision(p,0,98.,t)==(expected[reason],2)


def test_no_cross_expires_and_wvf_must_match_the_cross_bar():
    p=path();arrow=np.array([False,False,True,False,False,True])
    green=np.array([False,True,False,True,False,True])
    assert timed_decision(p,0,98.,arrow,limit=4)==('confirmed',2)
    assert timed_decision(p,0,98.,arrow & green,limit=4)==('expired',4)
    assert timed_decision(p,0,98.,arrow & green)==('confirmed',5)
    assert timed_decision(p,0,98.,np.zeros(6,bool))==('pending_boundary',5)


def test_prefix_decision_and_distinct_breakout_retest_bars():
    p=path();t=np.ones(6,bool)
    assert timed_decision(p,0,98.,t)==('confirmed',2)
    q=SimpleNamespace(**{k:v[:3] for k,v in vars(p).items()})
    assert timed_decision(q,0,98.,t[:3])==('confirmed',2)


def test_timed_entry_preserves_anchor_stop_and_next_open_price():
    idx=pd.date_range('2024-01-01',periods=12,freq='15min',tz='UTC')
    f=pd.DataFrame(dict(open=[100]*5+[101,103,102,105,105,105,97],
        high=[101]*5+[104,110,106,107,106,106,99],
        low=[99]*5+[100,100.5,101.5,104,104,104,96],
        close=[100]*5+[103,102,105,106,105,105,98],atr=[1.]*12),index=idx)
    p=parent.source.prepared_arm(f,np.zeros(12,bool),np.zeros(12,int),'test',{},15,.01)
    t=np.zeros(12,bool);t[8]=True
    base=attempt(p,4,1,'pullback');r=timed_attempt(p,4,'pullback_stoch',t)
    assert base['entry_i']==7 and r['entry_i']==9
    assert base['initial_stop']==r['initial_stop']==98
    assert r['entry_price']==105 and r['initial_risk']==7
    assert r['status']=='closed'
    z=excursions(p,r)
    assert z['mae_r']==pytest.approx(8/7)
    assert z['mae_bp']==pytest.approx(8/105*10000)
    assert z['mae_anchor_r']==pytest.approx(8/3)


def test_pair_denominator_retains_wvf_rejections_and_excludes_required_unknowns():
    from yoyo.evaluation.spike_stoch_entry_summary import pairs
    rows=[]
    for key in ['closed','unknown','skipped']:
        for policy in ['stoch','stoch_wvf']:
            for cohort in ['signal','control']:
                rows.append(dict(trade_key=key,policy=policy,cohort=cohort,eligible=policy=='stoch',
                    pair_eligible=True,serial_pair=True,filled=True,status='closed',net_r=2.,
                    anchor_time='2024-01-01T00:00:00Z',decision_time='2024-01-01T00:00:00Z',
                    mature_time='2024-01-02T00:00:00Z',symbol='TEST',timeframe_min=15))
    rows[4].update(status='censored_boundary',net_r=np.nan)
    rows[8].update(status='censored_boundary',net_r=np.nan,serial_pair=False)
    # Ineligible arms must never be marked taken by the caller.
    for r in rows:
        if not r['eligible']:r['serial_pair']=False
    p=pairs(pd.DataFrame(rows),('stoch','stoch_wvf'),'primary')
    assert set(p.index)=={'closed','skipped'}
    assert p.loc['closed','signal_stoch_wvf']==0
    assert p.loc['skipped','signal_stoch']==0
