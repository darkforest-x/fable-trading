"""Preregistered first frozen-source release, not another static MA threshold.

Entry features: eight complete source hours and first subsequent release within
eight hours, all known at their own close. Matching uses current completed ATR,
prior720 shifted terciles and exactly completed native5m colour; only real open
is read at execution. All source intentions remain, while beta controls are
assigned to entry requests, never to favourable future outcomes. No audit phase.
StageA support gates precede every P/L call. Actual execution, morphology and
full costs are unchanged except the explicitly registered entry-family change.

NumPy2.0 month draws: https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.data.hourly_impulse_source_zone import build_source_zone_requests
from yoyo.evaluation.hourly_impulse_context_research import (
    committed_sources, development_gates, month_support, simulate,
)
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import (
    describe, direct_requests, episode_ledger, matched_episodes, single_pending_ledger,
)
from yoyo.evaluation.hourly_impulse_research import (
    ROOT, Study, digest, metrics, utc, write_csv, write_json,
)
from yoyo.evaluation.hourly_impulse_source_matching import (
    assign_source_controls, build_source_matching_frame,
)
from yoyo.evaluation.hourly_impulse_transition_research import read_frame, state_diagnostics


EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-frozen-source-preholdout-20260906-v7"
ZONE_SPEC = {
    "source_hours": 8, "prior_hours": 4, "inside_hours": 4,
    "strict_envelope": True, "first_release_only": True,
    "wait_hours": 8, "arm_embargo_hours": 80,
    "rearm": "only eight complete bars opening at or after causal source terminal",
    "shape": "large_or_engulf", "body_ratio_min": .65, "range_atr_min": 1.,
    "engulf_range_atr_min": .65, "close_location_min": .7,
    "require_ma_cross": False, "require_hourly_ma_side": False,
}
POLICY = {"id": "5m_native40", "management_minutes": 5, "ma_kind": "SMA",
          "ma_length": 40, "exit_mode": "transition_colour", "confirmations": 1}
SELECTION = {"minimum_events": 80, "minimum_per_fold": 12, "positive_folds": 4,
             "minimum_profit_factor": 1.1, "minimum_active_months": 12,
             "minimum_months_per_fold": 3, "matched_coverage": .9}
MATCHING = {"count": 3, "seed": 20260906,
            "keys": ["symbol", "fold", "month", "utc_6h_bucket", "vol_bucket", "known_5m_colour"],
            "control_time_reuse": False, "fallback": False}
SOURCE_PATHS = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_colour_context.py",
    "yoyo/data/hourly_impulse_source_zone.py",
    "yoyo/layers/l3_backtest/hourly_impulse.py",
    "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_k2_matching.py",
    "yoyo/evaluation/hourly_impulse_transition_research.py",
    "yoyo/evaluation/hourly_impulse_diagnostics.py",
    "yoyo/evaluation/hourly_impulse_source_matching.py",
    "yoyo/evaluation/hourly_impulse_source_research.py",
    "tests/test_hourly_impulse_source_zone.py",
    "tests/test_hourly_impulse_source_matching.py",
    "tests/test_hourly_impulse_source_research.py",
]
KNOWN_SOURCE_NONENTRY = {"first_release_unqualified", "expired_no_release"}


def verify_config(config: dict, base: dict) -> None:
    """Reject silent alternative rules, economics or output permissions."""
    if config["zone"] != ZONE_SPEC or config["policy"] != POLICY:
        raise RuntimeError("Frozen entry-family/exit contract changed")
    if config["selection"] != SELECTION or config["matching"] != MATCHING:
        raise RuntimeError("Preregistered support/economic/matching gates changed")
    if not config["stage_a_requires_complete_zones"] or not config["no_audit_entry_point"]:
        raise RuntimeError("Do not bypass source evidence or open an audit")
    if any(config[k] is not False for k in ("holdout_consumed", "training_eligible", "production_eligible")):
        raise RuntimeError("Research only, with zero holdout price rows")
    if base["execution"]["max_hours"] != 72 or base["execution"]["cost_fraction"] != .002:
        raise RuntimeError("Frozen economics changed")
    if config["inference"]["draws"] != 9999 or config["inference"]["seed"] != 20260906 or config["inference"]["p_limit"] != .01:
        raise RuntimeError("Frozen inference changed")


def support_info(entries: pd.DataFrame, zones: pd.DataFrame,
                 assignments: pd.DataFrame, folds: list[str]) -> dict:
    """No outcomes: request support is necessary, not completed-trade success."""
    if entries["event_id"].isna().any() or entries["event_id"].duplicated().any():
        raise ValueError("Request IDs must be finite and unique")
    if assignments["event_id"].duplicated().any() or set(assignments["event_id"]) != set(entries["event_id"]):
        raise ValueError("Every original request needs one allocation status")
    if zones["zone_id"].isna().any() or zones["zone_id"].duplicated().any():
        raise ValueError("Every source needs one identity")
    emitted = zones.loc[zones["status"].eq("request_emitted"), "event_id"]
    if emitted.duplicated().any() or set(emitted) != set(entries["event_id"]):
        raise ValueError("Sources and actual request identities disagree")
    if not entries["fold"].isin(folds).all() or not zones["fold"].isin(folds).all():
        raise ValueError("Unknown fold")
    counts = entries.groupby("fold").size().reindex(folds, fill_value=0)
    times = pd.to_datetime(entries["decision_time"], utc=True)
    months = entries.assign(month=times.dt.strftime("%Y-%m")).groupby("fold")["month"].nunique().reindex(folds, fill_value=0)
    unknown = int(zones["status"].str.startswith("censored_").sum())
    statuses = set(zones["status"])
    if not statuses.issubset(KNOWN_SOURCE_NONENTRY | {"request_emitted", "censored_source_gap", "censored_source_end"}):
        raise ValueError("Unknown source terminal status")
    assigned = assignments["match_status"].eq("matched")
    if not assignments["assigned_controls"].eq(assigned.astype(int)*3).all():
        raise ValueError("All-or-none three-control allocation required")
    info = {"zones": len(zones), "requests": len(entries), "request_counts_by_fold": counts.to_dict(),
            "minimum_fold_requests": int(counts.min()), "matched_requests": int(assigned.sum()),
            "assignment_coverage": assigned.mean() if len(assigned) else 0.,
            "active_request_months": int(times.dt.strftime("%Y-%m").nunique()),
            "request_months_by_fold": months.to_dict(), "unknown_zones": unknown,
            "source_statuses": zones["status"].value_counts().sort_index().to_dict(),
            "pnl_computed": False}
    info["gates"] = {
        "request_count": len(entries) >= 80, "fold_support": counts.min() >= 12,
        "assignment_coverage": info["assignment_coverage"] >= .9,
        "month_support": info["active_request_months"] >= 12 and months.min() >= 3,
        "complete_source_evidence": unknown == 0,
    }
    info["passed"] = all(info["gates"].values())
    return info


def zone_outcome_ledger(zones: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """All source intentions; no random-entry comparison uses these zeros.

    Sources are completed before outcomes, so future exits cannot change source
    release/expiry or rearming. Serial occupancy alone consults realized exits.
    Unknown source/entry/holding paths reserve the maximum arm+80h envelope.
    """
    result = zones.copy()
    if result["zone_id"].duplicated().any():
        raise ValueError("Duplicate source")
    result["entry_event_id"] = result["event_id"]
    result["event_id"] = result["zone_id"]
    result["mother_decision_time"] = pd.to_datetime(result["zone_arm_time"], utc=True)
    result["mother_deadline"] = result["mother_decision_time"] + pd.Timedelta(hours=80)
    result["episode_status"] = result["status"]
    result["episode_net_return"] = np.nan
    result.loc[result["status"].isin(KNOWN_SOURCE_NONENTRY), "episode_net_return"] = 0.
    result["occupied_until"] = pd.to_datetime(result["terminal_time"], utc=True)
    result["completed_trade"] = False
    result["executed"] = False
    for name in ("entry_time", "exit_time"):
        result[name] = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
    if trades["event_id"].duplicated().any():
        raise ValueError("Duplicate trade result")
    emitted = result.loc[result["status"].eq("request_emitted"), "entry_event_id"]
    if emitted.duplicated().any() or set(emitted) != set(trades["event_id"]):
        raise ValueError("Every emitted source needs exactly one execution result")
    lookup = trades.set_index("event_id")
    for index in result.index[result["status"].eq("request_emitted")]:
        trade = lookup.loc[result.at[index, "entry_event_id"]]
        result.at[index, "episode_status"] = trade["outcome"]
        for name in ("entry_time", "exit_time"):
            result.at[index, name] = pd.to_datetime(trade[name], utc=True)
        if trade["closed"] and np.isfinite(trade["net_return"]):
            result.at[index, "episode_net_return"] = trade["net_return"]
            result.at[index, "completed_trade"] = True
            result.at[index, "executed"] = True
            result.at[index, "occupied_until"] = pd.to_datetime(trade["exit_time"], utc=True)
        elif trade["outcome"] == "entry_invalid_risk":
            result.at[index, "episode_net_return"] = 0.
        elif not trade["outcome"].startswith("entry_"):
            result.at[index, "executed"] = True
    result["observed"] = np.isfinite(result["episode_net_return"])
    result.loc[~result["observed"], "occupied_until"] = result.loc[~result["observed"], "mother_deadline"]
    if result["occupied_until"].lt(result["mother_decision_time"]).any():
        raise ValueError("A source cannot terminate before it is armed")
    return result


def evaluate_prepared(study, entries, zones, controls, assignments, config, results, support):
    """The sole outcome gateway; unsupported designs never call simulation."""
    if not support["passed"]:
        return {"status": "rejected_support_no_outcomes", "support": support,
                "outcomes_computed": False, "audit_opened": False}
    write_json(results/"outcomes_started.json", {"at": pd.Timestamp.now(tz="UTC"),
                "support_sha256": digest(results/"support.json"),
                "case_requests_sha256": digest(results/"case_requests.csv.gz"),
                "control_requests_sha256": digest(results/"control_requests.csv.gz")})
    folds = [f[0] for f in study.folds]
    cases = simulate(study, entries, POLICY)
    randoms = simulate(study, controls, POLICY)
    write_csv(results/"case_trades.csv.gz", cases)
    write_csv(results/"control_trades.csv.gz", randoms)
    case_episodes = episode_ledger(entries, direct_requests(entries)[1], cases)
    control_episodes = episode_ledger(controls, direct_requests(controls)[1], randoms)
    write_csv(results/"case_request_outcomes.csv.gz", case_episodes)
    write_csv(results/"control_request_outcomes.csv.gz", control_episodes)
    pairs, matching = matched_episodes(case_episodes, control_episodes)
    write_csv(results/"matched_request_outcomes.csv", pairs)
    complete_zones = zone_outcome_ledger(zones, cases)
    serial = single_pending_ledger(complete_zones)
    write_csv(results/"zone_outcomes.csv.gz", complete_zones)
    write_csv(results/"single_pending_zone_ledger.csv.gz", serial)
    accepted = set(serial.loc[serial["portfolio_selected"], "entry_event_id"].dropna())
    serial_trades = cases.loc[cases["event_id"].isin(accepted)].copy()
    write_csv(results/"single_position_trades.csv.gz", serial_trades)
    info, control_info, serial_info = metrics(cases, folds), metrics(randoms, folds), metrics(serial_trades, folds)
    monthly = month_support(cases, folds)
    gates = development_gates(info, matching, serial_info, monthly, config)
    net = describe(case_episodes["episode_net_return"], case_episodes["mother_decision_time"])
    zone_effect = describe(complete_zones["episode_net_return"], complete_zones["mother_decision_time"])
    gates["complete_evidence"] = bool(case_episodes["observed"].all() and control_episodes["observed"].all() and complete_zones["observed"].all())
    for label, effect in (("net", net), ("excess", matching["effect"])):
        p, lower = effect["month_cluster_p"], effect["ci95_bp"][0]
        gates[label+"_inference"] = bool(np.isfinite(p) and p < .01 and np.isfinite(lower) and lower > 0)
    classified, diagnosis, diagnostic_tables = diagnose_frame(cases)
    write_csv(results/"classified_case_trades.csv.gz", classified)
    for name, table in diagnostic_tables.items():
        write_csv(results/("diagnosis_"+name+".csv"), table)
    states, state_info = state_diagnostics(cases)
    write_csv(results/"entry_states.csv", states)
    # Only a descriptive saved benchmark: do not pair different entry families.
    benchmark_path = next(path for path in config["benchmark_inputs"] if path.endswith("case_trades.csv.gz"))
    benchmark = metrics(read_frame(ROOT/benchmark_path), folds)
    return {"status": "development_pass_requires_prospective_validation" if all(gates.values()) else "rejected_development_no_audit",
            "support": support, "outcomes_computed": True, "metrics": info,
            "control_metrics": control_info, "matching": matching, "single_position": serial_info,
            "request_intention_effect": net, "zone_intention_effect": zone_effect,
            "zone_outcome_counts": complete_zones["episode_status"].value_counts().to_dict(),
            "serial_accepted_zones": int(serial["portfolio_selected"].sum()),
            "months": monthly, "entry_context": state_info, "diagnosis": diagnosis,
            "gates": gates, "benchmark_descriptive_only": benchmark,
            "audit_opened": False, "independent_confirmation": False}


def run() -> None:
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Frozen base configuration changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCE_PATHS] + [config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    for path, expected in config["benchmark_inputs"].items():
        if digest(ROOT/path) != expected:
            raise RuntimeError("Saved descriptive benchmark changed: "+path)
    results = EXPERIMENT/"results"
    if results.exists():
        raise RuntimeError("Preserve previous attempts; output already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
                "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    study = Study(base, "development")
    hourly, management = study.featured(60, "SMA", 40), study.featured(5, "SMA", 40)
    all_entries, all_zones = [], []
    for fold, start, end in study.folds:
        entries, zones = build_source_zone_requests(hourly, fold=fold, start=utc(start),
                            end_exclusive=utc(end), observed_through=min(utc(end), study.raw["open_time"].max()+pd.Timedelta(minutes=5)))
        all_entries.append(entries)
        all_zones.append(zones)
    entries = pd.concat(all_entries, ignore_index=True)
    zones = pd.concat(all_zones, ignore_index=True)
    entries = attach_entry_colour_context(study.raw, management, entries)
    frame = build_source_matching_frame(study.raw, hourly, management, entries)
    all_controls, all_assignments, allocation_info = [], [], []
    for fold, start, end in study.folds:
        control, assignment, info = assign_source_controls(entries.loc[entries["fold"].eq(fold)], frame,
                            count=3, seed=20260906, start_inclusive=utc(start), end_exclusive=utc(end), embargo_hours=72)
        all_controls.append(control)
        all_assignments.append(assignment)
        allocation_info.append({"fold": fold, **info})
    controls = attach_entry_colour_context(study.raw, management, pd.concat(all_controls, ignore_index=True))
    assignments = pd.concat(all_assignments, ignore_index=True)
    for name, table in {"source_zones": zones, "case_requests": entries, "control_requests": controls,
                        "assignments": assignments, "matching_frame": frame}.items():
        write_csv(results/(name+".csv.gz"), table)
    support = support_info(entries, zones, assignments, [f[0] for f in study.folds])
    write_json(results/"support.json", {**support, "source_receipt": study.source_receipt, "allocations": allocation_info})
    final = evaluate_prepared(study, entries, zones, controls, assignments, config, results, support)
    final.update(source_receipt=study.source_receipt, config_sha256=digest(config_path),
                 source_hashes=sources, allocations=allocation_info, holdout_price_rows=0,
                 training_eligible=False, production_eligible=False)
    write_json(results/"summary.json", final)
    print(json.dumps({"status": final["status"], "support": json.loads((results/"support.json").read_text()),
                      "metrics": final.get("metrics"), "matching": final.get("matching")}, ensure_ascii=False))


if __name__ == "__main__":
    run()
