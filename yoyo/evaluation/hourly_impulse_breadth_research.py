"""V21: fixed external rank-pressure gate, not a second BTC MA vote.

ChartPrime Multi Asset Histogram (MPL2, KkoxM97D) supplies the HL2 rank50
formula; four predeclared external assets and one-hour availability lag are
explicit adaptations, not original-script parity or live-feed verification.
All713 own contexts freeze before V18 cached exits can be accessed. No new
intrabar simulation, execution-price source substitution, or holdout access.

The pandas2.3.3 loader first parses timestamps only, validates byte receipts,
then uses explicit skiprows/nrows/usecols to materialize only the bounded
2022-12-29..2024 external OHLCV window. Never parse a price chunk then filter.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html
https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/
"""
from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS
from yoyo.data.hourly_impulse_breadth import add_breadth_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_research import ROOT, digest, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_support_research import (
    BASE_CONFIG, BASE_SHA256, PARENT, INPUTS, FOLDS, EXPECTED,
    read_table, validate_population,
)
from yoyo.evaluation import hourly_impulse_prior_breakout_research as support
from yoyo.evaluation.hourly_impulse_structure_research import OUTCOMES, OUTCOME_INPUTS
from yoyo.evaluation.hourly_impulse_failed_launch_research import read_parent_frame
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity
from yoyo.evaluation.hourly_impulse_breadth_accounting import evaluate_cached

EXPERIMENT_ID = "exp-btcusdtp-1h-external-breadth-preholdout-20260906-v21"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
PHASE_END = "2025-01-01"
WARMUP_START = "2022-12-29"
ARCHIVE = "data/kline_preholdout_binance_um5m"
PINE = "experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/KkoxM97D.pine"
PINE_SHA256 = "58d49892627a886094b269c7b9d7ac15ae9ba1c0844696fc0cd85ab7856b3ae5"
EXTERNAL = {
    "ETHUSDT": {"file": "binance_um_ETHUSDT_5m_665856.csv", "rows": 665856,
        "sha256": "8041770149cff3551f84966b1b5f3641f2f731dc3ae7c7d0bfeaf7b24dcd64e8",
        "audit_sha256": "7f58ccf84648c44a1b7f0b99f8823d663765f94969ead2f903d233114588b2c9"},
    "SOLUSDT": {"file": "binance_um_SOLUSDT_5m_590316.csv", "rows": 590316,
        "sha256": "87a76aa5c36208d862a29016c399d9124365dc93e7cdbd9799e14e2dba8e1165",
        "audit_sha256": "1af0e67261c9328a7ff75b204470eb6c43d1cafd4bd5142dba9732d51a6242f0"},
    "BNBUSDT": {"file": "binance_um_BNBUSDT_5m_654240.csv", "rows": 654240,
        "sha256": "1cb88e0dc3f82b1176e13ff8a9efeca74c282a55fb4e1adcd6a41141bedea2a8",
        "audit_sha256": "14b14689e0fd89e51e14c2da7354f17b38157c9d2e9dbcf81deec621e77b5af4"},
    "XRPUSDT": {"file": "binance_um_XRPUSDT_5m_662876.csv", "rows": 662876,
        "sha256": "7bebe067d4dc6e7169bdf30411472178a828aff16c1f527365582e98e69a1f94",
        "audit_sha256": "aea65c8e187678c09cff2a22d841522f6a12e84a0e1524622b67d64a12edbc7e"},
}
SOURCES = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_breadth.py",
    "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_support_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_prior_breakout_research.py",
    "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_management_research.py",
    "yoyo/evaluation/hourly_impulse_failed_launch_research.py",
    "yoyo/evaluation/hourly_impulse_structure_research.py",
    "yoyo/evaluation/hourly_impulse_structure_accounting.py",
    "yoyo/evaluation/hourly_impulse_breadth_research.py",
    "yoyo/evaluation/hourly_impulse_breadth_accounting.py",
    "tests/test_hourly_impulse_breadth.py",
    "tests/test_hourly_impulse_breadth_accounting.py",
    "tests/test_hourly_impulse_breadth_research.py", PINE,
]


def frozen_config():
    return {
        "experiment_id": EXPERIMENT_ID, "base_config": BASE_CONFIG,
        "base_config_sha256": BASE_SHA256, "parent_requests": PARENT,
        "request_inputs": deepcopy(INPUTS), "outcomes": OUTCOMES,
        "outcome_inputs": deepcopy(OUTCOME_INPUTS), "development_folds": deepcopy(FOLDS),
        "phase_end_exclusive": PHASE_END, "warmup_start": WARMUP_START,
        "expected": deepcopy(EXPECTED), "support": deepcopy(support.SUPPORT),
        "external_sources": deepcopy(EXTERNAL), "archive": ARCHIVE,
        "gate": {"rank_length": 50, "source": "native_complete_1h_HL2",
            "comparison": "current_hl2_greater_or_equal_lag_adds_one_else_minus_one",
            "history": "51_consecutive_complete_hours_per_asset",
            "universe": list(EXTERNAL), "weights": "equal_mean_scores_divided_by50",
            "cutoff": "own_K1_open", "lag_hours_before_entry": 1,
            "join": "exact_last_hour_available_at_equals_own_signal_time",
            "accept": "own_direction_times_breadth_score_strictly_positive",
            "zero": "known_abstain", "missing_any_asset": "unknown_NaN",
            "forward_fill": False, "nz_price_fill": False, "length_search": False,
            "extra_structure_ma_volume_gate": False, "own_controls": True,
            "pine_runtime_parity_verified": False, "live_feed_latency_verified": False},
        "fixed_execution": {"policy": "15m_native40_failed_confirm2",
            "cost_fraction": .002, "max_hours": 72, "stop": "K1_extreme",
            "execution_source": "original_OKX_BTC_cached_V18_episodes",
            "new_intrabar_replays": 0, "serial_recomputed_per_arm": True},
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
        raise ValueError("Frozen V21 contract changed")
    e = base["execution"]
    if (base["development_folds"] != FOLDS or e["max_hours"] != 72 or
            e["cost_fraction"] != .002 or e["stop_first"] is not True):
        raise ValueError("Original2023--2024,72h,K1-stop,20bp must remain")


def load_external(symbol, spec, end, root=ROOT):
    """Read only fixed source metadata then explicitly bounded OHLCV rows.

    `end` is at most the latest request's K1 OPEN, so not even later intraday
    external prices are materialized. Full-file timestamp/byte inspection is
    not external-price evaluation. No raw CSV content is printed or repaired.
    """
    end = utc(end)
    if end > utc(PHASE_END) or end <= utc(WARMUP_START):
        raise ValueError("External phase bound invalid")
    path, audit_path = root/ARCHIVE/"series"/spec["file"], root/ARCHIVE/"audits"/(symbol+".json")
    if digest(audit_path) != spec["audit_sha256"]:
        raise ValueError("External audit hash changed: "+symbol)
    audit = json.loads(audit_path.read_text())
    if (audit.get("holdout_ohlcv_rows_materialized") != 0 or
            audit.get("status") != "complete" or audit.get("symbol") != symbol or
            audit.get("output_sha256") != spec["sha256"] or
            audit.get("rows") != spec["rows"] or utc(audit["last_time"]) >= utc("2026-05-04")):
        raise ValueError("External receipt contract changed: "+symbol)
    stamps = pd.read_csv(path, usecols=["open_time"])["open_time"]
    if pd.api.types.is_numeric_dtype(stamps.dtype):
        raise ValueError("External timestamps must be explicit UTC text")
    if stamps.isna().any() or any(pd.Timestamp(t).tzinfo is None for t in stamps):
        raise ValueError("External timestamps must carry timezones")
    stamps = pd.to_datetime(stamps, utc=True, errors="raise")
    if (len(stamps) != spec["rows"] or not stamps.is_monotonic_increasing or
            stamps.duplicated().any() or not stamps.eq(stamps.dt.floor("5min")).all() or
            stamps.min() != utc(audit["first_time"]) or stamps.max() != utc(audit["last_time"]) or
            stamps.max() >= utc("2026-05-01")):
        raise ValueError("External physical timestamp contract changed: "+symbol)
    if digest(path) != spec["sha256"]:
        raise ValueError("External price archive hash changed: "+symbol)
    lo, hi = int(stamps.lt(utc(WARMUP_START)).sum()), int(stamps.lt(end).sum())
    if hi <= lo:
        raise ValueError("No external prefix available")
    raw = pd.read_csv(path, usecols=BAR_COLUMNS, skiprows=range(1, lo+1), nrows=hi-lo)
    raw["open_time"] = pd.to_datetime(raw.open_time, utc=True, errors="raise")
    if len(raw) != hi-lo or not raw.open_time.reset_index(drop=True).equals(stamps.iloc[lo:hi].reset_index(drop=True)):
        raise ValueError("Bounded external read changed timestamp rows")
    return raw, {"symbol": symbol, "path": str(path.relative_to(root)),
        "sha256": spec["sha256"], "audit_sha256": spec["audit_sha256"],
        "physical_rows": len(stamps), "skipped_before_warmup_rows": lo,
        "price_rows_materialized": len(raw), "first_price_time": raw.open_time.min(),
        "last_price_time": raw.open_time.max(), "price_end_exclusive": end,
        "price_rows_2025_plus_materialized": 0, "holdout_ohlcv_rows_materialized": 0,
        "execution_source_unchanged": True, "timestamp_and_hash_preflight": True}


def audit_population(raw_by_symbol, mothers, controls, assignments):
    validate_population(mothers, controls, assignments)
    requests = pd.concat([mothers.assign(population="case"), controls.assign(population="control")], ignore_index=True)
    context, trace = add_breadth_context(requests, raw_by_symbol)
    pd.testing.assert_frame_equal(context[requests.columns], requests)
    if support.GATE_COLUMN in context:
        raise ValueError("No prior-breakout stacking")
    view = context.rename(columns={"breadth_gate_state": support.GATE_COLUMN})
    values, gates = support.support_gates(view)
    return {"entry_context": context, "counts": support.support_counts(view),
        "matched_support": support.matched_support(view, assignments), "external_hourly_trace": trace}, {
        "population": {p: support.count_states(view.loc[view.population.eq(p)]) for p in ("case", "control")},
        "support_values": values, "support_gates": gates, "support_pass": all(gates.values()),
        "matching": {"assigned": 154, "unassigned": 97, "coverage": 154/251, "required": .9, "pass": False}}


def read_outcomes_after_freeze(results, summary, context):
    if not summary["support_pass"] or not all(summary["support_gates"].values()):
        raise ValueError("Insufficient support prohibits outcome access")
    frozen = json.loads((results/"context_frozen.json").read_text())
    if frozen["requests"] != 713 or len(context) != 713:
        raise ValueError("All713 own contexts must freeze")
    for name, sha in frozen["output_hashes"].items():
        if digest(results/name) != sha:
            raise ValueError("Frozen context bytes changed")
    marker = results/"outcomes_started.json"
    if marker.exists():
        raise ValueError("Preserve previous outcome access")
    write_json(marker, {"at": pd.Timestamp.now(tz="UTC"),
        "frozen_context_sha256": digest(results/"context_frozen.json"), "new_intrabar_replays": 0})
    for name, sha in OUTCOME_INPUTS.items():
        if digest(ROOT/OUTCOMES/name) != sha:
            raise ValueError("Frozen V18 outcome bytes changed")
    cases, controls = [read_parent_frame(ROOT/OUTCOMES/(p+"_episodes.csv.gz")) for p in ("case", "control")]
    tables, economics = evaluate_cached(cases, controls, context)
    assert_saved_parity(read_parent_frame(ROOT/OUTCOMES/"matched.csv"), tables["baseline_matched"])
    assert_saved_parity(read_parent_frame(ROOT/OUTCOMES/"single_pending.csv.gz"), tables["baseline_case_serial"])
    economics["baseline_saved_matching_and_serial_parity"] = True
    return tables, economics


def run():
    config_path, plan_path, base_path = EXPERIMENT/"config.json", EXPERIMENT/"PROJECT_PLAN.md", ROOT/BASE_CONFIG
    if digest(base_path) != BASE_SHA256 or digest(ROOT/PINE) != PINE_SHA256:
        raise ValueError("Frozen source/base contract changed")
    config, base = json.loads(config_path.read_text()), json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, plan_path, base_path])
    for name, sha in INPUTS.items():
        if digest(ROOT/PARENT/name) != sha:
            raise ValueError("Frozen request input changed")
    mothers, controls, assignments = [read_table(ROOT/PARENT/n) for n in
        ("original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv")]
    validate_population(mothers, controls, assignments)
    results = EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve previous run; results already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    try:
        end = max(pd.to_datetime(mothers.signal_time, utc=True).max(), pd.to_datetime(controls.signal_time, utc=True).max())
        raw, receipts = {}, {}
        for symbol, spec in EXTERNAL.items():
            raw[symbol], receipts[symbol] = load_external(symbol, spec, end)
        tables, summary = audit_population(raw, mothers, controls, assignments)
        for name, frame in tables.items():
            write_csv(results/(name+".csv.gz"), frame)
        write_json(results/"context_frozen.json", {"at": pd.Timestamp.now(tz="UTC"),
            "requests": len(tables["entry_context"]), "outcomes_read": False,
            "output_hashes": {p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
            "external_receipts": receipts})
        if summary["support_pass"]:
            economics_tables, economics = read_outcomes_after_freeze(results, summary, tables["entry_context"])
            for name, frame in economics_tables.items():
                write_csv(results/(name+".csv.gz"), frame)
            summary.update(economics=economics, outcomes_read=True,
                status="fixed_episode_gate_comparison_not_independent_validation")
        else:
            summary.update(outcomes_read=False, status="insufficient_support_no_outcomes")
        summary.update(experiment_id=EXPERIMENT_ID, generated_at=pd.Timestamp.now(tz="UTC"),
            external_receipts=receipts, sources=sources, config_sha256=digest(config_path),
            output_hashes={p.name: digest(p) for p in sorted(results.glob("*.csv.gz"))},
            holdout_consumed=False, new_intrabar_replays=0, independent_validation=False,
            training_eligible=False, production_eligible=False, overall_goal_achieved=False)
        write_json(results/"summary.json", summary)
    except Exception as error:
        write_json(results/"failure.json", {"at": pd.Timestamp.now(tz="UTC"),
            "status": "failed_not_evidence", "error_type": type(error).__name__, "message": str(error)})
        raise
    print(json.dumps({k: summary[k] for k in ("status", "population", "support_values", "support_gates", "outcomes_read")}))


if __name__ == "__main__":
    run()
