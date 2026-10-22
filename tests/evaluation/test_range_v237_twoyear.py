"""Pairing, publication and descriptive-outcome boundaries, not fitted returns."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import range_v237_twoyear as m

UNIT=300_000_000_000


@pytest.mark.parametrize('offset,expected',[(-6,0),(-5,1),(0,1),(5,1),(6,0)])
def test_window_is_five_minute_opens(offset,expected):
    rows=m.pair_positions(np.array([9]),np.array([-1]),np.array([10*UNIT]),np.array([13*UNIT]),
        np.array([(10+offset)*UNIT]),np.array([(11+offset)*UNIT]),np.array([-1]))
    assert len(rows)==expected
    if expected:
        assert rows[0]['publication_ns']==max(13,11+offset)*UNIT


def test_same_side_earliest_match_and_one_per_primary():
    rows=m.pair_positions(np.array([7,8]),np.array([1,1]),np.array([10,11])*UNIT,np.array([13,14])*UNIT,
        np.array([5,6,9,10])*UNIT,np.array([6,7,10,11])*UNIT,np.array([-1,1,1,1]))
    assert len(rows)==2 and [r['matched_ns'] for r in rows]==[6*UNIT,6*UNIT]


def test_reference_and_causal_stop_use_their_own_close_only():
    dates=pd.date_range('2026-01-01',periods=15,freq='5min',tz='UTC')
    f=pd.DataFrame(dict(open=100.,high=102.,low=98.,close=100.),index=dates)
    f.iloc[8]=[103,105,101,104]
    ev=pd.DataFrame([dict(original_i=5,side=-1,publication_ns=dates[8].value)])
    ref=m.project_entries(f,ev,.1,'reference').iloc[0]
    live=m.project_entries(f,ev,.1,'causal').iloc[0]
    assert ref.entry_i==5 and ref.entry==100 and abs(ref.stop-102.1)<1e-8
    assert live.entry_i==8 and live.entry==104 and abs(live.stop-105.1)<1e-8
    f.iloc[9:]=[150,160,140,150]
    pd.testing.assert_series_equal(live,m.project_entries(f,ev,.1,'causal').iloc[0])


def test_boundary_marks_not_wins_and_losing_stop_is_explicit():
    f=pd.DataFrame(dict(reason=[0,1,1,2,3],net_r=[99,-1.2,-1.2,.8,-.1],gross_r=[100,-1,-1,1,.1],
        net_notional_bp=[99,-10,-10,2,-1],peak_r=[101,0,2,6,1],fee_r=[1,.2,.2,.2,.2],
        entry_i=[0]*5,exit_i=[1]*5))
    r=m.metrics(f)
    assert r['n']==5 and r['closed']==4 and r['open']==1
    assert r['net_wins']==1 and r['gross_wins']==2
    assert r['positive_then_initial_sl']==1 and r['mfe1_then_losing_stop']==1
    assert r['positive_then_losing_stop']==1 and r['boundary_net_r']==99


def test_core_lane_has_hour_gate_only_for_fifteen(monkeypatch):
    dates=pd.date_range('2026-01-01',periods=60,freq='5min',tz='UTC')+pd.Timedelta(minutes=5)
    f=pd.DataFrame(dict(open=100.,high=102.,low=98.,close=100.),index=dates)
    def arrows(frame):
        a=np.zeros(len(frame),int)
        a[30 if len(frame)==60 else 10]=-1
        return pd.Series(a,index=frame.index)
    monkeypatch.setattr(m,'signals',arrows)
    monkeypatch.setattr(m,'prepare',lambda frame:dict(signal=np.zeros(len(frame),int),observed=np.full(len(frame),-1,int)))
    e=m.events(f,dates[0],dates[-1],.1)
    assert e.lane.tolist()==[5]
    assert e.publication_ns.iloc[0]==dates[32].value
