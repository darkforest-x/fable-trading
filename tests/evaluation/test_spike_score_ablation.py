"""A score ablation must preserve unknowns, execution anchors, and controls."""
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_score_ablation import masks, select_orders, cache_matches


def test_score_removal_keeps_unknown_scores_and_common_arm_is_explicit():
    s=pd.DataFrame({'stored':[.6,.59,np.nan], 'causal':[.59,.6,np.nan],
                    'minute':[.59,.59,np.nan]},index=['old','causal','unknown'])
    m=masks(s,{'score_cut':.6})
    assert list(s.index[m['old_touch_score']])==['old']
    assert list(s.index[m['causal_5m']])==['causal']
    assert not m['causal_1m'].any()
    assert m['no_score_all'].all() and m['no_score_common'].tolist()==[True,True,False]


def test_controls_survive_without_independent_score_filtering():
    rows=pd.DataFrame([dict(event_id=e,reclaim_minutes=m,exit_mode=t,control_id=c)
        for e in ['selected','other'] for m in [5,60] for t in ['near_monday_edge','opposite_1618'] for c in [0,1,2,3]])
    got=select_orders(rows,['selected'])
    assert len(got)==4 and set(got.control_id)=={0,1,2,3}
    assert set(got.reclaim_minutes)=={60} and set(got.exit_mode)=={'near_monday_edge'}


def test_cache_rejects_one_tick_stop_or_different_fill_clock():
    order=dict(entry=100.,stop=82.,target_price=105.,native_touch_i=4)
    row=dict(entry=100.,stop=82.,target_price=105.,entry_i=4)
    assert cache_matches(row,order,1e-6)
    assert not cache_matches(dict(row,stop=82.000001),order,1e-6)
    assert not cache_matches(dict(row,entry_i=5),order,1e-6)
