"""Independent, read-only V11 SAVED-ledger checks; never a price replay.

Only stdlib CSV/gzip/JSON readers and git-show source receipts are used. Counts,
own initial risk, 20bp accounting, clocks, fixed triples, complete intention
deltas and serial occupancy are reconstructed without importing the strategy.
Saved launch diagnostics can establish internal consistency, not whether the
underlying completed CLOSE really crossed 0.5R: that requires raw replay. Month
bootstrap/sign-flip p values are NOT independently recalculated by this tool.

Python 3.9 contracts (explicit CSV strings and timezone-aware ISO parsing):
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/datetime.html#datetime.datetime.fromisoformat
https://docs.python.org/3.9/library/subprocess.html#subprocess.run
All entry data are saved original fields; launch/path/returns are outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11"
EXPERIMENT_PATH = "experiments/active/" + EXPERIMENT_ID
ARMS = ("baseline", "candidate")
TABLE_FILES = {"case_trades": "case_trades.csv.gz", "control_trades": "control_trades.csv.gz",
               "case_episodes": "case_episodes.csv.gz", "control_episodes": "control_episodes.csv.gz",
               "matched": "matched.csv", "single_pending": "single_pending.csv.gz"}
MINUTE = 60 * 10**9
HOUR = 60 * MINUTE
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FOLDS = {"2023H1": ("2023-01-01", "2023-07-01"), "2023H2": ("2023-07-01", "2024-01-01"),
         "2024H1": ("2024-01-01", "2024-07-01"), "2024H2": ("2024-07-01", "2025-01-01")}


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def number(value, *, nullable=False):
    if value is None or value == "":
        require(nullable, "Missing numeric value")
        return None
    require(not isinstance(value, bool), "Boolean is not a number")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise VerificationError("Invalid number") from exc
    require(math.isfinite(result), "Nonfinite number")
    return result


def equal_number(left, right, message):
    a, b = number(left, nullable=True), number(right, nullable=True)
    require((a is None and b is None) or (a is not None and b is not None and
            math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)), message)


def boolean(value):
    require(value is True or value is False or value in ("True", "False"), "Invalid boolean")
    return value is True or value == "True"


def stamp(value, *, nullable=False):
    """UTC nanoseconds, retaining sub-microsecond differences rather than truncating."""
    if value is None or value == "":
        require(nullable, "Missing timestamp")
        return None
    require(isinstance(value, str), "Timestamp must be an ISO string, not numeric units")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", value)
    require(match is not None, "Timezone-aware ISO timestamp required")
    try:
        dt = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError("Invalid timestamp") from exc
    delta = dt.astimezone(timezone.utc) - EPOCH
    return (delta.days*86400 + delta.seconds)*10**9 + int((match[2] or "").ljust(9, "0"))


def date_stamp(day):
    return stamp(day + "T00:00:00Z")


def indexed(rows):
    result = {}
    for row in rows:
        key = row.get("event_id")
        require(isinstance(key, str) and key.strip() and key not in result, "Missing or duplicate event_id")
        result[key] = row
    return result


def parity(before, after):
    """All old fields must remain, timestamps exact, extra diagnostics permitted."""
    a, b = indexed(before), indexed(after)
    require(a.keys() == b.keys(), "Parity identity drift")
    for key, row in a.items():
        require(row.keys() <= b[key].keys(), "Parity lost old column")
        for column, old in row.items():
            new = b[key][column]
            if old == new:
                continue
            if column.endswith(("_time", "_at", "_deadline", "_until", "_bar_open", "_available")):
                require(stamp(old, nullable=True) == stamp(new, nullable=True), "Parity clock drift: " + column)
            else:
                equal_number(old, new, "Parity value drift: " + column)


def safe_path(root, identity):
    require(isinstance(identity, str) and identity and not Path(identity).is_absolute(), "Unsafe path")
    require(all(p not in ("", ".", "..") for p in identity.split("/")), "Unsafe relative path")
    path = root / identity
    require(path.resolve().is_relative_to(root.resolve()), "Path escapes root")
    require(not path.is_symlink(), "Symlink evidence is forbidden")
    return path


def sha(path):
    require(path.is_file() and not path.is_symlink(), "Missing evidence: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    require(path.is_file() and not path.is_symlink(), "Missing JSON")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(VerificationError("Nonfinite JSON")))


def read_csv(path):
    require(path.is_file() and not path.is_symlink(), "Missing CSV")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames), "Missing/duplicate CSV header")
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows), "Ragged CSV")
    return rows


def mean(values):
    known = [v for v in values if v is not None]
    return math.fsum(known)/len(known) if known else None


def bp(value):
    return None if value is None else value*1e4


def check_trade(row):
    direction, entry, stop, atr = (number(row[k]) for k in ("direction", "entry_price", "initial_stop", "signal_atr"))
    require(direction in (-1, 1) and min(entry, stop, atr) > 0, "Invalid original entry")
    risk = direction*(entry-stop)
    require(risk > 0, "Nonpositive own initial risk")
    equal_number(row["risk_pct"], risk/entry, "Own risk_pct drift")
    equal_number(row["risk_atr"], risk/atr, "Own risk_atr drift")
    start = stamp(row["entry_time"])
    require(start == stamp(row["decision_time"]) == stamp(row["mother_decision_time"]), "Direct entry clock drift")
    require(start % HOUR == 0, "Direct entry is not an exact hour")
    require(stamp(row["mother_deadline"]) == start+72*HOUR, "Mother72h horizon drift")
    require(row["fold"] in FOLDS, "Unknown development fold")
    low, high = map(date_stamp, FOLDS[row["fold"]])
    require(low <= start < high-72*HOUR, "Entry outside strict development embargo")
    if "mother_signal_time" in row:
        require(stamp(row["mother_signal_time"]) == start-HOUR, "K1 decision before completion")
    if "wait_hours" in row:
        equal_number(row["wait_hours"], 0, "Direct cohort acquired a wait")
    end = stamp(row["exit_time"], nullable=True)
    closed = boolean(row["closed"])
    net = number(row["net_return"], nullable=True)
    gross = number(row["gross_return"], nullable=True)
    if end is not None:
        require(start <= end <= start+72*HOUR and end <= high, "Exit outside fixed horizon")
        require(end % (5*MINUTE) == 0, "Exit outside5m grid")
        equal_number(row["hold_minutes"], (end-start)/MINUTE, "Holding clock inconsistent")
    if closed:
        require(end is not None and net is not None and gross is not None, "Closed trade missing realised result")
        price = number(row["exit_price"])
        require(price > 0, "Invalid exit price")
        equal_number(gross, direction*(price/entry-1), "Gross return does not match own fill")
        equal_number(net, gross-.002, "20bp net accounting drift")
        equal_number(row["net_r"], net/(risk/entry), "Own netR drift")
        if row["outcome"] == "hard_stop":
            equal_number(price, stop, "Hard stop fill not frozen K1 extreme")
        if row["outcome"] == "hard_stop_gap":
            require(direction*(price-stop) <= 1e-12, "Gap stop fill improves beyond stop")
    else:
        require(net is None and gross is None, "Censored path must not invent realised return")
    require(not row["outcome"].startswith("entry_"), "Pinned original valid entry rejected")
    for key, value in (("partial_fraction", 0), ("exit_remaining_fraction", 1), ("realised_partial_gross_return", 0)):
        if key in row:
            equal_number(row[key], value, "Unregistered partial execution")
    if "funding_modelled" in row:
        require(not boolean(row["funding_modelled"]), "Unregistered funding model")
    return net


def check_launch(old, row):
    """Only diagnostic consistency; no assertion that saved CLOSE extrema are true."""
    require(boolean(row["launch_enabled"]), "Launch disabled")
    equal_number(row["launch_deadline_minutes"], 60, "Deadline parameter drift")
    equal_number(row["launch_progress_r"], .5, "Progress parameter drift")
    start = stamp(row["entry_time"])
    deadline = start+HOUR
    require(stamp(row["launch_deadline_at"]) == deadline, "Launch deadline not entry+60")
    count = number(row["launch_completed_close_count"])
    require(count == int(count) and 0 <= count <= 12, "Invalid completed close count")
    maximum = number(row["launch_max_completed_close_r"], nullable=True)
    require((count == 0) == (maximum is None), "Close count/max missingness inconsistent")
    reached = boolean(row["launch_progress_reached"])
    first = stamp(row["launch_progress_first_at"], nullable=True)
    checked = stamp(row["launch_deadline_checked_at"], nullable=True)
    end = stamp(row["exit_time"], nullable=True)
    require(end is not None and count <= min(12,(end-start)//(5*MINUTE)), "Completed CLOSE count uses unheld future")
    require(checked is None or (checked == deadline and end is not None and end >= checked), "Invalid deadline check time")
    if reached:
        require(first is not None and start < first <= deadline and first % (5*MINUTE) == 0, "Invalid first progress clock")
        require(end is not None and first <= end and count >= (first-start)/(5*MINUTE), "Progress after held path")
        require(maximum is not None and maximum >= .5-1e-12 and row["launch_status"] == "progress_confirmed", "Progress state inconsistent")
    else:
        require(first is None and (maximum is None or maximum < .5), "Unreached progress has confirming evidence")
    timeout = row["outcome"] == "launch_timeout_exit"
    if timeout:
        require(boolean(row["closed"]) and not reached and end == checked == deadline, "Invalid timeout decision")
        require(count == 12 and maximum is not None and row["launch_status"] == "timeout_exit", "Timeout lacks12 complete observations")
        require(stamp(old["exit_time"]) > deadline, "Timeout did not strictly precede original exit")
    elif boolean(row["closed"]):
        require(row["launch_status"] == ("progress_confirmed" if reached else "prior_exit"), "Retained exit status inconsistent")
        require(reached or end <= deadline, "Unconfirmed launch survived deadline")
        # Every original saved output, including transition diagnostics/MFE, must
        # survive when the new policy did not exit. New launch fields are extra.
        parity([old], [row])
    else:
        require(row["launch_status"] in ("unknown_source", "progress_confirmed"), "Unknown path promoted to known timeout")
    require(row["launch_status"] != "pending", "Internal pending state escaped")
    return timeout


def check_episode(trade, episode):
    require(episode["status"] == "request_emitted", "Original direct request lost")
    require(stamp(episode["terminal_time"]) == stamp(episode["mother_decision_time"]), "Direct terminal clock drift")
    require(episode["episode_status"] == trade["outcome"], "Episode outcome mismatch")
    require(boolean(episode["executed"]), "Valid direct trade labelled nonentry")
    closed = boolean(trade["closed"])
    require(boolean(episode["completed_trade"]) == closed and boolean(episode["observed"]) == closed, "Episode observability mismatch")
    equal_number(episode["episode_net_return"], trade["net_return"], "Episode omitted/changed realised return")
    for name in ("entry_time", "exit_time"):
        require(stamp(episode[name], nullable=True) == stamp(trade[name], nullable=True), "Episode execution clock mismatch")
    expected = stamp(trade["exit_time"]) if closed else stamp(episode["mother_deadline"])
    require(stamp(episode["occupied_until"]) == expected, "Censored occupancy was shortened")


def check_serial(episodes, serial):
    a, b = indexed(episodes), indexed(serial)
    require(a.keys() == b.keys(), "Serial dropped original intention")
    parity(episodes, serial)
    free = {}
    values = {}
    for key, row in sorted(a.items(), key=lambda x: (stamp(x[1]["mother_decision_time"]), x[0])):
        now, fold = stamp(row["mother_decision_time"]), row["fold"]
        selected = now >= free.get(fold, -10**30)
        require(boolean(b[key]["portfolio_selected"]) == selected, "Serial selection inconsistent with occupancy")
        require(b[key]["portfolio_reason"] == ("accepted_mother" if selected else "pending_or_position_busy"), "Serial reason inconsistent")
        if selected:
            until = stamp(row["occupied_until"])
            free[fold] = ((until+5*MINUTE-1)//(5*MINUTE))*(5*MINUTE)
        values[key] = number(row["episode_net_return"], nullable=True) if selected else 0.
    return values


def check_metrics(rows, summary):
    values = [number(r["net_return"], nullable=True) for r in rows if boolean(r["closed"])]
    equal_number(summary["events"], len(values), "Metrics closed denominator mismatch")
    equal_number(summary["mean_net_bp"], bp(mean(values)), "Mean return mismatch")
    if values:
        equal_number(summary["win_rate"], sum(v > 0 for v in values)/len(values), "Win denominator mismatch")
        gains, losses = math.fsum(v for v in values if v > 0), -math.fsum(v for v in values if v < 0)
        if losses:
            equal_number(summary["profit_factor"], gains/losses, "PF mismatch")
        equal_number(summary["extra_10bp_mean_net_bp"], bp(mean(values))-10, "Cost stress drift")
    return values


def verify_tables(tables, arm_summaries, effects, *, expected_counts=(251, 462, 154)):
    """Pure synthetic-test entry point; CLI always enforces251/462/154.

    tables has baseline/candidate dictionaries of six native saved tables and
    root case_delta/excess_delta/serial_delta rows. No source OHLC is accepted.
    """
    cases_n, controls_n, matched_n = expected_counts
    states, mapping, serial_values, timeout_counts = {}, None, {}, {}
    for arm in ARMS:
        t = tables[arm]
        states[arm] = {name: indexed(rows) for name, rows in t.items()}
        require(len(t["case_trades"]) == cases_n and len(t["control_trades"]) == controls_n, "Frozen population count drift")
        current_mapping = {key: (row["parent_event_id"], row["decision_time"]) for key, row in states[arm]["control_trades"].items()}
        require(len({stamp(v[1]) for v in current_mapping.values()}) == controls_n, "Reused control timestamp")
        counts = Counter(parent for parent, _ in current_mapping.values())
        require(len(counts) == matched_n and set(counts.values()) == {3}, "Fixed triples incomplete")
        require(set(counts) <= states[arm]["case_trades"].keys(), "Foreign control parent")
        if mapping is not None:
            require(mapping == current_mapping, "Frozen control mapping changed")
        mapping = current_mapping
        timeout_counts[arm] = {}
        for label in ("case", "control"):
            trades, episodes = states[arm][label+"_trades"], states[arm][label+"_episodes"]
            require(trades.keys() == episodes.keys(), "Episode population drift")
            timeouts = 0
            for key, row in trades.items():
                check_trade(row)
                check_episode(row, episodes[key])
                if arm == "candidate":
                    old = states["baseline"][label+"_trades"].get(key)
                    require(old is not None, "Candidate changed event identity")
                    fixed = {c: old[c] for c in ("event_id", "direction", "decision_time", "mother_decision_time", "mother_deadline", "entry_time", "entry_price", "initial_stop", "signal_atr", "risk_pct", "risk_atr", "fold")}
                    parity([fixed], [row])
                    timeouts += check_launch(old, row)
            timeout_counts[arm][label] = timeouts
            check_metrics(list(trades.values()), arm_summaries[arm]["metrics" if label == "case" else "control_metrics"])
        pairs = states[arm]["matched"]
        require(pairs.keys() == states[arm]["case_episodes"].keys(), "Matching denominator shrank")
        for key, pair in pairs.items():
            case = states[arm]["case_episodes"][key]
            control = [states[arm]["control_episodes"][cid] for cid, (parent, _) in mapping.items() if parent == key]
            values = [number(r["episode_net_return"], nullable=True) for r in control]
            control_mean = mean(values) if len(values) == 3 and all(v is not None for v in values) else None
            case_net = number(case["episode_net_return"], nullable=True)
            excess = case_net-control_mean if case_net is not None and control_mean is not None else None
            equal_number(pair["assigned_controls"], len(control), "Assignment changed")
            equal_number(pair["event_net_return"], case_net, "Matching case mismatch")
            equal_number(pair["control_mean_return"], control_mean, "Incomplete controls became a mean")
            equal_number(pair["excess"], excess, "Matched excess mismatch")
            require(stamp(pair["mother_decision_time"]) == stamp(case["mother_decision_time"]), "Matched month clock changed")
        match_summary = arm_summaries[arm]["matching"]
        finite = [number(r["excess"], nullable=True) for r in pairs.values()]
        equal_number(match_summary["paired_events"], sum(x is not None for x in finite), "Paired known denominator mismatch")
        equal_number(match_summary["mother_events"], cases_n, "Mother denominator mismatch")
        equal_number(match_summary["coverage"], sum(x is not None for x in finite)/cases_n, "Coverage denominator mismatch")
        equal_number(match_summary["mean_excess_bp"], bp(mean(finite)), "Matching mean mismatch")
        serial_values[arm] = check_serial(t["case_episodes"], t["single_pending"])
        equal_number(arm_summaries[arm]["serial_selected_mothers"], sum(boolean(r["portfolio_selected"]) for r in t["single_pending"]), "Serial selected count mismatch")
        selected_ids = {r["event_id"] for r in t["single_pending"] if boolean(r["portfolio_selected"])}
        check_metrics([r for r in t["case_trades"] if r["event_id"] in selected_ids], arm_summaries[arm]["single_position"])
    result = {}
    for name, table, column in (("case_delta", "case_episodes", "episode_net_return"), ("excess_delta", "matched", "excess"), ("serial_delta", None, None)):
        rows = indexed(tables[name])
        require(rows.keys() == states["baseline"]["case_episodes"].keys(), "Paired all-mother denominator drift")
        deltas = []
        for key, row in rows.items():
            before, after = ((serial_values[a][key] for a in ARMS) if table is None else
                             (number(states[a][table][key][column], nullable=True) for a in ARMS))
            delta = after-before if before is not None and after is not None else None
            for field, value in (("before", before), ("after", after), ("difference", delta)):
                equal_number(row[field], value, "Paired identity/arithmetic mismatch: " + name)
            require(stamp(row["mother_decision_time"]) == stamp(states["baseline"]["case_episodes"][key]["mother_decision_time"]), "Paired cluster clock changed")
            deltas.append(delta)
        known = [d for d in deltas if d is not None]
        expected = {"total_pairs": cases_n, "n": len(known), "unknown_pairs": cases_n-len(known),
                    "improved": sum(d > 1e-12 for d in known), "worsened": sum(d < -1e-12 for d in known),
                    "unchanged": sum(abs(d) <= 1e-12 for d in known), "mean_bp": bp(mean(known))}
        for field, value in expected.items():
            equal_number(effects[name][field], value, "Effect denominator/mean mismatch: " + name + "/" + field)
        result[name] = {**expected, "sum_event_bp": math.fsum(known)*1e4 if known else None}
    return {"counts": {"cases": cases_n, "controls": controls_n, "matched": matched_n},
            "timeout_exits": timeout_counts["candidate"], "effects": result,
            "raw_replay": False, "inferential_p_recomputed": False,
            "limitation": "Saved-ledger consistency only; no independent raw CLOSE/price, path completeness, or profitability proof."}


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    location = (len(values)-1)*fraction
    low, high = math.floor(location), math.ceil(location)
    return values[low] + (values[high]-values[low])*(location-low)


def verify_diagnostics(tables, mechanics_rows, group_rows, monthly_rows, summary):
    """Independently reconcile every saved paired row, monthly mean and bin-free distribution."""
    old = indexed(tables["baseline"]["case_trades"])
    new = indexed(tables["candidate"]["case_trades"])
    mechanics = indexed(mechanics_rows)
    require(old.keys() == mechanics.keys() == new.keys(), "Mechanics dropped original cases")
    transitions, grouped, distributions = Counter(), defaultdict(list), defaultdict(list)
    for key, row in mechanics.items():
        expected = {"event_id": key}
        for suffix, source in (("before", old[key]), ("after", new[key])):
            expected.update({c+"_"+suffix: value for c, value in source.items() if c != "event_id"})
        # Suffixed time names must still receive exact nanosecond comparisons.
        for suffix in ("before", "after"):
            actual = {c[:-len(suffix)-1]: value for c, value in row.items() if c.endswith("_"+suffix)}
            actual["event_id"] = key
            parity([old[key] if suffix == "before" else new[key]], [actual])
        a, b = (number(source[key]["net_return"], nullable=True) for source in (old, new))
        known = boolean(old[key]["closed"]) and boolean(new[key]["closed"]) and a is not None and b is not None
        delta = b-a if known else None
        equal_number(row["difference"], delta, "Mechanics delta mismatch")
        timeout = new[key]["outcome"] == "launch_timeout_exit"
        require(boolean(row["timeout_exit"]) == timeout, "Mechanics timeout classification mismatch")
        transition = ("unknown" if not known else "includes_flat" if a == 0 or b == 0 else
                      ("win" if a > 0 else "loss")+"_to_"+("win" if b > 0 else "loss"))
        group = "unknown" if not known else "launch_timeout" if timeout else "original_exit_retained"
        require(row["win_loss_transition"] == transition and row["mechanism_group"] == group, "Mechanics group mismatch")
        transitions[transition] += 1
        grouped[group].append((a,b,delta))
        for column, value in (("net_return_before",a), ("net_return_after",b), ("difference",delta)):
            if known:
                distributions[column].append(value*1e4)
    require(summary["transitions"] == dict(transitions), "Win/loss movement counts mismatch")
    equal_number(summary["total"], len(old), "Mechanics total mismatch")
    equal_number(summary["known"], len(distributions["difference"]), "Mechanics known count mismatch")
    equal_number(summary["timeout_exits"], sum(new[k]["outcome"] == "launch_timeout_exit" for k in new), "Timeout count mismatch")
    actual_groups = {r["group"]:r for r in group_rows}
    require(len(actual_groups) == len(group_rows) and actual_groups.keys() == grouped.keys(), "Mechanism group missing/duplicate")
    summary_groups = {r["group"]:r for r in summary["groups"]}
    require(len(summary_groups) == len(summary["groups"]) and summary_groups.keys() == actual_groups.keys(), "Root/group-table identity mismatch")
    for name,row in actual_groups.items():
        for column,value in row.items():
            if column != "group":
                equal_number(summary_groups[name][column],value,"Root/group-table mismatch")
    for name, values in grouped.items():
        expected = {"n":len(values),"known":sum(d is not None for a,b,d in values),
            "old_mean_net_bp":bp(mean([a for a,b,d in values])),"new_mean_net_bp":bp(mean([b for a,b,d in values])),
            "mean_delta_bp":bp(mean([d for a,b,d in values])),
            "sum_delta_event_bp":bp(math.fsum(d for a,b,d in values if d is not None)) if any(d is not None for a,b,d in values) else None,
            "wins_before":sum(a is not None and a>0 for a,b,d in values),"wins_after":sum(b is not None and b>0 for a,b,d in values)}
        for column,value in expected.items():
            equal_number(actual_groups[name][column],value,"Mechanism aggregate mismatch")
    for column in ("net_return_before","net_return_after","difference"):
        values, stated = distributions[column], summary["distributions"][column]
        for name,value in (("n",len(values)),("unknown",len(old)-len(values)),("outliers_removed",0),
                           ("sd_bp",statistics.stdev(values) if len(values)>1 else None)):
            equal_number(stated[name],value,"Distribution count/SD mismatch")
        require(set(stated["quantiles_bp"]) == {str(x) for x in (0.,.05,.25,.5,.75,.95,1.)}, "Distribution quantiles omitted")
        for fraction,value in stated["quantiles_bp"].items():
            equal_number(value,quantile(values,float(fraction)),"Untrimmed quantile mismatch")
    groups = defaultdict(list)
    for arm in ARMS:
        for row in tables[arm]["case_episodes"]:
            month = (EPOCH+timedelta(seconds=stamp(row["mother_decision_time"])/10**9)).strftime("%Y-%m")
            groups[(arm,row["fold"],month)].append(number(row["episode_net_return"],nullable=True))
    actual = {(r["arm"],r["fold"],r["month"]):r for r in monthly_rows}
    require(len(actual) == len(monthly_rows) and actual.keys() == groups.keys(), "Monthly groups missing/duplicated")
    for key,values in groups.items():
        for column,value in (("n",len(values)),("known",sum(x is not None for x in values)),("mean_net_bp",bp(mean(values)))):
            equal_number(actual[key][column],value,"Monthly whole-cohort count/mean mismatch")
    return {"paired_rows":len(mechanics),"monthly_rows":len(monthly_rows),"untrimmed_distributions":3}


def verify_committed_sources(root, started, summary, required):
    require(started["sources"] == summary["sources"] and started["sources"], "Source receipts missing/mismatched")
    commit = started["builder_commit"]
    require(bool(re.fullmatch(r"[a-f0-9]{40}", commit)), "Invalid builder commit")
    source_ids = [item["path"] for item in started["sources"]]
    require(len(set(source_ids)) == len(source_ids) and set(required) <= set(source_ids), "Missing/duplicate committed source")
    for item in started["sources"]:
        safe_path(root, item["path"])
        require(not item["path"].startswith("data/"), "Raw price receipt must not be read")
        try:
            content = subprocess.run(["git", "show", commit+":"+item["path"]], cwd=root,
                                     check=True, capture_output=True).stdout
        except subprocess.CalledProcessError as exc:
            raise VerificationError("Committed source unavailable; cannot skip verification") from exc
        require(hashlib.sha256(content).hexdigest() == item["sha256"], "Committed source hash mismatch")
    return len(source_ids)


def verify_output_hashes(results, hashes):
    actual = {str(p.relative_to(results)) for p in results.rglob("*") if p.is_file()}
    require(actual == set(hashes) | {"summary.json"}, "Output hash coverage incomplete or extra files")
    for name, expected in hashes.items():
        require(sha(safe_path(results, name)) == expected, "Output hash mismatch: " + name)
    return len(hashes)


def verify(root=ROOT, experiment_path=EXPERIMENT_PATH):
    root = Path(root)
    experiment = safe_path(root, experiment_path)
    results = experiment/"results"
    config, summary, started = (read_json(p) for p in (experiment/"config.json", results/"summary.json", results/"started.json"))
    require(summary["experiment_id"] == config["experiment_id"] == EXPERIMENT_ID, "Wrong experiment")
    require(summary["status"] == "diagnostic_only_no_candidate_acceptance", "Support diagnostic promoted to acceptance")
    for key in ("holdout_consumed", "production_eligible", "training_eligible"):
        require(summary[key] is False and config[key] is False, "Eligibility/holdout drift")
    require(summary["audit_prices_loaded"] is False, "Audit prices loaded")
    expected_policy = {"id":"5m_native40","management_minutes":5,"ma_kind":"SMA","ma_length":40,
                       "exit_mode":"transition_colour","confirmations":1}
    require(config["policies"] == [expected_policy,dict(expected_policy,id="5m_native40_launch60",
            launch_deadline_minutes=60,launch_progress_r=.5)], "Frozen policy specification drift")
    equal_number(summary["known_coverage_ceiling"], 154/251, "Coverage ceiling drift")
    equal_number(summary["coverage_required"], .9, "Coverage gate weakened")
    require(sha(experiment/"config.json") == summary["config_sha256"], "Config hash mismatch")
    require(sha(safe_path(root, config["base_config"])) == config["base_config_sha256"], "Base config hash mismatch")
    for directory, key in ((config["parent_results"], "inputs"), (config["mother_results"], "mother_inputs")):
        require(config[key] == summary[key] == started[key], "Frozen input receipt mismatch")
        require(directory.startswith("experiments/active/") and directory.endswith("/results"), "Input directory not saved evidence")
        for name, expected in config[key].items():
            require(sha(safe_path(root, directory+"/"+name)) == expected, "Prior input hash mismatch: " + name)
    hashes = summary["output_hashes"]
    verify_output_hashes(results,hashes)
    commit = started["builder_commit"]
    source_count = verify_committed_sources(root,started,summary,
        {"yoyo/layers/l3_backtest/hourly_impulse.py","yoyo/evaluation/hourly_impulse_launch_research.py",
         experiment_path+"/config.json",experiment_path+"/PROJECT_PLAN.md"})
    tables = {arm: {name: read_csv(results/arm/file) for name, file in TABLE_FILES.items()} for arm in ARMS}
    for name in ("case_delta", "excess_delta", "serial_delta"):
        tables[name] = read_csv(results/(name+".csv"))
    for arm in ARMS:
        require(read_json(results/arm/"summary.json") == summary["arms"][arm], "Root/arm summary drift")
        for label in ("case", "control"):
            context = read_csv(results/(label+"_context.csv.gz"))
            parent_context = read_csv(safe_path(root, config["parent_results"]+"/direct_k1_stop_"+label+"_context.csv.gz"))
            parity(parent_context, context)
            parity(context, tables[arm][label+"_trades"])
    parent = safe_path(root, config["parent_results"])
    for name, filename in TABLE_FILES.items():
        parity(read_csv(parent/("direct_k1_stop__transition_colour_"+filename)), tables["baseline"][name])
    assignments = read_csv(results/"assignments.csv")
    parity(read_csv(safe_path(root, config["mother_results"]+"/assignments.csv")), assignments)
    matched = {row["event_id"] for row in assignments if row["match_status"] == "matched"}
    require(matched == {r["parent_event_id"] for r in tables["baseline"]["control_trades"]}, "Old154 assignments replaced")
    output = verify_tables(tables, summary["arms"], summary["effects"])
    output["diagnostics"] = verify_diagnostics(tables,read_csv(results/"paired_case_mechanics.csv.gz"),
        read_csv(results/"mechanism_groups.csv"),read_csv(results/"monthly_case_net.csv"),summary["mechanics"])
    require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False,
            "Failed90percent support gate was bypassed")
    output.update(status="passed", output_hashes_verified=len(hashes), committed_sources_verified=source_count,
                  builder_commit=commit, summary_sha256=sha(results/"summary.json"))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(verify(), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
