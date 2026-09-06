"""Read-only V10 saved-support verification; stdout JSON, no market replay.

Uses only standard-library CSV/gzip readers, not the research runner, MILP,
pandas, or any raw archive. CSV parsing follows Python 3.9's explicit-string
and newline contracts:
https://docs.python.org/3.9/library/csv.html#csv.DictReader
https://docs.python.org/3.9/library/gzip.html#gzip.open

Allocation feasibility and connected-component upper bounds are reconstructed
independently. Equality of an allocation with that bound proves optimality of
the SAVED graph. Otherwise only the saved solver certificate's consistency is
verified; its global optimality is not independently reproved. Neither case
proves raw feature causality, completeness against raw prices, profitability,
or individual impossibility for mothers unselected in one optimal allocation.
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
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-1h-matching-support-preholdout-20260906-v10"
EXPERIMENT_PATH = "experiments/active/" + EXPERIMENT_ID
BASE_CONFIG = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
PARENT_PATH = "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
INPUT_NAMES = {"original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv", "assignment_receipt.json"}
OUTPUT_NAMES = {name + ".csv.gz" for name in (
    "greedy_controls", "greedy_assignments", "mother_audit", "eligible_edges", "greedy_edges",
    "candidate_stages", "key_supply", "stage_counts", "original_mothers", "matching_frame",
    "maximum_allocation", "fold_coverage")}
CHECKPOINT_NAMES = OUTPUT_NAMES - {"maximum_allocation.csv.gz", "fold_coverage.csv.gz"}
FOLDS = [["2023H1", "2023-01-01", "2023-07-01"], ["2023H2", "2023-07-01", "2024-01-01"],
         ["2024H1", "2024-01-01", "2024-07-01"], ["2024H2", "2024-07-01", "2025-01-01"]]
EXPECTED = {"mothers": 251, "controls": 462, "matched": 154,
            "status_counts": {"matched": 154, "insufficient_exact_controls": 94, "missing_causal_matching_support": 3}}
MATCHING = {"count": 3, "seed": 20260906, "embargo_hours": 72,
            "no_reuse": True, "no_fallback": True, "keys_unchanged": True}
SOURCE_PY = {"yoyo/data/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py", "yoyo/evaluation/hourly_impulse_k2_matching.py",
    "yoyo/evaluation/hourly_impulse_matching_support.py", "yoyo/evaluation/hourly_impulse_matching_capacity.py",
    "yoyo/evaluation/hourly_impulse_support_research.py", "tests/test_hourly_impulse_k2_matching.py",
    "tests/test_hourly_impulse_matching_support.py", "tests/test_hourly_impulse_matching_capacity.py",
    "tests/test_hourly_impulse_support_research.py"}


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def number(value):
    require(not isinstance(value, bool), "Boolean is not a numeric count")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VerificationError("Missing or invalid numeric value") from exc
    require(math.isfinite(result), "Non-finite numeric value")
    return result


def integer(value):
    result = number(value)
    require(result == int(result), "Non-integral count")
    return int(result)


def same_number(actual, expected, label):
    require(math.isclose(number(actual), number(expected), rel_tol=1e-12, abs_tol=1e-12), label)


def stamp(value, *, grid=False):
    require(isinstance(value, str) and value, "Missing timestamp")
    if grid:
        require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:00:00(?:\.0+)?(?:\+00:00|Z)", value)),
                "Decision timestamp is not an exact UTC hour")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError("Invalid timestamp") from exc
    if result.tzinfo is None:
        require(not grid and len(value) == 10, "Timezone missing")
        result = result.replace(tzinfo=timezone.utc)
    require(result.utcoffset() == timedelta(0), "Timestamp must be UTC")
    return result


def boolean(value):
    require(value in ("True", "False") and isinstance(value, str), "CSV boolean must be explicit True/False")
    return value == "True"


def sha(path):
    require(path.is_file() and not path.is_symlink(), "Missing file or symlink: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    require(path.is_file() and not path.is_symlink(), "Missing JSON or symlink: " + str(path))
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "Duplicate JSON key")
            value[key] = item
        return value
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(VerificationError("Non-finite JSON constant")))


def read_csv(path):
    require(path.is_file() and not path.is_symlink(), "Missing CSV or symlink: " + str(path))
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames
        require(columns is not None and len(columns) == len(set(columns)), "CSV header missing or duplicated")
        require(not any(re.search(r"(^|_)(pnl|return|returns|mfe|mae|outcome|closed)($|_)", c, re.I)
                        or c.startswith("max_favourable") for c in columns), "Outcome column is forbidden")
        rows = list(reader)
    require(all(None not in row and all(value is not None for value in row.values()) for row in rows), "Ragged CSV")
    return rows


def indexed(rows, key="event_id"):
    require(all(isinstance(row.get(key), str) and row[key].strip() for row in rows), "Missing ID: " + key)
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), "Duplicate ID: " + key)
    return result


def parity(left, right):
    a, b = indexed(left), indexed(right)
    require(a.keys() == b.keys(), "Historical population drift")
    for event_id, row in a.items():
        other = {k: v for k, v in b[event_id].items() if k != "audit_fold"}
        require(row.keys() == other.keys(), "Historical schema drift")
        for key, value in row.items():
            candidate = other[key]
            if value == candidate:
                continue
            if key.endswith(("_time", "_available")):
                require(stamp(value, grid=True) == stamp(candidate, grid=True), "Historical clock drift")
            else:
                try:
                    same_number(value, candidate, "Historical value drift: " + key)
                except VerificationError as exc:
                    raise VerificationError("Historical value drift: " + key) from exc


def component_bound(mothers, edges, count=3):
    """Independent union-find on mothers sharing candidate IDs, not solver code."""
    parents = {m: m for m in mothers}
    def find(m):
        while parents[m] != m:
            parents[m] = parents[parents[m]]
            m = parents[m]
        return m
    candidate_mothers = defaultdict(set)
    for mother, candidate in edges:
        candidate_mothers[candidate].add(mother)
    for owners in candidate_mothers.values():
        first, *rest = sorted(owners)
        for mother in rest:
            parents[find(mother)] = find(first)
    groups = defaultdict(set)
    for mother in mothers:
        groups[find(mother)].add(mother)
    components = []
    for group in sorted(groups.values(), key=lambda g: min(g)):
        candidates = {c for c, owners in candidate_mothers.items() if owners & group}
        components.append({"mother_ids": sorted(group), "mother_count": len(group),
            "candidate_count": len(candidates), "edge_count": sum(m in group for m, _ in edges),
            "complete_mother_upper_bound": min(len(group), len(candidates) // count)})
    return components


def audit_graph(mothers, edges, allocation, greedy_controls, assignments, capacity, count=3):
    """Pure saved-row graph audit, usable with synthetic arbitrary populations."""
    mother_map, assignment_map = indexed(mothers), indexed(assignments)
    require(mother_map.keys() == assignment_map.keys(), "Assignments do not preserve all mothers")
    edge_pairs, candidate_folds = set(), defaultdict(set)
    for row in edges:
        mother, candidate = row.get("event_id"), row.get("candidate_id")
        require(mother in mother_map and isinstance(candidate, str) and candidate, "Orphan or empty edge")
        instant = stamp(candidate, grid=True)
        require(candidate == instant.isoformat(), "Candidate ID is not canonical UTC time")
        require((mother, candidate) not in edge_pairs, "Duplicate edge")
        edge_pairs.add((mother, candidate))
        fold = mother_map[mother]["fold"]
        for key in ("fold", "audit_fold"):
            require(row.get(key) == fold, "Edge fold differs from mother")
        require(stamp(row["candidate_time"], grid=True) == instant, "Candidate time/ID mismatch")
        candidate_folds[candidate].add(fold)
    require(all(len(x) == 1 for x in candidate_folds.values()), "Candidate time spans folds")
    def feasible(rows, *, old=False):
        pairs, candidate_times, counts = set(), set(), Counter()
        for row in rows:
            mother = row["parent_event_id"] if old else row["event_id"]
            candidate = stamp(row["decision_time"], grid=True).isoformat() if old else row["candidate_id"]
            require((mother, candidate) in edge_pairs, "Allocation contains an illegal edge")
            require((mother, candidate) not in pairs and candidate not in candidate_times, "Candidate reused globally")
            require(row["fold"] == mother_map[mother]["fold"], "Allocation fold drift")
            pairs.add((mother, candidate)); candidate_times.add(candidate); counts[mother] += 1
        require(all(n == count for n in counts.values()), "Partial control group counted as matched")
        return pairs, counts
    selected, selected_counts = feasible(allocation)
    old_pairs, old_counts = feasible(greedy_controls, old=True)
    old_matched = {m for m, row in assignment_map.items() if row["match_status"] == "matched"}
    require(old_matched == set(old_counts), "Old matched statuses differ from old triplets")
    require(all(integer(row["assigned_controls"]) == (count if m in old_matched else 0)
                for m, row in assignment_map.items()), "Old assignment count mismatch")
    require(len(selected_counts) >= len(old_counts), "Maximum below existing feasible allocation")
    components = component_bound(mother_map, edge_pairs, count)
    bound = sum(c["complete_mother_upper_bound"] for c in components)
    require(capacity.get("optimal") is True and capacity.get("solution_verified") is True, "No saved optimal certificate")
    expected = {"mother_count": len(mothers), "candidate_count": len(candidate_folds), "edge_count": len(edges),
                "count_per_mother": count, "matched_mothers": len(selected_counts), "allocated_controls": len(selected),
                "unmatched_mothers": len(mothers)-len(selected_counts), "connected_component_upper_bound": bound,
                "connected_component_count": len(components), "complete_mother_upper_bound": len(selected_counts)}
    for key, value in expected.items():
        same_number(capacity.get(key), value, "Capacity count mismatch: " + key)
    component_keys = ("mother_ids", "mother_count", "candidate_count", "edge_count", "complete_mother_upper_bound")
    saved_components = [{k: c[k] for k in component_keys} for c in capacity.get("components", [])]
    require(saved_components == components, "Saved connected components differ from independent graph")
    require(capacity.get("mothers_without_edges") == sorted(set(mother_map)-{m for m, _ in edge_pairs}), "Isolated mother list drift")
    require(capacity.get("unmatched_mother_ids") == sorted(set(mother_map)-set(selected_counts)), "Unselected mother list drift")
    require(len(selected_counts) <= bound, "Allocation exceeds graph upper bound")
    if bound == 0:
        require(capacity.get("solver_called") is False and capacity.get("solver_status") is None,
                "Structural zero falsely claims a solver certificate")
    else:
        require(capacity.get("solver_called") is True and type(capacity.get("solver_status")) is int
                and capacity["solver_status"] == 0, "Non-optimal solver status")
        for key, value in (("solver_objective", -len(selected_counts)), ("solver_dual_bound", -len(selected_counts)), ("solver_mip_gap", 0)):
            require(abs(number(capacity.get(key))-value) <= 1e-6, "Inconsistent solver objective/bound/gap")
    return {"mothers": len(mothers), "maximum_matched": len(selected_counts), "greedy_matched": len(old_counts),
            "matching_edges": len(edges), "independent_component_upper_bound": bound,
            "independent_optimum_proven_for_saved_graph": bound == len(selected_counts),
            "components": components, "selected_counts": selected_counts, "old_counts": old_counts,
            "edge_pairs": edge_pairs, "old_pairs": old_pairs}


def git_blob(root, commit, path):
    return subprocess.run(["git", "show", commit + ":" + path], cwd=root,
                          check=True, capture_output=True).stdout


def verify_results(results=None, *, repository_root=ROOT):
    """Read fixed saved-artifact allowlists only; never call research or solver."""
    root = Path(repository_root).resolve()
    results = Path(results) if results is not None else root/EXPERIMENT_PATH/"results"
    require(not (results/"failure.json").exists(), "failure.json exists: not capacity evidence")
    summary, started, frozen = (read_json(results/name) for name in ("summary.json", "started.json", "support_frozen.json"))
    config_path = root/EXPERIMENT_PATH/"config.json"
    config, base = read_json(config_path), read_json(root/BASE_CONFIG)
    require(config["experiment_id"] == EXPERIMENT_ID and summary["experiment_id"] == EXPERIMENT_ID, "Wrong experiment")
    require(config["base_config"] == BASE_CONFIG and config["parent_results"] == PARENT_PATH, "Input scope changed")
    require(json.dumps(config["matching"], sort_keys=True) == json.dumps(MATCHING, sort_keys=True), "Matching contract changed")
    require(config["expected"] == EXPECTED and base["development_folds"] == FOLDS, "Population/fold contract changed")
    require(config["capacity"]["required_complete_mothers"] == 226 and config["capacity"]["minimum_coverage"] == .9
            and config["capacity"]["optimal_required"] is True and config["capacity"]["outcomes_used"] is False,
            "Capacity contract changed")
    for key in ("holdout_consumed", "training_eligible", "production_eligible"):
        require(config[key] is False and summary[key] is False, "Forbidden eligibility/holdout claim")
    require(config["no_audit_entry_point"] is True and config["no_outcome_entry_point"] is True,
            "Outcome-free scope changed")
    require(summary["outcomes_read_or_computed"] is False and summary["profitability_test"] is False,
            "Support audit cannot be a profitability test")
    require(sha(config_path) == summary["config_sha256"] and sha(root/BASE_CONFIG) == config["base_config_sha256"], "Config hash drift")
    require(set(summary["output_hashes"]) == OUTPUT_NAMES, "Final output manifest incomplete or unexpected")
    require(set(p.name for p in results.glob("*.csv.gz")) == OUTPUT_NAMES, "Output directory/manifest mismatch")
    for name, digest in summary["output_hashes"].items():
        require(sha(results/name) == digest, "Output hash drift: " + name)
    require(set(frozen["output_hashes"]) == CHECKPOINT_NAMES, "Checkpoint does not contain exact pre-solver tables")
    require(all(summary["output_hashes"][k] == v for k, v in frozen["output_hashes"].items()), "Checkpoint bytes changed")
    require(frozen["capacity_attempted"] is False, "Checkpoint is not pre-capacity")
    require(started["sources"] == summary["source_receipts"] == frozen["source_receipts"], "Source receipts disagree")
    source_records = indexed(started["sources"], "path")
    expected_sources = SOURCE_PY | {BASE_CONFIG, EXPERIMENT_PATH+"/config.json", EXPERIMENT_PATH+"/PROJECT_PLAN.md"}
    require(set(source_records) == expected_sources, "Source receipt scope changed")
    commit = started["builder_commit"]
    require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit), "Invalid builder commit")
    for path, receipt in source_records.items():
        require(sha(root/path) == receipt["sha256"], "Current source hash mismatch: " + path)
        require(hashlib.sha256(git_blob(root, commit, path)).hexdigest() == receipt["sha256"], "Source not present in builder commit")
    require(stamp(started["at"]) <= stamp(frozen["generated_at"]) <= stamp(summary["generated_at"]), "Receipt clock order invalid")
    require(config["inputs"] == started["inputs"] and set(config["inputs"]) == INPUT_NAMES, "Input manifest changed")
    parent = root/PARENT_PATH
    for name, digest in config["inputs"].items():
        require(sha(parent/name) == digest, "Pre-entry input hash mismatch: " + name)
    source = summary["source_receipt"]
    require(source == frozen["source_receipt"], "Price-prefix receipt changed after checkpoint")
    require(source["sha256"] == base["source"]["sha256"] and source["holdout_price_rows"] == 0
            and source["timestamp_preflight_before_price_hash"] is True, "Invalid source-prefix receipt")
    require(0 < integer(source["phase_rows"]) <= integer(source["physical_rows"]), "Invalid source receipt row counts")
    require(stamp(source["phase_price_last_open"]) < stamp("2025-01-01")
            and stamp(source["phase_price_last_open"]) <= stamp(source["physical_last_open"])
            < stamp(base["source"]["end_exclusive"]), "Source-prefix receipt exceeds boundary")
    old_mothers, old_controls, old_assignments = (read_csv(parent/name) for name in
        ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv"))
    receipts = read_json(parent/"assignment_receipt.json")
    require(frozen["greedy_receipts"] == receipts, "Original assignment receipts changed")
    tables = {name[:-7]: read_csv(results/name) for name in OUTPUT_NAMES}
    mothers, controls, assignments = (tables[n] for n in ("original_mothers", "greedy_controls", "greedy_assignments"))
    for old, new in ((old_mothers, mothers), (old_controls, controls), (old_assignments, assignments)):
        parity(old, new)
    require(len(mothers) == EXPECTED["mothers"] and len(controls) == EXPECTED["controls"], "Frozen populations changed")
    indexed(controls)
    require(dict(Counter(r["match_status"] for r in assignments)) == EXPECTED["status_counts"] == summary["old_status_counts"], "Old status totals differ")
    graph = audit_graph(mothers, tables["eligible_edges"], tables["maximum_allocation"], controls,
                        assignments, summary["capacity"])
    mother_map = indexed(mothers)
    fold_windows = {fold: (stamp(start), stamp(end)-timedelta(hours=72)) for fold, start, end in FOLDS}
    actual_times = {stamp(row["decision_time"], grid=True) for row in mothers}
    for edge in tables["eligible_edges"]:
        mother = mother_map[edge["event_id"]]
        candidate = stamp(edge["candidate_id"], grid=True)
        maternal = stamp(mother["decision_time"], grid=True)
        lower, upper = fold_windows[mother["fold"]]
        require(lower <= candidate < upper, "Candidate outside fold/embargo")
        require((candidate.year, candidate.month, candidate.hour//6) ==
                (maternal.year, maternal.month, maternal.hour//6), "Candidate month/session mismatch")
        require(candidate not in actual_times, "Actual mother time was used as control")
    audits = indexed(tables["mother_audit"])
    require(audits.keys() == mother_map.keys(), "Mother audit dropped a mother")
    edge_counts = Counter(m for m, _ in graph["edge_pairs"])
    for mother, row in audits.items():
        require(row["fold"] == row["audit_fold"] == mother_map[mother]["fold"], "Mother audit fold drift")
        require(row["match_status"] == row["reconstructed_status"] == indexed(assignments)[mother]["match_status"], "Mother audit status drift")
        if boolean(row["mother_search_reached"]):
            same_number(row["preallocation_available"], edge_counts[mother], "Mother graph degree mismatch")
            same_number(row["available_before_greedy"], integer(row["preallocation_available"])-integer(row["used_before_count"]), "Greedy supply arithmetic mismatch")
        else:
            require(row["preallocation_available"] == row["available_before_greedy"] == row["used_before_count"] == ""
                    and edge_counts[mother] == 0, "Unavailable supply was filled with zero or edges")
        same_number(row["selected_count"], graph["old_counts"][mother], "Mother selected count mismatch")
    selected_greedy = {(r["event_id"], r["candidate_id"]) for r in tables["greedy_edges"] if boolean(r["selected"])}
    require(selected_greedy == graph["old_pairs"], "Selected greedy edges differ from historical controls")
    require(len(tables["greedy_edges"]) == len(graph["edge_pairs"])
            and {(r["event_id"], r["candidate_id"]) for r in tables["greedy_edges"]} == graph["edge_pairs"], "Greedy graph differs from admissible graph")
    fold_rows = indexed(tables["fold_coverage"], "fold")
    fold_summary = indexed(summary["folds"], "fold")
    require(set(fold_rows) == set(fold_summary) == {f[0] for f in FOLDS}, "Fold coverage incomplete")
    require(len({stamp(r["decision_time"], grid=True) for r in tables["matching_frame"]}) == len(tables["matching_frame"]),
            "Matching-frame candidate timestamps are not unique")
    for fold, start, end in FOLDS:
        for collection in (mothers, controls, assignments):
            require(all(r["fold"] in fold_rows for r in collection), "Unknown fold")
            for row in collection:
                if row["fold"] == fold:
                    require(stamp(start) <= stamp(row["decision_time"], grid=True) < stamp(end)-timedelta(hours=72), "Decision outside fold/embargo")
        n = sum(r["fold"] == fold for r in mothers)
        old_n = sum(mother_map[m]["fold"] == fold for m in graph["old_counts"])
        new_n = sum(mother_map[m]["fold"] == fold for m in graph["selected_counts"])
        active = [r for r in tables["matching_frame"] if stamp(start) <= stamp(r["decision_time"], grid=True) < stamp(end)-timedelta(hours=72)]
        values = {"mothers": n, "greedy_matched": old_n, "maximum_matched": new_n,
            "allocation_recoverable": new_n-old_n, "greedy_coverage": old_n/n,
            "maximum_coverage": new_n/n, "active_hourly_rows": len(active),
            "active_candidate_eligible": sum(boolean(r["candidate_eligible"]) for r in active),
            "historical_cumulative_pool_before_keys": receipts[[x[0] for x in FOLDS].index(fold)]["candidate_count_before_exact_keys"]}
        for key, value in values.items():
            same_number(fold_rows[fold][key], value, "Fold CSV mismatch: " + key)
            same_number(fold_summary[fold][key], value, "Fold summary mismatch: " + key)
    for key, value in (("mothers", len(mothers)), ("greedy_matched", graph["greedy_matched"]),
                       ("greedy_controls", len(controls)), ("maximum_matched", graph["maximum_matched"]),
                       ("maximum_coverage", graph["maximum_matched"]/len(mothers)),
                       ("allocation_recoverable", graph["maximum_matched"]-graph["greedy_matched"]),
                       ("matching_edges", graph["matching_edges"]), ("required_complete_mothers", 226)):
        same_number(summary[key], value, "Global summary mismatch: " + key)
    for key in ("mothers", "matching_edges"):
        same_number(frozen[key], graph[key], "Checkpoint count mismatch")
    for key in ("historical_full_parity", "original_assignment_feasible"):
        require(summary[key] is True and frozen[key] is True, "Parity/feasibility attestation missing")
    enough = graph["maximum_matched"] >= 226
    require(summary["coverage_gate_attainable"] is enough and summary["status"] ==
            ("strict_support_attainable_not_profit_test" if enough else "strict_support_unattainable"), "Capacity gate conclusion mismatch")
    return {"ok": True, "experiment_id": EXPERIMENT_ID, "mothers": len(mothers),
        "historical_controls": len(controls), "greedy_matched": graph["greedy_matched"],
        "maximum_matched": graph["maximum_matched"], "matching_edges": graph["matching_edges"],
        "independent_component_upper_bound": graph["independent_component_upper_bound"],
        "independent_optimum_proven_for_saved_graph": graph["independent_optimum_proven_for_saved_graph"],
        "coverage_gate_independent_conclusion": "attainable" if graph["maximum_matched"] >= 226 else
            "unattainable" if graph["independent_component_upper_bound"] < 226 else "unresolved",
        "allocation_feasible": True, "solver_certificate_consistent": True, "output_hashes_verified": len(OUTPUT_NAMES),
        "source_commit_hashes_verified": len(source_records), "raw_prices_read": False,
        "historical_outcomes_read": False, "solver_called": False,
        "caveat": "Saved-graph verification only; no raw feature replay or proof of graph completeness, no profitability claim. Unselected IDs are not individually impossible."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT/EXPERIMENT_PATH/"results")
    args = parser.parse_args(argv)
    try:
        result = verify_results(args.results)
    except (VerificationError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "raw_prices_read": False, "solver_called": False}))
        return 1
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
