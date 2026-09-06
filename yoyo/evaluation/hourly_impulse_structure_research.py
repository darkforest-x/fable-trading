"""V20: a persistent confirmed structure gate on fixed V18 hourly intentions.

Only complete own hourly OHLC up to K1 close establishes the gate. The fixed
10-left/10-right extrema-equality Python variant is not certified Pine pivot
tie parity. State persists across confirmed breaks, resetting on source gaps.
This family already failed on an older multiasset K1/K2 cohort; this test only
isolates its effect on the original251 BTC direct-K1 mothers and own controls.

The runner freezes all713 contexts and the whole causal hourly state trace
before opening any saved V18 outcomes. If support passes, it reuses unchanged
independent episode exits and recomputes policy accounting/serial occupancy.
This is NOT a fresh intrabar replay, independent validation, or new training.
Pine clock: https://www.tradingview.com/pine-script-docs/concepts/repainting/
"""
from __future__ import annotations

import json
import argparse
import subprocess
from copy import deepcopy

import pandas as pd

from yoyo.data.hourly_impulse import resample_complete
from yoyo.data.hourly_impulse_structure import add_structure_context, add_hourly_structure_state
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_research import ROOT, digest, load_source, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_support_research import (
    BASE_CONFIG, BASE_SHA256, PARENT, INPUTS, FOLDS, EXPECTED,
    read_table, validate_population,
)
from yoyo.evaluation import hourly_impulse_prior_breakout_research as support
from yoyo.evaluation.hourly_impulse_structure_accounting import evaluate_cached
from yoyo.evaluation.hourly_impulse_failed_launch_research import read_parent_frame
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity

EXPERIMENT_ID = "exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
PHASE_END = "2025-01-01"
OUTCOMES = "experiments/active/exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18/results/candidate"
OUTCOME_INPUTS = {
    "case_episodes.csv.gz": "f1d6d8c29af2c78f4fe0a3c79560b1ac9e21062202d3ac1b462b640463ad8e02",
    "control_episodes.csv.gz": "cdc677b08fab6185d2be363e871fe2f7cce0f5d72cdec52196e7c61ff52282e0",
    "matched.csv": "3604757c56daee054c3caed6fe9dbf28018c72a73c0faf151de3c97c76b60a8b",
    "single_pending.csv.gz": "c72d429fbd2193fa107d4335be835c37909596aedbc0184c50acababe23cd1ab",
}
PINE = "experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/0vET13Ra.pine"
PINE_SHA256 = "3a714019441695693642f4487754a56d8d55a0c9dcc280606abea6ff8cd66b52"
SOURCES = [
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
    "tests/test_hourly_impulse_structure_research.py", PINE,
]


def frozen_config():
    return {
        "experiment_id": EXPERIMENT_ID, "base_config": BASE_CONFIG,
        "base_config_sha256": BASE_SHA256, "parent_requests": PARENT,
        "request_inputs": deepcopy(INPUTS), "outcomes": OUTCOMES,
        "outcome_inputs": deepcopy(OUTCOME_INPUTS), "development_folds": deepcopy(FOLDS),
        "phase_end_exclusive": PHASE_END, "expected": deepcopy(EXPECTED),
        "support": deepcopy(support.SUPPORT),
        "gate": {"left": 10, "right": 10, "window": 21,
            "pivot_rule": "centre_equals_window_extreme_ties_allowed",
            "pine_builtin_tie_parity_verified": False,
            "unchanged_level_price_required": True, "persistent": True,
            "decision": "own_K1_close_after_state_update", "gap_resets_state_and_levels": True,
            "no_break_on_k1_requirement": True, "own_controls": True,
            "unknown_is_abstention": False, "length_search": False},
        "fixed_execution": {"policy": "15m_native40_failed_confirm2",
            "cost_fraction": .002, "max_hours": 72, "stop": "K1_extreme",
            "unchanged_cached_episodes": True, "new_intrabar_replays": 0,
            "serial_recomputed_per_arm": True},
        "outcome_read_rule": "only_after713context_freeze_and_all_support_gates_pass",
        "inference": {"unit": "paired_original_intention", "clusters": "calendar_month",
            "draws": 9999, "seed": 20260906, "alpha": .01,
            "joint_required": ["case_delta", "excess_delta"],
            "reused_development_not_confirmatory": True},
        "matching_coverage_required": .9, "holdout_consumed": False,
        "training_eligible": False, "production_eligible": False,
    }


def verify_config(config, base):
    if config != frozen_config():
        raise ValueError("Frozen V20 contract changed")
    e = base["execution"]
    if (base["development_folds"] != FOLDS or e["max_hours"] != 72 or
            e["cost_fraction"] != .002 or e["stop_first"] is not True):
        raise ValueError("Only original2023--2024,72h,K1-stop,20bp permitted")


def support_view(context):
    """Reuse only outcome-free counting functions with an explicit column alias."""
    if support.GATE_COLUMN in context:
        raise ValueError("A prior-breakout gate cannot be mixed into V20")
    return context.rename(columns={"structure_gate_state": support.GATE_COLUMN})


def audit_population(raw, mothers, controls, assignments):
    validate_population(mothers, controls, assignments)
    requests = pd.concat([mothers.assign(population="case"), controls.assign(population="control")], ignore_index=True)
    context = add_structure_context(requests, raw)
    pd.testing.assert_frame_equal(context[requests.columns], requests)
    view = support_view(context)
    values, gates = support.support_gates(view)
    counts = support.support_counts(view)
    triples = support.matched_support(view, assignments)
    # Persistent state needs the entire fixed causal history, not per-request
    # short snippets; later raw bars are removed even from exported evidence.
    end = pd.to_datetime(requests.decision_time, utc=True).max()
    hourly = resample_complete(raw.loc[raw.open_time.lt(end)], 60)
    trace = add_hourly_structure_state(hourly)
    return {"entry_context": context, "counts": counts, "matched_support": triples,
        "hourly_trace": trace}, {
        "population": {p: support.count_states(view.loc[view.population.eq(p)]) for p in ("case", "control")},
        "support_values": values, "support_gates": gates, "support_pass": all(gates.values()),
        "matching": {"assigned": len(triples), "unassigned": 97,
            "coverage": 154/251, "required": .9, "pass": False},
    }


def read_outcomes_after_freeze(results, summary, context, marker="outcomes_started.json"):
    """No outcome file, including its hash, is touched on a failed support run."""
    if not summary["support_pass"] or not all(summary["support_gates"].values()):
        raise ValueError("Insufficient support prohibits outcome access")
    freeze = json.loads((results / "context_frozen.json").read_text())
    if freeze["requests"] != 713 or len(context) != 713:
        raise ValueError("All713 own contexts must be frozen before outcomes")
    for name, expected in freeze["output_hashes"].items():
        if digest(results/name) != expected:
            raise ValueError("Frozen feature evidence changed")
    marker_path = results/marker
    if marker_path.exists():
        raise ValueError("Preserve earlier outcome-access attempt")
    write_json(marker_path, {"at": pd.Timestamp.now(tz="UTC"),
        "context_frozen_sha256": digest(results/"context_frozen.json"),
        "cached_fixed_episode_accounting_only": True, "intrabar_replays": 0})
    for name, expected in OUTCOME_INPUTS.items():
        if digest(ROOT/OUTCOMES/name) != expected:
            raise ValueError("Frozen V18 outcome changed: "+name)
    cases, controls = [read_parent_frame(ROOT/OUTCOMES/(p+"_episodes.csv.gz")) for p in ("case", "control")]
    if len(cases) != 251 or len(controls) != 462:
        raise ValueError("Original fixed episode population changed")
    tables, economics = evaluate_cached(cases, controls, context)
    assert_saved_parity(read_parent_frame(ROOT/OUTCOMES/"matched.csv"), tables["baseline_matched"])
    assert_saved_parity(read_parent_frame(ROOT/OUTCOMES/"single_pending.csv.gz"), tables["baseline_case_serial"])
    economics["baseline_saved_matching_and_serial_parity"] = True
    return tables, economics


def run():
    config_path, plan_path = EXPERIMENT/"config.json", EXPERIMENT/"PROJECT_PLAN.md"
    base_path = ROOT/BASE_CONFIG
    if digest(base_path) != BASE_SHA256 or digest(ROOT/PINE) != PINE_SHA256:
        raise ValueError("Source/base contract hash changed")
    config, base = json.loads(config_path.read_text()), json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, base_path, plan_path])
    for name, expected in INPUTS.items():
        if digest(ROOT/PARENT/name) != expected:
            raise ValueError("Frozen request input changed: "+name)
    mothers, controls, assignments = [read_table(ROOT/PARENT/n) for n in
        ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv")]
    validate_population(mothers, controls, assignments)
    results = EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve previous attempt; results already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    try:
        raw, receipt = load_source(base, utc(PHASE_END))
        if raw.open_time.max() >= utc(PHASE_END):
            raise ValueError("Later price prefix is forbidden")
        tables, summary = audit_population(raw, mothers, controls, assignments)
        for name, frame in tables.items():
            write_csv(results/(name+".csv.gz"), frame)
        hashes = {p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))}
        write_json(results/"context_frozen.json", {"at": pd.Timestamp.now(tz="UTC"),
            "requests": len(tables["entry_context"]), "output_hashes": hashes,
            "outcomes_read": False, "source_receipt": receipt})
        if summary["support_pass"]:
            outcome_tables, economics = read_outcomes_after_freeze(results, summary, tables["entry_context"])
            for name, frame in outcome_tables.items():
                write_csv(results/(name+".csv.gz"), frame)
            summary.update(economics=economics, outcomes_read=True,
                status="fixed_episode_gate_comparison_not_independent_validation")
        else:
            summary.update(outcomes_read=False, status="insufficient_support_no_outcomes")
        summary.update(experiment_id=EXPERIMENT_ID, generated_at=pd.Timestamp.now(tz="UTC"),
            source_receipt=receipt, sources=sources, config_sha256=digest(config_path),
            output_hashes={p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
            holdout_consumed=False, new_intrabar_replays=0,
            training_eligible=False, production_eligible=False,
            independent_validation=False, overall_goal_achieved=False)
        write_json(results/"summary.json", summary)
    except Exception as error:
        write_json(results/"failure.json", {"at": pd.Timestamp.now(tz="UTC"),
            "status": "failed_not_evidence", "error_type": type(error).__name__, "message": str(error)})
        raise
    print(json.dumps({k: summary[k] for k in ("status", "population", "support_values", "support_gates", "outcomes_read")}))


def resume_frozen_accounting():
    """Resume a failed serialization/accounting attempt without market access.

    Preserve the original failure and access timestamps. Only the immutable
   713 frozen feature rows are reused; no threshold/source/state recalculation.
    This entrypoint refuses an existing summary or economic output file.
    """
    results = EXPERIMENT/"results"
    if not (results/"failure.json").exists() or (results/"summary.json").exists():
        raise ValueError("Only an incomplete failed frozen attempt can resume")
    config_path, plan_path = EXPERIMENT/"config.json", EXPERIMENT/"PROJECT_PLAN.md"
    config = json.loads(config_path.read_text())
    base_path = ROOT/BASE_CONFIG
    if digest(base_path) != BASE_SHA256:
        raise ValueError("Frozen base changed")
    verify_config(config, json.loads(base_path.read_text()))
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, base_path, plan_path])
    started = json.loads((results/"started.json").read_text())
    original_sources = {item["path"]: item["sha256"] for item in started["sources"]}
    for path in ("yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_structure.py"):
        if original_sources[path] != digest(ROOT/path):
            raise ValueError("Feature generator changed since frozen attempt")
    for name, expected in INPUTS.items():
        if digest(ROOT/PARENT/name) != expected:
            raise ValueError("Original request input changed")
    freeze = json.loads((results/"context_frozen.json").read_text())
    for name, expected in freeze["output_hashes"].items():
        if digest(results/name) != expected:
            raise ValueError("Original frozen feature evidence changed")
    context = read_table(results/"entry_context.csv.gz")
    view = support_view(context)
    values, gates = support.support_gates(view)
    summary = {"support_pass": all(gates.values()), "support_values": values, "support_gates": gates,
        "population": {p: support.count_states(view.loc[view.population.eq(p)]) for p in ("case", "control")},
        "matching": {"assigned": 154, "unassigned": 97, "coverage": 154/251, "required": .9, "pass": False}}
    tables, economics = read_outcomes_after_freeze(results, summary, context, "outcomes_resumed_1.json")
    if any((results/(name+".csv.gz")).exists() for name in tables):
        raise ValueError("Cannot overwrite any earlier economic output")
    for name, frame in tables.items():
        write_csv(results/(name+".csv.gz"), frame)
    summary.update(economics=economics, outcomes_read=True,
        status="fixed_episode_gate_comparison_not_independent_validation", experiment_id=EXPERIMENT_ID,
        generated_at=pd.Timestamp.now(tz="UTC"), sources=started["sources"], resume_sources=sources,
        resume_builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        source_receipt=freeze["source_receipt"], config_sha256=digest(config_path),
        output_hashes={p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
        holdout_consumed=False, new_intrabar_replays=0, training_eligible=False, production_eligible=False,
        independent_validation=False, overall_goal_achieved=False, preserved_first_failure="failure.json",
        frozen_features_recomputed=False)
    write_json(results/"summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("status", "population", "support_values", "support_gates", "outcomes_read")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-frozen-accounting", action="store_true")
    args = parser.parse_args()
    resume_frozen_accounting() if args.resume_frozen_accounting else run()
