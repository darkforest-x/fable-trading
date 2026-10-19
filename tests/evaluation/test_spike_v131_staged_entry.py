"""Entry lifecycle boundaries independent of market-history optimization."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v131_staged_entry import leg_paths, candidate_returns, period_mask


def run(bars, opposite=False, side=1, trail=4, atr=1):
    a=np.asarray(bars,dtype=float)
    entry,stop=100.,90.
    if side==-1:
        a=200-a[:,[0,2,1,3]];stop=110.
    return leg_paths(side=side,entry=entry,stop0=stop,tick=.1,o=a[:,0],h=a[:,1],l=a[:,2],c=a[:,3],
        atr=np.full(len(a),atr),e=0,x=len(a)-1,opposite=opposite,trail=trail,depths=[.5])


@pytest.mark.parametrize('side',[1,-1])
def test_limit_fills_before_initial_stop_and_fee_basis(side):
    r=run([[100,101,89,91]],side=side)
    assert r['limit_50_offset']==0
    fill=100-side*5
    assert r['limit_50_price']==fill
    assert r['net_r']==pytest.approx(-1.02)
    assert r['limit_50_r']==pytest.approx(-.5-.002*fill/10)


def test_exact_touch_is_only_optimistic_fill():
    r=run([[100,101,95,98],[99,100,98,99]],opposite=True)
    assert r['limit_50_offset']==-1
    assert r['touch_50_offset']==0


def test_opposite_open_cancels_unfilled_limit_and_pending_reclaim():
    r=run([[100,104,94,102],[101,105,89,90]],opposite=True)
    assert r['reclaim_50_offset']==-1
    assert r['limit_50_offset']==0


def test_gap_stop_cancels_pending_reclaim():
    r=run([[100,104,94,102],[89,95,87,90]])
    assert r['reclaim_50_offset']==-1
    assert r['net_r']==pytest.approx(-1.12)


def test_gap_limit_price_improvement_when_protection_survives_open():
    r=run([[100,102,98,101],[93,95,89,92]])
    assert r['limit_50_price']==93
    assert r['limit_50_offset']==1


def test_no_add_below_trailed_stop_or_from_exit_bar_close():
    r=run([[100,125,99,121],[120,122,90,110]])
    assert r['actual_exit_price']==117
    assert r['limit_50_offset']==-1
    assert r['reclaim_50_offset']==-1


def test_reclaim_next_open_and_lifetime_counts_entry_bar():
    r=run([[100,104,94,102],[103,105,100,104],[104,105,103,104]],opposite=True)
    assert r['reclaim_50_price']==103
    assert r['reclaim_50_offset']==1
    frame=pd.DataFrame([r])
    p=dict(method='reclaim',fraction=.5,depth=.5,expiry=1)
    value,fill=candidate_returns(frame,p)
    assert value[0]==pytest.approx(.5*r['net_r']) and not fill[0]
    p['expiry']=3
    value,fill=candidate_returns(frame,p)
    assert value[0]==pytest.approx(.5*(r['net_r']+r['reclaim_50_r'])) and fill[0]


def test_constant_weight_affinity_including_no_fill():
    frame=pd.DataFrame([run([[100,104,94,102],[104,105,103,104]],opposite=True),
                        run([[100,104,98,102],[104,105,103,104]],opposite=True)])
    p=dict(method='limit',fraction=0,depth=.5,expiry=0)
    low,_=candidate_returns(frame,p)
    high,_=candidate_returns(frame,p|{'fraction':1})
    half,_=candidate_returns(frame,p|{'fraction':.5})
    np.testing.assert_allclose(half,(low+high)/2)


def test_chronological_split_excludes_crossing_trades():
    frame=pd.DataFrame(dict(entry_time=pd.to_datetime(['2024-12-01','2024-12-31','2025-01-01'],utc=True),
                            exit_time=pd.to_datetime(['2024-12-02','2025-01-02','2025-01-02'],utc=True)))
    cut=pd.Timestamp('2025-01-01',tz='UTC')
    assert period_mask(frame,'early',cut).tolist()==[True,False,False]
    assert period_mask(frame,'later',cut).tolist()==[False,False,True]


def test_reclaim_risk_cap_keeps_original_absolute_stop_budget():
    r=run([[100,104,94,102],[103,105,100,104],[104,105,103,104]],opposite=True)
    frame=pd.DataFrame([r|dict(side=1,initial_stop=90,initial_risk=10)])
    p=dict(method='reclaim_capped',fraction=.5,depth=.5,expiry=0)
    value,fill=candidate_returns(frame,p)
    assert fill[0]
    assert value[0]==pytest.approx(.5*r['net_r']+.5*r['reclaim_50_r']/1.3)
    assert .5+.5/1.3*1.3==pytest.approx(1.)


def test_zero_depth_is_favorable_close_confirmation():
    r=leg_paths(side=1,entry=100,stop0=90,tick=.1,o=np.array([100,102,104]),
        h=np.array([102,104,105]),l=np.array([100,101,103]),c=np.array([101,103,104]),
        atr=np.ones(3),e=0,x=2,opposite=True,trail=4,depths=[0])
    assert r['reclaim_00_offset']==1 and r['reclaim_00_price']==102
    assert r['limit_00_offset']==-1 and r['touch_00_offset']==0
