"""Conditional excursions must respect trigger order, SL and entry+2h."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_v237_2h_scaleout import conditional_rows, trajectory


def base(end=2, side=1):
    return pd.DataFrame([dict(pair_id=1,entry_i=0,end_i=end,entry=100.,side=side,risk=1.)])


@pytest.mark.parametrize('side',[1,-1])
def test_post_trigger_excludes_adverse_move_before_touch(side):
    bars=np.array([[100,200,1,100],[100,101.5,99.2,101.4],[101.4,102.5,101.2,102]],float)
    if side==-1:bars=200-bars[:,[0,2,1,3]]
    path='OLHC' if side==1 else 'OHLC'
    r=conditional_rows(bars,base(side=side),path).iloc[0]
    assert r.reached and r.minimum_r==pytest.approx(1.)
    assert r.peak_r==pytest.approx(2.5) and r.extra_r==pytest.approx(1.5)
    assert not r.returned_entry and r.next2_before_entry


@pytest.mark.parametrize('side',[1,-1])
def test_same_candle_high_low_order_changes_trigger_population(side):
    bars=np.array([[100,200,1,100],[100,102.5,98,101]],float)
    if side==-1:bars=200-bars[:,[0,2,1,3]]
    favorable='OHLC' if side==1 else 'OLHC'
    a=conditional_rows(bars,base(end=1,side=side),favorable)
    b=conditional_rows(bars,base(end=1,side=side),'OLHC' if favorable=='OHLC' else 'OHLC')
    assert a.reached.all() and not b.reached.any()
    assert a.minimum_r.eq(-1.).all()
    assert a.iloc[0].next2_before_entry
    assert a.iloc[0].max_drawdown_r==pytest.approx(3.5)


def test_return_to_entry_then_extension_distinct_from_first_target_win():
    bars=np.array([[100,200,1,100],[100,101.2,99.5,100.5],[100.5,103.2,100.2,103]],float)
    r=conditional_rows(bars,base(),'OHLC').iloc[0]
    assert r.returned_entry and r.next2 and not r.next2_before_entry
    assert r.next2_after_entry_return and r.pullback_before_next2_r==pytest.approx(1.5)
    assert r.trigger_pullback_r==pytest.approx(1.5)
    assert r.extra_r==pytest.approx(2.2)


def test_original_stop_terminates_all_later_extrema():
    bars=np.array([[100,200,1,100],[100,101.5,98,99],[99,150,98,140]],float)
    r=conditional_rows(bars,base(),'OHLC')
    assert r.iloc[0].peak_r==pytest.approx(1.5)
    assert not r.iloc[1].reached


def test_open_gap_stop_is_not_interpolated_fill():
    bars=np.array([[100,200,1,100],[100,101.5,99.5,101],[98,150,97,140]],float)
    v,ix,stopped=trajectory(bars,0,2,1,100,1,'OHLC')
    assert stopped and v[-1]==-2 and ix[-1]==2 and v.max()==pytest.approx(1.5)


def test_deadline_is_two_hours_after_entry_not_trigger():
    bars=np.array([[100,200,1,100]]+[[100,100.5,99.5,100]]*23+[[100,101.2,99.5,101]]+[[101,150,100,140]],float)
    r=conditional_rows(bars,base(end=24),'OLHC').iloc[0]
    assert r.reached and r.hit_i==24 and r.minutes_to_touch_bar_close==120
    assert r.peak_r==pytest.approx(1.2) and not r.next2


def test_repeated_trigger_keeps_first_hit():
    bars=np.array([[100,200,1,100],[100,101.1,99.8,100.5],[100.5,101.5,99.7,101]],float)
    r=conditional_rows(bars,base(),'OHLC').iloc[0]
    assert r.hit_i==1 and r.minimum_r==pytest.approx(-.3)
