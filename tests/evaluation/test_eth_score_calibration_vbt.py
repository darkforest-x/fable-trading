"""Run with isolated vectorbt-python: cached fill capacity and cost parity."""
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_score_calibration_vbt import cached_portfolios,choose_rule


def fixture():
    # A reserves the slot before B; C submits at A's exit close. D enters/exits
    # in one bar, and E may reserve at that same close. Both sides are covered.
    rows=[]
    for name,submit,entry,exit_,side,ep,xp,reason in [
        ('a',1,2,5,1,100.,101.,4),('b',2,3,4,-1,100.,101.,1),
        ('c',5,6,8,-1,100.,99.,4),('d',8,9,9,1,100.,99.,1),
        ('e',9,10,11,1,100.,100.5,0)]:
        fee=.0002*ep+(.0002 if reason==4 else .0005)*xp
        rows.append(dict(trade_id=name,submission_i=submit,entry_i=entry,exit_i=exit_,
                         side=side,entry=ep,exit_price=xp,risk=1.,reason=reason,net_r=side*(xp-ep)-fee))
    return pd.DataFrame(rows)


def test_vectorized_capacity_equals_frozen_native_timing_and_scalar_column():
    d=fixture();masks=np.column_stack([np.ones(len(d),bool),np.array([False,True,True,True,True]),np.zeros(len(d),bool)])
    out,audit=cached_portfolios(d,masks,['all','skip_a','none'])
    assert list(out['all'].trade_id)==['a','c','d','e']
    assert list(out['skip_a'].trade_id)==['b','c','d','e']
    assert out['none'].empty and audit['orders']==16
    scalar,_=cached_portfolios(d,masks[:,1:2],['skip_a'])
    np.testing.assert_array_equal(out['skip_a'].trade_id,scalar['skip_a'].trade_id)
    np.testing.assert_allclose(out['skip_a'].vbt_net_r,scalar['skip_a'].vbt_net_r,rtol=0,atol=1e-12)


def test_calibration_rejects_small_or_losing_candidates_and_preserves_ties():
    empty=dict(name='none',net_r=0.,eligible=True)
    assert choose_rule([empty,dict(name='small',net_r=10.,eligible=False),dict(name='loss',net_r=-1.,eligible=True)])==empty
    a=dict(name='raw_zero',net_r=1.,eligible=True);b=dict(name='q50',net_r=1.,eligible=True)
    assert choose_rule([empty,a,b])==a


if __name__=='__main__':
    test_vectorized_capacity_equals_frozen_native_timing_and_scalar_column()
    test_calibration_rejects_small_or_losing_candidates_and_preserves_ties()
    print('2 VectorBT behavioral tests passed')
