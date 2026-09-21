"""Causal waiting and ambiguous exit-bar regression cases."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_5r_followthrough import path_bounds, initial_at, confirmation_masks
from yoyo.evaluation.spike_exit_policy_study import ExecutionSpec


def prepared():
    index=pd.date_range('2025-01-01',periods=10,freq='h',tz='UTC')
    f=pd.DataFrame(dict(open=[100.]*10,high=[101.]*10,low=[99.]*10,close=[100.]*10,atr=[1.]*10),index=index)
    for c in ['s20','e20','s60','e60','s120','e120']:f[c]=100.
    return SimpleNamespace(frame=f,open=f.open.to_numpy(),high=f.high.to_numpy(),low=f.low.to_numpy(),close=f.close.to_numpy(),atr=f.atr.to_numpy(),gap=np.zeros(10,bool),raw_side=np.zeros(10,int),spec=ExecutionSpec(tick=.01))


def test_delayed_entry_reprices_r_without_moving_stop():
    p=prepared();p.open[6]=104
    base=initial_at(p,4,0);late=initial_at(p,4,1)
    assert late['initial_stop']==base['initial_stop']==98
    assert late['entry_price']==104 and late['initial_risk']==6
    p.low[5]=97
    assert initial_at(p,4,1) is None


def test_delayed_cancel_on_reverse_or_gap():
    p=prepared();p.raw_side[5]=-1
    assert initial_at(p,4,1) is None
    p.raw_side[5]=0;p.gap[6]=True
    assert initial_at(p,4,1) is None


def test_exit_bar_high_is_unknown_before_intrabar_stop():
    p=prepared();p.frame.loc[p.frame.index[6],'high']=120
    row=dict(valid_entry=True,censored=False,entry_time=p.frame.index[5],exit_time=p.frame.index[6],entry_price=100,initial_risk=2,exit_price=98,exit_reason='initial_stop',net_r=-1.1)
    r=path_bounds(p.frame,row)
    assert r['path_group']=='exit_bar_ambiguous'
    assert r['net_peak_lower']<5<r['net_peak_upper']
    row['exit_reason']='opposite_v6_next_open';row['exit_price']=100
    assert path_bounds(p.frame,row)['path_group']=='never_observed_gt5'
    row['censored']=True
    assert path_bounds(p.frame,row)['path_group']=='unknown'


def test_confirmation_known_only_after_wait_and_ignores_later_bars():
    p=prepared();p.close[5]=101;p.close[6]=102
    before=confirmation_masks(p)['confirm2'][4]
    p.close[7:]=1000;p.low[7:]=500
    assert confirmation_masks(p)['confirm2'][4]==before
    assert before


def test_confirmation_requires_all_six_original_mas():
    p=prepared();p.close[5]=101;p.close[6]=102
    p.frame.loc[p.frame.index[4],'s120']=np.nan
    masks=confirmation_masks(p)
    assert not any(masks[a][4] for a in ('confirm1','confirm2','retest2'))
    assert masks['wait1'][4] and masks['wait2'][4]


def test_entry_bar_future_low_and_reverse_do_not_cancel_wait():
    p=prepared()
    baseline=initial_at(p,4,1)
    p.low[6]=1.;p.raw_side[6]=-1
    late=initial_at(p,4,1)
    assert late is not None and late['entry_time']==baseline['entry_time']
    assert late['initial_risk']==baseline['initial_risk']
