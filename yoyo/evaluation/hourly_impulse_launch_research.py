"""V11 one-hour launch-progress deadline on the original V5 direct K1 pool.

Only the post-entry execution policy changes. The entry-known V5 contexts and
V4 original251 mothers/462 controls are byte-pinned; baseline all-column parity
precedes every candidate outcome. Progress uses completed native5m CLOSE only,
up to entry+60min, relative to frozen initial risk. No outcome selects an entry,
control, parameter or denominator. Reused2023--2024 only, no audit/live API.

Paired D uses all251 intentions; I uses the original154 complete control triples,
with missing controls remaining NaN. Calendar-month blocks preserve paired rows:
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
All-field comparison uses explicit timestamp equality and CSV float tolerance:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources, development_gates, month_support
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import (
    describe, direct_requests, simulate_requests, episode_ledger, matched_episodes, single_pending_ledger,
)
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects, positive_inference
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, metrics, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame


EXPERIMENT_ID = "exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
BASE_CONFIG = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
BASE_SHA256 = "95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a"
MOTHERS = "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results"
PARENT = "experiments/active/exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5/results"
MOTHER_INPUTS = {
    "original_mothers.csv.gz": "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
    "control_mothers.csv.gz": "01050c7a9602f469406df515edcc73ef2f4c9db2d46529e25030934012eebd5a",
    "assignments.csv": "671782877ee67824f7687243d5e7deae29d78a0bcba6245319ecf55629027b0f",
    "assignment_receipt.json": "1d77ca407712520e645463d30f97d26d452ccce45e87e68c2adcbc4120c43220",
}
INPUTS = {
    "direct_k1_stop_case_context.csv.gz": "bf3cb2796c5ede1cb731fee75833d292184f7a9ce6d69b37663dc519462bb4d7",
    "direct_k1_stop_control_context.csv.gz": "a239256f8139e262e4f3b5a573a9d5879cde16717c1e17bc459df860ca28b1bc",
    "direct_k1_stop__transition_colour_case_trades.csv.gz": "6f3f7772163823d956f9f369be51db88f350067ea1b9734ba93e03714de75584",
    "direct_k1_stop__transition_colour_case_episodes.csv.gz": "bcc83278b9bb79b3281fbf5d478530697a156408ea74b805012cdb0c6291cd81",
    "direct_k1_stop__transition_colour_control_trades.csv.gz": "f8576bd23181ad94be5c1c67608761dd6e5b8dcd16324f12f6a58c4a8631376a",
    "direct_k1_stop__transition_colour_control_episodes.csv.gz": "65df9793687d816ac1dda7488038f4656239396e6aabdf1925c03f0d16891b45",
    "direct_k1_stop__transition_colour_matched.csv": "b4c5d5b3a9848428eb2b90189b03c29812f33c8df1f46068ca3540fcb988f38f",
    "direct_k1_stop__transition_colour_single_pending.csv.gz": "bb8157da8486263120b87146cad84c53f99c5c0f58a36e52a2a002f2f0052fbb",
    "summary.json": "4ab9e30e7c95aa672d9359a16b00bfe82d204f5e2980c41672cf1e09b0347d3a",
}
POLICIES = [
    {"id": "5m_native40", "management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
     "exit_mode": "transition_colour", "confirmations": 1},
    {"id": "5m_native40_launch60", "management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
     "exit_mode": "transition_colour", "confirmations": 1,
     "launch_deadline_minutes": 60, "launch_progress_r": 0.5},
]
FOLDS = [["2023H1", "2023-01-01", "2023-07-01"], ["2023H2", "2023-07-01", "2024-01-01"],
         ["2024H1", "2024-01-01", "2024-07-01"], ["2024H2", "2024-07-01", "2025-01-01"]]
SELECTION = {"minimum_events": 80, "minimum_per_fold": 12, "positive_folds": 4,
    "minimum_profit_factor": 1.1, "minimum_active_months": 12,
    "minimum_months_per_fold": 3, "matched_coverage": 0.9}
SOURCES = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_colour_context.py",
    "yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py", "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_management_research.py", "yoyo/evaluation/hourly_impulse_diagnostics.py",
    "yoyo/evaluation/hourly_impulse_transition_research.py", "yoyo/evaluation/hourly_impulse_launch_research.py",
    "tests/test_hourly_impulse_launch_deadline.py", "tests/test_hourly_impulse_launch_research.py",
]


def frozen_config():
    return {"experiment_id": EXPERIMENT_ID, "base_config": BASE_CONFIG, "base_config_sha256": BASE_SHA256,
        "mother_results": MOTHERS, "mother_inputs": MOTHER_INPUTS, "parent_results": PARENT,
        "inputs": INPUTS, "policies": POLICIES, "selection": SELECTION,
        "inference": {"draws": 9999, "seed": 20260906, "p_limit": .01,
                      "joint_required": ["case_delta", "excess_delta"], "method": "month_cluster"},
        "known_support": {"cases": 251, "controls": 462, "matched": 154, "coverage_gate_unattainable": True},
        "no_audit_entry_point": True, "holdout_consumed": False, "production_eligible": False, "training_eligible": False}


def verify_config(config, base):
    if json.dumps(config, sort_keys=True) != json.dumps(frozen_config(), sort_keys=True):
        raise ValueError("Frozen V11 single launch deadline contract changed")
    if base["development_folds"] != FOLDS:
        raise ValueError("Only original2023--2024 development is permitted")
    e = base["execution"]
    if e["max_hours"] != 72 or e["cost_fraction"] != .002 or e["stop_first"] is not True:
        raise ValueError("Original stop-first/72h/20bp economics must remain unchanged")


def validate_population(mothers, contexts, assignments):
    """No outcome fields used: exact full support, UTC folds and old3 assignment."""
    for label, count in (("case", 251), ("control", 462)):
        m, c = mothers[label], contexts[label]
        if len(m) != count or len(c) != count:
            raise ValueError("Original mother/request counts changed")
        assert_saved_parity(m, c)
        for frame in (m, c):
            if frame.event_id.isna().any() or not frame.event_id.is_unique:
                raise ValueError("Nonunique mother/request identities")
            stamps = pd.to_datetime(frame.decision_time, utc=True, format="mixed")
            allowed = pd.Series(False, index=frame.index)
            for fold, start, end in FOLDS:
                allowed |= frame.fold.eq(fold) & stamps.ge(utc(start)) & stamps.lt(utc(end)-pd.Timedelta(hours=72))
            if not allowed.all() or not stamps.eq(stamps.dt.floor("h")).all():
                raise ValueError("Mother/request decision outside frozen hourly folds")
    control = mothers["control"]
    counts = control.groupby("parent_event_id").size()
    if len(counts) != 154 or not counts.eq(3).all() or not control.decision_time.is_unique:
        raise ValueError("Exactly154 fixed non-reused triples required")
    if len(assignments) != 251 or not assignments.event_id.is_unique:
        raise ValueError("All251 original assignments required")
    matched = set(assignments.loc[assignments.match_status.eq("matched"), "event_id"])
    if set(counts.index) != matched or set(assignments.event_id) != set(mothers["case"].event_id):
        raise ValueError("Control parents differ from old assignment")


def replay_arm(study, policy, mothers, contexts, folder, config, *, parent=None):
    folder.mkdir()
    trades, episodes, parity = {}, {}, {}
    folds = [f[0] for f in FOLDS]
    prefix = "direct_k1_stop__transition_colour_"
    for label in ("case", "control"):
        t = simulate_requests(study, contexts[label], policy)
        e = episode_ledger(mothers[label], direct_requests(mothers[label])[1], t)
        if parent is not None:
            for suffix, table in (("trades", t), ("episodes", e)):
                saved = read_frame(parent/(prefix+label+"_"+suffix+".csv.gz"))
                assert_saved_parity(saved, table)
                parity[label+"_"+suffix] = {"rows": len(saved), "columns": len(saved.columns)}
        trades[label], episodes[label] = t, e
        write_csv(folder/f"{label}_trades.csv.gz", t)
        write_csv(folder/f"{label}_episodes.csv.gz", e)
    pairs, matching = matched_episodes(episodes["case"], episodes["control"])
    serial = single_pending_ledger(episodes["case"])
    if parent is not None:
        for name, table, suffix in (("matched", pairs, ".csv"), ("single_pending", serial, ".csv.gz")):
            saved = read_frame(parent/(prefix+name+suffix))
            assert_saved_parity(saved, table)
            parity[name] = {"rows": len(saved), "columns": len(saved.columns)}
    write_csv(folder/"matched.csv", pairs)
    write_csv(folder/"single_pending.csv.gz", serial)
    chosen = set(serial.loc[serial.portfolio_selected, "event_id"])
    selected = trades["case"].loc[trades["case"].event_id.isin(chosen)]
    info, controls, single = (metrics(x, folds) for x in (trades["case"], trades["control"], selected))
    months = month_support(trades["case"], folds)
    gates = development_gates(info, matching, single, months, config)
    gates["complete_evidence"] = bool(episodes["case"].observed.all() and episodes["control"].observed.all())
    net = describe(episodes["case"].episode_net_return, episodes["case"].mother_decision_time)
    gates.update(net_inference=positive_inference(net), excess_inference=positive_inference(matching["effect"]))
    classified, diagnosis, tables = diagnose_frame(trades["case"])
    write_csv(folder/"classified_case_trades.csv.gz", classified)
    for name, frame in tables.items():
        write_csv(folder/("diagnosis_"+name+".csv"), frame)
    summary = {"policy": policy, "metrics": info, "control_metrics": controls, "matching": matching,
        "single_position": single, "serial_selected_mothers": len(chosen), "original_mothers": len(serial),
        "months": months, "net_effect": net, "diagnosis": diagnosis, "gates": gates, "parity": parity}
    write_json(folder/"summary.json", summary)
    return summary, trades, episodes, pairs, serial


def paired_mechanics(before, after):
    """Retrospective full case ledger; no future subset replaces the251 denominator."""
    fixed = ["event_id", "entry_time", "entry_price", "direction", "initial_stop", "signal_atr", "risk_pct", "risk_atr"]
    assert_saved_parity(before[fixed], after[fixed])
    joined = before.merge(after, on="event_id", suffixes=("_before", "_after"), validate="one_to_one")
    known = joined.closed_before & joined.closed_after & np.isfinite(joined.net_return_before) & np.isfinite(joined.net_return_after)
    joined["difference"] = (joined.net_return_after-joined.net_return_before).where(known)
    joined["timeout_exit"] = joined.outcome_after.eq("launch_timeout_exit")
    joined["win_loss_transition"] = "unknown"
    for old, new, name in ((False, False, "loss_to_loss"), (False, True, "loss_to_win"),
                           (True, False, "win_to_loss"), (True, True, "win_to_win")):
        joined.loc[known & joined.net_return_before.gt(0).eq(old) & joined.net_return_after.gt(0).eq(new), "win_loss_transition"] = name
    # Zero-net is explicitly separated if present, rather than labelled a loss.
    joined.loc[known & (joined.net_return_before.eq(0) | joined.net_return_after.eq(0)), "win_loss_transition"] = "includes_flat"
    joined["mechanism_group"] = np.where(~known, "unknown", np.where(joined.timeout_exit, "launch_timeout", "original_exit_retained"))
    retained = joined.loc[known & ~joined.timeout_exit]
    if retained.difference.abs().gt(1e-12).any() or not retained.exit_time_before.eq(retained.exit_time_after).all():
        raise ValueError("Non-timeout completed paths must retain original returns and exit times")
    retained_ids = set(retained.event_id)
    assert_saved_parity(before.loc[before.event_id.isin(retained_ids)], after.loc[after.event_id.isin(retained_ids)])
    timed = joined.loc[known & joined.timeout_exit]
    if not timed.hold_minutes_after.eq(60).all() or timed.hold_minutes_after.ge(timed.hold_minutes_before).any():
        raise ValueError("Launch timeout must strictly shorten a still-open original path at60min")
    rows = []
    for name, part in joined.groupby("mechanism_group", sort=True):
        rows.append({"group": name, "n": len(part), "known": int(part.difference.notna().sum()),
            "old_mean_net_bp": part.net_return_before.mean()*1e4,
            "new_mean_net_bp": part.net_return_after.mean()*1e4, "mean_delta_bp": part.difference.mean()*1e4,
            "sum_delta_event_bp": part.difference.sum(min_count=1)*1e4,
            "wins_before": int(part.net_return_before.gt(0).sum()), "wins_after": int(part.net_return_after.gt(0).sum())})
    distributions = {}
    for column in ("net_return_before", "net_return_after", "difference"):
        x = joined[column].where(known).dropna()*1e4
        distributions[column] = {"n": len(x), "unknown": len(joined)-len(x), "outliers_removed": 0,
            "quantiles_bp": {str(k): v for k,v in x.quantile([0,.05,.25,.5,.75,.95,1]).items()}, "sd_bp": x.std(ddof=1)}
    return joined, pd.DataFrame(rows), {"total": len(joined), "known": int(known.sum()),
        "timeout_exits": int(joined.timeout_exit.sum()), "transitions": joined.win_loss_transition.value_counts().to_dict(),
        "distributions": distributions, "groups": rows,
        "interpretation": "Observed counterfactual exit-policy differences on reused historical paths; not randomized live treatment."}


def run():
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/config["base_config"]
    if digest(base_path) != BASE_SHA256:
        raise ValueError("Frozen base config hash changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES]+[config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    for directory, hashes in ((ROOT/MOTHERS, MOTHER_INPUTS), (ROOT/PARENT, INPUTS)):
        for name, expected in hashes.items():
            if digest(directory/name) != expected:
                raise ValueError("Pinned prior evidence changed: "+name)
    mothers = {"case": read_frame(ROOT/MOTHERS/"original_mothers.csv.gz"),
               "control": read_frame(ROOT/MOTHERS/"control_mothers.csv.gz")}
    contexts = {label: read_frame(ROOT/PARENT/f"direct_k1_stop_{label}_context.csv.gz") for label in mothers}
    assignments = pd.read_csv(ROOT/MOTHERS/"assignments.csv")
    validate_population(mothers, contexts, assignments)
    results = EXPERIMENT/"results"
    if results.exists():
        raise ValueError("Preserve prior outcome attempts; no overwrite")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
        "inputs": config["inputs"], "mother_inputs": MOTHER_INPUTS,
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    try:
        study = Study(base, "development")
        assert_saved_parity(mothers["case"], study.entries(base["baseline"]))
        for label in mothers:
            regenerated = attach_entry_colour_context(study.raw, study.featured(5, "SMA", 40), direct_requests(mothers[label])[0])
            assert_saved_parity(contexts[label], regenerated)
            write_csv(results/f"{label}_context.csv.gz", contexts[label])
        write_csv(results/"assignments.csv", assignments)
        # Entire old two-population/six-table parity must finish before treatment.
        old = replay_arm(study, POLICIES[0], mothers, contexts, results/"baseline", config, parent=ROOT/PARENT)
        write_json(results/"anchor_parity.json", old[0]["parity"])
        new = replay_arm(study, POLICIES[1], mothers, contexts, results/"candidate", config)
        for label in mothers:
            fixed = list(contexts[label].columns)+["entry_time", "entry_price", "risk_pct", "risk_atr"]
            assert_saved_parity(old[1][label][fixed], new[1][label][fixed])
        frames, effects = paired_effects(old[2]["case"], new[2]["case"], old[3], new[3], old[4], new[4])
        for name, frame in frames.items():
            write_csv(results/(name+".csv"), frame)
        mechanics, groups, diagnosis = paired_mechanics(old[1]["case"], new[1]["case"])
        write_csv(results/"paired_case_mechanics.csv.gz", mechanics)
        write_csv(results/"mechanism_groups.csv", groups)
        control_mechanics, control_groups, control_diagnosis = paired_mechanics(old[1]["control"], new[1]["control"])
        write_csv(results/"paired_control_mechanics.csv.gz", control_mechanics)
        write_csv(results/"control_mechanism_groups.csv", control_groups)
        monthly = []
        for name, episode in (("baseline", old[2]["case"]), ("candidate", new[2]["case"])):
            part = episode.assign(month=episode.mother_decision_time.dt.strftime("%Y-%m"))
            for (fold, month), subset in part.groupby(["fold", "month"]):
                monthly.append({"arm": name, "fold": fold, "month": month, "n": len(subset),
                    "known": int(subset.observed.sum()), "mean_net_bp": subset.episode_net_return.mean()*1e4})
        write_csv(results/"monthly_case_net.csv", pd.DataFrame(monthly))
        gates = {**new[0]["gates"], **{key: positive_inference(effects[key]) for key in ("case_delta", "excess_delta")}}
        summary = {"experiment_id": EXPERIMENT_ID, "status": "diagnostic_only_no_candidate_acceptance",
            "arms": {"baseline": old[0], "candidate": new[0]}, "effects": effects, "mechanics": diagnosis,
            "control_mechanics": control_diagnosis,
            "gates": gates, "all_financial_gates_pass": all(gates.values()),
            "known_coverage_ceiling": 154/251, "coverage_required": .9,
            "source": study.source_receipt, "sources": sources, "config_sha256": digest(config_path),
            "audit_prices_loaded": False, "holdout_consumed": False, "production_eligible": False, "training_eligible": False,
            "inputs": INPUTS, "mother_inputs": MOTHER_INPUTS,
            "output_hashes": {str(p.relative_to(results)): digest(p) for p in sorted(results.rglob("*")) if p.is_file()}}
        write_json(results/"summary.json", summary)
        print(json.dumps({"status": summary["status"], "candidate_net_bp": new[0]["metrics"]["mean_net_bp"],
            "timeout_exits": diagnosis["timeout_exits"], "case_delta_bp": effects["case_delta"]["mean_bp"]}), flush=True)
    except Exception as error:
        write_json(results/"failure.json", {"at": pd.Timestamp.now(tz="UTC"), "type": type(error).__name__, "message": str(error)})
        raise


if __name__ == "__main__":
    run()
