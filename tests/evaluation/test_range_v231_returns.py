"""Prevent reuse of stale stops and accidental population/identity loss."""
import pandas as pd
import pytest
from yoyo.evaluation.range_v231_returns import combine, select_cached


def source():
    return pd.DataFrame([dict(signal_i=i,side=1,touch_i=i,entry_i=i,entry=100.,stop=99.,key='0.5:up',level=99.5) for i in [10,20,30]])


def test_rematch_detected_by_stop_even_when_arrow_and_entry_unchanged():
    old=source();new=old.iloc[[0,2]].copy();new.loc[2,'stop']=98.
    mapped,changed=select_cached(old,new)
    assert mapped.pair_id.tolist()==[0,2] and changed==[2]


def execution():
    return pd.DataFrame([dict(pair_id=i,population=p,entry_i=10+i,side=1,entry=100.,stop=99.,end_i=99,segment=0,week=1,bucket=2,net_r=9.)
                         for i in [0,1,2] for p in ['signal','random']])


def test_delta_replaces_both_populations_and_reuses_only_retained_ids():
    old=execution();delta=old[old.pair_id==2].copy();delta['stop']=98.;delta['net_r']=-3.
    base=pd.concat([old[old.pair_id==0],delta])
    got=combine(old,delta,[0,2],[2],base)
    assert set(got.pair_id)=={0,2}
    assert got[got.pair_id==2].net_r.tolist()==[-3.,-3.]


def test_changed_stop_cannot_reuse_prior_execution_even_if_identity_matches():
    old=execution();base=old.copy();base.loc[0,'stop']=98.
    with pytest.raises(AssertionError):
        combine(old,None,[0,1,2],[],base)


def test_missing_control_fails_closed():
    old=execution();base=old.copy()
    with pytest.raises(AssertionError):
        combine(old.iloc[1:],None,[0,1,2],[],base)
