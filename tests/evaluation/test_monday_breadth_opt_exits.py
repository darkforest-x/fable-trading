"""Synthetic regressions for execution order, exit-window edges and funding on partial fills."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip('vectorbt')
from yoyo.evaluation import monday_breadth_opt_exits as exits
from yoyo.evaluation import monday_breadth_opt_funding as funding


def quotes():
    index=pd.date_range('2026-01-02',periods=15,freq='5min',tz='UTC')
    frame=pd.DataFrame(100.,index=index,columns=['open','high','low','close'])
    return exits.prepare(frame)


def test_ambiguous_entry_candle_stop_first_and_excludes_exit_candle_high():
    frame=quotes()
    frame.iloc[0,1:3]=[112.,92.]
    frame.iloc[12,1]=200.
    p=exits.Policy('time',1,6,take_pct=10,take_fraction=1)
    f=exits.simulate(frame,[frame.index[0]],[p]).iloc[0]
    assert f.exit_vwap==94 and f.ambiguous==1
    assert f.peak_pct==pytest.approx(12)


def test_partial_limit_at_gap_open_and_stop_of_remaining_size_on_same_bar():
    frame=quotes()
    frame.iloc[1,:4]=[112.,113.,92.,99.]
    p=exits.Policy('time',1,6,take_pct=10,take_fraction=.5)
    f=exits.simulate(frame,[frame.index[0]],[p]).iloc[0]
    assert f.first_exit_time==f.exit_time==frame.index[1]
    assert f.exit_legs==2 and f.first_exit_price==pytest.approx(110)
    assert f.exit_vwap==pytest.approx(102)
    assert f.net_points==pytest.approx(2.-100*.0005-.5*110*.0002-.5*94*.0005)


def test_trailing_stop_arms_after_high_and_gap_pays_worse_open():
    frame=quotes()
    frame.iloc[1,:4]=[100.,120.,95.,105.]
    frame.iloc[2,:4]=[105.,112.,100.,111.]
    p=exits.Policy('percent_trail',1,12,5,4)
    f=exits.simulate(frame,[frame.index[0]],[p]).iloc[0]
    assert f.exit_time==frame.index[2] and f.exit_vwap==105


def test_funding_charged_to_residual_size_with_adverse_boundary_ordering():
    t=pd.date_range('2026-01-01',periods=4,freq='8h',tz='UTC')
    hist=pd.DataFrame(dict(realized_rate=[-.01,.02,.01,.01],mark_price=[100.]*4),index=t)
    fills=pd.DataFrame(dict(entry_time=[t[0]],first_exit_time=[t[1]],exit_time=[t[3]],policy_id=[0],exit_legs=[2]))
    cost=funding.charged(fills,hist,[exits.Policy('time',24,6,take_pct=5,take_fraction=.75)])
    assert cost[0]==pytest.approx(2+.25+.25)
