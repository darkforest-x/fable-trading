"""Frozen chronological research for hourly impulses and lower-timeframe exits.

Features use completed native bars only. Entry shape is based on real OHLC;
MA colour is HL2 versus MA. Labels alone inspect the subsequent 72 hours.
Random controls use completed-hour decision boundaries, prior-hour ATR and
its causal preceding-720-hour percentile, the same direction/time-of-day
bucket and the same last-known management colour alignment. No model is
trained. A frozen 2025--2026 transport audit is not globally pristine data.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import add_features, make_entries, resample_complete
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events, single_position_ledger


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1"


def utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True)


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(path, index=False, compression=compression)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_source(config: dict, phase_end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Validate receipt and timestamp-only boundary before loading prices."""
    source = config["source"]
    path = ROOT / source["path"]
    audit = json.loads((ROOT / source["audit"]).read_text())
    if audit.get("holdout_ohlcv_rows_materialized") != 0:
        raise RuntimeError("Archive receipt does not attest a pre-holdout physical source")
    if utc(audit["last_time"]) >= utc("2026-05-04"):
        raise RuntimeError("Archive receipt reaches repository holdout")
    times = pd.read_csv(path, usecols=["open_time"])
    stamps = pd.to_datetime(times["open_time"], utc=True)
    if stamps.max() >= utc(source["end_exclusive"]) or not stamps.is_monotonic_increasing or stamps.duplicated().any():
        raise RuntimeError("Physical source timestamp contract failed")
    sha = digest(path)
    if sha != source["sha256"]:
        raise RuntimeError("Physical source hash changed")
    # Read only the required phase prefix after boundary proof. Later outcomes
    # cannot enter even the feature-frame materialisation of development.
    count = int((stamps < phase_end).sum())
    raw = pd.read_csv(path, nrows=count, usecols=["open_time", "open", "high", "low", "close", "volume"])
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
    return resample_complete(raw, 5), {
        "sha256": sha, "physical_rows": len(stamps), "phase_rows": len(raw),
        "phase_price_last_open": raw["open_time"].max(), "physical_last_open": stamps.max(),
        "holdout_price_rows": 0, "timestamp_preflight_before_price_hash": True,
    }


def cluster_p(values: pd.Series, times: pd.Series, seed: int = 20260906, monthly: bool = False) -> float:
    """One-sided calendar-block sign flip; cross-boundary dependence is approximate."""
    # https://numpy.org/doc/2.0/reference/generated/numpy.isfinite.html
    finite = np.isfinite(pd.to_numeric(values, errors="coerce"))
    values = pd.Series(np.asarray(values)[finite], dtype=float)
    stamps = pd.to_datetime(np.asarray(times)[finite], utc=True)
    if len(values) < 2:
        return float("nan")
    groups = pd.Series(stamps.strftime("%Y-%m" if monthly else "%G-%V"))
    sums = values.groupby(groups).sum().to_numpy()
    if len(sums) < 4:
        return float("nan")
    observed = sums.sum()
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1, 1], size=(9999, len(sums)))
    return float((1 + np.count_nonzero((signs * sums).sum(axis=1) >= observed)) / 10000)


def auc(y: pd.Series, scores: pd.Series) -> float:
    good = np.isfinite(pd.to_numeric(scores, errors="coerce")) & y.notna()
    labels = y[good].to_numpy(dtype=bool)
    ranks = scores[good].rank().to_numpy()
    pos, neg = int(labels.sum()), int((~labels).sum())
    return float((ranks[labels].sum() - pos * (pos + 1) / 2) / (pos * neg)) if pos and neg else float("nan")


def metrics(trades: pd.DataFrame, fold_names: list[str]) -> dict:
    t = trades.loc[trades["closed"].eq(True) & np.isfinite(pd.to_numeric(trades["net_return"], errors="coerce"))].copy() if len(trades) else trades.copy()
    rejected = int(trades["outcome"].str.startswith("entry_").sum()) if len(trades) else 0
    censored = int((~trades["closed"].eq(True) & ~trades["outcome"].str.startswith("entry_")).sum()) if len(trades) else 0
    attrition = {"excluded_results":len(trades)-len(t),"rejected_entries":rejected,"censored":censored}
    if not len(t):
        return {"events": 0, **attrition, "mean_net_bp": float("nan"), "robust_score_bp": -1e9,
                "minimum_fold_events": 0, "worst_fold_bp": -1e9, "eligible": False}
    x = t["net_return"].to_numpy(dtype=float)
    folds = t.groupby("fold")["net_return"].agg(["count", "mean"]).reindex(fold_names)
    means = folds["mean"].to_numpy() * 10000
    score = float(np.median(means) - 0.5 * np.std(means)) if np.isfinite(means).all() else -1e9
    ordered = np.sort(x)[::-1]
    winners = x[x > 0].sum()
    losses = -x[x < 0].sum()
    score_col = t["range_atr"] if "range_atr" in t else pd.Series(np.nan, index=t.index)
    top = t.loc[score_col.nlargest(max(1, int(np.ceil(len(t) * .1)))).index]
    return {
        "events": len(t), **attrition, "mean_net_bp": x.mean() * 10000,
        "median_net_bp": np.median(x) * 10000, "profit_factor": winners / losses if losses else float("inf"),
        "win_rate": float((x > 0).mean()), "p95_net_bp": float(np.quantile(x, .95) * 10000),
        "worst_fold_bp": float(np.nanmin(means)), "positive_folds": int((means > 0).sum()),
        "minimum_fold_events": int(folds["count"].fillna(0).min()), "folds": clean(folds.reset_index().to_dict("records")),
        "robust_score_bp": score, "net_week_cluster_p": cluster_p(t["net_return"], t["entry_time"]),
        "net_month_cluster_p": cluster_p(t["net_return"], t["entry_time"], monthly=True),
        "extra_10bp_mean_net_bp": x.mean() * 10000 - 10,
        "leave_top_two_mean_net_bp": ordered[2:].mean() * 10000 if len(x) > 2 else float("nan"),
        "range_feature_auc": auc(t["net_return"].gt(0), score_col),
        "range_top_decile_net_bp": top["net_return"].mean() * 10000,
        "range_top_decile_gross_bp": top["gross_return"].mean() * 10000,
        "mean_hold_hours": t["hold_minutes"].mean() / 60,
    }


class Study:
    def __init__(self, config: dict, phase: str):
        self.config = config
        self.folds = config["development_folds" if phase == "development" else "audit_folds"]
        self.raw, self.source_receipt = load_source(config, utc(self.folds[-1][2]))
        self.frames: dict[tuple, pd.DataFrame] = {}
        self.results: dict[str, tuple[pd.DataFrame, dict]] = {}

    def featured(self, minutes: int, kind: str, length: int) -> pd.DataFrame:
        key = (minutes, kind, length)
        if key not in self.frames:
            self.frames[key] = add_features(resample_complete(self.raw, minutes), kind, length)
        return self.frames[key]

    def entries(self, params: dict) -> pd.DataFrame:
        h = self.featured(60, params["ma_kind"], params["ma_length"])
        events = make_entries(h, {k:v for k,v in params.items() if k not in {"ma_kind", "ma_length"}})
        if events.empty:
            events["fold"] = pd.Series(dtype=str)
            return events
        rows = []
        for name, start, end in self.folds:
            # The entire 72h label fits inside the fold independently of exit.
            keep = events["decision_time"].ge(utc(start)) & events["decision_time"].lt(utc(end) - pd.Timedelta(hours=self.config["execution"]["max_hours"]))
            rows.append(events.loc[keep].assign(fold=name))
        return pd.concat(rows, ignore_index=True)

    def evaluate(self, params: dict, policy: dict) -> tuple[pd.DataFrame, dict]:
        key = json.dumps([params, policy], sort_keys=True)
        if key in self.results:
            return self.results[key]
        management = self.featured(policy["management_minutes"], policy["ma_kind"], policy["ma_length"])
        entries = self.entries(params)
        result = []
        full_policy = {**self.config["execution"], **policy}
        for fold, _, end in self.folds:
            subset = entries.loc[entries["fold"].eq(fold)]
            if len(subset):
                result.append(simulate_events(self.raw, management, subset, full_policy, end_exclusive=utc(end)))
        trades = pd.concat(result, ignore_index=True) if result else pd.DataFrame()
        info = metrics(trades, [f[0] for f in self.folds])
        s = self.config["selection"]
        info["eligible"] = info["events"] >= s["minimum_total"] and info["minimum_fold_events"] >= s["minimum_per_fold"]
        self.results[key] = trades, info
        return trades, info

    def matched(self, trades: pd.DataFrame, policy: dict, params: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        """Match outcomes only after hashing causal control candidates; no fallbacks."""
        if not len(trades):
            return pd.DataFrame(), pd.DataFrame(), {"coverage": 0, "mean_excess_bp": None}
        h = self.featured(60, params["ma_kind"], params["ma_length"]).copy()
        atr_name = "atr" if "atr" in h else "atr14"
        h["signal_atr"] = h[atr_name]
        h["decision_time"] = h["open_time"] + pd.Timedelta(hours=1)
        h["atr_fraction"] = h[atr_name] / h["close"]
        for q in (1/3, 2/3):
            h[f"cut{q}"] = h.groupby("segment_id")["atr_fraction"].transform(lambda s: s.shift(1).rolling(720, min_periods=168).quantile(q))
        h["vol_bucket"] = (h["atr_fraction"] > h["cut0.3333333333333333"]).astype(int) + (h["atr_fraction"] > h["cut0.6666666666666666"]).astype(int)
        h = h.loc[h["cut0.3333333333333333"].notna()].copy()
        management = self.featured(policy["management_minutes"], policy["ma_kind"], policy["ma_length"])
        colour = management[["open_time", "ma_side"]].copy()
        colour["available"] = colour["open_time"] + pd.Timedelta(minutes=policy["management_minutes"])
        # Backward selects only a management bar already completed at decision.
        # https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
        h = pd.merge_asof(h.sort_values("decision_time"), colour[["available", "ma_side"]].rename(columns={"ma_side":"ltf_side"}).sort_values("available"), left_on="decision_time", right_on="available", direction="backward")
        h["month"] = h["decision_time"].dt.strftime("%Y-%m")
        h["session"] = h["decision_time"].dt.hour // 6
        opens = self.raw.set_index("open_time")["open"]
        h["entry_open"] = h["decision_time"].map(opens)
        h = h.loc[h["entry_open"].notna() & h["signal_atr"].gt(0)].copy()
        lookup = h.set_index("decision_time")
        # Exclude raw crossings already known at candidate entry: current and
        # preceding hour only. Never inspect a future crossing to pick controls.
        crossed = h.loc[((h["open"] < h["ma"]) & (h["close"] > h["ma"])) | ((h["open"] > h["ma"]) & (h["close"] < h["ma"])), "decision_time"]
        banned = set()
        for stamp in crossed:
            for offset in (0, 1):
                banned.add(stamp + pd.Timedelta(hours=offset))
        pools = []
        for name, start, end in self.folds:
            keep = h["decision_time"].ge(utc(start)) & h["decision_time"].lt(utc(end) - pd.Timedelta(hours=self.config["execution"]["max_hours"])) & ~h["decision_time"].isin(banned)
            pools.append(h.loc[keep].assign(fold=name))
        pool = pd.concat(pools, ignore_index=True)
        requests, pairs = [], []
        used_control_times = set()
        count = self.config["matching"]["count_per_trade"]
        for row in trades.loc[trades["closed"].eq(True)].to_dict("records"):
            stamp = utc(row["entry_time"])
            if stamp not in lookup.index:
                pairs.append({"event_id":row["event_id"],"match_status":"no_causal_vol_bucket"})
                continue
            own = lookup.loc[stamp]
            match = pool.loc[pool["month"].eq(own["month"]) & pool["session"].eq(own["session"]) & pool["vol_bucket"].eq(own["vol_bucket"]) & pool["ltf_side"].eq(own["ltf_side"]) & ~pool["decision_time"].isin(used_control_times)]
            candidates = match.to_dict("records")
            candidates.sort(key=lambda c: hashlib.sha256(f"20260906|{row['event_id']}|{c['decision_time']}".encode()).hexdigest())
            if len(candidates) < count:
                pairs.append({"event_id":row["event_id"],"match_status":"insufficient_exact_controls","available":len(candidates)})
                continue
            for j, c in enumerate(candidates[:count]):
                direction = row["direction"]
                control = {k:c[k] for k in ("ma", "ma_side", "body_ratio", "range_atr", "volume_ratio", "cross_count24", "efficiency24")}
                control.update(event_id=f"{row['event_id']}::control{j}", parent_event_id=row["event_id"], decision_time=c["decision_time"], signal_time=c["open_time"], signal_atr=c["signal_atr"], initial_stop=c["entry_open"]-direction*row["risk_atr"]*c["signal_atr"], fold=c["fold"], direction=direction, transferred_risk_atr=row["risk_atr"], ma_slope_atr=direction*c["ma_slope_atr"], close_location=c["long_close_location" if direction==1 else "short_close_location"], extension_atr=direction*(c["close"]-c["ma"])/c["signal_atr"])
                control.update({f"signal_{k}":c[k] for k in ("open", "high", "low", "close")})
                requests.append(control)
                used_control_times.add(c["decision_time"])
            pairs.append({"event_id":row["event_id"],"entry_time":stamp,"fold":row["fold"],"match_status":"matched","event_net_return":row["net_return"]})
        controls = []
        if requests:
            req = pd.DataFrame(requests)
            for fold, _, end in self.folds:
                subset = req.loc[req["fold"].eq(fold)]
                if len(subset):
                    controls.append(simulate_events(self.raw, management, subset, {**self.config["execution"], **policy}, end_exclusive=utc(end)))
        control_frame = pd.concat(controls, ignore_index=True) if controls else pd.DataFrame()
        pair_frame = pd.DataFrame(pairs)
        if len(control_frame):
            closed = control_frame.loc[control_frame["closed"].eq(True) & np.isfinite(control_frame["net_return"])]
            means = closed.groupby("parent_event_id")["net_return"].agg(["count", "mean"])
            eligible = means.loc[means["count"].eq(count), "mean"]
            pair_frame["control_mean_return"] = pair_frame["event_id"].map(eligible)
            pair_frame["excess"] = pair_frame["event_net_return"] - pair_frame["control_mean_return"]
            matched = pair_frame.loc[np.isfinite(pair_frame["excess"])]
            excess = matched["excess"].mean()*10000
            p = cluster_p(matched["excess"], matched["entry_time"], monthly=True)
            coverage = len(matched) / max(1, len(trades.loc[trades["closed"].eq(True)]))
            control_mean = matched["control_mean_return"].mean()*10000
            unique = closed["entry_time"].nunique()
        else:
            excess, p, coverage, control_mean, unique = None, None, 0, None, 0
        return control_frame, pair_frame, {"coverage":coverage,"mean_excess_bp":excess,"control_mean_net_bp":control_mean,"month_cluster_p":p,"control_rows":len(control_frame),"unique_control_times":unique,"control_time_reuse_allowed":False}


def improvement(candidate: dict, incumbent: dict, config: dict) -> bool:
    s = config["selection"]
    return bool(candidate["eligible"] and candidate["robust_score_bp"] >= incumbent["robust_score_bp"] + s["move_margin_bp"] and candidate["worst_fold_bp"] >= incumbent["worst_fold_bp"]-s["worst_fold_tolerance_bp"])


def run_development(config: dict, results: Path) -> None:
    if (results / "selection.json").exists():
        raise RuntimeError("Selection already exists; preserve experiment history")
    study = Study(config, "development")
    params = deepcopy(config["baseline"])
    policy = deepcopy(config["exit_policies"][0])
    baseline, incumbent = study.evaluate(params, policy)
    write_csv(results / "development_baseline_trades.csv.gz", baseline)
    search_rows = []
    for candidate_policy in config["exit_policies"]:
        trades, info = study.evaluate(params, candidate_policy)
        write_csv(results / f"development_exit_{candidate_policy['id']}_trades.csv.gz", trades)
        search_rows.append({"stage":"exit","value":candidate_policy["id"],**info})
        print(json.dumps(clean({"stage":"exit","id":candidate_policy["id"],**info})), flush=True)
    ranked = sorted(zip(config["exit_policies"], search_rows), key=lambda x: (x[1]["robust_score_bp"], x[1]["worst_fold_bp"], x[1]["events"]), reverse=True)
    for p, row in ranked:
        if p.get("selection_eligible", True) and improvement(row, incumbent, config):
            policy, incumbent = deepcopy(p), row
            break
    for coordinate in config["coordinate_pass"]:
        options = []
        for value in coordinate["values"]:
            candidate = {**params, coordinate["key"]:value}
            _, info = study.evaluate(candidate, policy)
            row = {"stage":coordinate["key"],"value":value,**info}
            search_rows.append(row)
            options.append((value, info))
        options.sort(key=lambda x: (x[1]["robust_score_bp"],x[1]["worst_fold_bp"],x[1]["events"]), reverse=True)
        moved = False
        for value, info in options:
            if improvement(info, incumbent, config):
                params[coordinate["key"]] = value
                incumbent = info
                moved = True
                break
        print(json.dumps(clean({"coordinate":coordinate["key"],"moved":moved,"chosen":params[coordinate["key"]],"events":incumbent["events"],"net_bp":incumbent["mean_net_bp"],"robust_bp":incumbent["robust_score_bp"]})), flush=True)
    candidate_trades, selected_metrics = study.evaluate(params, policy)
    controls, pairs, matched = study.matched(candidate_trades, policy, params)
    baseline_controls, baseline_pairs, baseline_matched = study.matched(baseline, config["exit_policies"][0], config["baseline"])
    write_csv(results / "development_search.csv", pd.DataFrame(search_rows).drop(columns=["folds"], errors="ignore"))
    write_csv(results / "development_candidate_trades.csv.gz", candidate_trades)
    write_csv(results / "development_controls.csv.gz", controls)
    write_csv(results / "development_matched_pairs.csv", pairs)
    write_csv(results / "development_baseline_controls.csv.gz", baseline_controls)
    write_csv(results / "development_baseline_matched_pairs.csv", baseline_pairs)
    selection = {"config_sha256":digest(EXPERIMENT / "config.json"),"entry":params,"policy":policy,"metrics":selected_metrics,"matched":matched,"baseline_metrics":metrics(baseline,[f[0] for f in study.folds]),"baseline_matched":baseline_matched,"source":study.source_receipt,"candidates_evaluated":len(search_rows),"audit_results_seen":False,"status":"frozen_candidate_for_transport_not_profit_claim"}
    write_json(results / "selection.json", selection)
    print(json.dumps(clean(selection), indent=2), flush=True)


def run_audit(config: dict, results: Path) -> None:
    selection_path = results / "selection.json"
    if not selection_path.exists() or (results / "audit_started.json").exists():
        raise RuntimeError("Audit needs frozen selection and is one-shot")
    relative = str(selection_path.relative_to(ROOT))
    committed = subprocess.run(["git","show",f"HEAD:{relative}"],cwd=ROOT,capture_output=True,check=True).stdout
    if committed != selection_path.read_bytes():
        raise RuntimeError("Commit exact selection before opening audit")
    selection = json.loads(committed)
    if digest(EXPERIMENT / "config.json") != selection["config_sha256"]:
        raise RuntimeError("Config changed after development")
    write_json(results / "audit_started.json", {"selection_sha256":digest(selection_path),"audit_use":1,"started_at":pd.Timestamp.now(tz="UTC")})
    study = Study(config,"audit")
    summary = {"source":study.source_receipt,"audit_use":1,"lineage":config["lineage"]}
    for label, params, policy in [("baseline",config["baseline"],config["exit_policies"][0]),("candidate",selection["entry"],selection["policy"])]:
        trades, info = study.evaluate(params,policy)
        controls, pairs, matched = study.matched(trades,policy,params)
        single = single_position_ledger(trades) if len(trades) else trades
        for suffix, frame in [("trades",trades),("controls",controls),("single_position",single)]:
            write_csv(results / f"audit_{label}_{suffix}.csv.gz",frame)
        write_csv(results / f"audit_{label}_matched_pairs.csv",pairs)
        selected_single = single.loc[single["portfolio_selected"].eq(True)] if len(single) else single
        single_info = metrics(selected_single,[f[0] for f in study.folds])
        summary[label] = {"metrics":info,"matched":matched,"single_position":single_info}
        print(json.dumps(clean({"audit":label,**summary[label]})),flush=True)
    c = summary["candidate"]
    m, r, s = c["metrics"], c["matched"], c["single_position"]
    gates = config["audit_gates"]
    checks = {
        "samples":m["events"]>=gates["closed_events"] and m["minimum_fold_events"]>=gates["minimum_per_fold"],
        "mean":m["mean_net_bp"]>0,
        "profit_factor":m.get("profit_factor",0)>gates["profit_factor"],
        "both_full_halfyears_positive":all(row.get("mean") is not None and row["mean"]>0 for row in m.get("folds",[])[:2]) and len(m.get("folds",[]))>=2,
        "net_cluster_p":np.isfinite(m.get("net_month_cluster_p",np.nan)) and m["net_month_cluster_p"]<.01 and m["net_week_cluster_p"]<.01,
        "matched_coverage":r["coverage"]>=gates["matched_coverage"],
        "matched_positive":r["mean_excess_bp"] is not None and r["mean_excess_bp"]>0,
        "matched_p":r.get("month_cluster_p") is not None and r["month_cluster_p"]<.01,
        "single_position":s["mean_net_bp"]>0,
        "cost_stress":m["mean_net_bp"]>10,
    }
    summary["gate_checks"] = checks
    summary["status"] = "passed_transport_requires_new_prospective_data" if all(checks.values()) else "rejected_transport"
    write_json(results / "audit_summary.json",summary)
    print(json.dumps(clean(summary),indent=2),flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase",choices=["development","audit"],required=True)
    args = parser.parse_args()
    config = json.loads((EXPERIMENT / "config.json").read_text())
    results = EXPERIMENT / "results"
    results.mkdir(exist_ok=True)
    if args.phase == "development":
        run_development(config,results)
    else:
        run_audit(config,results)


if __name__ == "__main__":
    main()
