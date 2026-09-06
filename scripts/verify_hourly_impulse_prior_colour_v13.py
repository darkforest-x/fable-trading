"""Read-only V13 saved-ledger verifier, never raw prices or strategy execution.

Three-state admission is evaluated at K1 OPEN using the saved completed4h
HL2/SMA40 state. Full opportunity rows and fixed control triples survive known
abstention zeros and unknowns. This verifies saved arithmetic and clocks, not
the truth of40 source bars, raw continuity, or independent inferential p values.

Stdlib CSV strings/nulls and direct-file helper loading follow Python3.9 docs:
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/importlib.html#importing-a-source-file-directly
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import timedelta
import gzip
import importlib.util
import json
import math
from pathlib import Path


_SPEC=importlib.util.spec_from_file_location("_v13_saved_common",Path(__file__).with_name("verify_hourly_impulse_frozen_ma_v12.py"))
h=importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h)
b=h.h
VerificationError=h.VerificationError
require,number,equal_number,boolean,stamp=h.require,h.number,h.equal_number,h.boolean,h.stamp
indexed,parity,safe_path,sha,read_json,read_csv=h.indexed,h.parity,h.safe_path,h.sha,h.read_json,h.read_csv
mean,bp=h.mean,h.bp
ROOT,ARMS,TABLE_FILES,MINUTE,HOUR,FOLDS=h.ROOT,h.ARMS,h.TABLE_FILES,h.MINUTE,h.HOUR,h.FOLDS
EXPERIMENT_ID="exp-btcusdtp-1h-prior4h-colour-preholdout-20260906-v13"
EXPERIMENT_PATH="experiments/active/"+EXPERIMENT_ID
BASE_PATH,PARENT_PATH,MOTHER_PATH=h.BASE_PATH,h.PARENT_PATH,h.MOTHER_PATH
GATE_FIELDS={"prior_colour_"+suffix for suffix in ("bar_open","available_at","ma","hl2","side","known","reason","count","raw_segment_id","gate_state")}
STATES=("accepted","abstain","unknown")
UNKNOWN_REASONS={"no_complete_4h","stale_context","source_gap","warmup","invalid_colour"}
GATE_CONTRACT={"time":"signal_time_K1_open","minutes":240,"ma_kind":"SMA","ma_length":40,
    "ma_source":"HL2","side":"1_if_hl2_greater_equal_ma_else_minus1","maximum_age_hours_exclusive":4,
    "minimum_contiguous_complete_bars":40,"require_atr":False,"require_slope":False,
    "control_gate":"own_context_no_transfer","known_opposite":"zero_no_entry_no_fee","unknown":"NaN_not_abstention",
    "serial_unknown":"conservative_full72h_reservation_not_actual_position","population":"all251_cases462_controls154_fixed_triples97_unmatched"}
REQUIRED_CODE_SOURCES=(h.REQUIRED_CODE_SOURCES-{
    "yoyo/evaluation/hourly_impulse_frozen_ma_research.py","tests/test_hourly_impulse_frozen_ma_research.py","tests/test_hourly_impulse_frozen_ma_exit.py"}) | {
    "yoyo/data/hourly_impulse_prior_colour.py","tests/test_hourly_impulse_prior_colour.py",
    "yoyo/evaluation/hourly_impulse_prior_colour_research.py","tests/test_hourly_impulse_prior_colour_research.py"}
EPISODE_MUTABLE={"status","terminal_time","episode_status","episode_net_return","executed","completed_trade","observed","entry_time","exit_time","occupied_until"}


def check_gate(row, original):
    """Verify only declared source support; do not reconstruct raw40-bar SMA."""
    require(GATE_FIELDS <= row.keys(),"Missing prior-colour gate diagnostics")
    require(not GATE_FIELDS.intersection(original),"Original context acquired future gate columns")
    parity([original],[row])
    signal,decision=stamp(row["signal_time"]),stamp(row["decision_time"])
    require(signal % HOUR==0 and signal+HOUR==decision,"Gate is not at the original K1 OPEN")
    direction=number(row["direction"])
    require(direction in (-1,1),"Invalid gate direction")
    known=boolean(row["prior_colour_known"])
    side=number(row["prior_colour_side"],nullable=True)
    state=row["prior_colour_gate_state"]
    require(state in STATES,"Unknown gate state")
    require(isinstance(row["prior_colour_reason"],str) and row["prior_colour_reason"],"Missing gate reason")
    count=number(row["prior_colour_count"],nullable=True)
    require(count is None or (count==int(count) and count>=0),"Invalid declared contiguous-bar count")
    available=stamp(row["prior_colour_available_at"],nullable=True)
    opened=stamp(row["prior_colour_bar_open"],nullable=True)
    if opened is not None or available is not None:
        require(opened is not None and available is not None and opened % (4*HOUR)==0 and opened+4*HOUR==available,
                "Partial or off-grid prior4h candle clock")
        require(available<=signal,"Context consumes K1's own hour/future4h candle")
    if known:
        require(count is not None and count>=40 and side in (-1,1),"Known colour lacks40-bar support/side")
        require(row["prior_colour_reason"]=="known","Known context has contradictory diagnostic reason")
        ma,hl2=number(row["prior_colour_ma"]),number(row["prior_colour_hl2"])
        require(min(ma,hl2)>0,"Invalid completed4h price/MA")
        require(available is not None and 0<=signal-available<4*HOUR,"Known context is not the latest completed4h bar")
        require(side==(1 if hl2>=ma else -1),"Prior colour differs from own HL2>=MA convention")
        number(row["prior_colour_raw_segment_id"])
        expected="accepted" if side==direction else "abstain"
        require(state==expected,"Gate transferred direction or ignored own colour")
    else:
        require(side is None and state=="unknown","Missing context was converted into known abstention")
        require(row["prior_colour_reason"] in UNKNOWN_REASONS,"Unknown context lacks a registered reason")
    return state


def check_candidate_episode(old, row, gate, trade):
    require(old.keys()<=row.keys(),"Candidate episode lost original columns")
    for field in GATE_FIELDS:
        parity([{"event_id":row["event_id"],field:gate[field]}],[row])
    state=gate["prior_colour_gate_state"]
    if state=="accepted":
        require(trade is not None,"Accepted gate has no replayed trade")
        parity([old],[row])
        b.check_episode(trade,row)
        equal_number(row["policy_fee_fraction"],.002 if boolean(trade["closed"]) else None,"Executed trade fee/unknown cost drift")
    else:
        require(trade is None,"Non-admitted gate fabricated execution")
        parity([{key:value for key,value in old.items() if key not in EPISODE_MUTABLE}],[row])
        status="prior_colour_"+state
        require(row["status"]==row["episode_status"]==status,"Nonentry status not explicit")
        require(stamp(row["terminal_time"])==stamp(row["mother_decision_time"]),"Known-at-entry gate waited into future")
        require(not boolean(row["executed"]) and not boolean(row["completed_trade"]),"Nonentry counted as a completed trade")
        require(stamp(row["entry_time"],nullable=True) is None and stamp(row["exit_time"],nullable=True) is None,
                "Nonentry acquired fake entry/exit times")
        observed=state=="abstain"
        require(boolean(row["observed"])==observed,"Zero/unknown observability drift")
        equal_number(row["episode_net_return"],0. if observed else None,"Known abstention/unknown return drift")
        equal_number(row["policy_fee_fraction"],0. if observed else None,"Known abstention/unknown fee drift")
        until=row["mother_decision_time"] if observed else row["mother_deadline"]
        require(stamp(row["occupied_until"])==stamp(until),"Abstention/unknown occupancy drift")


def check_metrics(rows, summary):
    """Closed executed trades only; known nonentry zeros never inflate N."""
    values=b.check_metrics(rows,summary)
    if "minimum_fold_events" in summary:
        counts=Counter(row["fold"] for row in rows if boolean(row["closed"]))
        equal_number(summary["minimum_fold_events"],min(counts.get(fold,0) for fold in FOLDS),"Sample gate counts abstentions")
    return values


def check_month_support(rows,summary):
    observed={fold:set() for fold in FOLDS}
    for row in rows:
        if boolean(row["closed"]):observed[row["fold"]].add(row["entry_time"][:7])
    expected={fold:len(months) for fold,months in observed.items()}
    require(summary["by_fold"]==expected,"Trade month support changed")
    equal_number(summary["active_months"],sum(expected.values()),"Nontrading months counted active")
    equal_number(summary["minimum_months_per_fold"],min(expected.values()),"Nontrading fold counted active")


def verify_tables(tables,contexts,gates,arm_summaries,effects,*,expected_counts=(251,462,154)):
    """Pure saved-row interface, with full opportunity and selected-trade grains."""
    n,m,k=expected_counts
    originals={label:indexed(contexts[label]) for label in ("case","control")}
    require(len(originals["case"])==n and len(originals["control"])==m,"Original opportunity denominator drift")
    gate_map={}
    counts={label:dict.fromkeys(STATES,0) for label in originals}
    for row in gates:
        label,key=row["population"],row["event_id"]
        require(label in originals and key in originals[label] and (label,key) not in gate_map,"Duplicate or foreign gate identity")
        state=check_gate(row,originals[label][key])
        gate_map[(label,key)]=row;counts[label][state]+=1
    require(len(gate_map)==n+m,"Gate sidecar lost original opportunities")
    controls={key:(row["parent_event_id"],row["decision_time"]) for key,row in originals["control"].items()}
    parents=Counter(parent for parent,time in controls.values())
    require(len(parents)==k and set(parents.values())=={3} and set(parents)<=originals["case"].keys(),"Original fixed triples invalid")
    require(len({stamp(time) for parent,time in controls.values()})==m,"Control times reused")
    states,serial_values={},{}
    for arm in ARMS:
        t=tables[arm];summary=arm_summaries[arm]
        states[arm]={name:indexed(rows) for name,rows in t.items()}
        for label in originals:
            trades,episodes=states[arm][label+"_trades"],states[arm][label+"_episodes"]
            require(episodes.keys()==originals[label].keys(),"Episode denominator shrank after gate")
            expected=set(originals[label]) if arm=="baseline" else {key for key in originals[label] if gate_map[(label,key)]["prior_colour_gate_state"]=="accepted"}
            require(trades.keys()==expected,"Trade rows are not exactly admitted original requests")
            for key,row in trades.items():
                b.check_trade(row);h.check_colour_clock(row)
                parity([originals[label][key]],[row])
                if arm=="candidate":
                    old=states["baseline"][label+"_trades"][key]
                    require(row.keys()==old.keys(),"Admission-only replay changed trade schema")
                    parity([old],[row])
            for key,row in episodes.items():
                if arm=="baseline":
                    require(not GATE_FIELDS.intersection(row) and "policy_fee_fraction" not in row,"Baseline acquired new gate fields")
                    b.check_episode(trades[key],row)
                else:
                    check_candidate_episode(states["baseline"][label+"_episodes"][key],row,gate_map[(label,key)],trades.get(key))
            check_metrics(list(trades.values()),summary["metrics" if label=="case" else "control_metrics"])
        pairs=states[arm]["matched"]
        require(pairs.keys()==originals["case"].keys(),"Fixed matched table denominator shrank")
        for key,pair in pairs.items():
            case=states[arm]["case_episodes"][key]
            own_controls=[states[arm]["control_episodes"][control] for control,(parent,time) in controls.items() if parent==key]
            vals=[number(row["episode_net_return"],nullable=True) for row in own_controls]
            cm=mean(vals) if len(vals)==3 and all(value is not None for value in vals) else None
            net=number(case["episode_net_return"],nullable=True)
            excess=net-cm if net is not None and cm is not None else None
            require(stamp(pair["mother_decision_time"])==stamp(case["mother_decision_time"]) and pair["fold"]==case["fold"],"Matched cluster clock/fold changed")
            for field,value in (("assigned_controls",len(own_controls)),("event_net_return",net),("control_mean_return",cm),("excess",excess)):
                equal_number(pair[field],value,"Fixed3 mean/zero/unknown arithmetic drift: "+field)
        match=summary["matching"]
        finite=[number(pair["excess"],nullable=True) for pair in pairs.values()]
        known=sum(value is not None for value in finite)
        for field,value in (("paired_events",known),("mother_events",n),("coverage",known/n),("assignment_coverage",k/n),("mean_excess_bp",bp(mean(finite)))):
            equal_number(match[field],value,"Matching coverage/mean drift")
        serial_values[arm]=b.check_serial(t["case_episodes"],t["single_pending"])
        selected={key for key,row in states[arm]["single_pending"].items() if boolean(row["portfolio_selected"])}
        equal_number(summary["serial_selected_mothers"],len(selected),"Accepted opportunity count drift")
        check_metrics([row for key,row in states[arm]["case_trades"].items() if key in selected],summary["single_position"])
        if "months" in summary:check_month_support(t["case_trades"],summary["months"])
        values=[number(row["episode_net_return"],nullable=True) for row in states[arm]["case_episodes"].values()]
        if "net_effect" in summary:
            equal_number(summary["net_effect"]["n"],sum(value is not None for value in values),"Opportunity known denominator drift")
            equal_number(summary["net_effect"]["mean_bp"],bp(mean(values)),"Opportunity-normalized net drift")
        if "gates" in summary and "complete_evidence" in summary["gates"]:
            complete=all(boolean(row["observed"]) for label in originals for row in states[arm][label+"_episodes"].values())
            require(summary["gates"]["complete_evidence"] is complete,"Unknown evidence promoted to complete")
    effect_output={}
    for name,table,column in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),("serial_delta",None,None)):
        rows=indexed(tables[name]);require(rows.keys()==originals["case"].keys(),"D/I/serial omitted original mothers")
        deltas=[]
        for key,row in rows.items():
            a,c=((serial_values[arm][key] for arm in ARMS) if table is None else
                (number(states[arm][table][key][column],nullable=True) for arm in ARMS))
            delta=c-a if a is not None and c is not None else None
            for field,value in (("before",a),("after",c),("difference",delta)):
                equal_number(row[field],value,"Paired policy effect drift")
            require(stamp(row["mother_decision_time"])==stamp(originals["case"][key]["decision_time"]),"Effect cluster clock changed")
            deltas.append(delta)
        values=[d for d in deltas if d is not None]
        expected=dict(total_pairs=n,n=len(values),unknown_pairs=n-len(values),improved=sum(d>1e-12 for d in values),
            worsened=sum(d< -1e-12 for d in values),unchanged=sum(abs(d)<=1e-12 for d in values),mean_bp=bp(mean(values)))
        for field,value in expected.items():equal_number(effects[name][field],value,"Effect summary drift: "+field)
        effect_output[name]=expected
    no_unknown=counts["case"]["unknown"]==0
    old_selected=all(boolean(row["portfolio_selected"]) for row in tables["baseline"]["single_pending"])
    if no_unknown and old_selected:
        require(all(boolean(row["portfolio_selected"]) for row in tables["candidate"]["single_pending"]),"Known-only zero-duration gate introduced impossible serial skip")
    return dict(counts=dict(cases=n,controls=m,matched=k,unmatched=n-k),gate_counts=counts,effects=effect_output,
        raw_replay=False,inferential_p_recomputed=False,
        limitation="Saved-ledger consistency; no independent40bar/SMA/source-continuity or intrabar replay; no profitability proof.")


def verify_mechanics(tables,label,rows,group_rows,summary):
    old,new=(indexed(tables[arm][label+"_episodes"]) for arm in ARMS)
    actual=indexed(rows)
    require(actual.keys()==old.keys()==new.keys(),"Gate mechanics lost opportunities")
    groups={};avoid,miss=0,0;avoided_total,missed_total=0.,0.
    state_counts=dict.fromkeys(STATES,0)
    for key,row in actual.items():
        parity([new[key]],[row])
        a,c=(number(source[key]["episode_net_return"],nullable=True) for source in (old,new))
        delta=c-a if a is not None and c is not None else None
        equal_number(row["baseline_net_return"],a,"Mechanics changed original outcome")
        equal_number(row["difference"],delta,"Mechanics delta drift")
        state=new[key]["prior_colour_gate_state"];state_counts[state]+=1
        avoids=state=="abstain" and a is not None and a<0
        misses=state=="abstain" and a is not None and a>0
        require(boolean(row["avoided_net_loser"])==avoids and boolean(row["missed_net_winner"])==misses,
                "Retrospective avoided/missed classification drift")
        avoid+=avoids;miss+=misses
        avoided_total+=-a if avoids else 0.;missed_total+=a if misses else 0.
        groups.setdefault(state,[]).append((a,c,delta,avoids,misses))
    values=dict(total=len(old),**state_counts,known_pairs=sum(d is not None for rows_ in groups.values() for a,c,d,av,mi in rows_),
        avoided_net_losers=avoid,missed_net_winners=miss,avoided_loss_total_bp=avoided_total*1e4,missed_winner_total_bp=missed_total*1e4)
    for field,value in values.items():equal_number(summary[field],value,"Gate mechanics summary drift")
    observed={r["gate_state"]:r for r in group_rows};stated={r["gate_state"]:r for r in summary["groups"]}
    require(len(observed)==len(group_rows) and len(stated)==len(summary["groups"]) and observed.keys()==stated.keys()==groups.keys(),
            "Gate mechanism group omitted/duplicated")
    for state,group in groups.items():
        known=[(a,c,d) for a,c,d,av,mi in group if d is not None]
        expected=dict(n=len(group),known_pairs=len(known),old_mean_net_bp=bp(mean([a for a,c,d in known])),
            new_mean_net_bp=bp(mean([c for a,c,d in known])),mean_delta_bp=bp(mean([d for a,c,d in known])),
            sum_delta_event_bp=bp(math.fsum(d for a,c,d in known)) if known else None,
            avoided_net_losers=sum(av for a,c,d,av,mi in group),missed_net_winners=sum(mi for a,c,d,av,mi in group))
        for field,value in expected.items():
            equal_number(observed[state][field],value,"Saved gate group arithmetic drift")
            equal_number(stated[state][field],value,"Summary gate group arithmetic drift")
    return values


def verify_monthly(tables,rows):
    groups={}
    for arm in ARMS:
        for row in tables[arm]["case_episodes"]:
            month=(b.EPOCH+timedelta(seconds=stamp(row["mother_decision_time"])//10**9)).strftime("%Y-%m")
            groups.setdefault((arm,row["fold"],month),[]).append(row)
    actual={(row["arm"],row["fold"],row["month"]):row for row in rows}
    require(len(actual)==len(rows) and actual.keys()==groups.keys(),"Monthly opportunity groups changed")
    for key,group in groups.items():
        expected=dict(n=len(group),known=sum(boolean(row["observed"]) for row in group),executed=sum(boolean(row["executed"]) for row in group),
            mean_net_bp=bp(mean([number(row["episode_net_return"],nullable=True) for row in group])))
        for field,value in expected.items():equal_number(actual[key][field],value,"Monthly opportunity count/mean drift")
    return len(rows)


def verify_config(config,summary):
    require(config["experiment_id"]==summary["experiment_id"]==EXPERIMENT_ID,"Wrong experiment")
    require(summary["status"]=="diagnostic_only_no_candidate_acceptance","Diagnostic promoted to acceptance")
    for field in ("holdout_consumed","production_eligible","training_eligible"):
        require(config[field] is False and summary[field] is False,"Eligibility/holdout drift")
    require(config["no_audit_entry_point"] is True and summary["audit_prices_loaded"] is False,"Audit enabled")
    policy=dict(id="5m_native40",management_minutes=5,ma_kind="SMA",ma_length=40,exit_mode="transition_colour",confirmations=1)
    policies=[policy,dict(policy,id="5m_native40_prior4h_colour",entry_gate="prior4h_colour_at_k1_open")]
    require(json.dumps(config["policies"],sort_keys=True)==json.dumps(policies,sort_keys=True),"Frozen admission/exit policy changed")
    require(json.dumps(config["gate_contract"],sort_keys=True)==json.dumps(summary["gate_contract"],sort_keys=True)==json.dumps(GATE_CONTRACT,sort_keys=True),
            "Pure prior4h colour contract changed")
    require(config["base_config"]==BASE_PATH and config["parent_results"]==PARENT_PATH and config["mother_results"]==MOTHER_PATH,"Pinned evidence moved")
    require(config["known_support"]==dict(cases=251,controls=462,matched=154,coverage_gate_unattainable=True),"Known support drift")
    require(config["selection"]==dict(minimum_events=80,minimum_per_fold=12,positive_folds=4,minimum_profit_factor=1.1,
        minimum_active_months=12,minimum_months_per_fold=3,matched_coverage=.9),"Development gates weakened")
    require(config["inference"]==dict(draws=9999,seed=20260906,p_limit=.01,joint_required=["case_delta","excess_delta"],method="month_cluster"),"Inference specification changed")
    equal_number(summary["known_coverage_ceiling"],154/251,"Known coverage ceiling drift")
    equal_number(summary["coverage_required"],.9,"Coverage gate weakened")
    expected_inputs={"direct_k1_stop_"+label+"_context.csv.gz" for label in ("case","control")} | {
        "direct_k1_stop__transition_colour_"+file for file in TABLE_FILES.values()} | {"summary.json"}
    require(set(config["inputs"])==expected_inputs and set(config["mother_inputs"])=={
        "original_mothers.csv.gz","control_mothers.csv.gz","assignments.csv","assignment_receipt.json"},"Pinned input manifest changed")


def verify(root=ROOT,experiment_path=EXPERIMENT_PATH):
    root=Path(root);experiment=safe_path(root,experiment_path);results=experiment/"results"
    require(not (results/"failure.json").exists(),"Failed attempt is not completed evidence")
    config,summary,started=(read_json(path) for path in (experiment/"config.json",results/"summary.json",results/"started.json"))
    verify_config(config,summary)
    require(sha(experiment/"config.json")==summary["config_sha256"],"Config hash drift")
    base_path=safe_path(root,config["base_config"])
    require(sha(base_path)==config["base_config_sha256"],"Base config hash drift")
    base=read_json(base_path)
    require(base["execution"]["cost_fraction"]==.002 and base["execution"]["max_hours"]==72 and base["execution"]["stop_first"] is True,"Base economics drift")
    require(base["development_folds"]==[[fold,a,c] for fold,(a,c) in FOLDS.items()],"Development folds changed")
    require(summary["source"]["sha256"]==base["source"]["sha256"] and summary["source"]["holdout_price_rows"]==0 and
        stamp(summary["source"]["phase_price_last_open"])<b.date_stamp("2025-01-01"),"Saved source is not original development")
    for directory,key in ((config["parent_results"],"inputs"),(config["mother_results"],"mother_inputs")):
        require(config[key]==summary[key]==started[key],"Pinned input receipts differ")
        for name,digest in config[key].items():require(sha(safe_path(root,directory+"/"+name))==digest,"Pinned old input changed: "+name)
    b.verify_output_hashes(results,summary["output_hashes"])
    required=REQUIRED_CODE_SOURCES | {experiment_path+"/config.json",experiment_path+"/PROJECT_PLAN.md",config["base_config"]}
    source_count=b.verify_committed_sources(root,started,summary,required)
    source_hashes={row["path"]:row["sha256"] for row in started["sources"]}
    require(source_hashes[experiment_path+"/config.json"]==summary["config_sha256"] and source_hashes[config["base_config"]]==config["base_config_sha256"],
            "Current configuration differs from its committed builder")
    b.verify_commit_time(root,started)
    contexts={label:read_csv(results/(label+"_context.csv.gz")) for label in ("case","control")}
    tables={arm:{name:read_csv(results/arm/file) for name,file in TABLE_FILES.items()} for arm in ARMS}
    for label in ("case","control"):
        name=label+"_trades"
        headers=[]
        for arm in ARMS:
            path=results/arm/TABLE_FILES[name]
            with gzip.open(path,"rt",newline="") as handle:headers.append(next(csv.reader(handle)))
        require(headers[0]==headers[1],"Accepted replay changed the original CSV schema, including empty selections")
    for name in ("case_delta","excess_delta","serial_delta"):tables[name]=read_csv(results/(name+".csv"))
    for label,context in contexts.items():
        parity(read_csv(safe_path(root,PARENT_PATH+"/direct_k1_stop_"+label+"_context.csv.gz")),context)
        parity(read_csv(safe_path(root,MOTHER_PATH+"/"+("original_mothers" if label=="case" else "control_mothers")+".csv.gz")),context)
    anchor=read_json(results/"anchor_parity.json")
    for name,file in TABLE_FILES.items():
        old=read_csv(safe_path(root,PARENT_PATH+"/direct_k1_stop__transition_colour_"+file))
        parity(old,tables["baseline"][name])
        equal_number(anchor[name]["rows"],len(old),"Baseline receipt row count changed")
        equal_number(anchor[name]["columns"],len(old[0]),"Baseline receipt schema changed")
    assignments=read_csv(results/"assignments.csv")
    parity(read_csv(safe_path(root,MOTHER_PATH+"/assignments.csv")),assignments)
    assignment=indexed(assignments)
    require(assignment.keys()==indexed(contexts["case"]).keys(),"Assignments omitted mothers")
    require({key for key,row in assignment.items() if row["match_status"]=="matched"}=={row["parent_event_id"] for row in contexts["control"]},
            "Original154 assignments replaced")
    for arm in ARMS:require(read_json(results/arm/"summary.json")==summary["arms"][arm],"Arm/root summary changed")
    gates=read_csv(results/"context_gates.csv")
    output=verify_tables(tables,contexts,gates,summary["arms"],summary["effects"])
    receipt=read_json(results/"context_gates_frozen.json")
    require(receipt==summary["context_receipt"] and receipt["sha256"]==sha(results/"context_gates.csv") and
        receipt["before_any_arm_outcomes"] is True and stamp(receipt["at"])>=stamp(started["at"]),"Context gates not frozen by declared checkpoint")
    for label,count in output["gate_counts"].items():
        expected=dict(total=sum(count.values()),**count)
        require(receipt["populations"][label]==expected and summary["arms"]["candidate"]["gate_counts"][label]==expected,"Gate count receipt/summary changed")
        meta=summary["arms"]["candidate"]["parity"]["accepted_trade_fields_unchanged"][label]
        equal_number(meta["rows"],count["accepted"],"Accepted replay parity receipt count drift")
        equal_number(meta["columns"],len(tables["baseline"][label+"_trades"][0]),"Accepted replay parity schema drift")
    output["mechanics"]={label:verify_mechanics(tables,label,read_csv(results/("paired_"+label+"_mechanics.csv.gz")),
        read_csv(results/(label+"_mechanism_groups.csv")),summary["mechanics" if label=="case" else "control_mechanics"]) for label in ("case","control")}
    output["monthly_rows"]=verify_monthly(tables,read_csv(results/"monthly_case_net.csv"))
    require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False,"Known unattainable support gate bypassed")
    output.update(status="passed",output_hashes_verified=len(summary["output_hashes"]),committed_sources_verified=source_count,
        builder_commit=started["builder_commit"],summary_sha256=sha(results/"summary.json"))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT)
    args=parser.parse_args()
    try:
        output=verify(args.root)
    except (VerificationError,KeyError,TypeError,ValueError,OSError) as error:
        print(json.dumps(dict(status="failed",error=str(error),raw_replay=False,inferential_p_recomputed=False),ensure_ascii=False))
        return 1
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False));return 0


if __name__=="__main__":
    raise SystemExit(main())
