"""Four frozen entry-environment arms on reused 2023--2024 development.

The 4h context is known by K1 OPEN, whereas K1 morphology is known at close.
All arms share context-valid support. Control assignments are frozen before
any outcome simulation, and exact arm/context eligibility is shared with cases.
Only labels inspect the subsequent 72h. No audit phase or live writer exists.
Official timing basis: pandas 2.3 backward merge_asof, and native complete bars.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_context import add_prior_4h_context
from yoyo.evaluation.hourly_impulse_context_matching import arm_mask, assign_controls
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_research import (
    ROOT, Study, clean, cluster_p, digest, metrics, utc, write_csv, write_json,
)
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events, single_position_ledger


EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-prior-context-preholdout-20260906-v3"
ARM_IDS = ["original", "hourly_slope", "prior4h_trend", "prior4h_not_chasing"]
NEW_ARMS = ARM_IDS[2:]
CONTEXT_COLUMNS = ["context_available", "context_side", "context_slope_atr", "context_valid"]
SOURCE_MODULES = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_context.py",
    "yoyo/layers/l3_backtest/hourly_impulse.py",
    "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_matching.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_diagnostics.py",
]


def committed_sources(paths: list[Path]) -> list[dict]:
    """Require exact committed builder/config bytes before loading prices."""
    result = []
    for path in paths:
        relative = str(path.relative_to(ROOT))
        content = subprocess.run(["git", "show", "HEAD:" + relative], cwd=ROOT,
                                 check=True, capture_output=True).stdout
        if content != path.read_bytes():
            raise RuntimeError("Commit frozen source before outcomes: " + relative)
        result.append({"path": relative, "sha256": digest(path)})
    return result


def closed_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return frame.loc[frame["closed"].eq(True) & np.isfinite(frame["net_return"])].copy()


def month_support(trades: pd.DataFrame, folds: list[str]) -> dict:
    t = closed_rows(trades)
    if t.empty:
        return {"active_months": 0, "minimum_months_per_fold": 0, "by_fold": {f: 0 for f in folds}}
    t["month"] = pd.to_datetime(t["entry_time"], utc=True).dt.strftime("%Y-%m")
    counts = t.groupby("fold")["month"].nunique().reindex(folds, fill_value=0)
    return {"active_months": int(t["month"].nunique()),
            "minimum_months_per_fold": int(counts.min()), "by_fold": counts.to_dict()}


def attach_outcomes(pairs: pd.DataFrame, trades: pd.DataFrame,
                    controls: pd.DataFrame, count: int = 3) -> tuple[pd.DataFrame, dict]:
    """Full finite closed controls only; assignment status never follows PnL."""
    final = pairs.copy()
    if "event_id" not in final:
        final["event_id"] = pd.Series(dtype=str)
    t, c = closed_rows(trades), closed_rows(controls)
    case_returns = t.set_index("event_id")["net_return"] if len(t) else pd.Series(dtype=float)
    case_times = t.set_index("event_id")["entry_time"] if len(t) else pd.Series(dtype="datetime64[ns, UTC]")
    final["event_net_return"] = final["event_id"].map(case_returns)
    final["entry_time"] = final["event_id"].map(case_times)
    if len(c):
        grouped = c.groupby("parent_event_id")["net_return"].agg(["count", "mean"])
        means = grouped.loc[grouped["count"].eq(count), "mean"]
    else:
        means = pd.Series(dtype=float)
    final["control_mean_return"] = final["event_id"].map(means)
    final["excess"] = final["event_net_return"] - final["control_mean_return"]
    paired = final.loc[np.isfinite(final["excess"])]
    info = {
        "paired_events": len(paired), "closed_events": len(t),
        "coverage": len(paired) / max(1, len(t)),
        "matched_event_mean_net_bp": paired["event_net_return"].mean() * 1e4,
        "control_mean_net_bp": paired["control_mean_return"].mean() * 1e4,
        "mean_excess_bp": paired["excess"].mean() * 1e4,
        "month_cluster_p": cluster_p(paired["excess"], paired["entry_time"], monthly=True),
        "control_rows": len(controls), "closed_control_rows": len(c),
        "unique_control_times": int(controls["entry_time"].nunique()) if len(controls) else 0,
    }
    return final, info


def positive(value) -> bool:
    return bool(value is not None and np.isfinite(value) and value > 0)


def development_gates(info: dict, match: dict, single: dict, months: dict, config: dict) -> dict:
    """All fixed gates precede finalist ranking; no top-arm rescue rule."""
    s = config["selection"]
    return {
        "samples": info.get("events", 0) >= s["minimum_events"] and info.get("minimum_fold_events", 0) >= s["minimum_per_fold"],
        "positive_folds": info.get("positive_folds", 0) >= s["positive_folds"],
        "net_profit": positive(info.get("mean_net_bp")),
        "profit_factor": info.get("profit_factor") is not None and not np.isnan(info["profit_factor"]) and info["profit_factor"] > s["minimum_profit_factor"],
        "month_support": months["active_months"] >= s["minimum_active_months"] and months["minimum_months_per_fold"] >= s["minimum_months_per_fold"],
        "matched_coverage": match.get("coverage", 0) >= s["matched_coverage"],
        "matched_excess": positive(match.get("mean_excess_bp")),
        "single_position": positive(single.get("mean_net_bp")),
        "cost_stress": positive(info.get("extra_10bp_mean_net_bp")),
        "leave_top_two": positive(info.get("leave_top_two_mean_net_bp")),
    }


def choose_finalist(rows: list[dict]) -> dict | None:
    passing = [r for r in rows if r["arm"]["id"] in NEW_ARMS and all(r["gates"].values())]
    passing.sort(key=lambda r: (-r["metrics"]["robust_score_bp"], -r["metrics"]["worst_fold_bp"], r["arm"]["id"]))
    return passing[0] if passing else None


def holm_two(values: dict[str, float]) -> dict[str, float]:
    """Two newly registered arms only; not a cure for historical data reuse."""
    if set(values) != set(NEW_ARMS):
        raise ValueError("Exactly the two new hypotheses are required")
    ordered = sorted(values, key=lambda k: values[k] if np.isfinite(values[k]) else 1.0)
    result, previous = {}, 0.0
    for rank, name in enumerate(ordered):
        p = values[name] if np.isfinite(values[name]) else 1.0
        previous = max(previous, min(1.0, (2-rank) * p))
        result[name] = previous
    return result


def simulate(study: Study, entries: pd.DataFrame, policy: dict) -> pd.DataFrame:
    management = study.featured(5, "SMA", 40)
    pieces = []
    for fold, _, end in study.folds:
        part = entries.loc[entries["fold"].eq(fold)] if len(entries) else entries
        if len(part):
            pieces.append(simulate_events(study.raw, management, part,
                                         {**study.config["execution"], **policy}, end_exclusive=utc(end)))
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def filtered_events(common: pd.DataFrame, hourly: pd.DataFrame, arm: dict) -> pd.DataFrame:
    selected = pd.Series(False, index=common.index)
    for direction in (1, -1):
        allowed = hourly.loc[arm_mask(hourly, direction, arm), "open_time"]
        selected |= common["direction"].eq(direction) & common["signal_time"].isin(allowed)
    return common.loc[selected].copy()


def verify_config(config: dict, base: dict) -> None:
    expected = [
        {"id": "original", "require_hourly_slope": False, "require_context_trend": False, "max_extension_atr": 99},
        {"id": "hourly_slope", "require_hourly_slope": True, "require_context_trend": False, "max_extension_atr": 99},
        {"id": "prior4h_trend", "require_hourly_slope": False, "require_context_trend": True, "max_extension_atr": 99},
        {"id": "prior4h_not_chasing", "require_hourly_slope": False, "require_context_trend": True, "max_extension_atr": 1.5},
    ]
    if config["arms"] != expected or config["policy_id"] != "5m_native40":
        raise RuntimeError("Frozen finite arms or exit clock changed")
    if config["context"] != {"minutes": 240, "ma_kind": "SMA", "ma_length": 40, "known_by": "K1 open", "common_valid_support": True}:
        raise RuntimeError("Prior context contract changed")
    if base["execution"]["cost_fraction"] != .002 or base["execution"]["max_hours"] != 72:
        raise RuntimeError("Parent execution economics changed")
    if config.get("no_audit_entry_point") is not True:
        raise RuntimeError("V3 is development only")
    if config["matching"]["count_per_trade"] != 3:
        raise RuntimeError("Exactly three controls per event are frozen")


def run() -> None:
    config_path = EXPERIMENT / "config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT / config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Parent config hash differs")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT / p for p in SOURCE_MODULES] + [config_path, base_path, EXPERIMENT / "PROJECT_PLAN.md"])
    results = EXPERIMENT / "results"
    if results.exists():
        raise RuntimeError("Preserve prior V3 run; no silent overwrite")
    results.mkdir()
    write_json(results / "started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
                                         "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    study = Study(base, "development")
    policy = next(p for p in base["exit_policies"] if p["id"] == config["policy_id"])
    hourly = add_prior_4h_context(study.raw, study.featured(60, "SMA", 40))
    original = study.entries(base["baseline"])
    by_time = hourly.set_index("open_time")
    for column in CONTEXT_COLUMNS:
        original[column] = original["signal_time"].map(by_time[column])
    common = original.loc[original["context_valid"].eq(True)].copy()
    write_csv(results / "common_support_events.csv.gz", common)
    assigned = {}
    # All arms' request sets are recorded before any price-path outcome is run.
    for arm in config["arms"]:
        entries = filtered_events(common, hourly, arm)
        requests, pairs, receipt = assign_controls(hourly, study.raw, study.featured(5, "SMA", 40), entries, arm, study.folds,
                                                  max_hours=72, count=3, seed=config["matching"]["seed"])
        assigned[arm["id"]] = (entries, requests, pairs, receipt)
        write_csv(results / (arm["id"] + "_entry_requests.csv.gz"), entries)
        write_csv(results / (arm["id"] + "_control_requests.csv.gz"), requests)
        write_json(results / (arm["id"] + "_assignment.json"), receipt)
    folds = [f[0] for f in study.folds]
    rows, ledgers = [], {}
    for arm in config["arms"]:
        name = arm["id"]
        entries, requests, pairs, receipt = assigned[name]
        trades, controls = simulate(study, entries, policy), simulate(study, requests, policy)
        pairs, match = attach_outcomes(pairs, trades, controls)
        info = metrics(trades, folds)
        single = single_position_ledger(trades) if len(trades) else trades
        single_selected = single.loc[single["portfolio_selected"].eq(True)] if len(single) else single
        single_info, months = metrics(single_selected, folds), month_support(trades, folds)
        row = {"arm": arm, "metrics": info, "matched": match, "single_position": single_info,
               "month_support": months, "assignment": receipt, "kept_requests": len(entries),
               "dropped_from_common": len(common)-len(entries),
               "net_bp_per_original_request": closed_rows(trades)["net_return"].sum()*1e4/max(1,len(common)) if len(trades) else 0,
               "directions": {str(d): metrics(trades.loc[trades["direction"].eq(d)], folds) for d in (1,-1)} if len(trades) else {}}
        row["gates"] = development_gates(info, match, single_info, months, config)
        for suffix, frame in [("trades.csv.gz", trades), ("controls.csv.gz", controls), ("matched_pairs.csv", pairs), ("single_position.csv.gz", single)]:
            write_csv(results / (name + "_" + suffix), frame)
        if len(trades):
            classified, diagnosis, tables = diagnose_frame(trades)
            write_csv(results / (name + "_classified.csv.gz"), classified)
            write_csv(results / (name + "_losing_trades.csv.gz"), classified.loc[classified["net_loser"]])
            row["diagnosis"] = diagnosis
            for table_name, table in tables.items():
                write_csv(results / (name + "_diagnosis_" + table_name + ".csv"), table)
        rows.append(row)
        ledgers[name] = trades
        print(json.dumps(clean({"arm": name, "events": info["events"], "net_bp": info["mean_net_bp"], "matched": match, "gates": row["gates"]})), flush=True)
    original_ledger = ledgers["original"]
    for row in rows:
        retained = set(assigned[row["arm"]["id"]][0]["event_id"])
        removed = original_ledger.loc[~original_ledger["event_id"].isin(retained)] if len(original_ledger) else original_ledger
        row["removed_original_metrics"] = metrics(removed, folds)
    adjusted = holm_two({r["arm"]["id"]: r["matched"]["month_cluster_p"] for r in rows if r["arm"]["id"] in NEW_ARMS})
    for row in rows:
        row["matched_holm_two_p"] = adjusted.get(row["arm"]["id"])
    best = choose_finalist(rows)
    summary = {"status": "development_pass_needs_new_verification_design" if best else "rejected_development_no_audit",
               "source": study.source_receipt, "source_manifest": sources, "config_sha256": digest(config_path),
               "lineage": config["lineage"], "original_requests": len(original), "common_requests": len(common),
               "context_support_removed": len(original)-len(common), "policy": policy, "arms": rows,
               "finalist": best["arm"] if best else None, "audit_prices_loaded": False,
               "training_eligible": False, "production_eligible": False,
               "holm_scope": "two new-arm excess tests only; does not remove historical search bias"}
    write_json(results / "summary.json", summary)
    write_csv(results / "comparison.csv", pd.DataFrame([{"arm": r["arm"]["id"], **{k:v for k,v in r["metrics"].items() if k != "folds"},
                                                        "matched_excess_bp": r["matched"]["mean_excess_bp"], "matched_coverage": r["matched"]["coverage"],
                                                        "all_gates": all(r["gates"].values())} for r in rows]))
    print(json.dumps(clean({"status": summary["status"], "finalist": summary["finalist"], "common_support":len(common)})), flush=True)


if __name__ == "__main__":
    run()
