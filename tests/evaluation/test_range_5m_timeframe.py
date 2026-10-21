"""Five-minute clocks for admissions, Monday snapshots and native 2/2 exits."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.monday_range_v2_31_reference import select_matches
from yoyo.evaluation.range_v230_exit_compare import entry_keys
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, confirmed_pivots, run_native


@pytest.mark.parametrize('touch,entry',[(3,5),(4,5),(5,5),(6,6),(7,7)])
def test_five_minute_signal_touch_and_entry_timestamps(touch,entry):
    q=pd.DataFrame([[103.5,104.3,103.,104.]]*9,columns=['open','high','low','close'],
                   index=pd.date_range('2026-10-20T12:00Z',periods=9,freq='5min'))
    q.iloc[touch,q.columns.get_loc('high')]=106.
    a=np.array([0,0,0,0,0,-1,0,0,0]);p=np.tile([100.,110.,105.],(9,1));w=np.array(['2026-10-19']*9)
    kwargs=dict(bar_minutes=5,buffer=0)
    rows=select_matches(q,a,p,['0:up','0:down','0.5:up'],w,**kwargs)
    assert rows[0]['entry_i']==entry
    assert pd.Timestamp(rows[0]['touch_time'])==q.index[touch]-pd.Timedelta(minutes=5)
    assert pd.Timestamp(rows[0]['signal_time'])==q.index[5]-pd.Timedelta(minutes=5)
    assert pd.Timestamp(rows[0]['entry_time'])==q.index[entry]
    for end in range(1,10):
        got=select_matches(q.iloc[:end],a[:end],p[:end],['0:up','0:down','0.5:up'],w[:end],**kwargs)
        assert got==[r for r in rows if r['entry_i']<end]


def test_monday_snapshot_and_entry_close_use_five_minute_open_clock():
    q=pd.DataFrame([[100,102,98,101],[101,104,97,103],[103,106,96,105],[105,108,95,107]],
        columns=['open','high','low','close'],
        index=pd.date_range('2026-10-19T04:00Z',periods=4,freq='5min'))
    p,n,w,_=monday_levels(q,[0,.5],'America/New_York',bar_minutes=5)
    assert np.isnan(p[0]).all() and np.isnan(p[1]).all()
    assert p[2,:2].tolist()==[97.,104.] and p[3,:2].tolist()==[96.,106.]
    assert w[0] is None and w[1]=='2026-10-19'
    base=pd.DataFrame([dict(trade_id='s',entry_i=1)])
    keys=entry_keys(q,base,[0,.5],'America/New_York',bar_minutes=5)
    assert keys['s'][:2].tolist()==[97.,104.]
    earlier=entry_keys(q.iloc[:2],base,[0,.5],'America/New_York',bar_minutes=5)
    np.testing.assert_array_equal(keys['s'],earlier['s'])


BASE=[[100,102,99,100],[100,106,99,105],[105,110,104,108],
      [108,109,103,105],[105,107,102,104],[104,106,101,103]]


def execute(side=1,stop_hit=False):
    bars=[b.copy() for b in BASE]
    if stop_hit:bars[4][2]=97
    if side==-1:bars=[[200-o,200-l,200-h,200-c] for o,h,l,c in bars]
    q=pd.DataFrame(bars,columns=['open','high','low','close'],
        index=pd.date_range('2026-01-01T00:05Z',periods=len(bars),freq='5min'))
    e=pd.DataFrame([dict(trade_id='s',entry_i=0,side=side,entry=100.,stop=100-side*2,risk=2.,end_i=5)])
    rows,fills,audit=run_native(q,np.ones(6),np.zeros(6),e,
        ExitPolicy(confirmed_pivot_exit=True,opposite_hour_exit=False,pivot_minutes=5),tick=1.,symbol='TEST')
    return q,rows.iloc[0],audit


@pytest.mark.parametrize('side',[1,-1])
def test_native_five_minute_pivot_fills_at_second_confirmation_close(side):
    q,r,audit=execute(side)
    assert r.reason==7 and r.exit_i==4
    assert r.exit_price==100+side*4 and r.pivot_price==100+side*10
    assert pd.Timestamp(r.pivot_open_time)==q.index[2]-pd.Timedelta(minutes=5)
    assert r.gross_r==2 and audit['pivot_execution'].startswith('5m ')
    full=confirmed_pivots(q,pivot_minutes=5)
    for end in range(1,7):
        for actual,expected in zip(confirmed_pivots(q.iloc[:end],pivot_minutes=5),full):
            np.testing.assert_array_equal(actual,expected[:end])


def test_five_minute_stop_wins_on_confirmation_bar():
    _,r,_=execute(stop_hit=True)
    assert r.reason==1 and r.exit_i==4 and r.exit_price==98


def test_reject_pivot_timeframe_without_pivot_policy():
    with pytest.raises(ValueError):ExitPolicy(pivot_minutes=5)
    with pytest.raises(ValueError):ExitPolicy(confirmed_pivot_exit=True,opposite_hour_exit=False,pivot_minutes=10)
