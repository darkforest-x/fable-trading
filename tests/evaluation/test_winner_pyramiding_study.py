"""Focused matching and inference tests; no market source reads."""
import numpy as np
import pandas as pd

from yoyo.evaluation.winner_pyramiding_study import choose_control, block_inference, period_bounds


def test_control_has_same_month_bucket_and_never_same_event():
    ready=np.array([True]*7)
    months=np.array(["a","a","a","b","a","a","b"])
    bins=np.array([1,2,1,1,3,1,1])
    for seed in range(30):
        result=choose_control(ready,months,bins,0,"event",seed)
        assert result in (2,5)
    ready[[2,5]]=False
    assert choose_control(ready,months,bins,0,"event",1) is None


def test_month_blocked_sign_flips_do_not_count_rows_as_independent():
    x=pd.DataFrame({"month":["a"]*100+["b"]*100,"delta_r":[1.]*200})
    result=block_inference(x,reps=100)
    assert result["months"]==2
    assert result["p_one_sided"]==.25
    assert result["mean_delta"]==1


def test_zero_delta_cannot_support_superiority():
    x=pd.DataFrame({"month":["a","b"],"delta_r":[0.,0.]})
    result=block_inference(x,reps=100)
    assert result["p_one_sided"]==1
    assert result["ci_low"]==result["ci_high"]==0


def test_control_excludes_all_true_signal_times_before_drawing():
    for seed in range(30):
        chosen=choose_control(np.ones(4,bool),np.array(["a"]*4),np.ones(4),
            0,"event",seed,excluded=np.array([True,True,False,True]))
        assert chosen==2


def test_entry_exactly_on_temporal_cut_is_in_later_period():
    index=pd.date_range("2025-09-09T22:00Z",periods=6,freq="h")
    split=pd.Timestamp("2025-09-10T00:00Z")
    earlier,early_end=period_bounds(index,index[0],split)
    later,late_end=period_bounds(index,split,index[-1]+pd.Timedelta(hours=1))
    signal_i=1;entry_i=signal_i+1
    assert index[entry_i]==split
    assert not earlier[signal_i] and later[signal_i]
    assert entry_i==early_end and entry_i<late_end
