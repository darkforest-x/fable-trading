"""Excursions must stop at fills; intrabar favorable wick order stays unknown."""
import numpy as np
import pytest
from yoyo.evaluation.spike_v131_excursions import trace


@pytest.mark.parametrize('side',[1,-1])
def test_stop_bar_bounds_and_no_post_exit_extreme(side):
    o=np.array([100.,100.]); h=np.array([103.,200.]);l=np.array([98.,1.]);c=np.array([102.,100.])
    if side==-1:o,h,l,c=200-o,200-l,200-h,200-c
    r=trace(side=side,entry=100,stop0=100-side,tick=.01,o=o,h=h,l=l,c=c,atr=np.ones(2),e=0,x=1,opposite=False,trail=4)
    assert r['actual_exit_i']==0 and r['mae_r']==1
    assert r['mfe_lower_r']==0 and r['mfe_upper_r']==3


def test_gap_exit_has_no_intrabar_excursion():
    r=trace(side=1,entry=100,stop0=99,tick=.01,o=np.array([100.,98.]),h=np.array([101.,200.]),l=np.array([99.5,1.]),c=np.array([100.,100.]),atr=np.ones(2),e=0,x=1,opposite=False,trail=4)
    assert r['mae_r']==2 and r['mfe_lower_r']==r['mfe_upper_r']==1


def test_trail_applies_next_bar_and_recovers_earlier_exit():
    r=trace(side=1,entry=100,stop0=99,tick=.01,o=np.array([100.,103.,104.]),h=np.array([104.,104.,200.]),l=np.array([99.5,100.,1.]),c=np.array([103.,103.,100.]),atr=np.ones(3),e=0,x=2,opposite=True,trail=2)
    assert r['actual_exit_i']==1 and r['actual_exit_price']==101
    assert r['mae_r']==.5 and r['mfe_lower_r']==4


def test_opposite_at_open_excludes_entire_exit_bar():
    r=trace(side=1,entry=100,stop0=99,tick=.01,o=np.array([100.,100.5]),h=np.array([101.,200.]),l=np.array([99.5,1.]),c=np.array([100.,100.]),atr=np.ones(2),e=0,x=1,opposite=True,trail=4)
    assert r['mae_r']==.5 and r['mfe_lower_r']==r['mfe_upper_r']==1
