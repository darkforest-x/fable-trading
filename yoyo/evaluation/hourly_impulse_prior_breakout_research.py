"""V14 outcome-free support preflight for strict prior20 hourly breakouts.

Only completed OHLCV in [K1 open - 20h, K1 close) enters each request's
context. No V5 outcome files, simulator, MFE, PNL or return ranking is read or
called. The original V4 population and three-control assignments stay fixed.
The source loader materializes only the already-reused 2023--2024 prefix;
archive timestamps and byte hashes are not price/outcome calculations.

Source semantics: pandas 2.3.3 shift excludes the current observation; a
20-observation rolling window still requires explicit clock-contiguity checks.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.shift.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html
"""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pandas as pd

from yoyo.data.hourly_impulse import resample_complete
from yoyo.data.hourly_impulse_prior_breakout import add_prior_breakout_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_research import ROOT, digest, load_source, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_support_research import (
    BASE_CONFIG, BASE_SHA256, PARENT, INPUTS, FOLDS, EXPECTED,
    read_table, validate_population,
)

EXPERIMENT_ID = "exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
STATES = ("accepted", "abstain", "unknown")
GATE_COLUMN = "prior_breakout_gate_state"
SUPPORT = {"minimum_events": 80, "minimum_per_fold": 12,
           "minimum_active_months": 12, "minimum_months_per_fold": 3}
SOURCES = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_prior_breakout.py",
    "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_support_research.py",
    "yoyo/evaluation/hourly_impulse_prior_breakout_research.py",
    "tests/test_hourly_impulse_prior_breakout.py",
    "tests/test_hourly_impulse_prior_breakout_research.py",
]


def frozen_config():
    return {"experiment_id": EXPERIMENT_ID, "base_config": BASE_CONFIG,
        "base_config_sha256": BASE_SHA256, "parent_results": PARENT,
        "inputs": deepcopy(INPUTS), "development_folds": deepcopy(FOLDS),
        "expected": deepcopy(EXPECTED), "support": deepcopy(SUPPORT),
        "gate": {"prior_hours": 20, "exclude_k1": True, "require_contiguous": True,
            "long": "own_signal_close > prior_high20", "short": "own_signal_close < prior_low20",
            "equal_boundary": "abstain", "missing_context": "unknown",
            "control_gate": "own_context_no_transfer", "decision": "K1_close",
            "extra_ma_slope_or_4h_gate": False, "length_grid": False},
        "inherited_execution_not_run": {"cost_fraction": .002, "max_hours": 72,
            "stop": "K1_extreme", "exit": "5m_native40_true_aligned_to_opposite"},
        "matching_coverage": {"actual": 154/251, "required": .9, "pass": False},
        "no_outcome_entry_point": True, "holdout_consumed": False,
        "training_eligible": False, "production_eligible": False}


def verify_config(config, base):
    if config != frozen_config():
        raise ValueError("Frozen V14 support-only contract changed")
    if base["development_folds"] != FOLDS:
        raise ValueError("Only reused 2023--2024 development is permitted")
    e = base["execution"]
    if e["cost_fraction"] != .002 or e["max_hours"] != 72 or e["stop_first"] is not True:
        raise ValueError("Inherited execution cannot change during support preflight")


def count_states(frame):
    states = frame[GATE_COLUMN]
    if states.isna().any() or not states.isin(STATES).all():
        raise ValueError("Every opportunity requires an explicit support state")
    return {"total": len(frame), **states.value_counts().reindex(STATES, fill_value=0).to_dict()}


def support_counts(context):
    """Full population and zero-filled fixed fold/month denominators, no outcomes."""
    result = []
    months = pd.date_range("2023-01-01", "2024-12-01", freq="MS", tz="UTC").strftime("%Y-%m")
    for population in ("case", "control"):
        rows = context.loc[context.population.eq(population)].copy()
        rows["month"] = pd.to_datetime(rows.decision_time, utc=True).dt.strftime("%Y-%m")
        dimensions = {"all": ["all"], "fold": [f[0] for f in FOLDS],
                      "direction": ["1", "-1"], "month": list(months)}
        for dimension, keys in dimensions.items():
            for key in keys:
                subset = rows if dimension == "all" else rows.loc[rows[dimension].astype(str).eq(key)]
                counts = count_states(subset)
                result.append({"population": population, "dimension": dimension, "key": key,
                    **counts, "accepted_rate": counts["accepted"]/counts["total"] if counts["total"] else None})
    return pd.DataFrame(result)


def support_gates(context):
    cases = context.loc[context.population.eq("case") & context[GATE_COLUMN].eq("accepted")].copy()
    cases["month"] = pd.to_datetime(cases.decision_time, utc=True).dt.strftime("%Y-%m")
    fold_count = cases.groupby("fold").size().reindex([f[0] for f in FOLDS], fill_value=0)
    fold_months = cases.groupby("fold").month.nunique().reindex(fold_count.index, fill_value=0)
    values = {"events": len(cases), "minimum_fold_events": int(fold_count.min()),
              "active_months": cases.month.nunique(), "minimum_fold_months": int(fold_months.min())}
    gates = {"minimum_events": values["events"] >= SUPPORT["minimum_events"],
        "minimum_per_fold": values["minimum_fold_events"] >= SUPPORT["minimum_per_fold"],
        "minimum_active_months": values["active_months"] >= SUPPORT["minimum_active_months"],
        "minimum_months_per_fold": values["minimum_fold_months"] >= SUPPORT["minimum_months_per_fold"]}
    return values, gates


def matched_support(context, assignments):
    """All original154 triples remain, regardless of gate outcomes; no rematch."""
    lookup = context.set_index("event_id")
    controls = context.loc[context.population.eq("control")]
    rows = []
    for case in assignments.loc[assignments.match_status.eq("matched")].itertuples():
        group = controls.loc[controls.parent_event_id.eq(case.event_id)]
        if len(group) != 3:
            raise ValueError("Original three-control group changed")
        rows.append({"event_id": case.event_id, "fold": case.fold,
            "case_state": lookup.loc[case.event_id, GATE_COLUMN],
            "control_ids": "|".join(sorted(group.event_id)),
            **{"control_"+k: v for k, v in count_states(group).items()},
            "all_known": lookup.loc[case.event_id, GATE_COLUMN] != "unknown" and
                         not group[GATE_COLUMN].eq("unknown").any()})
    return pd.DataFrame(rows)


def source_windows(context, raw):
    """Export per-request entry-known hours for independent extrema reconstruction.

    Uses only raw OHLCV grouped to complete60m bars. Source rows are duplicated
    by request intentionally; event_id/role/open_time is the unique grain.
    Missing hours remain missing, not forward-filled. Never export later bars.
    """
    hourly = resample_complete(raw, 60)
    pieces = []
    for request in context.itertuples():
        start = pd.Timestamp(request.signal_time)
        chosen = hourly.loc[hourly.open_time.ge(start-pd.Timedelta(hours=20)) & hourly.open_time.le(start),
            ["open_time", "open", "high", "low", "close", "segment_id"]].copy()
        chosen["role"] = chosen.open_time.eq(start).map({True: "k1", False: "prior"})
        chosen["event_id"], chosen["population"] = request.event_id, request.population
        pieces.append(chosen)
    return pd.concat(pieces, ignore_index=True)


def audit_population(raw, mothers, controls, assignments):
    validate_population(mothers, controls, assignments)
    requests = pd.concat([mothers.assign(population="case"), controls.assign(population="control")], ignore_index=True)
    context = add_prior_breakout_context(requests, raw)
    if len(context) != 713 or not context.event_id.equals(requests.event_id):
        raise ValueError("Original request order/population changed")
    pd.testing.assert_frame_equal(context[requests.columns], requests)
    counts = support_counts(context)
    matched = matched_support(context, assignments)
    values, gates = support_gates(context)
    passed = all(gates.values())
    summary = {"experiment_id": EXPERIMENT_ID,
        "status": "support_pass_requires_separate_replay" if passed else "insufficient_support_no_outcomes",
        "population": {p: count_states(context.loc[context.population.eq(p)]) for p in ("case", "control")},
        "support_values": values, "support_gates": gates, "support_pass": passed,
        "matching": {"matched": len(matched), "unmatched": 97, "all_known": int(matched.all_known.sum()),
            "coverage": 154/251, "required_coverage": .9, "coverage_pass": False},
        "gate_hours": 20, "outcomes_read_or_computed": False, "outcome_replays": 0,
        "profitability_test": False, "holdout_consumed": False,
        "training_eligible": False, "production_eligible": False,
        "limitation": "Support counts are not profitability, statistical power, fresh validation or production evidence."}
    return {"entry_context": context, "counts": counts, "matched_support": matched,
            "prior_hourly_rows": source_windows(context, raw)}, summary


def run():
    config_path, plan_path = EXPERIMENT/"config.json", EXPERIMENT/"PROJECT_PLAN.md"
    base_path = ROOT/BASE_CONFIG
    if digest(base_path) != BASE_SHA256:
        raise ValueError("Base configuration hash changed")
    config, base = json.loads(config_path.read_text()), json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, base_path, plan_path])
    parent = ROOT/PARENT
    for name, expected in INPUTS.items():
        if digest(parent/name) != expected:
            raise ValueError("Frozen pre-entry input changed: "+name)
    mothers, controls, assignments = [read_table(parent/n) for n in
        ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv")]
    validate_population(mothers, controls, assignments)
    results = EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve previous attempt: results already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"),
        "sources": sources, "inputs": INPUTS,
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    try:
        raw, source_receipt = load_source(base, utc("2025-01-01"))
        tables, summary = audit_population(raw, mothers, controls, assignments)
        for name, frame in tables.items():
            write_csv(results/(name+".csv"), frame)
        hashes = {p.name: digest(p) for p in sorted(results.glob("*.csv"))}
        write_json(results/"support_frozen.json", {"at": pd.Timestamp.now(tz="UTC"),
            "output_hashes": hashes, "outcome_replays": 0})
        summary.update(source_receipt=source_receipt, source_receipts=sources,
            config_sha256=digest(config_path), input_hashes=INPUTS,
            output_hashes=hashes, generated_at=pd.Timestamp.now(tz="UTC"))
        write_json(results/"summary.json", summary)
    except Exception as error:
        write_json(results/"failure.json", {"at": pd.Timestamp.now(tz="UTC"),
            "status": "failed_not_support_evidence", "error_type": type(error).__name__,
            "message": str(error), "outcome_replays": 0})
        raise
    print(json.dumps({k: summary[k] for k in ("status", "population", "support_values", "support_gates")}))


if __name__ == "__main__":
    run()
