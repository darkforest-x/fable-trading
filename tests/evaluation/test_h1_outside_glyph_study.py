"""Behavioral fixtures for native VectorBT fills and experiment boundaries."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip('vectorbt')
from yoyo.evaluation.h1_outside_glyph_study import execute

CFG=dict(max_hours=1,maker_fee=.0002,taker_fee=.0005,market_slippage=.0005)


def bars(rows):
    return pd.DataFrame(rows,columns=['open','high','low','close'],
                        index=pd.date_range('2026-01-01',periods=len(rows),freq='5min',tz='UTC'))


def entry(side=1,stop=98,target=104):
    return pd.DataFrame([dict(entry_i=0,side=side,entry=100,stop=stop,target_price=target,
                             risk=abs(100-stop),trade_key='fixture')])


def test_same_bar_target_and_exact_costs():
    q,f=execute(bars([[100,105,99,103]]),entry(),CFG)
    assert q.reason.iloc[0]=='target' and q.raw_exit_price.iloc[0]==104
    expected=104-100.05-(100.05*.0005+104*.0002)
    assert q.net_r.iloc[0]==pytest.approx(expected/2)
    assert len(f)==2


def test_short_stop_gap_is_open_not_stop_level():
    q,_=execute(bars([[100,101,99,100],[105,106,104,105]]),entry(-1,102,96),CFG)
    assert q.reason.iloc[0]=='stop' and q.raw_exit_price.iloc[0]==105
    assert q.exit_fill.iloc[0]==pytest.approx(105*1.0005)


def test_gap_censors_without_crossing_missing_period():
    frame=bars([[100,101,99,100],[100,101,99,100],[100,110,90,100]])
    frame.index=pd.DatetimeIndex(['2026-01-01T00:00Z','2026-01-01T00:05Z','2026-01-01T00:20Z'])
    q,_=execute(frame,entry(),CFG)
    assert q.reason.iloc[0]=='censored' and q.exit_i.iloc[0]==1


def test_timeout_at_exact_horizon_and_no_future_effect():
    frame=bars([[100,101,99,100]]*12+[[100,110,90,100]])
    q,_=execute(frame,entry(),CFG)
    assert q.reason.iloc[0]=='timeout' and q.exit_i.iloc[0]==11
    assert not q.censored.iloc[0]


def test_ambiguous_first_bar_is_reported_stop_first():
    q,_=execute(bars([[100,105,97,100]]),entry(),CFG)
    assert q.reason.iloc[0]=='target' and q.ambiguous_bar.iloc[0]
    expected=98*(1-.0005)-100.05-(100.05+98*(1-.0005))*.0005
    assert q.stop_first_net_r.iloc[0]==pytest.approx(expected/2)
    assert q.stop_first_net_r.iloc[0]<q.net_r.iloc[0]
