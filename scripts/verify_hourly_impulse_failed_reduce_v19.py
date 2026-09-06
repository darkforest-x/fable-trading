"""Independent V19 saved-ledger audit; never imports strategy or raw prices.

The independently checked V18 baseline supplies the unchanged two-bar causal
decision prefix. Only its confirmed full fills may become original-notional
50% risk reductions, at the identical quote/time. The remainder is independently
accounted and serial occupancy is recomputed per arm. Known partial proceeds
never make a censored whole position known. Recorded evidence cannot prove that
unlogged edges are absent or independently recompute source SMA/market fills.

Python 3.9 contracts: https://docs.python.org/3.9/library/decimal.html (construct
from saved quote strings, not binary floats), https://docs.python.org/3.9/library/
csv.html#csv.DictReader and https://docs.python.org/3.9/library/importlib.html
#importing-a-source-file-directly. Local dependency closure is V19/V18/V17/V16
saved verifiers only; no strategy functions, global monkeypatches or raw replay.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess


_SPEC = importlib.util.spec_from_file_location("_v19_saved_v18", Path(__file__).with_name("verify_hourly_impulse_failed_confirm_v18.py"))
v18 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(v18)
v17, h = v18.v17, v18.h
VerificationError = h.VerificationError
require, number, eq, boolean, stamp = h.require, h.number, h.eq, h.boolean, h.stamp
indexed, parity, same, mean, bp = h.indexed, h.parity, h.same, h.mean, h.bp
read_json, read_csv, sha, safe_path = h.read_json, h.read_csv, h.sha, h.safe_path
ROOT, ARMS, TABLE_FILES, DELTAS, MINUTE, HOUR = h.ROOT, h.ARMS, h.TABLE_FILES, h.DELTAS, h.MINUTE, h.HOUR
EXPERIMENT_ID = "exp-btcusdtp-1h-failed-reduce-preholdout-20260906-v19"
BASE_POLICY = dict(v18.CANDIDATE_POLICY)
CANDIDATE_POLICY = dict(BASE_POLICY, id="15m_native40_failed_reduce_half", fast_failed_launch_fraction=.5)
PARENT = "experiments/active/exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18/results/candidate"
STRUCTURE_REFERENCE = "experiments/active/exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16/results/candidate"
STRUCTURE_COLUMNS = ["event_id", "exit_time", "exit_price", "outcome", "closed", "hold_minutes", "max_favourable_r", "max_adverse_r"]
SCOPE = dict(v18.SCOPE, limitation="Saved V18 decision prefixes and V19 quotes, logged sources, weighted two-leg accounting, fixed pairs and serial occupancy only; not raw replay/SMA, proof of no omitted edges, independent inference, live fills or profitability.")
REDUCE_FIELDS = {"failed_reduce_" + name for name in (
    "enabled", "target_fraction", "role", "fill_count", "status", "fraction", "fill_time", "fill_price",
    "full_notional_gross_return", "realised_gross_return", "realised_net_return")}
FILL_OBSERVATION_FIELDS = {"fill_action", "fill_fraction", "fill_price", "fill_available_at"}
EPISODE_IDENTITY_FIELDS = {"event_id", "fold", "signal_time", "decision_time", "direction",
    "initial_stop", "signal_atr", "signal_close", "mother_decision_time", "mother_deadline",
    "entry_time", "exit_time"}
EPISODE_OWNED_FIELDS = {"status", "episode_status", "episode_net_return", "executed", "completed_trade",
    "observed", "terminal_time", "occupied_until"}


def check_episode_identity(trade, episode):
    """Anchor ledger metadata before using its fold for serial occupancy.

    The frozen direct-request runner preserves the original mother columns in
    both outputs. Only the episode builder's explicitly owned lifecycle fields
    have distinct meanings; every other shared field must describe the same
    request/execution. Internal episode/single/matched agreement alone cannot
    establish that identity. This is saved-ledger parity, not a price replay.
    """
    require(EPISODE_IDENTITY_FIELDS <= trade.keys() and EPISODE_IDENTITY_FIELDS <= episode.keys(),
            "Missing original episode/trade identity column")
    for field in sorted((trade.keys() & episode.keys()) - EPISODE_OWNED_FIELDS):
        same(trade[field], episode[field], field)


def check_reduce_diagnostics(row, reduced):
    require(REDUCE_FIELDS <= row.keys() and boolean(row["failed_reduce_enabled"]), "Missing/disabled risk-reduction evidence")
    require(row["failed_reduce_role"] == "risk_reduction", "Risk reduction was mislabelled as profitable TP")
    for field, value in (("target_fraction", .5), ("fill_count", int(reduced)), ("fraction", .5 if reduced else 0)):
        eq(row["failed_reduce_" + field], value, "Original risk-reduction fraction/count drift")
    closed = boolean(row["closed"])
    status = ("risk_reduced_closed" if closed else "risk_reduced_censored") if reduced else "not_reduced_exit" if closed else "unknown_source"
    require(row["failed_reduce_status"] == status, "Risk-reduction observability status drift")
    if not reduced:
        for key in ("fill_time", "fill_price", "full_notional_gross_return"):
            require((stamp if key == "fill_time" else number)(row["failed_reduce_" + key], True) is None,
                    "Unexecuted reduction retained invented fill")
        for key in ("realised_gross_return", "realised_net_return"):
            eq(row["failed_reduce_" + key], 0, "Unexecuted reduction retained proceeds")


def check_reduced_accounting(row):
    """Two saved quotes, own original R, one total20bp cost, unknown remainder."""
    d, entry, stop, atr = (number(row[k]) for k in ("direction", "entry_price", "initial_stop", "signal_atr"))
    require(d in (-1, 1) and min(entry, stop, atr) > 0 and d * (entry-stop) > 0, "Invalid original entry risk")
    risk = d * (entry-stop)
    eq(row["risk_pct"], risk/entry, "Original risk_pct rebased")
    eq(row["risk_atr"], risk/atr, "Original risk_atr rebased")
    start, fill, end = (stamp(row[k]) for k in ("entry_time", "partial_exit_time", "exit_time"))
    require(start == stamp(row["decision_time"]) == stamp(row["mother_decision_time"]) and start % HOUR == 0 and
            stamp(row["signal_time"]) + HOUR == start and stamp(row["mother_deadline"]) == start + 72*HOUR,
            "Original hourly entry/deadline changed")
    require(row["fold"] in h.FOLDS, "Unknown development fold")
    lo, hi = (stamp(t+"T00:00:00Z") for t in h.FOLDS[row["fold"]])
    require(lo <= start < hi-72*HOUR and start < fill <= end <= start+72*HOUR and end <= hi and
            fill % (5*MINUTE) == 0 and end % (5*MINUTE) == 0, "Reduction/remainder outside held native5 clock")
    eq(row["hold_minutes"], (end-start)/MINUTE, "Remainder hold clock drift")
    closed, outcome = boolean(row["closed"]), row["outcome"]
    require(outcome in ({"hard_stop", "hard_stop_gap", "transition_colour_exit", "time_exit"} if closed else
                       {"data_gap_censored", "right_censored"}), "Reduction invented a full fast terminal")
    require(not closed or end > fill, "Known remainder exited before another completed bar")
    price, quote = number(row["exit_price"]), number(row["partial_exit_price"])
    require(min(price, quote) > 0 and d*(quote-stop) > 0, "Reduction displaced original gap stop")
    if outcome == "hard_stop": eq(price, stop, "Remainder hard stop moved")
    if outcome == "hard_stop_gap": require(d*(price-stop) <= 1e-12, "Gap stop improved fill")
    if outcome == "time_exit": require(end == start+72*HOUR, "Remainder deadline moved")
    trigger = [stamp(row[k], True) for k in ("transition_trigger_previous_open_time", "transition_trigger_open_time", "transition_trigger_available_at")]
    if outcome == "transition_colour_exit":
        p, c, a = trigger
        require(None not in trigger and p+15*MINUTE == c and c+15*MINUTE == a == end and c >= start and
                d*(price-stop) > 0, "Remainder native15 true-transition clock/priority drift")
    else: require(all(t is None for t in trigger), "Other remainder exit invented slow trigger")
    for field, value in (("partial_fraction", .5), ("exit_remaining_fraction", .5), ("partial_fast_fill_count", 0),
                         ("partial_fast_realised_net_return", 0), ("failed_launch_count", 0)):
        eq(row[field], value, "Duplicate fast leg or fraction drift: " + field)
    first = v18.exact_gross(row, row["partial_exit_price"])
    last = v18.exact_gross(row, row["exit_price"])
    require(first <= Decimal(".002"), "Profitable quote was incorrectly risk-reduced")
    # Match the frozen exact-quote, then weighted-float arithmetic; never epsilon
    # classify a tiny real loss as zero. The exact20bp/20bp case is strictly zero.
    realised, gross = .5*float(first), .5*float(first)+.5*float(last)
    for field, value in (("failed_reduce_full_notional_gross_return", float(first)),
                         ("failed_reduce_realised_gross_return", realised), ("failed_reduce_realised_net_return", realised-.001),
                         ("realised_partial_gross_return", realised), ("marked_gross_return", gross), ("marked_net_return", gross-.002)):
        eq(row[field], value, "Two-leg original-notional accounting drift: " + field)
        if field.endswith("net_return"):
            require((number(row[field]) > 0)-(number(row[field]) < 0) == (value > 0)-(value < 0),
                    "Tolerance changed the sign of a real leg/whole return")
    for field, value in (("gross_return", gross), ("net_return", gross-.002), ("net_r", (gross-.002)/(risk/entry))):
        eq(row[field], value if closed else None, "Unknown remainder became a known whole trade: " + field)
        if closed and field in ("net_return", "net_r"):
            require((number(row[field]) > 0)-(number(row[field]) < 0) == (value > 0)-(value < 0),
                    "Tolerance changed the sign of a real closed return")
    if first == last == Decimal(".002"):
        require(number(row["marked_net_return"]) == 0 and (not closed or number(row["net_return"]) == number(row["net_r"]) == 0),
                "Two exact20bp fills became a floating winner")
    if first == Decimal(".002"):
        require(number(row["failed_reduce_realised_net_return"]) == 0, "Exact20bp reduced half is not exact zero")
    f, a = number(row["max_favourable_r"]), number(row["max_adverse_r"])
    excursions = [d*(p-entry)/risk for p in (price, quote)]
    require(f >= max(0, *excursions)-1e-12 and a <= min(0, *excursions)+1e-12, "Held excursions omit known quote")
    if "funding_modelled" in row: require(not boolean(row["funding_modelled"]), "Funding changed")


def check_candidate(old, new):
    """Compare against an independently verified V18 decision, not new labels."""
    require(old.keys() <= new.keys(), "Candidate lost old columns")
    reduced = old["outcome"] == "fast_failed_launch"
    check_reduce_diagnostics(new, reduced)
    if not reduced:
        parity(old, new)
        return {k: number(new["failed_confirm_"+f]) for k, f in
                (("created", "create_count"), ("cancelled", "cancel_count"), ("terminated", "priority_termination_count"), ("confirmed", "confirm_count"))}
    require(boolean(old["closed"]) and number(old["failed_confirm_confirm_count"]) == 1, "Baseline full lacks known confirmation")
    mutable = v17.MUTABLE_FIELDS | {"partial_fast_events", "partial_fast_flip_count", "partial_fast_status", "partial_fast_reset_count",
        "partial_fast_last_reset_reason", "failed_launch_count", "failed_launch_status", "failed_confirm_status", "failed_confirm_events"}
    mutable |= {key for key in old if key.startswith("transition_") and not key.startswith("transition_initial_")}
    parity(old, new, exceptions=mutable)
    fill, end = stamp(old["exit_time"]), stamp(new["exit_time"])
    require(end >= fill, "Remainder ended before original confirmation")
    for key in ("partial_exit_time", "failed_reduce_fill_time"):
        require(stamp(new[key]) == fill, "Risk half used another confirmation clock")
    for key in ("partial_exit_price", "failed_reduce_fill_price"):
        eq(new[key], old["exit_price"], "Risk half used another confirmation quote")
    require(number(new["max_favourable_r"])+1e-12 >= number(old["max_favourable_r"]) and
            number(new["max_adverse_r"]) <= number(old["max_adverse_r"])+1e-12, "Extended path lost held excursions")
    require(number(new["partial_fast_reset_count"]) >= number(old["partial_fast_reset_count"]), "Extended path lost resets")
    before, after = v18.lifecycle(old), v18.lifecycle(new)
    require(len(before) == len(after), "Risk half allowed another pending lifecycle")
    confirmed = None
    for a, z in zip(before, after):
        require(a.keys() == z.keys(), "Lifecycle record schema drift")
        if a["action"] != "confirmed": require(a == z, "Pre-reduction lifecycle changed"); continue
        confirmed = z
        require({k: val for k, val in a.items() if k != "observation"} ==
                {k: val for k, val in z.items() if k != "observation"}, "Confirmation first edge/clock changed")
        x, y = a["observation"], z["observation"]
        require(y.keys() == x.keys() | FILL_OBSERVATION_FIELDS, "Risk fill evidence schema drift")
        require({k: y[k] for k in x} == x, "Confirmed colour/quote predicate changed")
        require(y["fill_action"] == "risk_reduce" and number(y["fill_fraction"]) == .5 and
                stamp(y["fill_available_at"]) == fill, "Confirmation invented another fill/flip")
        eq(y["fill_price"], old["exit_price"], "Confirmation fill quote drift")
    require(confirmed is not None, "Missing second confirmation")
    old_edges, edges = v17.events(old), v17.events(new)
    prefix = [e for e in edges if stamp(e["available_at"]) <= fill]
    require(prefix == old_edges, "Original recorded true-edge prefix changed")
    seen = {}
    for record in before:
        if record["observation"] is not None:
            obs = record["observation"]
            if number(obs["current_fast"]["side"], True) is not None:
                v18.observed_source(seen, obs["current_fast"], stamp(obs["available_at"]), 5)
    previous, direction = fill, number(new["direction"])
    for edge in edges[len(prefix):]:
        now = stamp(edge["available_at"])
        require(previous < now <= end and now < stamp(new["mother_deadline"]), "Post-reduction edge order drift")
        require(now < end or not boolean(new["closed"]), "Fast edge displaced higher-priority terminal")
        previous = now
        p, c = edge["previous_fast"], edge["current_fast"]
        ps, cs = v18.observed_source(seen, p, now-5*MINUTE, 5), v18.observed_source(seen, c, now, 5)
        require(direction*ps > 0 and direction*cs < 0 and p["management_segment_id"] == c["management_segment_id"] and
                p["raw_segment_id"] == c["raw_segment_id"], "Post-reduction invented/reset-crossing edge")
        v18.slow_snapshot(new, edge, now, seen, c["raw_segment_id"])
        v18.quote_check(new, edge)
        eq(edge["profit_threshold"], .002, "Post-reduction economics changed")
        require(edge["action"] == "already_partial", "A second fast fill/pending consumed remaining half")
    eq(new["partial_fast_flip_count"], len(edges), "Confirmation was counted as a new flip")
    status = "risk_reduced_closed" if boolean(new["closed"]) else "risk_reduced_censored"
    require(new["partial_fast_status"] == new["failed_launch_status"] == status and
            new["failed_confirm_status"] == ("confirmed_reduced_closed" if boolean(new["closed"]) else "confirmed_reduced_censored"),
            "Risk half/full/profit status roles drift")
    check_reduced_accounting(new)
    if boolean(new["closed"]):
        eq(number(new["net_return"])-number(old["net_return"]),
           .5*(float(v18.exact_gross(new, new["exit_price"]))-number(old["gross_return"])),
           "Reduced-pair delta does not close to half the remainder change")
    return {k: number(new["failed_confirm_"+f]) for k, f in
            (("created", "create_count"), ("cancelled", "cancel_count"), ("terminated", "priority_termination_count"), ("confirmed", "confirm_count"))}


def verify_tables(tables, summary, *, expected_counts=(251, 462, 154)):
    """Pure fifteen-table API; default/CLI pin all251/462/154/97 identities."""
    n, control_n, matched_n = expected_counts
    require(n > 0 and 0 <= matched_n <= n and control_n == 3 * matched_n, "Invalid expected original cohort")
    require(summary["experiment_id"] == EXPERIMENT_ID and summary["status"] == "diagnostic_only_no_candidate_acceptance", "Wrong V19 experiment/status")
    for flag in ("holdout_consumed", "audit_prices_loaded", "production_eligible", "training_eligible"):
        require(summary[flag] is False, "Unsafe result flag: " + flag)
    states, mapping, serial, counts, confirmation_counts, reductions = {}, None, {}, {}, {}, {}
    for arm in ARMS:
        data, info = tables[arm], summary["arms"][arm]
        require(info["policy"] == (BASE_POLICY if arm == "baseline" else CANDIDATE_POLICY), "More than confirmed fill fraction changed")
        states[arm] = {key: indexed(data[key]) for key in TABLE_FILES}
        require(len(data["case_trades"]) == n and len(data["control_trades"]) == control_n, "Original population omitted")
        current = {key: (row["parent_event_id"], stamp(row["decision_time"])) for key, row in states[arm]["control_trades"].items()}
        require(len({when for _, when in current.values()}) == control_n, "Control timestamp reused")
        parents = Counter(parent for parent, _ in current.values())
        require(len(parents) == matched_n and (not parents or set(parents.values()) == {3}) and set(parents) <= states[arm]["case_trades"].keys(), "Fixed triples incomplete/foreign")
        require(mapping is None or mapping == current, "Frozen triple mapping changed")
        mapping = current
        for population in ("case", "control"):
            trades, episodes = states[arm][population + "_trades"], states[arm][population + "_episodes"]
            require(trades.keys() == episodes.keys(), "Original episode denominator changed")
            counts[arm + "/" + population] = 0
            reductions[arm + "/" + population] = 0
            confirmation_counts[arm + "/" + population] = dict(created=0, cancelled=0, terminated=0, confirmed=0)
            for key, row in trades.items():
                check_episode_identity(row, episodes[key])
                if population == "control":
                    eq(row["direction"], states[arm]["case_trades"][row["parent_event_id"]]["direction"], "Control own assigned direction changed")
                if arm == "baseline":
                    require(not any(k.startswith("failed_reduce_") for k in row), "Baseline contains candidate-only fields")
                    checked = v18.check_candidate(row)
                    v18.check_accounting(row)
                else:
                    require(key in states["baseline"][population + "_trades"], "Candidate identity changed")
                    require(states["baseline"][population + "_episodes"][key].keys() <= episodes[key].keys(),
                            "Candidate episode lost original column")
                    old = states["baseline"][population + "_trades"][key]
                    checked = check_candidate(old, row)
                    reductions[arm + "/" + population] += int(number(row["failed_reduce_fill_count"]))
                for name, value in checked.items():
                    confirmation_counts[arm + "/" + population][name] += value
                counts[arm + "/" + population] += int(row["outcome"] == "fast_failed_launch")
                h.check_episode(row, episodes[key])
            h.check_metrics(list(trades.values()), info["metrics" if population == "case" else "control_metrics"])
        pairs = states[arm]["matched"]
        require(pairs.keys() == states[arm]["case_episodes"].keys(), "Matching lost original unknowns")
        for key, pair in pairs.items():
            case = states[arm]["case_episodes"][key]
            controls = [states[arm]["control_episodes"][cid] for cid, (parent, _) in mapping.items() if parent == key]
            vals = [number(row["episode_net_return"], True) for row in controls]
            cm = mean(vals) if len(vals) == 3 and None not in vals else None
            net = number(case["episode_net_return"], True)
            excess = net - cm if net is not None and cm is not None else None
            for field, value in (("assigned_controls", len(controls)), ("event_net_return", net), ("control_mean_return", cm), ("excess", excess)):
                eq(pair[field], value, "Matched own-cost arithmetic drift: " + field)
            for field in ("mother_decision_time", "fold"):
                same(pair[field], case[field], field)
        vals = [number(row["excess"], True) for row in pairs.values()]
        for field, value in (("paired_events", sum(v is not None for v in vals)), ("mother_events", n),
                             ("coverage", sum(v is not None for v in vals) / n), ("mean_excess_bp", bp(mean(vals)))):
            eq(info["matching"][field], value, "Matching summary drift")
        if "assignment_coverage" in info["matching"]:
            eq(info["matching"]["assignment_coverage"], matched_n / n, "Support drift")
        serial[arm] = h.serial_values(data["case_episodes"], data["single_pending"])
        selected = {row["event_id"] for row in data["single_pending"] if boolean(row["portfolio_selected"])}
        eq(info["serial_selected_mothers"], len(selected), "Per-arm serial count drift")
        h.check_metrics([r for r in data["case_trades"] if r["event_id"] in selected], info["single_position"])
    effects = {}
    for name, table, column in (("case_delta", "case_episodes", "episode_net_return"), ("excess_delta", "matched", "excess"), ("serial_delta", None, None)):
        rows = indexed(tables[name])
        require(rows.keys() == states["baseline"]["case_episodes"].keys(), "Paired all-mother denominator lost")
        values = []
        for key, row in rows.items():
            a, z = ((serial[arm][key] for arm in ARMS) if table is None else (number(states[arm][table][key][column], True) for arm in ARMS))
            difference = z - a if a is not None and z is not None else None
            for field, value in (("before", a), ("after", z), ("difference", difference)):
                eq(row[field], value, "Paired original opportunity arithmetic drift: " + name)
            same(row["mother_decision_time"], states["baseline"]["case_episodes"][key]["mother_decision_time"], "mother_decision_time")
            values.append(difference)
        known = [v for v in values if v is not None]
        derived = dict(total_pairs=n, n=len(known), unknown_pairs=n-len(known), mean_bp=bp(mean(known)),
                       improved=sum(v > 1e-12 for v in known), worsened=sum(v < -1e-12 for v in known), unchanged=sum(abs(v) <= 1e-12 for v in known))
        for field, value in derived.items():
            eq(summary["effects"][name][field], value, "Paired summary denominator/mean drift: " + name)
        effects[name] = dict(derived, sum_event_bp=bp(math.fsum(known)) if known else None)
    groups = {population: paired_groups(states["baseline"][population + "_trades"], states["candidate"][population + "_trades"])
              for population in ("case", "control")}
    for population, output in groups.items():
        if population in summary.get("mechanics", {}):
            check_mechanic_summary(states["baseline"][population + "_trades"], states["candidate"][population + "_trades"],
                                   output, summary["mechanics"][population])
    eq(summary["known_coverage_ceiling"], matched_n / n, "Original support ceiling changed")
    eq(summary["coverage_required"], .9, "Coverage gate weakened")
    if matched_n / n < .9:
        require(summary["gates"]["matched_coverage"] is False and summary["all_financial_gates_pass"] is False, "Known coverage failure bypassed")
    return dict(status="passed", counts=dict(cases=n, controls=control_n, matched=matched_n, unmatched=n-matched_n), effects=effects,
                accounting=dict(failed_launch_exits=counts, confirmation_lifecycle=confirmation_counts, risk_reductions=reductions,
                                original_cost_fraction=.002, partial_fraction=.5, serial_recomputed=True), groups=groups, **SCOPE)

def paired_groups(old, new):
    require(old.keys() == new.keys(), "Paired group population drift")
    groups, transitions = defaultdict(list), Counter()
    for key, a in old.items():
        z = new[key]
        before, after = number(a["net_return"], True), number(z["net_return"], True)
        delta = after - before if before is not None and after is not None else None
        transition = "flat_or_unknown" if before is None or after is None or before == 0 or after == 0 else ("win" if before > 0 else "loss") + "_to_" + ("win" if after > 0 else "loss")
        transitions[transition] += 1
        pair = (before, after, delta) if delta is not None else (None, None, None)
        for label in ("all", "baseline_failed" if a["outcome"] == "fast_failed_launch" else "unchanged", transition):
            groups[label].append(pair)
    result = {}
    for label, rows in groups.items():
        changes = [d for _, _, d in rows if d is not None]
        result[label] = dict(n=len(rows), known=len(changes), old_mean_net_bp=bp(mean([a for a, _, _ in rows])),
                             new_mean_net_bp=bp(mean([z for _, z, _ in rows])), mean_delta_bp=bp(mean(changes)),
                             sum_delta_event_bp=bp(math.fsum(changes)) if changes else None)
    result["transitions"] = dict(transitions)
    return result


def load_tables(results):
    return h.load_tables(results)


def verify_sources(root, results, summary):
    """Check every declared output and original git-show source, not live code."""
    started, config = read_json(results / "started.json"), read_json(results.parent / "config.json")
    require(started["sources"] == summary["sources"] and started["sources"], "Empty/changed source receipt")
    commit = started["builder_commit"]
    require(re.fullmatch(r"[a-f0-9]{40}", commit) is not None, "Invalid builder commit")
    identities = [row["path"] for row in started["sources"]]
    config_id = str(results.parent.relative_to(root)) + "/config.json"
    required = {"yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_failed_reduce_research.py",
                config_id, str(results.parent.relative_to(root)) + "/PROJECT_PLAN.md"}
    require(len(identities) == len(set(identities)) and required <= set(identities), "Missing/duplicate strategy source pins")
    for row in started["sources"]:
        safe_path(root, row["path"])
        try:
            content = subprocess.run(["git", "show", commit + ":" + row["path"]], cwd=root, check=True, capture_output=True).stdout
        except subprocess.CalledProcessError as error:
            raise VerificationError("Pinned builder source unavailable; cannot skip") from error
        require(hashlib.sha256(content).hexdigest() == row["sha256"], "Committed strategy source SHA changed")
    try:
        when = subprocess.run(["git", "show", "-s", "--format=%ct", commit], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise VerificationError("Builder timestamp unavailable") from error
    require(re.fullmatch(r"\d+", when) is not None and int(when) * 10**9 <= stamp(started["at"]), "Study predates committed builder")
    actual = {str(path.relative_to(results)) for path in results.rglob("*") if path.is_file()}
    require(actual == set(summary["output_hashes"]) | {"summary.json"}, "Output inventory incomplete or unexpected")
    for path, digest in summary["output_hashes"].items():
        require(sha(safe_path(results, path)) == digest, "Saved output SHA mismatch: " + path)
    require(config["experiment_id"] == EXPERIMENT_ID and config["policies"] == [BASE_POLICY, CANDIDATE_POLICY], "More than confirmed fill fraction changed")
    require(sha(results.parent / "config.json") == summary["config_sha256"] ==
            next(row["sha256"] for row in started["sources"] if row["path"] == config_id), "Frozen configuration SHA mismatch")
    require(config["parent_results"] == PARENT, "Baseline is not original V18 candidate")
    require(config["structure_reference"] == STRUCTURE_REFERENCE and config["structure_columns"] == STRUCTURE_COLUMNS and
            set(config["structure_inputs"]) == {"case_trades.csv.gz", "control_trades.csv.gz"}, "V16 structure-only reference drift")
    for directory, key in ((config["parent_results"], "inputs"), (config["mother_results"], "mother_inputs"),
                           (config["entry_context_results"], "entry_context_inputs"), (config["structure_reference"], "structure_inputs")):
        require(config[key] == summary[key], "Frozen saved input receipt changed")
        for filename, digest in config[key].items():
            require(sha(safe_path(root, directory + "/" + filename)) == digest, "Frozen input bytes changed")
    base_file = safe_path(root, config["base_config"])
    require(sha(base_file) == config["base_config_sha256"], "Original base config changed")
    base = read_json(base_file)
    require(base["execution"]["cost_fraction"] == .002 and base["execution"]["max_hours"] == 72 and base["execution"]["stop_first"] is True,
            "Original economic/stop discipline changed")
    require(base["development_folds"] == [[fold, lo, hi] for fold, (lo, hi) in h.FOLDS.items()], "Original time splits changed")
    require(summary["source"]["sha256"] == base["source"]["sha256"] and summary["source"]["holdout_price_rows"] == 0 and
            stamp(summary["source"]["phase_price_last_open"]) < stamp("2025-01-01T00:00:00Z"), "Source receipt exceeds development")
    return dict(builder_commit=commit, committed_sources_verified=len(identities), output_hashes_verified=len(summary["output_hashes"]),
                source_pins=started["sources"], output_hashes=summary["output_hashes"])


def verify_lineage(root, results, tables, summary):
    """V18 six-table anchor and separately frozen native5/native15 context."""
    config, anchor = read_json(results.parent / "config.json"), read_json(results / "anchor_parity.json")
    for name, filename in TABLE_FILES.items():
        old = indexed(read_csv(safe_path(root, PARENT + "/" + filename)))
        new = indexed(tables["baseline"][name])
        require(old.keys() == new.keys(), "V18 six-table identity drift")
        for key, row in old.items():
            parity(row, new[key])
        eq(anchor[name]["rows"], len(old), "V17 anchor row count drift")
        eq(anchor[name]["columns"], len(next(iter(old.values()))), "V17 anchor column count drift")
    for population in ("case", "control"):
        context = indexed(read_csv(results / (population + "_context.csv.gz")))
        sources = [config["entry_context_results"] + "/direct_k1_stop_" + population + "_context.csv.gz",
                   config["mother_results"] + "/" + ("original_mothers" if population == "case" else "control_mothers") + ".csv.gz"]
        for source in sources:
            upstream = indexed(read_csv(safe_path(root, source)))
            require(upstream.keys() == context.keys(), "Original mother/context identities lost")
            for key, row in upstream.items():
                parity(row, context[key])
        for arm in ARMS:
            trades = indexed(tables[arm][population + "_trades"])
            for key, row in context.items():
                parity(row, trades[key])
    assignment = indexed(read_csv(results / "assignments.csv"))
    upstream = indexed(read_csv(safe_path(root, config["mother_results"] + "/assignments.csv")))
    require(assignment.keys() == upstream.keys() == indexed(tables["baseline"]["case_trades"]).keys(), "Assignments lost mothers")
    for key, row in upstream.items():
        parity(row, assignment[key])
    require({k for k, r in assignment.items() if r["match_status"] == "matched"} ==
            {r["parent_event_id"] for r in tables["baseline"]["control_trades"]}, "Original154 support rematched")
    native, fast = read_csv(results / "native_entry_context.csv.gz"), read_csv(results / "fast_entry_context.csv.gz")
    counts = h.verify_contexts(native, fast, tables)
    for population in ("case", "control"):
        context = indexed([r for r in fast if r["population"] == population])
        old = indexed(tables["baseline"][population + "_trades"])
        require(context.keys() == old.keys(), "Baseline fast seed denominator drift")
        for key, row in context.items():
            h.check_context(row, old[key], 5, fast=True)
    frozen, started = read_json(results / "context_frozen.json"), read_json(results / "started.json")
    require(stamp(frozen["at"]) >= stamp(started["at"]) and frozen["before_outcome_reads"] is True and
            frozen["outcomes_hashed_or_read"] is False and frozen["entry_gates"] is False, "Pre-outcome freeze receipt drift")
    eq(frozen["rows"], len(native), "Native context freeze count drift")
    eq(frozen["fast_rows"], len(fast), "Fast context freeze count drift")
    require(frozen["context_sha256"] == sha(results / "native_entry_context.csv.gz") and
            frozen["fast_context_sha256"] == sha(results / "fast_entry_context.csv.gz"), "Context freeze SHA mismatch")
    for rows in (summary["native_context"], frozen["counts"], read_csv(results / "native_initial_state_counts.csv")):
        actual = {}
        for row in rows:
            key = (row["arm"], row["population"], row["mg_entry_state"])
            require(key not in actual, "Duplicate native context count")
            actual[key] = number(row["n"])
        require(actual == dict(counts), "Native context state denominator drift")
    edges = {}
    confirmation_records = []
    for arm in ARMS:
        for population in ("case", "control"):
            for row in tables[arm][population + "_trades"]:
                for edge in v17.events(row):
                    edges[(arm, population, row["event_id"], stamp(edge["available_at"]))] = edge
                confirmation_records.extend((arm, population, row["event_id"], record) for record in v18.lifecycle(row))
    seen = set()
    for row in read_csv(results / "fast_edges.csv.gz"):
        key = (row["arm"], row["population"], row["event_id"], stamp(row["available_at"]))
        require(key in edges and key not in seen, "Unknown/duplicate exported fast edge")
        seen.add(key)
        for field, value in edges[key].items():
            if isinstance(value, dict):
                parity(value, h.parse_json(row[field]))
            elif type(value) is bool:
                require(boolean(row[field]) == value, "Exported edge boolean drift")
            else:
                same(row[field], value, field)
    require(seen == edges.keys(), "Export omitted recorded fast edge")
    exported = read_csv(results / "confirmation_events.csv.gz")
    require(len(exported) == len(confirmation_records), "Exported pending lifecycle denominator drift")
    for row, (arm, population, key, record) in zip(exported, confirmation_records):
        require((row["arm"], row["population"], row["event_id"], row["action"]) == (arm, population, key, record["action"]),
                "Confirmation export ordering/identity drift")
        require(h.parse_json(row["evidence_json"]) == record, "Confirmation export not lossless")
    return dict(anchor_tables=6, native_context_rows=len(native), fast_context_rows=len(fast), recorded_fast_edges=len(edges),
                recorded_confirmation_events=len(exported), context_freeze_is_saved_receipt_not_runtime_trace=True)


def verify_structure(root, results, tables, summary):
    """Only V16 final-path columns; reference returns are never used here."""
    config = read_json(results.parent / "config.json")
    saved = read_json(results / "remainder_structure_parity.json")
    require(config["structure_reference"] == STRUCTURE_REFERENCE and config["structure_columns"] == STRUCTURE_COLUMNS,
            "Wrong final-path reference contract")
    require(saved["reference"] == STRUCTURE_REFERENCE and saved["inputs"] == config["structure_inputs"] and
            saved["columns"] == STRUCTURE_COLUMNS and saved["pnl_borrowed"] is False and
            set(saved["checks"]) == {"case", "control"}, "Structure receipt borrowed returns or changed source")
    counts = {}
    for population in ("case", "control"):
        path = safe_path(root, STRUCTURE_REFERENCE + "/" + population + "_trades.csv.gz")
        require(sha(path) == config["structure_inputs"][path.name], "Structure input SHA mismatch")
        source = indexed([{k: r[k] for k in STRUCTURE_COLUMNS} for r in read_csv(path)])
        candidate = indexed(tables["candidate"][population + "_trades"])
        require(source.keys() == candidate.keys(), "Structure reference lost original identities")
        for key, row in source.items(): parity(row, candidate[key])
        counts[population] = len(source)
        eq(saved["checks"][population]["rows"], len(source), "Structure receipt row count drift")
        eq(saved["checks"][population]["columns"], len(STRUCTURE_COLUMNS), "Structure receipt column scope drift")
    return dict(rows=counts, columns=STRUCTURE_COLUMNS, reference=STRUCTURE_REFERENCE,
                return_columns_used=False, structure_input_hashes=config["structure_inputs"])


def mechanic_rows(old, new):
    """All paired rows; realised all-risk and known-remainder bases stay distinct."""
    require(old.keys() == new.keys(), "Mechanics identity denominator drift")
    rows = []
    for key, a in old.items():
        z = new[key]
        before, after = number(a["net_return"], True), number(z["net_return"], True)
        delta = after-before if before is not None and after is not None else None
        if delta is None: before, after = None, None
        reduced = number(z["failed_reduce_fill_count"]) == 1
        risk_net = number(z["failed_reduce_realised_net_return"]) if reduced else 0.
        remainder = number(z["net_return"])-risk_net if reduced and boolean(z["closed"]) else None
        row = dict(event_id=key, mother_decision_time=a["mother_decision_time"], baseline_net_bp=bp(before), candidate_net_bp=bp(after),
            delta_net_bp=bp(delta), exit_delay_minutes=(stamp(z["exit_time"])-stamp(a["exit_time"]))/MINUTE,
            baseline_confirmed_full=a["outcome"] == "fast_failed_launch", candidate_risk_reduced=reduced,
            candidate_profitable_partial=number(z["partial_fast_fill_count"]) == 1,
            candidate_risk_realised_net_bp=bp(risk_net), candidate_remainder_net_bp=bp(remainder),
            recovered_winner=a["outcome"] == "fast_failed_launch" and after is not None and after > 0,
            newly_unknown=boolean(a["closed"]) and not boolean(z["closed"]))
        row["outcome_transition"] = "flat_or_unknown" if before is None or after is None or before == 0 or after == 0 else ("win" if before > 0 else "loss")+"_to_"+("win" if after > 0 else "loss")
        for arm, trade in (("baseline", a), ("candidate", z)):
            for suffix, field in (("exit_time", "exit_time"), ("exit_reason", "outcome"), ("mfe_r", "max_favourable_r"), ("hold_minutes", "hold_minutes")):
                row[arm+"_"+suffix] = trade[field]
        rows.append(row)
    return rows


def mechanism_summary(old, new):
    rows = mechanic_rows(old, new)
    reduced = [r for r in rows if r["candidate_risk_reduced"]]
    known_reduced = [r for r in reduced if r["candidate_remainder_net_bp"] is not None]
    unknown_reduced = [r for r in reduced if r["candidate_remainder_net_bp"] is None]
    known = [r for r in rows if r["delta_net_bp"] is not None]
    def total(part, field):
        values = [r[field] for r in part if r[field] is not None]
        return math.fsum(values) if values else None if part else 0.
    groups = paired_groups(old, new)
    return dict(total=len(rows), known=len(known), transitions=groups["transitions"],
        groups=[dict(group=k, **groups[k]) for k in sorted(groups["transitions"])],
        later_exits=sum(r["exit_delay_minutes"] > 0 for r in rows), earlier_exits=sum(r["exit_delay_minutes"] < 0 for r in rows),
        same_exit_time=sum(r["exit_delay_minutes"] == 0 for r in rows),
        baseline_confirmed_full_count=sum(r["baseline_confirmed_full"] for r in rows), candidate_risk_reduced_count=len(reduced),
        candidate_profitable_partial_count=sum(r["candidate_profitable_partial"] for r in rows),
        baseline_profitable_partial_count=sum(number(r["partial_fast_fill_count"]) for r in old.values()),
        unchanged_paths=sum(not r["baseline_confirmed_full"] for r in rows),
        pending_events=sum(number(r["failed_confirm_create_count"]) for r in new.values()),
        cancelled_pending_events=sum(number(r["failed_confirm_cancel_count"]) for r in new.values()),
        priority_terminated_pending_events=sum(number(r["failed_confirm_priority_termination_count"]) for r in new.values()),
        changed_improved=sum(r["delta_net_bp"] is not None and r["delta_net_bp"] > 1e-8 for r in reduced),
        changed_hurt=sum(r["delta_net_bp"] is not None and r["delta_net_bp"] < -1e-8 for r in reduced),
        changed_unknown_pairs=sum(r["delta_net_bp"] is None for r in reduced), recovered_winners=sum(r["recovered_winner"] for r in rows),
        newly_unknown=sum(r["newly_unknown"] for r in rows), baseline_partial_count=sum(number(r["partial_fraction"]) == .5 for r in old.values()),
        candidate_partial_count=sum(number(r["partial_fraction"]) == .5 for r in new.values()),
        improved=sum(r["delta_net_bp"] > 1e-8 for r in known), hurt=sum(r["delta_net_bp"] < -1e-8 for r in known),
        unchanged=sum(abs(r["delta_net_bp"]) <= 1e-8 for r in known), unknown_pairs=len(rows)-len(known),
        remainder_known_count=len(known_reduced), remainder_unknown_count=len(unknown_reduced),
        risk_realised_net_event_bp=total(reduced, "candidate_risk_realised_net_bp"),
        risk_realised_net_known_pairs_event_bp=total(known_reduced, "candidate_risk_realised_net_bp"),
        risk_realised_net_unknown_remainder_event_bp=total(unknown_reduced, "candidate_risk_realised_net_bp"),
        remainder_net_event_bp=total(reduced, "candidate_remainder_net_bp"),
        reduced_total_net_event_bp=total(reduced, "candidate_net_bp"), reduced_delta_event_bp=total(reduced, "delta_net_bp"))


def check_mechanic_summary(old, new, groups, info):
    derived = mechanism_summary(old, new)
    require(info["transitions"] == derived["transitions"], "Win/loss migration drift")
    saved = {r["group"]: r for r in info["groups"]}
    require(len(saved) == len(info["groups"]) and saved.keys() == groups["transitions"].keys(), "Mechanics groups lost unknowns")
    for name, row in saved.items():
        for field, value in groups[name].items(): eq(row[field], value, "Mechanics group arithmetic drift")
    for key, value in derived.items():
        if key not in ("groups", "transitions"): eq(info[key], value, "Mechanics/leg denominator arithmetic drift: "+key)


def verify_mechanics_exports(results, tables, summary):
    result = {}
    for population in ("case", "control"):
        old, new = (indexed(tables[arm][population+"_trades"]) for arm in ARMS)
        expected = indexed(mechanic_rows(old, new))
        saved = indexed(read_csv(results/("reduced_"+population+"_mechanics.csv")))
        require(saved.keys() == expected.keys(), "Mechanics export lost original population")
        for key, row in expected.items():
            require(row.keys() == saved[key].keys(), "Mechanics export schema drift")
            for field, value in row.items():
                if type(value) is bool: require(boolean(saved[key][field]) == value, "Mechanics boolean drift")
                else: same(value, saved[key][field], field)
        check_mechanic_summary(old, new, paired_groups(old, new), summary["mechanics"][population])
        rows = read_csv(results/("reduced_"+population+"_groups.csv"))
        groups = {r["group"]: r for r in rows}
        expected_groups = {r["group"]: r for r in summary["mechanics"][population]["groups"]}
        require(len(groups) == len(rows) and groups.keys() == expected_groups.keys(), "Mechanics group export lost unknowns")
        for key, row in expected_groups.items(): parity(row, groups[key])
        result[population+"_mechanics_rows"] = len(saved)
    monthly = defaultdict(list)
    for arm in ARMS:
        for row in tables[arm]["case_episodes"]:
            month = (h.EPOCH+timedelta(seconds=stamp(row["mother_decision_time"])//10**9)).strftime("%Y-%m")
            monthly[(arm, row["fold"], month)].append(number(row["episode_net_return"], True))
    rows = read_csv(results/"monthly_case_net.csv")
    saved = {(r["arm"], r["fold"], r["month"]): r for r in rows}
    require(len(saved) == len(rows) and saved.keys() == monthly.keys(), "Monthly export denominator drift")
    for key, values in monthly.items():
        for field, value in (("n", len(values)), ("known", sum(x is not None for x in values)), ("mean_net_bp", bp(mean(values)))):
            eq(saved[key][field], value, "Monthly return arithmetic drift")
    return dict(result, monthly_rows=len(rows))


def verify(results, summary_path=None, *, root=ROOT):
    results, root = Path(results).resolve(), Path(root)
    summary_path = Path(summary_path).resolve() if summary_path else results / "summary.json"
    summary = read_json(summary_path)
    require(summary == read_json(results / "summary.json") and not (results / "failure.json").exists(), "Changed summary or failed attempt")
    require(set(summary["mechanics"]) == {"case", "control"}, "Missing all-population mechanics")
    receipts = verify_sources(root, results, summary)
    for arm in ARMS:
        require(read_json(results / arm / "summary.json") == summary["arms"][arm], "Root/arm summary drift")
    tables = load_tables(results)
    output = verify_tables(tables, summary)
    output["lineage"] = verify_lineage(root, results, tables, summary)
    output["structure"] = verify_structure(root, results, tables, summary)
    output["diagnostics"] = verify_mechanics_exports(results, tables, summary)
    output.update(receipts, summary_sha256=sha(summary_path), verifier_sources=[
        dict(path="scripts/" + path.name, sha256=sha(path)) for path in (Path(__file__), Path(v18.__file__), Path(v17.__file__), Path(h.__file__))])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        if args.out:
            require(not args.out.exists() and not args.out.resolve().is_relative_to(args.results.resolve()), "Use new receipt outside saved results")
        output = verify(args.results, args.summary)
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as error:
        output = dict(status="failed", error=str(error), **SCOPE)
    text = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.out and output["status"] == "passed":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0 if output["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
