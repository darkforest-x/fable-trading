"""Exact-close intersection and initial-risk contracts; no outcome-driven gating."""
import numpy as np
import pandas as pd

from yoyo.evaluation.range_entry_clock import watched_entries
from yoyo.evaluation.range_mtf_confluence import watch_state,screenshot_candidates,gate_rows


def context(hour,quarter):
    n=len(hour);index=pd.date_range('2026-02-03T00:05Z',periods=n,freq='5min').as_unit('ns')
    frame=pd.DataFrame(dict(open=np.full(n,100.),high=np.full(n,103.),low=np.full(n,97.),close=np.full(n,100.)),index=index)
    signal,observed=watched_entries(hour,quarter)
    return dict(frame=frame,bars=frame.to_numpy(),hour=np.array(hour),quarter=np.array(quarter),signal=signal,observed=observed)


def test_latest_hour_refresh_blocks_same_close_even_when_same_direction():
    h=[1,0,0,1,0,-1,0];q=[1,0,1,1,1,-1,-1]
    signals,since=watched_entries(h,q)
    np.testing.assert_array_equal(signals,[0,0,1,0,1,0,-1])
    side,at=watch_state(h)
    np.testing.assert_array_equal(side,[1,1,1,1,1,-1,-1])
    np.testing.assert_array_equal(at,[0,0,0,3,3,5,5])
    assert since[4]==3


def test_prior_mtf_arrow_is_not_a_simultaneous_entry_signal():
    ctx=context([1,0,0,0,0,0,0,0,0],[0,0,1,0,0,0,0,0,0])
    m=pd.DataFrame([dict(pair_id=4,signal_i=0,entry_i=2,side=1,entry=100.,key='0.5:up',level=98.)])
    base=pd.DataFrame([dict(pair_id=4,population='signal',entry_i=8)])
    got=gate_rows(ctx,m,base).iloc[0]
    assert got.paired_arrow_was_mtf and not got.strict_both_now


def test_same_close_same_side_qualifies_but_new_hour_refresh_blocks():
    m=pd.DataFrame([dict(pair_id=4,signal_i=2,entry_i=2,side=1,entry=100.,key='0.5:up',level=98.)])
    base=pd.DataFrame([dict(pair_id=4,population='signal',entry_i=8)])
    ctx=context([1,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,1])
    assert gate_rows(ctx,m,base).strict_both_now.iloc[0]
    ctx=context([1,0,0,0,0,0,0,0,1],[0,0,0,0,0,0,0,0,1])
    assert not gate_rows(ctx,m,base).strict_both_now.iloc[0]


def test_screenshot_signal_stop_and_equal_relative_control_risk():
    ctx=context([0]*12,[0]*12)
    base=pd.DataFrame([dict(pair_id=p,population=pop,entry_i=i,end_i=11,side=s,entry=100. if pop=='signal' else 200.,stop=0.)
        for p,s in [(1,1),(2,-1)] for pop,i in [('signal',8),('random',11)]])
    got=screenshot_candidates(ctx,base,.1,dict(stop_window_5m=5,buffer_percent=.05))
    s=got[got.population=='signal'].set_index('side');r=got[got.population=='random'].set_index('side')
    assert abs(s.loc[1,'stop']-96.9)<1e-10 and abs(s.loc[-1,'stop']-103.1)<1e-10
    np.testing.assert_allclose(r.risk,2*s.risk,atol=1e-10,rtol=0)
    before=got.copy();ctx['frame'].iloc[9:]*=10
    after=screenshot_candidates(ctx,base,.1,dict(stop_window_5m=5,buffer_percent=.05))
    pd.testing.assert_frame_equal(before,after)
