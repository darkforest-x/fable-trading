"""Causality, admission and native path contracts for Task62 V4."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.eth_stoch_confirm_exits import Policy, trace
from yoyo.evaluation.eth_stoch_extreme_stop import (
    feature_extrema, filter_mask, stops, matched_indices, native_library,
    native_replay, REASONS,
)


def test_threshold_window_is_current_plus_prior_three_and_strict():
    k = pd.Series([3.,10.,10.,10.,10.,96.19,96.2])
    low, high = feature_extrema(k,[3])[3]
    rows = pd.DataFrame(dict(side=[1,1,-1,-1],kmin_3=low[[3,4,5,6]],kmax_3=high[[3,4,5,6]]))
    assert filter_mask(rows,4,96.19,3).tolist() == [True,False,False,True]


def test_future_mutation_cannot_change_existing_filter_windows():
    a = pd.Series([50.,3.,6.,9.,90.,99.,95.,60.])
    b = a.copy(); b.iloc[5:] = [0.,0.,0.]
    for lb in [0,1,2,3,5]:
        x=feature_extrema(a,[lb])[lb];y=feature_extrema(b,[lb])[lb]
        for old,new in zip(x,y):
            np.testing.assert_equal(old[:5],new[:5])


def test_smaller_buffer_keeps_structure_and_cap_only_skips():
    rows = pd.DataFrame(dict(side=[1,-1,1],entry=[100.,100.,98.5],stop=[97.,103.,98.]))
    stop,risk = stops(rows,.25)
    np.testing.assert_allclose(stop,[97.75,102.25,98.75])
    np.testing.assert_allclose(risk,[2.25,2.25,-.25])
    assert ((risk>0)&(risk<=2)).tolist()==[False,False,False]
    assert ((risk>0)&(risk<=4)).tolist()==[True,True,False]


def test_control_uses_same_gate_and_group_without_replacing_missing():
    rows=pd.DataFrame(dict(is_signal=[True,True,False,False],raw_side=[1,-1,0,0],group_id=[0,1,0,1]))
    sig,left,right,missing=matched_indices(rows,np.array([True,True,True,False]),1)
    assert sig.tolist()==[0,1] and left.tolist()==[0] and right.tolist()==[2] and missing.tolist()==[1]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    return native_library(tmp_path_factory.mktemp('native_stoch'))


@pytest.mark.parametrize('side',[1,-1])
def test_native_preserves_gap_ambiguity_and_next_minute_activation(lib,side):
    bars=np.array([[100,102,99.2,101],[101,111,99.8,110],[98,100,97,99]],float)
    if side==-1:
        bars=200-bars[:,[0,2,1,3]]
    ctx=dict(zip(('o','h','l','c'),bars.T));ctx['arrow']=np.zeros(1)
    events=pd.DataFrame(dict(entry_i=[0,0,0],end_i=[1,2,3],side=[side]*3,entry=[100.]*3))
    stop=np.array([100-side]*3,dtype=float)
    arms=[Policy('base',target=10,target_tick=.01),Policy('price',target=10,be=1,target_tick=.01),Policy('net',target=10,be=.5,net_be=True,target_tick=.01)]
    got=native_replay(lib,ctx,events,stop,arms,.002)
    for i,r in events.iterrows():
        for j,p in enumerate(arms):
            z=trace(ctx,0,side,100,stop[i],p,end_i=int(r.end_i))
            assert got['exit_i'][i,j]==z['exit_i']
            assert REASONS[got['reason'][i,j]]==z['reason']
            assert got['exit_price'][i,j]==pytest.approx(z['exit_price'])
            assert bool(got['be_activated'][i,j])==z['be_activated']


def test_nonpositive_new_risk_is_not_a_trade(lib):
    ctx=dict(o=np.array([100.]),h=np.array([101.]),l=np.array([99.]),c=np.array([100.]))
    events=pd.DataFrame(dict(entry_i=[0],end_i=[1],side=[1],entry=[100.]))
    got=native_replay(lib,ctx,events,np.array([100.1]),[Policy('base',target=10)],.002)
    assert got['reason'][0,0]==255 and got['exit_i'][0,0]==-1 and np.isnan(got['exit_price'][0,0])
