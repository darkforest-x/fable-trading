"""Feature matching changes comparison membership before either queue starts."""
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v131_state_matched import same_membership,serial_flags


def test_same_state_qualification_requires_both_causal_anchors():
    f=pd.DataFrame(dict(trade_key=['a','a','b','b'],cohort=['signal','control']*2,pair_eligible=[True]*4))
    member,si,ci=same_membership(f,np.array([True,False,True,True]))
    assert member.tolist()==[False,False,True,True]
    assert si.tolist()==[2] and ci.tolist()==[3]


def test_shared_membership_rebuilds_occupancy_instead_of_filtering_old_fills():
    f=pd.DataFrame(dict(trade_key=['a','a','b','b'],cohort=['signal','control']*2,pair_eligible=[True]*4,
        anchor_i=[0,0,1,1],decision_i=[2,2,3,3],exit_i=[10,10,5,5],filled=[True]*4,status=['closed']*4))
    target=f.cohort.eq('signal').to_numpy()
    old=serial_flags(f,target)
    assert old.tolist()==[True,False,False,False]
    member,_,_=same_membership(f,np.array([True,False,True,True]))
    assert serial_flags(f,member&target).tolist()==[False,False,True,False]
