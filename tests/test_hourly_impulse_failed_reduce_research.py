"""V19 frozen runner/leg accounting; all price paths are synthetic fixtures."""
from copy import deepcopy
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_failed_reduce_research as r
from yoyo.evaluation.hourly_impulse_failed_confirm_research import export_confirmations
from test_hourly_impulse_launch_research import base
from test_hourly_impulse_failed_reduce import E, V18, V19, fixture, quote, run


def pair(kind="recovered", direction=1):
    data=fixture(direction)
    data[3]["event_id"]=kind
    data[3]["mother_decision_time"]=E
    quote(data[0],10,100-direction)
    if kind=="hurt":quote(data[0],30,100-5*direction,invalid=True)
    elif kind=="profitable":quote(data[0],5,100+direction)
    elif kind=="unknown":
        data[0].loc[data[0].open_time.eq(E+pd.Timedelta(minutes=10)),["high","low","close"]]=np.nan
    return run(data,V18),run(data,V19),data


def ledger():
    parts=[pair(kind) for kind in ("recovered","hurt","profitable","unknown")]
    return tuple(pd.concat([part[index] for part in parts],ignore_index=True) for index in (0,1))


def test_frozen_config_only_changes_confirmed_fraction():
    config=r.frozen_config()
    r.verify_config(config,base())
    first,second=deepcopy(config["policies"])
    first.pop("id");second.pop("id")
    assert second.pop("fast_failed_launch_fraction")==.5 and first==second
    assert first["fast_failed_launch_confirmations"]==2
    assert config["parent_results"].endswith("v18/results/candidate")
    assert config["structure_reference"].endswith("v16/results/candidate")
    assert len(r.INPUTS)==7 and len(r.STRUCTURE_INPUTS)==2
    assert len(r.SOURCES)==len(set(r.SOURCES))
    assert "scope_stop_rule" in config


@pytest.mark.parametrize("key,value",[("fast_failed_launch_fraction",1),
    ("fast_failed_launch_confirmations",1),("fast_partial_fraction",1),
    ("management_minutes",5),("launch_deadline_minutes",60)])
def test_changed_policy_is_refused(key,value):
    config=r.frozen_config();config["policies"][1][key]=value
    with pytest.raises(ValueError):r.verify_config(config,base())


@pytest.mark.parametrize("change",["cost","fold","horizon","coverage","structure","half","serial"])
def test_frozen_contract_mutations_refused(change):
    config=r.frozen_config();original=base()
    if change=="cost":original["execution"]["cost_fraction"]=.001
    elif change=="fold":original["development_folds"][0][1]="2022-01-01"
    elif change=="horizon":original["execution"]["max_hours"]=80
    elif change=="coverage":config["selection"]["matched_coverage"]=.6
    elif change=="structure":config["structure_columns"]=r.STRUCTURE_COLUMNS+["net_return"]
    elif change=="half":config["failed_reduce_contract"]["candidate_confirmed_fraction"]=.25
    else:config["failed_reduce_contract"]["serial_recomputed_for_each_arm"]=False
    with pytest.raises(ValueError):r.verify_config(config,original)


def test_full_denominator_and_two_leg_sums_keep_unknown_remainder():
    before,after=ledger()
    out,groups,info=r.reduced_mechanics(before,after)
    assert len(out)==4 and groups.n.sum()==4 and info["known"]==3
    assert info["baseline_confirmed_full_count"]==info["candidate_risk_reduced_count"]==3
    assert info["baseline_profitable_partial_count"]==info["candidate_profitable_partial_count"]==1
    assert info["unchanged_paths"]==1
    assert (info["changed_improved"],info["changed_hurt"],info["changed_unknown_pairs"])==(1,1,1)
    assert info["recovered_winners"]==info["newly_unknown"]==1
    assert (info["remainder_known_count"],info["remainder_unknown_count"])==(2,1)
    assert info["risk_realised_net_event_bp"]==pytest.approx(-180)
    assert info["risk_realised_net_known_pairs_event_bp"]==pytest.approx(-120)
    assert info["risk_realised_net_unknown_remainder_event_bp"]==pytest.approx(-60)
    assert info["remainder_net_event_bp"]==pytest.approx(-70)
    assert info["reduced_total_net_event_bp"]==pytest.approx(-190)
    assert info["reduced_delta_event_bp"]==pytest.approx(50)
    unknown=out.loc[out.event_id.eq("unknown")].iloc[0]
    assert unknown.candidate_risk_realised_net_bp==pytest.approx(-60)
    assert pd.isna(unknown.candidate_remainder_net_bp) and pd.isna(unknown.delta_net_bp)
    assert info["risk_realised_net_known_pairs_event_bp"]+info["remainder_net_event_bp"]==pytest.approx(info["reduced_total_net_event_bp"])


def test_all_unknown_remainders_do_not_report_zero_total():
    before,after,_=pair("unknown")
    _,_,info=r.reduced_mechanics(before,after)
    assert np.isnan(info["remainder_net_event_bp"])
    assert np.isnan(info["reduced_total_net_event_bp"])
    assert np.isnan(info["reduced_delta_event_bp"])
    assert info["risk_realised_net_event_bp"]==pytest.approx(-60)


def test_no_risk_fills_have_true_empty_leg_sums_and_unchanged_old_fields():
    before,after,_=pair("profitable")
    out,_,info=r.reduced_mechanics(before,after)
    assert info["candidate_risk_reduced_count"]==0
    assert info["risk_realised_net_event_bp"]==info["remainder_net_event_bp"]==0
    assert out.candidate_remainder_net_bp.isna().all()
    assert out.delta_net_bp.eq(0).all()


@pytest.mark.parametrize("column",["entry_price","initial_stop","direction","risk_pct"])
def test_entry_identity_cannot_change(column):
    before,after,_=pair();after.loc[0,column]+=1
    with pytest.raises((AssertionError,ValueError)):r.reduced_mechanics(before,after)


@pytest.mark.parametrize("column,value",[("exit_price",130.),("outcome","hard_stop"),
    ("partial_fast_flip_count",10),("failed_confirm_events","[] ")])
def test_all_old_nonfull_columns_remain_exact(column,value):
    before,after,_=pair("profitable");after.loc[0,column]=value
    with pytest.raises((AssertionError,ValueError)):r.reduced_mechanics(before,after)


@pytest.mark.parametrize("column,value",[("failed_reduce_fill_count",0),
    ("partial_fast_fill_count",1),("failed_launch_count",1),("partial_fraction",1.),
    ("exit_remaining_fraction",1.),("failed_reduce_fraction",1.),
    ("failed_reduce_realised_net_return",.001),("realised_partial_gross_return",.5),
    ("failed_confirm_create_count",2),("failed_launch_trigger_open_price",103.)])
def test_invalid_reduction_or_changed_trigger_refused(column,value):
    before,after,_=pair();after.loc[0,column]=value
    with pytest.raises((AssertionError,ValueError)):r.reduced_mechanics(before,after)


def test_risk_fill_is_same_old_full_open_not_an_extra_five_minutes():
    before,after,_=pair()
    out,_,_=r.reduced_mechanics(before,after)
    assert after.failed_reduce_fill_time.iloc[0]==before.exit_time.iloc[0]
    assert out.exit_delay_minutes.iloc[0]==20
    for delta in (pd.Timedelta(nanoseconds=1),pd.Timedelta(minutes=5)):
        corrupted=after.copy();corrupted.loc[0,"failed_reduce_fill_time"]+=delta
        with pytest.raises((AssertionError,ValueError)):r.reduced_mechanics(before,corrupted)


def test_finite_partial_cannot_uncensor_total_or_change_cost():
    before,after,_=pair("unknown");after.loc[0,"net_return"]=-.006
    with pytest.raises(ValueError,match="Unknown remainder"):r.reduced_mechanics(before,after)
    before,after,_=pair();after.loc[0,"net_return"]+=.001
    with pytest.raises(AssertionError,match="cost changed"):r.reduced_mechanics(before,after)


def test_confirmation_log_allows_only_fill_annotation_not_new_flip():
    before,after,_=pair()
    events=json.loads(after.loc[0,"failed_confirm_events"])
    events[-1]["observation"]["current_fast"]["side"]=1
    after.loc[0,"failed_confirm_events"]=json.dumps(events)
    with pytest.raises(ValueError,match="new flip"):r.reduced_mechanics(before,after)
    assert r.export_confirmations is export_confirmations
    clean=pair()[1]
    exported=r.export_confirmations({"baseline":{"case":before},"candidate":{"case":clean}})
    assert exported.groupby("arm").size().to_dict()=={"baseline":2,"candidate":2}


@pytest.mark.parametrize("direction",[-1,1])
def test_actual_synthetic_paths_match_v16_structure_not_its_pnl(direction):
    before,after,data=pair(direction=direction)
    v16={key:value for key,value in V18.items() if not key.startswith("fast_failed_launch")}
    reference=run(data,v16)
    refs={label:reference for label in ("case","control")}
    candidates={label:after for label in refs}
    receipt=r.remainder_structure_parity(refs,candidates)
    assert receipt["checks"]=={"case":{"rows":1,"columns":8},"control":{"rows":1,"columns":8}}
    assert receipt["pnl_borrowed"] is False
    assert reference.net_return.iloc[0]!=after.net_return.iloc[0]
    reference["net_return"]=99999  # Economic columns cannot be the structure anchor.
    r.remainder_structure_parity(refs,candidates)
    out,_,_=r.reduced_mechanics(before,after)
    assert out.candidate_risk_reduced.all()


@pytest.mark.parametrize("change",["time","price","mfe","id","missing","duplicate"])
def test_corrupt_structure_is_refused(change):
    _,after,_=pair();reference=after[r.STRUCTURE_COLUMNS].copy()
    if change=="time":reference.loc[0,"exit_time"]+=pd.Timedelta(nanoseconds=1)
    elif change=="price":reference.loc[0,"exit_price"]+=1
    elif change=="mfe":reference.loc[0,"max_favourable_r"]+=1
    elif change=="id":reference.loc[0,"event_id"]="other"
    elif change=="missing":reference=reference.drop(columns="outcome")
    else:reference=pd.concat([reference,reference],ignore_index=True)
    with pytest.raises((AssertionError,ValueError,KeyError)):
        r.remainder_structure_parity({"case":reference,"control":after},{"case":after,"control":after})


def test_run_freezes_and_replays_before_reading_v16_structure_only():
    source=inspect.getsource(r.run)
    checkpoints=['"context_frozen.json"','pin(ROOT/PARENT,INPUTS)','old=replay_arm',
                 'new=replay_arm','pin(ROOT/V16_STRUCTURE,STRUCTURE_INPUTS)','"remainder_structure_parity.json"']
    offsets=[source.index(text) for text in checkpoints]
    assert offsets==sorted(offsets)
    assert 'usecols=STRUCTURE_COLUMNS' in source
    assert 'saved_reader=read_parent_frame' in source
    assert 'paired_effects(old[2]["case"],new[2]["case"],old[3],new[3],old[4],new[4])' in source
    assert 'reduced_{label}_mechanics.csv' in source and 'reduced_{label}_groups.csv' in source
    assert 'export_confirmations({"baseline":old[1],"candidate":new[1]})' in source
    assert '"structure_inputs":STRUCTURE_INPUTS' in source
    assert 'confirmed_mechanics(' not in source
