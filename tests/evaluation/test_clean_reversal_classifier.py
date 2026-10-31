"""Behavioral checks for binary-target semantics and unchanged matched cohorts."""
import numpy as np
import pandas as pd
from yoyo.evaluation.clean_reversal_classifier import training_target,paired_comparison


def test_binary_target_ignores_quality_magnitude_and_owner_metadata():
    d=pd.DataFrame(dict(clean_candidate=[True,True,False,False],quality=[2.01,34.6,1.9,0.],owner_confirmed_positive=[False,True,False,False],week_start=pd.to_datetime(['2025-01-06']*4,utc=True),side=['upper']*4))
    a=training_target(d,'full',np.random.default_rng(1))
    d.quality*=1000;d.owner_confirmed_positive=~d.owner_confirmed_positive
    b=training_target(d,'full',np.random.default_rng(2))
    assert a.tolist()==b.tolist()==[1,1,0,0]
    assert training_target(d,'shuffled_training_labels',np.random.default_rng(3)).sum()==2


def test_identical_admission_sets_have_zero_paired_difference():
    d=pd.DataFrame(dict(event_id=['a','b','c','d'],clean_candidate=[True,False,True,False],top_decile=[True,False,False,True],week=['2025-01-06','2025-01-06','2025-01-13','2025-01-13'],symbol=['BTC']*4,fold=['2025Q1']*4,vol_bucket=[0]*4))
    out=paired_comparison(d,d.iloc[::-1].copy())
    assert out['hit_rate_difference']==out['matched_lift_difference']==0
    assert out['hit_rate_difference_95ci']==out['matched_lift_difference_95ci']==[0.,0.]
    assert out['shared_selected']==2 and out['new_only']==out['old_only']==0
