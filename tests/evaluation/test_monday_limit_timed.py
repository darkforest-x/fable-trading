"""Causal standing limits, native mixed fees and unknown fill-clock bounds."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.monday_timed_engine import first_limits,account
from yoyo.evaluation.monday_limit_timed import event_prices,sample_entries,pending_windows

CFG=dict(holding_minutes=[5,15,60,240],controls_per_signal=3,seed=20261023,maker_fee=.0002,taker_fee=.0005)


def frame(start='2024-01-02 05:00',periods=60):
    ix=pd.date_range(start,periods=periods,freq='5min',tz='UTC')
    return pd.DataFrame(dict(open=100.,high=102.,low=98.,close=101.,count=5,quality_segment=0),index=ix)


def entry(**kw):
    r=dict(parent_id='a',pair_id='a',symbol='BTCUSDT',population='signal',side=1,level=99.,entry=99.,entry_i=0,week='2024-01-01',bucket=-20)
    r.update(kw);return pd.DataFrame([r])


def test_native_first_crossing_gap_and_unfilled():
    p=np.array([[100,102,98,101],[95,98,94,97],[104,105,102,103]],float)
    x=first_limits(p,np.array([0,0,0,0]),np.array([3]*4),np.array([99.,96.,103.,90.]),np.array([1,1,-1,1]))
    np.testing.assert_allclose(x[:3],[[0,99],[1,95],[2,104]])
    assert np.isnan(x[3]).all()


def test_bounds_do_not_claim_grid_is_exact_elapsed_time():
    f=frame();events,c=event_prices(f,None,entry(),CFG)
    x=events[events.horizon.eq(5)].set_index('scenario')
    assert x.loc['grid','exit_price']==100
    assert x.loc['lower','exit_price']==98 and x.loc['upper','exit_price']==102
    assert not x.actual_duration_exact.any()
    assert x.grid_hold_min_minutes.eq(0).all() and x.grid_hold_max_minutes.eq(5).all()
    assert c.common_horizons.all()


@pytest.mark.parametrize('side,level',[(1,101.),(-1,99.)])
def test_opening_fill_has_exact_clock_and_collapsed_bounds(side,level):
    events,_=event_prices(frame(),None,entry(side=side,level=level,entry=100),CFG)
    assert events.actual_duration_exact.all()
    assert events.groupby('horizon').exit_price.nunique().eq(1).all()
    rows,fills=account(events,'BTCUSDT',CFG)
    assert len(fills)==8
    assert (rows.net_bp<0).all()  # same entry/exit, both actual fees charged
    np.testing.assert_allclose(rows.net_bp,-7.)


def test_short_profit_bounds_reverse_prices():
    events,_=event_prices(frame(),None,entry(side=-1,level=101.,entry=101.),CFG)
    rows,_=account(events,'BTCUSDT',CFG)
    x=rows.pivot(index='horizon',columns='scenario',values='net_bp')
    assert (x.lower<=x.grid).all() and (x.grid<=x.upper).all()
    assert events[events.scenario.eq('lower')].exit_price.eq(102.).all()


def test_no_stop_even_large_loss_and_actual_exit_notional_fee():
    f=frame();f.iloc[1,f.columns.get_indexer(['open','high','low','close'])]=[50,51,49,50]
    events,_=event_prices(f,None,entry(),CFG);rows,fills=account(events,'BTCUSDT',CFG)
    r=rows[rows.horizon.eq(5)&rows.scenario.eq('grid')].iloc[0]
    assert r.exit_price==50
    assert r.net_bp==pytest.approx(((50-99)-99*.0002-50*.0005)/99*1e4)
    assert len(fills)>0


def test_spot_is_gross_only():
    events,_=event_prices(frame(),None,entry(),CFG);rows,_=account(events,'XAUUSD',CFG)
    assert rows.net_bp.isna().all() and rows.fee_bp.isna().all()
    assert rows.gross_bp.notna().all()


def test_missing_expiry_keeps_other_horizons_but_excludes_common_cohort():
    f=frame().drop(frame().index[12]);events,c=event_prices(f,None,entry(),CFG)
    assert set(events.horizon)=={5,15,240}
    assert not events.common_horizons.any() and not c.common_horizons.any()


def test_refine_minute_timing_without_interpolation():
    f=frame();fine=pd.DataFrame(dict(open=100.,high=102.,low=99.5,close=101.),index=pd.date_range(f.index[0],periods=300,freq='min'))
    fine.loc[fine.index.minute%5==4,'low']=98.
    events,c=event_prices(f,fine,entry(),CFG)
    assert c.refinement.iloc[0]=='verified_1m'
    assert events.entry_time.eq(str(f.index[0]+pd.Timedelta(minutes=4))).all()
    assert events.entry_resolution_minutes.eq(1).all()
    x=events[events.horizon.eq(5)].iloc[0]
    assert x.grid_hold_min_minutes==4 and not x.actual_duration_exact


def test_disagreeing_minute_source_does_not_silently_replace_registered_prices():
    f=frame();fine=pd.DataFrame(dict(open=200.,high=202.,low=198.,close=201.),index=pd.date_range(f.index[0],periods=300,freq='min'))
    events,c=event_prices(f,fine,entry(),CFG)
    assert events.entry_resolution_minutes.eq(5).all()
    assert c.refinement.iloc[0]=='missing_or_disagreeing_entry_window'


def monday_week():
    f=frame('2024-01-01 05:00',600)
    f[['open','high','low','close']]=[100.,101.,99.,100.]
    return f


def test_completed_monday_first_touch_no_body_filter_and_no_later_retouch():
    f=monday_week();lower=99-.618*2
    f.iloc[300,f.columns.get_indexer(['open','high','low','close'])]=[100,100,lower-1,lower-.5]
    f.iloc[310,f.columns.get_loc('low')]=lower-2
    e,d=sample_entries(f,'BTCUSDT',{},CFG);s=e[e.population.eq('signal')]
    assert len(s)==1 and s.entry_i.iloc[0]==300 and s.side.iloc[0]==1
    assert s.level.iloc[0]==lower and d['pending_orders']==2
    assert s.placed_time.iloc[0]=='2024-01-02 05:00:00+00:00'


def test_gap_before_touch_cannot_establish_first_weekly_touch():
    f=monday_week();f.iloc[310,f.columns.get_loc('low')]=90
    f=f.drop(f.index[300]);e,_=sample_entries(f,'BTCUSDT',{},CFG)
    assert e.empty


def test_incomplete_monday_has_no_pending_limit():
    f=monday_week().iloc[1:];p,_=pending_windows(f,'BTCUSDT',{})
    assert p.empty
