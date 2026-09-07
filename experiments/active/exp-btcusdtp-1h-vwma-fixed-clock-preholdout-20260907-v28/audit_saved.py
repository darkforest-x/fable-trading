"""Independent V28 saved-label/RNG/monthly audit, no strategy helper imports.

Reads saved OPEN prefix only, never raw archive or2025+ quotes. Decimal own
endpoints and full grid independently rebuild labels. NumPy PCG64 is the shared
numeric primitive; neither sampling, label nor statistical builder is called.
"""
from pathlib import Path
from decimal import Decimal
import hashlib
import json
import math
import subprocess

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
E=Path(__file__).resolve().parent
V27="experiments/active/exp-btcusdtp-1h-vwma-background-support-preholdout-20260907-v27/results"
MONTHS=["%d-%02d"%(year,month) for year in (2023,2024) for month in range(1,13)]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def equal(a,b):
    if pd.isna(a) and pd.isna(b):return
    if not np.isfinite([float(a),float(b)]).all() or not math.isclose(float(a),float(b),rel_tol=1e-11,abs_tol=1e-12):
        raise ValueError("Numeric reconstruction mismatch: %s != %s"%(a,b))


def check_labels(open_rows,requests,labels):
    prices={pd.Timestamp(r.open_time):Decimal(str(r.open)) for r in open_rows.itertuples()}
    index=labels.set_index(["event_id","horizon_hours"])
    if index.index.duplicated().any() or len(index)!=len(requests)*4:raise ValueError("Label identity grid changed")
    for row in requests.itertuples():
        t=pd.Timestamp(row.decision_time)
        if t.tzinfo is None or t!=t.floor("h") or t>=pd.Timestamp("2025-01-01T00:00Z"):raise ValueError("Bad request time")
        end=pd.Timestamp({"2023H1":"2023-07-01","2023H2":"2024-01-01","2024H1":"2024-07-01","2024H2":"2025-01-01"}[row.fold],tz="UTC")
        for hours in (1,4,12,24):
            out=index.loc[(row.event_id,hours)];endpoint=t+pd.Timedelta(hours=hours)
            grid=[t+pd.Timedelta(minutes=5*i) for i in range(hours*12+1)]
            window=[prices.get(x) for x in grid]
            reason=("endpoint_outside_fold" if endpoint>=end else "missing_bar" if any(x is None for x in window)
                    else "invalid_open" if any(not x.is_finite() or x<=0 for x in window) else "known")
            if out.reason!=reason or out.status!=("known" if reason=="known" else "unknown"):raise ValueError("Label known/reason mismatch")
            if out.direction!=row.direction or out.fold!=row.fold or pd.Timestamp(out.decision_time)!=t or pd.Timestamp(out.endpoint_time)!=endpoint:
                raise ValueError("Label own clock/direction mismatch")
            for key in ("mother_id","control_slot","mother_month","matched_support","request_kind"):
                if hasattr(row,key):
                    want=getattr(row,key);got=out[key]
                    if not ((pd.isna(want) and pd.isna(got)) or got==want):raise ValueError("Label metadata detached: "+key)
            if out.n_expected!=len(grid):raise ValueError("Expected inclusive5m grid changed")
            if reason!="endpoint_outside_fold":equal(out.n_observed,sum(x is not None for x in window))
            else:equal(out.n_observed,np.nan)
            if reason=="known":
                gross=float(Decimal(int(row.direction))*(prices[endpoint]-prices[t])/prices[t])
                equal(out.gross_markout,gross);equal(out.cost_threshold_markout,float(Decimal(int(row.direction))*(prices[endpoint]-prices[t])/prices[t]-Decimal(".002")))
            else:
                equal(out.gross_markout,np.nan);equal(out.cost_threshold_markout,np.nan)
    return len(index)


def check_sampling(mothers,edges,allocation,sampling):
    by={event:set() for event in mothers.event_id};rev={}
    for row in edges.itertuples():by[row.event_id].add(row.candidate_id);rev.setdefault(row.candidate_id,set()).add(row.event_id)
    rng=np.random.Generator(np.random.PCG64(20260907))
    if rng.bit_generator.state!=sampling["initial_rng_state"]:raise ValueError("Initial RNG mismatch")
    seen=set();expected=[]
    for first in sorted(by):
        if first in seen:continue
        pending=[first];ids=set();candidates=set()
        while pending:
            event=pending.pop()
            if event in ids:continue
            ids.add(event);candidates|=by[event]
            for candidate in by[event]:pending.extend(rev[candidate]-ids)
        seen|=ids
        if any(by[event]!=candidates for event in ids) or (candidates and len(candidates)<3*len(ids)):raise ValueError("Not complete/sufficient component")
        permutation=rng.permutation(np.asarray(sorted(candidates),dtype=object)).tolist() if candidates else []
        for ordinal,event in enumerate(sorted(ids)):
            for slot,candidate in enumerate(permutation[ordinal*3:ordinal*3+3]):expected.append((event,slot,candidate))
    actual=list(allocation[["event_id","control_slot","candidate_id"]].itertuples(index=False,name=None))
    if actual!=expected or allocation.candidate_id.duplicated().any():raise ValueError("Seeded allocation differs/reuses")
    if rng.bit_generator.state!=sampling["final_rng_state"]:raise ValueError("Final RNG mismatch")
    return len(expected)


def check_statistics(cases,controls,pairs,inference):
    if len(pairs)!=1152 or pairs.duplicated(["event_id","horizon_hours"]).any():raise ValueError("Pair identity count")
    cg={(m,h):g for (m,h),g in controls.groupby(["mother_id","horizon_hours"])}
    ci=cases.set_index(["event_id","horizon_hours"])
    for r in pairs.itertuples():
        c=ci.loc[(r.event_id,r.horizon_hours)];g=cg.get((r.event_id,r.horizon_hours),controls.iloc[:0])
        known=len(g)==3 and g.status.eq("known").all();complete=known and c.status=="known"
        if bool(r.pair_complete)!=complete or r.n_controls_assigned!=len(g) or r.n_controls_known!=g.status.eq("known").sum():raise ValueError("Pair completeness changed")
        equal(r.case_cost_threshold_markout,c.cost_threshold_markout)
        expected=c.cost_threshold_markout-g.cost_threshold_markout.mean() if complete else np.nan
        equal(r.cost_threshold_excess_markout,expected)
        equal(r.control_mean_cost_threshold_markout,g.cost_threshold_markout.mean() if known else np.nan)
    rng=np.random.Generator(np.random.PCG64(20260907));idx=rng.integers(0,24,(9999,24),dtype=np.int64);signs=2*rng.integers(0,2,(9999,24),dtype=np.int64)-1
    for key,array in (("month_indices_sha256",idx),("month_signs_sha256",signs)):
        if inference["contract"][key]!=hashlib.sha256(array.astype("<i8").tobytes()).hexdigest():raise ValueError("Inference RNG arrays changed")
    for hours in (1,4,12,24):
        for name,frame,column in (("all_case",cases,"cost_threshold_markout"),("paired_excess",pairs,"cost_threshold_excess_markout")):
            f=frame.loc[frame.horizon_hours.eq(hours)].sort_values("event_id");x=f[column];r=inference["horizons"][str(hours)][name]
            if r["n_total"]!=288 or r["n_known"]!=x.count():raise ValueError("Statistics denominator changed")
            equal(r["mean"],x.mean());equal(r["sd"],x.std(ddof=1));equal(r["median"],x.median())
            sums=np.array([math.fsum(f.loc[f.mother_month.eq(m),column].dropna()) for m in MONTHS]);counts=np.array([f.loc[f.mother_month.eq(m),column].count() for m in MONTHS])
            for i,row in enumerate(r["monthly_cluster"]["months"]):
                if row["month"]!=MONTHS[i] or row["n_known"]!=counts[i]:raise ValueError("Monthly denominator changed")
                equal(row["sum"],sums[i])
            if hours==4:
                intervals=np.quantile(sums[idx].sum(1)/counts[idx].sum(1),[.025,.975],method="linear")
                for a,b in zip(intervals,r["monthly_cluster"]["ci95"]):equal(a,b)
                p=(1+np.count_nonzero((signs*sums).sum(1)>=sums.sum()))/10000
                equal(r["monthly_cluster"]["one_sided_p"],p)
            elif "one_sided_p" in r["monthly_cluster"]:raise ValueError("Descriptive horizon gained inference")
        for fold,row in inference["horizons"][str(hours)]["folds"].items():
            for name,frame,column in (("all_case",cases,"cost_threshold_markout"),("paired_excess",pairs,"cost_threshold_excess_markout")):
                x=frame.loc[frame.horizon_hours.eq(hours)&frame.fold.eq(fold),column];equal(row[name]["mean"],x.mean())
    a=inference["horizons"]["4"];coverage=all(a[n]["n_known"]>=260 for n in ("all_case","paired_excess"))
    positive=all(r["all_case"]["mean"]>0 for r in a["folds"].values())
    evidence=all(a[n]["mean"]>0 and a[n]["monthly_cluster"]["ci95"][0]>0 and a[n]["monthly_cluster"]["one_sided_p"]<.01 for n in ("all_case","paired_excess"))
    d=inference["decision"]
    if (d["primary_coverage_passed"],d["four_fold_case_means_positive"],d["both_primary_evidence_gates_passed"],d["exploratory_continue"])!=(coverage,positive,evidence,coverage and positive and evidence):raise ValueError("Decision differs")
    return len(pairs)


def main():
    own=Path(__file__).resolve();commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    if subprocess.check_output(["git","show",commit+":"+str(own.relative_to(ROOT))],cwd=ROOT)!=own.read_bytes():raise ValueError("Commit auditor first")
    r=E/"results";summary=json.loads((r/"summary.json").read_text());before=sha(r/"summary.json")
    config=json.loads((E/"config.json").read_text());frozen=json.loads((r/"sampling_frozen.json").read_text())
    hashes={r/name:expected for name,expected in summary["output_hashes"].items()}
    hashes.update({ROOT/path:expected for path,expected in config["inputs"].items()})
    hashes.update({r/"statistics.json":summary["statistics_sha256"],r/"sampling_frozen.json":summary["sampling_frozen_sha256"]})
    for path,expected in hashes.items():
        if sha(path)!=expected:raise ValueError("Evidence hash changed: "+str(path))
    for source in summary["sources"]:
        blob=subprocess.check_output(["git","show",summary["builder_commit"]+":"+source["path"]],cwd=ROOT)
        if hashlib.sha256(blob).hexdigest()!=source["sha256"]:raise ValueError("Historical builder changed")
    start=json.loads((r/"started.json").read_text())
    if not pd.Timestamp(start["at"])<pd.Timestamp(frozen["at"])<pd.Timestamp(summary["generated_at"]) or not frozen["before_any_raw_read"]:raise ValueError("Freeze chronology failed")
    read=lambda name:pd.read_csv(r/(name+".csv.gz"))
    raw=pd.read_csv(r/"open_prefix.csv.gz",dtype=str,keep_default_na=False)
    times=pd.to_datetime(raw.open_time,utc=True)
    if len(raw)!=219551 or not times.is_unique or not times.is_monotonic_increasing or times.max()>=pd.Timestamp("2025-01-01T00:00Z"):raise ValueError("Saved OPEN domain changed")
    mothers=read("original_mothers");edges=pd.read_csv(ROOT/V27/"eligible_edges.csv.gz")
    allocation=read("random_allocation");allocated=check_sampling(mothers,edges,allocation,summary["sampling"])
    case_requests,control_requests=read("case_requests"),read("control_requests")
    pd.testing.assert_frame_equal(case_requests[mothers.columns],mothers,check_dtype=False,rtol=1e-12,atol=1e-12)
    pd.testing.assert_frame_equal(mothers,pd.read_csv(ROOT/config["cohort_source"]),check_dtype=False,rtol=1e-12,atol=1e-12)
    by_control=control_requests.set_index("event_id");by_mother=mothers.set_index("event_id")
    if len(by_control)!=852 or not by_control.index.is_unique:raise ValueError("Control requests differ")
    for row in allocation.itertuples():
        control=by_control.loc[row.control_event_id];mother=by_mother.loc[row.event_id]
        if control.mother_id!=row.event_id or control.direction!=mother.direction or control.fold!=mother.fold or control.control_slot!=row.control_slot or pd.Timestamp(control.decision_time)!=pd.Timestamp(row.candidate_id):
            raise ValueError("Control requests detached from own sampled clocks")
    for name,expected in frozen["output_hashes"].items():
        if summary["output_hashes"].get(name)!=expected:raise ValueError("Sampling outputs changed after freeze")
    cases,controls=read("case_labels"),read("control_labels")
    labels=check_labels(raw,case_requests,cases)+check_labels(raw,control_requests,controls)
    inference=json.loads((r/"statistics.json").read_text());pair_count=check_statistics(cases,controls,read("paired_labels"),inference)
    for path,expected in hashes.items():
        if sha(path)!=expected:raise ValueError("Evidence drift during audit")
    if sha(r/"summary.json")!=before:raise ValueError("Summary drift")
    result=dict(status="passed",summary_sha256=before,auditor_commit=commit,auditor_sha256=sha(own),
                labels_rebuilt=labels,pairs_rebuilt=pair_count,seeded_controls_rebuilt=allocated,
                saved_open_rows=len(raw),hashes_verified=len(hashes),builder_sources_verified=len(summary["sources"]),
                raw_archive_read=False,post2024_prices_read=False,independent_of_strategy_helpers=True,
                limitation="Saved evidence audit,not raw exchange authenticity or stop-managed execution validation.")
    with (E/"audit.json").open("x") as handle:json.dump(result,handle,indent=2)
    print(json.dumps(result))


if __name__=="__main__":main()
