"""Independent V17 SAVED-ledger audit; never raw prices or strategy replay.

V16's stdlib readers, exact UTC clocks, decimal quote gate, episode/serial and
metric arithmetic are reused; its fixed-final-path experiment is NOT reused.
This file independently checks the new failed-launch edge, original entries,
earlier whole exit, weighted20bp accounting, fixed triples and recomputed
serial opportunity deltas. Both original251 cases and462 own controls remain.
Unknown BEFORE/AFTER pairs stay unknown; an earlier known exit cannot fill in
an unknown baseline counterfactual. Recorded-edge firstness is not proof that
no edge was omitted or that a saved SMA/quote corresponds to original prices.

Local dependency closure is exactly this file plus
scripts/verify_hourly_impulse_dual_partial_v16.py. No strategy imports.
https://docs.python.org/3.9/library/importlib.html#importing-a-source-file-directly
https://docs.python.org/3.9/library/decimal.html
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess


_SPEC=importlib.util.spec_from_file_location("_v17_saved_common",Path(__file__).with_name("verify_hourly_impulse_dual_partial_v16.py"))
h=importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h)
VerificationError=h.VerificationError
require,number,eq,boolean,stamp=h.require,h.number,h.eq,h.boolean,h.stamp
indexed,parity,same,mean,bp=h.indexed,h.parity,h.same,h.mean,h.bp
read_json,read_csv,sha,safe_path=h.read_json,h.read_csv,h.sha,h.safe_path
ROOT,ARMS,TABLE_FILES,DELTAS,MINUTE,HOUR=h.ROOT,h.ARMS,h.TABLE_FILES,h.DELTAS,h.MINUTE,h.HOUR
EXPERIMENT_ID="exp-btcusdtp-1h-failed-launch-preholdout-20260906-v17"
BASE_POLICY=dict(h.CANDIDATE_POLICY)
CANDIDATE_POLICY=dict(BASE_POLICY,id="15m_native40_failed_launch",fast_failed_launch_exit=True)
PARENT="experiments/active/exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16/results/candidate"
SCOPE=dict(h.SCOPE,limitation="Saved clocks, logged-first eligibility, colour snapshots, formulas and receipts only; "
    "not raw OHLC/SMA/complete-edge replay, live fills, independent inference or profitability proof.")
FAILED_FIELDS={"failed_launch_enabled","failed_launch_count","failed_launch_profit_threshold","failed_launch_status",
    "failed_launch_trigger_previous_open_time","failed_launch_trigger_previous_available_at","failed_launch_trigger_open_time",
    "failed_launch_trigger_available_at","failed_launch_trigger_previous_side","failed_launch_trigger_side",
    "failed_launch_trigger_open_price","failed_launch_trigger_gross_return","failed_launch_slow_open_time",
    "failed_launch_slow_available_at","failed_launch_slow_side","failed_launch_slow_state"}
MUTABLE_FIELDS=h.ACCOUNTING_COLUMNS|{"exit_time","exit_price","closed","outcome","hold_minutes",
    "max_favourable_r","max_adverse_r","bars_to_first_positive"}


def events(row):
    result=h.parse_json(row["partial_fast_events"]) if isinstance(row["partial_fast_events"],str) else row["partial_fast_events"]
    require(isinstance(result,list),"Fast events must be a JSON array")
    return result


def check_original_entry(old,new):
    """Preserve ALL original features, source contexts and initial state fields."""
    require(old.keys()<=new.keys(),"Candidate lost an original column")
    for field,value in old.items():
        changed=field in MUTABLE_FIELDS or field.startswith(("transition_","partial_fast_"))
        if field.startswith(("transition_initial_","partial_fast_initial_")):
            changed=False
        if not changed:same(value,new[field],field)


def check_candidate_events(row):
    """First eligible logged event chooses >20bp half or <=20bp full, never both."""
    require(FAILED_FIELDS<=(row.keys()),"Missing failed-launch evidence fields")
    require(boolean(row["failed_launch_enabled"]),"Candidate failed-launch switch disabled")
    require(boolean(row["partial_fast_enabled"]),"Original partial branch disabled")
    eq(row["partial_fast_fraction"],.5,"Original profitable half fraction changed")
    eq(row["partial_fast_profit_threshold"],.002,"Original profitable half threshold changed")
    eq(row["failed_launch_profit_threshold"],.002,"Failed-launch threshold changed")
    log=events(row);eq(row["partial_fast_flip_count"],len(log),"Fast edge count differs from log")
    start,end=stamp(row["entry_time"]),stamp(row["exit_time"])
    direction,entry=number(row["direction"]),number(row["entry_price"])
    armed=stamp(row["partial_fast_first_armed_at"],True)
    require(armed is None or start<=armed<=end,"Fast arming outside held path")
    previous=start;half=False;failed=None;seen={}
    for edge in log:
        now=stamp(edge["available_at"])
        require(previous<now<=end and now<start+72*HOUR and now%(5*MINUTE)==0,"Fast edge outside ordered held5m path")
        require(failed is None,"Log continued after whole failed-launch exit")
        previous=now
        p,c=edge["previous_fast"],edge["current_fast"]
        ps,cs=h.valid_source(p,now-5*MINUTE,5),h.valid_source(c,now,5)
        require(direction*ps>0 and direction*cs<0,"Failed-launch is not a true aligned-to-opposite edge")
        require(p["management_segment_id"]==c["management_segment_id"] and p["raw_segment_id"]==c["raw_segment_id"],
            "Fast edge crossed a segment reset")
        for at,source in ((now-5*MINUTE,p),(now,c)):
            if at in seen:parity(seen[at],source)
            seen[at]=source
        require(armed is not None and armed<=now-5*MINUTE,"Edge before causal arming")
        slow_at=now//(15*MINUTE)*(15*MINUTE)
        require(stamp(edge["slow_available_at"])==slow_at,"Slow condition used unfinished/older native bar")
        state=edge["slow_state"]
        require(state in ("aligned","opposite","unknown"),"Invalid slow state")
        if state=="unknown":
            require(edge["slow_reason"]!="valid" and number(edge["slow"]["side"],True) is None,"Unknown slow colour filled")
        else:
            side=h.valid_source(edge["slow"],slow_at,15)
            require(edge["slow_reason"]=="valid" and state==("aligned" if direction*side>0 else "opposite"),"Own slow alignment drift")
            require(edge["slow"]["raw_segment_id"]==c["raw_segment_id"],"Slow carry crossed raw gap")
        price=number(edge["open_price"])
        require(price>0 and direction*(price-number(row["initial_stop"]))>0,"Fast market exit displaced gap stop")
        eq(edge["gross_return"],direction*(price/entry-1),"Logged gross differs from executable quote")
        eq(edge["profit_threshold"],.002,"Fast fee threshold changed")
        qualifies=h.profit_qualified(edge["open_price"],row["entry_price"],row["direction"])
        require(boolean(edge["profit_qualified"])==qualifies,"Decimal fee equality/partition drift")
        action=("already_partial" if half else "slow_unknown" if state=="unknown" else
            "slow_not_aligned" if state!="aligned" else "executed" if qualifies else "failed_launch_exit")
        require(edge["action"]==action,"Skipped first eligible logged edge or wrong >20bp/<=20bp branch")
        if action=="executed":half=True
        if action=="failed_launch_exit":failed=edge
        require(now<end or action=="failed_launch_exit" or row["outcome"] in ("right_censored","data_gap_censored"),
            "Fast decision displaced higher-priority final exit")
    is_failed=failed is not None
    eq(row["failed_launch_count"],int(is_failed),"Failed exit count/log drift")
    require((row["outcome"]=="fast_failed_launch")==is_failed,"Outcome/log whole exit mismatch")
    expected="failed_launch_closed" if is_failed else "prior_exit" if boolean(row["closed"]) else "unknown_source"
    require(row["failed_launch_status"]==expected,"Failed-launch terminal status drift")
    scalar={"trigger_previous_open_time":failed["previous_fast"]["open_time"] if failed else None,
        "trigger_open_time":failed["current_fast"]["open_time"] if failed else None,
        "trigger_available_at":failed["available_at"] if failed else None,
        "trigger_previous_side":failed["previous_fast"]["side"] if failed else None,
        "trigger_side":failed["current_fast"]["side"] if failed else None,
        "trigger_open_price":failed["open_price"] if failed else None,
        "trigger_gross_return":failed["gross_return"] if failed else None,
        "slow_open_time":failed["slow"]["open_time"] if failed else None,
        "slow_available_at":failed["slow_available_at"] if failed else None,
        "slow_side":failed["slow"]["side"] if failed else None}
    for field,value in scalar.items():same(row["failed_launch_"+field],value,field)
    if is_failed:
        require(not half and number(row["partial_fast_fill_count"])==0 and row["partial_fast_status"]=="failed_launch_closed",
            "Failed full exit invented a partial or used partial already held")
        require(row["failed_launch_slow_state"]=="aligned","Failed full lacks slow alignment")
        require(stamp(row["failed_launch_trigger_previous_available_at"])==stamp(failed["previous_fast"]["open_time"])+5*MINUTE,
            "Previous fast close availability drift")
        require(stamp(failed["available_at"])==end,"Failed confirmation and real-open fill differ")
        eq(row["exit_price"],failed["open_price"],"Failed exit not recorded real open")
    else:
        require(stamp(row["failed_launch_trigger_previous_available_at"],True) is None and row["failed_launch_slow_state"]=="unknown",
            "No failed exit but failed trigger retained")
    return failed


def check_failed_trade(old,row,edge):
    """Independent full-fill accounting and earlier-path consistency."""
    require(boolean(row["closed"]),"Failed whole exit cannot be censored")
    start,end,old_end=stamp(row["entry_time"]),stamp(row["exit_time"]),stamp(old["exit_time"])
    require(start<end<start+72*HOUR and end<=old_end and end%(5*MINUTE)==0,"Failed fill outside held clock or later than baseline")
    require(not boolean(old["closed"]) or end<old_end,"Failed exit tied a prior known terminal event")
    for field in ("partial_fast_first_armed_at","transition_first_armed_at"):
        if field in old:same(old[field],row[field],field)
    resets=number(row["partial_fast_reset_count"])
    require(resets==int(resets) and 0<=resets<=number(old["partial_fast_reset_count"]) and
        (resets==0 or row["partial_fast_last_reset_reason"]),"Failed held prefix reset evidence drift")
    eq(row["hold_minutes"],(end-start)/MINUTE,"Failed hold duration drift")
    for field,target in (("partial_fraction",0),("exit_remaining_fraction",1),("realised_partial_gross_return",0),
            ("partial_fast_fill_count",0),("partial_fast_realised_net_return",0)):
        eq(row[field],target,"Failed full position fraction/cost drift: "+field)
    require(stamp(row["partial_exit_time"],True) is None and number(row["partial_exit_price"],True) is None,"Failed full retained a partial fill")
    for field in row:
        if field.startswith(("partial_fast_trigger_","partial_fast_slow_")):
            require(row[field] in (("unknown",) if field.endswith("_state") else (None,"")),"Failed full invented partial trigger")
    direction,entry,price=number(row["direction"]),number(row["entry_price"]),number(row["exit_price"])
    with localcontext() as context:
        context.prec=40
        exact_gross=Decimal(str(row["direction"]))*(Decimal(str(row["exit_price"]))-Decimal(str(row["entry_price"])))/Decimal(str(row["entry_price"]))
        gross=float(exact_gross)
    require(not h.profit_qualified(row["exit_price"],row["entry_price"],row["direction"]),"Profitable quote took failed-launch full branch")
    for field,value in (("gross_return",gross),("net_return",gross-.002),("net_r",(gross-.002)/number(row["risk_pct"])),
            ("marked_gross_return",gross),("marked_net_return",gross-.002)):
        eq(row[field],value,"Full20bp accounting drift: "+field)
    require(number(row["net_return"])<=1e-12,"Failed-launch <=20bp cannot have material positive net")
    if exact_gross==Decimal("0.002"):
        require(number(row["gross_return"])==.002 and number(row["net_return"])==0 and number(row["net_r"])==0 and
            number(row["failed_launch_trigger_gross_return"])==.002 and number(edge["gross_return"])==.002,
            "Exact20bp failed-launch must be exact net zero, not a floating tiny winner")
    for field in ("transition_trigger_previous_open_time","transition_trigger_open_time","transition_trigger_available_at"):
        require(stamp(row[field],True) is None,"Fast failed full pretended to be slow full")
    favourable,adverse=number(row["max_favourable_r"]),number(row["max_adverse_r"])
    require(favourable<=number(old["max_favourable_r"])+1e-12 and adverse>=number(old["max_adverse_r"])-1e-12,
        "Earlier held path uses later MFE/MAE")
    excursion=direction*(price-entry)/(entry*number(row["risk_pct"]))
    require(favourable>=max(0,excursion)-1e-12 and adverse<=min(0,excursion)+1e-12,"MFE/MAE omitted actual exit open")
    before_log,after_log=events(old),events(row)
    prefix=[e for e in before_log if stamp(e["available_at"])<=end]
    require(len(prefix)==len(after_log),"Failed held prefix omitted/added recorded edge")
    for a,z in zip(prefix,after_log):
        require(a.keys()==z.keys(),"Failed edge schema drift")
        for field,value in a.items():
            if field=="action" and stamp(z["available_at"])==end:
                require(value=="insufficient_profit" and z[field]=="failed_launch_exit","Failed event differs from same old quote decision")
            elif isinstance(value,dict):parity(value,z[field])
            else:same(value,z[field],field)


def check_metrics(rows,summary):
    h.check_metrics(rows,summary)


def verify_tables(tables,summary,*,expected_counts=(251,462,154)):
    """Pure15-table audit; CLI always enforces the original251/462/154."""
    n,controls_n,matched_n=expected_counts
    require(n>0 and 0<=matched_n<=n and controls_n==3*matched_n,"Invalid expected original counts")
    require(summary["experiment_id"]==EXPERIMENT_ID and summary["status"]=="diagnostic_only_no_candidate_acceptance","Wrong V17 experiment/status")
    for flag in ("holdout_consumed","audit_prices_loaded","production_eligible","training_eligible"):
        require(summary[flag] is False,"Unsafe result flag: "+flag)
    states,mapping,serial,failed_counts,grouped={},{},{},{},{}
    for arm in ARMS:
        data,info=tables[arm],summary["arms"][arm]
        require(info["policy"]==(BASE_POLICY if arm=="baseline" else CANDIDATE_POLICY),"More than failed-launch switch changed")
        states[arm]={key:indexed(data[key]) for key in TABLE_FILES}
        require(len(data["case_trades"])==n and len(data["control_trades"])==controls_n,"Original population lost")
        current={key:(r["parent_event_id"],stamp(r["decision_time"])) for key,r in states[arm]["control_trades"].items()}
        require(len({at for _,at in current.values()})==controls_n,"Control source time reused")
        counts=Counter(parent for parent,_ in current.values())
        require(len(counts)==matched_n and (not counts or set(counts.values())=={3}) and set(counts)<=states[arm]["case_trades"].keys(),"Fixed triples incomplete/foreign")
        require(not mapping or mapping==current,"Frozen matched controls changed")
        mapping=current
        for population in ("case","control"):
            trades,episodes=states[arm][population+"_trades"],states[arm][population+"_episodes"]
            require(trades.keys()==episodes.keys(),"Episode denominator lost")
            failed_counts[arm+"/"+population]=0
            for key,row in trades.items():
                if population=="control":eq(row["direction"],states[arm]["case_trades"][row["parent_event_id"]]["direction"],"Control direction differs from own case assignment")
                if arm=="baseline":
                    require(not any(k.startswith("failed_launch_") for k in row),"Baseline contains new failed-launch diagnostics")
                    h.check_trade(row,True)
                else:
                    require(key in states["baseline"][population+"_trades"],"Candidate changed original identity")
                    old=states["baseline"][population+"_trades"][key]
                    check_original_entry(old,row)
                    edge=check_candidate_events(row)
                    if edge is None:
                        h.check_trade(row,True);parity(old,row)
                    else:
                        check_failed_trade(old,row,edge);failed_counts[arm+"/"+population]+=1
                h.check_episode(row,episodes[key])
            check_metrics(list(trades.values()),info["metrics" if population=="case" else "control_metrics"])
        pairs=states[arm]["matched"]
        require(pairs.keys()==states[arm]["case_episodes"].keys(),"Matching omitted original unknowns")
        for key,pair in pairs.items():
            case=states[arm]["case_episodes"][key]
            controls=[states[arm]["control_episodes"][cid] for cid,(parent,_) in mapping.items() if parent==key]
            values=[number(r["episode_net_return"],True) for r in controls]
            cm=mean(values) if len(values)==3 and None not in values else None
            net=number(case["episode_net_return"],True)
            excess=net-cm if net is not None and cm is not None else None
            for field,value in (("assigned_controls",len(controls)),("event_net_return",net),("control_mean_return",cm),("excess",excess)):
                eq(pair[field],value,"Matched own-cost arithmetic drift: "+field)
            for field in ("mother_decision_time","fold"):same(pair[field],case[field],field)
        vals=[number(r["excess"],True) for r in pairs.values()]
        known=sum(x is not None for x in vals)
        for field,value in (("paired_events",known),("mother_events",n),("coverage",known/n),("mean_excess_bp",bp(mean(vals)))):
            eq(info["matching"][field],value,"Matching summary denominator drift")
        if "assignment_coverage" in info["matching"]:eq(info["matching"]["assignment_coverage"],matched_n/n,"Assignment support changed")
        serial[arm]=h.serial_values(data["case_episodes"],data["single_pending"])
        selected={r["event_id"] for r in data["single_pending"] if boolean(r["portfolio_selected"])}
        eq(info["serial_selected_mothers"],len(selected),"Serial selection count drift")
        check_metrics([r for r in data["case_trades"] if r["event_id"] in selected],info["single_position"])
    effects={}
    for name,table,column in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),("serial_delta",None,None)):
        rows=indexed(tables[name]);require(rows.keys()==states["baseline"]["case_episodes"].keys(),"Paired all-mother denominator changed")
        vals=[]
        for key,row in rows.items():
            before,after=((serial[a][key] for a in ARMS) if table is None else
                (number(states[a][table][key][column],True) for a in ARMS))
            d=after-before if before is not None and after is not None else None
            for field,value in (("before",before),("after",after),("difference",d)):eq(row[field],value,"Paired effect arithmetic drift: "+name+"/"+field)
            same(row["mother_decision_time"],states["baseline"]["case_episodes"][key]["mother_decision_time"],"mother_decision_time")
            vals.append(d)
        known=[x for x in vals if x is not None]
        derived=dict(total_pairs=n,n=len(known),unknown_pairs=n-len(known),improved=sum(x>1e-12 for x in known),
            worsened=sum(x< -1e-12 for x in known),unchanged=sum(abs(x)<=1e-12 for x in known),mean_bp=bp(mean(known)))
        for field,value in derived.items():eq(summary["effects"][name][field],value,"Effect summary drift: "+name+"/"+field)
        effects[name]=dict(derived,sum_event_bp=bp(math.fsum(known)) if known else None)
    for population in ("case","control"):
        groups=defaultdict(list);transitions=Counter();lost_winners=0;prior_partials=0;new_partials=0;cuts=0;delays=[]
        for key,old in states["baseline"][population+"_trades"].items():
            new=states["candidate"][population+"_trades"][key]
            a,z=number(old["net_return"],True),number(new["net_return"],True)
            d=z-a if a is not None and z is not None else None
            failed=new["outcome"]=="fast_failed_launch"
            transition="flat_or_unknown" if a is None or z is None or a==0 or z==0 else ("win" if a>0 else "loss")+"_to_"+("win" if z>0 else "loss")
            transitions[transition]+=1
            lost_winners+=int(failed and a is not None and a>0)
            old_partial=number(old["partial_fraction"])==.5
            prior_partials+=int(old_partial);new_partials+=int(number(new["partial_fraction"])==.5)
            cuts+=int(failed and old_partial)
            delays.append((stamp(new["exit_time"])-stamp(old["exit_time"]))/MINUTE)
            pair=(a,z,d) if d is not None else (None,None,None)
            for label in ("all","failed_launch" if failed else "unchanged",transition):groups[label].append(pair)
        grouped[population]={}
        for label,values in groups.items():
            ds=[d for a,z,d in values if d is not None]
            grouped[population][label]=dict(n=len(values),known=len(ds),old_mean_net_bp=bp(mean([a for a,z,d in values])),
                new_mean_net_bp=bp(mean([z for a,z,d in values])),mean_delta_bp=bp(mean(ds)),
                sum_delta_event_bp=bp(math.fsum(ds)) if ds else None)
        grouped[population]["missed_baseline_winners"]=lost_winners
        grouped[population]["transitions"]=dict(transitions)
        if population in summary.get("mechanics",{}):
            info=summary["mechanics"][population]
            failed_pairs=groups.get("failed_launch",[])
            expected=dict(total=len(states["baseline"][population+"_trades"]),known=grouped[population]["all"]["known"],
                failed_launch_count=len(failed_pairs),unchanged_paths=len(groups.get("unchanged",[])),
                failed_improved=sum(d is not None and d>1e-12 for a,z,d in failed_pairs),
                failed_hurt=sum(d is not None and d< -1e-12 for a,z,d in failed_pairs),
                failed_unknown_pairs=sum(d is None for a,z,d in failed_pairs),sacrificed_recoveries=lost_winners,
                prior_partial_paths_cut=cuts,baseline_partial_count=prior_partials,candidate_partial_count=new_partials,
                later_exits=sum(d>0 for d in delays),earlier_exits=sum(d<0 for d in delays),same_exit_time=sum(d==0 for d in delays))
            for field,value in expected.items():eq(info[field],value,"Failed mechanics summary drift: "+population+"/"+field)
            require(info["transitions"]==dict(transitions),"Failed mechanics win/loss migration drift")
            saved={r["group"]:r for r in info["groups"]}
            require(len(saved)==len(info["groups"]) and saved.keys()==transitions.keys(),"Missing/duplicate paired groups")
            for label,row in saved.items():
                for field,value in grouped[population][label].items():eq(row[field],value,"Paired mechanism mean/denominator drift")
    eq(summary["known_coverage_ceiling"],matched_n/n,"Original known coverage changed")
    eq(summary["coverage_required"],.9,"Coverage gate weakened")
    if matched_n/n<.9:require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False,"Known unmatched support failure bypassed")
    return dict(status="passed",counts=dict(cases=n,controls=controls_n,matched=matched_n,unmatched=n-matched_n),
        effects=effects,accounting=dict(failed_launch_exits=failed_counts,original_cost_fraction=.002,partial_fraction=.5,serial_recomputed=True),
        groups=grouped,**SCOPE)


def load_tables(results):
    return h.load_tables(results)


def verify_sources(root,results,summary):
    """All actual builder/output pins checked; never a hardcoded file count."""
    started=read_json(results/"started.json");config=read_json(results.parent/"config.json")
    require(started["sources"]==summary["sources"] and started["sources"],"Builder source receipts empty/different")
    commit=started["builder_commit"]
    require(re.fullmatch(r"[a-f0-9]{40}",commit) is not None,"Invalid builder commit")
    ids=[r["path"] for r in started["sources"]]
    required={"yoyo/layers/l3_backtest/hourly_impulse.py","yoyo/evaluation/hourly_impulse_failed_launch_research.py",
        str(results.parent.relative_to(root))+"/config.json",str(results.parent.relative_to(root))+"/PROJECT_PLAN.md"}
    require(len(ids)==len(set(ids)) and required<=set(ids),"Missing/duplicate builder sources")
    for row in started["sources"]:
        safe_path(root,row["path"])
        try:content=subprocess.run(["git","show",commit+":"+row["path"]],cwd=root,check=True,capture_output=True).stdout
        except subprocess.CalledProcessError as error:raise VerificationError("Pinned builder unavailable; cannot skip") from error
        require(hashlib.sha256(content).hexdigest()==row["sha256"],"Original committed source hash changed")
    try:when=subprocess.run(["git","show","-s","--format=%ct",commit],cwd=root,check=True,capture_output=True,text=True).stdout.strip()
    except subprocess.CalledProcessError as error:raise VerificationError("Builder timestamp unavailable") from error
    require(re.fullmatch(r"\d+",when) is not None and int(when)*10**9<=stamp(started["at"]),"Study predates builder")
    actual={str(p.relative_to(results)) for p in results.rglob("*") if p.is_file()}
    require(actual==set(summary["output_hashes"])|{"summary.json"},"Output hash inventory incomplete/extra")
    for path,digest in summary["output_hashes"].items():require(sha(safe_path(results,path))==digest,"Output hash mismatch: "+path)
    require(config["experiment_id"]==EXPERIMENT_ID and config["policies"]==[BASE_POLICY,CANDIDATE_POLICY],"Frozen policy changed")
    require(sha(results.parent/"config.json")==summary["config_sha256"],"Current configuration hash mismatch")
    cfg_id=str(results.parent.relative_to(root))+"/config.json"
    require(next(row["sha256"] for row in started["sources"] if row["path"]==cfg_id)==summary["config_sha256"],"Config differs from committed builder")
    require(config["parent_results"]==PARENT,"Baseline is not V16 candidate")
    for directory,key in ((config["parent_results"],"inputs"),(config["mother_results"],"mother_inputs"),(config["entry_context_results"],"entry_context_inputs")):
        require(config[key]==summary[key],"Frozen saved inputs changed")
        for file,digest in config[key].items():require(sha(safe_path(root,directory+"/"+file))==digest,"Original saved input SHA changed")
    base_path=safe_path(root,config["base_config"])
    require(sha(base_path)==config["base_config_sha256"],"Base config SHA changed")
    base=read_json(base_path)
    require(base["execution"]["cost_fraction"]==.002 and base["execution"]["max_hours"]==72 and base["execution"]["stop_first"] is True,"Economic gates changed")
    require(base["development_folds"]==[[f,a,z] for f,(a,z) in h.FOLDS.items()],"Development fold boundaries changed")
    require(summary["source"]["sha256"]==base["source"]["sha256"] and summary["source"]["holdout_price_rows"]==0 and
        stamp(summary["source"]["phase_price_last_open"])<stamp("2025-01-01T00:00:00Z"),"Source receipt exceeds approved development")
    return dict(builder_commit=commit,committed_sources_verified=len(ids),output_hashes_verified=len(summary["output_hashes"]),
        source_pins=started["sources"],output_hashes=summary["output_hashes"])


def verify_lineage(root,results,tables,summary):
    """V16 six-table anchor, original matching and pre-outcome context receipts."""
    config=read_json(results.parent/"config.json");anchor=read_json(results/"anchor_parity.json")
    for table,file in TABLE_FILES.items():
        old=indexed(read_csv(safe_path(root,PARENT+"/"+file)));new=indexed(tables["baseline"][table])
        require(old.keys()==new.keys(),"V16 baseline identities changed")
        for key,row in old.items():parity(row,new[key])
        eq(anchor[table]["rows"],len(old),"Anchor count drift")
        eq(anchor[table]["columns"],len(next(iter(old.values()))),"Anchor columns drift")
    for population in ("case","control"):
        context=indexed(read_csv(results/(population+"_context.csv.gz")))
        upstream=read_csv(safe_path(root,config["entry_context_results"]+"/direct_k1_stop_"+population+"_context.csv.gz"))
        mothers=read_csv(safe_path(root,config["mother_results"]+"/"+("original_mothers" if population=="case" else "control_mothers")+".csv.gz"))
        for source in (upstream,mothers):
            source=indexed(source);require(source.keys()==context.keys(),"Original contexts/mothers lost")
            for key,row in source.items():parity(row,context[key])
        for arm in ARMS:
            trades=indexed(tables[arm][population+"_trades"])
            for key,row in context.items():parity(row,trades[key])
    saved=indexed(read_csv(results/"assignments.csv"));old=indexed(read_csv(safe_path(root,config["mother_results"]+"/assignments.csv")))
    require(saved.keys()==old.keys()==indexed(tables["baseline"]["case_trades"]).keys(),"Original assignments omitted mothers")
    for key,row in old.items():parity(row,saved[key])
    require({key for key,row in saved.items() if row["match_status"]=="matched"}==
        {row["parent_event_id"] for row in tables["baseline"]["control_trades"]},"Fixed154 support rematched")
    native,fast=read_csv(results/"native_entry_context.csv.gz"),read_csv(results/"fast_entry_context.csv.gz")
    counts=h.verify_contexts(native,fast,tables)
    for population in ("case","control"):
        part=indexed([r for r in fast if r["population"]==population]);trades=indexed(tables["baseline"][population+"_trades"])
        require(part.keys()==trades.keys(),"Baseline fast seed count drift")
        for key,row in part.items():h.check_context(row,trades[key],5,fast=True)
    frozen,started=read_json(results/"context_frozen.json"),read_json(results/"started.json")
    require(stamp(frozen["at"])>=stamp(started["at"]) and frozen["before_outcome_reads"] is True and
        frozen["outcomes_hashed_or_read"] is False and frozen["entry_gates"] is False,"Context pre-outcome receipt drift")
    eq(frozen["rows"],len(native),"Native freeze count drift");eq(frozen["fast_rows"],len(fast),"Fast freeze count drift")
    require(frozen["context_sha256"]==sha(results/"native_entry_context.csv.gz") and frozen["fast_context_sha256"]==sha(results/"fast_entry_context.csv.gz"),"Context freeze SHA mismatch")
    for stated in (summary["native_context"],frozen["counts"],read_csv(results/"native_initial_state_counts.csv")):
        actual={}
        for row in stated:
            key=(row["arm"],row["population"],row["mg_entry_state"]);require(key not in actual,"Duplicate context count")
            actual[key]=number(row["n"])
        require(actual==dict(counts),"Frozen context state denominators differ")
    expected={}
    for arm in ARMS:
        for population in ("case","control"):
            for row in tables[arm][population+"_trades"]:
                for edge in events(row):expected[(arm,population,row["event_id"],stamp(edge["available_at"]))]=edge
    seen=set()
    for row in read_csv(results/"fast_edges.csv.gz"):
        key=(row["arm"],row["population"],row["event_id"],stamp(row["available_at"]))
        require(key in expected and key not in seen,"Unknown/duplicate exported edge");seen.add(key)
        for field,value in expected[key].items():
            if isinstance(value,dict):parity(value,h.parse_json(row[field]))
            elif type(value) is bool:require(boolean(row[field])==value,"Exported edge boolean differs")
            else:same(row[field],value,field)
    require(seen==expected.keys(),"Exported fast edges incomplete")
    return dict(anchor_tables=6,native_context_rows=len(native),fast_context_rows=len(fast),recorded_fast_edges=len(seen),
        context_freeze_is_saved_receipt_not_runtime_trace=True)


def verify_mechanics_exports(results,tables,summary):
    """Audit full paired rows and observed-month means without reusing runner."""
    result={}
    for population in ("case","control"):
        old,new=(indexed(tables[arm][population+"_trades"]) for arm in ARMS)
        saved=indexed(read_csv(results/("failed_launch_"+population+"_mechanics.csv")))
        require(saved.keys()==old.keys()==new.keys(),"Mechanics export denominator drift")
        for key,row in saved.items():
            a,z=old[key],new[key]
            before,after=number(a["net_return"],True),number(z["net_return"],True)
            d=after-before if before is not None and after is not None else None
            if d is None:before,after=None,None
            same(row["mother_decision_time"],a["mother_decision_time"],"mother_decision_time")
            for field,value in (("baseline_net_bp",bp(before)),("candidate_net_bp",bp(after)),("delta_net_bp",bp(d)),
                    ("exit_delay_minutes",(stamp(z["exit_time"])-stamp(a["exit_time"]))/MINUTE)):
                eq(row[field],value,"Mechanics row arithmetic drift")
            for arm,source in (("baseline",a),("candidate",z)):
                for suffix,key2 in (("exit_time","exit_time"),("exit_reason","outcome"),("mfe_r","max_favourable_r"),
                        ("hold_minutes","hold_minutes"),("partial_exit_time","partial_exit_time")):
                    same(row[arm+"_"+suffix],source[key2],suffix)
                require(boolean(row[arm+"_partial_executed"])==(number(source["partial_fraction"])==.5),"Mechanics partial flag drift")
            failed=z["outcome"]=="fast_failed_launch"
            require(boolean(row["failed_launch_executed"])==failed,"Mechanics full-exit flag drift")
            require(boolean(row["sacrificed_recovery"])==(failed and before is not None and before>0 and after<=1e-12),"Sacrificed recovery denominator drift")
            require(boolean(row["prior_partial_path_cut"])==(failed and number(a["partial_fraction"])==.5),"Cut prior partial path drift")
            transition="flat_or_unknown" if before is None or after is None or before==0 or after==0 else ("win" if before>0 else "loss")+"_to_"+("win" if after>0 else "loss")
            require(row["outcome_transition"]==transition,"Mechanics migration drift")
        groups=read_csv(results/("failed_launch_"+population+"_groups.csv"))
        by_name={r["group"]:r for r in groups}
        actual={r["group"]:r for r in summary["mechanics"][population]["groups"]}
        require(len(by_name)==len(groups) and by_name.keys()==actual.keys(),"Mechanics group export lost rows")
        for key,row in actual.items():parity(row,by_name[key])
        result[population+"_mechanics_rows"]=len(saved)
    expected=defaultdict(list)
    for arm in ARMS:
        for row in tables[arm]["case_episodes"]:
            month=(h.EPOCH+timedelta(seconds=stamp(row["mother_decision_time"])//10**9)).strftime("%Y-%m")
            expected[(arm,row["fold"],month)].append(number(row["episode_net_return"],True))
    # datetime is stdlib; do not import the research pandas grouping helper.
    rows=read_csv(results/"monthly_case_net.csv");actual={}
    for row in rows:
        key=(row["arm"],row["fold"],row["month"]);require(key not in actual,"Duplicate observed month")
        actual[key]=row
    require(actual.keys()==expected.keys(),"Observed month population changed")
    for key,values in expected.items():
        for field,value in (("n",len(values)),("known",sum(v is not None for v in values)),("mean_net_bp",bp(mean(values)))):
            eq(actual[key][field],value,"Observed month denominator/mean drift")
    return dict(result,monthly_rows=len(rows))


def verify(results,summary_path=None,*,root=ROOT):
    results=Path(results).resolve();root=Path(root)
    summary_path=Path(summary_path).resolve() if summary_path else results/"summary.json"
    summary=read_json(summary_path)
    require(summary==read_json(results/"summary.json") and not (results/"failure.json").exists(),"Summary changed or failed attempt")
    receipts=verify_sources(root,results,summary)
    for arm in ARMS:require(read_json(results/arm/"summary.json")==summary["arms"][arm],"Arm/root summary differs")
    tables=load_tables(results);output=verify_tables(tables,summary)
    output["lineage"]=verify_lineage(root,results,tables,summary)
    output["diagnostics"]=verify_mechanics_exports(results,tables,summary)
    output.update(receipts,summary_sha256=sha(summary_path),verifier_sources=[
        dict(path="scripts/"+path.name,sha256=sha(path)) for path in (Path(__file__),Path(h.__file__))])
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,required=True);parser.add_argument("--summary",type=Path);parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    try:
        if args.out:require(not args.out.exists() and not args.out.resolve().is_relative_to(args.results.resolve()),"Use a new receipt outside saved results")
        output=verify(args.results,args.summary)
    except (VerificationError,KeyError,TypeError,ValueError,OSError) as error:output=dict(status="failed",error=str(error),**SCOPE)
    text=json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)+"\n"
    if args.out and output["status"]=="passed":args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(text,encoding="utf-8")
    print(text,end="")
    return 0 if output["status"]=="passed" else 1


if __name__=="__main__":raise SystemExit(main())
