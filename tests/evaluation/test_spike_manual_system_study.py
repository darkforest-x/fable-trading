"""Causal clocks, serial occupancy, and control constraints for the manual study."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_manual_system_study as study


def test_higher_slope_uses_only_hours_closed_before_chart_open():
    index=pd.date_range('2024-01-01',periods=1600,freq='5min',tz='UTC')
    close=100+np.arange(len(index))*.01
    base=pd.DataFrame({'open':close,'high':close+1,'low':close-1,'close':close,'volume':100.},index=index)
    chart=index[1000:1100]
    before=study.higher_features(base,chart,15)
    altered=base.copy();altered.loc[altered.index>=chart[-1].floor('1h'),'close']+=100
    altered.loc[altered.index>=chart[-1].floor('1h'),'high']+=100
    after=study.higher_features(altered,chart,15)
    # The final chart bar still cannot see its currently developing hour.
    pd.testing.assert_frame_equal(before,after)
    assert (before.htf_close_time<=before.index).all()
    assert before.htf_slope.gt(0).all()


def event(i,allowed=True,in_window=True):
    return dict(signal_i=i,side=1,symbol='BTCUSDT',timeframe_min=5,arm='ordinary',
        signal_close=str(i),in_window=in_window,htf_slope_aligned=allowed)


def outcome(exit_i,censored=False):
    return ('censored_boundary' if censored else 'closed',dict(exit_i=exit_i,censored=censored))


def test_each_policy_replays_its_own_occupancy_and_warmup():
    events=[event(0,False,False),event(2),event(4),event(6)]
    results={}
    for ev in events:
        i=ev['signal_i'];results[(i,1,'baseline')]=outcome(i+5);results[(i,1,'take_4r')]=outcome(i+2)
    baseline,status=study.choose_serial(events,results,'baseline',20)
    slope,_=study.choose_serial(events,results,'htf_slope',20)
    target,_=study.choose_serial(events,results,'take_4r',20)
    assert [x['signal_i'] for x in baseline]==[6]
    assert status[0]['status']=='skipped_in_position'
    assert [x['signal_i'] for x in slope]==[2]
    assert [x['signal_i'] for x in target]==[2,4,6]


def test_unclosed_position_blocks_all_later_entries():
    events=[event(0),event(2)]
    results={(0,1,'baseline'):outcome(3,True),(2,1,'baseline'):outcome(3)}
    trades,status=study.choose_serial(events,results,'baseline',4)
    assert len(trades)==1 and status[-1]['status']=='skipped_in_position'


def test_time_split_purges_crossing_outcomes():
    entry=pd.to_datetime(['2024-01-01','2024-01-01','2025-01-01'],utc=True)
    exit_=pd.to_datetime(['2024-02-01','2025-02-01','2025-02-01'],utc=True)
    assert study.fold_of(entry,exit_,pd.Timestamp('2025-01-01',tz='UTC')).tolist()==['earlier','cross_split','later']


def test_slope_control_obeys_gate_side_week_and_volatility_without_redraw():
    index=pd.date_range('2025-02-03',periods=8,freq='5min',tz='UTC')
    prepared=SimpleNamespace(frame=pd.DataFrame({'ready':True},index=index),context=SimpleNamespace(minutes=5),
        gap=np.zeros(8,bool),atr=np.ones(8),close=np.full(8,100.))
    hf=pd.DataFrame({'htf_slope':[-1,1,-1,1,-1,1,-1,1]})
    trades=[dict(signal_i=0,side=-1,policy='htf_slope',trade_key='t',symbol='BTCUSDT',arm='ordinary',censored=False,net_return=.01)]
    cfg=dict(start='2025-02-03T00:00Z',end='2025-02-03T00:40Z',split='2025-02-03T00:20Z',vol_bins=[.005,.02],control_seed=1)
    calls=[]
    def evaluate(i,side,policy):
        calls.append((i,side,policy));return 'closed',dict(exit_time=index[i+1],net_return=.002,net_r=.1)
    rows=study.controls(prepared,trades,hf,cfg,evaluate)
    assert calls==[(2,-1,'baseline')]
    assert rows.iloc[0].matched and abs(rows.iloc[0].excess_bp-80)<1e-9
