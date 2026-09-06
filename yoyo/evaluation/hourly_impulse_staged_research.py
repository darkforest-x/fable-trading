"""Preregistered six-arm partial-realisation experiment on fixed hourly entries.

Only entry-hour and earlier features are supplied to the execution engine.
The study uses V1 safe source/chronological folds and causal control selection;
control outcomes are recomputed under the staged policy, not borrowed from V1.
V1 transport was seen before this hypothesis, so any V2 audit is reuse #2 and
never a pristine holdout. No new audit is opened after failed development gates.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_research import (
    ROOT, Study, clean, cluster_p, digest, metrics, utc, write_csv, write_json,
)
from yoyo.layers.l3_backtest.hourly_impulse import single_position_ledger
from yoyo.layers.l3_backtest.hourly_impulse_staged import simulate_staged_events

EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-staged-realisation-preholdout-20260906-v2"
REFERENCE_POLICY = {"management_minutes":15,"ma_kind":"SMA","ma_length":40,"exit_mode":"colour","confirmations":1}


def evaluate(study: Study, entries: pd.DataFrame, policy: dict) -> pd.DataFrame:
    """Complete native management bars; independent events, same boundary embargo."""
    early, runner = study.featured(15,"SMA",40), study.featured(60,"SMA",40)
    rows = []
    for fold, _, end in study.folds:
        part = entries.loc[entries["fold"].eq(fold)]
        if len(part):
            rows.append(simulate_staged_events(study.raw, early, runner, part, {**study.config["execution"], **policy}, end_exclusive=utc(end)))
    return pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()


def matched(study: Study, trades: pd.DataFrame, policy: dict, entry: dict):
    """Reuse only causal control assignment; recalculate every control's outcome."""
    requests, pairs, _ = study.matched(trades,REFERENCE_POLICY,entry)
    controls = evaluate(study,requests,policy) if len(requests) else requests
    if not len(controls):
        return controls,pairs,{"coverage":0,"mean_excess_bp":None,"month_cluster_p":None}
    t = controls.loc[controls["closed"].eq(True) & np.isfinite(controls["net_return"])]
    grouped = t.groupby("parent_event_id")["net_return"].agg(["count","mean"])
    valid = grouped.loc[grouped["count"].eq(study.config["matching"]["count_per_trade"]),"mean"]
    pairs["control_mean_return"] = pairs["event_id"].map(valid)
    pairs["excess"] = pairs["event_net_return"]-pairs["control_mean_return"]
    exact = pairs.loc[np.isfinite(pairs["excess"])]
    return controls,pairs,{
        "coverage":len(exact)/max(1,int(trades["closed"].sum())),
        "mean_excess_bp":exact["excess"].mean()*10000,
        "matched_event_mean_net_bp":exact["event_net_return"].mean()*10000,
        "control_mean_net_bp":exact["control_mean_return"].mean()*10000,
        "month_cluster_p":cluster_p(exact["excess"],exact["entry_time"],monthly=True),
        "control_rows":len(controls),"unique_control_times":controls["entry_time"].nunique(),
        "control_time_reuse_allowed":False,
    }


def development_gates(info: dict, match: dict, config: dict) -> dict:
    s = config["selection"]
    return {
        "samples":info["events"]>=s["development_minimum_events"] and info["minimum_fold_events"]>=s["development_minimum_per_fold"],
        "positive_folds":info.get("positive_folds",0)>=s["development_positive_folds"],
        "net_profit":info["mean_net_bp"]>s["development_min_mean_net_bp"],
        "profit_factor":info.get("profit_factor",0)>s["development_min_profit_factor"],
        "matched_coverage":match["coverage"]>=s["matched_coverage"],
        "matched_excess":match.get("mean_excess_bp") is not None and match["mean_excess_bp"]>0,
    }


def run(config: dict, phase: str):
    base_path = ROOT/config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Parent frozen config changed")
    base = json.loads(base_path.read_text())
    if config["management"] != {"early_minutes":15,"runner_minutes":60,"ma_kind":"SMA","ma_length":40}:
        raise RuntimeError("Staged management clock differs from frozen implementation")
    if any(config[k] != base["execution"][k] for k in ("cost_fraction","max_hours")):
        raise RuntimeError("Staged economics differs from frozen parent replay")
    results = EXPERIMENT/"results"
    results.mkdir(exist_ok=True)
    selection_path = results/"selection.json"
    if phase == "development":
        if selection_path.exists():
            raise RuntimeError("Preserve existing selection; no silent rerun")
        entry = {**base["baseline"], **config["entry_override"]}
        policies = config["policies"]
    else:
        frozen_bytes = subprocess.run(["git","show",f"HEAD:{selection_path.relative_to(ROOT)}"],cwd=ROOT,check=True,capture_output=True).stdout
        if frozen_bytes != selection_path.read_bytes():
            raise RuntimeError("Commit exact staged selection before transport")
        frozen = json.loads(frozen_bytes)
        if not frozen["go_to_transport"] or digest(EXPERIMENT/"config.json") != frozen["config_sha256"]:
            raise RuntimeError("Development gate failed or configuration changed")
        if (results/"audit_started.json").exists():
            raise RuntimeError("Preserve one-shot V2 transport history")
        write_json(results/"audit_started.json",{"family_historical_transport_use":2,"started_at":pd.Timestamp.now(tz="UTC")})
        entry, policies = frozen["entry"], [config["policies"][0], frozen["policy"]]
    study = Study(base,phase)
    entries = study.entries(entry)
    fold_names = [f[0] for f in study.folds]
    summaries, ledgers = [], {}
    for policy in policies:
        trades = evaluate(study,entries,policy)
        info = metrics(trades,fold_names)
        write_csv(results/f"{phase}_{policy['id']}_trades.csv.gz",trades)
        summaries.append({"policy":policy, "metrics":info})
        ledgers[policy["id"]] = trades
        print(json.dumps(clean(summaries[-1])),flush=True)
    ranked = sorted(summaries,key=lambda row:row["metrics"]["robust_score_bp"],reverse=True)
    best = ranked[0] if phase == "development" else summaries[-1]
    for row in (summaries[0],best):
        name = row["policy"]["id"]
        if "matched" in row:
            continue
        controls,pairs,match = matched(study,ledgers[name],row["policy"],entry)
        row["matched"] = match
        write_csv(results/f"{phase}_{name}_controls.csv.gz",controls)
        write_csv(results/f"{phase}_{name}_matched_pairs.csv",pairs)
        single = single_position_ledger(ledgers[name]) if len(ledgers[name]) else ledgers[name]
        write_csv(results/f"{phase}_{name}_single_position.csv.gz",single)
        selected = single.loc[single["portfolio_selected"].eq(True)] if len(single) else single
        row["single_position"] = metrics(selected,fold_names)
    summary = {"phase":phase,"source":study.source_receipt,"entry":entry,"arms":summaries,"best":best,"lineage":config["lineage"]}
    if phase == "development":
        checks = development_gates(best["metrics"],best["matched"],config)
        selection = {"config_sha256":digest(EXPERIMENT/"config.json"),"entry":entry,"policy":best["policy"],"gate_checks":checks,"go_to_transport":all(checks.values()),"status":"frozen_for_transport" if all(checks.values()) else "rejected_development_no_new_transport"}
        write_json(selection_path,selection)
        summary["selection"] = selection
    else:
        m,r = best["metrics"],best["matched"]
        summary["audit_gates"] = {
            "samples":m["events"]>=60 and m["minimum_fold_events"]>=8,
            "full_halfyears":len(m.get("folds",[]))>=2 and all(f["mean"] is not None and f["mean"]>0 for f in m["folds"][:2]),
            "net":m["mean_net_bp"]>0,"pf":m.get("profit_factor",0)>1.1,
            "match":r["coverage"]>=.9 and r.get("mean_excess_bp") is not None and r["mean_excess_bp"]>0 and r.get("month_cluster_p") is not None and r["month_cluster_p"]<.01,
            "significance":m.get("net_month_cluster_p",1)<.01 and m.get("net_week_cluster_p",1)<.01,
            "cost_stress":m["mean_net_bp"]>10,
            "single_position":best["single_position"]["mean_net_bp"]>0,
        }
        summary["status"] = "passed_reused_transport_not_profit_proof" if all(summary["audit_gates"].values()) else "rejected_reused_transport"
    write_json(results/f"{phase}_summary.json",summary)
    print(json.dumps(clean(summary),indent=2),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase",choices=["development","audit"],required=True)
    arguments = parser.parse_args()
    run(json.loads((EXPERIMENT/"config.json").read_text()),arguments.phase)
