"""V18 frozen design and all-intention invariants; synthetic data only."""
from copy import deepcopy
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_failed_confirm_research as r
from test_hourly_impulse_launch_research import base
from test_hourly_impulse_failed_launch_research import ledger as old_ledger


def test_frozen_config_one_confirmation_switch():
    config=json.loads((r.EXPERIMENT/"config.json").read_text())
    r.verify_config(config,base())
    a,b=deepcopy(config["policies"]);a.pop("id");b.pop("id")
    assert b.pop("fast_failed_launch_confirmations")==2 and a==b
    assert a["fast_failed_launch_exit"] is True
    assert config["parent_results"].endswith("v17/results/candidate")
    assert len(r.INPUTS)==7 and len(r.SOURCES)==len(set(r.SOURCES))


@pytest.mark.parametrize("key,value",[("fast_failed_launch_confirmations",1),
    ("fast_failed_launch_exit",False),("fast_partial_fraction",1),("management_minutes",5),
    ("ma_length",20),("confirmations",2),("launch_deadline_minutes",60)])
def test_policy_mutation_refused(key,value):
    c=r.frozen_config();c["policies"][1][key]=value
    with pytest.raises(ValueError):r.verify_config(c,base())


@pytest.mark.parametrize("change",["fee","duration","stop","fold","coverage","holdout","threshold","serial"])
def test_frozen_design_mutation_refused(change):
    c=r.frozen_config();b=base()
    if change=="fee":b["execution"]["cost_fraction"]=.001
    elif change=="duration":b["execution"]["max_hours"]=24
    elif change=="stop":b["execution"]["stop_first"]=False
    elif change=="fold":b["development_folds"][0][1]="2022-01-01"
    elif change=="coverage":c["selection"]["matched_coverage"]=.6
    elif change=="holdout":c["holdout_consumed"]=True
    elif change=="threshold":c["failed_confirm_contract"]["full_exit_if_open_gross_not_above"]=.003
    else:c["failed_confirm_contract"]["serial_recomputed_for_each_arm"]=False
    with pytest.raises(ValueError):r.verify_config(c,b)


def ledger():
    _,before=old_ledger();after=before.copy()
    full=before.outcome.eq("fast_failed_launch")
    after["failed_confirm_create_count"]=full.astype(int)
    after["failed_confirm_confirm_count"]=full.astype(int)
    after["failed_confirm_cancel_count"]=0
    after["failed_confirm_priority_termination_count"]=0
    after["failed_confirm_events"]=[json.dumps([{"action":"created","created_at":t.isoformat()}]) if f else "[]"
        for f,t in zip(full,before.exit_time)]
    after.loc[full,"exit_time"]+=pd.Timedelta(minutes=5)
    after.loc[full,"hold_minutes"]+=5
    after.loc[0,"net_return"]=.02;after.loc[0,"outcome"]="transition_colour_exit"
    after.loc[0,"failed_launch_count"]=0;after.loc[0,"failed_confirm_confirm_count"]=0
    after.loc[0,"failed_confirm_cancel_count"]=1
    after.loc[0,"partial_fraction"]=.5;after.loc[0,"exit_remaining_fraction"]=.5
    after.loc[0,"partial_fast_fill_count"]=1
    after.loc[1,"net_return"]=-.02
    after.loc[3,"net_return"]=np.nan;after.loc[3,"closed"]=False
    after.loc[3,"outcome"]="data_gap_censored"
    after.loc[3,"failed_confirm_confirm_count"]=0;after.loc[3,"failed_launch_count"]=0
    after.loc[3,"failed_confirm_priority_termination_count"]=1
    return before,after


def test_all_intentions_recovery_hurt_and_unknown_preserved():
    b,a=ledger();out,groups,info=r.confirmed_mechanics(b,a)
    assert len(out)==4 and groups.n.sum()==4 and info["known"]==3
    assert info["baseline_failed_full_count"]==3 and info["candidate_confirmed_full_count"]==1
    assert info["changed_improved"]==1 and info["changed_hurt"]==1
    assert info["recovered_winners"]==1 and info["restored_partial_paths"]==1
    assert info["changed_unknown_pairs"]==1 and info["newly_unknown"]==1
    assert out.delta_net_bp.isna().sum()==1


@pytest.mark.parametrize("column",["entry_price","initial_stop","direction","risk_pct"])
def test_entry_cannot_change(column):
    b,a=ledger();a.loc[0,column]+=1
    with pytest.raises((AssertionError,ValueError)):r.confirmed_mechanics(b,a)


@pytest.mark.parametrize("column,value",[("net_return",.1),("exit_price",130.),("outcome","hard_stop"),("partial_fraction",0)])
def test_old_nonfull_all_old_fields_equal(column,value):
    b,a=ledger();a.loc[2,column]=value
    with pytest.raises((AssertionError,ValueError)):r.confirmed_mechanics(b,a)


@pytest.mark.parametrize("column,value",[("partial_fraction",.5),("exit_remaining_fraction",.5),
    ("realised_partial_gross_return",.002),("partial_fast_fill_count",1),("closed",False),
    ("failed_confirm_confirm_count",0),("net_return",.001),("net_return",np.nan)])
def test_confirmed_full_accounting_invalid(column,value):
    b,a=ledger();a.loc[1,column]=value
    with pytest.raises((AssertionError,ValueError)):r.confirmed_mechanics(b,a)


def test_same_timestamp_known_refused_unknown_allowed():
    b,a=ledger();a.loc[1,"exit_time"]=b.loc[1,"exit_time"]
    with pytest.raises(ValueError,match="full raw5"):r.confirmed_mechanics(b,a)
    b,a=ledger();a.loc[3,"exit_time"]=b.loc[3,"exit_time"]
    r.confirmed_mechanics(b,a)


def test_first_pending_must_be_old_full_clock():
    b,a=ledger();a.loc[0,"failed_confirm_events"]='[{"action":"created","created_at":"2024-01-01T01:00:00Z"}]'
    with pytest.raises(ValueError,match="First pending"):r.confirmed_mechanics(b,a)


def test_source_freeze_old_anchor_and_independent_serial():
    source=inspect.getsource(r.run)
    assert source.index('"context_frozen.json"')<source.index('pin(ROOT/PARENT,INPUTS)')<source.index('old=replay_arm')<source.index('new=replay_arm')
    assert 'saved_reader=read_parent_frame' in source
    assert 'paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])' in source
    assert 'confirmation_events.csv.gz' in source


def test_confirmation_export_is_lossless_and_not_a_fast_edge():
    event={"action":"confirmed","observed_at":"2024-01-01T00:10:00Z","observation":{"side":-1}}
    frame=pd.DataFrame({"event_id":["x"],"failed_confirm_events":[json.dumps([event])]})
    out=r.export_confirmations({"candidate":{"case":frame}})
    assert out.action.tolist()==["confirmed"] and json.loads(out.evidence_json.iloc[0])==event
    assert list(r.export_confirmations({}).columns)==list(out.columns)


@pytest.mark.parametrize("direction",[-1,1])
@pytest.mark.parametrize("second_move",[-1.,.05,.4])
def test_actual_synthetic_engine_lifecycle_fits_runner_schema(direction,second_move):
    from test_hourly_impulse_failed_confirm import fixture,quote,run,V17,E
    data=fixture(direction)
    data[3]["mother_decision_time"]=E
    quote(data[0],10,100+direction*second_move)
    old=run(data,V17);new=run(data)
    out,_,info=r.confirmed_mechanics(old,new)
    assert info["total"]==1 and info["pending_events"]==1
    assert info["candidate_confirmed_full_count"]==int(second_move<=.2)
    exported=r.export_confirmations({"candidate":{"case":new}})
    assert len(exported)==2 and json.loads(exported.evidence_json.iloc[0])["action"]=="created"
    assert out.exit_delay_minutes.iloc[0]>=5
