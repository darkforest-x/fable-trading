"""V38 first-confirmation feasibility audit, without economic outcomes.

Inputs: immutable V37 pre-outcome requests, V34 BinanceUSD-M BTCUSDT2023-2024
native5 source. Requests only contain fields known at original K2 close. Later
observations are sequential audit states, never features at the original time.
No execution, exit, return, flow filter or source expansion is implemented.

CSV float roundtrip and cardinality checks use pandas2.3.3 documented APIs:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.merge.html
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, add_features, resample_complete
from yoyo.data.k1k2_pending_entry import audit_pending_entries
from yoyo.evaluation.owner_k1k2_genuine_flow import (
    ROOT, FOLDS, checked as checked_source, load_month, sha, clean, write_json,
)

REL=Path("experiments/active/exp-btcusdtp-owner-k1k2-pending-entry-20260907-v38")
HERE=ROOT/REL
SOURCE=Path("data/owner_k1k2_transition_exit_v37")
COLS=["event_id","decision_time","direction","initial_stop","signal_atr","fold","gap_bars","ma","body_ratio"]
STATUS_SQL="""WITH totals AS (SELECT cohort,COUNT(*) AS denominator FROM main.audit_events GROUP BY cohort)
SELECT a.cohort,a.status,COUNT(*) AS events,t.denominator,1.0*COUNT(*)/t.denominator AS share,
SUM(a.status='eligible' AND a.waiting_bars=0) AS immediately_eligible,
SUM(a.status='eligible' AND a.waiting_bars>0) AS delayed_eligible,
AVG(a.waiting_bars*5.0) AS observed_wait_minutes
FROM main.audit_events a JOIN totals t ON a.cohort=t.cohort
GROUP BY a.cohort,a.status,t.denominator ORDER BY a.status,a.cohort"""


def checked():
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    config=json.loads((HERE/"config.json").read_text())
    if (config["audit_only"] is not True or config["economic_outcomes"] is not False
            or config["entry_expiry"] is not None or config["audit_minutes"]!=60):
        raise ValueError("Audit scope changed")
    for path in config["builder_paths"]+[str(REL/"config.json"),str(REL/"PROJECT_PLAN.md")]:
        if subprocess.check_output(["git","show",commit+":"+path],cwd=ROOT)!=(ROOT/path).read_bytes():
            raise ValueError("Commit exact audit builder and tests first: "+path)
    if sha(ROOT/config["source_receipt"])!=config["source_receipt_sha256"]:
        raise ValueError("Original pre-outcome receipt drift")
    receipt=json.loads((ROOT/config["source_receipt"]).read_text())
    expected={str(SOURCE/(x+".csv")) for x in ["case_requests","control_requests","assignments"]}
    if set(receipt["files"])!=expected or receipt["outcomes_materialized"] is not False:
        raise ValueError("Only original pre-outcome input projection allowed")
    for path,meta in receipt["files"].items():
        if sha(ROOT/path)!=meta["sha256"]: raise ValueError("Original request bytes drift")
    source_commit,_,manifest=checked_source()
    if source_commit!=commit: raise ValueError("Shared source snapshot changed")
    return commit,config,receipt,manifest


def read_requests(path,cohort):
    """Timestamp-only preflight, then explicit pre-outcome columns only."""
    if cohort not in {"case","control"}: raise ValueError("Unknown cohort")
    clocks=pd.to_datetime(pd.read_csv(path,usecols=["decision_time"]).decision_time,utc=True,errors="raise")
    if clocks.isna().any() or not (clocks.ge(pd.Timestamp("2023-01-01",tz="UTC"))&clocks.lt(pd.Timestamp("2025-01-01",tz="UTC"))).all():
        raise ValueError("Unauthorized request clock; do not read price fields")
    if not clocks.eq(clocks.dt.floor("h")).all(): raise ValueError("Original hourly decision required")
    frame=pd.read_csv(path,usecols=COLS+(["parent_event_id"] if cohort=="control" else []),float_precision="round_trip")
    frame["decision_time"]=clocks
    frame["audit_cutoff"]=clocks+pd.Timedelta(hours=1)
    frame["cohort"]=cohort
    if frame.event_id.isna().any() or not frame.event_id.is_unique: raise ValueError("Request identity invalid")
    return frame


def validate_support(requests,assignments):
    if len(requests)!=171 or not requests.cohort.isin(["case","control"]).all():
        raise ValueError("Original cohort support changed")
    case=requests.loc[requests.cohort.eq("case")]
    ctrl=requests.loc[requests.cohort.eq("control")]
    if (len(case)!=63 or len(ctrl)!=108 or len(assignments)!=63
            or not assignments.event_id.is_unique or not requests.event_id.is_unique
            or set(case.event_id)!=set(assignments.event_id)):
        raise ValueError("Original support changed")
    matched=set(assignments.loc[assignments.match_status.eq("matched"),"event_id"])
    if len(matched)!=36 or set(ctrl.parent_event_id)!=matched or not ctrl.groupby("parent_event_id").size().eq(3).all():
        raise ValueError("Original three-control links required")
    if not requests.fold.isin([f[0] for f in FOLDS]).all(): raise ValueError("Unexpected fold")
    for fold,start,end in FOLDS:
        f=requests.loc[requests.fold.eq(fold)]
        if not (f.decision_time.ge(pd.Timestamp(start))&f.decision_time.lt(pd.Timestamp(end)-pd.Timedelta(hours=72))
                &f.audit_cutoff.lt(pd.Timestamp(end))).all(): raise ValueError("Original fold clock changed")


def terminal_prefix_checks(requests,events,trace,raw):
    """Recompute each seed SMA from40complete bars; last boundary has OPEN only.

    Future HLC never enters local resampling/features. These checks compare
    all original terminal traces, not just eligible or favorable events. Opaque
    segment labels are normalized by first observation within each source domain;
    equality tests unknowns and change patterns, not arbitrary integer naming.
    """
    proof=[]
    for request in requests.to_dict("records"):
        expected=events.loc[events.event_id.eq(request["event_id"])].reset_index(drop=True)
        terminal=expected.status_known_at.iloc[0]
        start=request["decision_time"]-pd.Timedelta(minutes=200)
        local=raw.loc[raw.open_time.ge(start)&raw.open_time.le(terminal),BAR_COLUMNS].copy()
        native=add_features(resample_complete(local.loc[local.open_time.lt(terminal)],5),"SMA",40)
        local.loc[local.open_time.eq(terminal),["high","low","close"]]=np.nan
        actual,actual_trace=audit_pending_entries(pd.DataFrame([request]),native,local)
        pd.testing.assert_frame_equal(expected,actual.reset_index(drop=True),check_dtype=False,rtol=1e-12,atol=1e-12)
        target_trace=trace.loc[trace.event_id.eq(request["event_id"])].reset_index(drop=True)
        pd.testing.assert_frame_equal(normalize_segments(target_trace),normalize_segments(actual_trace.reset_index(drop=True)),
                                      check_dtype=False,rtol=1e-12,atol=1e-12)
        proof.append(dict(event_id=request["event_id"],cohort=request["cohort"],terminal=terminal,
                          prefix_rows=len(local),events_equal=True,trace_equal=True,current_hlc_masked=True))
    return pd.DataFrame(proof)


def normalize_segments(trace):
    result=trace.copy()
    for name in ["raw_segment_id","management_segment_id"]:
        values=pd.factorize(result[name],sort=False)[0]
        result[name]=pd.array([pd.NA if v<0 else v for v in values],dtype="Int64")
    return result


def future_negative_control():
    """Deliberately wrong future-low gate changes; correct entry audit must not."""
    e=pd.Timestamp("2024-01-01T12:00:00Z")
    raw=pd.DataFrame({"open_time":pd.date_range(e-pd.Timedelta(minutes=200),e,freq="5min"),
                      "open":100.,"high":100.,"low":100.,"close":100.,"volume":1.})
    native=add_features(resample_complete(raw.loc[raw.open_time.lt(e)],5),"SMA",40)
    req=pd.DataFrame([dict(event_id="synthetic",decision_time=e,direction=1,initial_stop=99.,signal_atr=1.,audit_cutoff=e)])
    original,t=audit_pending_entries(req,native,raw)
    mutated=raw.copy(); mutated.loc[mutated.open_time.eq(e),["high","low"]]=[200.,.5]
    altered,u=audit_pending_entries(req,native,mutated)
    pd.testing.assert_frame_equal(original,altered); pd.testing.assert_frame_equal(t,u)
    assert original.status.iloc[0]=="eligible"
    wrong_before=bool(raw.loc[raw.open_time.eq(e),"low"].iloc[0]<=99.)
    wrong_after=bool(mutated.loc[mutated.open_time.eq(e),"low"].iloc[0]<=99.)
    assert wrong_before is False and wrong_after is True
    return dict(synthetic_cases=1,correct_unchanged=True,deliberately_wrong_future_gate_changed=True)


def status_counts(events):
    with sqlite3.connect(":memory:") as con:
        events.to_sql("audit_events",con,index=False)
        return pd.read_sql_query(STATUS_SQL,con)


def run():
    commit,config,receipt,manifest=checked()
    output=ROOT/config["output_dir"]
    if output.exists() or (HERE/"summary.json").exists(): raise ValueError("One-shot audit evidence already exists")
    cases=read_requests(ROOT/SOURCE/"case_requests.csv","case")
    controls=read_requests(ROOT/SOURCE/"control_requests.csv","control")
    requests=pd.concat([cases,controls],ignore_index=True)
    assignments=pd.read_csv(ROOT/SOURCE/"assignments.csv",float_precision="round_trip")
    validate_support(requests,assignments)
    source=pd.concat([load_month(ROOT/r["output_path"],r) for r in manifest["monthly"]],ignore_index=True)
    raw=resample_complete(source[BAR_COLUMNS],5)
    native=add_features(raw,"SMA",40)
    negative=future_negative_control()
    output.mkdir(parents=True)
    files={}
    def save(name,f):
        path=output/(name+".csv")
        if path.exists(): raise ValueError("Refuse evidence overwrite")
        f.to_csv(path,index=False,float_format="%.17g")
        files[str(path.relative_to(ROOT))]=dict(sha256=sha(path),rows=len(f),columns=list(f.columns))
    save("requests",requests); save("assignments",assignments)
    write_json(HERE/"pre_audit_receipt.json",dict(source_commit=commit,config_sha256=sha(HERE/"config.json"),
        generated_at=pd.Timestamp.now(tz="UTC"),files=files.copy(),audited=False,economics_materialized=False))
    events,trace=audit_pending_entries(requests,native,raw)
    if len(events)!=171 or set(events.event_id)!=set(requests.event_id): raise ValueError("Lost request")
    if any(c in events for c in ["net_return","gross_return","exit_time","profit_factor"]): raise ValueError("Economic field in audit")
    proof=terminal_prefix_checks(requests,events,trace,raw)
    counts=status_counts(events)
    assert int(counts.events.sum())==171
    for name,f in [("events",events),("trace",trace),("prefix_checks",proof),("status_counts",counts)]: save(name,f)
    foldcounts=events.groupby(["fold","cohort","status"],dropna=False).size().rename("events").reset_index()
    seedcounts=events.groupby(["cohort","seed_state","status"],dropna=False).size().rename("events").reset_index()
    save("fold_counts",foldcounts); save("seed_counts",seedcounts)
    summary=dict(source_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),status="clock_audit_complete_not_economic",
        config_sha256=sha(HERE/"config.json"),source_bars=len(raw),requests=len(requests),cases=63,controls=108,
        original_matched_cases=36,original_unmatched_cases=27,administrative_observation_minutes=60,
        entry_expiry=None,counts=counts.to_dict("records"),fold_counts=foldcounts.to_dict("records"),
        seed_counts=seedcounts.to_dict("records"),prefix_checks=dict(events=len(proof),all_passed=True),
        negative_control=negative,trace_rows=len(trace),sql=STATUS_SQL,files=files,
        holdout_evaluated=False,training_eligible=False,production_eligible=False,economics_materialized=False,
        observed_live_delivery_verified=False)
    end_commit,end_config,end_receipt,end_manifest=checked()
    if (end_config,end_receipt,end_manifest)!=(config,receipt,manifest): raise ValueError("Inputs changed during audit")
    summary["verification_commit"]=end_commit
    write_json(HERE/"summary.json",summary)
    print(json.dumps(clean({k:v for k,v in summary.items() if k not in {"files","sql"}}),indent=2))


if __name__=="__main__":
    try:
        run()
    except Exception as exc:
        if (HERE/"pre_audit_receipt.json").exists() and not (HERE/"summary.json").exists():
            failure=HERE/"failed_run.json"
            if not failure.exists():
                write_json(failure,dict(generated_at=pd.Timestamp.now(tz="UTC"),
                    error_type=type(exc).__name__,reason=str(exc),outputs_preserved=True,
                    economics_materialized=False))
        raise
