"""Frozen single-gate semantics and no future-dependent event selection."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from yoyo.evaluation import spike_v3_gate_diagnostic as diag


def signals():
    f=pd.DataFrame(dict(arm=['early','early','confirmed'],event_id=['a','b','c'],
        instrument=['X']*3,decision_i=[10,20,13],net_bp=[-100,1000,100]))
    for gate in set(sum(diag.FILTERS.values(),[])): f[gate]=True
    return f


def test_single_gates_do_not_accumulate_or_use_outcomes():
    f=signals();f.loc[0,'tag_original_volume']=False;f.loc[1,'tag_above_six']=False
    assert diag.select(f,'early','tag_original_volume').event_id.tolist()==['b']
    assert diag.select(f,'early','tag_above_six').event_id.tolist()==['a']
    changed=f.copy();changed['net_bp']=[1e9,-1e9,np.nan]
    assert diag.select(changed,'early','tag_original_volume').event_id.tolist()==['b']
    assert len(diag.select(f,'early','baseline'))==2


def test_unknown_tag_does_not_pass_and_unknown_gate_rejected():
    f=signals();f.loc[0,'tag_original_body']=None
    assert diag.select(f,'early','tag_original_body').event_id.tolist()==['b']
    with pytest.raises(ValueError): diag.select(f,'early','net_bp')
    with pytest.raises(ValueError): diag.select(f,'v2','baseline')


def test_confirmation_never_backdates_to_parent_and_denominator_stays():
    f=signals()
    labels=pd.DataFrame(dict(instrument=['X','X','Y'],event_i=[10,19,10],
        decision_time=pd.to_datetime(['2020-01-01Z'.replace('01Z','01T00:00Z')]*3),
        label=['positive']*3))
    early=diag.recall(labels,diag.select(f,'early','baseline'))
    child=diag.recall(labels,diag.select(f,'confirmed','baseline'))
    assert early.hit_1.tolist()==[True,True,False]
    assert child.hit_1.tolist()==[False,False,False]
    assert child.hit_6.tolist()==[True,False,False]
    assert len(child)==len(labels)
    assert_frame_equal(labels,labels.copy(deep=True))


def test_registered_thirteen_original_ingredient_tests():
    assert sum(map(len,diag.FILTERS.values()))==13
    assert diag.FILTERS['early']==['tag_recent_density']+diag.ORIGINAL_GATES
    assert diag.FILTERS['confirmed']==diag.ORIGINAL_GATES
