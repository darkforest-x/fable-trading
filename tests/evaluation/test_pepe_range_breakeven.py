"""Causal activation, real cost coverage, and independent original exits."""
import numpy as np
import pytest

from yoyo.evaluation.pepe_range_breakeven import replay, scalar, assert_equal


def ctx(bars, atr=None, hour=None):
    return dict(bars=np.ascontiguousarray(bars,dtype=float),
                atr=np.ascontiguousarray(atr if atr is not None else [1]*len(bars),dtype=float),
                hour=np.ascontiguousarray(hour if hour is not None else [0]*len(bars),dtype=float))


def run(context, side=1, arm="be3", cost=.002, tick=.01, entry=100, stop=None, last=None):
    stop = entry-side if stop is None else stop
    event = [0,side,entry,stop,len(context["bars"])-1 if last is None else last]
    result = replay(context,[event],arm,cost,tick)[0]
    assert_equal(result,scalar(context,event,arm,cost,tick),tick)
    return result


@pytest.mark.parametrize("side",[1,-1])
def test_cost_be_on_next_bar_with_long_short_symmetry(side):
    bars=np.array([[100,200,1,100],[100,103.5,99.5,103],[103,103.2,100.1,101]],dtype=float)
    if side == -1:
        bars=200-bars
        bars[:,[1,2]]=bars[:,[2,1]]
    x=run(ctx(bars),side)
    assert x[2] == 5 and x[9] == 1 and x[0] == 2
    assert side*(x[1]-100) == pytest.approx(.2)
    assert x[4] == -1


def test_stop_hit_on_trigger_bar_cannot_be_rescued():
    x=run(ctx([[100]*4,[100,104,98.5,103]]))
    assert x[2] == 1 and x[1] == 99 and x[8] == -1


def test_wick_reaching_3r_does_not_arm():
    x=run(ctx([[100]*4,[100,103.5,99.5,102.9],[102.9,103,98,100]]))
    assert x[2] == 1 and x[8] == -1


def test_non_quarter_close_does_not_arm():
    x=run(ctx([[100]*4,[100,103.5,99.5,103],[103,103.1,98,99]],atr=[1,np.nan,1]))
    assert x[2] == 1 and x[8] == -1


def test_gap_can_lose_after_breakeven_protection():
    x=run(ctx([[100]*4,[100,104,99.5,103],[99.8,100,99,99.9]]))
    assert x[2] == 5 and x[1] == 99.8
    assert x[1]-100-.2 < 0


def test_be_only_does_not_enable_opposite_hour_exit():
    x=run(ctx([[100]*4,[100,103.5,99.5,103],[103,104.2,102.5,104]],hour=[0,-1,0]))
    assert x[9] == 1 and x[4] == -1 and x[2] == 0 and x[1] == 104


def test_trailing_ratchet_can_be_tighter_than_cost_be():
    x=run(ctx([[100]*4,[100,106.5,99.5,106],[106,106.2,104,104.5]],atr=[.25]*3))
    assert x[4] == 1 and x[9] == 1 and x[2] == 2 and x[1] == 105


def test_cost_be_price_rounds_toward_profit():
    long=run(ctx([[100]*4,[100,104,99.5,103]]),cost=.002001)
    short=run(ctx([[100]*4,[100,100.5,96,97]]),side=-1,cost=.002001)
    assert long[11] == pytest.approx(100.21)
    assert short[11] == pytest.approx(99.79)


def test_cost_greater_than_trigger_defers_instead_of_invalid_stop():
    x=run(ctx([[100]*4,[100,104,99.5,103],[103,104.2,102,104.02]]),cost=.04)
    assert x[8] == 1 and x[9] == 2 and x[12] == 1 and x[2] == 0
    assert x[5] == 104


def test_period_boundary_hides_later_bar():
    x=run(ctx([[100]*4,[100,104,99.5,103],[103,104,99,100]]),last=1)
    assert x[2] == 0 and x[1] == 103 and x[0] == 1


def test_decimal_exact_5r_has_shared_baseline_correction():
    c=ctx([[1.084e-6]*4,[1.084e-6,1.085e-6,1.073e-6,1.074e-6]],atr=[2e-9]*2)
    legacy=run(c,side=-1,arm="legacy",entry=1.084e-6,stop=1.086e-6,tick=1e-9)
    corrected=run(c,side=-1,arm="baseline",entry=1.084e-6,stop=1.086e-6,tick=1e-9)
    assert legacy[4] == -1 and corrected[4] == 1


def test_entry_wick_is_never_used_for_be():
    x=run(ctx([[100,110,1,100],[100,102,99.5,101]]))
    assert x[8] == -1 and x[3] == 2
