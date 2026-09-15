"""Synthetic checks for paired inference and binary diagnostic AUC."""
import pandas as pd
import pytest
from yoyo.evaluation.ifvg_lrl_study import paired_stats, binary_auc


def test_identical_pairs_have_zero_excess_and_p_one():
    c=pd.DataFrame(dict(matched=[True]*4,block=['a','a','b','b'],target_net_r=[1,-1,1,-1],control_net_r=[1,-1,1,-1]))
    s=paired_stats(c)
    assert s['excess']==0 and s['p']==1 and s['days']==2


def test_one_day_is_one_cluster_not_four_independent_wins():
    c=pd.DataFrame(dict(matched=[True]*4,block=['a']*4,target_net_r=[1]*4,control_net_r=[0]*4))
    s=paired_stats(c)
    assert .45 < s['p'] < .55
    assert s==paired_stats(c)


def test_missing_control_is_not_zero_return():
    c=pd.DataFrame(dict(matched=[False],block=['a'],target_net_r=[1],control_net_r=[float('nan')]))
    assert paired_stats(c)['matched']==0
    assert paired_stats(c)['p'] is None


def test_auc_ties_and_direction():
    t=pd.DataFrame(dict(censored=[False]*4,has_lrl=[True,True,False,False],net_r=[1,2,-1,-2]))
    assert binary_auc(t)==1
    t['has_lrl']=True
    assert binary_auc(t)==pytest.approx(.5)
    t['net_r']=1
    assert binary_auc(t) is None
