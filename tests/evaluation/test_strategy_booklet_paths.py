"""Trace observers must preserve the original allocator and cash-flow identity."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_limit_return_opt import linear, frac
from yoyo.evaluation.monday_limit_spike_hw import slot_replay
from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
from yoyo.evaluation.monday_limit_langlang import path_run
from yoyo.evaluation.strategy_booklet_paths import Trace
from yoyo.evaluation.strategy_booklet_metrics import project


def fills():
    return pd.DataFrame(dict(entry_ns=[1000000, 1000000, 4000000, 6000000],
                             exit_ns=[3000000, 1000000, 5000000, 7000000],
                             net=[200., -10000., 300., 500.], mae_pct=[0., 60., 0., 0.],
                             side=[1, -1, 1, -1], event=['a', 'b', 'c', 'd']))


@pytest.mark.parametrize('run', [lambda t, c: linear(t, np.full(len(t), .1), 1, 2, trace=c),
                                      lambda t, c: frac(t, 2, 3, 2, trace=c),
                                      lambda t, c: slot_replay(t, 2, 5, 19, 2, trace=c)[0],
                                      lambda t, c: replay_w(t.rename(columns={'entry_ns':'entry','exit_ns':'exit'}), np.full(len(t), .1), trace=c)[0]])
def test_trace_preserves_allocator_and_reconciles(run):
    t = fills()
    observer = Trace(0)
    expected = run(t, None)
    actual = run(t, observer)
    assert actual == expected
    assert observer.rows[-1]['equity'] == pytest.approx(1000*actual['final_multiple'])
    assert sum(r['pnl'] for r in observer.rows)+1000 == pytest.approx(observer.rows[-1]['equity'])
    assert all(r['notional'] > 0 for r in observer.rows[1:])


def test_allin_refills_are_not_trading_profit():
    t = fills().iloc[[1, 2, 3]].reset_index(drop=True)
    observer = Trace(0)
    expected = path_run(t, 'allin', 3, 2)
    assert path_run(t, 'allin', 3, 2, trace=observer) == expected
    events = observer.rows
    assert any(r['equity'] == 0 for r in events)
    refill = next(r for r in events if r['cashflow'])
    assert refill['cashflow'] == 1000
    assert refill['pnl'] == 0
    assert events[-1]['equity'] == pytest.approx(1000+sum(r['pnl']+r['cashflow'] for r in events))


def test_chart_metrics_keep_same_timestamp_low_and_exclude_refills():
    observer = Trace(0)
    row = pd.Series(dict(event='BTC:one', symbol='BTC', side=1, entry_ns=0, net=-10000))
    observer(row, 1_000_000, 0, -1000, 3000)
    observer(row, 1_000_000, 1000, 0, 0, cashflow=1000, stake_budget=1000)
    observer(row, 2_000_000, 1100, 100, 3000)
    data = project(pd.DataFrame(observer.rows), {})
    assert data['summary']['pnl'] == -900
    assert data['summary']['cash_added'] == 1000
    assert data['summary']['return_pct'] is None
    assert data['summary']['max_drawdown_pct'] == 100
    assert [p[1] for p in data['points']] == [1000, 0, 1000, 1100]
    assert data['monthly'][0][1] == -900
    assert data['summary']['win_pct'] == 50


def test_chart_monthly_returns_compound_and_future_months_are_absent():
    start=pd.Timestamp('2025-01-01',tz='UTC').value
    observer=Trace(start)
    row=pd.Series(dict(event='A',symbol='A',side=1,entry_ns=start,net=1000))
    observer(row,pd.Timestamp('2025-01-31',tz='UTC').value,1100,100,1000)
    observer(row,pd.Timestamp('2025-02-28',tz='UTC').value,990,-110,1100)
    data=project(pd.DataFrame(observer.rows),{})
    assert data['monthly'][0][1] == pytest.approx(10)
    assert data['monthly'][1][1] == pytest.approx(-10)
    assert len(data['monthly']) == 2
    assert data['yearly'][0][1] == pytest.approx(-1)
    assert data['summary']['max_drawdown_pct'] == pytest.approx(10)
    broken=pd.DataFrame(observer.rows);broken.loc[1,'equity']+=1
    with pytest.raises(ValueError,match='reconcile'):
        project(broken,{})
