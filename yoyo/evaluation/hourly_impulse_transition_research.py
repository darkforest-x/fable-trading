"""Frozen opposite-state versus actual-transition exit experiment.

Entry context uses only the native5m bar completed at the request boundary.
All post-entry paths, arm times and returns are outcomes, never entry features.
V4 requests/controls are byte-pinned before prices; no outcome-based rematching.
The complete mother intention remains the denominator for the K2 diagnostic.
Only reused 2023--2024 development is materialised. No audit/live entrypoint.
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources, month_support, development_gates
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import (
    describe, direct_requests, simulate_requests, episode_ledger,
    single_pending_ledger, matched_episodes, compare_episodes,
)
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, clean, digest, metrics, write_csv, write_json


EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5"
COHORTS = ["direct_k1_stop", "wait_k2_k1_stop"]
MODES = ["colour", "transition_colour"]
SOURCE_PATHS = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_colour_context.py",
    "yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py", "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_diagnostics.py", "yoyo/evaluation/hourly_impulse_transition_research.py",
]
PARITY_COLUMNS = ["event_id", "entry_time", "entry_price", "exit_time", "exit_price", "closed", "outcome",
                  "initial_stop", "signal_atr", "gross_return", "net_return", "hold_minutes"]


def read_frame(path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in frame:
        if column.endswith(("_time", "_deadline")):
            frame[column] = pd.to_datetime(frame[column], utc=True, format="mixed")
    return frame


def assert_parity(before: pd.DataFrame, after: pd.DataFrame, *, transition: bool = False) -> None:
    """Exact old replay, or equivalent genuine-flip paths ignoring exit label."""
    columns = [c for c in PARITY_COLUMNS if not (transition and c == "outcome")]
    left = before[columns].sort_values("event_id").reset_index(drop=True)
    right = after[columns].sort_values("event_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12)


def state_diagnostics(trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Predeclared entry-known strata; held-path excursions are descriptive."""
    rows = []
    for state in ("aligned", "opposite", "unknown"):
        part = trades.loc[trades["ltf_entry_state"].eq(state)]
        closed = part.loc[part["closed"].eq(True) & np.isfinite(part["net_return"])]
        losers = closed.loc[closed["net_return"].lt(0)]
        row = {"state": state, "requests": len(part), "closed": len(closed),
               "net_mean_bp": closed["net_return"].mean()*1e4,
               "gross_mean_bp": closed["gross_return"].mean()*1e4,
               "wins": int(closed["net_return"].gt(0).sum()), "losses": len(losers),
               "exit_5min": int(closed["hold_minutes"].eq(5).sum()),
               "exit_le_30min": int(closed["hold_minutes"].le(30).sum()),
               "hold_median_minutes": closed["hold_minutes"].median(),
               "hold_mean_minutes": closed["hold_minutes"].mean(),
               "hard_stops": int(closed["outcome"].str.startswith("hard_stop").sum()),
               "time_exits": int(closed["outcome"].eq("time_exit").sum()),
               "fee_flips": int((closed["gross_return"].gt(0) & closed["net_return"].le(0)).sum()),
               "loser_mfe_ge_1r": int(losers["max_favourable_r"].ge(1).sum()),
               "mae_median_r": closed["max_adverse_r"].median()}
        if "transition_first_armed_at" in part:
            executed = part.loc[~part["outcome"].str.startswith("entry_")]
            row["never_armed_executed"] = int(executed["transition_first_armed_at"].isna().sum())
        rows.append(row)
    return pd.DataFrame(rows), {"state_counts": trades["ltf_entry_state"].value_counts().to_dict(),
                               "strata": rows}


def verify_config(config: dict, base: dict) -> None:
    if config["cohorts"] != COHORTS or config["exit_modes"] != MODES or config["primary_cohort"] != COHORTS[0]:
        raise RuntimeError("Frozen cohorts/modes changed")
    if config["policy_id"] != "5m_native40" or config["no_audit_entry_point"] is not True:
        raise RuntimeError("Frozen timing/development contract changed")
    if base["execution"]["max_hours"] != 72 or base["execution"]["cost_fraction"] != .002:
        raise RuntimeError("Fixed economics changed")


def adjust_contrasts(contrasts: dict) -> None:
    """Holm across exactly two planned paired effects; missing p is one."""
    if set(contrasts) != set(COHORTS):
        raise ValueError("Exactly the two frozen contrasts are required")
    def finite_p(key):
        value = contrasts[key]["month_cluster_p"]
        return float(value) if value is not None and np.isfinite(value) else 1.0
    previous = 0.0
    for rank, key in enumerate(sorted(contrasts, key=finite_p)):
        previous = max(previous, min(1.0, (2-rank)*finite_p(key)))
        contrasts[key]["holm_two_p"] = previous


def run() -> None:
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Base config changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCE_PATHS] + [config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    parent = ROOT/config["parent_results"]
    for name, expected in config["inputs"].items():
        if digest(parent/name) != expected:
            raise RuntimeError("Frozen parent input changed: "+name)
    results = EXPERIMENT/"results"
    if results.exists():
        raise RuntimeError("Preserve prior outcomes; no overwrite")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
               "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    study = Study(base, "development")
    mother_sets = {"case": read_frame(parent/"original_mothers.csv.gz"),
                   "control": read_frame(parent/"control_mothers.csv.gz")}
    regenerated = study.entries(base["baseline"]).sort_values("event_id").reset_index(drop=True)
    saved = mother_sets["case"].sort_values("event_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(regenerated, saved[regenerated.columns], check_dtype=False, rtol=1e-12, atol=1e-12)
    management = study.featured(5, "SMA", 40)
    policy = next(p for p in base["exit_policies"] if p["id"] == config["policy_id"])
    prepared = {}
    for cohort in COHORTS:
        prepared[cohort] = {}
        for label, mothers in mother_sets.items():
            requests = read_frame(parent/(cohort+"_"+label+"_requests.csv.gz"))
            requests = attach_entry_colour_context(study.raw, management, requests)
            statuses = direct_requests(mothers)[1] if cohort == COHORTS[0] else read_frame(parent/(label+"_wait_status.csv.gz"))
            prepared[cohort][label] = requests, statuses
            # Freeze entry-known context before joining any newly replayed outcomes.
            write_csv(results/(cohort+"_"+label+"_context.csv.gz"), requests)
    rows, ledgers, all_trades, contrasts, parity = [], {}, {}, {}, {}
    folds = [f[0] for f in study.folds]
    for cohort in COHORTS:
        for mode in MODES:
            arm = cohort+"__"+mode
            cases, controls, trades = None, None, None
            for label, mothers in mother_sets.items():
                requests, statuses = prepared[cohort][label]
                replay = simulate_requests(study, requests, {**policy, "exit_mode": mode})
                if mode == "colour":
                    assert_parity(read_frame(parent/(cohort+"_"+label+"_trades.csv.gz")), replay)
                    parity[cohort+"_"+label+"_old_replay"] = len(replay)
                else:
                    valid = ~replay["outcome"].str.startswith("entry_")
                    if not replay.loc[valid, "transition_initial_state"].eq(replay.loc[valid, "ltf_entry_state"]).all():
                        raise AssertionError("Execution initialization disagrees with independent entry context")
                    before = all_trades[cohort+"__colour_"+label]
                    aligned = replay["ltf_entry_state"].eq("aligned") & replay["transition_reset_count"].eq(0)
                    ids = set(replay.loc[aligned, "event_id"])
                    assert_parity(before.loc[before["event_id"].isin(ids)], replay.loc[aligned], transition=True)
                    parity[cohort+"_"+label+"_initially_aligned"] = len(ids)
                all_trades[arm+"_"+label] = replay
                episodes = episode_ledger(mothers, statuses, replay)
                write_csv(results/(arm+"_"+label+"_trades.csv.gz"), replay)
                write_csv(results/(arm+"_"+label+"_episodes.csv.gz"), episodes)
                state_table, states = state_diagnostics(replay)
                write_csv(results/(arm+"_"+label+"_states.csv"), state_table)
                if label == "case":
                    cases, trades, case_states = episodes, replay, states
                else:
                    controls = episodes
            pairs, match = matched_episodes(cases, controls)
            write_csv(results/(arm+"_matched.csv"), pairs)
            serial = single_pending_ledger(cases)
            write_csv(results/(arm+"_single_pending.csv.gz"), serial)
            ids = set(serial.loc[serial["portfolio_selected"], "event_id"])
            info = metrics(trades, folds)
            single = metrics(trades.loc[trades["event_id"].isin(ids)], folds)
            months = month_support(trades, folds)
            classified, diagnosis, tables = diagnose_frame(trades)
            write_csv(results/(arm+"_losing_trades.csv.gz"), classified.loc[classified["net_loser"]])
            for name, frame in tables.items():
                write_csv(results/(arm+"_diagnosis_"+name+".csv"), frame)
            row = {"arm": arm, "cohort": cohort, "mode": mode, "metrics": info, "matched": match,
                   "single_position": single, "serial_accepted_mothers": len(ids), "month_support": months,
                   "mother_intention": describe(cases["episode_net_return"], cases["mother_decision_time"]),
                   "unknown_mothers": int((~cases["observed"]).sum()), "entry_states": case_states, "diagnosis": diagnosis}
            row["gates"] = development_gates(info, match, single, months, config)
            row["gates"]["complete_mother_evidence"] = row["unknown_mothers"] == 0
            row["gates"]["matched_p"] = match["effect"]["month_cluster_p"] < config["inference"]["selection_p"]
            rows.append(row)
            ledgers[arm] = cases
            print(json.dumps(clean({"arm": arm, "metrics": info, "states": case_states, "matched": match})), flush=True)
        old, new = cohort+"__colour", cohort+"__transition_colour"
        paired, contrast = compare_episodes(ledgers[old], ledgers[new])
        paired["former_winner_now_nonpositive"] = paired["episode_net_return_before"].gt(0) & paired["episode_net_return_after"].le(0)
        paired["former_loser_now_positive"] = paired["episode_net_return_before"].lt(0) & paired["episode_net_return_after"].gt(0)
        contrast["former_winners_now_nonpositive"] = int(paired["former_winner_now_nonpositive"].sum())
        contrast["former_losers_now_positive"] = int(paired["former_loser_now_positive"].sum())
        context = prepared[cohort]["case"][0][["event_id", "ltf_entry_state"]]
        paired = paired.merge(context, on="event_id", how="left", validate="one_to_one")
        write_csv(results/(cohort+"_paired_changes.csv.gz"), paired)
        contrasts[cohort] = contrast
    adjust_contrasts(contrasts)
    for row in rows:
        row["gates"]["paired_effect"] = contrasts[row["cohort"]]["mean_bp"] > 0 and contrasts[row["cohort"]]["holm_two_p"] < .01
    primary = next(r for r in rows if r["cohort"] == COHORTS[0] and r["mode"] == "transition_colour")
    summary = {"status": "mechanism_pass_requires_separate_verification" if all(primary["gates"].values()) else "rejected_development_no_audit",
               "arms": rows, "contrasts": contrasts, "parity": parity, "source": study.source_receipt,
               "config_sha256": digest(config_path), "sources": sources, "lineage": config["lineage"],
               "audit_prices_loaded": False, "holdout_consumed": False, "production_eligible": False, "training_eligible": False}
    write_json(results/"summary.json", summary)
    print(json.dumps(clean({"status": summary["status"], "contrasts": contrasts, "parity": parity})), flush=True)


if __name__ == "__main__":
    run()
