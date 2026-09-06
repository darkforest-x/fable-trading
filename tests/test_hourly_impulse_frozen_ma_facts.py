"""Synthetic fixed-support failure decomposition; no saved price archive."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.hourly_impulse_frozen_ma_facts import facts


def fixture():
    m=pd.DataFrame({"event_id":["a","b","c"],"net_return_before":[-.003,.002,np.nan],
        "net_return_after":[-.001,-.001,np.nan],"gross_return_after":[.001,.001,np.nan],
        "frozen_exit":[True,True,False],"win_loss_transition":["loss_to_loss","win_to_loss","unknown"],
        "outcome_after":["frozen_ma_exit","frozen_ma_exit","data_gap_censored"],
        "frozen_ma_status":["structure_exit","structure_exit","unknown_source"]})
    for c in ["entry_time_before","direction_before","entry_price_before","initial_stop_before","ma_before",
              "exit_time_before","exit_time_after","exit_price_before","exit_price_after","hold_minutes_before","hold_minutes_after",
              "frozen_ma_trigger_close","frozen_ma_trigger_open_time","frozen_ma_trigger_available_at"]:m[c]=1
    match=pd.DataFrame({"event_id":["a","b","c"],"assigned_controls":[3,0,0]})
    geometry=pd.DataFrame({"population":["case"]*3,"event_id":["a","b","c"],"geometry_bin":["inside"]*3,"entry_distance_r":[.5]*3})
    return m,match,geometry


def test_unknown_kept_cost_losses_and_retrospective_examples():
    m,a,g=fixture();info,tables=facts(m,a,g)
    assert info["all_pairs"]==3 and info["unknown_pairs"]==1
    assert info["all_delta_bp"]==pytest.approx(-5)
    assert info["total_event_delta_bp"]==pytest.approx(-10)
    row=tables["exit_failure_groups"].set_index("outcome").loc["frozen_ma_exit"]
    assert row.losses==row.positive_gross_cost_losses==2
    assert row.gross_nonpositive_losses==0
    assert tables["matching_strata"].n.sum()==3
    examples=tables["retrospective_examples"]
    assert len(examples)==2 and examples.event_id.is_unique
    assert examples.loc[examples.example_selection.str.contains("sacrifices"),"net_change_bp"].lt(0).all()
    assert examples.loc[examples.example_selection.str.contains("savings"),"net_change_bp"].gt(0).all()


def test_asymmetric_unknown_does_not_change_paired_mean_denominator():
    m,a,g=fixture();m.loc[2,"net_return_before"]=.05
    info,tables=facts(m,a,g)
    assert info["unknown_pairs"]==1
    for table in (tables["matching_strata"],tables["geometry_outcomes"]):
        known=table.loc[table.known.gt(0)]
        np.testing.assert_allclose(known.new_net_bp-known.old_net_bp,known.delta_bp)


def test_one_sided_changes_do_not_create_fake_sacrifices():
    m,a,g=fixture();m.loc[1,"net_return_after"]=.004
    _,tables=facts(m,a,g)
    assert len(tables["retrospective_examples"])==2
    assert tables["retrospective_examples"].example_selection.str.contains("savings").all()


@pytest.mark.parametrize("which",[0,1,2])
def test_no_silent_population_drop(which):
    frames=list(fixture());frames[which]=frames[which].iloc[1:]
    with pytest.raises(ValueError):facts(*frames)


def test_partial_control_group_rejected():
    m,a,g=fixture();a.loc[0,"assigned_controls"]=2
    with pytest.raises(ValueError,match="triples"):facts(m,a,g)
