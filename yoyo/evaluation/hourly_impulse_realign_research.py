"""Frozen first-K2 immediate versus flat native-5m alignment comparison.

Entry requests use only the first known completed five-minute aligned colour
and that timestamp's raw OPEN. All V4 mothers and fixed random controls remain
in the intention ledger. Stops, cost, exits and absolute deadlines are fixed.
Outcome comparisons are descriptive labels, never request selection features.
Only reused 2023--2024 development prices can be loaded; no audit entry point.
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_colour_context import attach_entry_colour_context
from yoyo.data.hourly_impulse_realign import build_realign_requests
from yoyo.evaluation.hourly_impulse_aligned_execution import (
    simulate_realign_requests, realign_episode_ledger,
)
from yoyo.evaluation.hourly_impulse_context_research import (
    committed_sources, development_gates, month_support,
)
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import (
    describe, single_pending_ledger, matched_episodes, compare_episodes,
)
from yoyo.evaluation.hourly_impulse_research import (
    ROOT, Study, clean, digest, metrics, utc, write_csv, write_json,
)
from yoyo.evaluation.hourly_impulse_transition_research import read_frame, state_diagnostics


EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-flat-realignment-preholdout-20260906-v6"
ARMS = ["k2_immediate", "k2_flat_alignment"]
SOURCE_PATHS = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_colour_context.py",
    "yoyo/data/hourly_impulse_realign.py", "yoyo/layers/l3_backtest/hourly_impulse.py",
    "yoyo/evaluation/hourly_impulse_aligned_execution.py",
    "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py",
    "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_diagnostics.py",
    "yoyo/evaluation/hourly_impulse_transition_research.py",
    "yoyo/evaluation/hourly_impulse_realign_research.py",
]


def verify_config(config: dict, base: dict) -> None:
    """No silent entry, cost, exit, retry or holdout variations."""
    expected = {"mother_deadline_minutes": 480, "total_horizon_minutes": 4320,
                "first_alignment_only": True, "cancel_on_k1_extreme": False,
                "unknown_colour": "censor", "risk_check": "first_actual_open_only"}
    if config["arms"] != ARMS or config["wait"] != expected:
        raise RuntimeError("Frozen entry comparison changed")
    if config["exit_mode"] != "transition_colour" or config["policy_id"] != "5m_native40":
        raise RuntimeError("Fixed transition exit changed")
    if base["execution"]["max_hours"] != 72 or base["execution"]["cost_fraction"] != .002:
        raise RuntimeError("Fixed economics changed")
    if config["no_audit_entry_point"] is not True:
        raise RuntimeError("Development-only contract changed")


def merge_wait_statuses(original: pd.DataFrame, replacement: pd.DataFrame) -> pd.DataFrame:
    """Replace only already-emitted K2 requests; preserve all no-K2 mothers."""
    for frame in (original, replacement):
        if frame["event_id"].duplicated().any():
            raise ValueError("Each mother must have one terminal record")
    expected = set(original.loc[original["status"].eq("request_emitted"), "event_id"])
    if set(replacement["event_id"]) != expected:
        raise ValueError("All and only original emitted K2 requests need realignment status")
    result = original.copy().set_index("event_id")
    update = replacement.set_index("event_id")
    # Replace complete cells, including explicit NaN. DataFrame.update would
    # skip NaN and could accidentally preserve an obsolete terminal record.
    for column in update:
        if column not in result:
            result[column] = pd.Series(index=result.index, dtype=update[column].dtype)
        result.loc[update.index, column] = update[column]
    return result.reset_index()


def assert_saved_columns(before: pd.DataFrame, after: pd.DataFrame) -> None:
    """Compare every old column, normalizing only serialization dtypes."""
    if before["event_id"].duplicated().any() or after["event_id"].duplicated().any():
        raise ValueError("Parity IDs must be unique")
    left = before.sort_values("event_id").reset_index(drop=True).copy()
    right = after.sort_values("event_id").reset_index(drop=True)[left.columns].copy()
    for column in left:
        nonnull = right[column].dropna()
        temporal_objects = len(nonnull) > 0 and nonnull.map(lambda value: isinstance(value, pd.Timestamp)).all()
        if pd.api.types.is_datetime64_any_dtype(right[column]) or temporal_objects:
            left[column] = pd.to_datetime(left[column], utc=True, format="mixed")
            right[column] = pd.to_datetime(right[column], utc=True, format="mixed")
        if left[column].isna().all() and right[column].isna().all():
            left[column] = right[column]
        elif right[column].dtype == object:
            left[column] = left[column].fillna("")
            right[column] = right[column].fillna("")
    pd.testing.assert_frame_equal(left, right, check_dtype=False, rtol=1e-12, atol=1e-12)


def opportunity_changes(before: pd.DataFrame, after: pd.DataFrame,
                        old_trades: pd.DataFrame, new_trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Account for price/timing and participation on all original mothers.

    Every return, exit, price difference and future winner label below is an
    outcome diagnostic. No call from request construction depends on this.
    """
    pairs, contrast = compare_episodes(before, after)
    for suffix, trades in (("before", old_trades), ("after", new_trades)):
        columns = ["event_id", "entry_time", "entry_price", "initial_stop", "risk_pct",
                   "risk_atr", "exit_time", "outcome", "net_return", "hold_minutes"]
        part = trades.reindex(columns=columns).rename(columns={c: c+"_"+suffix for c in columns if c != "event_id"})
        pairs = pairs.merge(part, on="event_id", how="left", validate="one_to_one")
    pairs = pairs.merge(before[["event_id", "direction"]], on="event_id", validate="one_to_one")
    both = pairs["executed_before"] & pairs["executed_after"]
    finite = pairs["difference"].notna()
    pairs["same_executed"] = both
    pairs["participation_changed"] = pairs["executed_before"] != pairs["executed_after"]
    pairs["entry_delay_minutes"] = (pd.to_datetime(pairs["entry_time_after"], utc=True)-pd.to_datetime(pairs["entry_time_before"], utc=True)).dt.total_seconds()/60
    pairs["directional_entry_change_bp"] = pairs["direction"]*(pairs["entry_price_after"]/pairs["entry_price_before"]-1)*1e4
    pairs["former_winner_now_nonpositive"] = pairs["episode_net_return_before"].gt(0) & pairs["episode_net_return_after"].le(0)
    pairs["former_loser_now_positive"] = pairs["episode_net_return_before"].lt(0) & pairs["episode_net_return_after"].gt(0)
    denominator = int(finite.sum())
    contrast.update(
        same_executed=int(both.sum()),
        participation_changes=int(pairs["participation_changed"].sum()),
        same_executed_effect_per_mother_bp=pairs.loc[both & finite, "difference"].sum()*1e4/denominator if denominator else np.nan,
        participation_effect_per_mother_bp=pairs.loc[~both & finite, "difference"].sum()*1e4/denominator if denominator else np.nan,
        former_winners_now_nonpositive=int(pairs["former_winner_now_nonpositive"].sum()),
        former_losers_now_positive=int(pairs["former_loser_now_positive"].sum()),
        same_executed_entry_delay_median_minutes=pairs.loc[both, "entry_delay_minutes"].median(),
        same_executed_directional_entry_change_median_bp=pairs.loc[both, "directional_entry_change_bp"].median(),
    )
    return pairs, contrast


def flat_path_diagnostics(raw: pd.DataFrame, requests: pd.DataFrame) -> pd.DataFrame:
    """Describe known completed raw5 extrema during flat wait, never filter.

    For each emitted confirmation use raw opens >=base_decision_time and <new
    decision_time only: no confirmation-entry-bar extrema or later outcome.
    Stop touches are descriptive because cancellation was NOT a frozen rule.
    """
    rows = []
    for entry in requests.to_dict("records"):
        base, end = utc(entry["base_decision_time"]), utc(entry["decision_time"])
        part = raw.loc[raw["open_time"].ge(base) & raw["open_time"].lt(end)]
        touched = part["low"].le(entry["initial_stop"]) if entry["direction"] == 1 else part["high"].ge(entry["initial_stop"])
        rows.append({"event_id": entry["event_id"], "flat_minutes": (end-base).total_seconds()/60,
                     "flat_bars": len(part), "flat_stop_touched": bool(touched.any()),
                     "flat_first_stop_touch_open_time": part.loc[touched, "open_time"].min()})
    return pd.DataFrame(rows, columns=["event_id", "flat_minutes", "flat_bars", "flat_stop_touched", "flat_first_stop_touch_open_time"])


def run() -> None:
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Base config changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCE_PATHS]+[config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    parent, previous = ROOT/config["parent_results"], ROOT/config["previous_results"]
    for directory, key in ((parent, "inputs"), (previous, "previous_inputs")):
        for name, expected in config[key].items():
            if digest(directory/name) != expected:
                raise RuntimeError("Frozen source changed: "+name)
    results = EXPERIMENT/"results"
    if results.exists():
        raise RuntimeError("Preserve prior outcomes; no overwrite")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
               "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    study = Study(base, "development")
    management = study.featured(5, "SMA", 40)
    mothers = {"case": read_frame(parent/"original_mothers.csv.gz"),
               "control": read_frame(parent/"control_mothers.csv.gz")}
    regenerated = study.entries(base["baseline"])
    assert_saved_columns(regenerated, mothers["case"])
    prepared = {arm: {} for arm in ARMS}
    for label in mothers:
        original = read_frame(parent/("wait_k2_k1_stop_"+label+"_requests.csv.gz"))
        old_status = read_frame(parent/(label+"_wait_status.csv.gz"))
        contextual = attach_entry_colour_context(study.raw, management, original)
        prepared[ARMS[0]][label] = contextual, old_status
        delayed, status = build_realign_requests(study.raw, management, original,
                                                observed_through=study.raw["open_time"].max())
        delayed = attach_entry_colour_context(study.raw, management, delayed)
        if not delayed["ltf_entry_state"].eq("aligned").all():
            raise AssertionError("Every emitted alignment must be independently known aligned")
        new_status = merge_wait_statuses(old_status, status)
        prepared[ARMS[1]][label] = delayed, new_status
        write_csv(results/(label+"_original_context.csv.gz"), contextual)
        write_csv(results/(label+"_realign_requests.csv.gz"), delayed)
        write_csv(results/(label+"_realign_status.csv.gz"), new_status)
        write_csv(results/(label+"_flat_path_diagnostics.csv"), flat_path_diagnostics(study.raw, delayed))
    # All entry requests/statuses are frozen on disk before outcomes are joined.
    policy = {**next(p for p in base["exit_policies"] if p["id"] == config["policy_id"]),
              "exit_mode": config["exit_mode"]}
    rows, ledgers, all_trades, parity = [], {}, {}, {}
    folds = [f[0] for f in study.folds]
    for arm in ARMS:
        cohort_ledgers = {}
        for label, mother in mothers.items():
            requests, statuses = prepared[arm][label]
            replay = simulate_realign_requests(study, requests, policy)
            episodes = realign_episode_ledger(mother, statuses, replay)
            if arm == ARMS[0]:
                old_trade = read_frame(previous/("wait_k2_k1_stop__transition_colour_"+label+"_trades.csv.gz"))
                assert_saved_columns(old_trade, replay)
                old_episode = read_frame(previous/("wait_k2_k1_stop__transition_colour_"+label+"_episodes.csv.gz"))
                assert_saved_columns(old_episode, episodes)
                parity[label+"_old_trades"] = {"rows": len(old_trade), "columns": len(old_trade.columns)}
                parity[label+"_old_mothers"] = len(old_episode)
            else:
                aligned_ids = set(prepared[ARMS[0]][label][0].loc[lambda f: f["ltf_entry_state"].eq("aligned"), "event_id"])
                before = all_trades[ARMS[0]+"_"+label]
                assert_saved_columns(before.loc[before["event_id"].isin(aligned_ids)], replay.loc[replay["event_id"].isin(aligned_ids)])
                parity[label+"_initially_aligned"] = len(aligned_ids)
                valid = ~replay["outcome"].str.startswith("entry_")
                if not replay.loc[valid, "transition_initial_state"].eq("aligned").all():
                    raise AssertionError("Execution did not initialize confirmed alignment")
            all_trades[arm+"_"+label] = replay
            cohort_ledgers[label] = episodes
            write_csv(results/(arm+"_"+label+"_trades.csv.gz"), replay)
            write_csv(results/(arm+"_"+label+"_episodes.csv.gz"), episodes)
        cases, controls = cohort_ledgers["case"], cohort_ledgers["control"]
        trades = all_trades[arm+"_case"]
        pairs, match = matched_episodes(cases, controls)
        serial = single_pending_ledger(cases)
        ids = set(serial.loc[serial["portfolio_selected"], "event_id"])
        info, single = metrics(trades, folds), metrics(trades.loc[trades["event_id"].isin(ids)], folds)
        months = month_support(trades, folds)
        classified, diagnosis, tables = diagnose_frame(trades)
        write_csv(results/(arm+"_losing_trades.csv.gz"), classified.loc[classified["net_loser"]])
        for name, frame in tables.items():
            write_csv(results/(arm+"_diagnosis_"+name+".csv"), frame)
        write_csv(results/(arm+"_matched.csv"), pairs)
        write_csv(results/(arm+"_single_pending.csv.gz"), serial)
        state_table, states = state_diagnostics(trades)
        write_csv(results/(arm+"_entry_states.csv"), state_table)
        row = {"arm": arm, "metrics": info, "matched": match, "single_position": single,
               "serial_accepted_mothers": len(ids), "month_support": months,
               "mother_intention": describe(cases["episode_net_return"], cases["mother_decision_time"]),
               "unknown_mothers": int((~cases["observed"]).sum()),
               "mother_statuses": cases["episode_status"].value_counts().to_dict(),
               "control_statuses": controls["episode_status"].value_counts().to_dict(),
               "entry_states": states, "diagnosis": diagnosis}
        row["gates"] = development_gates(info, match, single, months, config)
        row["gates"]["complete_mother_evidence"] = row["unknown_mothers"] == 0 and controls["observed"].all()
        match_p = match["effect"]["month_cluster_p"]
        row["gates"]["matched_p"] = match_p is not None and np.isfinite(match_p) and match_p < .01
        rows.append(row)
        ledgers[arm] = cases
        print(json.dumps(clean({"arm": arm, "metrics": info, "matched": match, "statuses": row["mother_statuses"]})), flush=True)
    paired, contrast = opportunity_changes(ledgers[ARMS[0]], ledgers[ARMS[1]],
                                           all_trades[ARMS[0]+"_case"], all_trades[ARMS[1]+"_case"])
    original_context = prepared[ARMS[0]]["case"][0][["event_id", "ltf_entry_state"]].rename(columns={"ltf_entry_state": "original_k2_ltf_state"})
    paired = paired.merge(original_context, on="event_id", how="left", validate="one_to_one")
    write_csv(results/"paired_changes.csv.gz", paired)
    write_csv(results/"changed_mothers.csv", paired.loc[paired["difference"].abs().gt(1e-12)])
    candidate = rows[1]
    p = contrast["month_cluster_p"]
    candidate["gates"]["paired_effect"] = contrast["mean_bp"] > 0 and p is not None and np.isfinite(p) and p < .01
    summary = {"status": "mechanism_pass_requires_separate_verification" if all(candidate["gates"].values()) else "rejected_development_no_audit",
               "arms": rows, "contrast": contrast, "parity": parity,
               "source": study.source_receipt, "sources": sources,
               "config_sha256": digest(config_path), "lineage": config["lineage"],
               "audit_prices_loaded": False, "holdout_consumed": False,
               "production_eligible": False, "training_eligible": False}
    write_json(results/"summary.json", summary)
    print(json.dumps(clean({"status": summary["status"], "contrast": contrast, "parity": parity})), flush=True)


if __name__ == "__main__":
    run()
