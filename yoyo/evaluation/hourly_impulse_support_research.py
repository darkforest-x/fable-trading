"""V10 outcome-free exact-control support audit for original hourly MA crosses.

Only completed-hour/5m features and the decision's contemporaneous open are
materialized, through reused development2024. Original251 K1 mothers, matching
keys, crossing exclusions, three controls and no reuse remain frozen. No K2
waiting, exits, MFE, PNL, model fit or profitability selection is performed.
The existing Study is used solely for price-prefix loading and causal features;
its evaluation methods are never called. Matching capacity is an offline
same-month support question, not a prefix-stable online allocation or random
treatment assignment. Later controls in the same month retain their own causal
feature times; their availability cannot become a live signal filter.

Exact identity checks use pandas2.3 merge/assertions (null keys are rejected):
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
Capacity solver status follows SciPy1.13.1:
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_k2_matching import build_matching_frame
from yoyo.evaluation.hourly_impulse_matching_capacity import maximum_complete_matching
from yoyo.evaluation.hourly_impulse_matching_support import build_support_audit
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, clean, digest, utc, write_csv, write_json


EXPERIMENT_ID = "exp-btcusdtp-1h-matching-support-preholdout-20260906-v10"
EXPERIMENT = ROOT/"experiments/active"/EXPERIMENT_ID
BASE_CONFIG = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
BASE_SHA256 = "95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a"
PARENT = "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
INPUTS = {
    "original_mothers.csv.gz": "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
    "control_mothers.csv.gz": "01050c7a9602f469406df515edcc73ef2f4c9db2d46529e25030934012eebd5a",
    "assignments.csv": "671782877ee67824f7687243d5e7deae29d78a0bcba6245319ecf55629027b0f",
    "assignment_receipt.json": "1d77ca407712520e645463d30f97d26d452ccce45e87e68c2adcbc4120c43220",
}
FOLDS = [["2023H1", "2023-01-01", "2023-07-01"], ["2023H2", "2023-07-01", "2024-01-01"],
         ["2024H1", "2024-01-01", "2024-07-01"], ["2024H2", "2024-07-01", "2025-01-01"]]
MATCHING = {"count": 3, "seed": 20260906, "embargo_hours": 72,
            "no_reuse": True, "no_fallback": True, "keys_unchanged": True}
CAPACITY = {"minimum_coverage": .9, "required_complete_mothers": 226,
            "time_limit_seconds": 30.0, "optimal_required": True, "outcomes_used": False}
EXPECTED = {"mothers": 251, "controls": 462, "matched": 154,
            "status_counts": {"matched": 154, "insufficient_exact_controls": 94,
                              "missing_causal_matching_support": 3}}
SOURCES = [
    "yoyo/data/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_k2_matching.py",
    "yoyo/evaluation/hourly_impulse_matching_support.py",
    "yoyo/evaluation/hourly_impulse_matching_capacity.py",
    "yoyo/evaluation/hourly_impulse_support_research.py",
    "tests/test_hourly_impulse_k2_matching.py",
    "tests/test_hourly_impulse_matching_support.py",
    "tests/test_hourly_impulse_matching_capacity.py",
    "tests/test_hourly_impulse_support_research.py",
]


def frozen_config():
    return {"experiment_id": EXPERIMENT_ID, "base_config": BASE_CONFIG,
            "base_config_sha256": BASE_SHA256, "parent_results": PARENT, "inputs": INPUTS,
            "matching": MATCHING, "capacity": CAPACITY, "expected": EXPECTED,
            "no_audit_entry_point": True, "no_outcome_entry_point": True,
            "holdout_consumed": False, "training_eligible": False, "production_eligible": False}


def verify_config(config, base):
    """Reject any frozen design drift before price materialization."""
    if json.dumps(config, sort_keys=True) != json.dumps(frozen_config(), sort_keys=True):
        raise ValueError("Frozen outcome-free support contract changed")
    if base["development_folds"] != FOLDS:
        raise ValueError("Only original2023--2024 folds are permitted")
    e = base["execution"]
    if e["cost_fraction"] != .002 or e["max_hours"] != 72 or e["stop_first"] is not True:
        raise ValueError("Do not change inherited execution economics during a support audit")


def read_table(path):
    frame = pd.read_csv(path)
    for column in frame:
        if column.endswith(("_time", "_available")):
            frame[column] = pd.to_datetime(frame[column], utc=True, format="mixed")
    return frame


def exact_saved_parity(before, after):
    """Every saved column; exact timestamps,1e-12 serialization tolerance."""
    for frame in (before, after):
        if not frame.columns.is_unique or frame.event_id.isna().any() or not frame.event_id.is_unique:
            raise ValueError("Parity requires unique finite event IDs and columns")
    a, b = (x.set_index("event_id").sort_index() for x in (before, after))
    if not a.index.equals(b.index) or set(a) != set(b):
        raise ValueError("Historical mother/assignment schema or population changed")
    for column in a:
        left, right = a[column], b[column]
        time = column.endswith(("_time", "_available"))
        if time:
            left, right = (pd.to_datetime(x, utc=True, format="mixed") for x in (left, right))
        pd.testing.assert_series_equal(left, right, check_dtype=False, check_exact=time,
                                       rtol=1e-12, atol=1e-12)


def validate_population(mothers, controls, assignments):
    for frame, count in ((mothers, EXPECTED["mothers"]), (controls, EXPECTED["controls"]),
                         (assignments, EXPECTED["mothers"])):
        if len(frame) != count or frame.event_id.isna().any() or not frame.event_id.is_unique:
            raise ValueError("Frozen original population/identity count changed")
        for fold, start, end in FOLDS:
            times = pd.to_datetime(frame.loc[frame.fold.eq(fold), "decision_time"], utc=True)
            if times.isna().any() or not (times.ge(utc(start)) & times.lt(utc(end)-pd.Timedelta(hours=72))).all():
                raise ValueError("Mother/control decision outside frozen fold and embargo")
        if not frame.fold.isin([x[0] for x in FOLDS]).all():
            raise ValueError("Unknown fold")
    if set(assignments.event_id) != set(mothers.event_id):
        raise ValueError("Assignments must cover every original mother")
    if assignments.match_status.value_counts().to_dict() != EXPECTED["status_counts"]:
        raise ValueError("Historical support status counts changed")
    if not set(controls.parent_event_id).issubset(set(mothers.event_id)):
        raise ValueError("Orphan historical control")
    counts = controls.groupby("parent_event_id").size()
    if not counts.eq(3).all() or set(counts.index) != set(assignments.loc[assignments.match_status.eq("matched"), "event_id"]):
        raise ValueError("Historical triplets changed")
    if pd.to_datetime(controls.decision_time, utc=True).duplicated().any():
        raise ValueError("Historical control time reused")


def audit_population(study, originals, saved_controls, saved_assignments, saved_receipts, *, checkpoint=None):
    """Reconstruct support, prove old allocation parity, only then solve capacity."""
    mothers = study.entries(study.config["baseline"])
    exact_saved_parity(originals, mothers)
    matching = build_matching_frame(study.raw, study.featured(60, "SMA", 40),
                                     study.featured(5, "SMA", 40), mothers)
    pieces, receipts = {}, []
    for fold, _, end in study.folds:
        audit = build_support_audit(mothers.loc[mothers.fold.eq(fold)], matching,
                                   count=3, seed=20260906, end_exclusive=utc(end), embargo_hours=72)
        receipts.append(audit["greedy_diagnostics"])
        for key, frame in audit.items():
            if isinstance(frame, pd.DataFrame):
                pieces.setdefault(key, []).append(frame.copy().assign(audit_fold=fold))
    tables = {name: pd.concat(parts, ignore_index=True) for name, parts in pieces.items()}
    for saved, name in ((saved_controls, "greedy_controls"), (saved_assignments, "greedy_assignments")):
        exact_saved_parity(saved, tables[name].drop(columns="audit_fold"))
    if clean(receipts) != saved_receipts:
        raise ValueError("Historical assignment hashes or diagnostics changed")
    edges = tables["eligible_edges"]
    if edges.groupby("candidate_id").audit_fold.nunique().gt(1).any():
        raise ValueError("Fold reset would allow shared control reuse")
    greedy_edges = set(zip(saved_controls.parent_event_id,
                          pd.to_datetime(saved_controls.decision_time, utc=True).map(pd.Timestamp.isoformat)))
    if not greedy_edges.issubset(set(zip(edges.event_id, edges.candidate_id))):
        raise ValueError("Original assignment is not feasible in reconstructed graph")
    tables.update(original_mothers=mothers, matching_frame=matching)
    if checkpoint is not None:
        checkpoint(tables, {"historical_full_parity": True,
            "original_assignment_feasible": True, "greedy_receipts": receipts,
            "mothers": len(mothers), "matching_edges": len(edges), "capacity_attempted": False})
    allocation, capacity = maximum_complete_matching(mothers.event_id.tolist(),
        edges[["event_id", "candidate_id"]], count=3, time_limit=CAPACITY["time_limit_seconds"])
    if capacity.get("optimal") is not True or capacity["matched_mothers"] < EXPECTED["matched"]:
        raise ValueError("No certified capacity consistent with original feasible assignment")
    allocation = allocation.merge(mothers[["event_id", "fold"]], on="event_id", validate="many_to_one")
    tables["maximum_allocation"] = allocation
    rows = []
    for fold, start, end in study.folds:
        a = tables["greedy_assignments"].loc[lambda x: x.audit_fold.eq(fold)]
        n = len(a)
        matched = int(a.match_status.eq("matched").sum())
        maximum = int(allocation.loc[allocation.fold.eq(fold), "event_id"].nunique())
        active = matching.decision_time.ge(utc(start)) & matching.decision_time.lt(utc(end)-pd.Timedelta(hours=72))
        rows.append({"fold": fold, "mothers": n, "greedy_matched": matched,
            "maximum_matched": maximum, "allocation_recoverable": maximum-matched,
            "greedy_coverage": matched/n if n else None, "maximum_coverage": maximum/n if n else None,
            "active_hourly_rows": int(active.sum()),
            "active_candidate_eligible": int((active & matching.candidate_eligible).sum()),
            "historical_cumulative_pool_before_keys": receipts[len(rows)]["candidate_count_before_exact_keys"]})
    tables["fold_coverage"] = pd.DataFrame(rows)
    enough = capacity["matched_mothers"] >= CAPACITY["required_complete_mothers"]
    summary = {"experiment_id": EXPERIMENT_ID,
        "status": "strict_support_attainable_not_profit_test" if enough else "strict_support_unattainable",
        "mothers": len(mothers), "greedy_matched": EXPECTED["matched"], "greedy_controls": len(saved_controls),
        "maximum_matched": capacity["matched_mothers"], "maximum_coverage": capacity["matched_mothers"]/len(mothers),
        "allocation_recoverable": capacity["matched_mothers"]-EXPECTED["matched"],
        "required_complete_mothers": 226, "coverage_gate_attainable": enough,
        "matching_edges": len(edges), "capacity": capacity, "folds": rows,
        "old_status_counts": saved_assignments.match_status.value_counts().to_dict(),
        "historical_full_parity": True, "original_assignment_feasible": True,
        "outcomes_read_or_computed": False, "profitability_test": False,
        "holdout_consumed": False, "training_eligible": False, "production_eligible": False,
        "limitation": "Offline exact same-month support; not online allocation, random treatment assignment, or profit evidence."}
    return tables, summary


def run():
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/BASE_CONFIG
    if digest(base_path) != BASE_SHA256:
        raise ValueError("Frozen base configuration hash changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    parent = ROOT/PARENT
    for name, expected in INPUTS.items():
        if digest(parent/name) != expected:
            raise ValueError("Frozen pre-entry input hash changed: "+name)
    tables = {name: read_table(parent/name) for name in INPUTS if name.endswith((".csv", ".gz"))}
    mothers, controls, assignments = (tables[x] for x in ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv"))
    validate_population(mothers, controls, assignments)
    saved_receipts = json.loads((parent/"assignment_receipt.json").read_text())
    results = EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve previous attempts; results already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
        "inputs": INPUTS, "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()})
    def checkpoint(tables, receipt):
        for name, table in tables.items():
            write_csv(results/(name+".csv.gz"), table)
        write_json(results/"support_frozen.json", {**receipt,
            "source_receipt": study.source_receipt, "source_receipts": sources,
            "output_hashes": {p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
            "generated_at": pd.Timestamp.now(tz="UTC")})
    try:
        study = Study(base, "development")
        outputs, summary = audit_population(study, mothers, controls, assignments, saved_receipts,
                                            checkpoint=checkpoint)
    except Exception as error:
        # Preserve the first attempt. A failure is never a capacity certificate.
        write_json(results/"failure.json", {"status": "failed_not_capacity_evidence",
            "error_type": type(error).__name__, "message": str(error),
            "diagnostics": getattr(error, "diagnostics", None),
            "support_frozen": (results/"support_frozen.json").exists(),
            "generated_at": pd.Timestamp.now(tz="UTC")})
        raise
    for name, table in outputs.items():
        # Keep the pre-capacity checkpoint byte-for-byte unchanged.
        if not (results/(name+".csv.gz")).exists():
            write_csv(results/(name+".csv.gz"), table)
    summary.update(source_receipt=study.source_receipt, config_sha256=digest(config_path),
        output_hashes={p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
        source_receipts=sources, generated_at=pd.Timestamp.now(tz="UTC"))
    write_json(results/"summary.json", summary)
    print(json.dumps(clean({k: summary[k] for k in ("status", "mothers", "greedy_matched", "maximum_matched", "maximum_coverage", "allocation_recoverable")})))


if __name__ == "__main__":
    run()
