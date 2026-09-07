"""V39 frozen entry-policy comparison; approved 2023-2024 source only.

Inputs are original pre-outcome K2 requests, fixed stops/ATR and native5 OHLC.
Pending confirmation uses completed bars only. Future prices are used solely
for execution labels, never for choosing request membership or entry windows.
All original request contributions and controls remain, including cash/unknown.
CSV projection/parity: pandas2.3.3 read_csv float_precision='round_trip'.
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.merge.html
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS,add_features,resample_complete
from yoyo.evaluation import owner_k1k2_pending_entry_audit as previous
from yoyo.evaluation.k1k2_delayed_entry import replay_delayed,entry_ledger,annotate_immediate
from yoyo.evaluation.owner_k1k2_genuine_flow import ROOT,FOLDS,sha,clean,write_json,load_month,month_inference,rank_diagnostics
from yoyo.evaluation.owner_k1k2_transition_exit import baseline_parity
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events

REL=Path("experiments/active/exp-btcusdtp-owner-k1k2-delayed-entry-20260907-v39")
HERE=ROOT/REL
POLICY=dict(management_minutes=5,exit_mode="transition_colour",confirmations=1,max_hours=72,cost_fraction=.002)


def checked():
    commit,_,receipt,manifest=previous.checked()
    config=json.loads((HERE/"config.json").read_text())
    if (config["policy"]!=POLICY or config["entry_lifetime_minutes"]!=60
            or config["entry_end_inclusive"] is not False or config["absolute_horizon_from_original_request"] is not True
            or config["flow_gate"] is not False or config["draws"]!=9999 or config["seed"]!=20260906
            or any(config[k] is not False for k in ["holdout_evaluated","training_eligible","production_eligible"])):
        raise ValueError("Frozen single-entry-policy scope drift")
    for p in config["builder_paths"]+[str(REL/"config.json"),str(REL/"PROJECT_PLAN.md")]:
        if subprocess.check_output(["git","show",commit+":"+p],cwd=ROOT)!=(ROOT/p).read_bytes():
            raise ValueError("Commit exact V39 builder/tests before replay: "+p)
    if sha(ROOT/config["baseline_summary"])!=config["baseline_summary_sha256"]:
        raise ValueError("Original parity receipt bytes drift")
    return commit,config,receipt,manifest


def compare(a,b):
    """Same original request, possibly different fill; unknown cannot become cash."""
    keys=["event_id","decision_time","fold","direction","initial_stop","signal_atr"]
    if not a.event_id.is_unique or not b.event_id.is_unique or set(a.event_id)!=set(b.event_id):
        raise ValueError("Original request universe drift")
    a=a.sort_values("event_id").reset_index(drop=True);b=b.sort_values("event_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(a[keys],b[keys],check_dtype=False,check_exact=True)
    out=a[keys].copy()
    for arm,f in [("immediate",a),("delayed",b)]:
        for target,source in [("net","request_net"),("gross","request_gross"),("known","request_known"),
                ("outcome","outcome"),("entry_time","entry_time"),("entry_price","entry_price"),
                ("exit_time","exit_time"),("hold_minutes","hold_minutes")]: out[arm+"_"+target]=f[source]
    out["known"]=out.immediate_known & out.delayed_known
    for measure in ["net","gross"]: out["delta_"+measure]=out["delayed_"+measure]-out["immediate_"+measure]
    out["delta_saved_cost"]=out.delta_net-out.delta_gross
    out["delta_hold_minutes"]=out.delayed_hold_minutes-out.immediate_hold_minutes
    out["entry_shift_minutes"]=(pd.to_datetime(out.delayed_entry_time,utc=True)-pd.to_datetime(out.immediate_entry_time,utc=True)).dt.total_seconds()/60
    out["diagnostic_group"]=np.select([out.delayed_outcome.astype(str).str.startswith("no_fill_"),out.entry_shift_minutes.gt(0)],
                                      ["foregone_request","delayed_fill"],default="same_or_unknown_entry")
    return out


def pairs(cases,controls,assignments,requests):
    ctrl=controls.merge(requests[["event_id","parent_event_id"]],on="event_id",validate="one_to_one")
    rows=[]
    for event_id in assignments.loc[assignments.match_status.eq("matched"),"event_id"]:
        c=cases.loc[cases.event_id.eq(event_id)];r=ctrl.loc[ctrl.parent_event_id.eq(event_id)]
        if len(c)!=1 or len(r)!=3 or r.event_id.nunique()!=3: raise ValueError("Original three-control support changed")
        row=c.iloc[0];known=bool(row.known and r.known.all())
        out=dict(event_id=event_id,decision_time=row.decision_time,fold=row.fold,controls=3,complete_pair=known)
        for measure in ["net","gross"]:
            for arm in ["immediate","delayed"]:
                out[arm+"_excess_"+measure]=row[arm+"_"+measure]-r[arm+"_"+measure].mean() if known else np.nan
            out["delta_excess_"+measure]=out["delayed_excess_"+measure]-out["immediate_excess_"+measure]
        out["delta_excess_saved_cost"]=out["delta_excess_net"]-out["delta_excess_gross"]
        rows.append(out)
    return pd.DataFrame(rows)


def metrics(f):
    known=f.request_known.astype(bool);filled=f.entry_time.notna() & ~f.outcome.astype(str).str.startswith("entry_")
    closed=f.loc[filled & f.closed.astype(bool)]
    n=closed.net_return.astype(float);positive=n.clip(lower=0).sum();negative=-n.clip(upper=0).sum()
    return clean(dict(requests=len(f),known=int(known.sum()),unknown=int((~known).sum()),
        known_no_fill=int((known&(f.entry_time.isna()|f.outcome.eq("entry_invalid_risk"))).sum()),executed_closed=len(closed),
        mean_request_net_bp=f.request_net.mean()*1e4 if known.all() else None,
        mean_request_gross_bp=f.request_gross.mean()*1e4 if known.all() else None,
        mean_known_request_net_bp=f.loc[known,"request_net"].mean()*1e4,
        mean_trade_net_bp=n.mean()*1e4,median_trade_net_bp=n.median()*1e4,
        trade_win_rate=n.gt(0).mean(),trade_pf=positive/negative if negative>0 else None,
        outcomes=f.outcome.value_counts().to_dict()))


def diagnostic_groups(changes, cohort):
    """Keep original group denominators; do not compare unlike known subsets."""
    output=[]
    for group,g in changes.groupby("diagnostic_group"):
        complete=bool(g.known.all())
        row=dict(cohort=cohort,group=group,n=len(g),known=int(g.known.sum()),
                 unknown=int((~g.known).sum()))
        for target,source in [("immediate_net_bp","immediate_net"),("delayed_net_bp","delayed_net"),
                ("delta_net_bp","delta_net"),("delta_gross_bp","delta_gross"),("saved_cost_bp","delta_saved_cost")]:
            row[target]=g[source].mean()*1e4 if complete else None
        row["lost_winners"]=int((g.immediate_net.gt(0)&g.delayed_net.le(0)).sum()) if complete else None
        row["recovered_winners"]=int((g.immediate_net.le(0)&g.delayed_net.gt(0)).sum()) if complete else None
        output.append(row)
    return output


def run():
    commit,config,receipt,manifest=checked();output=ROOT/config["output_dir"]
    if output.exists() or (HERE/"summary.json").exists(): raise ValueError("One-shot economic evidence already exists")
    cases=previous.read_requests(ROOT/previous.SOURCE/"case_requests.csv","case")
    controls=previous.read_requests(ROOT/previous.SOURCE/"control_requests.csv","control")
    assignments=pd.read_csv(ROOT/previous.SOURCE/"assignments.csv",float_precision="round_trip")
    previous.validate_support(pd.concat([cases,controls],ignore_index=True),assignments)
    cases=cases.drop(columns="audit_cutoff");controls=controls.drop(columns="audit_cutoff")
    source=pd.concat([load_month(ROOT/r["output_path"],r) for r in manifest["monthly"]],ignore_index=True)
    raw=resample_complete(source[BAR_COLUMNS],5);native=add_features(raw,"SMA",40)
    output.mkdir(parents=True);files={}
    def save(name,frame):
        path=output/(name+".csv")
        if path.exists(): raise ValueError("Refuse evidence overwrite")
        frame.to_csv(path,index=False,float_format="%.17g")
        files[str(path.relative_to(ROOT))]=dict(sha256=sha(path),rows=len(frame),columns=list(frame))
    for name,f in [("case_requests",cases),("control_requests",controls),("assignments",assignments)]: save(name,f)
    write_json(HERE/"pre_outcome_receipt.json",dict(source_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),
        config_sha256=sha(HERE/"config.json"),files=files.copy(),outcomes_materialized=False))
    result={};foldrows=[];ledgers={}
    for cohort,requests in [("case",cases),("control",controls)]:
        immediate=[];delayed=[];audits=[];traces=[]
        for fold,_,end in FOLDS:
            req=requests.loc[requests.fold.eq(fold)]
            immediate.append(annotate_immediate(simulate_events(raw,native,req,POLICY,end_exclusive=end)))
            d,a,t=replay_delayed(raw,native,req,POLICY,end_exclusive=end)
            delayed.append(d);audits.append(a);traces.append(t)
        save(cohort+"_entry_audit",pd.concat(audits,ignore_index=True));save(cohort+"_entry_trace",pd.concat(traces,ignore_index=True))
        for arm,frames in [("immediate",immediate),("delayed",delayed)]:
            t=pd.concat(frames,ignore_index=True);result[arm,cohort]=t
            save(arm+"_"+cohort+"_trades",t)
            for fold,_,_ in FOLDS: foldrows.append(dict(arm=arm,cohort=cohort,fold=fold,**metrics(t.loc[t.fold.eq(fold)])))
            l=entry_ledger(t);save(arm+"_"+cohort+"_single_position",l)
            ledgers[arm+"_"+cohort]=dict(selected=int(l.portfolio_selected.sum()),
                reasons=l.portfolio_skip_reason.value_counts().to_dict(),metrics=metrics(l.loc[l.portfolio_selected]))
    # Old economic files are opened only now, solely to verify unchanged baseline.
    old=json.loads((ROOT/config["baseline_summary"]).read_text());parity={}
    for cohort in ["case","control"]:
        path="data/owner_k1k2_transition_exit_v37/transition_"+cohort+"_trades.csv"
        if sha(ROOT/path)!=old["files"][path]["sha256"]: raise ValueError("V37 baseline parity bytes drift")
        parity[cohort]=baseline_parity(result["immediate",cohort],pd.read_csv(ROOT/path,float_precision="round_trip"))
    c=compare(result["immediate","case"],result["delayed","case"])
    r=compare(result["immediate","control"],result["delayed","control"])
    p=pairs(c,r,assignments,controls)
    for name,f in [("case_changes",c),("control_changes",r),("paired_contrasts",p),("fold_metrics",pd.DataFrame(foldrows))]: save(name,f)
    inf=month_inference(p,"delta_excess_net",config["draws"],config["seed"])
    summaries={arm:{cohort:metrics(result[arm,cohort]) for cohort in ["case","control"]} for arm in ["immediate","delayed"]}
    groups=[]
    for cohort,delta in [("case",c),("control",r)]:
        groups.extend(diagnostic_groups(delta,cohort))
    absolute_excess=p.delayed_excess_net.mean()*1e4 if p.complete_pair.all() else None
    delayed=result["delayed","case"];finite=delayed.loc[delayed.closed]
    ownfold=[x for x in foldrows if x["arm"]=="delayed" and x["cohort"]=="case"]
    gates=dict(min80=len(finite)>=80,min12_per_fold=all(x["executed_closed"]>=12 for x in ownfold),
        four_positive_folds=all(x["mean_request_net_bp"] is not None and x["mean_request_net_bp"]>0 for x in ownfold),
        positive_net=(summaries["delayed"]["case"]["mean_request_net_bp"] or 0)>0,
        pf1_1=(summaries["delayed"]["case"]["trade_pf"] or 0)>=1.1,
        min12_active_months=pd.to_datetime(finite.decision_time,utc=True).dt.strftime("%Y-%m").nunique()>=12,
        min3_months_per_fold=all(pd.to_datetime(finite.loc[finite.fold.eq(f),"decision_time"],utc=True).dt.strftime("%Y-%m").nunique()>=3 for f,_,_ in FOLDS),
        matched_coverage90=len(p)/len(cases)>=.9,all_pairs_known=bool(p.complete_pair.all()),
        primary_p01=inf.get("p_one_sided",1)<.01,primary_ci_positive=inf.get("ci95_bp",[-np.inf])[0]>0,
        positive_absolute_excess=bool(absolute_excess is not None and absolute_excess>0))
    rank=delayed.copy();rank["closed"]=rank.request_known;rank["net_return"]=rank.request_net;rank["gross_return"]=rank.request_gross
    summary=dict(source_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),config_sha256=sha(HERE/"config.json"),
        status="research_gate_failed" if not all(gates.values()) else "research_pass_not_deployable",
        metrics=summaries,fold_metrics=foldrows,diagnostic_groups=groups,ledgers=ledgers,
        primary_inference=inf,absolute_excess_net_bp=absolute_excess,gates=gates,
        case_improved=int(c.delta_net.gt(1e-12).sum()),case_worsened=int(c.delta_net.lt(-1e-12).sum()),case_unchanged=int(c.delta_net.abs().le(1e-12).sum()),
        baseline_parity=parity,single_feature_body_ratio=rank_diagnostics(rank,"body_ratio"),files=files,
        holdout_evaluated=False,training_eligible=False,production_eligible=False)
    end_commit,end_config,end_receipt,end_manifest=checked()
    if (end_config,end_receipt,end_manifest)!=(config,receipt,manifest): raise ValueError("Frozen inputs changed during replay")
    summary["verification_commit"]=end_commit
    write_json(HERE/"summary.json",summary)
    print(json.dumps(clean({k:v for k,v in summary.items() if k not in {"files","fold_metrics","baseline_parity"}}),indent=2))


if __name__=="__main__":
    try: run()
    except Exception as exc:
        if (HERE/"pre_outcome_receipt.json").exists() and not (HERE/"summary.json").exists() and not (HERE/"failed_run.json").exists():
            write_json(HERE/"failed_run.json",dict(generated_at=pd.Timestamp.now(tz="UTC"),
                error_type=type(exc).__name__,reason=str(exc),outputs_preserved=True))
        raise
