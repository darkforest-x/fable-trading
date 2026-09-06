"""Independent V15 saved-ledger verification; no prices or strategy imports.

Native5/15 completed-bar clocks, all original opportunities, fixed controls and
serial occupancy are checked from saved rows. This is not a second raw-price
replay: neither SMA truth nor absence of an earlier colour edge is established.
Native15 changes OHLC aggregation and SMA40 memory, not only check frequency.

Stdlib CSV readers preserve strings; clocks use exact integer nanoseconds:
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/importlib.html#importing-a-source-file-directly
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
import importlib.util
import json
import math
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location("_v15_saved_common", Path(__file__).with_name("verify_hourly_impulse_frozen_ma_v12.py"))
h = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h)
b = h.h
VerificationError = h.VerificationError
require, number, equal_number, boolean, stamp = h.require, h.number, h.equal_number, h.boolean, h.stamp
indexed, parity, safe_path, sha, read_json, read_csv = h.indexed, h.parity, h.safe_path, h.sha, h.read_json, h.read_csv
mean, bp = h.mean, h.bp
ROOT, ARMS, TABLE_FILES, MINUTE, HOUR, FOLDS = h.ROOT, h.ARMS, h.TABLE_FILES, h.MINUTE, h.HOUR, h.FOLDS
EXPERIMENT_ID = "exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"
EXPERIMENT_PATH = "experiments/active/"+EXPERIMENT_ID
BASE_PATH, PARENT_PATH, MOTHER_PATH = h.BASE_PATH, h.PARENT_PATH, h.MOTHER_PATH
POLICIES = [{"id": str(minutes)+"m_native40", "management_minutes": minutes, "ma_kind": "SMA",
             "ma_length": 40, "exit_mode": "transition_colour", "confirmations": 1} for minutes in (5, 15)]
NATIVE_CONTRACT = {"context_freeze_before_outcomes":True,"raw_stop_minutes":5,"direct_wait_hours":0,
    "sma_memory_minutes":[200,600],"entry_gates":False,"v1_state15_semantic_diagnostic":True,"selection_uses_diagnostic":False}
REQUIRED_CODE_SOURCES = (h.REQUIRED_CODE_SOURCES - {
    "yoyo/evaluation/hourly_impulse_frozen_ma_research.py", "tests/test_hourly_impulse_frozen_ma_research.py",
    "tests/test_hourly_impulse_frozen_ma_exit.py"}) | {
    "yoyo/data/hourly_impulse_management_context.py", "yoyo/data/hourly_impulse_native_exit_context.py",
    "yoyo/evaluation/hourly_impulse_native_exit_research.py", "tests/test_hourly_impulse_native_exit_context.py",
    "tests/test_hourly_impulse_native_exit_research.py", "tests/test_hourly_impulse_transition_15m.py"}
MG_FIELDS = {"mg_entry_side", "mg_entry_aligned", "mg_entry_state", "mg_entry_bar_open", "mg_entry_available_at",
    "mg_entry_reason", "mg_entry_known", "mg_entry_ma", "mg_entry_hl2", "mg_entry_management_segment_id",
    "mg_entry_raw_segment_id", "mg_entry_native_minutes"}
FIXED_FIELDS = ("event_id", "direction", "decision_time", "mother_decision_time", "mother_deadline", "entry_time",
    "entry_price", "initial_stop", "signal_atr", "risk_pct", "risk_atr", "fold", "ma", "signal_close", "signal_time")


def check_paired_path(old, new):
    """A longer held path cannot survive the other arm's unchanged hard stop."""
    if not boolean(old["closed"]) or not boolean(new["closed"]):
        return
    a, z = stamp(old["exit_time"]), stamp(new["exit_time"])
    for stop_arm, other in ((old,new),(new,old)):
        if stop_arm["outcome"] in ("hard_stop","hard_stop_gap"):
            require(stamp(other["exit_time"]) <= stamp(stop_arm["exit_time"]),
                    "Paired held path survived unchanged K1 hard stop")
    open_exits = {"hard_stop_gap","time_exit","transition_colour_exit"}
    if a == z and old["outcome"] in open_exits and new["outcome"] in open_exits:
        equal_number(old["exit_price"],new["exit_price"],"Same real open acquired different fill")
    if a != z and all("max_favourable_r" in row for row in (old,new)):
        shorter,longer = (old,new) if a < z else (new,old)
        small,large = (number(row["max_favourable_r"],nullable=True) for row in (shorter,longer))
        require(small is None or large is None or large >= small-1e-12,"Longer held path lost prior MFE")


def check_native_clock(row, minutes):
    """Check recorded native edge, not raw colour computation or firstness."""
    require(minutes in (5, 15), "Unsupported native clock")
    start = stamp(row["entry_time"])
    end = stamp(row["exit_time"], nullable=True)
    outcome = row["outcome"]
    closed = boolean(row["closed"])
    allowed = {"hard_stop", "hard_stop_gap", "transition_colour_exit", "time_exit"}
    require(not closed or outcome in allowed, "Unregistered closed exit mode")
    require(not any(key.startswith(("launch_", "frozen_ma_", "transition_decision_")) for key in row),
            "Different exit mechanism leaked into native comparison")
    initial = row["transition_initial_state"]
    require(initial in ("aligned", "opposite", "unknown"), "Invalid initial state")
    initial_time = stamp(row["transition_initial_open_time"], nullable=True)
    if initial != "unknown":
        require(initial_time == start-minutes*MINUTE, "Native seed is not latest completed bar")
    else:
        require(initial_time is None, "Unknown L3 seed invented a known bar")
    names = ("transition_trigger_previous_open_time", "transition_trigger_open_time", "transition_trigger_available_at")
    times = [stamp(row.get(name), nullable=True) for name in names]
    if outcome == "transition_colour_exit":
        previous, current, available = times
        require(closed and all(value is not None for value in times), "Colour exit missing edge evidence")
        require(previous+minutes*MINUTE == current and current+minutes*MINUTE == available == end,
                "Native colour edge/fill clock drift")
        require(current >= start and end >= start+minutes*MINUTE and end % (minutes*MINUTE) == 0,
                "Native colour exit used pre-entry or incomplete management bar")
        require(number(row["direction"])*(number(row["exit_price"])-number(row["initial_stop"])) > 0,
                "Colour exit displaced gap stop")
    else:
        require(all(value is None for value in times), "Non-colour exit retains a fabricated trigger")
    if outcome == "time_exit":
        require(end == start+72*HOUR, "Time exit changed original horizon")


def verify_tables(tables, arm_summaries, effects, *, expected_counts=(251, 462, 154)):
    """Pure rows API; fixed opportunities, paired arithmetic and actual serial clock."""
    cases_n, controls_n, matched_n = expected_counts
    states, mapping, serial_values = {}, None, {}
    for arm, minutes in zip(ARMS, (5, 15)):
        t = tables[arm]
        states[arm] = {name:indexed(rows) for name,rows in t.items()}
        require(len(t["case_trades"]) == cases_n and len(t["control_trades"]) == controls_n, "Original population count drift")
        current = {key:(row["parent_event_id"],stamp(row["decision_time"])) for key,row in states[arm]["control_trades"].items()}
        require(len({value[1] for value in current.values()}) == controls_n, "Control time reused")
        counts = Counter(parent for parent,_ in current.values())
        require(len(counts) == matched_n and set(counts.values()) == {3}, "Original fixed triples incomplete")
        require(set(counts) <= states[arm]["case_trades"].keys(), "Foreign control parent")
        require(mapping is None or mapping == current, "Fixed control assignments changed")
        mapping = current
        for label in ("case", "control"):
            trades, episodes = states[arm][label+"_trades"], states[arm][label+"_episodes"]
            require(trades.keys() == episodes.keys(), "Episode denominator drift")
            for key,row in trades.items():
                b.check_trade(row)
                check_native_clock(row, minutes)
                b.check_episode(row, episodes[key])
                require(stamp(row["signal_time"])+HOUR == stamp(row["entry_time"]), "K1 not completed at entry")
                if arm == "candidate":
                    old = states["baseline"][label+"_trades"].get(key)
                    require(old is not None, "Candidate changed original identity")
                    parity([{column:old[column] for column in FIXED_FIELDS}], [row])
                    check_paired_path(old,row)
            b.check_metrics(list(trades.values()), arm_summaries[arm]["metrics" if label == "case" else "control_metrics"])
        pairs = states[arm]["matched"]
        require(pairs.keys() == states[arm]["case_episodes"].keys(), "Matching lost unmatched opportunities")
        for key,pair in pairs.items():
            case = states[arm]["case_episodes"][key]
            controls = [states[arm]["control_episodes"][cid] for cid,(parent,_) in mapping.items() if parent == key]
            values = [number(row["episode_net_return"], nullable=True) for row in controls]
            cm = mean(values) if len(values) == 3 and all(value is not None for value in values) else None
            net = number(case["episode_net_return"], nullable=True)
            excess = net-cm if net is not None and cm is not None else None
            for field,value in (("assigned_controls",len(controls)), ("event_net_return",net),
                                ("control_mean_return",cm), ("excess",excess)):
                equal_number(pair[field],value,"Fixed matched arithmetic drift: "+field)
            require(stamp(pair["mother_decision_time"]) == stamp(case["mother_decision_time"]) and pair["fold"] == case["fold"],
                    "Matching time/fold drift")
        ms = arm_summaries[arm]["matching"]
        values = [number(row["excess"], nullable=True) for row in pairs.values()]
        for field,value in (("paired_events",sum(x is not None for x in values)), ("mother_events",cases_n),
                            ("coverage",sum(x is not None for x in values)/cases_n), ("mean_excess_bp",bp(mean(values)))):
            equal_number(ms[field],value,"Matching summary drift: "+field)
        if "assignment_coverage" in ms:
            equal_number(ms["assignment_coverage"],matched_n/cases_n,"Assigned support drift")
        serial_values[arm] = b.check_serial(t["case_episodes"],t["single_pending"])
        selected = {row["event_id"] for row in t["single_pending"] if boolean(row["portfolio_selected"])}
        equal_number(arm_summaries[arm]["serial_selected_mothers"],len(selected),"Serial selected count drift")
        b.check_metrics([row for row in t["case_trades"] if row["event_id"] in selected], arm_summaries[arm]["single_position"])
    result = {}
    for name, table, column in (("case_delta","case_episodes","episode_net_return"),
            ("excess_delta","matched","excess"), ("serial_delta",None,None)):
        rows = indexed(tables[name])
        require(rows.keys() == states["baseline"]["case_episodes"].keys(),"All-mother effect denominator drift")
        deltas = []
        for key,row in rows.items():
            a,z = ((serial_values[arm][key] for arm in ARMS) if table is None else
                   (number(states[arm][table][key][column],nullable=True) for arm in ARMS))
            delta = z-a if a is not None and z is not None else None
            for field,value in (("before",a),("after",z),("difference",delta)):
                equal_number(row[field],value,"Effect row drift: "+name+"/"+field)
            require(stamp(row["mother_decision_time"]) == stamp(states["baseline"]["case_episodes"][key]["mother_decision_time"]),
                    "Effect month clock drift")
            deltas.append(delta)
        known = [x for x in deltas if x is not None]
        values = {"total_pairs":cases_n,"n":len(known),"unknown_pairs":cases_n-len(known),
            "improved":sum(x > 1e-12 for x in known),"worsened":sum(x < -1e-12 for x in known),
            "unchanged":sum(abs(x) <= 1e-12 for x in known),"mean_bp":bp(mean(known))}
        for field,value in values.items(): equal_number(effects[name][field],value,"Effect summary drift: "+name+"/"+field)
        result[name] = {**values,"sum_event_bp":bp(math.fsum(known)) if known else None}
    return {"counts":{"cases":cases_n,"controls":controls_n,"matched":matched_n,"unmatched":cases_n-matched_n},
        "effects":result,"raw_replay":False,"inferential_p_recomputed":False}


def verify_native_context(rows, original_contexts, tables):
    """Verify saved own native seed evidence; unknown diagnostic values are not known."""
    groups = {(arm,label):[] for arm in ARMS for label in ("case","control")}
    for row in rows:
        key = (row["arm"],row["population"])
        require(key in groups, "Unexpected native context arm/population")
        groups[key].append(row)
    counts = {}
    for (arm,label), part in groups.items():
        contexts, originals = indexed(part), indexed(original_contexts[label])
        trades = indexed(tables[arm][label+"_trades"])
        require(contexts.keys() == originals.keys() == trades.keys(), "Native context identity loss")
        minutes = 5 if arm == "baseline" else 15
        states = Counter()
        for key,row in contexts.items():
            require(MG_FIELDS <= row.keys(), "Missing native context field")
            original,trade = originals[key],trades[key]
            parity([{field:original[field] for field in ("event_id","decision_time","direction")}],[row])
            require(MG_FIELDS <= trade.keys(),"Executed native seed source fields lost")
            parity([{field:row[field] for field in MG_FIELDS | {"event_id"}}],[trade])
            equal_number(row["mg_entry_native_minutes"],minutes,"Native management interval drift")
            state = row["mg_entry_state"]
            known = boolean(row["mg_entry_known"])
            require(state in ("aligned","opposite","unknown") and known == (state != "unknown"),"Unknown native seed coerced known")
            require(state == trade["transition_initial_state"], "Independent native seed differs from executor")
            side = number(row["mg_entry_side"],nullable=True)
            start = stamp(original["decision_time"])
            if known:
                require(row["mg_entry_reason"] == "valid" and side in (-1,1), "Known seed lacks valid colour")
                ma,hl2 = number(row["mg_entry_ma"]),number(row["mg_entry_hl2"])
                require(ma > 0 and hl2 > 0 and side == (1 if hl2 >= ma else -1),"Native colour does not match saved HL2/MA")
                aligned = number(original["direction"])*side == 1
                require(boolean(row["mg_entry_aligned"]) == aligned and state == ("aligned" if aligned else "opposite"),
                        "Own direction/colour alignment drift")
                require(stamp(row["mg_entry_available_at"]) == start and stamp(row["mg_entry_bar_open"])+minutes*MINUTE == start,
                        "Native seed is not latest fully completed own bar")
                require(stamp(trade["transition_initial_open_time"]) == stamp(row["mg_entry_bar_open"]),"L3 seed clock drift")
                for field in ("mg_entry_management_segment_id","mg_entry_raw_segment_id"):
                    require(row[field] is not None and str(row[field]).strip() != "", "Known seed lacks source continuity evidence")
                if "transition_initial_side" in trade: equal_number(trade["transition_initial_side"],side,"L3 seed side drift")
            else:
                require(side is None and row["mg_entry_aligned"] in (None,""), "Unknown colour/alignment filled")
                require(row["mg_entry_reason"] != "valid", "Unknown context claims valid source")
                require(stamp(trade["transition_initial_open_time"],nullable=True) is None,"Unknown L3 seed filled")
            if "transition_initial_reason" in trade:
                require(trade["transition_initial_reason"] == row["mg_entry_reason"],"Native initialization reason drift")
            states[state] += 1
        counts[arm+"/"+label] = {"n":len(part), **{state:states[state] for state in ("aligned","opposite","unknown")}}
    return counts


def verify_mechanics(rows, tables, *, population="case", summary=None, group_rows=None):
    old,new = (indexed(tables[arm][population+"_trades"]) for arm in ARMS)
    mechanics = indexed(rows)
    require(old.keys() == new.keys() == mechanics.keys(),"Mechanics denominator drift")
    counts,transitions,groups = Counter(),Counter(),{}
    for key,row in mechanics.items():
        a,z = old[key],new[key]
        before,after = (number(x["net_return"],nullable=True) for x in (a,z))
        known = boolean(a["closed"]) and boolean(z["closed"]) and before is not None and after is not None
        if not known: before,after = None,None
        delta = after-before if before is not None and after is not None else None
        ae,ze = (stamp(x["exit_time"],nullable=True) for x in (a,z))
        delay = (ze-ae)/MINUTE if ae is not None and ze is not None else None
        require(stamp(row["mother_decision_time"]) == stamp(a["mother_decision_time"]),"Mechanics decision drift")
        for field,value in (("baseline_net_bp",bp(before)),("candidate_net_bp",bp(after)),("delta_net_bp",bp(delta)),
                ("exit_delay_minutes",delay)):
            equal_number(row[field],value,"Native mechanics arithmetic drift: "+field)
        for prefix,source in (("baseline",a),("candidate",z)):
            require(stamp(row[prefix+"_exit_time"],nullable=True) == stamp(source["exit_time"],nullable=True),"Mechanics exit clock drift")
            require(row[prefix+"_exit_reason"] == source["outcome"],"Mechanics outcome drift")
            equal_number(row[prefix+"_hold_minutes"],source["hold_minutes"],"Mechanics holding drift")
            equal_number(row[prefix+"_mfe_r"],source.get("max_favourable_r"),"Mechanics held-path MFE drift")
        transition = "flat_or_unknown" if before is None or after is None or before == 0 or after == 0 else \
            ("win" if before > 0 else "loss")+"_to_"+("win" if after > 0 else "loss")
        require(row["outcome_transition"] == transition,"Native win/loss transition drift")
        transitions[transition] += 1
        groups.setdefault(transition,[]).append((before,after,delta))
        counts["known"] += known
        counts["unknown_exit_clock" if delay is None else "earlier" if delay < 0 else "later" if delay > 0 else "same_time"] += 1
        counts["stop_after"] += z["outcome"] in ("hard_stop","hard_stop_gap")
        counts["stop_before"] += a["outcome"] in ("hard_stop","hard_stop_gap")
    computed_groups = {}
    for name,values in groups.items():
        known = [delta for _,_,delta in values if delta is not None]
        computed_groups[name] = {"group":name,"n":len(values),"known":len(known),
            "old_mean_net_bp":bp(mean([x for x,_,_ in values])),"new_mean_net_bp":bp(mean([x for _,x,_ in values])),
            "mean_delta_bp":bp(mean(known)),"sum_delta_event_bp":bp(math.fsum(known)) if known else None}
    for rows_to_check in ([group_rows] if group_rows is not None else []) + ([summary["groups"]] if summary is not None else []):
        lookup = {row["group"]:row for row in rows_to_check}
        require(len(lookup) == len(rows_to_check) and lookup.keys() == computed_groups.keys(),"Mechanism groups dropped/duplicated")
        for name,expected in computed_groups.items():
            for field,value in expected.items():
                if field != "group": equal_number(lookup[name][field],value,"Mechanism group arithmetic drift: "+field)
    if summary is not None:
        require(summary["transitions"] == dict(transitions),"Transition summary drift")
        for field,value in (("total",len(rows)),("known",counts["known"]),("later_exits",counts["later"]),
                            ("earlier_exits",counts["earlier"]),("same_exit_time",counts["same_time"])):
            equal_number(summary[field],value,"Mechanics summary drift: "+field)
    return {"n":len(rows),**dict(counts)}


def verify_config(config, summary):
    require(config["experiment_id"] == summary["experiment_id"] == EXPERIMENT_ID,"Wrong native-exit experiment")
    require(summary["status"] == "diagnostic_only_no_candidate_acceptance","Unexpected result status")
    for field in ("holdout_consumed","production_eligible","training_eligible"):
        require(config[field] is False and summary[field] is False,"Eligibility/holdout drift")
    require(summary["audit_prices_loaded"] is False and config["no_audit_entry_point"] is True,"Audit enabled")
    require(json.dumps(config["policies"],sort_keys=True) == json.dumps(POLICIES,sort_keys=True),"Native policy specification drift")
    for arm,policy in zip(ARMS,POLICIES):
        require(json.dumps(summary["arms"][arm]["policy"],sort_keys=True) == json.dumps(policy,sort_keys=True),"Arm policy differs from frozen native specification")
    require(json.dumps(config["native_contract"],sort_keys=True) == json.dumps(NATIVE_CONTRACT,sort_keys=True),"Native contract drift")
    require(config["base_config"] == BASE_PATH and config["parent_results"] == PARENT_PATH and config["mother_results"] == MOTHER_PATH,
            "Fixed evidence directory drift")
    require(config["known_support"] == {"cases":251,"controls":462,"matched":154,"coverage_gate_unattainable":True},"Known support drift")
    equal_number(summary["known_coverage_ceiling"],154/251,"Coverage ceiling drift")
    equal_number(summary["coverage_required"],.9,"Coverage gate weakened")
    require(config["inference"] == {"draws":9999,"seed":20260906,"p_limit":.01,
        "joint_required":["case_delta","excess_delta"],"method":"month_cluster"},"Frozen inference drift")
    require(config["selection"] == {"minimum_events":80,"minimum_per_fold":12,"positive_folds":4,"minimum_profit_factor":1.1,
        "minimum_active_months":12,"minimum_months_per_fold":3,"matched_coverage":.9},"Selection gates changed")
    expected = {"direct_k1_stop_"+label+"_context.csv.gz" for label in ("case","control")}
    expected |= {"direct_k1_stop__transition_colour_"+file for file in TABLE_FILES.values()} | {"summary.json"}
    require(set(config["inputs"]) == expected and set(config["mother_inputs"]) == {
        "original_mothers.csv.gz","control_mothers.csv.gz","assignments.csv","assignment_receipt.json"},"Frozen input manifest drift")


def verify_context_receipt(rows, count_rows, receipt, summary_counts, started, context_hash):
    expected = Counter((row["arm"],row["population"],row["mg_entry_state"]) for row in rows)
    def counts(records):
        result = {}
        for row in records:
            key = (row["arm"],row["population"],row["mg_entry_state"])
            require(key not in result,"Duplicate native state summary")
            n = number(row["n"])
            require(n > 0 and n == int(n),"Native state summary invalid count")
            result[key] = int(n)
        return result
    require(counts(count_rows) == counts(receipt["counts"]) == counts(summary_counts) == dict(expected),"Native state counts drift")
    equal_number(receipt["rows"],len(rows),"Native context checkpoint denominator drift")
    require(receipt["context_sha256"] == context_hash,"Frozen native context hash drift")
    require(receipt["before_outcome_reads"] is True and receipt["outcomes_hashed_or_read"] is False and receipt["entry_gates"] is False,
            "Native context not declared frozen before outcomes without selection")
    require(stamp(receipt["at"]) >= stamp(started["at"]),"Native checkpoint predates builder start")
    return {"rows":len(rows),"state_groups":len(expected),"declared_pre_outcome_freeze":True,"independent_price_recomputation":False}


def verify_monthly(tables, rows):
    expected = {}
    for arm in ARMS:
        for row in tables[arm]["case_episodes"]:
            month = (b.EPOCH+timedelta(seconds=stamp(row["mother_decision_time"])//10**9)).strftime("%Y-%m")
            key = (arm,row["fold"],month)
            expected.setdefault(key,[]).append(number(row["episode_net_return"],nullable=True))
    actual = {}
    for row in rows:
        key = (row["arm"],row["fold"],row["month"])
        require(key not in actual,"Duplicate month")
        actual[key] = row
    require(actual.keys() == expected.keys(),"Observed month population drift")
    for key,values in expected.items():
        for field,value in (("n",len(values)),("known",sum(x is not None for x in values)),("mean_net_bp",bp(mean(values)))):
            equal_number(actual[key][field],value,"Monthly mean/denominator drift")
    return {"monthly_rows":len(rows)}


def verify(root=ROOT, experiment_path=EXPERIMENT_PATH):
    root = Path(root); experiment = safe_path(root,experiment_path); results = experiment/"results"
    require(not (results/"failure.json").exists(),"Failed attempt is not completed evidence")
    config,summary,started = (read_json(path) for path in (experiment/"config.json",results/"summary.json",results/"started.json"))
    verify_config(config,summary)
    require(sha(experiment/"config.json") == summary["config_sha256"],"Config hash drift")
    base_path = safe_path(root,config["base_config"])
    require(sha(base_path) == config["base_config_sha256"],"Base hash drift")
    base = read_json(base_path)
    require(base["execution"]["cost_fraction"] == .002 and base["execution"]["max_hours"] == 72 and base["execution"]["stop_first"] is True,
            "Fixed20bp/72h/stop-first economics changed")
    require(base["development_folds"] == [[fold,start,end] for fold,(start,end) in FOLDS.items()],"Development boundary drift")
    require(summary["source"]["sha256"] == base["source"]["sha256"] and summary["source"]["holdout_price_rows"] == 0 and
            stamp(summary["source"]["phase_price_last_open"]) < b.date_stamp("2025-01-01"),"Saved source exceeds allowed development")
    for directory,key in ((config["parent_results"],"inputs"),(config["mother_results"],"mother_inputs")):
        require(config[key] == summary[key],"Fixed input receipt drift")
        for name,expected in config[key].items():
            require(sha(safe_path(root,directory+"/"+name)) == expected,"Prior input hash mismatch: "+name)
    b.verify_output_hashes(results,summary["output_hashes"])
    required = REQUIRED_CODE_SOURCES | {experiment_path+"/config.json",experiment_path+"/PROJECT_PLAN.md",config["base_config"]}
    source_count = b.verify_committed_sources(root,started,summary,required)
    source_hashes = {row["path"]:row["sha256"] for row in started["sources"]}
    require(source_hashes[experiment_path+"/config.json"] == summary["config_sha256"] and
            source_hashes[config["base_config"]] == config["base_config_sha256"],"Current configuration differs from committed builder")
    b.verify_commit_time(root,started)
    tables = {arm:{name:read_csv(results/arm/file) for name,file in TABLE_FILES.items()} for arm in ARMS}
    for name in ("case_delta","excess_delta","serial_delta"): tables[name] = read_csv(results/(name+".csv"))
    contexts = {label:read_csv(results/(label+"_context.csv.gz")) for label in ("case","control")}
    for arm in ARMS:
        require(read_json(results/arm/"summary.json") == summary["arms"][arm],"Root and arm summaries differ")
        for label,context in contexts.items():
            parent_context = read_csv(safe_path(root,PARENT_PATH+"/direct_k1_stop_"+label+"_context.csv.gz"))
            parity(parent_context,context); parity(context,tables[arm][label+"_trades"])
            original = read_csv(safe_path(root,MOTHER_PATH+"/"+("original_mothers" if label == "case" else "control_mothers")+".csv.gz"))
            parity(original,context)
    anchor = read_json(results/"anchor_parity.json")
    for name,file in TABLE_FILES.items():
        old = read_csv(safe_path(root,PARENT_PATH+"/direct_k1_stop__transition_colour_"+file))
        parity(old,tables["baseline"][name])
        equal_number(anchor[name]["rows"],len(old),"Anchor receipt row drift")
        equal_number(anchor[name]["columns"],len(old[0]),"Anchor receipt column drift")
    assignments = read_csv(results/"assignments.csv")
    parity(read_csv(safe_path(root,MOTHER_PATH+"/assignments.csv")),assignments)
    assigned = indexed(assignments)
    require(assigned.keys() == indexed(contexts["case"]).keys(),"Original assignment cases dropped")
    matched = {key for key,row in assigned.items() if row["match_status"] == "matched"}
    require(matched == {row["parent_event_id"] for row in tables["baseline"]["control_trades"]},"Fixed154 assignments changed")
    output = verify_tables(tables,summary["arms"],summary["effects"])
    native = read_csv(results/"native_entry_context.csv.gz")
    output["native_context"] = verify_native_context(native,contexts,tables)
    output["context_receipt"] = verify_context_receipt(native,read_csv(results/"native_initial_state_counts.csv"),
        read_json(results/"context_frozen.json"),summary["native_context"],started,sha(results/"native_entry_context.csv.gz"))
    output["mechanics"] = verify_mechanics(read_csv(results/"native_exit_mechanics.csv"),tables,
        summary=summary["mechanics"],group_rows=read_csv(results/"mechanism_groups.csv"))
    output.update(verify_monthly(tables,read_csv(results/"monthly_case_net.csv")))
    # The prespecified opposite-state replay remains separate from both main
    # arms and all D/I/serial calculations; check saved economics, not selection.
    output["semantic_diagnostic"] = {}
    for label in ("case","control"):
        state_rows = read_csv(results/("semantic_state15_"+label+"_trades.csv.gz"))
        originals = indexed(tables["candidate"][label+"_trades"])
        require(indexed(state_rows).keys() == originals.keys(),"Semantic diagnostic selected requests")
        for row in state_rows:
            b.check_trade(row)
            parity([{key:originals[row["event_id"]][key] for key in FIXED_FIELDS}],[row])
            if boolean(row["closed"]):
                require(row["outcome"] in ("hard_stop","hard_stop_gap","time_exit","colour_exit"),"Semantic state replay mode drift")
                if row["outcome"] == "colour_exit":
                    require(stamp(row["exit_time"]) % (15*MINUTE) == 0 and stamp(row["exit_time"]) >= stamp(row["entry_time"])+15*MINUTE,
                            "State15 exit clock drift")
            check_paired_path(row,originals[row["event_id"]])
        joined = read_csv(results/("semantic_state15_"+label+"_delta.csv"))
        sem_tables = {"baseline":{label+"_trades":state_rows},"candidate":{label+"_trades":tables["candidate"][label+"_trades"]}}
        output["semantic_diagnostic"][label] = verify_mechanics(joined,sem_tables,population=label,summary=summary["semantics"][label])
        equal_number(summary["semantics"][label]["same_net"],sum(
            value is not None and abs(value) <= 1e-8 for value in [number(row["delta_net_bp"],nullable=True) for row in joined]),
            "State semantic identity count drift")
    require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False,"Known coverage failure bypassed")
    output.update(status="passed",output_hashes_verified=len(summary["output_hashes"]),committed_sources_verified=source_count,
        builder_commit=started["builder_commit"],summary_sha256=sha(results/"summary.json"),
        limitation="Saved clocks, formulas and receipts only; no independent raw OHLC/SMA/first-edge replay, inference rerun or profitability proof.")
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--root",type=Path,default=ROOT)
    args=parser.parse_args()
    try: output=verify(args.root)
    except (VerificationError,KeyError,TypeError,ValueError,OSError) as error:
        print(json.dumps({"status":"failed","error":str(error),"raw_replay":False,"inferential_p_recomputed":False},ensure_ascii=False)); return 1
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
