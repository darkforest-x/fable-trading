"""Saved-evidence denominator decomposition synthetic fixtures only."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_launch_facts import facts


def fixture():
    m=pd.DataFrame({"event_id":["a","b","c"],"net_return_before":[.03,-.04,np.nan],
        "net_return_after":[-.01,-.01,np.nan],"gross_return_after":[-.008,-.008,np.nan],
        "win_loss_transition":["win_to_loss","loss_to_loss","unknown"],
        "timeout_exit":[True,True,False],"outcome_after":["launch_timeout_exit"]*2+["right_censored"],
        "launch_status":["timeout_exit","timeout_exit","unknown_source"]})
    matching=pd.DataFrame({"event_id":["a","b","c"],"assigned_controls":[3,0,0]})
    return m,matching


def test_unmatched_not_dropped_and_unknown_not_zero():
    info,strata,transitions,exits,examples=facts(*fixture())
    assert info["all_pairs"]==3 and info["unknown_pairs"]==1
    assert strata.n.sum()==3 and strata.known.sum()==2
    assert strata.loc[strata.support.eq("matched"),"delta_bp"].iloc[0]==pytest.approx(-400)
    assert strata.loc[strata.support.eq("unmatched"),"delta_bp"].iloc[0]==pytest.approx(300)
    assert info["all_delta_bp"]==pytest.approx(-50)
    assert transitions.n.sum()==exits.n.sum()==3
    assert set(examples.example_selection)=={"three_largest_sacrifices","three_largest_savings"}


@pytest.mark.parametrize("mutation",["duplicate","drop","partial"])
def test_bad_matching_rejected(mutation):
    m,c=fixture()
    if mutation=="duplicate": m.loc[0,"event_id"]="b"
    elif mutation=="drop": c=c.iloc[1:]
    else: c.loc[0,"assigned_controls"]=2
    with pytest.raises(ValueError): facts(m,c)
