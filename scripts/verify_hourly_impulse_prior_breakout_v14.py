"""Independent V14 support-only audit from saved entry-known hourly windows.

No raw archive, historical outcomes, strategy replay, or economics are read.
The saved window contains prior20 hours plus the already-completed K1 hour.
Max/min are reconstructed from PRIOR rows only; the K1 row only supplies the
close used in a strict breakout comparison. This is a saved-source window audit,
not an independent verification of underlying raw5m hourly aggregation.

Python3.9 explicit CSV strings and direct-file stdlib helper loading:
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/importlib.html#importing-a-source-file-directly
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
import gzip
import importlib.util
import json
import math
from pathlib import Path
import re


_SPEC=importlib.util.spec_from_file_location("_v14_saved_common",Path(__file__).with_name("verify_hourly_impulse_launch_v11.py"))
h=importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h)
VerificationError=h.VerificationError
require,number,equal_number,boolean,stamp=h.require,h.number,h.equal_number,h.boolean,h.stamp
indexed,parity,safe_path,sha,read_json=h.indexed,h.parity,h.safe_path,h.sha,h.read_json
ROOT,HOUR,FOLDS=h.ROOT,h.HOUR,h.FOLDS
STATES=("accepted","abstain","unknown")
OUTCOME_COLUMN=re.compile(r"(^|_)(pnl|returns?|mfe|mae|outcome|closed)($|_)",re.I)
SOURCE_COLUMNS={"population","event_id","role","open_time","open","high","low","close","segment_id"}
EXPERIMENT_ID="exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14"
EXPERIMENT_PATH="experiments/active/"+EXPERIMENT_ID
BASE_PATH="experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
PARENT_PATH="experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
INPUT_NAMES={"original_mothers.csv.gz","control_mothers.csv.gz","assignments.csv","assignment_receipt.json"}
CSV_NAMES={name+".csv" for name in ("entry_context","counts","matched_support","prior_hourly_rows")}
SUPPORT={"minimum_events":80,"minimum_per_fold":12,"minimum_active_months":12,"minimum_months_per_fold":3}
GATE={"prior_hours":20,"exclude_k1":True,"require_contiguous":True,"long":"own_signal_close > prior_high20",
    "short":"own_signal_close < prior_low20","equal_boundary":"abstain","missing_context":"unknown",
    "control_gate":"own_context_no_transfer","decision":"K1_close","extra_ma_slope_or_4h_gate":False,"length_grid":False}
CONTEXT_COLUMNS={"prior_breakout_"+suffix for suffix in ("window_start","window_end","available_at","signal_available_at",
    "count","high","low","signal_close","raw_segment_id","known","reason","gate_state")}
SOURCE_FILES={"yoyo/data/hourly_impulse.py","yoyo/data/hourly_impulse_prior_breakout.py",
    "yoyo/evaluation/hourly_impulse_research.py","yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_support_research.py","yoyo/evaluation/hourly_impulse_prior_breakout_research.py",
    "tests/test_hourly_impulse_prior_breakout.py","tests/test_hourly_impulse_prior_breakout_research.py"}


def read_csv(path):
    """Reject economic/outcome schemas before consuming any row values."""
    require(path.is_file() and not path.is_symlink(),"Missing support CSV or symlink")
    opener=gzip.open if path.suffix==".gz" else open
    with opener(path,"rt",newline="",encoding="utf-8") as handle:
        reader=csv.DictReader(handle)
        columns=reader.fieldnames
        require(columns and len(columns)==len(set(columns)),"Missing or duplicate CSV columns")
        require(not any(OUTCOME_COLUMN.search(col) or col.startswith("max_favourable") or
            col in {"exit_time","exit_price","net_r","policy_fee_fraction"} for col in columns),"Outcome/economics column in support-only CSV")
        rows=list(reader)
    require(all(None not in row and all(value is not None for value in row.values()) for row in rows),"Ragged support CSV")
    return rows


def analyze_windows(context,source_windows):
    """Return independent causal-window facts for every original opportunity."""
    originals={}
    for row in context:
        key=(row["population"],row["event_id"])
        require(key[0] in ("case","control") and isinstance(key[1],str) and key[1].strip() and key not in originals,"Duplicate/foreign support ID")
        signal,decision=stamp(row["signal_time"]),stamp(row["decision_time"])
        require(signal % HOUR==0 and signal+HOUR==decision,"K1 decision must follow its exact complete1h candle")
        direction=number(row["direction"])
        require(direction in (-1,1),"Invalid direction")
        require(row["fold"] in FOLDS,"Unknown frozen halfyear")
        start,end=FOLDS[row["fold"]]
        require(h.date_stamp(start)<=decision<h.date_stamp(end)-72*HOUR,"Mother outside frozen halfyear/72h embargo")
        originals[key]=row
    windows=defaultdict(dict);physical={}
    for source in source_windows:
        require(set(source)==SOURCE_COLUMNS,"Saved window schema changed")
        key=(source["population"],source["event_id"])
        require(key in originals,"Saved source window belongs to a foreign opportunity")
        time=stamp(source["open_time"]);signal=stamp(originals[key]["signal_time"])
        require(time % HOUR==0 and signal-20*HOUR<=time<=signal,"Source window contains future or out-of-window hour")
        require(time not in windows[key],"Repeated hour within source window")
        role="k1" if time==signal else "prior"
        require(source["role"]==role,"Current K1 contaminated prior20 window, or prior row relabelled K1")
        o,high,low,close=(number(source[field]) for field in ("open","high","low","close"))
        require(min(o,high,low,close)>0 and low<=min(o,close)<=max(o,close)<=high,"Invalid saved source OHLC")
        number(source["segment_id"])
        values=(o,high,low,close,number(source["segment_id"]))
        require(time not in physical or physical[time]==values,"Same BTC source hour has contradictory request-specific OHLC/segment values")
        physical[time]=values
        windows[key][time]=source
    facts={}
    for key,row in originals.items():
        signal=stamp(row["signal_time"]);sources=windows[key]
        prior=[sources[t] for t in sorted(sources) if t<signal]
        k1=sources.get(signal)
        prior_times={stamp(r["open_time"]) for r in prior}
        tail=[]
        for i in range(1,21):
            source=sources.get(signal-i*HOUR)
            if source is None or (tail and number(source["segment_id"])!=number(tail[0]["segment_id"])):break
            tail.append(source)
        continuous=len(tail)==20 and k1 is not None and number(k1["segment_id"])==number(tail[0]["segment_id"])
        close=number(k1["close"]) if k1 else None
        if k1 and "signal_close" in row:equal_number(row["signal_close"],close,"Own K1 close differs from saved source")
        high=max(number(r["high"]) for r in tail) if len(tail)==20 else None
        low=min(number(r["low"]) for r in tail) if len(tail)==20 else None
        state="unknown" if not continuous else "accepted" if (close>high if number(row["direction"])==1 else close<low) else "abstain"
        facts[key]=dict(prior_count=len(tail),saved_prior_rows=len(prior),known=continuous,gate_state=state,prior_high=high,prior_low=low,k1_close=close,
            first_prior_open=min(prior_times) if prior_times else None,last_prior_open=max(prior_times) if prior_times else None)
    return facts


def support_counts(context,facts):
    """Admission counts, not selected outcomes, determine whether replay may start."""
    groups={}
    for population in ("case","control"):
        part=[row for row in context if row["population"]==population]
        states=Counter(facts[(population,row["event_id"])]["gate_state"] for row in part)
        admitted=[row for row in part if facts[(population,row["event_id"])]["gate_state"]=="accepted"]
        folds={fold:sum(row["fold"]==fold for row in admitted) for fold in FOLDS}
        months={fold:set() for fold in FOLDS}
        for row in admitted:
            #UTC month is derived from the original completed-K1 decision clock.
            time=stamp(row["decision_time"])
            month=(h.EPOCH+h.timedelta(seconds=time//10**9)).strftime("%Y-%m")
            months[row["fold"]].add(month)
        groups[population]=dict(total=len(part),**{state:states[state] for state in STATES},accepted_by_fold=folds,
            active_months=sum(len(values) for values in months.values()),accepted_months_by_fold={fold:len(values) for fold,values in months.items()})
    case=groups["case"]
    gates=dict(minimum_events=case["accepted"]>=80,minimum_per_fold=min(case["accepted_by_fold"].values())>=12,
        minimum_active_months=case["active_months"]>=12,minimum_months_per_fold=min(case["accepted_months_by_fold"].values())>=3)
    return groups,gates


def verify_context(context,facts):
    for row in context:
        require(CONTEXT_COLUMNS<=row.keys(),"Missing prior-breakout diagnostics")
        fact=facts[(row["population"],row["event_id"])]
        signal=stamp(row["signal_time"])
        expected={"window_start":signal-20*HOUR,"window_end":signal-HOUR,"available_at":signal,"signal_available_at":signal+HOUR}
        for suffix,value in expected.items():require(stamp(row["prior_breakout_"+suffix])==value,"Prior20 requested-window clock drift")
        require(boolean(row["prior_breakout_known"])==fact["known"],"Known/unknown differs from saved contiguous source")
        require(row["prior_breakout_gate_state"]==fact["gate_state"],"Strict breakout/equality/unknown gate drift")
        if row["population"]=="case" and fact["known"] and "breakout20" in row:
            require(boolean(row["breakout20"])==(fact["gate_state"]=="accepted"),"Original pre-entry breakout20 boolean differs from reconstructed known case")
        for suffix,value in (("count",fact["prior_count"]),("high",fact["prior_high"]),("low",fact["prior_low"]),("signal_close",fact["k1_close"])):
            equal_number(row["prior_breakout_"+suffix],value,"Prior20 source reconstruction drift: "+suffix)
        #The saved source windows use aggregated hourly segment IDs. The helper
        #stores raw5m IDs: different counter spaces must NOT be compared.
        number(row["prior_breakout_raw_segment_id"],nullable=fact["k1_close"] is None)
        reason=row["prior_breakout_reason"]
        require(reason=="known" if fact["known"] else reason in {"no_source","missing_signal_hour","warmup","source_gap"},
                "Unknown/known reason contradicts reconstructed support")


def verify_count_table(context,facts,counts):
    expected={}
    months=["{:04d}-{:02d}".format(year,month) for year in (2023,2024) for month in range(1,13)]
    dimensions={"all":["all"],"fold":list(FOLDS),"direction":["1","-1"],"month":months}
    for population in ("case","control"):
        part=[row for row in context if row["population"]==population]
        for dimension,keys in dimensions.items():
            for key in keys:
                if dimension=="all":subset=part
                elif dimension=="fold":subset=[row for row in part if row["fold"]==key]
                elif dimension=="direction":subset=[row for row in part if number(row["direction"])==int(key)]
                else:subset=[row for row in part if (h.EPOCH+h.timedelta(seconds=stamp(row["decision_time"])//10**9)).strftime("%Y-%m")==key]
                states=Counter(facts[(population,row["event_id"])]["gate_state"] for row in subset)
                expected[(population,dimension,key)]=dict(total=len(subset),**{s:states[s] for s in STATES},accepted_rate=states["accepted"]/len(subset) if subset else None)
    actual={(row["population"],row["dimension"],row["key"]):row for row in counts}
    require(len(actual)==len(counts) and actual.keys()==expected.keys(),"Count table omitted or duplicated a fixed dimension (including empty months)")
    for key,values in expected.items():
        for field,value in values.items():equal_number(actual[key][field],value,"Support count/rate denominator drift")
    return len(actual)


def verify_tables(context,source_windows,counts,matched,summary,*,expected_counts=(251,462,154)):
    n,m,k=expected_counts
    facts=analyze_windows(context,source_windows);verify_context(context,facts)
    groups,gates=support_counts(context,facts)
    require(groups["case"]["total"]==n and groups["control"]["total"]==m,"Original713 opportunity denominator drift")
    look={(row["population"],row["event_id"]):row for row in context}
    controls={};control_times=set()
    for row in context:
        if row["population"]!="control":continue
        parent=row["parent_event_id"]
        require(("case",parent) in look,"Foreign control parent")
        require(row["fold"]==look[("case",parent)]["fold"],"Fixed control moved to another halfyear")
        time=stamp(row["decision_time"])
        require(time not in control_times,"Original control time reused")
        control_times.add(time);controls.setdefault(parent,[]).append(row["event_id"])
    require(len(controls)==k and all(len(group)==3 for group in controls.values()),"Original three-control groups changed")
    pairs=indexed(matched)
    require(pairs.keys()==controls.keys(),"Matched support must preserve all original154 triples")
    all_known=0
    for parent,ids in controls.items():
        row=pairs[parent];case=look[("case",parent)]
        case_state=facts[("case",parent)]["gate_state"]
        require(row["fold"]==case["fold"] and row["case_state"]==case_state,"Matched case identity/state drift")
        require(row["control_ids"]=="|".join(sorted(ids)),"Matched control identities/rematching drift")
        states=Counter(facts[("control",key)]["gate_state"] for key in ids)
        for field,value in {"total":3,**{s:states[s] for s in STATES}}.items():equal_number(row["control_"+field],value,"Matched support counts follow selected subset")
        known=case_state!="unknown" and states["unknown"]==0
        require(boolean(row["all_known"])==known,"Matched unknowns were coerced to known")
        all_known+=known
    for population,group in groups.items():
        require(set(summary["population"][population])=={"total",*STATES},"Population summary schema drift")
        for field in ("total",*STATES):equal_number(summary["population"][population][field],group[field],"Population summary count drift")
    values=dict(events=groups["case"]["accepted"],minimum_fold_events=min(groups["case"]["accepted_by_fold"].values()),
        active_months=groups["case"]["active_months"],minimum_fold_months=min(groups["case"]["accepted_months_by_fold"].values()))
    require(set(summary["support_values"])==set(values) and set(summary["support_gates"])==set(gates),"Fixed support gates omitted/added")
    for field,value in values.items():equal_number(summary["support_values"][field],value,"Support gate used wrong population/denominator")
    for field,value in gates.items():require(summary["support_gates"][field] is value,"Support gate threshold drift")
    passed=all(gates.values())
    require(summary["support_pass"] is passed,"Support pass does not require every fixed gate")
    require(summary["status"]==("support_pass_requires_separate_replay" if passed else "insufficient_support_no_outcomes"),"Wrong support-stage terminal status")
    for field,value in (("matched",k),("unmatched",n-k),("all_known",all_known),("coverage",k/n),("required_coverage",.9)):
        equal_number(summary["matching"][field],value,"Original matching support changed")
    require(summary["matching"]["coverage_pass"] is False,"Old61.35percent support promoted to90percent")
    equal_number(summary["gate_hours"],20,"Prior lookback changed")
    equal_number(summary["outcome_replays"],0,"Support stage ran outcome replay")
    for field in ("outcomes_read_or_computed","profitability_test","holdout_consumed","training_eligible","production_eligible"):
        require(summary[field] is False,"Support stage claims outcomes/profitability/eligibility")
    return dict(status="passed",support_status=summary["status"],population={p:{f:groups[p][f] for f in ("total",*STATES)} for p in groups},
        support_values=values,support_gates=gates,matched_groups=k,unmatched=n-k,count_rows=verify_count_table(context,facts,counts),
        saved_source_rows=len(source_windows),raw_price_replay=False,saved_hourly_extrema_recomputed=True,outcomes_read_or_computed=False,
        limitation="Only saved entry-known hourly windows; no raw5m aggregation replay, economics, power, or profitability proof.")


def verify_config(config):
    require(config["experiment_id"]==EXPERIMENT_ID and config["base_config"]==BASE_PATH and config["parent_results"]==PARENT_PATH,"Wrong support experiment or prior input directory")
    require(set(config["inputs"])==INPUT_NAMES,"Only four pinned pre-entry inputs may be read")
    require(config["development_folds"]==[[fold,a,c] for fold,(a,c) in FOLDS.items()],"Frozen halfyears changed")
    require(json.dumps(config["gate"],sort_keys=True)==json.dumps(GATE,sort_keys=True) and config["support"]==SUPPORT,"Prior20 contract/support threshold changed")
    require(config["expected"]==dict(mothers=251,controls=462,matched=154,status_counts={"matched":154,"insufficient_exact_controls":94,"missing_causal_matching_support":3}),"Original support population changed")
    require(config["inherited_execution_not_run"]==dict(cost_fraction=.002,max_hours=72,stop="K1_extreme",exit="5m_native40_true_aligned_to_opposite"),"Inherited policy changed during support-only stage")
    equal_number(config["matching_coverage"]["actual"],154/251,"Known coverage ceiling drift")
    equal_number(config["matching_coverage"]["required"],.9,"Matching threshold weakened")
    require(config["matching_coverage"]["pass"] is False and config["no_outcome_entry_point"] is True,"Stage enables outcome/acceptance")
    for field in ("holdout_consumed","training_eligible","production_eligible"):require(config[field] is False,"Eligibility/holdout drift")


def verify(root=ROOT,experiment_path=EXPERIMENT_PATH):
    root=Path(root);experiment=safe_path(root,experiment_path);results=experiment/"results"
    require(not (results/"failure.json").exists(),"Failed run is not support evidence")
    config,summary,started,checkpoint=(read_json(path) for path in (experiment/"config.json",results/"summary.json",results/"started.json",results/"support_frozen.json"))
    verify_config(config)
    require(summary["experiment_id"]==EXPERIMENT_ID,"Wrong summary experiment")
    require(sha(experiment/"config.json")==summary["config_sha256"],"Support config hash changed")
    base_path=safe_path(root,BASE_PATH);require(sha(base_path)==config["base_config_sha256"],"Base config hash changed")
    base=read_json(base_path)
    require(base["development_folds"]==config["development_folds"],"Base/frozen folds drift")
    require(base["execution"]["cost_fraction"]==.002 and base["execution"]["max_hours"]==72 and base["execution"]["stop_first"] is True,"Base contract drift")
    require(config["inputs"]==started["inputs"]==summary["input_hashes"],"Pinned pre-entry input receipts changed")
    for name,digest in config["inputs"].items():require(sha(safe_path(root,PARENT_PATH+"/"+name))==digest,"Old pre-entry input hash changed")
    actual={str(path.relative_to(results)) for path in results.rglob("*") if path.is_file()}
    require(actual==CSV_NAMES | {"started.json","support_frozen.json","summary.json"},"Extra/missing artifacts or outcome directory in support stage")
    require(set(summary["output_hashes"])==CSV_NAMES and summary["output_hashes"]==checkpoint["output_hashes"],"Support hash checkpoint coverage drift")
    for name,digest in summary["output_hashes"].items():require(sha(safe_path(results,name))==digest,"Saved support CSV hash mismatch")
    require(stamp(started["at"])<=stamp(checkpoint["at"])<=stamp(summary["generated_at"]),"Support checkpoint clock precedes builder/start or follows summary")
    equal_number(checkpoint["outcome_replays"],0,"Checkpoint admits outcome replay")
    required=SOURCE_FILES | {experiment_path+"/config.json",experiment_path+"/PROJECT_PLAN.md",BASE_PATH}
    source_count=h.verify_committed_sources(root,started,{"sources":summary["source_receipts"]},required)
    h.verify_commit_time(root,started)
    hashes={row["path"]:row["sha256"] for row in started["sources"]}
    require(hashes[experiment_path+"/config.json"]==summary["config_sha256"] and hashes[BASE_PATH]==config["base_config_sha256"],"Configuration differs from committed builder")
    source=summary["source_receipt"]
    require(source["sha256"]==base["source"]["sha256"] and source["holdout_price_rows"]==0 and stamp(source["phase_price_last_open"])<h.date_stamp("2025-01-01"),"Saved source exceeds frozen development")
    context=read_csv(results/"entry_context.csv")
    for population,name in (("case","original_mothers.csv.gz"),("control","control_mothers.csv.gz")):
        old=read_csv(safe_path(root,PARENT_PATH+"/"+name))
        parity(old,[row for row in context if row["population"]==population])
    assignments=read_csv(safe_path(root,PARENT_PATH+"/assignments.csv"));assignment=indexed(assignments)
    require(assignment.keys()=={row["event_id"] for row in context if row["population"]=="case"},"Original assignments lost mothers")
    require(Counter(row["match_status"] for row in assignments)==config["expected"]["status_counts"],"Original matching status counts changed")
    matched=read_csv(results/"matched_support.csv")
    require({row["event_id"] for row in matched}=={key for key,row in assignment.items() if row["match_status"]=="matched"},"Selected-only rematching changed154groups")
    output=verify_tables(context,read_csv(results/"prior_hourly_rows.csv"),read_csv(results/"counts.csv"),matched,summary)
    output.update(output_hashes_verified=4,committed_sources_verified=source_count,builder_commit=started["builder_commit"],summary_sha256=sha(results/"summary.json"))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--root",type=Path,default=ROOT);args=parser.parse_args()
    try:output=verify(args.root)
    except (VerificationError,KeyError,TypeError,ValueError,OSError) as error:
        print(json.dumps(dict(status="failed",error=str(error),outcomes_read_or_computed=False),ensure_ascii=False));return 1
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False));return 0


if __name__=="__main__":raise SystemExit(main())
