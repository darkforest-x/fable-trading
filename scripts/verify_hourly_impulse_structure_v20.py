"""Independently verify saved V20 hourly structure and support, using stdlib.

The input is the WHOLE saved complete-hour OHLC trace, not raw five-minute
prices. Reconstruct every 21-hour, tie-inclusive pivot and persistent state;
then join original own K1s and recompute all support denominators. This does
not certify raw aggregation, Pine pivot tie parity, cached economics, or profit.
No feature, simulator, accounting, or other local module is imported.

CSV and explicit UTC clocks follow Python's documented stdlib contracts:
https://docs.python.org/3/library/csv.html
https://docs.python.org/3/library/datetime.html
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20"
EXPERIMENT_PATH = "experiments/active/" + EXPERIMENT_ID
BASE_PATH = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
BASE_SHA256 = "95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a"
PARENT_PATH = "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
INPUTS = {
    "original_mothers.csv.gz": "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
    "control_mothers.csv.gz": "01050c7a9602f469406df515edcc73ef2f4c9db2d46529e25030934012eebd5a",
    "assignments.csv": "671782877ee67824f7687243d5e7deae29d78a0bcba6245319ecf55629027b0f",
    "assignment_receipt.json": "1d77ca407712520e645463d30f97d26d452ccce45e87e68c2adcbc4120c43220",
}
PINE = "experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/0vET13Ra.pine"
PINE_SHA256 = "3a714019441695693642f4487754a56d8d55a0c9dcc280606abea6ff8cd66b52"
SOURCE_FILES = {
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_structure.py",
    "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_support_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_prior_breakout_research.py",
    "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_management_research.py",
    "yoyo/evaluation/hourly_impulse_failed_launch_research.py",
    "yoyo/evaluation/hourly_impulse_structure_research.py",
    "yoyo/evaluation/hourly_impulse_structure_accounting.py",
    "tests/test_hourly_impulse_structure.py",
    "tests/test_hourly_impulse_structure_accounting.py",
    "tests/test_hourly_impulse_structure_research.py",
    PINE,
}
RESUME_MUTABLE_SOURCES = {
    "yoyo/evaluation/hourly_impulse_structure_research.py",
    "yoyo/evaluation/hourly_impulse_structure_accounting.py",
    "tests/test_hourly_impulse_structure_research.py",
    "tests/test_hourly_impulse_structure_accounting.py",
}
CSV_NAMES = {name + ".csv.gz" for name in
             ("entry_context", "hourly_trace", "counts", "matched_support")}
NS = 10**9
HOUR = 3600 * NS
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FOLDS = {"2023H1": ("2023-01-01", "2023-07-01"),
         "2023H2": ("2023-07-01", "2024-01-01"),
         "2024H1": ("2024-01-01", "2024-07-01"),
         "2024H2": ("2024-07-01", "2025-01-01")}
STATES = ("accepted", "abstain", "unknown")
SUPPORT = {"minimum_events": 80, "minimum_per_fold": 12,
           "minimum_active_months": 12, "minimum_months_per_fold": 3}
GATE = {"left": 10, "right": 10, "window": 21,
        "pivot_rule": "centre_equals_window_extreme_ties_allowed",
        "pine_builtin_tie_parity_verified": False,
        "unchanged_level_price_required": True, "persistent": True,
        "decision": "own_K1_close_after_state_update", "gap_resets_state_and_levels": True,
        "no_break_on_k1_requirement": True, "own_controls": True,
        "unknown_is_abstention": False, "length_search": False}
TIME_FIELDS = {"structure_available_at", "structure_high_origin", "structure_low_origin",
               "structure_high_confirmed_at", "structure_low_confirmed_at",
               "structure_last_break_available_at"}
BOOL_FIELDS = {"structure_break_on_k1", "structure_known"}
INTEGER_FIELDS = {"structure_count", "structure_segment_id", "structure_state_before",
                  "structure_state", "structure_break_direction"}
NUMBER_FIELDS = {"structure_high", "structure_low", "structure_signal_close"}
TRACE_FIELDS = TIME_FIELDS | BOOL_FIELDS | INTEGER_FIELDS | NUMBER_FIELDS | {"structure_reason"}
OUTCOME_COLUMN = re.compile(r"(^|_)(pnl|returns?|mfe|mae|outcome|closed)($|_)", re.I)


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value)) or (
        isinstance(value, str) and value.strip().lower() in {"", "nan", "nat", "none", "<na>"})


def number(value, nullable=False):
    if missing(value):
        require(nullable, "Unexpected missing number")
        return None
    require(not isinstance(value, bool), "Boolean is not a number")
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise VerificationError("Invalid number") from error
    require(math.isfinite(result), "Nonfinite number")
    return result


def equal_number(actual, expected, message):
    a, b = number(actual, True), number(expected, True)
    require(a is None and b is None or a is not None and b is not None
            and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), message)


def boolean(value):
    if value is True or value == "True" or value == "true":
        return True
    if value is False or value == "False" or value == "false":
        return False
    raise VerificationError("Explicit boolean required")


def stamp(value, nullable=False):
    """Exact nanosecond ISO clock, rejecting naive/numeric timestamps."""
    if missing(value):
        require(nullable, "Missing timestamp")
        return None
    require(isinstance(value, (str, datetime)), "Explicit timezone-aware timestamp required")
    text = value.isoformat() if isinstance(value, datetime) else value
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", text)
    require(match is not None, "Invalid or naive timestamp")
    base, fraction, zone = match.groups()
    date = datetime.fromisoformat(base + ("+00:00" if zone == "Z" else zone))
    delta = date.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * NS + int((fraction or "").ljust(9, "0"))


def day(value):
    return stamp(value + "T00:00:00Z")


def month(value):
    return datetime.fromtimestamp(stamp(value) // NS, tz=timezone.utc).strftime("%Y-%m")


def indexed(rows, key="event_id"):
    result = {}
    for row in rows:
        identity = row.get(key)
        require(isinstance(identity, str) and identity.strip() and identity not in result,
                "Missing/duplicate identity: " + key)
        result[identity] = row
    return result


def parity(originals, current):
    """Every original request field, never only selected identities."""
    old, new = indexed(originals), indexed(current)
    require(old.keys() == new.keys(), "Frozen request identities changed")
    require([r["event_id"] for r in originals] == [r["event_id"] for r in current],
            "Frozen request order changed")
    for identity, before in old.items():
        after = new[identity]
        require(before.keys() <= after.keys(), "Original request column deleted")
        for field, value in before.items():
            other = after[field]
            if missing(value) or missing(other):
                require(missing(value) and missing(other), "Original request missingness changed: " + field)
            elif field.endswith(("_time", "_available", "_at", "_until", "_deadline", "_bar_open")):
                require(stamp(value) == stamp(other), "Original request clock changed: " + field)
            elif value == other or str(value) == str(other):
                continue
            else:
                equal_number(value, other, "Original request value changed: " + field)


def reconstruct_trace(trace):
    """Independent sliding slices, not feature code or supplied pivot metadata."""
    facts = {}
    continuous = []
    last_time = None
    segment = -1
    for row in trace:
        require(TRACE_FIELDS <= row.keys(), "Hourly trace omitted state metadata")
        time = stamp(row["open_time"])
        require(time % HOUR == 0 and time < day("2025-01-01")
                and (last_time is None or time > last_time), "Future/offgrid/duplicate/unordered hour")
        o, high, low, close = (number(row[field]) for field in ("open", "high", "low", "close"))
        require(0 < low <= min(o, close) <= max(o, close) <= high, "Invalid hourly OHLC")
        if last_time is None or time != last_time + HOUR:
            continuous = []
            segment += 1
            state = high_level = low_level = previous_close = None
            high_origin = low_origin = high_confirmed = low_confirmed = last_break = None
        count = len(continuous) + 1
        old_high, old_low, before = high_level, low_level, state
        continuous.append((time, high, low))
        if len(continuous) >= 21:
            window = continuous[-21:]
            centre = window[10]
            if all(centre[1] >= item[1] for item in window):
                high_level, high_origin, high_confirmed = centre[1], centre[0], time + HOUR
            if all(centre[2] <= item[2] for item in window):
                low_level, low_origin, low_confirmed = centre[2], centre[0], time + HOUR
        change = 0
        if (high_level is not None and old_high == high_level and previous_close is not None
                and previous_close <= high_level < close and state != 1):
            change = 1
        if (low_level is not None and old_low == low_level and previous_close is not None
                and previous_close >= low_level > close and state != -1):
            require(change == 0, "Contradictory simultaneous directional crosses")
            change = -1
        if change:
            state, last_break = change, time + HOUR
        values = dict(structure_available_at=time + HOUR, structure_count=count,
            structure_segment_id=segment, structure_state_before=before, structure_state=state,
            structure_break_direction=change, structure_high=high_level, structure_low=low_level,
            structure_high_origin=high_origin, structure_low_origin=low_origin,
            structure_high_confirmed_at=high_confirmed, structure_low_confirmed_at=low_confirmed,
            structure_last_break_available_at=last_break, structure_signal_close=close,
            structure_break_on_k1=change != 0, structure_known=state is not None,
            structure_reason="known" if state is not None else "warmup" if count < 21 else "no_confirmed_break")
        verify_state(row, values)
        if "segment_id" in row:
            equal_number(row["segment_id"], segment, "Hourly aggregation segment changed")
        facts[time] = values
        last_time, previous_close = time, close
    return facts


def verify_state(row, values):
    for field, expected in values.items():
        require(field in row, "Missing structure field: " + field)
        if field in TIME_FIELDS:
            require(stamp(row[field], True) == expected, "Pivot/state clock drift: " + field)
        elif field in BOOL_FIELDS:
            require(boolean(row[field]) is expected, "State boolean drift: " + field)
        elif field in INTEGER_FIELDS:
            actual = number(row[field], True)
            require(actual == expected, "State integer drift: " + field)
        elif field in NUMBER_FIELDS:
            equal_number(row[field], expected, "Pivot/state value drift: " + field)
        else:
            require(row[field] == expected, "State reason drift")


def verify_context(context, trace_facts, mothers, controls, assignments, expected_counts):
    n, m, k = expected_counts
    require(len(mothers) == n and len(controls) == m and len(assignments) == n,
            "Original request/assignment denominator changed")
    require(len(context) == n + m, "Deleted or added opportunity, including unknown")
    own = indexed(context)
    old = indexed(mothers)
    assigned = indexed(assignments)
    require(assigned.keys() == old.keys(), "Assignments no longer contain all original mothers")
    require(all(row["population"] in ("case", "control") for row in context), "Foreign population")
    for population, originals in (("case", mothers), ("control", controls)):
        parity(originals, [row for row in context if row["population"] == population])
    parent_controls = defaultdict(list)
    times = set()
    for row in controls:
        parent = row["parent_event_id"]
        require(parent in old and row["fold"] == old[parent]["fold"], "Orphan or crossfold control")
        time = stamp(row["decision_time"])
        require(time not in times, "Control time reused")
        times.add(time)
        parent_controls[parent].append(row["event_id"])
    matched_ids = {key for key, row in assigned.items() if row["match_status"] == "matched"}
    require(set(parent_controls) == matched_ids and len(matched_ids) == k
            and all(len(ids) == 3 for ids in parent_controls.values()), "Frozen three-control groups changed")
    require(all(row["match_status"] in {"matched", "insufficient_exact_controls", "missing_causal_matching_support"}
                for row in assignments), "Unknown historical matching status")
    if expected_counts == (251, 462, 154):
        require(Counter(r["match_status"] for r in assignments) == {
            "matched": 154, "insufficient_exact_controls": 94, "missing_causal_matching_support": 3},
            "Historical unmatched categories changed")
    facts = {}
    for row in context:
        signal, decision = stamp(row["signal_time"]), stamp(row["decision_time"])
        require(signal % HOUR == 0 and signal + HOUR == decision, "Own K1 close clock changed")
        require(row["fold"] in FOLDS, "Foreign fold")
        start, end = FOLDS[row["fold"]]
        require(day(start) <= decision < day(end) - 72 * HOUR, "Decision outside fold/72h embargo")
        direction = number(row["direction"])
        require(direction in (-1, 1), "Invalid direction")
        expected = trace_facts.get(signal)
        if expected is None:
            require(row["structure_reason"] in {"no_source", "missing_signal_hour"}, "Absent own hour reason drift")
            absent = {field: None for field in TRACE_FIELDS}
            absent.update(structure_available_at=decision, structure_count=0,
                structure_break_direction=0, structure_known=False, structure_break_on_k1=False,
                structure_reason=row["structure_reason"])
            verify_state(row, absent)
            require(missing(row["structure_raw_segment_id"]), "Absent own hour received raw segment")
            state = "unknown"
        else:
            verify_state(row, expected)
            equal_number(row["signal_close"], expected["structure_signal_close"], "Wrong own K1 close")
            raw_segment = number(row["structure_raw_segment_id"])
            require(raw_segment >= 0 and raw_segment == int(raw_segment), "Invalid raw segment label")
            state = "unknown" if not expected["structure_known"] else (
                "accepted" if expected["structure_state"] == direction else "abstain")
        require(row["structure_gate_state"] == state, "Own gate copied, unknown coerced, or direction changed")
        facts[row["event_id"]] = state
    if trace_facts:
        require(max(trace_facts) + HOUR <= max(stamp(r["decision_time"]) for r in context),
                "Exported hourly trace contains post-request future")
    for identity, row in assigned.items():
        for field in ("fold", "decision_time"):
            if field in row:
                require((stamp(row[field]) == stamp(old[identity][field])) if field == "decision_time"
                        else row[field] == old[identity][field], "Assignment own clock/fold drift")
    return own, facts, parent_controls


def expected_counts_table(context, states):
    months = ["%d-%02d" % (year, mon) for year in (2023, 2024) for mon in range(1, 13)]
    result = {}
    for population in ("case", "control"):
        rows = [row for row in context if row["population"] == population]
        for dimension, keys in {"all": ["all"], "fold": list(FOLDS),
                                "direction": ["1", "-1"], "month": months}.items():
            for key in keys:
                part = [row for row in rows if dimension == "all" or
                        (row["fold"] == key if dimension == "fold" else
                         number(row["direction"]) == int(key) if dimension == "direction" else
                         month(row["decision_time"]) == key)]
                count = Counter(states[row["event_id"]] for row in part)
                result[(population, dimension, key)] = dict(total=len(part),
                    **{s: count[s] for s in STATES},
                    accepted_rate=count["accepted"] / len(part) if part else None)
    return result


def verify_tables(context, hourly_trace, counts, matched, summary, *, mothers, controls,
                  assignments, expected_counts=(251, 462, 154)):
    trace_facts = reconstruct_trace(hourly_trace)
    own, states, groups = verify_context(context, trace_facts, mothers, controls, assignments, expected_counts)
    expected = expected_counts_table(context, states)
    actual = {(row["population"], row["dimension"], row["key"]): row for row in counts}
    require(len(actual) == len(counts) == 62 and actual.keys() == expected.keys(),
            "Counts omitted/duplicated fixed rows or zero months")
    for key, values in expected.items():
        for field, value in values.items():
            equal_number(actual[key][field], value, "Support count denominator/rate drift")
    pairs = indexed(matched)
    require(pairs.keys() == groups.keys(), "Matched support omitted or rematched original groups")
    all_known = 0
    for parent, ids in groups.items():
        row = pairs[parent]
        require(row["fold"] == own[parent]["fold"] and row["case_state"] == states[parent], "Matched parent drift")
        require(row["control_ids"] == "|".join(sorted(ids)), "Matched control IDs changed")
        count = Counter(states[identity] for identity in ids)
        for field, value in {"total": 3, **{s: count[s] for s in STATES}}.items():
            equal_number(row["control_" + field], value, "Matched control denominator drift")
        known = states[parent] != "unknown" and count["unknown"] == 0
        require(boolean(row["all_known"]) is known, "Unknown matched opportunity lost")
        all_known += known
    population = {p: {f: expected[(p, "all", "all")][f] for f in ("total", *STATES)}
                  for p in ("case", "control")}
    require(summary["population"] == population, "Summary population/gate counts drift")
    accepted = [r for r in context if r["population"] == "case" and states[r["event_id"]] == "accepted"]
    fold_counts = Counter(row["fold"] for row in accepted)
    fold_months = {fold: {month(r["decision_time"]) for r in accepted if r["fold"] == fold} for fold in FOLDS}
    values = dict(events=len(accepted), minimum_fold_events=min(fold_counts[f] for f in FOLDS),
        active_months=len({month(r["decision_time"]) for r in accepted}),
        minimum_fold_months=min(len(v) for v in fold_months.values()))
    gates = dict(minimum_events=values["events"] >= 80,
        minimum_per_fold=values["minimum_fold_events"] >= 12,
        minimum_active_months=values["active_months"] >= 12,
        minimum_months_per_fold=values["minimum_fold_months"] >= 3)
    require(summary["support_values"] == values and summary["support_gates"] == gates,
            "Support decision used wrong population or threshold")
    require(summary["support_pass"] is all(gates.values()), "Support requires every fixed gate")
    n, m, k = expected_counts
    for field, expected_value in {"assigned": k, "unassigned": n-k, "coverage": k/n, "required": .9}.items():
        equal_number(summary["matching"][field], expected_value, "Original matching denominator changed")
    require(summary["matching"]["pass"] is False, "Inherited coverage claimed sufficient")
    if not summary["support_pass"]:
        require(summary["status"] == "insufficient_support_no_outcomes" and summary["outcomes_read"] is False,
                "Insufficient support accessed outcomes")
    else:
        require(summary["status"] == "fixed_episode_gate_comparison_not_independent_validation"
                and summary["outcomes_read"] is True, "Unexpected support-pass run status")
    for field in ("holdout_consumed", "training_eligible", "production_eligible", "independent_validation", "overall_goal_achieved"):
        require(summary[field] is False, "Unsupported validation/profit/eligibility claim")
    equal_number(summary["new_intrabar_replays"], 0, "Unexpected market replay")
    return dict(status="passed", support_status=summary["status"], population=population,
        support_values=values, support_gates=gates, hourly_rows=len(hourly_trace),
        matched_groups=k, unmatched=n-k, matched_all_known=all_known, count_rows=len(counts),
        independent_hourly_state_recomputed=True, raw_aggregation_verified=False,
        pine_builtin_tie_parity_verified=False, economics_verified=False,
        raw_price_archive_read=False, saved_hourly_ohlc_read=True,
        limitation="Independent state reconstruction GIVEN saved complete-hour OHLC; raw12x5m aggregation, raw segment labels, absent-hour reason subtype, cached economics and profit are not independently verified.")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_path(root, relative):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
            and ".." not in Path(relative).parts, "Unsafe relative path")
    path = Path(root) / relative
    require(path.resolve().is_relative_to(Path(root).resolve()) and not path.is_symlink(), "Path escaped root or symlink")
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path):
    require(Path(path).is_file() and not Path(path).is_symlink(), "Missing CSV or symlink")
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        require(columns and len(columns) == len(set(columns)), "Missing/duplicate CSV fields")
        require(not any(OUTCOME_COLUMN.search(f) or f in {"exit_time", "exit_price", "net_r", "policy_fee_fraction"}
                        or f.startswith("max_favourable") for f in columns), "Outcome schema in support input")
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows), "Ragged CSV")
    return rows


def verify_config(config):
    require(config["experiment_id"] == EXPERIMENT_ID and config["parent_requests"] == PARENT_PATH
            and config["base_config"] == BASE_PATH and config["base_config_sha256"] == BASE_SHA256,
            "Wrong frozen V20 lineage")
    require(config["request_inputs"] == INPUTS and config["gate"] == GATE and config["support"] == SUPPORT,
            "Frozen request, structure, or support contract changed")
    require(config["development_folds"] == [[f, *bounds] for f, bounds in FOLDS.items()]
            and config["phase_end_exclusive"] == "2025-01-01", "Development prefix changed")
    require(config["outcome_read_rule"] == "only_after713context_freeze_and_all_support_gates_pass",
            "Outcome read rule changed")
    require(config["matching_coverage_required"] == .9, "Coverage requirement weakened")
    require(config["fixed_execution"] == dict(policy="15m_native40_failed_confirm2",
        cost_fraction=.002, max_hours=72, stop="K1_extreme", unchanged_cached_episodes=True,
        new_intrabar_replays=0, serial_recomputed_per_arm=True), "Inherited execution contract changed")
    require(config["expected"] == dict(mothers=251, controls=462, matched=154,
        status_counts={"matched":154, "insufficient_exact_controls":94, "missing_causal_matching_support":3}),
        "Frozen population contract changed")
    for field in ("holdout_consumed", "training_eligible", "production_eligible"):
        require(config[field] is False, "Eligibility/holdout contract changed")


def verify(results, summary_path=None, *, root=ROOT):
    root, results = Path(root), Path(results)
    require(results.resolve() == (root/EXPERIMENT_PATH/"results").resolve(), "Wrong V20 results directory")
    experiment = results.parent
    summary_path = Path(summary_path) if summary_path else results/"summary.json"
    require(summary_path.resolve() == (results/"summary.json").resolve(), "Foreign summary")
    summary, config, started, frozen = [read_json(p) for p in
        (summary_path, experiment/"config.json", results/"started.json", results/"context_frozen.json")]
    verify_config(config)
    resumed = "resume_sources" in summary
    require((results/"failure.json").exists() is resumed, "Failure requires an explicit preserved frozen-accounting resume")
    require(summary["experiment_id"] == EXPERIMENT_ID, "Foreign summary experiment")
    require(sha(experiment/"config.json") == summary["config_sha256"], "Config hash mismatch")
    require(sha(root/BASE_PATH) == BASE_SHA256 and sha(root/PINE) == PINE_SHA256, "Base/Pine source hash mismatch")
    base = read_json(root/BASE_PATH)
    require(summary["sources"] == started["sources"], "Started/summary source pins changed")
    sources = started["sources"]
    source_map = {r["path"]: r["sha256"] for r in sources}
    required = SOURCE_FILES | {BASE_PATH, EXPERIMENT_PATH+"/config.json", EXPERIMENT_PATH+"/PROJECT_PLAN.md"}
    require(len(source_map) == len(sources) and required <= source_map.keys(), "Source freeze missing required builder files")
    for relative, expected in source_map.items():
        require(relative in required or relative.startswith(("yoyo/", "scripts/", "tests/")) and relative.endswith(".py"),
                "Non-source path in source hash list")
        path = safe_path(root, relative)
        if not resumed or relative not in RESUME_MUTABLE_SOURCES:
            require(sha(path) == expected, "Unchanged feature/contract source bytes changed: " + relative)
        committed = subprocess.run(["git", "show", started["builder_commit"]+":"+relative],
            cwd=root, check=True, capture_output=True).stdout
        require(hashlib.sha256(committed).hexdigest() == expected, "Builder commit differs from source receipt")
    commit_seconds = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", started["builder_commit"]], cwd=root, text=True))
    require(commit_seconds*NS <= stamp(started["at"]) <= stamp(frozen["at"]) <= stamp(summary["generated_at"]),
            "Source/start/freeze/summary clock order changed")
    resumed_at = None
    if resumed:
        require(summary["support_pass"] is True and summary["outcomes_read"] is True,
                "Frozen accounting resume requires passing support")
        require(summary["preserved_first_failure"] == "failure.json"
                and summary["frozen_features_recomputed"] is False,
                "Resume did not preserve failure or regenerated features")
        failure = read_json(results/"failure.json")
        require(failure["status"] == "failed_not_evidence", "Original failure was rewritten as success")
        resume_receipt = read_json(results/"outcomes_resumed_1.json")
        resumed_at = stamp(resume_receipt["at"])
        require(resume_receipt["context_frozen_sha256"] == sha(results/"context_frozen.json")
                and resume_receipt["cached_fixed_episode_accounting_only"] is True
                and resume_receipt["intrabar_replays"] == 0, "Resume changed features or replayed market prices")
        resume_rows = summary["resume_sources"]
        resume_map = {r["path"]: r["sha256"] for r in resume_rows}
        require(len(resume_map) == len(resume_rows) and source_map.keys() <= resume_map.keys(),
                "Resume source receipt omitted original producer files")
        for relative, expected in resume_map.items():
            require(relative in required or relative.startswith(("yoyo/", "scripts/", "tests/"))
                    and relative.endswith(".py"), "Non-source path in resume receipt")
            if relative in source_map and relative not in RESUME_MUTABLE_SOURCES:
                require(expected == source_map[relative], "Resume altered feature, source, request, or contract")
            require(sha(safe_path(root, relative)) == expected, "Resumed source bytes changed")
            committed = subprocess.run(["git", "show", summary["resume_builder_commit"]+":"+relative],
                cwd=root, check=True, capture_output=True).stdout
            require(hashlib.sha256(committed).hexdigest() == expected, "Resume source was not committed first")
        resume_seconds = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", summary["resume_builder_commit"]],
            cwd=root, text=True))
        require(stamp(frozen["at"]) <= stamp(failure["at"])
                and stamp(failure["at"]) // NS <= resume_seconds
                and resume_seconds*NS <= resumed_at <= stamp(summary["generated_at"]),
                "Failure/resume commit/access clock order drift")
    require(frozen["requests"] == 713 and frozen["outcomes_read"] is False, "Context freeze incomplete or post-outcome")
    require(set(frozen["output_hashes"]) == CSV_NAMES, "Support checkpoint omitted/added tables")
    for name in CSV_NAMES:
        expected = frozen["output_hashes"][name]
        require(summary["output_hashes"][name] == expected and sha(safe_path(results, name)) == expected,
                "Frozen support output hash mismatch")
    receipt = summary["source_receipt"]
    require(receipt == frozen["source_receipt"], "Phase source receipt changed")
    require(receipt["sha256"] == base["source"]["sha256"] and receipt["holdout_price_rows"] == 0
            and stamp(receipt["phase_price_last_open"]) < day("2025-01-01")
            and stamp(receipt["physical_last_open"]) < day("2026-05-04"), "Source receipt violates development/holdout boundary")
    for name, expected in INPUTS.items():
        require(sha(safe_path(root, PARENT_PATH+"/"+name)) == expected, "Frozen original request input hash changed")
    mothers, controls, assignments = [read_csv(root/PARENT_PATH/name) for name in
        ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv")]
    context, trace, counts, matched = [read_csv(results/(name+".csv.gz")) for name in
        ("entry_context", "hourly_trace", "counts", "matched_support")]
    output = verify_tables(context, trace, counts, matched, summary,
        mothers=mothers, controls=controls, assignments=assignments)
    if summary["support_pass"]:
        outcome_start = read_json(results/"outcomes_started.json")
        require(stamp(frozen["at"]) <= stamp(outcome_start["at"]) <= stamp(summary["generated_at"])
                and outcome_start["context_frozen_sha256"] == sha(results/"context_frozen.json"),
                "Outcome accounting precedes frozen entry support")
        require(outcome_start["cached_fixed_episode_accounting_only"] is True
                and outcome_start["intrabar_replays"] == 0, "Original access receipt admits market replay")
        if resumed:
            require(stamp(outcome_start["at"]) <= stamp(failure["at"]) <= resumed_at,
                    "Original outcome access/failure was not preserved before resume")
    else:
        require(not (results/"outcomes_started.json").exists(), "Failed support has outcome-start receipt")
        require(set(summary["output_hashes"]) == CSV_NAMES, "Failed support produced outcome tables")
    output.update(support_output_hashes_verified=4, request_input_hashes_verified=4,
        source_hashes_verified=len(sources), builder_commit=started["builder_commit"],
        resumed_accounting=resumed, resume_builder_commit=summary.get("resume_builder_commit"),
        summary_sha256=sha(summary_path), verifier_sha256=sha(Path(__file__)))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        output = verify(args.results or args.root/EXPERIMENT_PATH/"results", args.summary, root=args.root)
        if args.out:
            require(not args.out.exists(), "Preserve existing verification receipt")
            args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (VerificationError, ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps(dict(status="failed", error=str(error), economics_verified=False), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
