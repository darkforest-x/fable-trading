"""Receipt-bound aggregate report for the V12.6 H1 recheck ledgers.

This CLI reads only a completed ``spike_v126_htf_recheck`` runner directory;
it never opens price archives, reruns decisions, or changes an execution rule.
Every per-symbol receipt and gzip ledger is hash checked before aggregation.
The resulting drawdown is explicitly an exit-ordered event-R series, not an
account equity curve.  Native TradingView parity remains false by design.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v10_4_study import SPLIT


TABLES = ("decisions", "trades", "statuses", "controls", "reference_boxes", "breaks")
ARMS = ("baseline", "joint_h1_recheck")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_gzip(path: Path) -> pd.DataFrame:
    """Read an empty gzip ledger as an empty frame, preserving receipt evidence."""
    try:
        return pd.read_csv(path, compression="gzip")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_run(root: Path) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    """Verify manifest/stream receipts completely before opening any ledger."""
    identity, manifest = _json(root / "identity.json"), _json(root / "manifest.json")
    if not manifest.get("complete"):
        raise ValueError("input manifest is not complete")
    symbols = list(manifest.get("symbols", []))
    receipts = manifest.get("receipts", {})
    if not symbols or set(symbols) != set(receipts):
        raise ValueError("manifest symbols/receipts inventory mismatch")
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if manifest.get("run_identity") != run_hash:
        raise ValueError("manifest run identity mismatch")
    input_keys = set(identity.get("inputs", {}))
    if set(identity.get("symbols", [])) != set(symbols) or input_keys != set(symbols):
        raise ValueError("identity/manifest/input symbol inventory mismatch")
    frames: dict[str, list[pd.DataFrame]] = {name: [] for name in TABLES}
    expected = {f"{name}.csv.gz" for name in TABLES}
    for symbol in symbols:
        directory = root / "streams" / symbol
        receipt_path = directory / "receipt.json"
        if not receipt_path.is_file() or sha(receipt_path) != receipts[symbol]:
            raise ValueError(f"stream receipt drift: {symbol}")
        receipt = _json(receipt_path)
        if (receipt.get("status") != "complete" or receipt.get("run_identity") != run_hash
                or receipt.get("input_sha256") != identity["inputs"][symbol]):
            raise ValueError(f"stream receipt identity mismatch: {symbol}")
        if set(receipt.get("files", {})) != expected:
            raise ValueError(f"stream ledger inventory mismatch: {symbol}")
        for filename, expected_sha in receipt["files"].items():
            path = directory / filename
            if not path.is_file() or sha(path) != expected_sha:
                raise ValueError(f"stream ledger drift: {symbol}/{filename}")
        for name in TABLES:
            frame = _read_gzip(directory / f"{name}.csv.gz")
            if not frame.empty:
                frame["stream_symbol"] = symbol
            frames[name].append(frame)
    return identity, {name: pd.concat(parts, ignore_index=True) if parts else pd.DataFrame() for name, parts in frames.items()}


def _closed(trades: pd.DataFrame, split: pd.Timestamp) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["arm", "event_key", "signal_close", "exit_time", "net_r", "net_bp", "month", "week"])
    required = {"arm", "trade_key", "signal_close", "exit_time", "net_r", "net_return", "censored"}
    missing = required - set(trades)
    if missing:
        raise ValueError(f"trades missing required columns: {sorted(missing)}")
    out = trades.copy()
    out["signal_close"] = pd.to_datetime(out.signal_close, utc=True, errors="coerce")
    out["exit_time"] = pd.to_datetime(out.exit_time, utc=True, errors="coerce")
    out["net_r"] = pd.to_numeric(out.net_r, errors="coerce")
    out["net_bp"] = pd.to_numeric(out.net_return, errors="coerce") * 1e4
    out["event_key"] = out.event_key if "event_key" in out else out.trade_key
    out = out.loc[~out.censored.astype(bool) & out.exit_time.notna() & out.signal_close.notna() & out.net_r.notna()].copy()
    out["month"] = out.signal_close.dt.strftime("%Y-%m")
    monday = out.signal_close.dt.normalize() - pd.to_timedelta(out.signal_close.dt.dayofweek, unit="D")
    out["week"] = monday.dt.strftime("%Y-%m-%d")
    out["cohort"] = np.where(out.signal_close >= split, "later", np.where(out.exit_time < split, "earlier", "cross_split"))
    return out


def _metric(frame: pd.DataFrame) -> dict[str, Any]:
    n = len(frame)
    if not n:
        return {"n": 0, "wins": 0, "win_rate": math.nan, "sum_net_r": 0., "mean_net_r": math.nan,
                "mean_net_bp": math.nan, "pf_r": math.nan, "mean_win_r": math.nan, "mean_loss_r": math.nan,
                "gt5_final_net_r": 0, "gt10_final_net_r": 0, "event_ordered_max_drawdown_r": math.nan,
                "drawdown_basis": "exit_ordered_event_r_nonaccount"}
    values = frame.net_r.to_numpy(float); positive, negative = values[values > 0], values[values < 0]
    ordered = frame.sort_values(["exit_time", "event_key"], kind="stable").net_r.to_numpy(float).cumsum()
    drawdown = np.maximum.accumulate(np.r_[0., ordered]) - np.r_[0., ordered]
    return {"n": n, "wins": int((values > 0).sum()), "win_rate": float((values > 0).mean()),
            "sum_net_r": float(values.sum()), "mean_net_r": float(values.mean()),
            "mean_net_bp": float(frame.net_bp.mean()), "pf_r": float(positive.sum() / abs(negative.sum())) if len(negative) else math.inf if len(positive) else math.nan,
            "mean_win_r": float(positive.mean()) if len(positive) else math.nan,
            "mean_loss_r": float(negative.mean()) if len(negative) else math.nan,
            "gt5_final_net_r": int((values > 5).sum()), "gt10_final_net_r": int((values > 10).sum()),
            "event_ordered_max_drawdown_r": float(drawdown.max()), "drawdown_basis": "exit_ordered_event_r_nonaccount"}


def _matched(frame: pd.DataFrame, controls: pd.DataFrame, split: pd.Timestamp | None = None) -> pd.DataFrame:
    if frame.empty or controls.empty:
        return pd.DataFrame(columns=["event_key", "arm", "net_r", "net_bp", "control_net_r", "control_net_bp"])
    need = {"trade_key", "arm", "matched", "control_net_r", "control_net_return"}
    if not need.issubset(controls):
        raise ValueError(f"controls missing required columns: {sorted(need-set(controls))}")
    c = controls.copy(); c = c.loc[c.matched.astype(bool)].copy()
    c["control_net_r"] = pd.to_numeric(c.control_net_r, errors="coerce")
    c["control_net_bp"] = pd.to_numeric(c.control_net_return, errors="coerce") * 1e4
    key = frame[["trade_key", "arm", "event_key", "net_r", "net_bp", "signal_close", "exit_time", "month", "week", "cohort"]]
    fields = ["trade_key", "arm", "control_net_r", "control_net_bp"]
    if "control_exit_time" in c:
        fields.append("control_exit_time")
    out = key.merge(c[fields], on=["trade_key", "arm"], how="inner", validate="one_to_one")
    if "control_exit_time" in out:
        out["control_exit_time"] = pd.to_datetime(out.control_exit_time, utc=True, errors="coerce")
        out = out.loc[out.control_exit_time.notna()]
        if split is not None:
            out = out.loc[~out.cohort.eq("earlier") | out.control_exit_time.lt(split)]
    return out.dropna(subset=["control_net_r", "control_net_bp"])


def _metric_row(frame: pd.DataFrame, controls: pd.DataFrame, arm: str, period: str, split: pd.Timestamp | None = None) -> dict[str, Any]:
    values = _metric(frame)
    paired = _matched(frame, controls, split if period == 'earlier' else None)
    values.update({"arm": arm, "period": period, "matched_n": len(paired),
                   "paired_target_win_rate": float((paired.net_r > 0).mean()) if len(paired) else math.nan,
                   "random_win_rate": float((paired.control_net_r > 0).mean()) if len(paired) else math.nan,
                   "random_mean_net_r": float(paired.control_net_r.mean()) if len(paired) else math.nan,
                   "random_mean_net_bp": float(paired.control_net_bp.mean()) if len(paired) else math.nan,
                   "paired_excess_mean_r": float((paired.net_r-paired.control_net_r).mean()) if len(paired) else math.nan,
                   "paired_excess_mean_bp": float((paired.net_bp-paired.control_net_bp).mean()) if len(paired) else math.nan})
    return values


def _calendar(config: Mapping[str, Any], period: str = "full") -> list[str]:
    start, split, end = pd.Timestamp(config["start"]), pd.Timestamp(config["split"]), pd.Timestamp(config["end"])
    if period == "earlier": end = split
    elif period == "later": start = split
    elif period != "full": raise ValueError(f"unknown period: {period}")
    cursor = pd.Timestamp(year=start.year, month=start.month, day=1, tz=start.tz)
    months = []
    while cursor < end:
        months.append(cursor.strftime("%Y-%m")); cursor += pd.DateOffset(months=1)
    return months


def summary_tables(closed: pd.DataFrame, controls: pd.DataFrame, config: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    split = pd.Timestamp(config["split"])
    metrics, monthly = [], []
    for arm in ARMS:
        unit = closed.loc[closed.arm.eq(arm)]
        for period in ("full", "earlier", "later"):
            subset = unit if period == "full" else unit.loc[unit.cohort.eq(period)]
            metrics.append(_metric_row(subset, controls, arm, period, split))
        for month in _calendar(config):
            subset = unit.loc[unit.month.eq(month)]
            monthly.append({"month": month, **_metric_row(subset, controls, arm, "month", split)})
    return pd.DataFrame(metrics), pd.DataFrame(monthly)


def paired_attribution(closed: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ("full", "earlier", "later"):
        unit = closed if period == "full" else closed.loc[closed.cohort.eq(period)]
        base, treatment = (unit.loc[unit.arm.eq(arm)].drop_duplicates("event_key") for arm in ARMS)
        keys_b, keys_t = set(base.event_key), set(treatment.event_key); common = keys_b & keys_t
        b, t = base.set_index("event_key"), treatment.set_index("event_key")
        if common:
            b_common, t_common = b.loc[sorted(common)], t.loc[sorted(common)]
        else:
            b_common, t_common = pd.DataFrame(), pd.DataFrame()
        common_mean = float(b_common.net_r.mean()) if common else 0.
        delta = float(t.net_r.sum() - b.net_r.sum())
        count_part = float((len(t)-len(b))*common_mean)
        rows.append({"period": period, "baseline_n": len(b), "treatment_n": len(t), "common_event_keys": len(common),
                     "lost_event_keys": len(keys_b-keys_t), "new_event_keys": len(keys_t-keys_b),
                     "common_event_key_list": json.dumps(sorted(common)), "lost_event_key_list": json.dumps(sorted(keys_b-keys_t)), "new_event_key_list": json.dumps(sorted(keys_t-keys_b)),
                     "baseline_sum_net_r": float(b.net_r.sum()), "treatment_sum_net_r": float(t.net_r.sum()),
                     "delta_sum_net_r": delta, "common_quality_delta_r": float((t_common.net_r-b_common.net_r).sum()) if common else 0.,
                     "new_sum_net_r": float(t.loc[sorted(keys_t-keys_b)].net_r.sum()) if keys_t-keys_b else 0.,
                     "lost_sum_net_r": float(b.loc[sorted(keys_b-keys_t)].net_r.sum()) if keys_b-keys_t else 0.,
                     "trade_count_contribution_r": count_part, "quality_contribution_r": delta-count_part,
                     "decomposition": "delta=sum(treatment)-sum(baseline); count=(n_t-n_b)*baseline_common_mean; quality=delta-count"})
    return pd.DataFrame(rows)


def tail_retention(closed: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ("full", "earlier", "later"):
        unit = closed if period == "full" else closed.loc[closed.cohort.eq(period)]
        for threshold in (5., 10.):
            sets = {arm: set(unit.loc[unit.arm.eq(arm) & unit.net_r.gt(threshold), "event_key"]) for arm in ARMS}
            common, lost, new = sets[ARMS[0]] & sets[ARMS[1]], sets[ARMS[0]]-sets[ARMS[1]], sets[ARMS[1]]-sets[ARMS[0]]
            rows.append({"period": period, "threshold": f">{int(threshold)}R", "baseline_tail_n": len(sets[ARMS[0]]),
                         "treatment_tail_n": len(sets[ARMS[1]]), "intersection_event_keys": len(sets[ARMS[0]] & sets[ARMS[1]]),
                         "lost_event_keys": len(sets[ARMS[0]]-sets[ARMS[1]]), "new_event_keys": len(sets[ARMS[1]]-sets[ARMS[0]]),
                         "intersection_event_key_list": json.dumps(sorted(common)), "lost_event_key_list": json.dumps(sorted(lost)), "new_event_key_list": json.dumps(sorted(new))})
    return pd.DataFrame(rows)


def _bootstrap(closed: pd.DataFrame, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    reps = int(config["bootstrap_reps"]); rows = []
    for period_index, period in enumerate(("full", "earlier", "later")):
        months = _calendar(config, period); rng = np.random.default_rng(int(config["bootstrap_seed"])+period_index)
        unit = closed if period == "full" else closed.loc[closed.cohort.eq(period)]
        # Aggregate each calendar month first, then resample its sufficient
        # statistics. This is exactly equivalent to concatenating ledgers but
        # retains zero-trade months and recomputes all ratio denominators.
        parts: dict[str, dict[str, np.ndarray]] = {}
        for arm in ARMS:
            arm_frame = unit.loc[unit.arm.eq(arm)]
            parts[arm] = {"n": np.asarray([int(arm_frame.month.eq(month).sum()) for month in months], float),
                          "wins": np.asarray([int((arm_frame.loc[arm_frame.month.eq(month), "net_r"] > 0).sum()) for month in months], float),
                          "sum_r": np.asarray([float(arm_frame.loc[arm_frame.month.eq(month), "net_r"].sum()) for month in months], float)}
        drawn = rng.integers(0, len(months), size=(reps, len(months)))
        totals = {arm: {key: values[drawn].sum(axis=1) for key, values in part.items()} for arm, part in parts.items()}
        def ratio(numer: np.ndarray, denom: np.ndarray) -> np.ndarray:
            return np.divide(numer, denom, out=np.full(reps, np.nan), where=denom > 0)
        bw = ratio(totals[ARMS[0]]["wins"], totals[ARMS[0]]["n"]); tw = ratio(totals[ARMS[1]]["wins"], totals[ARMS[1]]["n"])
        br = ratio(totals[ARMS[0]]["sum_r"], totals[ARMS[0]]["n"]); tr = ratio(totals[ARMS[1]]["sum_r"], totals[ARMS[1]]["n"])
        stats = {"baseline_win_rate": bw, "treatment_win_rate": tw, "delta_win_rate": tw-bw, "delta_mean_r": tr-br}
        base, treatment = (unit.loc[unit.arm.eq(arm)] for arm in ARMS)
        base_m, treatment_m = _metric(base), _metric(treatment)
        points = {"baseline_win_rate": base_m["win_rate"], "treatment_win_rate": treatment_m["win_rate"],
                  "delta_win_rate": treatment_m["win_rate"]-base_m["win_rate"], "delta_mean_r": treatment_m["mean_net_r"]-base_m["mean_net_r"]}
        for name, values in stats.items():
            finite = np.asarray(values, float); finite = finite[np.isfinite(finite)]
            rows.append({"method": "month_bootstrap", "metric": name, "period": period, "reps": reps, "seed": int(config["bootstrap_seed"])+period_index,
                         "zero_trade_months_included": True, "point_estimate": points[name], "valid_reps": len(finite),
                         "ci_low": float(np.quantile(finite,.025)) if len(finite) else math.nan,
                         "ci_high": float(np.quantile(finite,.975)) if len(finite) else math.nan,
                         "formula": "sample complete period calendar months with replacement; recompute each ratio from sampled numerators/denominators"})
    return rows


def _signflip(values: pd.Series, seed: int, reps: int) -> tuple[float, float]:
    value = values.to_numpy(float); observed = float(value.sum())
    if not len(value): return observed, math.nan
    rng = np.random.default_rng(seed); signs = rng.choice(np.array([-1., 1.]), size=(reps, len(value)))
    simulated = np.sum(signs * value, axis=1)
    p = (1 + int((np.abs(simulated) >= abs(observed)).sum())) / (reps + 1)
    return observed, float(p)


def _holm(rows: list[dict[str, Any]]) -> None:
    # The preregistered family is always four later-period tests. A missing
    # statistic is conservatively assigned p=1; it never shrinks the family.
    usable = sorted((row["p_raw"] if math.isfinite(row["p_raw"]) else 1., i) for i,row in enumerate(rows))
    running = 0.; total = 4
    for rank,(p,i) in enumerate(usable):
        running = max(running, min(1., (total-rank)*p)); rows[i]["p_holm"] = running


def _weekly_inference(closed: pd.DataFrame, controls: pd.DataFrame, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    later = closed.loc[closed.cohort.eq("later")]; reps, seed = int(config["permutation_reps"]), int(config["permutation_seed"])
    rows: list[dict[str, Any]] = []
    # Centre both independent arms on one pooled null mean. The weighted weekly
    # influence sums exactly to treatment-minus-baseline, unlike unequal-count
    # raw weekly sums.
    for name, outcome in (("treatment_minus_baseline_netwin", (later.net_r > 0).astype(float)), ("treatment_minus_baseline_mean_r", later.net_r.astype(float))):
        unit = later[["arm", "week"]].copy(); unit["outcome"] = outcome.to_numpy()
        pooled = float(unit.outcome.mean()) if len(unit) else 0.
        counts = unit.arm.value_counts(); nt, nb = int(counts.get(ARMS[1], 0)), int(counts.get(ARMS[0], 0))
        if nt and nb:
            sign = np.where(unit.arm.eq(ARMS[1]), 1/nt, -1/nb)
            weekly = (sign*(unit.outcome-pooled)).groupby(unit.week).sum()
        else: weekly = pd.Series(dtype=float)
        observed, p = _signflip(weekly, seed+len(rows), reps)
        rows.append({"method":"weekly_signflip_approximate","metric":name,"period":"later","estimate":observed,"weekly_values":json.dumps(weekly.tolist()),"p_raw":p,"reps":reps,"seed":seed+len(rows),
                     "formula":"approximate clustered sign-flip: sum_week[(y-pooled_mean)/n_treatment for treatment minus (y-pooled_mean)/n_baseline for baseline]; not a random-entry score permutation"})
    matched = _matched(later.loc[later.arm.eq(ARMS[1])], controls)
    for name, column in (("treatment_paired_random_excess_r", "control_net_r"), ("treatment_paired_random_excess_bp", "control_net_bp")):
        target = "net_r" if column.endswith("_r") else "net_bp"
        unit = matched.copy(); unit["delta"] = unit[target]-unit[column]
        weekly = unit.groupby("week").delta.sum() / len(unit) if len(unit) else pd.Series(dtype=float)
        observed, p = _signflip(weekly, seed+len(rows), reps)
        rows.append({"method":"weekly_signflip_approximate","metric":name,"period":"later","estimate":observed,"weekly_values":json.dumps(weekly.tolist()),"p_raw":p,"reps":reps,"seed":seed+len(rows),
                     "formula":"approximate clustered sign-flip: sum_week paired(target-control)/matched_n; not a random-entry score permutation"})
    _holm(rows)
    return rows


def build(input_dir: Path, output: Path) -> dict[str, Any]:
    """Aggregate one receipt-complete runner output into reproducible tables."""
    if output.exists(): raise FileExistsError(f"refusing to overwrite existing output: {output}")
    identity, frames = load_run(input_dir)
    config = identity.get("config", {})
    if identity.get("subset") is not False or len(identity["symbols"]) != config.get("expected_symbols"):
        raise ValueError("full frozen universe required; smoke/subset cannot be an aggregate report")
    if config.get("split") != SPLIT.isoformat():
        raise ValueError("frozen split differs from runner control strata")
    for field in ("start", "split", "end", "bootstrap_seed", "bootstrap_reps", "permutation_seed", "permutation_reps"):
        if field not in config: raise ValueError(f"identity config missing {field}")
    closed = _closed(frames["trades"], pd.Timestamp(config["split"]))
    metrics, monthly = summary_tables(closed, frames["controls"], config)
    inference = _bootstrap(closed, config) + _weekly_inference(closed, frames["controls"], config)
    output.mkdir(parents=True)
    metrics.to_csv(output / "metrics.csv", index=False); monthly.to_csv(output / "monthly.csv", index=False)
    tail_retention(closed).to_csv(output / "tail_retention.csv", index=False)
    paired_attribution(closed).to_csv(output / "paired_attribution.csv", index=False)
    pd.DataFrame(inference).to_csv(output / "inference.csv", index=False)
    files = {name: sha(output / name) for name in ("metrics.csv", "monthly.csv", "tail_retention.csv", "inference.csv", "paired_attribution.csv")}
    summary = {"input": str(input_dir), "input_manifest_sha256": sha(input_dir / "manifest.json"), "input_identity_sha256": sha(input_dir / "identity.json"),
               "input_source_hashes": identity.get("source", {}), "report_source_sha256": sha(Path(__file__)),
               "closed_rows": len(closed), "native_pine_parity": False, "auc": "not_applicable_no_model_score", "top_decile": "not_applicable_no_ranker",
               "drawdown_basis": "exit_ordered_event_r_nonaccount", "files": files, "training_eligible": False, "production_eligible": False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="completed V12.6 runner output")
    parser.add_argument("--output", type=Path, required=True, help="new aggregate output directory")
    args = parser.parse_args(); print(json.dumps(build(args.input, args.output), indent=2))


if __name__ == "__main__": main()
