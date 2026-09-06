"""Paired mother-intention test of first hourly K2, then its stop source.

All original K1 requests and three random mother controls are frozen before
waiting or outcomes. Source-derived K2 geometry uses completed hourly OHLC,
SMA40(HL2), ATR14, and strictly intermediate closes/colours (maximum eight
hours). Only outcomes inspect subsequent 5m prices. Non-entry is zero ONLY
after an observed terminal decision; missing paths remain unknown. The same
mother+72h absolute deadline applies to all arms. No audit or live entrypoint.

Inference resamples calendar-month clusters, not individual overlapping trades.
NumPy 2.0 Generator.choice: https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_k2 import build_entry_requests
from yoyo.evaluation.hourly_impulse_k2_matching import build_matching_frame, assign_controls
from yoyo.evaluation.hourly_impulse_context_research import committed_sources, month_support, development_gates
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, clean, cluster_p, digest, metrics, utc, write_csv, write_json
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4"
ARMS = ["direct_k1_stop", "wait_k2_k1_stop", "wait_k2_k2_stop"]
SOURCES = [
    "yoyo/data/hourly_impulse.py", "yoyo/data/hourly_impulse_k2.py",
    "yoyo/layers/l3_backtest/hourly_impulse.py", "yoyo/evaluation/hourly_impulse_research.py",
    "yoyo/evaluation/hourly_impulse_k2_matching.py", "yoyo/evaluation/hourly_impulse_k2_research.py",
    "yoyo/evaluation/hourly_impulse_context_research.py", "yoyo/evaluation/hourly_impulse_diagnostics.py",
]
KNOWN_NONENTRY = {"invalidated_wrong_close", "invalidated_ma_colour", "expired_no_k2"}


def describe(values: pd.Series, times: pd.Series, *, seed: int = 20260906, draws: int = 9999) -> dict:
    """Return descriptive bp effects and an approximate month-block 95% CI.

    Values are outcomes/differences only. Cluster membership is the original
    mother decision month; clusters are drawn with replacement, preserving all
    rows and their weights within each drawn month. No IID normality claim.
    Cross-month dependence and reused development data remain limitations.
    """
    frame = pd.DataFrame({"value": np.asarray(values, dtype=float), "time": pd.to_datetime(np.asarray(times), utc=True)})
    frame = frame.loc[np.isfinite(frame["value"]) & frame["time"].notna()].copy()
    if frame.empty:
        return {"n": 0, "mean_bp": np.nan, "ci95_bp": [np.nan, np.nan], "month_cluster_p": np.nan}
    frame["month"] = frame["time"].dt.strftime("%Y-%m")
    groups = frame.groupby("month")["value"].agg(["sum", "count"])
    ci = [np.nan, np.nan]
    if len(groups) >= 4:
        selected = np.random.default_rng(seed).choice(len(groups), size=(draws, len(groups)), replace=True)
        boot = groups["sum"].to_numpy()[selected].sum(axis=1) / groups["count"].to_numpy()[selected].sum(axis=1)
        ci = (np.quantile(boot, [.025, .975])*1e4).tolist()
    x = frame["value"]
    monthly_means = frame.groupby("month")["value"].mean()
    return {"n": len(frame), "mean_bp": x.mean()*1e4, "median_bp": x.median()*1e4,
            "sd_bp": x.std(ddof=1)*1e4, "iqr_bp": (x.quantile(.75)-x.quantile(.25))*1e4,
            "positive_fraction": x.gt(0).mean(), "zero_fraction": x.eq(0).mean(),
            "months": len(groups), "ci95_bp": ci,
            "month_cluster_p": cluster_p(x, frame["time"], monthly=True),
            "month_mean_lag1_autocorrelation": monthly_means.autocorr() if len(groups)>2 and monthly_means.std()>0 else np.nan,
            "inference": "exploratory month-cluster bootstrap/sign flip; not independent confirmation"}


def direct_requests(mothers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    entries = mothers.copy()
    entries["mother_decision_time"] = entries["decision_time"]
    entries["mother_signal_time"] = entries["signal_time"]
    entries["mother_deadline"] = entries["decision_time"] + pd.Timedelta(hours=72)
    entries["wait_hours"] = 0
    statuses = entries[["event_id", "mother_decision_time", "mother_signal_time", "mother_deadline", "wait_hours"]].copy()
    statuses["status"] = "request_emitted"
    statuses["terminal_time"] = statuses["mother_decision_time"]
    return entries, statuses


def simulate_requests(study: Study, entries: pd.DataFrame, policy: dict) -> pd.DataFrame:
    """Shorten the holding horizon by the already-observed wait, not the data."""
    pieces = []
    for fold, _, end in study.folds:
        subset = entries.loc[entries["fold"].eq(fold)] if len(entries) else entries
        if subset.empty:
            continue
        for delay, part in subset.groupby("wait_hours", sort=True):
            if not 0 <= delay <= 8 or int(delay) != delay:
                raise ValueError("Unexpected completed-hour delay")
            if not (part["decision_time"] + pd.Timedelta(hours=72-delay)).eq(part["mother_deadline"]).all():
                raise ValueError("All arms must share the mother's absolute horizon")
            pieces.append(simulate_events(study.raw, study.featured(5, "SMA", 40), part,
                                          {**study.config["execution"], **policy, "max_hours": 72-delay},
                                          end_exclusive=utc(end)))
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def episode_ledger(mothers: pd.DataFrame, statuses: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Join each original intention, preserving observed non-trade zeros."""
    if mothers["event_id"].duplicated().any() or statuses["event_id"].duplicated().any():
        raise ValueError("Mother/status identities must be unique")
    if set(mothers["event_id"]) != set(statuses["event_id"]):
        raise ValueError("Every original mother needs exactly one terminal record")
    result = mothers.copy().set_index("event_id")
    states = statuses.set_index("event_id")
    for col in states:
        result[col] = states[col]
    result["episode_status"] = result["status"]
    result["episode_net_return"] = np.nan
    result["executed"] = False
    result["completed_trade"] = False
    result["entry_time"] = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
    result["exit_time"] = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
    result["occupied_until"] = pd.to_datetime(result["terminal_time"], utc=True)
    result.loc[result["status"].isin(KNOWN_NONENTRY), "episode_net_return"] = 0.0
    if len(trades):
        if trades["event_id"].duplicated().any() or not set(trades["event_id"]).issubset(result.index):
            raise ValueError("Unexpected duplicate or foreign trade")
        for row in trades.to_dict("records"):
            idx, outcome = row["event_id"], row["outcome"]
            if result.at[idx, "status"] != "request_emitted":
                raise ValueError("Trade without a waiting request")
            result.at[idx, "episode_status"] = outcome
            for name in ("entry_time", "exit_time"):
                result.at[idx, name] = row[name]
            if row["closed"] and np.isfinite(row["net_return"]):
                result.at[idx, "episode_net_return"] = row["net_return"]
                result.at[idx, "completed_trade"] = True
                result.at[idx, "executed"] = True
                result.at[idx, "occupied_until"] = row["exit_time"]
            elif outcome == "entry_invalid_risk":
                result.at[idx, "episode_net_return"] = 0.0
            elif not outcome.startswith("entry_"):
                result.at[idx, "executed"] = True
    emitted = set(result.index[result["status"].eq("request_emitted")])
    if emitted != (set(trades["event_id"]) if len(trades) else set()):
        raise ValueError("Emitted requests must each have an execution result")
    result["observed"] = np.isfinite(result["episode_net_return"])
    # For unknown paths the serial diagnostic reserves the full horizon. It
    # cannot assume a missing-data position/pending order conveniently vanished.
    result.loc[~result["observed"], "occupied_until"] = result.loc[~result["observed"], "mother_deadline"]
    return result.reset_index()


def single_pending_ledger(episodes: pd.DataFrame) -> pd.DataFrame:
    """One pending setup OR position; occupancy starts at original mother time.

    Existing terminal event at the same instant is processed first. A hard stop
    recorded inside a 5m bar may only release its slot at the next grid open.
    The independent event replay supplies exits, never the selection decision.
    """
    result = episodes.copy()
    result["portfolio_selected"] = False
    result["portfolio_reason"] = "pending_or_position_busy"
    for _, part in result.groupby("fold", sort=False):
        free_at = pd.Timestamp.min.tz_localize("UTC")
        for idx, row in part.sort_values(["mother_decision_time", "event_id"]).iterrows():
            stamp = utc(row["mother_decision_time"])
            if stamp < free_at:
                continue
            result.at[idx, "portfolio_selected"] = True
            result.at[idx, "portfolio_reason"] = "accepted_mother"
            terminal = utc(row["occupied_until"])
            free_at = terminal.ceil("5min")
    return result


def matched_episodes(cases: pd.DataFrame, controls: pd.DataFrame, count: int = 3) -> tuple[pd.DataFrame, dict]:
    pairs = cases[["event_id", "mother_decision_time", "fold", "episode_net_return"]].copy()
    pairs.rename(columns={"episode_net_return": "event_net_return"}, inplace=True)
    if len(controls):
        all_counts = controls.groupby("parent_event_id").size()
        finite = controls.loc[controls["observed"]]
        grouped = finite.groupby("parent_event_id")["episode_net_return"].agg(["count", "mean"])
        means = grouped.loc[grouped["count"].eq(count), "mean"]
    else:
        all_counts, means = pd.Series(dtype=float), pd.Series(dtype=float)
    pairs["assigned_controls"] = pairs["event_id"].map(all_counts).fillna(0).astype(int)
    if not pairs["assigned_controls"].isin([0, count]).all():
        raise ValueError("Matching must be all three controls or no assignment")
    pairs["control_mean_return"] = pairs["event_id"].map(means)
    pairs["excess"] = pairs["event_net_return"] - pairs["control_mean_return"]
    paired = pairs.loc[np.isfinite(pairs["excess"])]
    effect = describe(paired["excess"], paired["mother_decision_time"])
    info = {"paired_events": len(paired), "mother_events": len(cases),
            "assignment_coverage": pairs["assigned_controls"].eq(count).mean() if len(pairs) else 0,
            "coverage": len(paired)/max(1, len(cases)), "mean_excess_bp": effect["mean_bp"],
            "event_mean_net_bp": paired["event_net_return"].mean()*1e4,
            "control_mean_net_bp": paired["control_mean_return"].mean()*1e4, "effect": effect,
            "control_mothers": len(controls), "control_executed": int(controls["executed"].sum()) if len(controls) else 0}
    return pairs, info


def compare_episodes(previous: pd.DataFrame, candidate: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if set(previous["event_id"]) != set(candidate["event_id"]):
        raise ValueError("Paired arms must preserve all mother identities")
    columns = ["event_id", "mother_decision_time", "episode_net_return", "executed", "episode_status"]
    joined = previous[columns].merge(candidate[columns], on=["event_id", "mother_decision_time"], suffixes=("_before", "_after"), validate="one_to_one")
    joined["difference"] = joined["episode_net_return_after"]-joined["episode_net_return_before"]
    joined["missed_net_winner"] = joined["episode_net_return_before"].gt(0) & ~joined["executed_after"] & joined["episode_net_return_after"].eq(0)
    joined["avoided_net_loser"] = joined["episode_net_return_before"].lt(0) & ~joined["executed_after"] & joined["episode_net_return_after"].eq(0)
    info = describe(joined["difference"], joined["mother_decision_time"])
    info.update(total_mothers=len(joined), unknown_pairs=int(joined["difference"].isna().sum()),
                missed_net_winners=int(joined["missed_net_winner"].sum()),
                missed_winner_total_bp=joined.loc[joined["missed_net_winner"], "episode_net_return_before"].sum()*1e4,
                avoided_net_losers=int(joined["avoided_net_loser"].sum()),
                avoided_loss_total_bp=-joined.loc[joined["avoided_net_loser"], "episode_net_return_before"].sum()*1e4,
                improved=int(joined["difference"].gt(1e-12).sum()), worsened=int(joined["difference"].lt(-1e-12).sum()),
                unchanged=int(joined["difference"].abs().le(1e-12).sum()))
    return joined, info


def verify_config(config: dict, base: dict) -> None:
    if config["arms"] != ARMS or config["policy_id"] != "5m_native40":
        raise RuntimeError("Frozen three arms/exit policy changed")
    if config["wait"] != {"gap_min": 1, "gap_max": 8, "source_geometry": True, "cancel_on_k1_extreme": False, "require_k2_colour": False}:
        raise RuntimeError("Source-derived waiting contract changed")
    if base["execution"]["max_hours"] != 72 or base["execution"]["cost_fraction"] != .002:
        raise RuntimeError("Fixed horizon/cost changed")
    if config["matching"]["count_per_trade"] != 3 or not config["no_audit_entry_point"]:
        raise RuntimeError("Frozen mother assignment/development-only contract changed")


def run() -> None:
    config_path = EXPERIMENT / "config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT / config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Parent experiment changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES] + [config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    results = EXPERIMENT / "results"
    if results.exists():
        raise RuntimeError("Preserve prior experiment outcomes; no overwrite")
    results.mkdir()
    write_json(results/"started.json", {"sources": sources, "at": pd.Timestamp.now(tz="UTC"),
               "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    study = Study(base, "development")
    mothers = study.entries(base["baseline"])
    hourly, management = study.featured(60, "SMA", 40), study.featured(5, "SMA", 40)
    matching = build_matching_frame(study.raw, hourly, management, mothers)
    control_parts, assignment_parts, receipts = [], [], []
    for fold, _, end in study.folds:
        controls, assignments, receipt = assign_controls(mothers.loc[mothers["fold"].eq(fold)], matching,
                   count=3, seed=config["matching"]["seed"], end_exclusive=utc(end), embargo_hours=72)
        control_parts.append(controls)
        assignment_parts.append(assignments)
        receipts.append(receipt)
    control_mothers = pd.concat(control_parts, ignore_index=True)
    write_csv(results/"original_mothers.csv.gz", mothers)
    write_csv(results/"control_mothers.csv.gz", control_mothers)
    write_csv(results/"assignments.csv", pd.concat(assignment_parts, ignore_index=True))
    write_json(results/"assignment_receipt.json", receipts)
    # Assignment is complete and persisted before any waiting/outcome labels.
    policy = next(p for p in base["exit_policies"] if p["id"] == config["policy_id"])
    prepared = {}
    for label, mother_set in [("case", mothers), ("control", control_mothers)]:
        direct, direct_status = direct_requests(mother_set)
        waiting, wait_status = build_entry_requests(hourly, study.raw, mother_set,
                             observed_through=utc(study.folds[-1][2]), gap_min=1, gap_max=8)
        alternate = waiting.copy()
        if len(alternate):
            alternate["initial_stop"] = alternate["k2_initial_stop"]
        prepared[label] = [(direct, direct_status), (waiting, wait_status), (alternate, wait_status)]
        write_csv(results/(label+"_wait_status.csv.gz"), wait_status)
    rows, ledgers = [], {}
    folds = [f[0] for f in study.folds]
    for i, arm in enumerate(ARMS):
        cases, controls, trades = None, None, None
        for label, mother_set in [("case", mothers), ("control", control_mothers)]:
            requests, statuses = prepared[label][i]
            replay = simulate_requests(study, requests, policy)
            episodes = episode_ledger(mother_set, statuses, replay)
            write_csv(results/(arm+"_"+label+"_requests.csv.gz"), requests)
            write_csv(results/(arm+"_"+label+"_trades.csv.gz"), replay)
            write_csv(results/(arm+"_"+label+"_episodes.csv.gz"), episodes)
            if label == "case":
                cases, trades = episodes, replay
            else:
                controls = episodes
        pairs, match = matched_episodes(cases, controls)
        write_csv(results/(arm+"_matched_pairs.csv"), pairs)
        serial = single_pending_ledger(cases)
        write_csv(results/(arm+"_single_pending.csv.gz"), serial)
        selected_ids = set(serial.loc[serial["portfolio_selected"], "event_id"])
        serial_trades = trades.loc[trades["event_id"].isin(selected_ids)] if len(trades) else trades
        info, single, months = metrics(trades, folds), metrics(serial_trades, folds), month_support(trades, folds)
        row = {"arm": arm, "metrics": info, "matched": match, "single_pending_trades": single,
               "month_support": months, "mother_intention": describe(cases["episode_net_return"], cases["mother_decision_time"]),
               "mother_status_counts": cases["episode_status"].value_counts().to_dict(),
               "unknown_mothers": int((~cases["observed"]).sum()), "executed_mothers": int(cases["executed"].sum()),
               "serial_accepted_mothers": len(selected_ids), "serial_blocked_mothers": len(cases)-len(selected_ids),
               "serial_intention": describe(serial.loc[serial["portfolio_selected"], "episode_net_return"], serial.loc[serial["portfolio_selected"], "mother_decision_time"]),
               "directions": {str(d): metrics(trades.loc[trades["direction"].eq(d)], folds) for d in (1,-1)} if len(trades) else {}}
        row["gates"] = development_gates(info, match, single, months, config)
        row["gates"]["complete_mother_evidence"] = row["unknown_mothers"] == 0
        if len(trades):
            classified, diagnosis, tables = diagnose_frame(trades)
            write_csv(results/(arm+"_classified.csv.gz"), classified)
            write_csv(results/(arm+"_losing_trades.csv.gz"), classified.loc[classified["net_loser"]])
            row["diagnosis"] = diagnosis
            for name, frame in tables.items():
                write_csv(results/(arm+"_diagnosis_"+name+".csv"), frame)
        rows.append(row)
        ledgers[arm] = cases
        print(json.dumps(clean({"arm": arm, "metrics": info, "matched": match, "gates": row["gates"]})), flush=True)
    contrasts = {}
    for before, after in zip(ARMS[:-1], ARMS[1:]):
        pairs, contrast = compare_episodes(ledgers[before], ledgers[after])
        contrasts[after+"_minus_"+before] = contrast
        write_csv(results/(after+"_paired_change.csv.gz"), pairs)
    # Holm for the two predeclared paired mechanism contrasts. This does not
    # repair earlier reuse/search on 2023--2024, so no confirmatory claim follows.
    previous = 0.0
    for rank, key in enumerate(sorted(contrasts, key=lambda k: contrasts[k]["month_cluster_p"] if np.isfinite(contrasts[k]["month_cluster_p"]) else 1)):
        p = contrasts[key]["month_cluster_p"]
        previous = max(previous, min(1.0, (2-rank)*(p if np.isfinite(p) else 1)))
        contrasts[key]["holm_two_p"] = previous
    passed = [r["arm"] for r in rows[1:] if all(r["gates"].values())]
    summary = {"status": "development_pass_requires_new_verification" if passed else "rejected_development_no_audit",
               "original_mothers": len(mothers), "control_mothers": len(control_mothers), "source": study.source_receipt,
               "config_sha256": digest(config_path), "sources": sources, "arms": rows, "contrasts": contrasts,
               "passing_arms": passed, "lineage": config["lineage"], "audit_prices_loaded": False,
               "holdout_consumed": False, "production_eligible": False, "training_eligible": False}
    write_json(results/"summary.json", summary)
    print(json.dumps(clean({"status": summary["status"], "contrasts": contrasts})), flush=True)


if __name__ == "__main__":
    run()
