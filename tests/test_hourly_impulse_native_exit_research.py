"""V15 frozen specification, direct timing and paired-ledger synthetic checks."""
from copy import deepcopy
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_native_exit_research as r
from test_hourly_impulse_launch_research import base, population, trades


def test_frozen_config_matches_and_native_change_only():
    config=json.loads((r.EXPERIMENT/"config.json").read_text())
    r.verify_config(config,base())
    a,b=deepcopy(config["policies"])
    assert a.pop("management_minutes")==5 and b.pop("management_minutes")==15
    a.pop("id"); b.pop("id")
    assert a==b
    assert len(r.CONTEXT_INPUTS)==2 and len(r.OUTCOME_INPUTS)==7
    assert set(r.CONTEXT_INPUTS).isdisjoint(r.OUTCOME_INPUTS)
    assert len(r.SOURCES)==len(set(r.SOURCES))
    r.validate_population(*population())


@pytest.mark.parametrize("key,value", [("management_minutes",5),("ma_length",20),("decision_minutes",15),
    ("exit_mode","colour"),("confirmations",2),("launch_deadline_minutes",60),("frozen_ma_exit",True)])
def test_policy_mutation_rejected(key,value):
    c=r.frozen_config(); c["policies"][1][key]=value
    with pytest.raises(ValueError): r.verify_config(c,base())


@pytest.mark.parametrize("change",["fee","horizon","stop","fold","gate","holdout","contract"])
def test_contract_mutation_rejected(change):
    c=r.frozen_config(); b=base()
    if change=="fee": b["execution"]["cost_fraction"]=.001
    elif change=="horizon": b["execution"]["max_hours"]=24
    elif change=="stop": b["execution"]["stop_first"]=False
    elif change=="fold": b["development_folds"][0][1]="2022-01-01"
    elif change=="gate": c["selection"]["matched_coverage"]=.6
    elif change=="holdout": c["holdout_consumed"]=True
    else: c["native_contract"]["context_freeze_before_outcomes"]=False
    with pytest.raises(ValueError): r.verify_config(c,b)


def direct():
    t=pd.Timestamp("2024-01-01",tz="UTC")
    return pd.DataFrame({"decision_time":[t],"mother_decision_time":[t],"mother_deadline":[t+pd.Timedelta(hours=72)],"wait_hours":[0]})


def test_direct_clock():
    r.validate_direct_context(direct())


@pytest.mark.parametrize("column",["decision_time","mother_decision_time","mother_deadline","wait_hours"])
def test_delayed_request_rejected(column):
    f=direct(); f[column]+=1 if column=="wait_hours" else pd.Timedelta(minutes=5)
    with pytest.raises(ValueError): r.validate_direct_context(f)


def pair():
    a,b=trades()
    for f in (a,b):
        f["mother_decision_time"]=f.entry_time
        f["max_favourable_r"]=[1,2,0,0]
    return a,b


def test_mechanics_full_population_and_unknown():
    out,groups,info=r.paired_mechanics(*pair())
    assert len(out)==4 and groups.n.sum()==4 and info["known"]==3
    assert info["transitions"]=={"loss_to_win":1,"win_to_loss":1,"loss_to_loss":1,"flat_or_unknown":1}
    assert pd.isna(out.delta_net_bp.iloc[3]) and out.delta_net_bp.iloc[2]==0
    assert info["earlier_exits"]==2 and info["same_exit_time"]==2


def test_candidate_can_delay_or_hurt_no_positive_only_filter():
    a,b=pair(); b.loc[0,"exit_time"]+=pd.Timedelta(hours=4)
    b.loc[0,"hold_minutes"]+=240; b.loc[0,"net_return"]=-.03
    out,_,info=r.paired_mechanics(a,b)
    assert out.delta_net_bp.iloc[0]<0 and info["later_exits"]==1
    b.loc[0,"net_return"]=0
    assert r.paired_mechanics(a,b)[0].outcome_transition.iloc[0]=="flat_or_unknown"


@pytest.mark.parametrize("column",["entry_price","direction","initial_stop","signal_atr","risk_pct"])
def test_economic_pair_identity_preserved(column):
    a,b=pair(); b.loc[0,column]+=1
    with pytest.raises((ValueError,AssertionError)): r.paired_mechanics(a,b)


def initial():
    t=pd.Timestamp("2024-01-01",tz="UTC")
    return pd.DataFrame({"risk_pct":[.01,.01,np.nan],"risk_atr":[1,1,np.nan],
        "mg_entry_state":["aligned","unknown","opposite"],"transition_initial_state":["aligned","unknown","unknown"],
        "mg_entry_reason":["valid","missing_management","valid"],"transition_initial_reason":["valid","missing_management","entry_not_validated"],
        "mg_entry_known":[True,False,True],"mg_entry_side":[1,np.nan,-1],"transition_initial_side":[1,np.nan,np.nan],
        "mg_entry_bar_open":[t,t,t],"transition_initial_open_time":[t,pd.NaT,pd.NaT]})


def test_initial_unknown_candidate_diagnostic_and_invalid_risk_not_force_equal():
    r.assert_native_initial_state(initial())


@pytest.mark.parametrize("column,value",[("transition_initial_state","opposite"),("transition_initial_side",-1),
    ("transition_initial_open_time",pd.Timestamp("2024-01-02",tz="UTC"))])
def test_initial_disagreement_fails(column,value):
    t=initial(); t.loc[0,column]=value
    with pytest.raises((AssertionError,ValueError)): r.assert_native_initial_state(t)


def test_source_order_freezes_both_contexts_before_outcome_hash_and_replay():
    source=inspect.getsource(r.run)
    assert source.index('for arm,policy') < source.index('context_frozen.json')
    assert source.index('context_frozen.json') < source.index('pin(ROOT/PARENT,OUTCOME_INPUTS)') < source.index('old=replay_arm')
    assert source.index('anchor_parity.json') < source.index('new=replay_arm')
    assert 'pin(ROOT/PARENT,INPUTS)' not in source
    assert 'simulate_requests(' not in inspect.getsource(r.replay_arm)
