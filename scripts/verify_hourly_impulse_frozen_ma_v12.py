"""Read-only V12 saved-ledger verifier; no raw-price replay or strategy import.

Generic stdlib readers and accounting helpers are reused from the adjacent V11
verifier, without patching it or translating V12 diagnostics into launch fields.
This module independently checks frozen-MA clocks, fixed case/control/serial
denominators, source-schema-aware merged rows, and entry geometry equations.
It cannot establish the truth/completeness of underlying raw CLOSE observations,
OHLC stop ordering, or raw segment labels. Inferential p values are not rerun.

Python 3.9 CSV strings, integer-nanosecond timestamp parsing and git receipts:
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/importlib.html#importing-a-source-file-directly
https://docs.python.org/3.9/library/subprocess.html#subprocess.run
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import importlib.util
import json
import math
from pathlib import Path
import statistics


_SPEC = importlib.util.spec_from_file_location("_v12_saved_common", Path(__file__).with_name("verify_hourly_impulse_launch_v11.py"))
h = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h)
VerificationError = h.VerificationError
require, number, equal_number, boolean, stamp = h.require, h.number, h.equal_number, h.boolean, h.stamp
indexed, parity, safe_path, sha, read_json, read_csv = h.indexed, h.parity, h.safe_path, h.sha, h.read_json, h.read_csv
mean, bp = h.mean, h.bp
ROOT, ARMS, TABLE_FILES, MINUTE, HOUR, FOLDS = h.ROOT, h.ARMS, h.TABLE_FILES, h.MINUTE, h.HOUR, h.FOLDS
EXPERIMENT_ID = "exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12"
EXPERIMENT_PATH = "experiments/active/"+EXPERIMENT_ID
BASE_PATH = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
PARENT_PATH = "experiments/active/exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5/results"
MOTHER_PATH = "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
BINS = ("negative", "zero", "inside", "equal_stop", "beyond_stop")
BOUNDARY_CONTRACT = {"field":"ma", "source":"own_completed_signal_hour", "price":"completed_held_raw5_close",
    "comparison":"strict_opposite_state", "equal_is_exit":False, "entry_state_gate":False,
    "control_boundary":"own_signal_hour_ma_no_transfer", "priority":"source_gap_stop_colour_deadline_before_frozen_ma",
    "launch_deadline":False, "geometry_bins":"negative_zero_inside_equal_stop_beyond_stop"}
FROZEN_FIELDS = {"frozen_ma_enabled", "frozen_ma_boundary", "frozen_ma_available_at", "frozen_ma_entry_distance_atr",
    "frozen_ma_trigger_open_time", "frozen_ma_trigger_available_at", "frozen_ma_trigger_close",
    "frozen_ma_completed_close_count", "frozen_ma_status"}
GEOMETRY_FIELDS = {"population", "event_id", "parent_event_id", "matched_case", "fold", "signal_time", "decision_time",
    "direction", "ma", "signal_close", "signal_atr", "initial_stop", "entry_open", "raw_entry_segment_id",
    "entry_distance_atr", "entry_side", "previous_hour_close_distance_atr", "previous_hour_close_side",
    "initial_R", "entry_distance_r", "geometry_bin"}
REQUIRED_CODE_SOURCES = {
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_colour_context.py",
    "yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py", "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_management_research.py", "yoyo/evaluation/hourly_impulse_diagnostics.py",
    "yoyo/evaluation/hourly_impulse_transition_research.py", "yoyo/evaluation/hourly_impulse_launch_research.py",
    "tests/test_hourly_impulse_launch_deadline.py", "tests/test_hourly_impulse_launch_research.py",
    "yoyo/evaluation/hourly_impulse_frozen_ma_research.py", "tests/test_hourly_impulse_frozen_ma_research.py",
    "tests/test_hourly_impulse_frozen_ma_exit.py"}


def check_colour_clock(row):
    """Saved native5 trueflip fields, not independent MA-colour computation."""
    if row["outcome"] != "transition_colour_exit":
        return
    previous = stamp(row["transition_trigger_previous_open_time"])
    current = stamp(row["transition_trigger_open_time"])
    available = stamp(row["transition_trigger_available_at"])
    require(previous+5*MINUTE == current and current+5*MINUTE == available == stamp(row["exit_time"]),
            "Native5 colour trigger/fill clock drift")
    require(current >= stamp(row["entry_time"]), "Colour exit consumed a pre-entry bar")


def check_frozen(old, row):
    require(FROZEN_FIELDS <= row.keys(), "Missing frozen-MA diagnostic field")
    require(not any(key.startswith("launch_") for key in row), "V11 launch diagnostics leaked into V12")
    require(boolean(row["frozen_ma_enabled"]), "Frozen-MA exit disabled")
    boundary = number(row["frozen_ma_boundary"])
    require(boundary > 0 and boundary == number(row["ma"]), "Boundary differs from own signal MA")
    start, end = stamp(row["entry_time"]), stamp(row["exit_time"], nullable=True)
    require(stamp(row["signal_time"])+HOUR == stamp(row["decision_time"]) == start == stamp(row["frozen_ma_available_at"]),
            "Frozen hourly boundary unavailable at entry")
    direction, entry, atr = (number(row[key]) for key in ("direction", "entry_price", "signal_atr"))
    equal_number(row["frozen_ma_entry_distance_atr"], direction*(entry-boundary)/atr, "Frozen entry distance drift")
    count = number(row["frozen_ma_completed_close_count"])
    require(count == int(count) and count >= 0 and end is not None and count <= (end-start)//(5*MINUTE),
            "Completed-close count consumes unheld future")
    trigger, available = (stamp(row[key], nullable=True) for key in ("frozen_ma_trigger_open_time", "frozen_ma_trigger_available_at"))
    close = number(row["frozen_ma_trigger_close"], nullable=True)
    present = trigger is not None
    require(present == (available is not None) == (close is not None), "Partial frozen trigger evidence")
    if present:
        require(trigger >= start and trigger % (5*MINUTE) == 0 and trigger+5*MINUTE == available <= end,
                "Frozen trigger is not a held completed5m close")
        require(close > 0 and direction*(close-boundary) < 0, "Frozen CLOSE is not strictly wrong-side")
        require(count == (available-start)//(5*MINUTE), "Latched trigger close count mismatch")
    structural = row["outcome"] == "frozen_ma_exit"
    if structural:
        require(boolean(row["closed"]) and boolean(old["closed"]) and present and end == available and end-start >= 5*MINUTE,
                "Frozen exit lacks a complete paired next-open trigger")
        require(end < stamp(old["exit_time"]), "Frozen exit does not strictly precede original exit")
        require(row["frozen_ma_status"] == "structure_exit", "Wrong frozen exit status")
        require(stamp(row.get("transition_trigger_available_at"), nullable=True) != end,
                "Frozen exit displaced higher-priority colour exit")
        require(end < start+72*HOUR, "Frozen exit displaced total deadline")
        require(direction*(number(row["exit_price"])-number(row["initial_stop"])) > 0,
                "Frozen exit displaced a gap stop")
    elif boolean(row["closed"]):
        require(row["frozen_ma_status"] == "prior_exit", "Retained exit status drift")
        parity([old], [row])
        expected = (end-start)//(5*MINUTE)-(1 if row["outcome"] == "hard_stop" else 0)
        require(count == expected, "Retained path omitted or invented complete closes")
        require(not present or end == available, "A latched structure exit was silently postponed")
    else:
        require(row["frozen_ma_status"] == "unknown_source", "Unknown source reported successful exit")
    return structural


def verify_tables(tables, arm_summaries, effects, *, expected_counts=(251, 462, 154)):
    """Pure saved-row entry point; production verify always requires251/462/154."""
    cases_n, controls_n, matched_n = expected_counts
    states, mapping, serial_values, exit_counts = {}, None, {}, {}
    for arm in ARMS:
        t = tables[arm]
        states[arm] = {name:indexed(rows) for name,rows in t.items()}
        require(len(t["case_trades"]) == cases_n and len(t["control_trades"]) == controls_n, "Frozen population count drift")
        current = {key:(row["parent_event_id"],row["decision_time"]) for key,row in states[arm]["control_trades"].items()}
        require(len({stamp(value[1]) for value in current.values()}) == controls_n, "Reused control timestamp")
        counts = Counter(parent for parent,_ in current.values())
        require(len(counts) == matched_n and set(counts.values()) == {3}, "Fixed triples incomplete")
        require(set(counts) <= states[arm]["case_trades"].keys(), "Foreign control parent")
        if mapping is not None:
            require(mapping == current, "Frozen control mapping changed")
        mapping = current
        exit_counts[arm] = {}
        for label in ("case", "control"):
            trades, episodes = states[arm][label+"_trades"], states[arm][label+"_episodes"]
            require(trades.keys() == episodes.keys(), "Episode population drift")
            exits = 0
            for key,row in trades.items():
                h.check_trade(row)
                check_colour_clock(row)
                h.check_episode(row, episodes[key])
                require(stamp(row["signal_time"])+HOUR == stamp(row["entry_time"]), "Own signal hour clock drift")
                require(number(row["ma"]) > 0 and number(row["signal_close"]) > 0, "Invalid own hourly boundary source")
                if arm == "candidate":
                    old = states["baseline"][label+"_trades"].get(key)
                    require(old is not None, "Candidate changed identity")
                    fixed = {column:old[column] for column in ("event_id", "direction", "decision_time", "mother_decision_time",
                        "mother_deadline", "entry_time", "entry_price", "initial_stop", "signal_atr", "risk_pct", "risk_atr",
                        "fold", "ma", "signal_close", "signal_time")}
                    parity([fixed], [row])
                    exits += check_frozen(old, row)
                else:
                    require(not any(key.startswith(("frozen_ma_", "launch_")) for key in row), "Baseline acquired new policy fields")
            exit_counts[arm][label] = exits
            h.check_metrics(list(trades.values()), arm_summaries[arm]["metrics" if label == "case" else "control_metrics"])
        pairs = states[arm]["matched"]
        require(pairs.keys() == states[arm]["case_episodes"].keys(), "Matching denominator shrank")
        for key,pair in pairs.items():
            case = states[arm]["case_episodes"][key]
            controls = [states[arm]["control_episodes"][cid] for cid,(parent,_) in mapping.items() if parent == key]
            values = [number(row["episode_net_return"],nullable=True) for row in controls]
            control_mean = mean(values) if len(values) == 3 and all(value is not None for value in values) else None
            case_net = number(case["episode_net_return"],nullable=True)
            excess = case_net-control_mean if case_net is not None and control_mean is not None else None
            for field,value in (("assigned_controls",len(controls)),("event_net_return",case_net),
                                ("control_mean_return",control_mean),("excess",excess)):
                equal_number(pair[field],value,"Fixed matched arithmetic drift: "+field)
            require(stamp(pair["mother_decision_time"]) == stamp(case["mother_decision_time"]) and pair["fold"] == case["fold"],
                    "Matched cluster clock/fold changed")
        match_summary = arm_summaries[arm]["matching"]
        finite = [number(row["excess"],nullable=True) for row in pairs.values()]
        for field,value in (("paired_events",sum(x is not None for x in finite)),("mother_events",cases_n),
                            ("coverage",sum(x is not None for x in finite)/cases_n),("mean_excess_bp",bp(mean(finite)))):
            equal_number(match_summary[field],value,"Matching summary drift: "+field)
        if "assignment_coverage" in match_summary:
            equal_number(match_summary["assignment_coverage"],matched_n/cases_n,"Assignment coverage drift")
        serial_values[arm] = h.check_serial(t["case_episodes"],t["single_pending"])
        selected = {row["event_id"] for row in t["single_pending"] if boolean(row["portfolio_selected"])}
        equal_number(arm_summaries[arm]["serial_selected_mothers"],len(selected),"Serial selected count drift")
        h.check_metrics([row for row in t["case_trades"] if row["event_id"] in selected],arm_summaries[arm]["single_position"])
    result = {}
    for name,table,column in (("case_delta","case_episodes","episode_net_return"),("excess_delta","matched","excess"),
                              ("serial_delta",None,None)):
        rows = indexed(tables[name])
        require(rows.keys() == states["baseline"]["case_episodes"].keys(),"Paired all-mother denominator drift")
        deltas = []
        for key,row in rows.items():
            before,after = ((serial_values[arm][key] for arm in ARMS) if table is None else
                (number(states[arm][table][key][column],nullable=True) for arm in ARMS))
            difference = after-before if before is not None and after is not None else None
            for field,value in (("before",before),("after",after),("difference",difference)):
                equal_number(row[field],value,"Paired identity/arithmetic drift: "+name)
            require(stamp(row["mother_decision_time"]) == stamp(states["baseline"]["case_episodes"][key]["mother_decision_time"]),
                    "Paired cluster clock drift")
            deltas.append(difference)
        known = [value for value in deltas if value is not None]
        expected = {"total_pairs":cases_n,"n":len(known),"unknown_pairs":cases_n-len(known),
            "improved":sum(value > 1e-12 for value in known),"worsened":sum(value < -1e-12 for value in known),
            "unchanged":sum(abs(value) <= 1e-12 for value in known),"mean_bp":bp(mean(known))}
        for field,value in expected.items():
            equal_number(effects[name][field],value,"Effect denominator/mean drift: "+name+"/"+field)
        result[name] = {**expected,"sum_event_bp":bp(math.fsum(known)) if known else None}
    return {"counts":{"cases":cases_n,"controls":controls_n,"matched":matched_n,"unmatched":cases_n-matched_n},
        "frozen_ma_exits":exit_counts["candidate"],"effects":result,"raw_replay":False,"inferential_p_recomputed":False,
        "limitation":"Saved-ledger consistency only; no raw CLOSE truth/completeness, intrabar replay, or profitability proof."}


def restore_mechanics_row(row, old, new):
    """Restore shared suffixes and unique unsuffixed fields from authoritative schemas."""
    shared, used = old.keys() & new.keys(), {"event_id"}
    for suffix,source in (("before",old),("after",new)):
        actual = {"event_id":row["event_id"]}
        for column in source:
            if column == "event_id":
                continue
            merged = column+"_"+suffix if column in shared else column
            require(merged not in used and merged in row,"Missing or colliding merged source field: "+merged)
            used.add(merged)
            actual[column] = row[merged]
        parity([source],[actual])


def verify_mechanics(tables, population, mechanics_rows, group_rows, summary):
    old,new = (indexed(tables[arm][population+"_trades"]) for arm in ARMS)
    mechanics = indexed(mechanics_rows)
    require(old.keys() == mechanics.keys() == new.keys(),"Mechanics dropped original population")
    transitions, grouped, distributions = Counter(),defaultdict(list),defaultdict(list)
    for key,row in mechanics.items():
        restore_mechanics_row(row,old[key],new[key])
        a,b = (number(source[key]["net_return"],nullable=True) for source in (old,new))
        known = boolean(old[key]["closed"]) and boolean(new[key]["closed"]) and a is not None and b is not None
        delta = b-a if known else None
        equal_number(row["difference"],delta,"Mechanics delta mismatch")
        frozen = new[key]["outcome"] == "frozen_ma_exit"
        require(boolean(row["frozen_exit"]) == frozen and (not frozen or known),"Frozen classification/paired observability drift")
        transition = "unknown" if not known else "includes_flat" if a == 0 or b == 0 else ("win" if a > 0 else "loss")+"_to_"+("win" if b > 0 else "loss")
        group = "unknown" if not known else "frozen_ma_exit" if frozen else "original_exit_retained"
        require(row["win_loss_transition"] == transition and row["mechanism_group"] == group,"Mechanics group drift")
        transitions[transition] += 1
        grouped[group].append((a,b,delta))
        if known:
            for column,value in (("net_return_before",a),("net_return_after",b),("difference",delta)):
                distributions[column].append(value*1e4)
    require(summary["transitions"] == dict(transitions),"Win/loss counts drift")
    equal_number(summary["total"],len(old),"Mechanics denominator drift")
    equal_number(summary["known"],len(distributions["difference"]),"Mechanics known denominator drift")
    equal_number(summary["frozen_ma_exits"],sum(row["outcome"] == "frozen_ma_exit" for row in new.values()),"Frozen count drift")
    actual = {row["group"]:row for row in group_rows}
    stated = {row["group"]:row for row in summary["groups"]}
    require(len(actual) == len(group_rows) and len(stated) == len(summary["groups"]) and actual.keys() == grouped.keys() == stated.keys(),
            "Mechanics groups omitted or duplicated")
    for name,values in grouped.items():
        delta = [d for a,b,d in values if d is not None]
        expected = {"n":len(values),"known":len(delta),"old_mean_net_bp":bp(mean([a for a,b,d in values])),
            "new_mean_net_bp":bp(mean([b for a,b,d in values])),"mean_delta_bp":bp(mean(delta)),
            "sum_delta_event_bp":bp(math.fsum(delta)) if delta else None,
            "wins_before":sum(a is not None and a > 0 for a,b,d in values),"wins_after":sum(b is not None and b > 0 for a,b,d in values)}
        for column,value in expected.items():
            equal_number(actual[name][column],value,"Mechanism aggregate drift")
            equal_number(stated[name][column],value,"Root mechanism aggregate drift")
    for column in ("net_return_before","net_return_after","difference"):
        values,stated = distributions[column],summary["distributions"][column]
        for field,value in (("n",len(values)),("unknown",len(old)-len(values)),("outliers_removed",0),
                             ("sd_bp",statistics.stdev(values) if len(values)>1 else None)):
            equal_number(stated[field],value,"Distribution count/SD drift")
        require(set(stated["quantiles_bp"]) == {str(q) for q in (0.,.05,.25,.5,.75,.95,1.)},"Distribution quantiles omitted")
        for q,value in stated["quantiles_bp"].items():
            equal_number(value,h.quantile(values,float(q)),"Untrimmed quantile drift")
    return {"paired_rows":len(mechanics),"untrimmed_distributions":3}


def verify_monthly(tables, rows):
    groups = {}
    for arm in ARMS:
        for fold,(start,end) in FOLDS.items():
            year,month = map(int,start.split("-")[:2])
            while (year,month) < tuple(map(int,end.split("-")[:2])):
                groups[(arm,fold,"{:04d}-{:02d}".format(year,month))] = []
                year,month = (year+1,1) if month == 12 else (year,month+1)
        for row in tables[arm]["case_episodes"]:
            month = (h.EPOCH+timedelta(seconds=stamp(row["mother_decision_time"])//10**9)).strftime("%Y-%m")
            key = (arm,row["fold"],month)
            require(key in groups,"Monthly row outside development")
            groups[key].append(number(row["episode_net_return"],nullable=True))
    actual = {(row["arm"],row["fold"],row["month"]):row for row in rows}
    require(len(actual) == len(rows) and actual.keys() == groups.keys(),"All48 arm-months, including empty months, required")
    for key,values in groups.items():
        for column,value in (("n",len(values)),("known",sum(x is not None for x in values)),("mean_net_bp",bp(mean(values)))):
            equal_number(actual[key][column],value,"Whole-cohort monthly mean/count drift")
    return {"monthly_rows":len(rows)}


def verify_geometry(tables, contexts, assignments, rows, summary):
    cases = indexed(contexts["case"])
    assigned = indexed(assignments)
    require(cases.keys() == assigned.keys(),"Geometry assignments lost cases")
    matched = {key for key,row in assigned.items() if row["match_status"] == "matched"}
    geometry = {}
    groups = {name:Counter({category:0 for category in BINS}) for name in ("all_cases","matched_cases","unmatched_cases","controls")}
    for row in rows:
        require(set(row) == GEOMETRY_FIELDS,"Unexpected or missing pre-outcome geometry column")
        key = (row["population"],row["event_id"])
        require(key[0] in ("case","control") and key not in geometry,"Duplicate/foreign geometry identity")
        geometry[key] = row
    for label in ("case","control"):
        context,baseline = indexed(contexts[label]),indexed(tables["baseline"][label+"_trades"])
        require(context.keys() == baseline.keys() == {key for population,key in geometry if population == label},"Geometry population drift")
        for key,source in context.items():
            row,trade = geometry[(label,key)],baseline[key]
            fixed = {column:source[column] for column in ("event_id","fold","signal_time","decision_time","direction","ma","signal_close","signal_atr","initial_stop")}
            parity([fixed],[row]); parity([fixed],[trade])
            equal_number(row["entry_open"],trade["entry_price"],"Geometry used other entry price")
            parent = source.get("parent_event_id")
            require(row["parent_event_id"] in ("",None) if label == "case" else row["parent_event_id"] == parent,
                    "Geometry parent linkage drift")
            expected_matched = key in matched if label == "case" else parent in matched
            require(boolean(row["matched_case"]) == expected_matched and (label != "control" or expected_matched),"Geometry matched flag drift")
            direction,entry,ma,close,stop,atr = (number(value) for value in
                (source["direction"],trade["entry_price"],source["ma"],source["signal_close"],source["initial_stop"],source["signal_atr"]))
            require(direction in (-1,1) and min(entry,ma,close,stop,atr) > 0,"Invalid geometry inputs")
            require(stamp(source["signal_time"])+HOUR == stamp(source["decision_time"]),"Geometry unfinished signal hour")
            number(row["raw_entry_segment_id"])  # Its actual source lineage is NOT proven without raw prices.
            risk,entry_distance,close_distance = direction*(entry-stop),direction*(entry-ma),direction*(close-ma)
            require(risk > 0,"Geometry invalid initial R")
            g = entry_distance/risk
            sign = lambda value: 1 if value > 0 else -1 if value < 0 else 0
            for field,value in (("initial_R",risk),("entry_distance_atr",entry_distance/atr),("entry_side",sign(entry_distance)),
                ("previous_hour_close_distance_atr",close_distance/atr),("previous_hour_close_side",sign(close_distance)),("entry_distance_r",g)):
                equal_number(row[field],value,"Geometry equation drift: "+field)
            category = "negative" if g < 0 else "zero" if g == 0 else "inside" if g < 1 else "equal_stop" if g == 1 else "beyond_stop"
            require(row["geometry_bin"] == category,"Fixed exact geometry bin drift")
            destinations = ("all_cases","matched_cases" if expected_matched else "unmatched_cases") if label == "case" else ("controls",)
            for destination in destinations:
                groups[destination][category] += 1
    require(set(summary) == set(groups),"Geometry summary population drift")
    for group,counts in groups.items():
        equal_number(summary[group]["n"],sum(counts.values()),"Geometry summary count drift")
        require(set(summary[group]["geometry_bins"]) == set(BINS),"Geometry bins omitted")
        for category,n in counts.items():
            equal_number(summary[group]["geometry_bins"][category],n,"Geometry bin count drift")
    return {"rows":len(geometry),"raw_segment_lineage_replayed":False,"equations_verified":True}


def verify_config(config, summary):
    require(summary["experiment_id"] == config["experiment_id"] == EXPERIMENT_ID,"Wrong experiment")
    require(summary["status"] == "diagnostic_only_no_candidate_acceptance","Diagnostic promoted to acceptance")
    for key in ("holdout_consumed","production_eligible","training_eligible"):
        require(summary[key] is False and config[key] is False,"Eligibility/holdout drift")
    require(summary["audit_prices_loaded"] is False and config["no_audit_entry_point"] is True,"Audit enabled")
    policy = {"id":"5m_native40","management_minutes":5,"ma_kind":"SMA","ma_length":40,"exit_mode":"transition_colour","confirmations":1}
    expected = [policy,dict(policy,id="5m_native40_frozen_ma",frozen_ma_exit=True)]
    require(json.dumps(config["policies"],sort_keys=True) == json.dumps(expected,sort_keys=True),"Frozen policy drift")
    require(json.dumps(config["boundary_contract"],sort_keys=True) == json.dumps(summary["boundary_contract"],sort_keys=True) ==
            json.dumps(BOUNDARY_CONTRACT,sort_keys=True),"Boundary contract drift")
    require(config["base_config"] == BASE_PATH and config["parent_results"] == PARENT_PATH and config["mother_results"] == MOTHER_PATH,
            "Pinned evidence directory changed")
    require(config["known_support"] == {"cases":251,"controls":462,"matched":154,"coverage_gate_unattainable":True},"Known support drift")
    equal_number(summary["known_coverage_ceiling"],154/251,"Coverage ceiling drift")
    equal_number(summary["coverage_required"],.9,"Coverage gate weakened")
    require(config["inference"] == {"draws":9999,"seed":20260906,"p_limit":.01,
        "joint_required":["case_delta","excess_delta"],"method":"month_cluster"},"Inference specification drift")
    require(config["selection"] == {"minimum_events":80,"minimum_per_fold":12,"positive_folds":4,"minimum_profit_factor":1.1,
        "minimum_active_months":12,"minimum_months_per_fold":3,"matched_coverage":.9},"Development gates changed")
    expected_mothers = {"original_mothers.csv.gz","control_mothers.csv.gz","assignments.csv","assignment_receipt.json"}
    expected_inputs = {"direct_k1_stop_"+label+"_context.csv.gz" for label in ("case","control")}
    expected_inputs |= {"direct_k1_stop__transition_colour_"+file for file in TABLE_FILES.values()} | {"summary.json"}
    require(set(config["mother_inputs"]) == expected_mothers and set(config["inputs"]) == expected_inputs,"Pinned evidence manifest changed")


def verify_boundary_receipt(receipt, summary, started, geometry_receipt, *, expected_counts=(251,462)):
    """Validate declared pre-outcome exact join; do not re-read hourly prices."""
    require({key:value for key,value in receipt.items() if key != "at"} == summary,
            "Boundary source checkpoint/root mismatch")
    require(stamp(started["at"]) <= stamp(receipt["at"]) <= stamp(geometry_receipt["at"]),
            "Boundary source declared clock is outside pre-outcome sequence")
    require(receipt["feature_spec"] == {"minutes":60,"ma_kind":"SMA","ma_length":40,"ma_source":"HL2"} and
            receipt["join"] == "exact_own_signal_time" and receipt["available_at"] == "signal_time+1h == decision_time" and
            receipt["relative_tolerance"] == 1e-12 and receipt["absolute_tolerance"] == 1e-12 and
            receipt["before_any_arm_outcomes"] is True and receipt["saved_values_changed"] is False,
            "Own boundary source verification specification drift")
    require(set(receipt["populations"]) == {"case","control"},"Boundary source population omitted")
    for label,n in zip(("case","control"),expected_counts):
        row = receipt["populations"][label]
        for field in ("n","ma_matched","signal_close_matched"):
            equal_number(row[field],n,"Boundary source match count drift")
        for field in ("ma_max_abs_error","signal_close_max_abs_error"):
            require(number(row[field]) >= 0,"Invalid boundary source error receipt")
    return {"declared_complete_own_hour_join":True,"independent_hourly_recomputation":False}


def verify(root=ROOT, experiment_path=EXPERIMENT_PATH):
    root = Path(root)
    experiment = safe_path(root,experiment_path)
    results = experiment/"results"
    require(not (results/"failure.json").exists(),"Failed attempt is not completed evidence")
    config,summary,started = (read_json(path) for path in (experiment/"config.json",results/"summary.json",results/"started.json"))
    verify_config(config,summary)
    require(sha(experiment/"config.json") == summary["config_sha256"],"Config hash drift")
    base_path = safe_path(root,config["base_config"])
    require(sha(base_path) == config["base_config_sha256"],"Base hash drift")
    base = read_json(base_path)
    require(base["execution"]["cost_fraction"] == .002 and base["execution"]["max_hours"] == 72 and base["execution"]["stop_first"] is True,
            "Base economics drift")
    require(base["development_folds"] == [[fold,start,end] for fold,(start,end) in FOLDS.items()],"Development folds changed")
    require(summary["source"]["sha256"] == base["source"]["sha256"] and summary["source"]["holdout_price_rows"] == 0 and
            stamp(summary["source"]["phase_price_last_open"]) < h.date_stamp("2025-01-01"),"Saved source exceeds development boundary")
    for directory,key in ((config["parent_results"],"inputs"),(config["mother_results"],"mother_inputs")):
        require(config[key] == summary[key] == started[key],"Fixed input receipt drift")
        for name,expected in config[key].items():
            require(sha(safe_path(root,directory+"/"+name)) == expected,"Prior input hash mismatch: "+name)
    h.verify_output_hashes(results,summary["output_hashes"])
    required = REQUIRED_CODE_SOURCES | {experiment_path+"/config.json",experiment_path+"/PROJECT_PLAN.md",config["base_config"]}
    source_count = h.verify_committed_sources(root,started,summary,required)
    source_hashes = {row["path"]:row["sha256"] for row in started["sources"]}
    require(source_hashes[experiment_path+"/config.json"] == summary["config_sha256"] and
            source_hashes[config["base_config"]] == config["base_config_sha256"],"Current config differs from builder's committed config")
    h.verify_commit_time(root,started)
    tables = {arm:{name:read_csv(results/arm/file) for name,file in TABLE_FILES.items()} for arm in ARMS}
    for name in ("case_delta","excess_delta","serial_delta"):
        tables[name] = read_csv(results/(name+".csv"))
    contexts = {label:read_csv(results/(label+"_context.csv.gz")) for label in ("case","control")}
    for arm in ARMS:
        require(read_json(results/arm/"summary.json") == summary["arms"][arm],"Root/arm summary drift")
        for label,context in contexts.items():
            parent_context = read_csv(safe_path(root,PARENT_PATH+"/direct_k1_stop_"+label+"_context.csv.gz"))
            parity(parent_context,context); parity(context,tables[arm][label+"_trades"])
            original = read_csv(safe_path(root,MOTHER_PATH+"/"+("original_mothers" if label == "case" else "control_mothers")+".csv.gz"))
            parity(original,context)
    anchor = read_json(results/"anchor_parity.json")
    for name,file in TABLE_FILES.items():
        old = read_csv(safe_path(root,PARENT_PATH+"/direct_k1_stop__transition_colour_"+file))
        parity(old,tables["baseline"][name])
        equal_number(anchor[name]["rows"],len(old),"Anchor parity receipt row drift")
        equal_number(anchor[name]["columns"],len(old[0]),"Anchor parity receipt column drift")
    assignments = read_csv(results/"assignments.csv")
    parity(read_csv(safe_path(root,MOTHER_PATH+"/assignments.csv")),assignments)
    assigned = indexed(assignments)
    require(assigned.keys() == indexed(contexts["case"]).keys(),"Assignments omitted original cases")
    matched = {key for key,row in assigned.items() if row["match_status"] == "matched"}
    require(matched == {row["parent_event_id"] for row in tables["baseline"]["control_trades"]},"Old154 assignments replaced")
    output = verify_tables(tables,summary["arms"],summary["effects"])
    output["diagnostics"] = {}
    for label in ("case","control"):
        output["diagnostics"][label] = verify_mechanics(tables,label,read_csv(results/("paired_"+label+"_mechanics.csv.gz")),
            read_csv(results/("mechanism_groups.csv" if label == "case" else "control_mechanism_groups.csv")),
            summary["mechanics" if label == "case" else "control_mechanics"])
    output["diagnostics"].update(verify_monthly(tables,read_csv(results/"monthly_case_net.csv")))
    geometry_receipt = read_json(results/"entry_geometry_frozen.json")
    require(geometry_receipt["sha256"] == sha(results/"entry_geometry.csv") and geometry_receipt["before_any_arm_outcomes"] is True and
            geometry_receipt["used_for_selection"] is False and stamp(geometry_receipt["at"]) >= stamp(started["at"]),
            "Geometry not frozen before outcomes by the declared receipt")
    require(geometry_receipt["population"] == summary["entry_geometry"],"Geometry checkpoint/summary drift")
    output["boundary_source_receipt"] = verify_boundary_receipt(read_json(results/"boundary_source_parity.json"),
        summary["boundary_source_parity"],started,geometry_receipt)
    output["geometry"] = verify_geometry(tables,contexts,assignments,read_csv(results/"entry_geometry.csv"),summary["entry_geometry"])
    require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False,"Known failed support gate bypassed")
    output.update(status="passed",output_hashes_verified=len(summary["output_hashes"]),committed_sources_verified=source_count,
        builder_commit=started["builder_commit"],summary_sha256=sha(results/"summary.json"))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT)
    args = parser.parse_args()
    try:
        output = verify(args.root)
    except (VerificationError,KeyError,TypeError,ValueError,OSError) as error:
        print(json.dumps({"status":"failed","error":str(error),"raw_replay":False,"inferential_p_recomputed":False},ensure_ascii=False))
        return 1
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
