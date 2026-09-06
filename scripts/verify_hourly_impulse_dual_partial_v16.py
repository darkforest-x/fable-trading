"""Independent V16 SAVED-ledger verification, using only the Python stdlib.

The 15 CSVs contain two six-table arms and three all-mother paired deltas.
This verifier independently checks identities, unchanged final held paths,
weighted original-notional accounting, recorded fast edges, fixed triples and
serial occupancy. It does NOT import a strategy, read raw prices, recompute
SMA40, prove that the edge log is exhaustive, or rerun inferential statistics.
All fast/slow observations and returns are saved post-entry diagnostics, never
features for selecting mothers. Unknown whole paths remain unknown even when
a partial fill is known. Only the CLI reads files; verify_tables is pure.

Python3.9 source contracts: CSV stays strings, ISO clocks retain nanoseconds,
and decimal quote comparisons reject exact20bp equality without float noise:
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/datetime.html#datetime.datetime.fromisoformat
https://docs.python.org/3.9/library/decimal.html
No local imports: this file is the entire transitive verifier dependency.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16"
ARMS = ("baseline", "candidate")
TABLE_FILES = {"case_trades": "case_trades.csv.gz", "control_trades": "control_trades.csv.gz",
    "case_episodes": "case_episodes.csv.gz", "control_episodes": "control_episodes.csv.gz",
    "matched": "matched.csv", "single_pending": "single_pending.csv.gz"}
DELTAS = ("case_delta", "excess_delta", "serial_delta")
MINUTE, HOUR = 60*10**9, 3600*10**9
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FOLDS = {"2023H1": ("2023-01-01", "2023-07-01"), "2023H2": ("2023-07-01", "2024-01-01"),
    "2024H1": ("2024-01-01", "2024-07-01"), "2024H2": ("2024-07-01", "2025-01-01")}
BASE_POLICY = {"id": "15m_native40", "management_minutes": 15, "ma_kind": "SMA", "ma_length": 40,
    "exit_mode": "transition_colour", "confirmations": 1}
CANDIDATE_POLICY = dict(BASE_POLICY, id="15m_native40_dual_partial", fast_partial_fraction=.5)
ACCOUNTING_COLUMNS = {"gross_return", "net_return", "net_r", "partial_fraction", "exit_remaining_fraction",
    "partial_exit_time", "partial_exit_price", "realised_partial_gross_return", "marked_gross_return", "marked_net_return"}
SCOPE = {"raw_replay": False, "inferential_p_recomputed": False, "sma_recomputed": False,
    "unlogged_edges_excluded_independently": False,
    "limitation": "Saved clocks, colour snapshots, logged-first eligibility, formulas and receipts only; "
        "not independent raw OHLC/SMA/complete-edge replay, actual fills, inferential pipeline or profitability proof."}


class VerificationError(ValueError):
    """Saved evidence contradicts the frozen contract."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def number(value, nullable=False):
    if value is None or value == "":
        require(nullable, "Missing number")
        return None
    require(not isinstance(value, bool), "Boolean used as number")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise VerificationError("Invalid number") from error
    require(math.isfinite(result), "Nonfinite number")
    return result


def eq(a, b, message):
    a, b = number(a, True), number(b, True)
    require((a is None and b is None) or (a is not None and b is not None and
        math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)), message)


def boolean(value):
    require(type(value) is bool or value in ("True", "False"), "Invalid boolean")
    return value is True or value == "True"


def stamp(value, nullable=False):
    if value is None or value == "":
        require(nullable, "Missing clock")
        return None
    require(isinstance(value, str), "Clock requires timezone-aware ISO text")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", value)
    require(match is not None, "Invalid or naive ISO clock")
    try:
        dt = datetime.fromisoformat(match[1]+match[3].replace("Z", "+00:00"))
    except ValueError as error:
        raise VerificationError("Invalid clock") from error
    delta = dt.astimezone(timezone.utc)-EPOCH
    return (delta.days*86400+delta.seconds)*10**9 + int((match[2] or "").ljust(9, "0"))


def indexed(rows):
    answer = {}
    for row in rows:
        key = row.get("event_id")
        require(isinstance(key, str) and key.strip() and key not in answer, "Missing/duplicate event_id")
        answer[key] = row
    return answer


def same(old, new, field):
    if old == new:
        return
    if field.endswith(("_time", "_at", "_deadline", "_until", "_bar_open", "_available")):
        require(stamp(old, True) == stamp(new, True), "Clock parity: "+field)
    else:
        eq(old, new, "Value parity: "+field)


def parity(old, new, exceptions=()):
    require(old.keys() <= new.keys(), "Lost original column")
    for field, value in old.items():
        if field not in exceptions:
            same(value, new[field], field)


def mean(values):
    known = [v for v in values if v is not None]
    return math.fsum(known)/len(known) if known else None


def bp(value):
    return None if value is None else value*1e4


def parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(VerificationError("Nonfinite JSON")))


def profit_qualified(price, entry, direction):
    """Exact finite decimal quote comparison, not rounded binary gross return."""
    for value in (price, entry, direction):
        number(value)
    with localcontext() as context:
        context.prec = 50
        return Decimal(str(direction))*(Decimal(str(price))-Decimal(str(entry))) > Decimal("0.002")*Decimal(str(entry))


def valid_source(source, available, minutes):
    require(isinstance(source, dict), "Missing colour snapshot")
    require(stamp(source["open_time"])+minutes*MINUTE == available and available % (minutes*MINUTE) == 0,
        "Incomplete/stale native colour snapshot")
    side, ma, hl2 = (number(source[key]) for key in ("side", "ma", "hl2"))
    require(ma > 0 and hl2 > 0 and side == (1 if hl2 >= ma else -1), "Colour differs from saved HL2/MA")
    for name in ("management_segment_id", "raw_segment_id"):
        require(source[name] is not None and str(source[name]).strip() not in ("", "nan", "inf"), "Missing segment evidence")
    return side


def check_partial(row):
    """Validate all recorded fast edges; firstness is ONLY within this saved log."""
    require(boolean(row["partial_fast_enabled"]), "Partial disabled")
    eq(row["partial_fast_fraction"], .5, "Half fraction drift")
    eq(row["partial_fast_profit_threshold"], .002, "Fixed20bp event gate drift")
    start, end = stamp(row["entry_time"]), stamp(row["exit_time"])
    direction, entry = number(row["direction"]), number(row["entry_price"])
    state = row["partial_fast_initial_state"]
    require(state in ("aligned", "opposite", "unknown"), "Invalid fast initial state")
    if state == "unknown":
        require(number(row["partial_fast_initial_side"], True) is None and
            stamp(row["partial_fast_initial_open_time"], True) is None and
            stamp(row["partial_fast_initial_available_at"], True) is None and
            row["partial_fast_initial_reason"] != "valid", "Unknown seed invented known state")
    else:
        source = {name:row["partial_fast_initial_"+name] for name in
            ("open_time", "side", "ma", "hl2", "management_segment_id", "raw_segment_id")}
        side = valid_source(source, start, 5)
        require(stamp(row["partial_fast_initial_available_at"]) == start and row["partial_fast_initial_reason"] == "valid",
            "Fast seed not exact completed entry context")
        require(state == ("aligned" if direction*side > 0 else "opposite"), "Fast own direction drift")
    armed = stamp(row["partial_fast_first_armed_at"], True)
    require(armed is None or (start <= armed <= end and armed % (5*MINUTE) == 0), "Arming clock outside held path")
    require(state != "aligned" or armed == start, "Aligned seed was not armed at entry")
    resets = number(row["partial_fast_reset_count"])
    require(resets == int(resets) and resets >= 0 and (resets == 0 or row["partial_fast_last_reset_reason"]), "Invalid reset count/reason")
    events = parse_json(row["partial_fast_events"]) if isinstance(row["partial_fast_events"], str) else row["partial_fast_events"]
    require(isinstance(events, list), "Fast events must be JSON array")
    eq(row["partial_fast_flip_count"], len(events), "Fast edge count differs from log")
    previous_time, filled, fill_event = start, False, None
    observed_fast = {}
    for event in events:
        now = stamp(event["available_at"])
        require(previous_time < now <= end and now < start+72*HOUR and now % (5*MINUTE) == 0, "Unordered/unheld fast edge")
        # A final source censor may be recorded at its last-known open. Other
        # terminal paths consumed this boundary before a fast edge could run.
        require(now < end or row["outcome"] in ("data_gap_censored", "right_censored"), "Partial displaced prior terminal event")
        previous_time = now
        p, c = event["previous_fast"], event["current_fast"]
        ps, cs = valid_source(p, now-5*MINUTE, 5), valid_source(c, now, 5)
        for clock, source in ((now-5*MINUTE,p),(now,c)):
            if clock in observed_fast:
                parity(observed_fast[clock],source)
            observed_fast[clock] = source
        require(direction*ps > 0 and direction*cs < 0, "Not a true aligned-to-opposite fast edge")
        require(p["management_segment_id"] == c["management_segment_id"] and p["raw_segment_id"] == c["raw_segment_id"],
            "Fast edge crossed a segment reset")
        require(armed is not None and armed <= now-5*MINUTE, "Edge before observed arming")
        slow_available = now//(15*MINUTE)*(15*MINUTE)
        require(stamp(event["slow_available_at"]) == slow_available, "Slow gate used current unfinished/older bar")
        slow_state, reason = event["slow_state"], event["slow_reason"]
        require(slow_state in ("aligned", "opposite", "unknown"), "Invalid slow gate state")
        if slow_state != "unknown":
            ss = valid_source(event["slow"], slow_available, 15)
            require(reason == "valid" and slow_state == ("aligned" if direction*ss > 0 else "opposite"), "Slow own alignment drift")
            require(event["slow"]["raw_segment_id"] == c["raw_segment_id"], "Slow carry crossed source gap")
        else:
            require(reason != "valid" and number(event["slow"]["side"], True) is None, "Unknown slow state filled")
        price = number(event["open_price"])
        require(price > 0 and direction*(price-number(row["initial_stop"])) > 0, "Partial displaced gap-open stop")
        eq(event["gross_return"], direction*(price/entry-1), "Logged gross differs from open")
        eq(event["profit_threshold"], .002, "Logged cost gate drift")
        qualifies = profit_qualified(event["open_price"], row["entry_price"], row["direction"])
        require(boolean(event["profit_qualified"]) == qualifies, "Strict decimal20bp equality/gate drift")
        action = ("already_partial" if filled else "slow_unknown" if slow_state == "unknown" else
            "slow_not_aligned" if slow_state != "aligned" else "insufficient_profit" if not qualifies else "executed")
        require(event["action"] == action, "Not first recorded qualifying edge, or action precedence drift")
        if action == "executed":
            filled, fill_event = True, event
    eq(row["partial_fast_fill_count"], int(filled), "Partial fill count drift")
    eq(row["partial_fraction"], .5 if filled else 0, "Partial fraction/log drift")
    closed = boolean(row["closed"])
    require(row["partial_fast_status"] == (("partial_closed" if closed else "partial_censored") if filled else
        "no_partial_exit" if closed else "unknown_source"), "Partial status/whole observability drift")
    fields = ("trigger_previous_open_time", "trigger_open_time", "trigger_available_at", "trigger_previous_side",
        "trigger_side", "trigger_gross_return", "slow_open_time", "slow_available_at", "slow_side")
    if filled:
        expected = (fill_event["previous_fast"]["open_time"], fill_event["current_fast"]["open_time"], fill_event["available_at"],
            fill_event["previous_fast"]["side"], fill_event["current_fast"]["side"], fill_event["gross_return"],
            fill_event["slow"]["open_time"], fill_event["slow_available_at"], fill_event["slow"]["side"])
        for field, value in zip(fields, expected):
            same(row["partial_fast_"+field], value, field)
        same(row["partial_exit_time"], fill_event["available_at"], "partial_exit_time")
        eq(row["partial_exit_price"], fill_event["open_price"], "Fill differs from logged real open")
        require(row["partial_fast_slow_state"] == "aligned", "Partial did not have slow alignment")
    else:
        for field in fields:
            require(row["partial_fast_"+field] in (None, ""), "Unfilled partial invented scalar trigger")
        require(row["partial_fast_slow_state"] == "unknown", "Unfilled partial invented scalar slow state")
    return filled


def check_trade(row, candidate=False):
    """Original entry risk and full or half-plus-remainder arithmetic."""
    direction, entry, stop, atr = (number(row[k]) for k in ("direction", "entry_price", "initial_stop", "signal_atr"))
    require(direction in (-1, 1) and min(entry, stop, atr) > 0, "Invalid original entry")
    risk = direction*(entry-stop)
    require(risk > 0, "Nonpositive original risk")
    eq(row["risk_pct"], risk/entry, "Original risk_pct drift")
    eq(row["risk_atr"], risk/atr, "Original risk_atr drift")
    start, end = stamp(row["entry_time"]), stamp(row["exit_time"])
    require(start == stamp(row["decision_time"]) == stamp(row["mother_decision_time"]) and start % HOUR == 0, "Original hourly entry changed")
    require(stamp(row["signal_time"])+HOUR == start and stamp(row["mother_deadline"]) == start+72*HOUR, "K1/horizon clock drift")
    require(row["fold"] in FOLDS, "Unknown fold")
    low, high = (stamp(s+"T00:00:00Z") for s in FOLDS[row["fold"]])
    require(low <= start < high-72*HOUR and start <= end <= start+72*HOUR and end <= high and end % (5*MINUTE) == 0,
        "Outside original development window or5m clock")
    eq(row["hold_minutes"], (end-start)/MINUTE, "Holding clock drift")
    if "wait_hours" in row: eq(row["wait_hours"], 0, "Direct entry acquired waiting")
    closed = boolean(row["closed"])
    require(row["outcome"] in ({"hard_stop", "hard_stop_gap", "transition_colour_exit", "time_exit"} if closed else
        {"data_gap_censored", "right_censored"}), "Unregistered/rejected original execution")
    price = number(row["exit_price"])
    require(price > 0, "Invalid final mark/fill")
    if row["outcome"] == "hard_stop": eq(price, stop, "Hard stop changed")
    if row["outcome"] == "hard_stop_gap": require(direction*(price-stop) <= 1e-12, "Gap stop improved fill")
    if row["outcome"] == "time_exit": require(end == start+72*HOUR, "Duration changed")
    state = row["transition_initial_state"]
    require(state in ("aligned", "opposite", "unknown"), "Invalid native15 seed")
    seed = stamp(row["transition_initial_open_time"], True)
    require(seed == (None if state == "unknown" else start-15*MINUTE), "Native15 seed clock drift")
    trigger = [stamp(row[k], True) for k in ("transition_trigger_previous_open_time", "transition_trigger_open_time", "transition_trigger_available_at")]
    if row["outcome"] == "transition_colour_exit":
        p, c, a = trigger
        require(None not in trigger and p+15*MINUTE == c and c+15*MINUTE == a == end and
            c >= start and end % (15*MINUTE) == 0, "Native15 final edge clock drift")
        require(direction*(price-stop) > 0, "Slow exit displaced gap stop")
    else:
        require(all(t is None for t in trigger), "Other final exit invented slow trigger")
    q = number(row["partial_fraction"])
    require(q in ((0, .5) if candidate else (0,)), "Unregistered partial size")
    eq(row["exit_remaining_fraction"], 1-q, "Original fraction not conserved")
    if q:
        partial = number(row["partial_exit_price"])
        when = stamp(row["partial_exit_time"])
        require(start < when <= end and when % (5*MINUTE) == 0, "Partial fill clock invalid")
        require(profit_qualified(row["partial_exit_price"], row["entry_price"], row["direction"]), "Partial failed strict20bp economics")
        realised = q*direction*(partial/entry-1)
        excursion = direction*(partial-entry)/risk
        require(number(row.get("max_favourable_r")) >= excursion-1e-12, "Recorded partial open exceeds held MFE")
    else:
        require(stamp(row["partial_exit_time"], True) is None and number(row["partial_exit_price"], True) is None, "No fraction but fill retained")
        realised = 0.
    eq(row["realised_partial_gross_return"], realised, "Partial original-notional gross drift")
    gross = realised+(1-q)*direction*(price/entry-1)
    eq(row["marked_gross_return"], gross, "Weighted marked gross drift")
    eq(row["marked_net_return"], gross-.002, "Marked full-cost accounting drift")
    if candidate:
        eq(row["partial_fast_realised_net_return"], realised-q*.002, "Partial allocated-cost drift")
        check_partial(row)
    if closed:
        eq(row["gross_return"], gross, "Weighted whole gross drift")
        eq(row["net_return"], gross-.002, "Whole20bp cost drift")
        eq(row["net_r"], (gross-.002)/(risk/entry), "Original R rebased after partial")
    else:
        require(all(number(row[k], True) is None for k in ("gross_return", "net_return", "net_r")), "Partial/censor falsely became known whole trade")
    if "funding_modelled" in row: require(not boolean(row["funding_modelled"]), "Unregistered funding model")


def check_episode(trade, episode):
    require(episode["status"] == "request_emitted" and boolean(episode["executed"]), "Original request omitted")
    require(stamp(episode["terminal_time"]) == stamp(episode["mother_decision_time"]), "Direct request terminal drift")
    require(episode["episode_status"] == trade["outcome"], "Episode exit mismatch")
    closed = boolean(trade["closed"])
    require(boolean(episode["completed_trade"]) == boolean(episode["observed"]) == closed, "Episode unknown semantics drift")
    eq(episode["episode_net_return"], trade["net_return"], "Episode realised return drift")
    for key in ("entry_time", "exit_time", "mother_decision_time", "mother_deadline"):
        same(episode[key], trade[key], key)
    require(stamp(episode["occupied_until"]) == stamp(trade["exit_time"] if closed else trade["mother_deadline"]), "Unknown occupancy shortened")


def serial_values(episodes, rows):
    episodes, saved = indexed(episodes), indexed(rows)
    require(episodes.keys() == saved.keys(), "Serial denominator drift")
    free, result = {}, {}
    for key, row in sorted(episodes.items(), key=lambda x: (stamp(x[1]["mother_decision_time"]), x[0])):
        parity(row, saved[key])
        now, fold = stamp(row["mother_decision_time"]), row["fold"]
        accepted = now >= free.get(fold, -10**30)
        require(boolean(saved[key]["portfolio_selected"]) == accepted and saved[key]["portfolio_reason"] ==
            ("accepted_mother" if accepted else "pending_or_position_busy"), "Serial occupancy selection drift")
        if accepted:
            until = stamp(row["occupied_until"])
            free[fold] = (until+5*MINUTE-1)//(5*MINUTE)*(5*MINUTE)
        result[key] = number(row["episode_net_return"], True) if accepted else 0.
    return result


def check_metrics(rows, summary):
    values = [number(r["net_return"]) for r in rows if boolean(r["closed"])]
    eq(summary["events"], len(values), "Closed metric denominator drift")
    eq(summary["mean_net_bp"], bp(mean(values)), "Mean net drift")
    if values:
        eq(summary["win_rate"], sum(v > 0 for v in values)/len(values), "Win rate denominator drift")
        gains, losses = math.fsum(v for v in values if v > 0), -math.fsum(v for v in values if v < 0)
        if losses: eq(summary["profit_factor"], gains/losses, "Profit factor drift")
        eq(summary["extra_10bp_mean_net_bp"], bp(mean(values))-10, "Frozen-event30bp accounting stress drift")


def verify_tables(tables, summary, *, expected_counts=(251, 462, 154)):
    """Pure15-table API; CLI pins251/462/154, fixtures may override counts.

    tables[baseline/candidate] has TABLE_FILES' six keys. Three root keys are
    case_delta/excess_delta/serial_delta. summary is the complete root summary
    with arms/effects. Every original old trade column except the explicitly
    enumerated accounting columns must survive candidate byte/number parity.
    """
    cases_n, controls_n, matched_n = expected_counts
    require(cases_n > 0 and 0 <= matched_n <= cases_n and controls_n == 3*matched_n, "Invalid expected counts")
    require(summary["experiment_id"] == EXPERIMENT_ID and summary["status"] == "diagnostic_only_no_candidate_acceptance", "Wrong experiment/status")
    for flag in ("holdout_consumed", "audit_prices_loaded", "production_eligible", "training_eligible"):
        require(summary[flag] is False, "Unsafe result flag: "+flag)
    state, mapping, serial, partial_counts = {}, None, {}, {}
    for arm in ARMS:
        info, t = summary["arms"][arm], tables[arm]
        require(info["policy"] == (BASE_POLICY if arm == "baseline" else CANDIDATE_POLICY), "More than frozen partial switch changed")
        state[arm] = {k:indexed(t[k]) for k in TABLE_FILES}
        require(len(t["case_trades"]) == cases_n and len(t["control_trades"]) == controls_n, "Original population lost")
        current = {key:(r["parent_event_id"], stamp(r["decision_time"])) for key,r in state[arm]["control_trades"].items()}
        require(len({time for _,time in current.values()}) == controls_n, "Control timestamp reused across directions")
        counts = Counter(parent for parent,_ in current.values())
        require(len(counts) == matched_n and (not counts or set(counts.values()) == {3}) and
            set(counts) <= state[arm]["case_trades"].keys(), "Frozen triples incomplete/foreign")
        require(mapping is None or mapping == current, "Frozen control mapping changed")
        mapping = current
        for population in ("case", "control"):
            trades, episodes = state[arm][population+"_trades"], state[arm][population+"_episodes"]
            require(trades.keys() == episodes.keys(), "Episode denominator lost")
            partial_counts[arm+"/"+population] = 0
            for key, row in trades.items():
                check_trade(row, arm == "candidate")
                check_episode(row, episodes[key])
                partial_counts[arm+"/"+population] += int(number(row["partial_fraction"]) > 0)
                if arm == "candidate":
                    require(key in state["baseline"][population+"_trades"], "Original event identity changed")
                    old = state["baseline"][population+"_trades"][key]
                    parity(old, row, ACCOUNTING_COLUMNS)
                    if number(row["partial_fraction"]) == 0:
                        parity(old, row)
                    elif boolean(row["closed"]):
                        old_net, new_net = number(old["net_return"]), number(row["net_return"])
                        partial_net = number(row["partial_fast_trigger_gross_return"])-.002
                        eq(new_net, .5*old_net+.5*partial_net, "Paired weighted20bp identity failed")
                        require(old_net <= 0 or new_net > 0, "Profitable half converted old net winner to loss")
                        require(old_net >= 0 or new_net > old_net, "Profitable half worsened old net loser")
            check_metrics(list(trades.values()), info["metrics" if population == "case" else "control_metrics"])
        pairs = state[arm]["matched"]
        require(pairs.keys() == state[arm]["case_episodes"].keys(), "Unmatched mothers omitted")
        for key, pair in pairs.items():
            case = state[arm]["case_episodes"][key]
            controls = [state[arm]["control_episodes"][cid] for cid,(parent,_) in mapping.items() if parent == key]
            values = [number(r["episode_net_return"], True) for r in controls]
            cm = mean(values) if len(values) == 3 and None not in values else None
            net = number(case["episode_net_return"], True)
            excess = net-cm if net is not None and cm is not None else None
            for field, value in (("assigned_controls",len(controls)), ("event_net_return",net), ("control_mean_return",cm), ("excess",excess)):
                eq(pair[field], value, "Matched own-cost arithmetic: "+field)
            for field in ("mother_decision_time", "fold"): same(pair[field], case[field], field)
        values = [number(p["excess"], True) for p in pairs.values()]
        known = sum(v is not None for v in values)
        for field, value in (("paired_events",known), ("mother_events",cases_n), ("coverage",known/cases_n), ("mean_excess_bp",bp(mean(values)))):
            eq(info["matching"][field], value, "Matching summary drift: "+field)
        if "assignment_coverage" in info["matching"]: eq(info["matching"]["assignment_coverage"], matched_n/cases_n, "Assignment coverage drift")
        serial[arm] = serial_values(t["case_episodes"], t["single_pending"])
        selected = {r["event_id"] for r in t["single_pending"] if boolean(r["portfolio_selected"])}
        eq(info["serial_selected_mothers"], len(selected), "Serial selected count drift")
        check_metrics([r for r in t["case_trades"] if r["event_id"] in selected], info["single_position"])
        if arm == "candidate":
            for key,r in state[arm]["single_pending"].items():
                old = state["baseline"]["single_pending"][key]
                for field in ("portfolio_selected", "portfolio_reason", "occupied_until"):
                    same(old[field], r[field], field)
    effects = {}
    for name, table, column in (("case_delta", "case_episodes", "episode_net_return"),
            ("excess_delta", "matched", "excess"), ("serial_delta", None, None)):
        rows = indexed(tables[name])
        require(rows.keys() == state["baseline"]["case_episodes"].keys(), "All-mother paired denominator drift")
        values = []
        for key,row in rows.items():
            before, after = ((serial[arm][key] for arm in ARMS) if table is None else
                (number(state[arm][table][key][column], True) for arm in ARMS))
            difference = after-before if before is not None and after is not None else None
            for field,value in (("before",before), ("after",after), ("difference",difference)):
                eq(row[field], value, "Paired effect drift: "+name+"/"+field)
            same(row["mother_decision_time"], state["baseline"]["case_episodes"][key]["mother_decision_time"], "mother_decision_time")
            values.append(difference)
        known = [x for x in values if x is not None]
        derived = {"total_pairs":cases_n, "n":len(known), "unknown_pairs":cases_n-len(known),
            "improved":sum(x > 1e-12 for x in known), "worsened":sum(x < -1e-12 for x in known),
            "unchanged":sum(abs(x) <= 1e-12 for x in known), "mean_bp":bp(mean(known))}
        for field,value in derived.items(): eq(summary["effects"][name][field], value, "Effect summary drift: "+name+"/"+field)
        effects[name] = dict(derived, sum_event_bp=bp(math.fsum(known)) if known else None)
    eq(summary["known_coverage_ceiling"], matched_n/cases_n, "Original support ceiling changed")
    eq(summary["coverage_required"], .9, "Coverage gate weakened")
    if matched_n/cases_n < .9:
        require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False, "Known support failure bypassed")
    groups = {}
    for population in ("case","control"):
        derived = defaultdict(list)
        transitions = Counter()
        for key, old in state["baseline"][population+"_trades"].items():
            new = state["candidate"][population+"_trades"][key]
            a,z = number(old["net_return"],True),number(new["net_return"],True)
            d = z-a if a is not None and z is not None else None
            partial = number(new["partial_fraction"]) == .5
            transition = "flat_or_unknown" if a is None or z is None or a == 0 or z == 0 else \
                ("win" if a > 0 else "loss")+"_to_"+("win" if z > 0 else "loss")
            transitions[transition] += 1
            for label in ("all", "partial" if partial else "no_partial", transition):
                derived[label].append((a,z,d))
        groups[population] = {}
        for label, values in derived.items():
            differences = [d for a,z,d in values if d is not None]
            groups[population][label] = {"n":len(values),"known":len(differences),
                "old_mean_net_bp":bp(mean([a for a,z,d in values])),"new_mean_net_bp":bp(mean([z for a,z,d in values])),
                "mean_delta_bp":bp(mean(differences)),"sum_delta_event_bp":bp(math.fsum(differences)) if differences else None}
        if population in summary.get("mechanics",{}):
            info=summary["mechanics"][population]
            require(info["transitions"] == dict(transitions), "Mechanics transition denominator drift")
            count=partial_counts["candidate/"+population]
            known_partial=derived.get("partial",[])
            expected={"total":len(state["baseline"][population+"_trades"]),"known":groups[population]["all"]["known"],
                "partial_count":count,"unmodified_count":len(state["baseline"][population+"_trades"])-count,
                "partial_improved":sum(d is not None and d>1e-12 for a,z,d in known_partial),
                "partial_hurt":sum(d is not None and d< -1e-12 for a,z,d in known_partial),
                "later_exits":0,"earlier_exits":0,"same_exit_time":len(state["baseline"][population+"_trades"])}
            for field,value in expected.items(): eq(info[field],value,"Mechanics summary drift: "+population+"/"+field)
            saved={row["group"]:row for row in info["groups"]}
            require(len(saved)==len(info["groups"]) and saved.keys()==transitions.keys(),"Mechanics groups missing/duplicate")
            for label,row in saved.items():
                for field,value in groups[population][label].items():eq(row[field],value,"Mechanics group mean/denominator drift")
    return {"status":"passed", "counts":{"cases":cases_n,"controls":controls_n,"matched":matched_n,"unmatched":cases_n-matched_n},
        "effects":effects, "accounting":{"partial_fills":partial_counts,"unchanged_final_paths":cases_n+controls_n,
            "original_cost_fraction":.002,"partial_fraction":.5},"groups":groups, **SCOPE}


def safe_path(root, identity):
    require(isinstance(identity, str) and identity and not Path(identity).is_absolute() and
        all(p not in ("", ".", "..") for p in identity.split("/")), "Unsafe evidence identity")
    result = Path(root)/identity
    require(result.resolve().is_relative_to(Path(root).resolve()) and not result.is_symlink(), "Evidence escapes root or symlink")
    require(not identity.startswith("data/"), "Raw data reading forbidden")
    return result


def sha(path):
    require(path.is_file() and not path.is_symlink(), "Missing/symlink evidence: "+str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    require(path.is_file() and not path.is_symlink(), "Missing saved CSV")
    with (gzip.open if path.suffix == ".gz" else open)(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames), "Duplicate/missing CSV header")
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows), "Ragged CSV")
    return rows


def read_json(path):
    require(path.is_file() and not path.is_symlink(), "Missing saved JSON")
    return parse_json(path.read_text(encoding="utf-8"))


def load_tables(results):
    """Exactly15 saved outcome CSVs; no strategy/helper imports."""
    answer = {arm:{key:read_csv(Path(results)/arm/name) for key,name in TABLE_FILES.items()} for arm in ARMS}
    answer.update({name:read_csv(Path(results)/(name+".csv")) for name in DELTAS})
    return answer


def verify_sources(root, results, summary):
    """Verify every declared output/source SHA; git-show the recorded commit."""
    root, results = Path(root), Path(results)
    started = read_json(results/"started.json")
    require(started["sources"] == summary["sources"] and started["sources"], "Source receipts differ/empty")
    commit = started["builder_commit"]
    require(re.fullmatch(r"[a-f0-9]{40}", commit) is not None, "Invalid builder commit")
    sources = started["sources"]
    identities = [row["path"] for row in sources]
    require(len(identities) == len(set(identities)), "Duplicate source identity")
    experiment_identity = str(results.parent.relative_to(root))
    required = {"yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_dual_partial_research.py",
        experiment_identity+"/config.json", experiment_identity+"/PROJECT_PLAN.md"}
    require(required <= set(identities), "Missing essential pinned source")
    for item in sources:
        safe_path(root, item["path"])
        try:
            saved = subprocess.run(["git", "show", commit+":"+item["path"]], cwd=root, check=True, capture_output=True).stdout
        except subprocess.CalledProcessError as error:
            raise VerificationError("Pinned source unavailable; cannot skip") from error
        require(hashlib.sha256(saved).hexdigest() == item["sha256"], "Committed source hash drift: "+item["path"])
    try:
        when = subprocess.run(["git", "show", "-s", "--format=%ct", commit], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise VerificationError("Builder timestamp unavailable") from error
    require(re.fullmatch(r"\d+", when) is not None and int(when)*10**9 <= stamp(started["at"]), "Builder committed after study start")
    hashes = summary["output_hashes"]
    actual = {str(p.relative_to(results)) for p in results.rglob("*") if p.is_file()}
    require(actual == set(hashes) | {"summary.json"}, "Incomplete output hash inventory")
    for identity,digest in hashes.items(): require(sha(safe_path(results, identity)) == digest, "Output hash drift: "+identity)
    config = read_json(results.parent/"config.json")
    require(config["experiment_id"] == EXPERIMENT_ID and config["policies"] == [BASE_POLICY,CANDIDATE_POLICY], "Frozen config drift")
    require(sha(results.parent/"config.json") == summary["config_sha256"], "Config byte hash drift")
    require(next(s["sha256"] for s in sources if s["path"] == experiment_identity+"/config.json") == summary["config_sha256"],
        "Current config differs from committed configuration")
    for directory,key in ((config["parent_results"],"inputs"), (config["mother_results"],"mother_inputs"),
            (config["entry_context_results"],"entry_context_inputs")):
        require(config[key] == summary[key], "Frozen saved input receipt drift")
        for name,digest in config[key].items(): require(sha(safe_path(root,directory+"/"+name)) == digest, "Original saved input hash drift")
    source = summary["source"]
    base_path=safe_path(root,config["base_config"])
    require(sha(base_path)==config["base_config_sha256"],"Frozen base hash drift")
    base=read_json(base_path)
    require(base["execution"]["cost_fraction"]==.002 and base["execution"]["max_hours"]==72 and
        base["execution"]["stop_first"] is True,"Original economics changed")
    require(base["development_folds"]==[[fold,a,z] for fold,(a,z) in FOLDS.items()],"Development fold drift")
    require(source["sha256"]==base["source"]["sha256"],"Raw source identity receipt drift")
    require(source["holdout_price_rows"] == 0 and stamp(source["phase_price_last_open"]) < stamp("2025-01-01T00:00:00Z"), "Source receipt exceeds2023–24")
    return {"committed_sources_verified":len(sources), "output_hashes_verified":len(hashes), "builder_commit":commit,
        "source_pins":sources, "output_hashes":hashes}


def verify_contexts(native, fast, tables):
    """Saved source and executor init parity; not independent rolling40 proof."""
    parts=defaultdict(list)
    for row in native:
        key=(row["arm"],row["population"])
        require(key in {(a,p) for a in ARMS for p in ("case","control")},"Foreign native context")
        parts[key].append(row)
    fast_parts=defaultdict(list)
    for row in fast:
        require(row["population"] in ("case","control"),"Foreign fast context")
        fast_parts[row["population"]].append(row)
    counts=Counter()
    for arm in ARMS:
        for population in ("case","control"):
            rows, trades=indexed(parts[(arm,population)]),indexed(tables[arm][population+"_trades"])
            require(rows.keys()==trades.keys(),"Native frozen context denominator drift")
            if arm=="candidate":
                old=indexed(parts[("baseline",population)])
                for key,row in rows.items():parity({k:v for k,v in old[key].items() if k!="arm"},row)
            for key,row in rows.items():
                counts[(arm,population,row["mg_entry_state"])]+=1
                check_context(row,trades[key],15,fast=False)
    for population in ("case","control"):
        rows,trades=indexed(fast_parts[population]),indexed(tables["candidate"][population+"_trades"])
        require(rows.keys()==trades.keys(),"Fast frozen context denominator drift")
        for key,row in rows.items():check_context(row,trades[key],5,fast=True)
    return counts


def check_context(row, trade, minutes, *, fast):
    for field in ("event_id","decision_time","direction"):same(row[field],trade[field],field)
    state=row["mg_entry_state"]; known=boolean(row["mg_entry_known"])
    require(state in ("aligned","opposite","unknown") and known==(state!="unknown"),"Frozen unknown context filled")
    eq(row["mg_entry_native_minutes"],minutes,"Wrong native source memory")
    prefix="partial_fast_initial_" if fast else "transition_initial_"
    require(trade[prefix+"state"]==state and trade[prefix+"reason"]==row["mg_entry_reason"],"Frozen/helper executor seed mismatch")
    if not fast:
        for field in row:
            if field.startswith("mg_entry_"):
                require(field in trade,"Frozen native source field lost")
                same(row[field],trade[field],field)
    side=number(row["mg_entry_side"],True)
    if known:
        start=stamp(row["decision_time"])
        source={"open_time":row["mg_entry_bar_open"],"side":side,"ma":row["mg_entry_ma"],"hl2":row["mg_entry_hl2"],
            "management_segment_id":row["mg_entry_management_segment_id"],"raw_segment_id":row["mg_entry_raw_segment_id"]}
        valid_source(source,start,minutes)
        require(stamp(row["mg_entry_available_at"])==start and row["mg_entry_reason"]=="valid","Wrong exact entry source availability")
        aligned=number(row["direction"])*side>0
        require(boolean(row["mg_entry_aligned"])==aligned and state==("aligned" if aligned else "opposite"),"Own seed alignment drift")
        eq(trade[prefix+"side"],side,"Frozen seed side drift")
        same(trade[prefix+"open_time"],row["mg_entry_bar_open"],"open_time")
        if fast:
            same(trade[prefix+"available_at"],row["mg_entry_available_at"],"available_at")
            for field in ("ma","hl2","management_segment_id","raw_segment_id"):
                same(trade[prefix+field],source[field],field)
    else:
        require(side is None and row["mg_entry_aligned"] in (None,"") and row["mg_entry_reason"]!="valid", "Unknown seed has side/alignment")
        require(number(trade[prefix+"side"],True) is None and stamp(trade[prefix+"open_time"],True) is None,"Unknown executor seed filled")


def verify_saved_lineage(root, results, tables, summary):
    """Saved V15 all-column anchor and contexts/receipts, without raw reads."""
    config=read_json(results.parent/"config.json")
    anchor=read_json(results/"anchor_parity.json")
    require(config["parent_results"]=="experiments/active/exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15/results/candidate", "Wrong native15 parent")
    for table,filename in TABLE_FILES.items():
        rows=read_csv(safe_path(root,config["parent_results"]+"/"+filename))
        old,new=indexed(rows),indexed(tables["baseline"][table])
        require(old.keys()==new.keys(),"V15 anchor identities changed")
        for key,row in old.items():parity(row,new[key])
        eq(anchor[table]["rows"],len(rows),"Anchor receipt count drift")
        eq(anchor[table]["columns"],len(rows[0]),"Anchor receipt column drift")
    for population in ("case","control"):
        context=read_csv(results/(population+"_context.csv.gz"))
        upstream=read_csv(safe_path(root,config["entry_context_results"]+"/direct_k1_stop_"+population+"_context.csv.gz"))
        original=read_csv(safe_path(root,config["mother_results"]+"/"+("original_mothers" if population=="case" else "control_mothers")+".csv.gz"))
        current=indexed(context)
        for source in (upstream,original):
            rows=indexed(source);require(rows.keys()==current.keys(),"Mother/context identities changed")
            for key,row in rows.items():parity(row,current[key])
        for arm in ARMS:
            executed=indexed(tables[arm][population+"_trades"])
            for key,row in current.items():parity(row,executed[key])
    assignments=read_csv(results/"assignments.csv")
    upstream=indexed(read_csv(safe_path(root,config["mother_results"]+"/assignments.csv")))
    current=indexed(assignments)
    require(upstream.keys()==current.keys()==indexed(tables["baseline"]["case_trades"]).keys(),"Original assignments changed")
    for key,row in upstream.items():parity(row,current[key])
    require({key for key,row in current.items() if row["match_status"]=="matched"}==
        {row["parent_event_id"] for row in tables["baseline"]["control_trades"]},"Original154 support changed")
    native,fast=read_csv(results/"native_entry_context.csv.gz"),read_csv(results/"fast_entry_context.csv.gz")
    counts=verify_contexts(native,fast,tables)
    frozen,started=read_json(results/"context_frozen.json"),read_json(results/"started.json")
    require(stamp(frozen["at"])>=stamp(started["at"]),"Context receipt predates study start")
    require(frozen["before_outcome_reads"] is True and frozen["outcomes_hashed_or_read"] is False and frozen["entry_gates"] is False,
        "Context freeze lost pre-outcome/no-selection declaration")
    eq(frozen["rows"],len(native),"Frozen native count drift");eq(frozen["fast_rows"],len(fast),"Frozen fast count drift")
    require(frozen["context_sha256"]==sha(results/"native_entry_context.csv.gz") and
        frozen["fast_context_sha256"]==sha(results/"fast_entry_context.csv.gz"),"Frozen context SHA mismatch")
    for declared in (frozen["counts"],summary["native_context"],read_csv(results/"native_initial_state_counts.csv")):
        saved={}
        for row in declared:
            key=(row["arm"],row["population"],row["mg_entry_state"])
            require(key not in saved,"Duplicate context state count")
            saved[key]=number(row["n"])
        require(saved==dict(counts),"Context aggregate denominator drift")
    # Every denormalized fast-edge row must match the trade's canonical JSON.
    expected={}
    for population in ("case","control"):
        for trade in tables["candidate"][population+"_trades"]:
            for event in parse_json(trade["partial_fast_events"]):
                expected[(population,trade["event_id"],stamp(event["available_at"]))]=event
    edges=read_csv(results/"partial_fast_edges.csv.gz");seen=set()
    for row in edges:
        key=(row["population"],row["event_id"],stamp(row["available_at"]))
        require(key in expected and key not in seen,"Foreign/duplicate flat edge")
        seen.add(key)
        event=expected[key]
        for field,value in event.items():
            if field in ("previous_fast","current_fast","slow"):
                actual=parse_json(row[field]);require(actual.keys()==value.keys(),"Flat edge source shape changed")
                parity(value,actual)
            elif type(value) is bool:require(boolean(row[field])==value,"Flat edge boolean differs")
            else:same(row[field],value,field)
    require(seen==expected.keys(),"Flat edge export omitted events")
    return {"anchor_tables":6,"native_context_rows":len(native),"fast_context_rows":len(fast),
        "recorded_fast_edges":len(edges),"context_freeze_is_saved_receipt_not_runtime_trace":True}


def verify(results, summary_path=None, *, root=ROOT):
    results = Path(results).resolve()
    summary_path = Path(summary_path).resolve() if summary_path else results/"summary.json"
    summary = read_json(summary_path)
    require(summary == read_json(results/"summary.json"), "Supplied summary differs from immutable root summary")
    require(not (results/"failure.json").exists(), "Failed attempt cannot pass")
    receipt = verify_sources(root, results, summary)
    for arm in ARMS: require(read_json(results/arm/"summary.json") == summary["arms"][arm], "Arm/root summaries differ")
    tables=load_tables(results)
    output = verify_tables(tables, summary)
    output["lineage"]=verify_saved_lineage(Path(root),results,tables,summary)
    output.update(receipt, summary_sha256=sha(summary_path), verifier_sha256=sha(Path(__file__)),
        verifier_source="scripts/verify_hourly_impulse_dual_partial_v16.py")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        if args.out:
            require(not args.out.exists() and not args.out.resolve().is_relative_to(args.results.resolve()), "Use a new receipt outside immutable results")
        output = verify(args.results, args.summary)
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as error:
        output = {"status":"failed","error":str(error), **SCOPE}
    text = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    if args.out and output["status"] == "passed":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if output["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
