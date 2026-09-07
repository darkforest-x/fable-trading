"""Independent, stdlib-only V25 saved-hourly support audit.

No yoyo imports, raw5 access, labels, outcomes, or inference. Reconstruct the
10-left/10-right tie-inclusive approximation using complete-hour OHLC lists,
then independently count all original own-clock requests and frozen triples.
This verifies saved-hour semantics, not raw5 aggregation or Pine builtin ties.
Source contract: yoyo/data/hourly_impulse_structure.py (reviewed, not imported).
Run only after this script and the strategy sources have been committed.
The script prints JSON; the caller may preserve that receipt without altering
the original results. Python CSV values remain lexical until clock preflight.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = Path(__file__).resolve().parent
V20 = Path("experiments/active/exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20/results")
V24 = Path("experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results")
V4 = Path("experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz")
TRACE_SHA = "be9b9e73108047ada00ffac0ed0c4d5b2a3c137000435872c2d33c4ec1cbf7dd"
OUTPUTS = {"case_context.csv.gz", "control_context.csv.gz", "counts.csv.gz", "matched_support.csv.gz"}
HOUR = timedelta(hours=1)
TIMES = ("structure_available_at", "structure_high_origin", "structure_low_origin",
         "structure_high_confirmed_at", "structure_low_confirmed_at", "structure_last_break_available_at")
INTS = ("structure_count", "structure_segment_id", "structure_state_before",
        "structure_state", "structure_break_direction")
BOOLS = ("structure_break_on_k1", "structure_known", "structure_event_known", "matched_support",
         "complete_known", "accepted_case_complete_known")
NUMBERS = ("structure_high", "structure_low", "structure_signal_close", "accepted_rate")
FOLDS = {"2023H1": ("2023-01-01T00:00:00Z", "2023-07-01T00:00:00Z"),
         "2023H2": ("2023-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
         "2024H1": ("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
         "2024H2": ("2024-07-01T00:00:00Z", "2025-01-01T00:00:00Z")}
MONTHS = ["%d-%02d" % (year, month) for year in (2023, 2024) for month in range(1, 13)]


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def missing(value):
    return value is None or value == ""


def clock(value, nullable=False, hour=True):
    if nullable and missing(value):
        return None
    require(isinstance(value, str), "Explicit timestamp string required")
    # datetime truncates sub-microseconds; reject them before parsing hour clocks.
    fraction = re.search(r"\.(\d+)(?:Z|[+-]\d\d:\d\d)$", value)
    require(not hour or not fraction or not any(c != "0" for c in fraction[1]), "Subhour clock")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None and result.utcoffset() == timedelta(0), "Explicit UTC required")
    require(not hour or (result.minute, result.second, result.microsecond) == (0, 0, 0), "Hour grid")
    return result.astimezone(timezone.utc)


def flag(value):
    if value in ("True", "true") or value is True:
        return True
    if value in ("False", "false") or value is False:
        return False
    raise AssertionError("Invalid boolean %r" % value)


def number(value, nullable=False):
    if nullable and missing(value):
        return None
    require(not isinstance(value, bool), "Boolean is not numeric")
    result = float(value)
    require(math.isfinite(result), "Nonfinite number")
    return result


def integer(value, nullable=False):
    result = number(value, nullable)
    require(result is None or result == int(result), "Noninteger")
    return None if result is None else int(result)


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_csv(path, columns=None):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames is not None and len(reader.fieldnames) == len(set(reader.fieldnames)), "CSV schema")
        require(columns is None or set(columns).issubset(reader.fieldnames), "Missing CSV columns")
        return [row if columns is None else {c: row[c] for c in columns} for row in reader]


def indexed(rows, key="event_id"):
    result = {r[key]: r for r in rows}
    require(len(result) == len(rows) and all(result), "Duplicate or empty identities")
    return result


def request_value(key, value):
    if key in ("signal_time", "decision_time"):
        return clock(value)
    if key in ("direction", "control_slot"):
        return integer(value, nullable=True)
    if key in ("matched_support", "is_engulf", "breakout20"):
        return flag(value)
    if key in ("event_id", "fold", "request_kind", "mother_id", "mother_month"):
        return value
    return number(value, nullable=True)


def equal(actual, expected, label):
    if expected is None:
        require(missing(actual), label + ": expected missing")
    elif isinstance(expected, bool):
        require(flag(actual) == expected, label)
    elif isinstance(expected, datetime):
        require(clock(actual) == expected, label)
    elif isinstance(expected, int):
        require(integer(actual) == expected, label)
    elif isinstance(expected, float):
        require(math.isclose(number(actual), expected, rel_tol=1e-12, abs_tol=1e-12), label)
    else:
        require(actual == expected, label)


def reconstruct(rows):
    """Independent sequential saved-hour replay; no use of saved state as input."""
    output, segment_rows = [], []
    last_time, segment = None, -1
    for row in rows:
        time = clock(row["open_time"])
        require(last_time is None or time > last_time, "Trace order/duplicate")
        o, high, low, close = [number(row[c]) for c in ("open", "high", "low", "close")]
        require(min(o, high, low, close) > 0 and low <= min(o, close) <= max(o, close) <= high, "Invalid saved OHLC")
        if last_time is None or time != last_time + HOUR:
            segment += 1
            segment_rows = []
            level_high = level_low = high_origin = low_origin = high_confirmed = low_confirmed = None
            state = last_break = prior_close = None
        before = state
        old_high, old_low = level_high, level_low
        segment_rows.append((time, high, low))
        available = time + HOUR
        if len(segment_rows) >= 21:
            window = segment_rows[-21:]
            centre = window[10]
            if all(centre[1] >= item[1] for item in window):
                level_high, high_origin, high_confirmed = centre[1], centre[0], available
            if all(centre[2] <= item[2] for item in window):
                level_low, low_origin, low_confirmed = centre[2], centre[0], available
        up = (level_high is not None and old_high == level_high and prior_close is not None
              and prior_close <= level_high < close and before != 1)
        down = (level_low is not None and old_low == level_low and prior_close is not None
                and prior_close >= level_low > close and before != -1)
        require(not (up and down), "Contradictory break directions")
        change = 1 if up else -1 if down else 0
        if change:
            state, last_break = change, available
        output.append({"open_time": time, "structure_available_at": available,
                       "structure_high_origin": high_origin, "structure_low_origin": low_origin,
                       "structure_high_confirmed_at": high_confirmed, "structure_low_confirmed_at": low_confirmed,
                       "structure_last_break_available_at": last_break, "structure_count": len(segment_rows),
                       "structure_segment_id": segment, "structure_state_before": before, "structure_state": state,
                       "structure_break_direction": change, "structure_high": level_high, "structure_low": level_low,
                       "structure_signal_close": close, "structure_break_on_k1": bool(change),
                       "structure_known": state is not None,
                       "structure_reason": "known" if state is not None else "warmup" if len(segment_rows) < 21 else "no_confirmed_break"})
        last_time, prior_close = time, close
    return output


def contexts(requests, states):
    lookup = {r["open_time"]: r for r in states}
    result = []
    for request in requests:
        t, e, direction = clock(request["signal_time"]), clock(request["decision_time"]), integer(request["direction"])
        require(e == t + HOUR and direction in (-1, 1), "Own clock/direction")
        start, end = map(clock, FOLDS[request["fold"]])
        require(start <= e < end - timedelta(hours=72), "Fold embargo")
        if t in lookup:
            own = {k: v for k, v in lookup[t].items() if k != "open_time"}
            if "signal_close" in request:
                equal(request["signal_close"], own["structure_signal_close"], "Own saved case close")
        else:
            own = {k: None for k in (*TIMES, *INTS, "structure_high", "structure_low", "structure_signal_close")}
            own.update(structure_available_at=e, structure_count=0, structure_break_direction=0,
                       structure_break_on_k1=False, structure_known=False,
                       structure_reason="no_source" if not states or states[0]["open_time"] >= e else "missing_signal_hour")
        known, event = own["structure_known"], own["structure_break_on_k1"]
        accepted = known and event and own["structure_break_direction"] == direction
        own.update(structure_gate_state=("accepted" if own["structure_state"] == direction else "abstain") if known else "unknown",
                   structure_event_known=known, structure_event_gate_state="accepted" if accepted else "abstain" if known else "unknown",
                   structure_event_reason="directional_break" if accepted else "opposite_break" if known and event else "no_current_break" if known else own["structure_reason"])
        result.append({**request, **own})
    return result


def tally(rows):
    counts = Counter(r["structure_event_gate_state"] for r in rows)
    require(set(counts).issubset({"accepted", "abstain", "unknown"}), "Gate state")
    return {"total": len(rows), "accepted": counts["accepted"], "abstain": counts["abstain"],
            "unknown": counts["unknown"], "known": len(rows) - counts["unknown"]}


def rebuild_support(cases, controls):
    counts, matched = [], []
    for population, rows in (("case", cases), ("control", controls)):
        for dimension, keys in (("all", ["all"]), ("fold", list(FOLDS)), ("direction", ["1", "-1"]), ("month", MONTHS)):
            for key in keys:
                group = [r for r in rows if dimension == "all" or (r["fold"] if dimension == "fold" else
                         str(integer(r["direction"])) if dimension == "direction" else clock(r["decision_time"]).strftime("%Y-%m")) == key]
                values = tally(group)
                counts.append({"population": population, "dimension": dimension, "key": key, **values,
                               "accepted_rate": values["accepted"] / values["total"] if values["total"] else None})
    for case in cases:
        group = sorted([r for r in controls if r["mother_id"] == case["event_id"]], key=lambda r: integer(r["control_slot"]))
        values = tally(group)
        require(len(group) in (0, 3), "Frozen triple size")
        complete = case["structure_event_known"] and len(group) == 3 and values["unknown"] == 0
        matched.append({"event_id": case["event_id"], "fold": case["fold"], "case_state": case["structure_event_gate_state"],
                        "matched_support": bool(group), "control_ids": "|".join(r["event_id"] for r in group),
                        "control_total": len(group), **{"control_" + k: values[k] for k in ("accepted", "abstain", "unknown")},
                        "complete_known": complete, "accepted_case_complete_known": complete and case["structure_event_gate_state"] == "accepted"})
    accepted = [r for r in cases if r["structure_event_gate_state"] == "accepted"]
    values = {"events": len(accepted), "minimum_fold_events": min(sum(r["fold"] == f for r in accepted) for f in FOLDS),
              "active_months": len({clock(r["decision_time"]).strftime("%Y-%m") for r in accepted}),
              "minimum_fold_months": min(len({clock(r["decision_time"]).strftime("%Y-%m") for r in accepted if r["fold"] == f}) for f in FOLDS)}
    gates = {key: values[field] >= threshold for key, field, threshold in
             (("minimum_events", "events", 80), ("minimum_per_fold", "minimum_fold_events", 12),
              ("minimum_active_months", "active_months", 12), ("minimum_months_per_fold", "minimum_fold_months", 3))}
    return counts, matched, values, gates


def verify_sources(root, started):
    commit = started["builder_commit"]
    stamp = subprocess.check_output(["git", "show", "-s", "--format=%cI", commit], cwd=root, text=True).strip()
    commit_time = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    require(commit_time.tzinfo is not None and commit_time.astimezone(timezone.utc) <= clock(started["at"], hour=False), "Source commit after run")
    for item in started["sources"]:
        value = subprocess.check_output(["git", "show", commit + ":" + item["path"]], cwd=root)
        require(hashlib.sha256(value).hexdigest() == item["sha256"], "Historical source SHA")
    return len(started["sources"])


def verify(root=ROOT, experiment=EXPERIMENT):
    """Read only saved evidence after the auditor's own committed-source guard."""
    root, experiment = Path(root), Path(experiment)
    script = Path(__file__).resolve()
    relative = script.relative_to(root).as_posix()
    auditor_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    committed = subprocess.check_output(["git", "show", auditor_commit + ":" + relative], cwd=root)
    require(hashlib.sha256(committed).hexdigest() == sha(script), "Commit the auditor before reading actual evidence")
    directory = experiment / "results"
    config = read_json(experiment / "config.json")
    require(config["support"] == {"minimum_events": 80, "minimum_per_fold": 12,
                                  "minimum_active_months": 12, "minimum_months_per_fold": 3}, "Frozen support thresholds")
    started, frozen, summary = [read_json(directory / n) for n in ("started.json", "support_frozen.json", "summary.json")]
    source_count = verify_sources(root, started)
    require(started["config_sha256"] == sha(experiment / "config.json"), "V25 config SHA")
    require(started["sources"] == frozen["sources"] and started["builder_commit"] == frozen["builder_commit"] == summary["builder_commit"], "V25 sources/commit")
    require(clock(started["at"], hour=False) <= clock(frozen["at"], hour=False) <= clock(summary["generated_at"], hour=False), "V25 chronology")
    require(summary["support_frozen_sha256"] == sha(directory / "support_frozen.json"), "Support freeze SHA")
    allowed = {str(V20 / n) for n in ("started.json", "context_frozen.json", "hourly_trace.csv.gz", "failure.json", "outcomes_started.json", "outcomes_resumed_1.json")}
    allowed |= {str(V24 / n) for n in ("started.json", "sampling_frozen.json", "case_requests.csv.gz", "control_requests.csv.gz", "random_assignments.csv.gz", "random_allocation.csv.gz")}
    allowed.add(str(V4))
    require(set(config["inputs"]) == allowed, "Unexpected input file; refuse any outcomes")
    require(started["inputs"] == config["inputs"] == frozen["input_receipt"]["inputs"], "Frozen input identity")
    for path, expected in config["inputs"].items():
        require(sha(root / path) == expected, "Input SHA: " + path)
    require(config["inputs"][str(V20 / "hourly_trace.csv.gz")] == TRACE_SHA, "Original trace pin")
    old, old_freeze, first, failure, resumed = [read_json(root / V20 / n) for n in
        ("started.json", "context_frozen.json", "outcomes_started.json", "failure.json", "outcomes_resumed_1.json")]
    chronology = [clock(m["at"], hour=False) for m in (old, old_freeze, first, failure, resumed)]
    require(all(a < b for a, b in zip(chronology, chronology[1:])), "Historical feature/failure/recovery chronology")
    require(old_freeze["outcomes_read"] is False and old_freeze["output_hashes"]["hourly_trace.csv.gz"] == TRACE_SHA, "Historical feature freeze")
    require(all(m["context_frozen_sha256"] == sha(root / V20 / "context_frozen.json") for m in (first, resumed)), "Same frozen recovery")
    parent_source_counts = [verify_sources(root, old)]
    sampling_start, sampling = [read_json(root / V24 / n) for n in ("started.json", "sampling_frozen.json")]
    parent_source_counts.append(verify_sources(root, sampling_start))
    require(clock(sampling_start["at"], hour=False) < clock(sampling["at"], hour=False), "Sampling chronology")
    require(sampling_start["sources"] == sampling["sources"] and sampling_start["inputs"] == sampling["input_receipt"]["inputs"], "Sampling sources")
    require(sampling["before_any_label"] is True and sampling["before_any_raw_read"] is True and sampling["sampling"]["outcomes_used"] is False, "Sampling precedes outcomes")
    require(set(frozen["output_hashes"]) == OUTPUTS and frozen["output_hashes"] == summary["output_hashes"], "Output manifest")
    for name, expected in frozen["output_hashes"].items():
        require(sha(directory / name) == expected, "Output SHA: " + name)
    require(frozen["outcomes_read_or_computed"] is False and frozen["raw5_read"] is False and frozen["holdout_consumed"] is False, "Support scope")
    trace_path = root / V20 / "hourly_trace.csv.gz"
    times = [clock(r["open_time"]) for r in read_csv(trace_path, ["open_time"])]
    require(len(times) == 18222 and len(set(times)) == len(times) and times == sorted(times), "Trace timestamp preflight")
    require(times[0] == clock("2022-11-30T16:00:00Z") and times[-1] == clock("2024-12-28T22:00:00Z") and times[-1] < clock("2025-01-01T00:00:00Z"), "Trace phase")
    raw = read_csv(trace_path)
    states = reconstruct(raw)
    for i, (saved, rebuilt) in enumerate(zip(raw, states)):
        for field, expected in rebuilt.items():
            equal(saved[field], expected, "hour %d %s" % (i, field))
    requests = {p: read_csv(root / V24 / (p + "_requests.csv.gz")) for p in ("case", "control")}
    require(len(requests["case"]) == 251 and len(requests["control"]) == 744, "Original denominator")
    case_index, control_index = indexed(requests["case"]), indexed(requests["control"])
    original = indexed(read_csv(root / V4, ["event_id", "signal_time", "decision_time", "direction", "fold"]))
    require(set(original) == set(case_index) and not set(case_index).intersection(control_index), "Original mother/global IDs")
    for identity, row in original.items():
        for field, value in row.items():
            equal(case_index[identity][field], request_value(field, value), "Original mother identity")
    allocation = read_csv(root / V24 / "random_allocation.csv.gz")
    assignments = indexed(read_csv(root / V24 / "random_assignments.csv.gz"))
    require(len(allocation) == 744 and set(assignments) == set(case_index), "Allocation denominator")
    linked = indexed(allocation, "control_event_id")
    require(set(linked) == set(control_index), "Control allocation identities")
    used = set()
    for identity, c in control_index.items():
        a = linked[identity]
        t = clock(c["decision_time"])
        require(t not in used and t not in {clock(m["decision_time"]) for m in case_index.values()}, "Control time reuse")
        used.add(t)
        require(c["mother_id"] == a["event_id"] and integer(c["control_slot"]) == integer(a["control_slot"]) and t == clock(a["candidate_time"]) == clock(a["candidate_id"]), "Control slot/time provenance")
        mother = case_index[c["mother_id"]]
        require(all(c[k] == mother[k] for k in ("direction", "fold", "mother_month")) and c["mother_month"] == t.strftime("%Y-%m"), "Control own month/direction/fold")
    for identity, mother in case_index.items():
        group = [c for c in control_index.values() if c["mother_id"] == identity]
        matched = flag(assignments[identity]["matched_support"])
        require(mother["mother_id"] == identity and missing(mother["control_slot"]) and flag(mother["matched_support"]) == matched, "Case linkage")
        require(len(group) == integer(assignments[identity]["assigned_controls"]) == (3 if matched else 0), "Frozen zero-or-three")
        require(not group or {integer(c["control_slot"]) for c in group} == {0, 1, 2}, "Three distinct slots")
    own = {p: contexts(requests[p], states) for p in requests}
    for population in own:
        saved = indexed(read_csv(directory / (population + "_context.csv.gz")))
        require(set(saved) == {r["event_id"] for r in own[population]}, "Context IDs")
        for r in own[population]:
            for field, expected in r.items():
                if field in requests[population][0]:
                    expected = request_value(field, expected)
                equal(saved[r["event_id"]][field], expected, population + " " + r["event_id"] + " " + field)
    counts, matched, values, gates = rebuild_support(own["case"], own["control"])
    saved_counts = read_csv(directory / "counts.csv.gz")
    require(len(saved_counts) == len(counts) == 62, "All count rows")
    for actual, expected in zip(saved_counts, counts):
        for key, value in expected.items():
            equal(actual[key], value, "counts " + key)
    saved_matched = indexed(read_csv(directory / "matched_support.csv.gz"))
    require(len(saved_matched) == len(matched) == 251, "All mother triples")
    for expected in matched:
        for key, value in expected.items():
            equal(saved_matched[expected["event_id"]][key], value, "matched " + key)
    population = {p: tally(own[p]) for p in own}
    require(summary["population"] == population and summary["support_values"] == values and summary["support_gates"] == gates, "Summary counts/gates")
    require(summary["support_pass"] == all(gates.values()), "Summary support decision")
    require(summary["status"] == ("support_pass_requires_separate_outcome_preregistration" if all(gates.values()) else "insufficient_support_no_outcomes"), "Summary support status")
    require(summary["trace_validation"] == {"rows": 18222, "frozen_reference_recomputed": True,
                                            "raw5_aggregation_verified": False, "pine_builtin_parity": False}, "Trace scope")
    complete = sum(r["complete_known"] for r in matched)
    accepted_complete = sum(r["accepted_case_complete_known"] for r in matched)
    assigned = sum(r["matched_support"] for r in matched)
    accepted_assigned = sum(r["matched_support"] and r["case_state"] == "accepted" for r in matched)
    coverage = {"case_known": (population["case"]["known"], 251), "control_known": (population["control"]["known"], 744),
                "complete_known_triples_all_cases": (complete, 251), "complete_known_triples_matched_cases": (complete, assigned),
                "complete_known_triples_accepted_cases": (accepted_complete, values["events"]),
                "accepted_cases_with_matched_support": (accepted_assigned, values["events"])}
    require(set(summary["coverage"]) == set(coverage), "Coverage keys")
    for name, (n, d) in coverage.items():
        require(summary["coverage"][name] == {"numerator": n, "denominator": d, "rate": n / d if d else None}, "Coverage " + name)
    accepted_ids = {r["event_id"] for r in own["case"] if r["structure_event_gate_state"] == "accepted"}
    require(summary["accepted_case_control_states"] == tally([r for r in own["control"] if r["mother_id"] in accepted_ids]), "Accepted mothers' own controls")
    require(summary["outcomes_read_or_computed"] is False and summary["economic_acceptance"] is False, "Not economics")
    return {"status": "passed", "auditor_commit": auditor_commit, "auditor_sha256": sha(script),
            "builder_commit": started["builder_commit"], "strategy_sources_verified": source_count,
            "parent_sources_verified": parent_source_counts,
            "input_hashes_verified": len(config["inputs"]), "output_hashes_verified": len(OUTPUTS),
            "trace_rows_recomputed": len(states), "contexts_recomputed": 995, "count_rows": 62, "mother_rows": 251,
            "population": population, "support_values": values, "support_gates": gates, "coverage": summary["coverage"],
            "raw5_aggregation_recomputed": False, "pine_builtin_parity_verified": False,
            "outcomes_read_or_computed": False, "limitation": "Independent saved-hour state and support accounting only; not raw5 aggregation, vendor tie parity, randomization replay or economic validation."}


def self_test():
    """Small synthetic checks only; never calls verify or reads an input file."""
    t = datetime(2023, 1, 1, tzinfo=timezone.utc)
    rows = [dict(open_time=(t + i * HOUR).isoformat(), open="100", high="101", low="99", close="100") for i in range(21)]
    rows.append(dict(open_time=(t + 21 * HOUR).isoformat(), open="100", high="103", low="99", close="102"))
    states = reconstruct(rows)
    require(states[19]["structure_reason"] == "warmup" and states[20]["structure_reason"] == "no_confirmed_break", "Synthetic warmup")
    require(states[21]["structure_break_direction"] == 1 and states[21]["structure_high_origin"] == t + 10 * HOUR, "Synthetic up/confirmation")
    mirror = [dict(r) for r in rows]
    mirror[-1].update(high="101", low="97", close="98")
    require(reconstruct(mirror)[-1]["structure_break_direction"] == -1, "Synthetic mirror")
    equality = [dict(r) for r in rows]
    equality[-1].update(close="101")
    require(not reconstruct(equality)[-1]["structure_break_on_k1"], "Synthetic strict boundary")
    gap = rows + [dict(open_time=(t + 23 * HOUR).isoformat(), open="100", high="101", low="99", close="100")]
    require(reconstruct(gap)[-1]["structure_count"] == 1 and not reconstruct(gap)[-1]["structure_known"], "Synthetic gap")
    request = dict(event_id="m", signal_time=(t + 21 * HOUR).isoformat(), decision_time=(t + 22 * HOUR).isoformat(),
                   direction="1", fold="2023H1", mother_id="m", control_slot="", matched_support="False", mother_month="2023-01", request_kind="case")
    cases = contexts([request], states)
    require(cases[0]["structure_event_gate_state"] == "accepted", "Synthetic own direction")
    counts, triples, values, _ = rebuild_support(cases, [])
    require(len(counts) == 62 and len(triples) == 1 and not triples[0]["complete_known"] and values["events"] == 1, "Synthetic full denominator")
    request["direction"] = "-1"
    require(contexts([request], states)[0]["structure_event_gate_state"] == "abstain", "Synthetic own opposite")
    for value in ("2023-01-01T00:00:00.000000001Z", "2023-01-01T00:00:00"):
        try:
            clock(value)
        except AssertionError:
            pass
        else:
            raise AssertionError("Synthetic invalid clock accepted")
    equal("1.0000000000000002", request_value("ma", "1.0"), "Synthetic CSV roundtrip")
    return {"status": "synthetic_passed", "actual_inputs_read": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(), ensure_ascii=False, indent=2, allow_nan=False))
