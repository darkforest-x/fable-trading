"""Independent V22 saved-hour CHANGE/support verification (stdlib only).

The SHA-pinned V21 verifier supplies readers, nanosecond clocks and an
independent OHLC->HL2->rank50 reconstruction. V22 selects two exact adjacent
completed hours and recomputes the integer four-asset difference here. No
feature, strategy, accounting or research module is imported. This is NOT a
raw5 aggregation, archive authenticity, Pine/live or economic replay audit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_SHA = "da124925b84dc732850bab5e8fcdae2bc085633e47fdd3f2c4034281993aeae5"


def load_helpers(path):
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != DEPENDENCY_SHA:
        raise ValueError("Missing or changed frozen V21 verifier dependency")
    spec = importlib.util.spec_from_file_location("v22_saved_v21_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v21 = load_helpers(Path(__file__).with_name("verify_hourly_impulse_breadth_v21.py"))
VerificationError = v21.VerificationError
require, number, equal_number, boolean = v21.require, v21.number, v21.equal_number, v21.boolean
stamp, day, month, HOUR = v21.stamp, v21.day, v21.month, v21.HOUR
read_csv, read_json, sha, safe_path = v21.read_csv, v21.read_json, v21.sha, v21.safe_path
indexed, parity, state_counts = v21.indexed, v21.parity, v21.state_counts
SYMBOLS, FOLDS, SUPPORT, STATES = v21.SYMBOLS, v21.FOLDS, v21.SUPPORT, v21.STATES
PARENT, INPUTS, BASE, BASE_SHA, PINE, PINE_SHA = v21.PARENT, v21.INPUTS, v21.BASE, v21.BASE_SHA, v21.PINE, v21.PINE_SHA
EXPERIMENT_ID = "exp-btcusdtp-1h-external-change-preholdout-20260907-v22"
EXPERIMENT_PATH = "experiments/active/" + EXPERIMENT_ID
TRACE_PARENT = v21.EXPERIMENT_PATH + "/results"
TRACE_SHA = "870e898c0db830ad7c724bb93726f89b6842e6eb7462b3eac1c56bba03e853e6"
FREEZE_SHA = "bae01b79e34a0782598e18a9197db1853492fe6f04cb92d0b992fb4015700403"
CSV_NAMES = {name + ".csv.gz" for name in ("entry_context", "counts", "matched_support")}
SOURCE_FILES = v21.SOURCE_FILES | {
    "yoyo/data/hourly_impulse_breadth_change.py", "yoyo/evaluation/hourly_impulse_breadth_change_research.py",
    "tests/test_hourly_impulse_breadth_change.py", "tests/test_hourly_impulse_breadth_change_research.py",
}
GATE_CHANGES = dict(history="two_adjacent_rank50_windows_union52_contiguous_hours",
    weights="integer_sum_now_minus_integer_sum_previous_then_divide200",
    accept="own_direction_times_raw_rank_sum_change_strictly_positive",
    bookkeeping_score="raw_rank_sum_change_divided400_in_minus1_plus1",
    join="exact_hours_open_Tminus1h_and_Tminus2h_available_T_and_Tminus1h",
    absolute_mean_alignment=False, change_hours=1)
OUTCOME_INPUTS = {
    "case_episodes.csv.gz": "f1d6d8c29af2c78f4fe0a3c79560b1ac9e21062202d3ac1b462b640463ad8e02",
    "control_episodes.csv.gz": "cdc677b08fab6185d2be363e871fe2f7cce0f5d72cdec52196e7c61ff52282e0",
    "matched.csv": "3604757c56daee054c3caed6fe9dbf28018c72a73c0faf151de3c97c76b60a8b",
    "single_pending.csv.gz": "c72d429fbd2193fa107d4335be835c37909596aedbc0184c50acababe23cd1ab",
}


def context_facts(context, trace):
    """Rebuild both ranks from saved hourly OHLC, not the saved rank claims."""
    lookup = indexed(context)
    require(context, "All original requests missing")
    require(not any(k.startswith("structure_") or v21.OUTCOME.search(k) for r in context for k in r),
            "Stacked gate/outcome columns forbidden")
    cutoff = max(stamp(r["signal_time"]) for r in context)
    assets = v21.rebuild_trace(trace, cutoff)
    # Segment values have already been reconstructed and compared by rebuild_trace.
    for row in trace:
        for field in ("segment_id", "count", "trscore"):
            value = number(row[field], field == "trscore")
            require(value is None or value.is_integer(), "Trace discrete metadata must be exact integers")
        assets[row["symbol"]][stamp(row["open_time"])]["segment_id"] = int(number(row["segment_id"]))
    for row in context:
        signal, decision, direction = stamp(row["signal_time"]), stamp(row["decision_time"]), number(row["direction"])
        require(signal % HOUR == 0 and decision == signal+HOUR and direction in (-1, 1), "Invalid own K1 clock/direction")
        require(row["population"] in ("case", "control") and row["fold"] in FOLDS, "Invalid population/fold")
        start, end = FOLDS[row["fold"]]
        require(day(start) <= decision < day(end)-72*HOUR, "Fold/72h embargo drift")
        current, previous, missing, gap = [], [], False, False
        for symbol in SYMBOLS:
            pair = []
            for prefix, opened in (("", signal-HOUR), ("previous_", signal-2*HOUR)):
                source = assets[symbol].get(opened)
                pair.append(source)
                missing |= source is None
                values = dict(source, bar_open=opened) if source else dict(
                    score=None, count=0, available_at=None, window_start=None, segment_id=None, bar_open=None)
                for field, expected in values.items():
                    value = row["breadth_"+symbol+"_"+prefix+field]
                    if field in ("available_at", "window_start", "bar_open"):
                        require(stamp(value, True) == expected, "Exact two-hour clock/window drift")
                    else:
                        actual = number(value, True)
                        require(actual == expected, "Own integer rank/count/segment drift")
            now, before = pair
            if now is None or before is None:
                continue
            if now["segment_id"] != before["segment_id"]:
                gap = True
                continue
            if now["score"] is None or before["score"] is None:
                continue
            require(now["count"] == before["count"]+1 and now["count"] >= 52 and
                    before["window_start"] == signal-52*HOUR and now["window_start"] == signal-51*HOUR,
                    "Two rank windows must have a52-hour continuous union")
            current.append(int(now["score"]))
            previous.append(int(before["score"]))
        known = len(current) == 4
        raw = sum(current)-sum(previous) if known else None
        values = dict(breadth_raw_sum_change=raw, breadth_mean_now=sum(current)/200 if known else None,
            breadth_mean_previous=sum(previous)/200 if known else None,
            breadth_change=raw/200 if known else None, breadth_score=raw/400 if known else None)
        for field, expected in values.items():
            if field == "breadth_raw_sum_change":
                require(number(row[field], True) == expected, "Integer raw sum change drift")
            else:
                equal_number(row[field], expected, "Change/mean/half scaling drift: "+field)
                require(expected != 0 or number(row[field], True) == 0, "Exact aggregate zero must stay zero")
        # A tolerance must never erase the exact integer-zero admission boundary.
        actual_score = number(row["breadth_score"], True)
        require(not known or (actual_score > 0)-(actual_score < 0) == (raw > 0)-(raw < 0), "Half-score sign/zero drift")
        state = "unknown" if not known else "accepted" if direction*raw > 0 else "abstain"
        reason = (("neutral" if raw == 0 else "known") if known else
                  "missing_external_hour" if missing else "source_gap" if gap else "insufficient_history")
        require(boolean(row["breadth_known"]) == known and row["breadth_gate_state"] == state and
                row["breadth_reason"] == reason, "Change gate/neutral/unknown drift")
        require(number(row["breadth_source_count"]) == len(current), "Unknown asset denominator changed")
        require(stamp(row["breadth_cutoff"]) == signal and
                stamp(row["breadth_available_at"], True) == (signal if known else None), "Change availability drift")
    return lookup


def verify_tables(context, trace, counts, matched, summary, *, expected_counts=(251, 462, 154)):
    """Pure saved-table API; only explicit synthetic tests override counts."""
    require(isinstance(summary, dict), "Complete summary required")
    lookup = context_facts(context, trace)
    n, m, k = expected_counts
    population = {p: [r for r in context if r["population"] == p] for p in ("case", "control")}
    require((len(population["case"]), len(population["control"])) == (n, m), "Original denominator changed")
    groups, times = defaultdict(list), set()
    for row in population["control"]:
        parent = lookup.get(row["parent_event_id"])
        require(parent and parent["population"] == "case" and parent["fold"] == row["fold"] and
                number(parent["direction"]) == number(row["direction"]), "Control parent/fold/direction changed")
        time = stamp(row["decision_time"])
        require(time not in times, "Control time reused")
        times.add(time)
        groups[row["parent_event_id"]].append(row)
    require(len(groups) == k and all(len(part) == 3 for part in groups.values()), "Three-or-zero mapping changed")
    pairs = indexed(matched)
    require(pairs.keys() == groups.keys(), "Matched groups changed")
    for parent, part in groups.items():
        pair, case = pairs[parent], lookup[parent]
        require(pair["fold"] == case["fold"] and pair["case_state"] == case["breadth_gate_state"] and
                pair["control_ids"] == "|".join(sorted(r["event_id"] for r in part)), "Matched identity/state changed")
        for field, value in state_counts(part).items():
            equal_number(pair["control_"+field], value, "Matched counts changed")
        require(boolean(pair["all_known"]) == (case["breadth_gate_state"] != "unknown" and
                all(r["breadth_gate_state"] != "unknown" for r in part)), "Matched unknown coerced")
    dimensions = {"all": ["all"], "fold": list(FOLDS), "direction": ["1", "-1"],
                  "month": [f"{year}-{mo:02d}" for year in (2023, 2024) for mo in range(1, 13)]}
    actual = {(r["population"], r["dimension"], r["key"]): r for r in counts}
    expected = {}
    for pop, rows in population.items():
        for dimension, keys in dimensions.items():
            for key in keys:
                part = [r for r in rows if dimension == "all" or
                        (str(int(number(r["direction"]))) if dimension == "direction" else
                         month(r["decision_time"]) if dimension == "month" else r["fold"]) == key]
                values = state_counts(part)
                values["accepted_rate"] = values["accepted"]/len(part) if part else None
                expected[(pop, dimension, key)] = values
    require(len(actual) == len(counts) and actual.keys() == expected.keys(), "Missing/duplicate support dimensions")
    for key, values in expected.items():
        for field, value in values.items():
            equal_number(actual[key][field], value, "Support count/rate changed")
    totals = {p: state_counts(rows) for p, rows in population.items()}
    require(summary["population"] == totals, "Summary population changed")
    admitted = [r for r in population["case"] if r["breadth_gate_state"] == "accepted"]
    folds = {f: [r for r in admitted if r["fold"] == f] for f in FOLDS}
    values = dict(events=len(admitted), minimum_fold_events=min(map(len, folds.values())),
        active_months=len({month(r["decision_time"]) for r in admitted}),
        minimum_fold_months=min(len({month(r["decision_time"]) for r in part}) for part in folds.values()))
    gates = dict(zip(SUPPORT, (values["events"] >= 80, values["minimum_fold_events"] >= 12,
                             values["active_months"] >= 12, values["minimum_fold_months"] >= 3)))
    read = all(gates.values())
    require(summary["support_values"] == values and summary["support_gates"] == gates and
            summary["support_pass"] is read, "Support decision changed")
    require(summary["matching"] == dict(assigned=k, unassigned=n-k, coverage=k/n, required=.9, pass_gate=k/n >= .9),
            "Matching ceiling changed")
    require(summary["outcomes_read"] is read and summary["status"] ==
            ("fixed_episode_change_gate_not_independent_validation" if read else "insufficient_support_no_outcomes"),
            "Outcome access contradicts support")
    for flag in ("holdout_consumed", "independent_validation", "training_eligible", "production_eligible", "overall_goal_achieved"):
        require(summary[flag] is False, "Unjustified promotion: "+flag)
    equal_number(summary["new_intrabar_replays"], 0, "Unexpected replay")
    return dict(status="passed", population=totals, support_values=values, support_gates=gates,
        support_pass=read, matched_groups=k, unmatched=n-k, count_rows=len(counts), saved_hourly_rows=len(trace),
        saved_hourly_scores_recomputed=True, adjacent_change_recomputed=True,
        raw5_aggregation_recomputed=False, archive_price_bytes_read=False, economic_accounting_verified=False,
        independent_source_authenticity_verified=False, live_availability_verified=False, pine_runtime_verified=False,
        limitation="Saved hourly rank/change, support and byte lineage only; no raw5 aggregation, source authenticity, Pine/live or economic replay verification.")


def verify_config(config):
    require(config["experiment_id"] == EXPERIMENT_ID and config["trace_parent"] == TRACE_PARENT and
            config["trace_sha256"] == TRACE_SHA and config["trace_freeze_sha256"] == FREEZE_SHA and
            config["trace_source"] == "saved_complete_hour_OHLC_rank_not_new_raw5_aggregation", "V22 trace identity drift")
    require(all(config["gate"].get(k) == value for k, value in GATE_CHANGES.items()), "Frozen change formula drift")
    require(type(config["gate"]["change_hours"]) is int and config["gate"]["absolute_mean_alignment"] is False,
            "Explicit integer lag and boolean no-level gate required")
    # Validate the unchanged V21 contract using an explicit metadata projection;
    # no V22 data or result is translated into an absolute-level observation.
    old = deepcopy(config)
    old["experiment_id"] = v21.EXPERIMENT_ID
    for key in ("bookkeeping_score", "absolute_mean_alignment", "change_hours"):
        del old["gate"][key]
    old["gate"].update(history="51_consecutive_complete_hours_per_asset", weights="equal_mean_scores_divided_by50",
        accept="own_direction_times_breadth_score_strictly_positive", join="exact_last_hour_available_at_equals_own_signal_time")
    v21.verify_config(old)
    require(config["outcome_inputs"] == OUTCOME_INPUTS and config["outcomes"] ==
            "experiments/active/exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18/results/candidate", "Frozen outcome identities drift")
    require(config["inference"] == dict(unit="paired_original_intention", clusters="calendar_month", draws=9999,
            seed=20260906, alpha=.01, joint_required=["case_delta", "excess_delta"],
            reused_development_not_confirmatory=True), "Inference contract drift")


def verify_sources(root, started, summary, config):
    require(started["sources"] == summary["sources"] and started["sources"], "Source receipts mismatch")
    commit = started["builder_commit"]
    require(re.fullmatch(r"[a-f0-9]{40}", commit), "Invalid builder commit")
    pins = {r["path"]: r["sha256"] for r in started["sources"]}
    required = SOURCE_FILES | {EXPERIMENT_PATH+"/config.json", EXPERIMENT_PATH+"/PROJECT_PLAN.md", BASE}
    require(len(pins) == len(started["sources"]) and required <= pins.keys(), "Missing/duplicate source pin")
    for identity, expected in pins.items():
        safe_path(root, identity)
        require(not identity.startswith("data/") and re.fullmatch(r"[a-f0-9]{64}", expected), "Unsafe source")
        content = subprocess.run(["git", "show", commit+":"+identity], cwd=root, check=True, capture_output=True).stdout
        require(hashlib.sha256(content).hexdigest() == expected, "Committed source mismatch")
    seconds = subprocess.run(["git", "show", "-s", "--format=%ct", commit], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    require(seconds.isdigit() and int(seconds)*10**9 <= stamp(started["at"]), "Run predates builder")
    require(pins[EXPERIMENT_PATH+"/config.json"] == summary["config_sha256"] and
            pins[BASE] == config["base_config_sha256"] and pins[PINE] == PINE_SHA, "Source/config pin mismatch")
    return len(pins)


def verify_input(root, frozen, summary):
    """Read only the two pinned V21 saved-support inputs, never its outcomes."""
    require(frozen["input_receipt"] == summary["input_receipt"], "Frozen input receipt changed")
    receipt = summary["input_receipt"]
    folder = safe_path(root, TRACE_PARENT)
    require(sha(folder/"context_frozen.json") == FREEZE_SHA and sha(folder/"external_hourly_trace.csv.gz") == TRACE_SHA,
            "Pinned V21 support input bytes changed")
    prior = read_json(folder/"context_frozen.json")
    require(prior["outcomes_read"] is False and prior["requests"] == 713 and
            prior["output_hashes"]["external_hourly_trace.csv.gz"] == TRACE_SHA, "V21 pre-outcome lineage changed")
    require(receipt["path"] == TRACE_PARENT+"/external_hourly_trace.csv.gz" and receipt["sha256"] == TRACE_SHA and
            receipt["parent_freeze_sha256"] == FREEZE_SHA and receipt["saved_hour_rows"] == 70168 and
            receipt["raw5_prices_read"] is False and receipt["prices_2025_plus_materialized"] == 0 and
            receipt["new_intrabar_replays"] == 0, "V22 saved-input receipt drift")
    require(stamp(prior["at"]) <= stamp(frozen["at"]), "Parent freeze follows child freeze")
    trace = read_csv(folder/"external_hourly_trace.csv.gz")
    times = [stamp(r["open_time"]) for r in trace]
    require(len(trace) == 70168 and stamp(receipt["first_hour"]) == min(times) and
            stamp(receipt["last_hour"]) == max(times) and max(times) < day("2025-01-01"), "Saved input size/clock drift")
    return trace


def verify_output_receipts(results, started, frozen, summary):
    hashes = summary["output_hashes"]
    require(set(hashes) == {p.name for p in results.glob("*.csv.gz")} and CSV_NAMES <= hashes.keys() and
            set(frozen["output_hashes"]) == CSV_NAMES, "Incomplete output hash coverage")
    for name, expected in hashes.items():
        require(sha(safe_path(results, name)) == expected, "Saved output byte mismatch: "+name)
    require(all(hashes[n] == value for n, value in frozen["output_hashes"].items()) and
            frozen["requests"] == 713 and frozen["outcomes_read"] is False, "713 contexts not frozen before outcomes")
    require(stamp(started["at"]) <= stamp(frozen["at"]) <= stamp(summary["generated_at"]), "Freeze ordering changed")
    return hashes


def verify_outcome_access(results, frozen, summary, support_pass, hashes):
    marker = results/"outcomes_started.json"
    if support_pass:
        access = read_json(marker)
        require(stamp(frozen["at"]) <= stamp(access["at"]) <= stamp(summary["generated_at"]) and
                access["frozen_context_sha256"] == sha(results/"context_frozen.json") and
                access["new_intrabar_replays"] == 0, "Outcomes precede immutable support freeze")
    else:
        require(not marker.exists() and set(hashes) == CSV_NAMES and "economics" not in summary,
                "Insufficient support nevertheless accessed outcomes")


def verify(results=None, *, root=ROOT):
    root = Path(root).resolve()
    results = Path(results) if results is not None else root/EXPERIMENT_PATH/"results"
    if not results.is_absolute():
        results = root/results
    require(results.resolve() == root/EXPERIMENT_PATH/"results", "Unexpected results identity")
    require(not (results/"failure.json").exists(), "Failed run is not evidence")
    experiment = results.parent
    config, summary, started, frozen = (read_json(p) for p in
        (experiment/"config.json", results/"summary.json", results/"started.json", results/"context_frozen.json"))
    verify_config(config)
    require(summary["experiment_id"] == EXPERIMENT_ID and sha(experiment/"config.json") == summary["config_sha256"], "Summary/config mismatch")
    require(sha(safe_path(root, BASE)) == BASE_SHA, "Base changed")
    source_count = verify_sources(root, started, summary, config)
    hashes = verify_output_receipts(results, started, frozen, summary)
    trace = verify_input(root, frozen, summary)
    for name, expected in INPUTS.items():
        require(sha(safe_path(root, PARENT+"/"+name)) == expected, "Original pre-entry input changed")
    context = read_csv(results/"entry_context.csv.gz")
    for population, filename in (("case", "original_mothers.csv.gz"), ("control", "control_mothers.csv.gz")):
        parity(read_csv(safe_path(root, PARENT+"/"+filename)), [r for r in context if r["population"] == population])
    assignments = indexed(read_csv(safe_path(root, PARENT+"/assignments.csv")))
    require(assignments.keys() == {r["event_id"] for r in context if r["population"] == "case"} and
            Counter(r["match_status"] for r in assignments.values()) ==
            {"matched": 154, "insufficient_exact_controls": 94, "missing_causal_matching_support": 3}, "154/97 assignment drift")
    matched = read_csv(results/"matched_support.csv.gz")
    require({r["event_id"] for r in matched} == {k for k, r in assignments.items() if r["match_status"] == "matched"}, "Matched set drift")
    output = verify_tables(context, trace, read_csv(results/"counts.csv.gz"), matched, summary)
    verify_outcome_access(results, frozen, summary, output["support_pass"], hashes)
    output.update(experiment_id=EXPERIMENT_ID, builder_commit=started["builder_commit"],
        committed_sources_verified=source_count, output_hashes_verified=len(hashes), fixed_request_hashes_verified=len(INPUTS),
        summary_sha256=sha(results/"summary.json"), context_frozen_sha256=sha(results/"context_frozen.json"),
        parent_trace_sha256=TRACE_SHA, parent_context_frozen_sha256=FREEZE_SHA,
        auditor_sha256=sha(Path(__file__)), helper_sha256=DEPENDENCY_SHA)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        target = args.out if args.out is None or args.out.is_absolute() else args.root/args.out
        require(target is None or not target.exists(), "Preserve existing verification receipt")
        result = verify(args.results, root=args.root)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x") as handle:
                handle.write(rendered+"\n")
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps(dict(status="failed", error=str(error)), ensure_ascii=False))
        return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
