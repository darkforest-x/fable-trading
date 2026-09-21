"""Independent LP and exhaustive small-path checks for hindsight capacity labels."""
from itertools import combinations

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import linprog

from yoyo.evaluation.winner_roll_max10r import audit_schedule, optimize_path


def lp_schedule(frame, indexes, e, capital=100., leverage=40., fee=.001,
                maintenance_rate=.05, liquidation_fee=.001, buffer=.01):
    """Solve all fixed-time quantities simultaneously; no greedy implementation."""
    op, low = frame.open.to_numpy(), frame.low.to_numpy()
    prices = op[list(indexes)]
    a, b = [], []
    for t in range(e+1):
        active = np.asarray(indexes) <= t
        a.append(np.where(active, (1+fee)*prices-(1-maintenance_rate-liquidation_fee)*low[t], 0.))
        b.append(capital-buffer-capital*1e-10)
        if t in indexes:
            a.append(np.where(active, (1+fee)*prices-(1-1/leverage)*op[t], 0.))
            b.append(capital)
    unit_gain = (1-fee)*frame.high.iloc[e]-(1+fee)*prices
    result = linprog(-unit_gain, A_ub=a, b_ub=b, bounds=(0, None), method='highs')
    assert result.success
    return capital-result.fun


def brute_lp(frame, limit, **kwargs):
    best = -float('inf')
    for e in range(len(frame)):
        for k in range(limit+1):
            for adds in combinations(range(1, e+1), k):
                indexes = (0,)+adds
                prices = frame.open.iloc[list(indexes)].to_numpy()
                if np.any(np.diff(prices) <= 0):
                    continue
                best = max(best, lp_schedule(frame, indexes, e, **kwargs))
    return best


def test_complete_timing_search_matches_independent_lp():
    rng = np.random.default_rng(72109)
    for _ in range(35):
        op = np.r_[1., 1+rng.uniform(-.04, .5, 5)]
        low = op*(1-rng.uniform(0, .10, 6))
        high = op*(1+rng.uniform(.03, .13, 6))
        f = pd.DataFrame(dict(open=op, high=high, low=low, close=op))
        got = optimize_path(f)
        for limit, arm in enumerate(('none', 'one', 'two')):
            expected = brute_lp(f, limit)
            assert got[arm]['final_balance'] == pytest.approx(expected, rel=1e-8, abs=1e-6)
            assert got[arm]['adds_count'] <= limit
            assert got[arm]['min_maintenance_buffer'] >= .01-1e-6


def test_first_and_exit_bar_lows_are_checked():
    f = pd.DataFrame(dict(open=[1.,1.2,1.4], high=[1.1,1.3,2.], low=[.7,1.,.5], close=[1.,1.2,1.4]))
    got = optimize_path(f)
    for limit, arm in enumerate(('none','one','two')):
        assert got[arm]['final_balance'] == pytest.approx(brute_lp(f,limit), rel=1e-8)


def test_higher_price_with_negative_net_unit_gain_is_not_forced():
    f = pd.DataFrame(dict(open=[1.,1.02,1.021], high=[1.01,1.021,1.0211],
                          low=[.99,1.019,1.0209], close=[1.,1.02,1.021]))
    got = optimize_path(f,fee=.01)
    assert got['two']['adds_count'] == 0
    assert got['two']['final_balance'] == pytest.approx(brute_lp(f,2,fee=.01))


def test_price_scale_invariance_and_no_external_cash():
    f = pd.DataFrame(dict(open=[1.,1.1,1.5,1.7], high=[1.05,1.2,1.6,2.1],
                          low=[.98,1.08,1.4,1.6], close=[1.,1.1,1.5,1.7]))
    original = optimize_path(f)['two']
    tiny = optimize_path(f*1e-9)['two']
    huge = optimize_path(f*1e9)['two']
    assert original['final_balance'] == pytest.approx(tiny['final_balance'])
    assert original['final_balance'] == pytest.approx(huge['final_balance'])
    entry_cost = sum(x['quantity']*x['price'] for x in original['legs'])
    qty = sum(x['quantity'] for x in original['legs'])
    assert original['final_balance'] == pytest.approx(100+qty*original['exit_price']*.999-entry_cost*1.001)
    assert original['adds_count'] == 2


def test_audit_rejects_oversizing_and_descending_adds():
    f = pd.DataFrame(dict(open=[1.,1.2], high=[1.1,1.3], low=[.9,1.1], close=[1.,1.2]))
    with pytest.raises(AssertionError):
        audit_schedule(f,[dict(bar_i=0,price=1.,quantity=1e5)],1)
    with pytest.raises(ValueError):
        audit_schedule(f,[dict(bar_i=0,price=1.,quantity=1),dict(bar_i=1,price=.99,quantity=1)],1)


@pytest.mark.parametrize('field,value',[('low',0),('open',float('nan')),('high',.5),('low',2.)])
def test_bad_ohlc_rejected(field,value):
    f = pd.DataFrame(dict(open=[1.],high=[1.1],low=[.9],close=[1.]))
    f.loc[0,field] = value
    with pytest.raises(ValueError): optimize_path(f)
