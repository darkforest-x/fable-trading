"""V3 behavioral checks for tail preservation and causal protection."""
from types import SimpleNamespace
import numpy as np
import pytest

from yoyo.evaluation.eth_stoch_confirm_exits import trace, Policy
from yoyo.evaluation.eth_stoch_target_be import oracle, policies


def context(bars):
    a = np.array(bars, float)
    return dict(o=a[:, 0], h=a[:, 1], l=a[:, 2], c=a[:, 3], arrow=np.zeros(len(a)//5+1))


@pytest.mark.parametrize('side', [1, -1])
def test_protection_can_sacrifice_later_ten_r_winner(side):
    bars = np.array([[100, 101.5, 99.5, 101], [101, 102, 99.9, 100], [100, 111, 99.8, 110]])
    if side == -1:
        bars = 200-bars[:, [0, 2, 1, 3]]
    ctx = context(bars)
    r = SimpleNamespace(entry_i=0, side=side, entry=100, stop=100-side, risk=1)
    baseline = Policy('base', target=10, target_tick=.01)
    protected = Policy('be', target=10, be=1, target_tick=.01)
    assert oracle(ctx, r, 3, baseline)['reason'] == 'target'
    a = oracle(ctx, r, 3, protected)
    b = trace(ctx, 0, side, 100, 100-side, protected)
    assert a['exit_i'] == b['exit_i'] == 1 and a['exit_price'] == b['exit_price'] == 100
    assert a['be_activation_i'] == 0


def test_close_activation_does_not_retroactively_hit_same_minute_low():
    c = context([[100, 102, 99.2, 101], [101, 111, 100.1, 110]])
    r = SimpleNamespace(entry_i=0, side=1, entry=100, stop=99, risk=1)
    p = Policy('be', target=10, be=1, target_tick=.01)
    assert oracle(c, r, 2, p)['reason'] == 'target'


def test_net_stop_needs_fee_level_before_activation():
    c = context([[100, 100.11, 99.96, 100.1], [100.1, 100.21, 100.05, 100.2], [100.19, 100.25, 100.1, 100.22]])
    r = SimpleNamespace(entry_i=0, side=1, entry=100, stop=99.95, risk=.05)
    p = Policy('net', target=10, be=.5, net_be=True, target_tick=.01)
    a = oracle(c, r, 3, p)
    b = trace(c, 0, 1, 100, 99.95, p)
    assert a['be_activation_i'] == 1
    assert a['reason'] == b['reason'] == 'stop_gap'
    assert b['net_points'] == pytest.approx(-.01)


def test_target_precedes_close_activation_and_boundary_is_censored():
    c = context([[100, 110, 99.5, 105]])
    r = SimpleNamespace(entry_i=0, side=1, entry=100, stop=99, risk=1)
    p = Policy('be', target=10, be=1, target_tick=.01)
    assert oracle(c, r, 1, p)['be_activation_i'] == -1
    c = context([[100, 102, 99.5, 101]])
    a = oracle(c, r, 1, p)
    assert a['reason'] == 'boundary' and a['be_activation_i'] == 0


def test_grid_preserves_both_no_be_controls_and_late_timing():
    arms = policies(dict(targets_r=[10, 20], be_triggers_r=[.5, 1, 1.5, 2, 3, 5]))
    assert len(arms) == 26 and len({p.name for p in arms}) == 26
    assert sum(not p.be for p in arms) == 2
    assert all(not p.kd and not p.trail and not p.minutes for p in arms)
