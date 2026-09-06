"""Independent V18 saved-ledger verification; no price or strategy imports.

This audit checks the fixed original hourly entries, recorded two-bar pending
decisions, weighted original-notional 20bp accounting, all-mother paired means,
fixed triples, and each arm's independently recomputed serial occupancy. It
does not infer absent bars/edges, recompute SMA, rerun inferential statistics,
or claim live profitability. The baseline is the saved V17 candidate, not V16.

Local import closure is this file plus the V17 and V16 stdlib verifiers. Only
their parsing, clocks, numerical, episode and serial helpers are reused; V17's
single-edge candidate decision is never applied to the V18 candidate.
Python3.9 source contracts:
https://docs.python.org/3.9/library/importlib.html#importing-a-source-file-directly
https://docs.python.org/3.9/library/csv.html#csv.DictReader
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


_SPEC = importlib.util.spec_from_file_location("_v18_saved_v17", Path(__file__).with_name("verify_hourly_impulse_failed_launch_v17.py"))
v17 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(v17)
h = v17.h
VerificationError = h.VerificationError
require, number, eq, boolean, stamp = h.require, h.number, h.eq, h.boolean, h.stamp
indexed, parity, same, mean, bp = h.indexed, h.parity, h.same, h.mean, h.bp
read_json, read_csv, sha, safe_path = h.read_json, h.read_csv, h.sha, h.safe_path
ROOT, ARMS, TABLE_FILES, DELTAS, MINUTE, HOUR = h.ROOT, h.ARMS, h.TABLE_FILES, h.DELTAS, h.MINUTE, h.HOUR
EXPERIMENT_ID = "exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18"
BASE_POLICY = dict(v17.CANDIDATE_POLICY)
CANDIDATE_POLICY = dict(BASE_POLICY, id="15m_native40_failed_confirm2", fast_failed_launch_confirmations=2)
PARENT = "experiments/active/exp-btcusdtp-1h-failed-launch-preholdout-20260906-v17/results/candidate"
SCOPE = dict(h.SCOPE, limitation="Saved quotes, colour snapshots, recorded pending/confirmation firstness, formulas and receipts only; "
    "not raw prices/SMA, proof of no omitted edges, independent inference, live fills or profitability.")


def exact_gross(row, price):
    """Decimal is confined to the new full-fill quote equality contract."""
    with localcontext() as context:
        context.prec = 40
        return Decimal(str(row["direction"])) * (Decimal(str(price)) - Decimal(str(row["entry_price"]))) / Decimal(str(row["entry_price"]))


def check_accounting(row):
    """Independently check known/unknown full and original-half accounting."""
    direction, entry, stop, atr = (number(row[k]) for k in ("direction", "entry_price", "initial_stop", "signal_atr"))
    require(direction in (-1, 1) and min(entry, stop, atr) > 0, "Invalid original entry")
    risk = direction * (entry - stop)
    require(risk > 0, "Nonpositive original risk")
    eq(row["risk_pct"], risk / entry, "Original risk_pct drift")
    eq(row["risk_atr"], risk / atr, "Original risk_atr drift")
    start, end = stamp(row["entry_time"]), stamp(row["exit_time"])
    require(start == stamp(row["decision_time"]) == stamp(row["mother_decision_time"]) and start % HOUR == 0,
            "Original hourly entry clock changed")
    require(stamp(row["signal_time"]) + HOUR == start and stamp(row["mother_deadline"]) == start + 72 * HOUR,
            "Original K1/deadline changed")
    require(row["fold"] in h.FOLDS, "Unknown fold")
    low, high = (stamp(t + "T00:00:00Z") for t in h.FOLDS[row["fold"]])
    require(low <= start < high - 72 * HOUR and start <= end <= start + 72 * HOUR and end <= high and end % (5 * MINUTE) == 0,
            "Held path outside original development or native5 clock")
    eq(row["hold_minutes"], (end - start) / MINUTE, "Hold minutes drift")
    if "wait_hours" in row:
        eq(row["wait_hours"], 0, "Original direct entry acquired waiting")
    closed, outcome = boolean(row["closed"]), row["outcome"]
    require(outcome in ({"hard_stop", "hard_stop_gap", "transition_colour_exit", "time_exit", "fast_failed_launch"} if closed else
                        {"data_gap_censored", "right_censored"}), "Unregistered original outcome")
    price = number(row["exit_price"])
    require(price > 0, "Invalid terminal open/mark")
    if outcome == "hard_stop":
        eq(price, stop, "Original stop moved")
    if outcome == "hard_stop_gap":
        require(direction * (price - stop) <= 1e-12, "Gap stop improved price")
    if outcome == "time_exit":
        require(end == start + 72 * HOUR, "Time exit deadline changed")
    state = row["transition_initial_state"]
    require(state in ("aligned", "opposite", "unknown"), "Invalid slow seed")
    require(stamp(row["transition_initial_open_time"], True) == (None if state == "unknown" else start - 15 * MINUTE),
            "Native15 initial clock drift")
    trigger = [stamp(row[k], True) for k in ("transition_trigger_previous_open_time", "transition_trigger_open_time", "transition_trigger_available_at")]
    if outcome == "transition_colour_exit":
        p, c, a = trigger
        require(None not in trigger and p + 15 * MINUTE == c and c + 15 * MINUTE == a == end and c >= start,
                "Slow full trigger clock changed")
        require(direction * (price - stop) > 0, "Slow full displaced gap stop")
    else:
        require(all(t is None for t in trigger), "Other exit invented a slow trigger")
    fraction = number(row["partial_fraction"])
    require(fraction in (0, .5), "Original half size changed")
    eq(row["exit_remaining_fraction"], 1 - fraction, "Position fractions not conserved")
    if fraction:
        when, quote = stamp(row["partial_exit_time"]), number(row["partial_exit_price"])
        require(start < when <= end and when % (5 * MINUTE) == 0, "Partial fill clock invalid")
        require(h.profit_qualified(row["partial_exit_price"], row["entry_price"], row["direction"]), "Half requires strictly more than20bp")
        realised = fraction * direction * (quote / entry - 1)
        require(number(row["max_favourable_r"]) >= direction * (quote - entry) / risk - 1e-12, "Partial exceeds held MFE")
    else:
        require(stamp(row["partial_exit_time"], True) is None and number(row["partial_exit_price"], True) is None,
                "Zero partial fraction retained a fill")
        realised = 0.
    full = outcome == "fast_failed_launch"
    if full:
        require(fraction == 0 and start < end < start + 72 * HOUR and direction * (price - stop) > 0,
                "Failed full displaced partial/stop/deadline")
        require(not h.profit_qualified(row["exit_price"], row["entry_price"], row["direction"]), "Profitable full exit is forbidden")
        gross = float(exact_gross(row, row["exit_price"]))
    else:
        gross = realised + (1 - fraction) * direction * (price / entry - 1)
    eq(row["realised_partial_gross_return"], realised, "Partial gross not original-notional weighted")
    eq(row["partial_fast_realised_net_return"], realised - fraction * .002, "Partial allocated20bp cost drift")
    eq(row["marked_gross_return"], gross, "Weighted gross mark drift")
    eq(row["marked_net_return"], gross - .002, "Whole original-notional20bp cost drift")
    if closed:
        for key, value in (("gross_return", gross), ("net_return", gross - .002), ("net_r", (gross - .002) / (risk / entry))):
            eq(row[key], value, "Closed weighted accounting drift: " + key)
    else:
        require(all(number(row[k], True) is None for k in ("gross_return", "net_return", "net_r")), "Censored whole trade invented known return")
    if full and exact_gross(row, row["exit_price"]) == Decimal("0.002"):
        require(number(row["gross_return"]) == .002 and number(row["net_return"]) == 0 and number(row["net_r"]) == 0,
                "Exact20bp must be exact zero, not a floating winner")
    favourable, adverse = number(row["max_favourable_r"]), number(row["max_adverse_r"])
    require(favourable >= 0 and adverse <= 0, "Excursion sign drift")
    excursion = direction * (price - entry) / risk
    require(favourable >= max(0, excursion) - 1e-12 and adverse <= min(0, excursion) + 1e-12, "Held MFE/MAE omitted terminal quote")
    if "funding_modelled" in row:
        require(not boolean(row["funding_modelled"]), "Funding model changed")


def check_original_entry(old, new):
    require(old.keys() <= new.keys(), "Candidate lost original columns")
    for key, value in old.items():
        mutable = key in v17.MUTABLE_FIELDS or key.startswith(("partial_fast_", "transition_", "failed_launch_"))
        if key.startswith(("partial_fast_initial_", "transition_initial_")):
            mutable = False
        if not mutable:
            same(value, new[key], key)


def check_pair_path(old, new):
    """Only original V17 full-exit paths may change; their held prefix survives."""
    check_original_entry(old, new)
    if old["outcome"] != "fast_failed_launch":
        parity(old, new)
        return False
    end, later = stamp(old["exit_time"]), stamp(new["exit_time"])
    require(later >= end, "Confirmation delay exited before original first edge")
    # This engine stamps an intrabar hard stop at that raw5 bar's CLOSE.
    # Only a censor can retain the original last-known open timestamp.
    require(later > end or not boolean(new["closed"]), "Known candidate fill failed to wait for second completed bar")
    require(number(new["max_favourable_r"]) + 1e-12 >= number(old["max_favourable_r"]) and
            number(new["max_adverse_r"]) <= number(old["max_adverse_r"]) + 1e-12, "Extended path lost original held excursions")
    same(old["partial_fast_first_armed_at"], new["partial_fast_first_armed_at"], "partial_fast_first_armed_at")
    require(number(new["partial_fast_reset_count"]) >= number(old["partial_fast_reset_count"]), "Extended path lost previous fast resets")
    a, z = v17.events(old), [e for e in v17.events(new) if stamp(e["available_at"]) <= end]
    require(len(a) == len(z), "Candidate lost original recorded edge prefix")
    for before, after in zip(a, z):
        require(before.keys() == after.keys(), "Original edge schema changed")
        for key, value in before.items():
            if key == "action" and stamp(before["available_at"]) == end:
                require(value == "failed_launch_exit" and after[key] == "failed_launch_pending", "First full edge was not delayed")
            elif isinstance(value, dict):
                parity(value, after[key])
            else:
                same(value, after[key], key)
    return True


def check_baseline(row):
    """V17 semantics are appropriate only for the baseline arm."""
    require(not any(k.startswith("failed_confirm_") for k in row), "Baseline contains V18 confirmation fields")
    v17.check_candidate_events(row)
    check_accounting(row)
    if row["outcome"] != "fast_failed_launch":
        h.check_trade(row, True)


CONFIRM_FIELDS = {"failed_confirm_" + name for name in (
    "enabled", "required", "create_count", "confirm_count", "cancel_count", "priority_termination_count",
    "status", "last_reason", "events", "created_at", "due_at", "previous_open_time", "open_time",
    "available_at", "open_price", "gross_return", "slow_open_time", "slow_available_at", "slow_side", "slow_state")}


def lifecycle(row):
    value = row["failed_confirm_events"]
    value = h.parse_json(value) if isinstance(value, str) else value
    require(isinstance(value, list), "Confirmation lifecycle must be JSON array")
    return value


def check_seed(row):
    start, end = stamp(row["entry_time"]), stamp(row["exit_time"])
    state, direction = row["partial_fast_initial_state"], number(row["direction"])
    require(state in ("aligned", "opposite", "unknown"), "Invalid fast initial state")
    if state == "unknown":
        require(number(row["partial_fast_initial_side"], True) is None and
                stamp(row["partial_fast_initial_open_time"], True) is None and
                stamp(row["partial_fast_initial_available_at"], True) is None and row["partial_fast_initial_reason"] != "valid",
                "Unknown fast seed invented known state")
    else:
        source = {key: row["partial_fast_initial_" + key] for key in ("open_time", "side", "ma", "hl2", "management_segment_id", "raw_segment_id")}
        side = h.valid_source(source, start, 5)
        require(stamp(row["partial_fast_initial_available_at"]) == start and row["partial_fast_initial_reason"] == "valid" and
                state == ("aligned" if direction * side > 0 else "opposite"), "Fast own seed drift")
    armed = stamp(row["partial_fast_first_armed_at"], True)
    require(armed is None or start <= armed <= end and armed % (5 * MINUTE) == 0, "Fast arming clock drift")
    require(state != "aligned" or armed == start, "Aligned entry not armed")
    resets = number(row["partial_fast_reset_count"])
    require(resets == int(resets) and resets >= 0 and (resets == 0 or row["partial_fast_last_reset_reason"]), "Fast reset evidence drift")
    return armed


def observed_source(seen, source, available, minutes):
    side = h.valid_source(source, available, minutes)
    key = (minutes, available)
    if key in seen:
        parity(seen[key], source)
    seen[key] = source
    return side


def slow_snapshot(row, observation, now, seen, raw_segment=None):
    available = now // (15 * MINUTE) * (15 * MINUTE)
    require(stamp(observation["slow_available_at"]) == available, "Slow gate not latest completed native15")
    state = observation["slow_state"]
    require(state in ("aligned", "opposite", "unknown"), "Invalid slow gate state")
    if state == "unknown":
        require(observation["slow_reason"] != "valid" and number(observation["slow"]["side"], True) is None,
                "Unknown slow colour invented a known gate")
    else:
        side = observed_source(seen, observation["slow"], available, 15)
        require(observation["slow_reason"] == "valid" and state == ("aligned" if number(row["direction"]) * side > 0 else "opposite"),
                "Own slow colour differs from saved HL2/MA")
        if raw_segment is not None:
            require(observation["slow"]["raw_segment_id"] == raw_segment, "Slow carry crossed source reset")
    return state


def quote_check(row, observation, *, exact=False):
    price = number(observation["open_price"])
    require(price > 0 and number(row["direction"]) * (price - number(row["initial_stop"])) > 0,
            "Fast action displaced gap stop")
    gross = float(exact_gross(row, observation["open_price"])) if exact else number(row["direction"]) * (price / number(row["entry_price"]) - 1)
    eq(observation["gross_return"], gross, "Observed gross differs from actual open")
    qualifies = h.profit_qualified(observation["open_price"], row["entry_price"], row["direction"])
    require(boolean(observation["profit_qualified"]) == qualifies, "Strict Decimal20bp economics drift")
    return qualifies


def check_candidate(row):
    """Verify true-edge and one-shot lifecycle logs without inventing unseen bars."""
    require(CONFIRM_FIELDS <= row.keys() and v17.FAILED_FIELDS <= row.keys(), "Missing V18 confirmation evidence")
    require(boolean(row["failed_confirm_enabled"]) and boolean(row["failed_launch_enabled"]) and boolean(row["partial_fast_enabled"]),
            "Frozen exit branch disabled")
    for key, value in (("failed_confirm_required", 2), ("partial_fast_fraction", .5),
                       ("partial_fast_profit_threshold", .002), ("failed_launch_profit_threshold", .002)):
        eq(row[key], value, "Confirmation/partial parameter drift")
    start, end, direction = stamp(row["entry_time"]), stamp(row["exit_time"]), number(row["direction"])
    armed, seen = check_seed(row), {}
    edges = v17.events(row)
    eq(row["partial_fast_flip_count"], len(edges), "Confirmation was counted as a new true edge")
    previous, half, pending_edges = start, None, {}
    for edge in edges:
        now = stamp(edge["available_at"])
        require(previous < now <= end and now < start + 72 * HOUR and now % (5 * MINUTE) == 0, "Fast edge outside ordered held clock")
        previous = now
        p, c = edge["previous_fast"], edge["current_fast"]
        ps, cs = observed_source(seen, p, now - 5 * MINUTE, 5), observed_source(seen, c, now, 5)
        require(direction * ps > 0 and direction * cs < 0, "Not a true aligned-to-opposite edge")
        require(p["management_segment_id"] == c["management_segment_id"] and p["raw_segment_id"] == c["raw_segment_id"], "Edge crossed a reset")
        require(armed is not None and armed <= now - 5 * MINUTE, "Edge before causal arming")
        state = slow_snapshot(row, edge, now, seen, c["raw_segment_id"])
        qualifies = quote_check(row, edge)
        eq(edge["profit_threshold"], .002, "Edge cost threshold changed")
        action = ("already_partial" if half is not None else "slow_unknown" if state == "unknown" else
                  "slow_not_aligned" if state != "aligned" else "executed" if qualifies else "failed_launch_pending")
        require(edge["action"] == action, "First eligible edge skipped or invented half/full")
        require(now < end or row["outcome"] in ("data_gap_censored", "right_censored"), "Fast action displaced a higher-priority exit")
        if action == "executed":
            half = edge
        elif action == "failed_launch_pending":
            pending_edges[now] = edge
    log, active, created, cancelled, terminated, confirmed = lifecycle(row), None, [], [], [], None
    last_observed = start
    for record in log:
        action = record["action"]
        made, due, observed = (stamp(record[key]) for key in ("created_at", "due_at", "observed_at"))
        require(start < made <= end and due == made + 5 * MINUTE and observed >= made and observed >= last_observed,
                "Pending lifecycle clock/order drift")
        last_observed = observed
        require(made in pending_edges, "Pending lifecycle invented an edge")
        require(record["edge"] == pending_edges[made], "Pending original edge evidence changed")
        require(confirmed is None and not terminated, "Lifecycle continued after terminal outcome")
        if action == "created":
            require(active is None and observed == made and record["reason"] == "failed_profit_edge", "Pending overlapped or delayed creation")
            eq(record["pending_id"], len(created) + 1, "Pending identifiers not sequential")
            require(record["observation"] is None and record["terminal"] is None, "Creation used future confirmation data")
            active = record
            created.append(record)
            continue
        require(active is not None and record["pending_id"] == active["pending_id"] and made == stamp(active["created_at"]), "Pending resolution without creation")
        require(not any(made < stamp(edge["available_at"]) <= observed for edge in edges), "New true edge occurred before pending resolution")
        if action == "terminated":
            terminal = record["terminal"]
            require(record["observation"] is None and isinstance(terminal, dict), "Priority terminal invented future colour/quote")
            require(terminal["outcome"] == record["reason"] == row["outcome"] and boolean(terminal["closed"]) == boolean(row["closed"]),
                    "Priority pending termination differs from final outcome")
            same(terminal["exit_time"], row["exit_time"], "exit_time")
            eq(terminal["exit_price"], row["exit_price"], "Priority terminal fill drift")
            require(observed >= end and row["outcome"] != "fast_failed_launch", "Priority termination displaced confirmation")
            if boolean(row["closed"]):
                require(observed == end == due, "Known priority terminal outside immediate pending bar")
            terminated.append(record)
        else:
            require(action in ("confirmed", "cancelled") and record["terminal"] is None, "Unknown lifecycle action")
            obs = record["observation"]
            require(isinstance(obs, dict) and stamp(obs["available_at"]) == observed and observed <= end,
                    "Pending observation missing/unheld")
            qualifies = quote_check(row, obs, exact=True)
            p, c = obs["previous_fast"], obs["current_fast"]
            side = number(c["side"], True)
            if side is None:
                require(obs["fast_reason"] != "valid", "Unknown confirmation has valid reason")
            else:
                require(obs["fast_reason"] == "valid", "Known fast observation has invalid reason")
                observed_source(seen, c, observed, 5)
            # Pending's previous bar was already valid at creation. Its saved
            # identity must not be substituted with another request/bar.
            parity(active["edge"]["current_fast"], p)
            actual_consecutive = (stamp(c["open_time"], True) == stamp(p["open_time"]) + 5 * MINUTE and
                                  c["management_segment_id"] == p["management_segment_id"])
            require(boolean(obs["fast_consecutive"]) == actual_consecutive, "Confirmation continuity flag contradicts snapshots")
            state = slow_snapshot(row, obs, observed, seen, c["raw_segment_id"] if side is not None else None)
            partial_before = half is not None and stamp(half["available_at"]) <= observed
            expected = ("confirmation_clock_mismatch" if observed != due else obs["fast_reason"] if side is None else
                        "management_sequence_change" if not actual_consecutive else "fast_not_opposite" if direction * side >= 0 else
                        "already_partial" if partial_before else "slow_unknown" if state == "unknown" else
                        "slow_not_aligned" if state != "aligned" else "profit_recovered" if qualifies else None)
            if expected is None:
                require(action == "confirmed" and record["reason"] == "consecutive_opposite_failed_profit", "First eligible confirmation was skipped")
                require(c["raw_segment_id"] == p["raw_segment_id"], "Confirmation crossed raw source reset")
                require(observed == end and half is None, "Confirmed fill not actual final open or partial already held")
                eq(row["exit_price"], obs["open_price"], "Confirmed fill reused first quote")
                confirmed = record
            else:
                require(action == "cancelled" and record["reason"] == expected, "One-shot cancellation reason/priority drift")
                cancelled.append(record)
        active = None
    require(active is None and len(created) == len(pending_edges), "Unresolved or missing pending lifecycle")
    require(len(created) == len(cancelled) + len(terminated) + int(confirmed is not None), "Pending counts not conserved")
    for field, value in (("create_count", len(created)), ("cancel_count", len(cancelled)),
                         ("priority_termination_count", len(terminated)), ("confirm_count", int(confirmed is not None))):
        eq(row["failed_confirm_" + field], value, "Confirmation count/log drift")
    require(row["failed_confirm_last_reason"] == (log[-1]["reason"] if log else ""), "Last pending reason drift")
    for key in ("created_at", "due_at"):
        same(row["failed_confirm_" + key], created[-1][key] if created else None, key)
    failed = confirmed is not None
    require((row["outcome"] == "fast_failed_launch") == failed, "Confirmed outcome/log mismatch")
    eq(row["failed_launch_count"], int(failed), "Confirmed full count drift")
    closed = boolean(row["closed"])
    require(row["failed_launch_status"] == ("failed_launch_closed" if failed else "prior_exit" if closed else "unknown_source"), "Failed terminal status drift")
    require(row["failed_confirm_status"] == ("confirmed_closed" if failed else "prior_exit" if closed else "unknown_source"), "Confirmation terminal status drift")
    check_fill_scalars(row, half, confirmed)
    return dict(created=len(created), cancelled=len(cancelled), terminated=len(terminated), confirmed=int(failed))


def check_fill_scalars(row, half, confirmed):
    """A real edge scalar and second confirmation scalar have distinct clocks."""
    closed, failed = boolean(row["closed"]), confirmed is not None
    eq(row["partial_fast_fill_count"], int(half is not None), "Partial count/log drift")
    eq(row["partial_fraction"], .5 if half is not None else 0, "Partial fraction/log drift")
    expected_status = "failed_launch_closed" if failed else (("partial_closed" if closed else "partial_censored") if half is not None else "no_partial_exit" if closed else "unknown_source")
    require(row["partial_fast_status"] == expected_status, "Partial whole observability drift")
    first = confirmed["edge"] if failed else None
    for prefix, edge in (("partial_fast_", half), ("failed_launch_", first)):
        mapping = {
            "trigger_previous_open_time": edge["previous_fast"]["open_time"] if edge else None,
            "trigger_open_time": edge["current_fast"]["open_time"] if edge else None,
            "trigger_available_at": edge["available_at"] if edge else None,
            "trigger_previous_side": edge["previous_fast"]["side"] if edge else None,
            "trigger_side": edge["current_fast"]["side"] if edge else None,
            "trigger_gross_return": edge["gross_return"] if edge else None,
            "slow_open_time": edge["slow"]["open_time"] if edge else None,
            "slow_available_at": edge["slow_available_at"] if edge else None,
            "slow_side": edge["slow"]["side"] if edge else None,
        }
        for field, value in mapping.items():
            same(row[prefix + field], value, field)
        require(row[prefix + "slow_state"] == ("aligned" if edge else "unknown"), "Scalar slow state drift")
    same(row["failed_launch_trigger_open_price"], first["open_price"] if first else None, "trigger_open_price")
    require(stamp(row["failed_launch_trigger_previous_available_at"], True) ==
            (stamp(first["previous_fast"]["open_time"]) + 5 * MINUTE if first else None), "First edge previous availability drift")
    if half:
        same(row["partial_exit_time"], half["available_at"], "partial_exit_time")
        eq(row["partial_exit_price"], half["open_price"], "Half fill not original qualified edge quote")
    obs = confirmed["observation"] if failed else None
    mapping = {
        "previous_open_time": first["current_fast"]["open_time"] if first else None,
        "open_time": obs["current_fast"]["open_time"] if obs else None,
        "available_at": obs["available_at"] if obs else None,
        "open_price": obs["open_price"] if obs else None,
        "gross_return": obs["gross_return"] if obs else None,
        "slow_open_time": obs["slow"]["open_time"] if obs else None,
        "slow_available_at": obs["slow_available_at"] if obs else None,
        "slow_side": obs["slow"]["side"] if obs else None,
    }
    for field, value in mapping.items():
        same(row["failed_confirm_" + field], value, field)
    require(row["failed_confirm_slow_state"] == ("aligned" if failed else "unknown"), "Confirm slow state drift")
    if failed and exact_gross(row, row["exit_price"]) == Decimal("0.002"):
        require(number(row["failed_confirm_gross_return"]) == .002 and number(obs["gross_return"]) == .002,
                "Exact20bp confirmation evidence drift")


def verify_tables(tables, summary, *, expected_counts=(251, 462, 154)):
    """Pure fifteen-table API; default/CLI pin all251/462/154/97 identities."""
    n, control_n, matched_n = expected_counts
    require(n > 0 and 0 <= matched_n <= n and control_n == 3 * matched_n, "Invalid expected original cohort")
    require(summary["experiment_id"] == EXPERIMENT_ID and summary["status"] == "diagnostic_only_no_candidate_acceptance", "Wrong V18 experiment/status")
    for flag in ("holdout_consumed", "audit_prices_loaded", "production_eligible", "training_eligible"):
        require(summary[flag] is False, "Unsafe result flag: " + flag)
    states, mapping, serial, counts, confirmation_counts = {}, None, {}, {}, {}
    for arm in ARMS:
        data, info = tables[arm], summary["arms"][arm]
        require(info["policy"] == (BASE_POLICY if arm == "baseline" else CANDIDATE_POLICY), "More than full-confirmation count changed")
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
            confirmation_counts[arm + "/" + population] = dict(created=0, cancelled=0, terminated=0, confirmed=0)
            for key, row in trades.items():
                if population == "control":
                    eq(row["direction"], states[arm]["case_trades"][row["parent_event_id"]]["direction"], "Control own assigned direction changed")
                if arm == "baseline":
                    check_baseline(row)
                else:
                    require(key in states["baseline"][population + "_trades"], "Candidate identity changed")
                    old = states["baseline"][population + "_trades"][key]
                    check_pair_path(old, row)
                    checked = check_candidate(row)
                    for name, value in checked.items():
                        confirmation_counts[arm + "/" + population][name] += value
                    check_accounting(row)
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
                accounting=dict(failed_launch_exits=counts, confirmation_lifecycle=confirmation_counts,
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


def check_mechanic_summary(old, new, groups, info):
    delays = [(stamp(new[k]["exit_time"]) - stamp(a["exit_time"])) / MINUTE for k, a in old.items()]
    changed = [(a, new[k]) for k, a in old.items() if a["outcome"] == "fast_failed_launch"]
    changes = [(number(z["net_return"]) - number(a["net_return"]) if
                number(z["net_return"], True) is not None and number(a["net_return"], True) is not None else None)
               for a, z in changed]
    derived = dict(total=len(old), known=groups["all"]["known"],
        baseline_failed_full_count=len(changed), candidate_confirmed_full_count=sum(r["outcome"] == "fast_failed_launch" for r in new.values()),
        unchanged_paths=len(old)-len(changed), pending_events=sum(number(r["failed_confirm_create_count"]) for r in new.values()),
        cancelled_pending_events=sum(number(r["failed_confirm_cancel_count"]) for r in new.values()),
        priority_terminated_pending_events=sum(number(r["failed_confirm_priority_termination_count"]) for r in new.values()),
        changed_improved=sum(v is not None and v > 1e-12 for v in changes), changed_hurt=sum(v is not None and v < -1e-12 for v in changes),
        changed_unknown_pairs=sum(v is None for v in changes), recovered_winners=sum(number(z["net_return"], True) is not None and number(z["net_return"]) > 0 for _, z in changed),
        newly_unknown=sum(boolean(a["closed"]) and not boolean(new[k]["closed"]) for k, a in old.items()),
        restored_partial_paths=sum(number(z["partial_fraction"]) == .5 for _, z in changed),
        baseline_partial_count=sum(number(r["partial_fraction"]) == .5 for r in old.values()),
        candidate_partial_count=sum(number(r["partial_fraction"]) == .5 for r in new.values()),
        later_exits=sum(v > 0 for v in delays), earlier_exits=sum(v < 0 for v in delays), same_exit_time=sum(v == 0 for v in delays))
    for field, value in derived.items():
        eq(info[field], value, "Confirmation mechanics summary drift: " + field)
    require(info["transitions"] == groups["transitions"], "Win/loss migration denominator drift")
    saved = {r["group"]: r for r in info["groups"]}
    require(len(saved) == len(info["groups"]) and saved.keys() == groups["transitions"].keys(), "Missing paired migration group")
    for name, row in saved.items():
        for field, value in groups[name].items():
            eq(row[field], value, "Migration arithmetic drift")


def verify_sources(root, results, summary):
    """Check every declared output and original git-show source, not live code."""
    started, config = read_json(results / "started.json"), read_json(results.parent / "config.json")
    require(started["sources"] == summary["sources"] and started["sources"], "Empty/changed source receipt")
    commit = started["builder_commit"]
    require(re.fullmatch(r"[a-f0-9]{40}", commit) is not None, "Invalid builder commit")
    identities = [row["path"] for row in started["sources"]]
    config_id = str(results.parent.relative_to(root)) + "/config.json"
    required = {"yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_failed_confirm_research.py",
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
    require(config["experiment_id"] == EXPERIMENT_ID and config["policies"] == [BASE_POLICY, CANDIDATE_POLICY], "More than confirmation count changed")
    require(sha(results.parent / "config.json") == summary["config_sha256"] ==
            next(row["sha256"] for row in started["sources"] if row["path"] == config_id), "Frozen configuration SHA mismatch")
    require(config["parent_results"] == PARENT, "Baseline is not original V17 candidate")
    for directory, key in ((config["parent_results"], "inputs"), (config["mother_results"], "mother_inputs"), (config["entry_context_results"], "entry_context_inputs")):
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
    """V17 six-table anchor and separately frozen native5/native15 context."""
    config, anchor = read_json(results.parent / "config.json"), read_json(results / "anchor_parity.json")
    for name, filename in TABLE_FILES.items():
        old = indexed(read_csv(safe_path(root, PARENT + "/" + filename)))
        new = indexed(tables["baseline"][name])
        require(old.keys() == new.keys(), "V17 six-table identity drift")
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
                if arm == "candidate":
                    confirmation_records.extend((arm, population, row["event_id"], record) for record in lifecycle(row))
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


def verify_mechanics_exports(results, tables, summary):
    """Independently check every saved paired row, group and monthly mean."""
    result = {}
    for population in ("case", "control"):
        old, new = (indexed(tables[arm][population + "_trades"]) for arm in ARMS)
        saved = indexed(read_csv(results / ("confirmed_" + population + "_mechanics.csv")))
        require(saved.keys() == old.keys() == new.keys(), "Mechanics lost original denominator")
        for key, row in saved.items():
            a, z = old[key], new[key]
            before, after = number(a["net_return"], True), number(z["net_return"], True)
            delta = after-before if before is not None and after is not None else None
            if delta is None:
                before, after = None, None
            for field, value in (("baseline_net_bp", bp(before)), ("candidate_net_bp", bp(after)), ("delta_net_bp", bp(delta)),
                                 ("exit_delay_minutes", (stamp(z["exit_time"])-stamp(a["exit_time"])) / MINUTE)):
                eq(row[field], value, "Mechanics arithmetic drift")
            same(row["mother_decision_time"], a["mother_decision_time"], "mother_decision_time")
            for arm, trade in (("baseline", a), ("candidate", z)):
                for suffix, field in (("exit_time", "exit_time"), ("exit_reason", "outcome"), ("mfe_r", "max_favourable_r"), ("hold_minutes", "hold_minutes")):
                    same(row[arm + "_" + suffix], trade[field], suffix)
            changed = a["outcome"] == "fast_failed_launch"
            flags = dict(baseline_failed_full=changed, candidate_confirmed_full=z["outcome"] == "fast_failed_launch",
                         candidate_partial_executed=number(z["partial_fraction"]) == .5,
                         recovered_winner=changed and after is not None and after > 0,
                         newly_unknown=boolean(a["closed"]) and not boolean(z["closed"]))
            for field, value in flags.items():
                require(boolean(row[field]) == value, "Mechanics classification drift: " + field)
            for field, source in (("candidate_pending_created", "failed_confirm_create_count"), ("candidate_pending_cancelled", "failed_confirm_cancel_count"),
                                  ("candidate_pending_terminated", "failed_confirm_priority_termination_count")):
                eq(row[field], z[source], "Mechanics pending count drift")
            transition = "flat_or_unknown" if before is None or after is None or before == 0 or after == 0 else ("win" if before > 0 else "loss") + "_to_" + ("win" if after > 0 else "loss")
            require(row["outcome_transition"] == transition, "Mechanics migration drift")
        exported = read_csv(results / ("confirmed_" + population + "_groups.csv"))
        groups = {r["group"]: r for r in exported}
        expected = {r["group"]: r for r in summary["mechanics"][population]["groups"]}
        require(len(groups) == len(exported) and groups.keys() == expected.keys(), "Mechanics group export drift")
        for key, row in expected.items():
            parity(row, groups[key])
        result[population + "_mechanics_rows"] = len(saved)
    monthly = defaultdict(list)
    for arm in ARMS:
        for row in tables[arm]["case_episodes"]:
            month = (h.EPOCH + timedelta(seconds=stamp(row["mother_decision_time"]) // 10**9)).strftime("%Y-%m")
            monthly[(arm, row["fold"], month)].append(number(row["episode_net_return"], True))
    rows = read_csv(results / "monthly_case_net.csv")
    saved = {(r["arm"], r["fold"], r["month"]): r for r in rows}
    require(len(saved) == len(rows) and saved.keys() == monthly.keys(), "Monthly paired denominator drift")
    for key, values in monthly.items():
        for field, value in (("n", len(values)), ("known", sum(v is not None for v in values)), ("mean_net_bp", bp(mean(values)))):
            eq(saved[key][field], value, "Monthly arithmetic drift")
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
    output["diagnostics"] = verify_mechanics_exports(results, tables, summary)
    output.update(receipts, summary_sha256=sha(summary_path), verifier_sources=[
        dict(path="scripts/" + path.name, sha256=sha(path)) for path in (Path(__file__), Path(v17.__file__), Path(h.__file__))])
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
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if output["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
