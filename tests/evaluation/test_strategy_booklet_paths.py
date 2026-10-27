"""Trace observers must preserve the original allocator and cash-flow identity."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_limit_return_opt import linear, frac
from yoyo.evaluation.monday_limit_spike_hw import slot_replay
from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
from yoyo.evaluation.monday_limit_langlang import path_run
from yoyo.evaluation.strategy_booklet_paths import Trace


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
