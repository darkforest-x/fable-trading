"""Causal turn clocks and native same-entry-candle protection."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_v237_langlang_confirmation import (
    confirmation, opening_frame, open_entries, audit_open_exits,
)


def candles():
    # Candle3 already confirms, but is still within the three holding bars.
    return np.array([[100,103,99,101], [101,103,100,102], [102,103,100,101],
        [101,105,100,104], [104,107,101,106], [108,109,102,105],
        [105,108,103,107], [107,110,104,108]], dtype=float)


def mirror(b):
    return 250-b[:,[0,2,1,3]]


@pytest.mark.parametrize('side',[1,-1])
def test_wait_three_then_confirm_and_use_next_open(side):
    b=candles() if side==1 else mirror(candles())
    r=confirmation(b,0,0,side,.1,hold_bars=2)
    assert r['status']=='filled' and r['turn_i']==4 and r['decision_i']==4
    assert r['open_i']==5 and r['end_bar_i']==6
    assert r['entry']==b[5,0] and r['entry']!=b[4,3]
    assert r['stop']==b[0,2 if side==1 else 1]


def test_early_turn_waits_for_later_pair_and_does_not_backdate():
    r=confirmation(candles(),0,6,1,.1,hold_bars=1)
    assert r['turn_i']==4 and r['decision_i']==6 and r['open_i']==7


@pytest.mark.parametrize('where',[1,3,4,5])
def test_extreme_break_cancels_even_when_turn_precedes_publication(where):
    b=candles(); b[where,2]=98.9
    r=confirmation(b,0,6,1,.1,hold_bars=1)
    assert r['status']=='extreme_broken' and r['invalid_i']==where


def test_waiting_touch_allowed_and_open_at_stop_cancels():
    b=candles(); b[2,2]=99
    assert confirmation(b,0,0,1,.1,hold_bars=1)['status']=='filled'
    b[5,0]=99
    assert confirmation(b,0,0,1,.1,hold_bars=1)['status']=='invalid_open_risk'


def test_entry_bar_extrema_and_future_cannot_change_admission():
    b=candles(); a=confirmation(b,0,0,1,.1,hold_bars=1)
    b[5,1:]=[1000,1,99]; b[6:]=[100,1000,1,1]
    assert confirmation(b,0,0,1,.1,hold_bars=1)==a
    assert confirmation(b[:6],0,0,1,.1,hold_bars=1)==a


def test_boundary_censored_separately():
    r=confirmation(candles(),0,0,1,.1,hold_bars=24)
    assert r['status']=='incomplete_horizon' and r['decision_i']==4


def native_case(side, kind):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    b=np.tile([100.,102.,99.,101.],(27,1))
    b[1]=[103,105,101,104]
    if kind=='entry_stop':b[1]=[103,105,94,104]
    if kind=='gap_stop':b[2]=[93,100,90,98]
    if side==-1:b=mirror(b)
    frame=pd.DataFrame(b,columns=['open','high','low','close'],
        index=pd.date_range('2026-01-01 00:05',periods=len(b),freq='5min',tz='UTC'))
    stop=95 if side==1 else 155
    e=pd.DataFrame([dict(trade_id='test',open_i=1,end_bar_i=24,entry=b[1,0],
        side=side,stop=stop,risk=side*(b[1,0]-stop))])
    expanded=opening_frame(frame);zeros=np.zeros(len(expanded))
    rows,fills,_=run_native(expanded,zeros,zeros,open_entries(e),
        ExitPolicy(target_from_entry=True,opposite_hour_exit=False),tick=.1,symbol='TEST')
    audit_open_exits(b,e,rows,.1)
    assert len(fills)==2 and rows.iloc[0].entry==b[1,0]
    assert fills.iloc[0].ts_ns==expanded.index[2].value
    r=rows.iloc[0]
    assert r.net_r==pytest.approx(r.gross_r-.002*r.entry/r.risk)
    if kind=='entry_stop':
        assert r.exit_i==3 and r.reason==1 and r.gross_r==pytest.approx(-1)
    elif kind=='gap_stop':
        assert r.exit_i==4 and r.reason==1 and r.exit_price==b[2,0]
    else:
        assert r.exit_i==49 and r.reason==0
        actual_open=frame.index[1]-pd.Timedelta(minutes=5)
        assert frame.index[24]-actual_open==pd.Timedelta(hours=2)


@pytest.mark.parametrize('side',[1,-1])
@pytest.mark.parametrize('kind',['entry_stop','gap_stop','deadline'])
def test_native_observed_open_and_protection(side,kind):
    native_case(side,kind)
