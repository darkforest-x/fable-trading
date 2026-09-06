"""Frozen V17 contract and all-intention attribution without price-file access."""
from copy import deepcopy
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_failed_launch_research as r
from test_hourly_impulse_launch_research import base


def test_frozen_config_is_one_complementary_exit_switch():
    config=json.loads((r.EXPERIMENT/"config.json").read_text())
    r.verify_config(config,base())
    a,b=deepcopy(config["policies"]);a.pop("id");b.pop("id")
    assert b.pop("fast_failed_launch_exit") is True and a==b
    assert a["fast_partial_fraction"]==.5 and a["management_minutes"]==15
    assert config["parent_results"].endswith("v16/results/candidate")
    assert len(r.INPUTS)==7 and len(r.SOURCES)==len(set(r.SOURCES))
    assert config["failed_launch_contract"]["final_path_unchanged"] is False
    assert config["failed_launch_contract"]["serial_recomputed_for_each_arm"] is True


@pytest.mark.parametrize("key,value",[("fast_failed_launch_exit",False),("fast_partial_fraction",1),
    ("management_minutes",5),("exit_mode","colour"),("ma_length",20),("confirmations",2),
    ("launch_deadline_minutes",60),("frozen_ma_exit",True)])
def test_policy_mutation_refused(key,value):
    config=r.frozen_config();config["policies"][1][key]=value
    with pytest.raises(ValueError):r.verify_config(config,base())


@pytest.mark.parametrize("change",["fee","duration","stop","fold","coverage","holdout","threshold","serial"])
def test_frozen_design_mutation_refused(change):
    c=r.frozen_config();b=base()
    if change=="fee":b["execution"]["cost_fraction"]=.001
    elif change=="duration":b["execution"]["max_hours"]=24
    elif change=="stop":b["execution"]["stop_first"]=False
    elif change=="fold":b["development_folds"][0][1]="2022-01-01"
    elif change=="coverage":c["selection"]["matched_coverage"]=.6
    elif change=="holdout":c["holdout_consumed"]=True
    elif change=="threshold":c["failed_launch_contract"]["full_exit_if_open_gross_not_above"]=.003
    else:c["failed_launch_contract"]["serial_recomputed_for_each_arm"]=False
    with pytest.raises(ValueError):r.verify_config(c,b)


def ledger():
    t=pd.Timestamp("2024-01-01",tz="UTC")
    before=pd.DataFrame({"event_id":["winner","loser","same","unknown"],
        "mother_decision_time":[t]*4,"entry_time":[t]*4,"entry_price":[100.]*4,
        "direction":[1]*4,"initial_stop":[95.]*4,"signal_atr":[1.]*4,
        "risk_pct":[.05]*4,"risk_atr":[5.]*4,"exit_time":[t+pd.Timedelta(hours=2)]*4,
        "exit_price":[104.,96.2,102.4,100.],"outcome":["transition_colour_exit"]*3+["data_gap_censored"],
        "hold_minutes":[120]*4,"closed":[True,True,True,False],
        "net_return":[.03,-.04,.01,np.nan],"max_favourable_r":[2,0,1,0],
        "partial_fraction":[.5,0,.5,0],"exit_remaining_fraction":[.5,1,.5,1],
        "realised_partial_gross_return":[.012,0,0,0],"partial_fast_fill_count":[1,0,1,0],
        "partial_exit_time":[t+pd.Timedelta(minutes=30),pd.NaT,t+pd.Timedelta(minutes=30),pd.NaT]})
    after=before.copy();changed=[0,1,3]
    after.loc[changed,"outcome"]="fast_failed_launch"
    after.loc[changed,"exit_time"]=t+pd.Timedelta(minutes=15)
    after.loc[changed,"exit_price"]=[100.,99.2,99.7]
    after.loc[changed,"hold_minutes"]=15;after.loc[changed,"closed"]=True
    after.loc[changed,"net_return"]=[-.002,-.01,-.005]
    after.loc[changed,"partial_fraction"]=0;after.loc[changed,"partial_fast_fill_count"]=0
    after.loc[changed,"exit_remaining_fraction"]=1;after.loc[changed,"realised_partial_gross_return"]=0
    after.loc[changed,"partial_exit_time"]=pd.NaT
    after["failed_launch_count"]=[1,1,0,1]
    return before,after


def test_full_population_includes_sacrificed_winner_and_unknown():
    out,groups,info=r.failed_launch_mechanics(*ledger())
    assert len(out)==4 and groups.n.sum()==4 and info["known"]==3
    assert info["failed_launch_count"]==3 and info["unchanged_paths"]==1
    assert info["failed_improved"]==1 and info["failed_hurt"]==1
    assert info["failed_unknown_pairs"]==1 and info["sacrificed_recoveries"]==1
    assert info["prior_partial_paths_cut"]==1
    assert info["baseline_partial_count"]==2 and info["candidate_partial_count"]==1
    assert pd.isna(out.loc[out.event_id.eq("unknown"),"delta_net_bp"]).all()


@pytest.mark.parametrize("column",["entry_price","initial_stop","direction","risk_pct"])
def test_entry_risk_cannot_move(column):
    b,a=ledger();a.loc[0,column]+=1
    with pytest.raises((AssertionError,ValueError)):r.failed_launch_mechanics(b,a)


@pytest.mark.parametrize("column,value",[("net_return",.1),("exit_price",130.),
    ("hold_minutes",10),("outcome","hard_stop"),("partial_fraction",0)])
def test_nontriggered_path_is_old_column_identical(column,value):
    b,a=ledger();a.loc[2,column]=value
    with pytest.raises((AssertionError,ValueError)):r.failed_launch_mechanics(b,a)


@pytest.mark.parametrize("column,value",[("partial_fraction",.5),("exit_remaining_fraction",.5),
    ("realised_partial_gross_return",.002),("partial_fast_fill_count",1),("closed",False),
    ("failed_launch_count",0),("net_return",.001),("net_return",np.nan)])
def test_failed_full_accounting_contract_refuses_impossible_fields(column,value):
    b,a=ledger();a.loc[0,column]=value
    with pytest.raises((AssertionError,ValueError)):r.failed_launch_mechanics(b,a)


def test_failed_exit_not_later_than_baseline():
    b,a=ledger();a.loc[0,"exit_time"]=b.loc[0,"exit_time"]+pd.Timedelta(minutes=5)
    with pytest.raises(ValueError,match="after"):r.failed_launch_mechanics(b,a)


def test_baseline_censor_and_new_full_can_share_timestamp_without_imputing_return():
    b,a=ledger();a.loc[3,"exit_time"]=b.loc[3,"exit_time"]
    out,_,info=r.failed_launch_mechanics(b,a)
    assert info["failed_unknown_pairs"]==1 and out.delta_net_bp.isna().sum()==1


def test_exact_zero_new_net_is_not_sacrificed_positive_profit():
    b,a=ledger();a.loc[1,"net_return"]=0
    out,_,_=r.failed_launch_mechanics(b,a)
    assert out.loc[out.event_id.eq("loser"),"outcome_transition"].item()=="flat_or_unknown"


def test_source_freeze_then_old_anchor_then_candidate_and_serial_replay():
    source=inspect.getsource(r.run)
    assert source.index('"context_frozen.json"')<source.index('pin(ROOT/PARENT,INPUTS)')<source.index('old=replay_arm')<source.index('new=replay_arm')
    assert 'parent_prefix=""' in source and 'simulator=simulate_dual' in source
    assert 'paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])' in source
    assert "assert_final_path" not in source and "serial_fixed" not in source


def test_edge_export_keeps_arms_and_source_json_with_empty_schema():
    empty=r.export_edges({"baseline":{"case":pd.DataFrame()},"candidate":{"case":pd.DataFrame()}})
    assert list(empty.columns)[:3]==["arm","population","event_id"] and len(empty)==0
    edge={"available_at":"2024-01-01T00:05:00+00:00","action":"failed_launch_exit",
          "previous_fast":{"ma":99},"current_fast":{"ma":101},"slow":{"ma":98}}
    frame=pd.DataFrame({"event_id":["x"],"partial_fast_events":[json.dumps([edge])]})
    out=r.export_edges({"baseline":{"case":frame},"candidate":{"control":frame}})
    assert out.arm.tolist()==["baseline","candidate"]
    assert out.population.tolist()==["case","control"]
    assert all(json.loads(s)=={"ma":101} for s in out.current_fast)
