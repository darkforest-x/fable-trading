"""Stop width must not alter entry clocks or silently change R accounting."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_v237_smaller_stop import tighten
from yoyo.evaluation.range_v237_2h_scaleout import initial_stop_expectation


def base(side=1):
    return pd.DataFrame([dict(pair_id=0,entry_i=0,end_i=2,entry=100.,side=side,
        stop=100-side*1.1,risk=1.1,population='signal')])


@pytest.mark.parametrize('side',[1,-1])
def test_tightening_rounds_outward_and_preserves_clocks(side):
    row=tighten(base(side),.5,.1).iloc[0]
    assert row.risk==pytest.approx(.6)
    assert row.stop==pytest.approx(100-side*.6)
    assert row.original_risk==1.1 and row.entry_i==0 and row.end_i==2


def test_baseline_is_exact_even_with_float_tail():
    b=base(-1);b['stop']=101.10000000001;b['risk']=1.10000000001
    r=tighten(b,1.,.1)
    assert r.stop.iloc[0]==b.stop.iloc[0] and r.risk.iloc[0]==b.risk.iloc[0]


@pytest.mark.parametrize('side',[1,-1])
def test_smaller_stop_can_remove_a_later_winner(side):
    bars=np.array([[100,105,80,100],[100,100.2,99.3,99.8],[99.8,102,99.7,101.5]])
    if side==-1:bars=200-bars[:,[0,2,1,3]]
    old=initial_stop_expectation(bars,base(side),.1)[0]
    new=initial_stop_expectation(bars,tighten(base(side),.5,.1),.1)[0]
    assert old[0]==2 and old[2]==0
    assert new[0]==1 and new[2]==1
    assert side*(old[1]-100)>0 and side*(new[1]-100)<0


def test_new_r_fees_grow_while_notional_cost_is_unchanged():
    old=base().iloc[0];new=tighten(base(),.5,.1).iloc[0]
    old_fee=.002*old.entry/old.risk;new_fee=.002*new.entry/new.risk
    assert new_fee>old_fee
    assert old_fee*old.risk==pytest.approx(new_fee*new.risk)
    net_new=(101-new.entry-.002*new.entry)/new.risk
    assert net_new*new.risk/new.original_risk==pytest.approx((1-.2)/old.risk)
