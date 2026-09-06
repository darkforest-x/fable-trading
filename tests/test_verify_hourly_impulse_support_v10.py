"""Synthetic V10 verifier tests. No actual result, raw price, or MILP access."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path

import pytest


_PATH = Path(__file__).parents[1]/"scripts/verify_hourly_impulse_support_v10.py"
_SPEC = importlib.util.spec_from_file_location("verify_hourly_impulse_support_v10", _PATH)
v = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(v)


def time(hour):
    return (datetime(2023, 1, 1, tzinfo=timezone.utc)+timedelta(hours=hour)).isoformat()


def certificate(mothers, rows, selected):
    edges = {(r["event_id"], r["candidate_id"]) for r in rows}
    components = v.component_bound([m["event_id"] for m in mothers], edges)
    chosen = {r["event_id"] for r in selected}
    bound = sum(c["complete_mother_upper_bound"] for c in components)
    return {"optimal": True, "solution_verified": True, "mother_count": len(mothers),
        "candidate_count": len({c for _, c in edges}), "edge_count": len(edges), "count_per_mother": 3,
        "matched_mothers": len(chosen), "allocated_controls": len(selected),
        "unmatched_mothers": len(mothers)-len(chosen), "connected_component_upper_bound": bound,
        "connected_component_count": len(components), "complete_mother_upper_bound": len(chosen),
        "components": [dict(c, component_id=i) for i, c in enumerate(components)],
        "mothers_without_edges": sorted({m["event_id"] for m in mothers}-{m for m, _ in edges}),
        "unmatched_mother_ids": sorted({m["event_id"] for m in mothers}-chosen),
        "solver_called": bound > 0, "solver_status": 0 if bound else None,
        "solver_objective": -len(chosen), "solver_dual_bound": -len(chosen), "solver_mip_gap": 0}


def graph_fixture():
    mothers = [{"event_id": m, "fold": "2023H1"} for m in ("a", "b", "c")]
    rows = [{"event_id": m, "candidate_id": time(hour), "candidate_time": time(hour),
             "fold": "2023H1", "audit_fold": "2023H1"} for m, hour in
            [("a", 1), ("a", 2), ("a", 3), ("a", 4), ("a", 5), ("a", 6),
             ("b", 1), ("b", 2), ("b", 3)]]
    selected = [{"event_id": m, "candidate_id": time(hour), "fold": "2023H1"}
                for m, hour in [("a", 4), ("a", 5), ("a", 6), ("b", 1), ("b", 2), ("b", 3)]]
    controls = [{"event_id": "old"+str(i), "parent_event_id": "a", "decision_time": time(i), "fold": "2023H1"}
                for i in range(1, 4)]
    assignments = [{"event_id": m, "match_status": "matched" if m == "a" else "insufficient_exact_controls",
                    "assigned_controls": "3" if m == "a" else "0"} for m in ("a", "b", "c")]
    return [mothers, rows, selected, controls, assignments, certificate(mothers, rows, selected)]


def test_independent_components_and_full_allocation_prove_saved_graph_capacity():
    report = v.audit_graph(*graph_fixture())
    assert report["maximum_matched"] == 2 and report["greedy_matched"] == 1
    assert report["independent_component_upper_bound"] == 2
    assert report["independent_optimum_proven_for_saved_graph"]


def test_loose_component_bound_does_not_pretend_to_independently_prove_optimum():
    mothers = [{"event_id": m, "fold": "2023H1"} for m in ("a", "b", "c")]
    edges = [{"event_id": m, "candidate_id": time(h), "candidate_time": time(h), "fold": "2023H1", "audit_fold": "2023H1"}
             for m, h in [("a", 1), ("a", 2), ("b", 2), ("b", 3), ("c", 3), ("c", 1)]]
    assignments = [{"event_id": m, "match_status": "insufficient_exact_controls", "assigned_controls": "0"} for m in ("a", "b", "c")]
    report = v.audit_graph(mothers, edges, [], [], assignments, certificate(mothers, edges, []))
    assert report["independent_component_upper_bound"] == 1
    assert not report["independent_optimum_proven_for_saved_graph"]


@pytest.mark.parametrize("change", ["mother_missing", "mother_duplicate", "assignment_missing", "edge_duplicate", "edge_orphan",
    "edge_empty", "edge_alias", "edge_ns", "edge_wrong_time", "edge_fold", "partial", "illegal", "reuse", "old_illegal", "old_status", "old_count",
    "capacity_count", "capacity_candidate", "capacity_component", "capacity_unmatched", "capacity_without_edges", "not_optimal",
    "not_verified", "timeout", "gap", "dual", "objective", "saved_bound", "wrong_fold"])
def test_invalid_graph_or_certificate_fails_closed(change):
    m, e, a, c, s, cap = graph_fixture()
    if change == "mother_missing": m.pop()
    elif change == "mother_duplicate": m.append(dict(m[0]))
    elif change == "assignment_missing": s.pop()
    elif change == "edge_duplicate": e.append(dict(e[0]))
    elif change == "edge_orphan": e[0]["event_id"] = "orphan"
    elif change == "edge_empty": e[0]["candidate_id"] = ""
    elif change == "edge_alias": e[0]["candidate_id"] = time(1).replace("+00:00", "Z")
    elif change == "edge_ns": e[0]["candidate_id"] = time(1).replace("+00:00", ".000000001+00:00")
    elif change == "edge_wrong_time": e[0]["candidate_time"] = time(2)
    elif change == "edge_fold": e[0]["audit_fold"] = "2023H2"
    elif change == "partial": a.pop()
    elif change == "illegal": a[0]["candidate_id"] = time(100)
    elif change == "reuse": a[0]["candidate_id"] = time(1)
    elif change == "old_illegal": c[0]["decision_time"] = time(100)
    elif change == "old_status": s[1]["match_status"] = "matched"
    elif change == "old_count": s[0]["assigned_controls"] = "2"
    elif change == "capacity_count": cap["matched_mothers"] = 3
    elif change == "capacity_candidate": cap["candidate_count"] = 7
    elif change == "capacity_component": cap["components"][0]["candidate_count"] += 1
    elif change == "capacity_unmatched": cap["unmatched_mother_ids"] = []
    elif change == "capacity_without_edges": cap["mothers_without_edges"] = []
    elif change == "not_optimal": cap["optimal"] = False
    elif change == "not_verified": cap["solution_verified"] = False
    elif change == "timeout": cap["solver_status"] = 1
    elif change == "gap": cap["solver_mip_gap"] = .1
    elif change == "dual": cap["solver_dual_bound"] = -3
    elif change == "objective": cap["solver_objective"] = -1
    elif change == "saved_bound": cap["connected_component_upper_bound"] = 3
    else: a[0]["fold"] = "2023H2"
    with pytest.raises((v.VerificationError, KeyError)):
        v.audit_graph(m, e, a, c, s, cap)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["placeholder"]
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as dest:
        writer = csv.DictWriter(dest, columns)
        writer.writeheader(); writer.writerows(rows)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """251 fake named mothers; synthetic timestamps only, no actual data import."""
    root = tmp_path
    results = root/v.EXPERIMENT_PATH/"results"
    results.mkdir(parents=True)
    parent = root/v.PARENT_PATH
    counts = [55, 66, 55, 75]
    matched_counts = [28, 40, 41, 45]
    mothers, assignments, controls, edges, allocation, audits, frames, fold_rows, receipts = ([] for _ in range(9))
    for (fold, start, end), total, matched in zip(v.FOLDS, counts, matched_counts):
        base_time = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        for i in range(total):
            mother = fold+"_mother_"+str(i)
            decision = (base_time+timedelta(hours=6*i)).isoformat()
            row = {"event_id": mother, "fold": fold, "decision_time": decision}
            mothers.append(row)
            missing = fold == "2023H1" and i >= total-3
            status = "matched" if i < matched else "missing_causal_matching_support" if missing else "insufficient_exact_controls"
            assignments.append(dict(row, match_status=status, assigned_controls=str(3 if i < matched else 0)))
            audits.append(dict(row, audit_fold=fold, match_status=status, reconstructed_status=status,
                mother_search_reached=not missing, preallocation_available="" if missing else str(3 if i < matched else 0),
                used_before_count="" if missing else "0", available_before_greedy="" if missing else str(3 if i < matched else 0),
                selected_count=str(3 if i < matched else 0)))
            frames.append({"decision_time": decision, "candidate_eligible": False})
            if i < matched:
                for ordinal in range(1, 4):
                    when = (base_time+timedelta(hours=6*i+ordinal)).isoformat()
                    controls.append({"event_id": mother+"::control"+str(ordinal), "parent_event_id": mother,
                                     "decision_time": when, "fold": fold})
                    edges.append({"event_id": mother, "candidate_id": when, "candidate_time": when, "fold": fold, "audit_fold": fold})
                    allocation.append({"event_id": mother, "candidate_id": when, "fold": fold})
                    frames.append({"decision_time": when, "candidate_eligible": True})
        receipts.append({"candidate_count_before_exact_keys": len(controls)})
        fold_rows.append({"fold": fold, "mothers": total, "greedy_matched": matched, "maximum_matched": matched,
            "allocation_recoverable": 0, "greedy_coverage": matched/total, "maximum_coverage": matched/total,
            "active_hourly_rows": total+3*matched, "active_candidate_eligible": 3*matched,
            "historical_cumulative_pool_before_keys": len(controls)})
    tables = {"original_mothers": mothers, "greedy_controls": [dict(r, audit_fold=r["fold"]) for r in controls],
        "greedy_assignments": [dict(r, audit_fold=r["fold"]) for r in assignments],
        "mother_audit": audits, "eligible_edges": edges, "greedy_edges": [dict(r, selected=True) for r in edges],
        "maximum_allocation": allocation, "fold_coverage": fold_rows, "matching_frame": frames,
        "candidate_stages": [], "key_supply": [], "stage_counts": []}
    for name, rows in tables.items(): write_csv(results/(name+".csv.gz"), rows)
    for name, rows in (("original_mothers.csv.gz", mothers), ("control_mothers.csv.gz", controls), ("assignments.csv", assignments)):
        write_csv(parent/name, rows)
    write_json(parent/"assignment_receipt.json", receipts)
    inputs = {name: v.sha(parent/name) for name in v.INPUT_NAMES}
    base = {"development_folds": v.FOLDS, "source": {"sha256": "a"*64, "end_exclusive": "2026-02-28T16:00:00Z", "path": "data/MUST_NOT_READ.csv"}}
    write_json(root/v.BASE_CONFIG, base)
    config = {"experiment_id": v.EXPERIMENT_ID, "base_config": v.BASE_CONFIG, "parent_results": v.PARENT_PATH,
        "matching": v.MATCHING, "expected": v.EXPECTED, "inputs": inputs, "base_config_sha256": v.sha(root/v.BASE_CONFIG),
        "capacity": {"required_complete_mothers": 226, "minimum_coverage": .9, "optimal_required": True, "outcomes_used": False},
        "no_audit_entry_point": True, "no_outcome_entry_point": True, "holdout_consumed": False,
        "training_eligible": False, "production_eligible": False}
    config_path = root/v.EXPERIMENT_PATH/"config.json"
    write_json(config_path, config)
    for name in v.SOURCE_PY | {v.EXPERIMENT_PATH+"/PROJECT_PLAN.md"}:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic committed source\n")
    source_paths = v.SOURCE_PY | {v.BASE_CONFIG, v.EXPERIMENT_PATH+"/config.json", v.EXPERIMENT_PATH+"/PROJECT_PLAN.md"}
    sources = [{"path": name, "sha256": v.sha(root/name)} for name in sorted(source_paths)]
    committed = {name: (root/name).read_bytes() for name in source_paths}
    monkeypatch.setattr(v, "git_blob", lambda _root, _commit, path: committed[path])
    source = {"sha256": "a"*64, "holdout_price_rows": 0, "timestamp_preflight_before_price_hash": True,
        "phase_rows": 10000, "physical_rows": 20000, "phase_price_last_open": "2024-12-31T23:55:00Z",
        "physical_last_open": "2026-02-28T15:55:00Z"}
    hashes = {name: v.sha(results/name) for name in v.OUTPUT_NAMES}
    started = {"at": "2026-09-06T00:00:00Z", "sources": sources, "inputs": inputs, "builder_commit": "b"*40}
    frozen = {"generated_at": "2026-09-06T00:00:01Z", "source_receipts": sources, "source_receipt": source,
        "output_hashes": {name: hashes[name] for name in v.CHECKPOINT_NAMES}, "greedy_receipts": receipts,
        "mothers": 251, "matching_edges": len(edges), "capacity_attempted": False,
        "historical_full_parity": True, "original_assignment_feasible": True}
    summary = {"experiment_id": v.EXPERIMENT_ID, "mothers": 251, "greedy_matched": 154, "greedy_controls": 462,
        "maximum_matched": 154, "maximum_coverage": 154/251, "allocation_recoverable": 0,
        "required_complete_mothers": 226, "coverage_gate_attainable": False, "status": "strict_support_unattainable",
        "matching_edges": len(edges), "capacity": certificate(mothers, edges, allocation), "folds": fold_rows,
        "old_status_counts": v.EXPECTED["status_counts"], "historical_full_parity": True, "original_assignment_feasible": True,
        "outcomes_read_or_computed": False, "profitability_test": False, "holdout_consumed": False,
        "training_eligible": False, "production_eligible": False, "source_receipt": source,
        "config_sha256": v.sha(config_path), "output_hashes": hashes, "source_receipts": sources,
        "generated_at": "2026-09-06T00:00:02Z"}
    for name, obj in (("summary.json", summary), ("started.json", started), ("support_frozen.json", frozen)):
        write_json(results/name, obj)
    return root, results, tables, summary, frozen, started


def refresh(bundle):
    root, results, tables, summary, frozen, started = bundle
    for name, rows in tables.items(): write_csv(results/(name+".csv.gz"), rows)
    summary["output_hashes"] = {name: v.sha(results/name) for name in v.OUTPUT_NAMES}
    frozen["output_hashes"] = {name: summary["output_hashes"][name] for name in v.CHECKPOINT_NAMES}
    for name, obj in (("summary.json", summary), ("started.json", started), ("support_frozen.json", frozen)):
        write_json(results/name, obj)


def test_complete_saved_bundle_passes_without_reading_raw_or_calling_solver(bundle):
    root, results, *_ = bundle
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = v.verify_results(results, repository_root=root)
    assert report["ok"] and report["mothers"] == 251 and report["historical_controls"] == 462
    assert report["independent_optimum_proven_for_saved_graph"]
    assert report["coverage_gate_independent_conclusion"] == "unattainable"
    assert not report["raw_prices_read"] and not report["historical_outcomes_read"] and not report["solver_called"]
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
    assert not (root/"data").exists()


@pytest.mark.parametrize("name", ["summary.json", "support_frozen.json", "started.json", "eligible_edges.csv.gz", "maximum_allocation.csv.gz"])
def test_missing_or_running_results_fail_closed(bundle, name):
    root, results, *_ = bundle
    (results/name).unlink()
    with pytest.raises((v.VerificationError, FileNotFoundError)):
        v.verify_results(results, repository_root=root)


def test_failure_record_rejects_even_when_a_summary_exists(bundle):
    root, results, *_ = bundle
    write_json(results/"failure.json", {"status": "failed_not_capacity_evidence"})
    with pytest.raises(v.VerificationError, match="failure.json"):
        v.verify_results(results, repository_root=root)


@pytest.mark.parametrize("change", ["output_hash", "checkpoint_hash", "checkpoint_subset", "checkpoint_after", "clock_order", "source_receipt",
    "source_hash", "source_scope", "source_commit", "input_hash", "input_scope", "config_hash", "prefix_future", "source_rows", "holdout_receipt",
    "fold_mean", "fold_count", "global_count", "gate", "old_status", "read_outcomes", "profitability"])
def test_receipt_summary_and_provenance_counterexamples(bundle, monkeypatch, change):
    root, results, tables, summary, frozen, started = bundle
    if change == "output_hash": summary["output_hashes"]["eligible_edges.csv.gz"] = "0"*64
    elif change == "checkpoint_hash": frozen["output_hashes"]["eligible_edges.csv.gz"] = "0"*64
    elif change == "checkpoint_subset": frozen["output_hashes"].pop("eligible_edges.csv.gz")
    elif change == "checkpoint_after": frozen["capacity_attempted"] = True
    elif change == "clock_order": frozen["generated_at"] = "2026-09-07T00:00:00Z"
    elif change == "source_receipt": frozen["source_receipt"] = {"wrong": True}
    elif change == "source_hash": (root/next(iter(v.SOURCE_PY))).write_text("changed")
    elif change == "source_scope": started["sources"].append({"path": "data/raw.csv", "sha256": "0"*64})
    elif change == "source_commit": monkeypatch.setattr(v, "git_blob", lambda *args: b"not committed")
    elif change == "input_hash": (root/v.PARENT_PATH/"assignments.csv").write_text("changed")
    elif change == "input_scope": started["inputs"] = {"trades.csv": "0"*64}
    elif change == "config_hash": summary["config_sha256"] = "0"*64
    elif change == "prefix_future": summary["source_receipt"]["phase_price_last_open"] = "2025-01-01T00:00:00Z"
    elif change == "source_rows": summary["source_receipt"]["phase_rows"] = 30000
    elif change == "holdout_receipt": summary["source_receipt"]["holdout_price_rows"] = 1
    elif change == "fold_mean": summary["folds"][0]["maximum_coverage"] = .9
    elif change == "fold_count": summary["folds"][0]["maximum_matched"] += 1
    elif change == "global_count": summary["maximum_matched"] += 1
    elif change == "gate": summary["coverage_gate_attainable"] = True
    elif change == "old_status": summary["old_status_counts"] = {"matched": 251}
    elif change == "read_outcomes": summary["outcomes_read_or_computed"] = True
    else: summary["profitability_test"] = True
    for name, obj in (("summary.json", summary), ("started.json", started), ("support_frozen.json", frozen)):
        write_json(results/name, obj)
    with pytest.raises((v.VerificationError, KeyError)):
        v.verify_results(results, repository_root=root)


@pytest.mark.parametrize("change", ["mother_audit_drop", "missing_supply_zero", "supply_degree", "supply_arithmetic", "selected_count",
    "greedy_selected", "greedy_graph_drop", "old_controls_value", "allocation_partial", "edge_duplicate", "fold_table", "matching_rows", "outcome_column"])
def test_rehashed_bad_tables_still_fail_semantic_checks(bundle, change):
    root, results, tables, summary, frozen, started = bundle
    if change == "mother_audit_drop": tables["mother_audit"].pop()
    elif change == "missing_supply_zero": next(r for r in tables["mother_audit"] if not r["mother_search_reached"])["preallocation_available"] = "0"
    elif change == "supply_degree": tables["mother_audit"][0]["preallocation_available"] = "4"
    elif change == "supply_arithmetic": tables["mother_audit"][0]["used_before_count"] = "1"
    elif change == "selected_count": tables["mother_audit"][0]["selected_count"] = "2"
    elif change == "greedy_selected": tables["greedy_edges"][0]["selected"] = False
    elif change == "greedy_graph_drop": tables["greedy_edges"].pop()
    elif change == "old_controls_value": tables["greedy_controls"][0]["decision_time"] = "2023-01-01T00:00:00+00:00"
    elif change == "allocation_partial": tables["maximum_allocation"].pop()
    elif change == "edge_duplicate": tables["eligible_edges"].append(dict(tables["eligible_edges"][0]))
    elif change == "fold_table": tables["fold_coverage"][0]["mothers"] = 54
    elif change == "matching_rows": tables["matching_frame"].pop()
    else:
        for r in tables["mother_audit"]: r["net_return"] = "not permitted"
    refresh(bundle)
    with pytest.raises((v.VerificationError, KeyError)):
        v.verify_results(results, repository_root=root)


@pytest.mark.parametrize("candidate,reason", [
    ("2023-06-28T00:00:00+00:00", "fold/embargo"),
    ("2022-12-31T00:00:00+00:00", "fold/embargo"),
    ("2023-02-01T00:00:00+00:00", "month/session"),
    ("2023-01-25T08:00:00+00:00", "month/session"),
    ("2023-01-01T00:00:00+00:00", "Actual mother"),
])
def test_even_unallocated_edges_must_respect_frozen_time_support(bundle, candidate, reason):
    root, results, tables, summary, frozen, started = bundle
    mother = tables["original_mothers"][28]  # causal-ready but historically unmatched
    edge = {"event_id": mother["event_id"], "candidate_id": candidate, "candidate_time": candidate,
            "fold": mother["fold"], "audit_fold": mother["fold"]}
    tables["eligible_edges"].append(edge)
    tables["greedy_edges"].append(dict(edge, selected=False))
    summary["capacity"] = certificate(tables["original_mothers"], tables["eligible_edges"], tables["maximum_allocation"])
    summary["matching_edges"] += 1
    frozen["matching_edges"] += 1
    for row in tables["mother_audit"]:
        if row["event_id"] == mother["event_id"]:
            row["preallocation_available"] = row["available_before_greedy"] = "1"
    refresh(bundle)
    with pytest.raises(v.VerificationError, match=reason):
        v.verify_results(results, repository_root=root)


def test_cli_missing_results_reports_json_failure_without_writes(tmp_path, capsys):
    assert v.main(["--results", str(tmp_path/"missing")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["solver_called"] is False
    assert not list(tmp_path.iterdir())


def test_cli_success_prints_stdout_json(bundle, monkeypatch, capsys):
    root, results, *_ = bundle
    original = v.verify_results
    monkeypatch.setattr(v, "verify_results", lambda path: original(path, repository_root=root))
    assert v.main(["--results", str(results)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_rejects_duplicate_json_keys_and_ragged_csv(tmp_path):
    path = tmp_path/"file.json"
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(v.VerificationError): v.read_json(path)
    table = tmp_path/"table.csv"
    table.write_text("a,b\n1,2,3\n")
    with pytest.raises(v.VerificationError): v.read_csv(table)


def test_verifier_has_no_research_or_solver_imports():
    import ast
    tree = ast.parse(_PATH.read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(name.startswith(("yoyo", "scipy", "pandas")) for name in imports)
