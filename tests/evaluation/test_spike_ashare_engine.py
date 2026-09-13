"""Synthetic calendar/settlement causality tests; no historical prices read."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_ashare_engine import build_signals, prepare_cycles, price_limits, replay


def daily(dates, prices=None):
    prices = prices or [(10.,10.5,9.5,10.)]*len(dates)
    rows=[]
    for date,(o,h,l,c) in zip(dates,prices):
        rows.append(dict(date=date,code='sh.600000',open=o,high=h,low=l,close=c,volume=1000,
                         raw_open=o,raw_high=h,raw_low=l,raw_close=c,raw_preclose=10.,
                         factor=1.,tradestatus=1,isST=0,board='main_sh'))
    return pd.DataFrame(rows)


def cal(dates):
    return pd.DataFrame(dict(calendar_date=dates,is_trading_day=['1']*len(dates)))


def cycle(date, timeframe='1D', stop=9.):
    return dict(decision_date=date,timeframe=timeframe,in_window=True,ready_v1=True,
                ready_v8=True,long_v1=True,long_v8=True,short_v8=False,stop_base=stop,
                score=4.,vol_bucket=1,close=10.,atr=.5)


@pytest.mark.parametrize('version',['v1','v8'])
def test_next_session_after_holiday_and_t1_stop_deferred(version):
    dates=['2024-04-03','2024-04-08','2024-04-09']
    d=daily(dates,[(10,10.5,9.5,10),(10,10.2,8.5,9.5),(9.5,10,9.3,9.8)])
    c=pd.DataFrame([cycle(dates[0])])
    result=replay(d,cal(dates),c,version,dates[0],dates[-1])
    trade=result['trades'].iloc[0]
    assert trade.entry_date=='2024-04-08'
    assert trade.exit_date=='2024-04-09'
    assert trade.exit_reason=='entry_day_stop_T1'
    assert trade.exit_price==9.5
    assert trade.net_return==pytest.approx(-.052)
    assert not trade.censored


def test_weekly_friday_signal_monday_entry_can_exit_tuesday():
    dates=['2024-03-01','2024-03-04','2024-03-05','2024-03-08']
    d=daily(dates,[(10,10.5,9.5,10),(10,10.5,9.5,10),(9.5,10,8.8,9.5),(10,10.5,9.5,10)])
    result=replay(d,cal(dates),pd.DataFrame([cycle(dates[0],'1W',9.2)]),'v8',dates[0],dates[-1])
    t=result['trades'].iloc[0]
    assert t.entry_date==dates[1] and t.exit_date==dates[2]
    assert t.exit_price==pytest.approx(9.2)


def test_limit_down_exit_waits_and_st_limit_changes_by_date():
    dates=['2024-03-01','2024-03-04','2024-03-05','2024-03-06']
    d=daily(dates,[(10,10.5,9.5,10),(10,10.5,8.5,9),(9,9,9,9),(9.5,10,9.3,9.8)])
    result=replay(d,cal(dates),pd.DataFrame([cycle(dates[0])]),'v1',dates[0],dates[-1])
    assert result['trades'].iloc[0].exit_date==dates[3]
    assert result['skips']['exit_limit_down']==1
    assert price_limits(dict(date='2026-07-03',isST=1,raw_preclose=10))==(.95*10,1.05*10)
    assert price_limits(dict(date='2026-07-06',isST=1,raw_preclose=10))==(9,11)


@pytest.mark.parametrize('change,reason', [('st','entry_st'),('limit','entry_limit_up'),('suspended','entry_suspended_or_missing')])
def test_unexecutable_entries_expire(change,reason):
    dates=['2024-03-01','2024-03-04','2024-03-05']
    d=daily(dates)
    if change=='st': d.loc[1,'isST']=1
    elif change=='limit': d.loc[1,['open','high','close','raw_open','raw_high','raw_close']]=11
    else: d.loc[1,'tradestatus']=0
    result=replay(d,cal(dates),pd.DataFrame([cycle(dates[0])]),'v1',dates[0],dates[-1])
    assert result['trades'].empty
    assert result['skips'][reason]==1


def test_raw_v6_reverse_exits_when_not_an_admitted_entry():
    dates=['2024-03-01','2024-03-04','2024-03-05']
    d=daily(dates)
    first=cycle(dates[0]); reverse=cycle(dates[1]); reverse.update(long_v8=False,short_v8=True)
    result=replay(d,cal(dates),pd.DataFrame([first,reverse]),'v8',dates[0],dates[-1])
    assert result['trades'].iloc[0].exit_reason=='opposite_v6_next_open'
    assert not result['trades'].iloc[0].censored


def test_reverse_gap_stop_has_protective_priority():
    dates=['2024-03-01','2024-03-04','2024-03-05']
    d=daily(dates,[(10,10.5,9.5,10),(10,10.5,9.5,10),(9.1,9.2,9.05,9.15)])
    first=cycle(dates[0],stop=9.3);second=cycle(dates[1]);second.update(long_v8=False,short_v8=True)
    t=replay(d,cal(dates),pd.DataFrame([first,second]),'v8',dates[0],dates[-1])['trades'].iloc[0]
    assert t.exit_reason=='initial_stop_gap' and t.exit_price==pytest.approx(9.1)


def test_terminal_marks_are_censored_and_hfq_stop_uses_raw_tick():
    dates=['2024-03-01','2024-03-04']
    d=daily(dates)
    for name in ['open','high','low','close']: d[name]*=3
    d['factor']=3
    c=cycle(dates[0],stop=27.041);c.update(close=30,atr=1.5)
    result=replay(d,cal(dates),pd.DataFrame([c]),'v1',dates[0],dates[-1])
    t=result['trades'].iloc[0]
    assert t.initial_stop==pytest.approx(27.03)
    assert t.censored and t.net_return==pytest.approx(-.002)


def test_partial_week_not_visible_and_holiday_week_uses_last_session():
    dates=['2024-04-01','2024-04-02','2024-04-03','2024-04-08','2024-04-09']
    d=daily(dates)
    # Friday cutoff has passed: holiday week closes at Wednesday's session.
    week=prepare_cycles(d,cal(dates),'1W','2024-04-05')
    assert week.decision_date.tolist()==['2024-04-03']
    partial=prepare_cycles(d,cal(dates),'1W','2024-04-09')
    assert partial.decision_date.tolist()==['2024-04-03']


def test_signal_prefix_unchanged_when_future_prices_change():
    dates=pd.bdate_range('2018-01-01',periods=780).strftime('%Y-%m-%d').tolist()
    rng=np.random.default_rng(13)
    close=10*np.exp(np.cumsum(rng.normal(0,.008,len(dates))))
    d=daily(dates,[(v,v*1.01,v*.99,v) for v in close])
    a=build_signals(d,cal(dates),'1D',dates[710],dates[-1])
    changed=d.copy()
    for name in ['open','high','low','close']: changed.loc[750:,name]*=2
    b=build_signals(changed,cal(dates),'1D',dates[710],dates[-1])
    cols=['long_v1','long_v8','short_v8','ready_v1','ready_v8','stop_base','atr','vol_bucket']
    pd.testing.assert_frame_equal(a.loc[:749,cols],b.loc[:749,cols])
    assert not a.loc[:339,'ready_v1'].any()
    assert not a.loc[:710,'ready_v8'].any()


def test_adjusted_tick_scalar_parity_and_coordinate_invariance():
    from yoyo.evaluation.spike_burst_replay import features, replay as pine_replay
    index=pd.date_range('2020-01-01',periods=420,tz='UTC')
    values=10+np.sin(np.arange(420)/8)*.1
    raw=pd.DataFrame(dict(open=values,high=values+.15,low=values-.15,close=values,volume=1000.),index=index)
    frame=features(raw)
    scalar=pine_replay(frame,.01)
    dynamic=pine_replay(frame,.01,price_ticks=pd.Series(.01,index=index))
    dynamic.attrs=scalar.attrs
    pd.testing.assert_frame_equal(scalar,dynamic)
    adjusted=raw.copy()
    for name in ['open','high','low','close']: adjusted[name]*=3
    scaled=pine_replay(features(adjusted),.01,price_ticks=pd.Series(.03,index=index))
    pd.testing.assert_series_equal(scalar.burst,scaled.burst)
