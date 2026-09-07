"""V38 runner guards and provenance checks; synthetic inputs only.

No real archives, requests, outcomes, or experiment receipts are read. Fake CSV
inputs intentionally contain forbidden outcome columns to verify projection.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.data.hourly_impulse import add_features, resample_complete
from yoyo.data.k1k2_pending_entry import audit_pending_entries
from yoyo.evaluation import owner_k1k2_pending_entry_audit as m


E=pd.Timestamp("2023-03-03T12:00:00Z")


def request_frame(n=1):
    return pd.DataFrame([dict(event_id="case"+str(i),decision_time=E,direction=1,
        initial_stop=95.,signal_atr=2.,fold="2023H1",gap_bars=3,ma=99.,body_ratio=.7)
        for i in range(n)])


def support():
    cases=request_frame(63)
    cases["cohort"]="case"
    cases["audit_cutoff"]=E+pd.Timedelta(hours=1)
    controls=pd.concat([cases.iloc[:36].copy() for _ in range(3)],ignore_index=True)
    controls["parent_event_id"]=controls.event_id
    controls["event_id"]=["ctrl"+str(i) for i in range(108)]
    controls["cohort"]="control"
    assignments=pd.DataFrame(dict(event_id=cases.event_id,
        match_status=["matched"]*36+["no_match"]*27))
    return pd.concat([cases,controls],ignore_index=True),assignments


def test_reader_projects_only_pre_outcome_fields(tmp_path):
    frame=request_frame()
    frame["net_return"]=123456.;frame["future_label"]="not-an-input"
    path=tmp_path/"fake.csv";frame.to_csv(path,index=False)
    actual=m.read_requests(path,"case")
    assert set(actual)==set(m.COLS+["audit_cutoff","cohort"])
    assert actual.audit_cutoff.iloc[0]==E+pd.Timedelta(hours=1)


@pytest.mark.parametrize("clock",["2022-12-31T23:00:00Z","2025-01-01T00:00:00Z",None,"2023-03-03T12:05:00Z"])
def test_timestamp_preflight_precedes_numeric_materialization(clock,tmp_path,monkeypatch):
    frame=request_frame();frame["decision_time"]=clock
    path=tmp_path/"fake.csv";frame.to_csv(path,index=False)
    calls=[];original=pd.read_csv
    def read(*args,**kwargs):
        calls.append(kwargs.get("usecols"))
        return original(*args,**kwargs)
    monkeypatch.setattr(m.pd,"read_csv",read)
    with pytest.raises(ValueError): m.read_requests(path,"case")
    assert calls==[["decision_time"]]


def test_unknown_cohort_rejected_before_file_access():
    with pytest.raises(ValueError,match="cohort"): m.read_requests("nonexistent","other")


def test_original_support_is_valid():
    m.validate_support(*support())


@pytest.mark.parametrize("fault",["lost_case","lost_ctrl","duplicate","links","matched_count","fold","purge","extra_cohort"])
def test_support_drift_fails_closed(fault):
    requests,assignments=support()
    if fault=="lost_case": requests=requests.drop(index=0)
    elif fault=="lost_ctrl": requests=requests.drop(index=170)
    elif fault=="duplicate": requests.loc[170,"event_id"]=requests.loc[169,"event_id"]
    elif fault=="links": requests.loc[170,"parent_event_id"]="not-a-parent"
    elif fault=="matched_count": assignments.loc[0,"match_status"]="no_match"
    elif fault=="fold": requests.loc[0,"fold"]="2025H1"
    elif fault=="purge": requests.loc[0,"decision_time"]=pd.Timestamp("2023-06-30T00:00:00Z")
    elif fault=="extra_cohort":
        extra=requests.iloc[[0]].copy();extra["cohort"]="other";extra["event_id"]="extra"
        requests=pd.concat([requests,extra],ignore_index=True)
    with pytest.raises(ValueError): m.validate_support(requests,assignments)


def test_sql_counts_keep_full_cohort_denominators():
    frame=pd.DataFrame(dict(cohort=["case"]*4+["control"]*2,
        status=["eligible","eligible","unknown_raw","pending_at_cutoff","eligible","invalidated_open"],
        waiting_bars=[0,2,1,12,1,0]))
    counts=m.status_counts(frame)
    row=counts.loc[counts.cohort.eq("case")&counts.status.eq("eligible")].iloc[0]
    assert row.events==2 and row.denominator==4 and row.share==.5
    assert row.immediately_eligible==1 and row.delayed_eligible==1
    assert row.observed_wait_minutes==5
    assert counts.events.sum()==6


def test_deliberately_wrong_future_gate_is_detected():
    assert m.future_negative_control()==dict(synthetic_cases=1,correct_unchanged=True,
        deliberately_wrong_future_gate_changed=True)


def test_segment_labels_are_opaque_but_changes_and_unknowns_are_preserved():
    frame=pd.DataFrame(dict(raw_segment_id=[8,8,None,10],management_segment_id=["x","x",None,"y"]))
    result=m.normalize_segments(frame)
    assert result.raw_segment_id.iloc[:2].tolist()==[0,0]
    assert result.management_segment_id.iloc[:2].tolist()==[0,0]
    assert result.iloc[2].isna().all()
    assert result.iloc[3].tolist()==[1,1]


@pytest.mark.parametrize("state",["aligned","opposite","delayed","stop"])
def test_terminal_prefix_recompute_masks_future_hlc(state):
    raw=pd.DataFrame(dict(open_time=pd.date_range(E-pd.Timedelta(minutes=250),periods=64,freq="5min"),
        open=100.,high=100.2,low=99.8,close=100.,volume=1.))
    raw.loc[raw.open_time.ge(E-pd.Timedelta(minutes=5)),["high","low"]]=[101.,97.]
    if state=="aligned": raw.loc[raw.open_time.eq(E-pd.Timedelta(minutes=5)),["high","low"]]=[103.,99.]
    elif state in {"delayed","stop"}: raw.loc[raw.open_time.eq(E+pd.Timedelta(minutes=5)),["high","low"]]=[107.,95. if state=="stop" else 99.]
    raw=resample_complete(raw,5);native=add_features(raw,"SMA",40)
    requests=request_frame();requests["cohort"]="case";requests["audit_cutoff"]=E+pd.Timedelta(hours=1)
    events,trace=audit_pending_entries(requests,native,raw)
    assert events.status.iloc[0]=={"aligned":"eligible","opposite":"pending_at_cutoff","delayed":"eligible","stop":"invalidated_wait_bar"}[state]
    proof=m.terminal_prefix_checks(requests,events,trace,raw)
    assert len(proof)==1 and proof.events_equal.all() and proof.trace_equal.all() and proof.current_hlc_masked.all()
    # Same valid continuity, different opaque labels in the two source domains.
    raw["segment_id"]=9;native["segment_id"]=43
    events2,trace2=audit_pending_entries(requests,native,raw)
    proof2=m.terminal_prefix_checks(requests,events2,trace2,raw)
    assert proof2.trace_equal.all()


def fake_checked_tree(tmp_path,monkeypatch):
    here=tmp_path/m.REL;here.mkdir(parents=True)
    source=tmp_path/m.SOURCE;source.mkdir(parents=True)
    files={}
    for name in ["case_requests","control_requests","assignments"]:
        path=source/(name+".csv");path.write_text("event_id\nfake\n")
        files[str(path.relative_to(tmp_path))]=dict(sha256=m.sha(path))
    receipt=tmp_path/"receipt.json";receipt.write_text(json.dumps(dict(files=files,outcomes_materialized=False)))
    config=dict(audit_only=True,economic_outcomes=False,entry_expiry=None,audit_minutes=60,
        builder_paths=["builder.py"],source_receipt="receipt.json",source_receipt_sha256=m.sha(receipt))
    (tmp_path/"builder.py").write_text("# synthetic frozen builder\n")
    (here/"PROJECT_PLAN.md").write_text("# synthetic plan\n")
    (here/"config.json").write_text(json.dumps(config))
    frozen={str(p.relative_to(tmp_path)):p.read_bytes() for p in [tmp_path/"builder.py",here/"PROJECT_PLAN.md",here/"config.json"]}
    def command(args,**kwargs):
        return "synthetic-commit\n" if args[1]=="rev-parse" else frozen[args[2].split(":",1)[1]]
    monkeypatch.setattr(m,"ROOT",tmp_path);monkeypatch.setattr(m,"HERE",here)
    monkeypatch.setattr(m.subprocess,"check_output",command)
    monkeypatch.setattr(m,"checked_source",lambda:("synthetic-commit",{},dict(monthly=[])))
    return here,source,receipt,config


def test_frozen_builder_and_receipt_validated_without_materializing_prices(tmp_path,monkeypatch):
    fake_checked_tree(tmp_path,monkeypatch)
    assert m.checked()[0]=="synthetic-commit"


@pytest.mark.parametrize("fault",["builder","request_bytes","receipt_bytes","audit_only","economic_outcomes","entry_expiry","audit_minutes"])
def test_freeze_and_scope_guard_reject_drift(tmp_path,monkeypatch,fault):
    here,source,receipt,config=fake_checked_tree(tmp_path,monkeypatch)
    if fault=="builder": (tmp_path/"builder.py").write_text("# drift\n")
    elif fault=="request_bytes": (source/"case_requests.csv").write_text("event_id\nchanged\n")
    elif fault=="receipt_bytes": receipt.write_text("{}")
    else:
        config[fault]={"audit_only":False,"economic_outcomes":True,"entry_expiry":60,"audit_minutes":65}[fault]
        (here/"config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError): m.checked()
