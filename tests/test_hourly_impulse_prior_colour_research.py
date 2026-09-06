"""Synthetic tri-state entry accounting and fixed-denominator V13 regressions."""
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_prior_colour_research as r
from yoyo.evaluation.hourly_impulse_k2_research import direct_requests, episode_ledger


def fixture(n=4):
    time=pd.date_range("2024-01-01",periods=n,freq="5D",tz="UTC")
    mothers=pd.DataFrame({"event_id":[f"c{i}" for i in range(n)],"signal_time":time-pd.Timedelta(hours=1),
        "decision_time":time,"direction":1,"fold":"2024H1"})
    trades=mothers.assign(entry_time=time,exit_time=time+pd.Timedelta(hours=2),
        outcome="colour_exit",closed=True,net_return=np.resize([-.01,.02,.03,-.04],n))
    old=episode_ledger(mothers,direct_requests(mothers)[1],trades)
    context=mothers.assign(prior_colour_gate_state=np.resize(["accepted","abstain","unknown","abstain"],n))
    return mothers,old,context


def base():
    return {"execution":{"max_hours":72,"cost_fraction":.002,"stop_first":True},"development_folds":deepcopy(r.FOLDS)}


def test_frozen_one_variable_and_deep_copy():
    config=r.frozen_config();r.verify_config(config,base())
    old,new=deepcopy(config["policies"])
    assert new.pop("entry_gate")=="prior4h_colour_at_k1_open"
    new["id"]=old["id"];assert new==old
    assert config["gate_contract"]["require_slope"] is False
    assert config["gate_contract"]["require_atr"] is False
    assert config["known_support"]=={"cases":251,"controls":462,"matched":154,"coverage_gate_unattainable":True}
    config["policies"][0]["ma_length"]=1
    assert r.frozen_config()["policies"][0]["ma_length"]==40


@pytest.mark.parametrize("key",list(r.GATE_CONTRACT))
def test_all_gate_contract_fields_frozen(key):
    c=r.frozen_config();c["gate_contract"][key]="changed"
    with pytest.raises(ValueError):r.verify_config(c,base())


@pytest.mark.parametrize("key,value",[("cost_fraction",.001),("max_hours",24),("stop_first",False)])
def test_no_economic_drift(key,value):
    b=base();b["execution"][key]=value
    with pytest.raises(ValueError):r.verify_config(r.frozen_config(),b)


def test_no_period_or_holdout_drift():
    b=base();b["development_folds"][-1][-1]="2026-01-01"
    with pytest.raises(ValueError):r.verify_config(r.frozen_config(),b)
    c=r.frozen_config();c["holdout_consumed"]=True
    with pytest.raises(ValueError):r.verify_config(c,base())


def test_three_states_preserve_population_and_true_unknown():
    _,old,context=fixture();copy=old.copy(deep=True)
    new=r.gated_episodes(old,context)
    assert new.event_id.tolist()==old.event_id.tolist()
    assert r.counts(new)=={"total":4,"accepted":1,"abstain":2,"unknown":1}
    assert new.episode_net_return.iloc[0]==-.01
    assert new.episode_net_return.iloc[[1,3]].eq(0).all()
    assert np.isnan(new.episode_net_return.iloc[2])
    assert new.policy_fee_fraction.iloc[0]==.002
    assert new.policy_fee_fraction.iloc[[1,3]].eq(0).all()
    assert np.isnan(new.policy_fee_fraction.iloc[2])
    assert new.observed.tolist()==[True,True,False,True]
    assert new.executed.tolist()==[True,False,False,False]
    assert new.completed_trade.equals(new.executed)
    assert new.entry_time.iloc[1:].isna().all() and new.exit_time.iloc[1:].isna().all()
    assert new.occupied_until.iloc[1]==old.mother_decision_time.iloc[1]
    assert new.occupied_until.iloc[2]==old.mother_deadline.iloc[2]
    r.assert_saved_parity(old.iloc[:1],new.iloc[:1])
    pd.testing.assert_frame_equal(old,copy)


@pytest.mark.parametrize("mutation",["duplicate","foreign","missing","badstate","nullstate","preexisting"])
def test_population_and_state_cannot_silently_change(mutation):
    _,old,context=fixture()
    if mutation=="duplicate":context.loc[0,"event_id"]="c1"
    elif mutation=="foreign":context.loc[0,"event_id"]="other"
    elif mutation=="missing":context=context.iloc[:2]
    elif mutation=="badstate":context.loc[0,"prior_colour_gate_state"]="reject"
    elif mutation=="nullstate":context.loc[0,"prior_colour_gate_state"]=None
    else:old["prior_colour_gate_state"]="accepted"
    with pytest.raises(ValueError):r.gated_episodes(old,context)


def test_known_abstention_can_be_observed_even_when_old_outcome_unknown():
    _,old,context=fixture()
    old.loc[1,"episode_net_return"]=np.nan;old.loc[1,"observed"]=False
    new=r.gated_episodes(old,context)
    assert new.loc[1,"episode_net_return"]==0 and new.loc[1,"observed"]
    ledger,_,info=r.mechanism_table(old,new)
    assert np.isnan(ledger.loc[1,"difference"])
    assert info["known_pairs"]==2
    assert info["missed_net_winners"]==0


def test_avoidance_also_counts_discarded_winners():
    _,old,context=fixture();new=r.gated_episodes(old,context)
    ledger,groups,info=r.mechanism_table(old,new)
    assert info["avoided_net_losers"]==1 and info["missed_net_winners"]==1
    assert info["avoided_loss_total_bp"]==400 and info["missed_winner_total_bp"]==200
    assert info["known_pairs"]==3
    assert ledger.difference.iloc[0]==0
    assert ledger.difference.iloc[1]==-.02
    assert ledger.difference.iloc[3]==.04
    assert np.isnan(groups.set_index("gate_state").loc["unknown","old_mean_net_bp"])


def test_three_own_control_gates_and_unknown_invalidate_whole_triplet():
    _,old,context=fixture()
    cases=r.gated_episodes(old,context.assign(prior_colour_gate_state="accepted"))
    _,control,control_context=fixture(3)
    control["parent_event_id"]="c0";control["event_id"]=["r0","r1","r2"]
    control_context["event_id"]=control.event_id
    control_context["prior_colour_gate_state"]=["accepted","abstain","accepted"]
    controls=r.gated_episodes(control,control_context)
    pairs,info=r.matched_episodes(cases,controls)
    assert info["paired_events"]==1 and len(pairs)==4
    assert pairs.iloc[0].control_mean_return==pytest.approx((-.01+0+.03)/3)
    assert pairs.iloc[1:].excess.isna().all()
    control_context.loc[2,"prior_colour_gate_state"]="unknown"
    pairs,info=r.matched_episodes(cases,r.gated_episodes(control,control_context))
    assert info["paired_events"]==0 and pairs.control_mean_return.isna().all()


def test_known_filter_does_not_create_occupancy_and_handled_is_not_traded():
    _,old,context=fixture()
    context["prior_colour_gate_state"]=["accepted","abstain","accepted","abstain"]
    serial=r.single_pending_ledger(r.gated_episodes(old,context))
    assert serial.portfolio_selected.all() and serial.executed.sum()==2
    assert len(serial)==4


def test_unknown_keeps_conservative_occupancy_not_zero():
    _,old,context=fixture()
    old.loc[1,"mother_decision_time"]=old.loc[0,"mother_decision_time"]+pd.Timedelta(hours=1)
    context["prior_colour_gate_state"]=["unknown","accepted","accepted","accepted"]
    serial=r.single_pending_ledger(r.gated_episodes(old,context))
    assert serial.loc[0,"portfolio_selected"] and not serial.loc[1,"portfolio_selected"]
    assert np.isnan(serial.loc[0,"episode_net_return"])


def test_all_abstain_candidate_is_zero_policy_not_fake_trades(tmp_path,monkeypatch):
    mothers,old,context=fixture()
    context["prior_colour_gate_state"]="abstain"
    trades=mothers.assign(closed=True,net_return=.01,outcome="colour_exit")
    inputs={"case":mothers,"control":mothers.assign(parent_event_id="c0")}
    # Empty control triplet allocation is not needed for this empty-trade branch.
    episodes={"case":old,"control":old.assign(parent_event_id="c0")}
    gate={"case":context,"control":context.assign(parent_event_id="c0")}
    serial=r.single_pending_ledger(old)
    anchor=({},dict.fromkeys(inputs,trades),episodes,None,serial)
    monkeypatch.setattr(r,"simulate_requests",lambda *a,**k:pytest.fail("No accepted request may replay"))
    monkeypatch.setattr(r,"matched_episodes",lambda c,x:(c[["event_id"]],{"coverage":0,"mean_excess_bp":np.nan,
        "effect":{"month_cluster_p":np.nan,"ci95_bp":[np.nan,np.nan],"mean_bp":np.nan}}))
    summary,selected,new,_,result=r.replay_gated(None,inputs,inputs,gate,anchor,tmp_path/"candidate",r.frozen_config())
    assert summary["metrics"]["events"]==0 and not summary["gates"]["samples"]
    assert summary["net_effect"]["mean_bp"]==0
    assert summary["gate_counts"]["case"]["abstain"]==4
    assert new["case"].episode_net_return.eq(0).all()
    assert selected["case"].empty and result.portfolio_selected.all()
