"""Check audit semantics independently of frozen empirical results."""
import numpy as np
import pandas as pd
from yoyo.evaluation.clean_reversal_audit import flag_labels,matched_effect,ecdf,holm,FLAGS


def test_audit_flags_do_not_relabel_and_can_overlap():
    d=pd.DataFrame(dict(sustained_blocks_fraction=[.5,.75],max_close_drawdown_tr=[3.,1.],net_progress_tr=[2.,2.],positive_time_fraction=[.4,.9],clean_candidate=[True,True],quality=[3.,4.]))
    before=d.copy(deep=True);out=flag_labels(d)
    pd.testing.assert_frame_equal(d,before)
    assert out.review_flag_count.tolist()==[3,0]
    assert out.clean_candidate.tolist()==[True,True]
    assert len(out.loc[0,'review_flags'].split('; '))==3


def test_matched_effect_controls_stratum_baseline_and_weights():
    x=np.array([[.2],[.4],[.6],[.8]]); y=np.array([0.,1.,0.,1.]);groups=[np.array([0,1]),np.array([2,3])]
    np.testing.assert_allclose(matched_effect(x,y,groups),[.2])
    np.testing.assert_allclose(matched_effect(x,1-y,groups),[-.2])
    np.testing.assert_allclose(matched_effect(x,y,groups,np.array([0,0,2,2])),[.2])
    shifted=x.copy();shifted[2:]+=10
    np.testing.assert_allclose(matched_effect(shifted,y,groups),[.2])


def test_rank_scale_ties_and_holm_family_correction():
    reference=np.array([[1.],[2.],[2.],[4.]])
    np.testing.assert_allclose(ecdf(np.array([[0.],[2.],[5.]]),reference).ravel(),[0.,.5,1.])
    np.testing.assert_allclose(holm(np.array([.01,.04,.03])),[.03,.06,.06])
