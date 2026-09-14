"""Summarize completed V9/V8 receipts without reopening price history.

Period semantics match the frozen V8 six-filter report. Random-control pairs
are reported on their explicit jointly-closed denominator. Monthly-block sign
flips and bootstrap intervals describe exposed historical evidence, not live
profitability. R sums and drawdowns are event statistics, not account returns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, periods, START, SPLIT, END
from yoyo.evaluation.spike_six_filter_statistics import matched_deletion
from yoyo.evaluation.spike_v9_full_replay import CONFIG, DEPENDENCIES, SOURCE, base, engine, check_completed, digest, _committed

SEED = 91509
REPS = 2000
REQUIRED_FILES = {f"{arm}.{kind}.csv.gz" for arm in ("v8", "v9") for kind in ("trades", "fills")} | {"decisions.csv.gz", "controls.csv.gz"}


def validate_receipt(receipt, key):
    """A hash list cannot substitute for the required complete output schema."""
    if receipt.get("key") != key or receipt.get("baseline_parity") is not True:
        raise ValueError("unverified stream identity or baseline")
    if set(receipt.get("files", {})) != REQUIRED_FILES:
        raise ValueError("incomplete stream file set")
    summaries = receipt.get("summaries", [])
    if len(summaries) != 2 or {r.get("arm") for r in summaries} != {"v8", "v9"}:
        raise ValueError("incomplete arm summaries")
    for row in summaries:
        if row.get("stream_key") != key or not {"events", "closed", "censored", "net_r", "realized_ge10", "candidates"} <= row.keys():
            raise ValueError("invalid stream summary")
    return {row["arm"]: row for row in summaries}


def boolean(series):
    values = series.astype(str).str.lower().map({"true": True, "false": False})
    if values.isna().any():
        raise ValueError("invalid persisted boolean")
    return values.astype(bool)


def event_keys(frame):
    return frame.stream_key + ":" + frame.signal_i.astype(int).astype(str) + ":" + frame.side.astype(int).astype(str)


def load(output):
    config = json.loads(CONFIG.read_text())
    for field, timestamp, engine_stamp in (("evaluation_start", START, base.START), ("split", SPLIT, base.SPLIT), ("exclusive_end", END, base.END)):
        if pd.Timestamp(config[field]) != timestamp or timestamp != engine_stamp:
            raise ValueError("configured period differs from replay engine")
    if config["round_trip_cost"] != .002:
        raise ValueError("configured cost differs from fixed engine")
    manifest = json.loads((output / "manifest.json").read_text())
    if not manifest.get("complete") or manifest["streams"] != 3531:
        raise ValueError("full report requires all 3531 completed streams")
    folders = sorted(p for p in (output / "streams").iterdir() if (p / "completion.json").is_file())
    source_keys = {p.name for p in (engine.RAW / "streams").iterdir() if (p / "completion.json").is_file()}
    if len(folders) != manifest["streams"] or {p.name for p in folders} != source_keys:
        raise ValueError("manifest coverage mismatch")
    identity = json.loads((output / "identity.json").read_text())
    expected_identity = {str(p): digest(p) for p in DEPENDENCIES}
    expected_identity.update(baseline_manifest=digest(SOURCE / "manifest.json"), raw_manifest=digest(engine.RAW / "manifest.json"))
    if (identity != expected_identity or identity["baseline_manifest"] != config["baseline_manifest_sha256"]
            or identity["raw_manifest"] != config["raw_manifest_sha256"]
            or hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != manifest["run_identity"]):
        raise ValueError("replay source identity drift")
    if digest(output / "stream_summary.csv") != manifest["summary_sha256"]:
        raise ValueError("aggregate stream summary hash drift")
    aggregate = pd.read_csv(output / "stream_summary.csv").set_index(["stream_key", "arm"])
    if len(aggregate) != 7062 or not aggregate.index.is_unique:
        raise ValueError("aggregate stream summary coverage mismatch")
    trades, decisions, controls = [], [], []
    source_receipts = []
    for folder in folders:
        receipt = check_completed(folder, manifest["run_identity"])
        summaries = validate_receipt(receipt, folder.name)
        if (digest(SOURCE / "streams" / folder.name / "completion.json") != receipt["source_completion_sha256"]
                or digest(engine.RAW / "streams" / folder.name / "completion.json") != receipt["raw_completion_sha256"]):
            raise ValueError("frozen source receipt changed")
        candidate = pd.read_csv(folder / "decisions.csv.gz")
        for arm in ("v8", "v9"):
            frame = pd.read_csv(folder / f"{arm}.trades.csv.gz")
            if len(frame) and (not frame.arm.eq(arm).all() or not frame.stream_key.eq(folder.name).all()):
                raise ValueError("trade identity mismatch")
            closed = frame.loc[~boolean(frame.censored)]
            counts = {"events": len(frame), "closed": len(closed), "censored": len(frame)-len(closed),
                      "net_r": float(closed.net_r.sum()), "realized_ge10": int(closed.net_r.ge(10).sum()),
                      "candidates": len(candidate) if arm == "v8" else int(boolean(candidate.v9).sum())}
            for field, value in counts.items():
                if not np.isclose(value, summaries[arm][field], atol=1e-7, rtol=1e-12) or not np.isclose(value, aggregate.loc[(folder.name, arm), field], atol=1e-7, rtol=1e-12):
                    raise ValueError(f"stream summary mismatch: {folder.name} {arm} {field}")
            trades.append(frame)
        decisions.append(candidate)
        controls.append(pd.read_csv(folder / "controls.csv.gz"))
        source_receipts.append({"key": folder.name, "receipt_sha256": digest(folder / "completion.json")})
    trades = pd.concat(trades, ignore_index=True)
    decisions = pd.concat(decisions, ignore_index=True)
    controls = pd.concat(controls, ignore_index=True)
    for table in (trades, controls, decisions):
        for column in ("signal_bar_open", "entry_time", "exit_time", "scheduled_open_utc", "control_signal_time", "control_entry_time", "control_exit_time"):
            if column in table:
                table[column] = pd.to_datetime(table[column], utc=True)
        table["event_key"] = event_keys(table)
    trades["censored"] = boolean(trades.censored)
    decisions["v9"] = boolean(decisions.v9)
    controls["matched"] = boolean(controls.matched)
    controls["control_censored"] = boolean(controls.control_censored)
    if trades.duplicated(["arm", "event_key"]).any() or controls.duplicated(["arm", "event_key"]).any():
        raise ValueError("duplicate event identity")
    if len(trades) != len(controls):
        raise ValueError("each actual event must retain one control receipt")
    expected = pd.MultiIndex.from_frame(trades[["arm", "event_key"]])
    actual = pd.MultiIndex.from_frame(controls[["arm", "event_key"]])
    if len(expected.difference(actual)) or len(actual.difference(expected)):
        raise ValueError("control event coverage differs")
    return trades, decisions, controls, source_receipts


def block_statistics(delta, months):
    """Resample and sign-flip whole calendar months across all assets/venues."""
    block = pd.DataFrame({"delta": np.asarray(delta, dtype=float), "month": np.asarray(months)}).groupby("month").delta.agg(["sum", "size"])
    if len(block) < 2:
        return {"blocks": len(block), "mean_ci_low": np.nan, "mean_ci_high": np.nan, "p_month_signflip": np.nan}
    rng = np.random.default_rng(SEED)
    selection = rng.integers(0, len(block), size=(REPS, len(block)))
    values = block["sum"].to_numpy()
    boot = values[selection].sum(axis=1) / block["size"].to_numpy()[selection].sum(axis=1)
    signed = (rng.choice([-1., 1.], size=(REPS, len(block))) * values).sum(axis=1)
    p = (1 + np.sum(signed >= values.sum() - 1e-12)) / (REPS + 1)
    lo, hi = np.quantile(boot, [.025, .975])
    return {"blocks": len(block), "mean_ci_low": float(lo), "mean_ci_high": float(hi), "p_month_signflip": float(p)}


def control_metrics(part, controls, period):
    joined = part[["arm", "event_key"]].merge(controls, on=["arm", "event_key"], validate="one_to_one")
    eligible = joined.matched.copy()
    if period == "earlier":
        eligible &= (joined.control_entry_time < SPLIT) & (joined.control_exit_time < SPLIT)
    elif period == "later":
        eligible &= joined.control_entry_time >= SPLIT
    pair = joined.loc[eligible]
    r = pair.target_net_r - pair.control_net_r
    nominal = pair.target_net_return - pair.control_net_return
    return {"control_receipts": len(joined), "matched_pairs": len(pair),
            "unmatched_or_cross_period": len(joined)-len(pair),
            "paired_target_mean_r": float(pair.target_net_r.mean()),
            "control_mean_r": float(pair.control_net_r.mean()),
            "paired_excess_r": float(r.mean()),
            "control_win_rate": float(pair.control_net_r.gt(0).mean()) if len(pair) else np.nan,
            "paired_excess_nominal": float(nominal.mean()),
            **block_statistics(r, pair.entry_time.dt.strftime("%Y-%m"))}


def summary_rows(trades, controls, group_column=None):
    rows = []
    groups = [("all", trades)] if group_column is None else trades.groupby(group_column, sort=True)
    for group, table in groups:
        for arm, one_arm in table.groupby("arm", sort=True):
            for period, part in periods(one_arm):
                rows.append({"group": group, "arm": arm, "period": period,
                             **metrics(part), **control_metrics(part, controls, period)})
    return pd.DataFrame(rows)


def attribution(trades, decisions):
    left = trades.loc[trades.arm.eq("v8")].copy()
    right = trades.loc[trades.arm.eq("v9")].copy()
    joined = left.merge(right, on="event_key", suffixes=("_v8", "_v9"), how="outer", validate="one_to_one", indicator=True)
    common = joined.loc[joined._merge.eq("both")]
    for col in ("censored", "exit_time", "exit_reason"):
        if not common[f"{col}_v8"].equals(common[f"{col}_v9"]):
            raise ValueError(f"entry-only bundle changed shared-event {col}")
    if not np.allclose(common.net_r_v8, common.net_r_v9, rtol=1e-9, atol=1e-9, equal_nan=True):
        raise ValueError("entry-only bundle changed shared-event netR")
    removed = left.loc[~left.event_key.isin(right.event_key)]
    added = right.loc[~right.event_key.isin(left.event_key)]
    winners = left.loc[~left.censored & left.net_r.ge(10)]
    retained = winners.event_key.isin(right.event_key)
    missing_r = removed.loc[~removed.censored].net_r
    added_r = added.loc[~added.censored].net_r
    fixed = left.merge(decisions[["event_key", "v9", "signal_atr_pct", "v9_bundle_reasons"]], on="event_key", how="left", validate="one_to_one")
    if fixed.v9.isna().any():
        raise ValueError("baseline trade missing candidate decision")
    fixed["gate_rejected"] = ~fixed.v9.astype(bool)
    detail = {"common_events": len(common), "common_outcomes_unchanged": True,
              "removed_events": len(removed), "added_events": len(added),
              "avoided_loss_r": float(-missing_r.clip(upper=0).sum()),
              "foregone_profit_r": float(missing_r.clip(lower=0).sum()),
              "new_entry_net_r": float(added_r.sum()),
              "serial_delta_r": float(-missing_r.sum()+added_r.sum()),
              "baseline_10r": len(winners), "retained_original_10r": int(retained.sum()),
              "lost_original_10r": int((~retained).sum()),
              "new_10r": int((~added.censored & added.net_r.ge(10)).sum()),
              "cost_r_reduction": float(left.loc[~left.censored].eval("gross_r-net_r").sum()-right.loc[~right.censored].eval("gross_r-net_r").sum()),
              "gross_r_delta": float(right.loc[~right.censored].gross_r.sum()-left.loc[~left.censored].gross_r.sum())}
    return detail, fixed, removed, added


def build(output: Path, destination: Path):
    statistics_sources = (Path(__file__), Path("tests/evaluation/test_spike_v9_full_report.py"),
                          Path("yoyo/evaluation/spike_be05_report.py"), Path("yoyo/evaluation/spike_six_filter_statistics.py"))
    if not _committed(statistics_sources):
        raise ValueError("commit unchanged statistics builder/tests before aggregation")
    trades, decisions, controls, sources = load(output)
    destination.mkdir(parents=True, exist_ok=True)
    summary = summary_rows(trades, controls)
    reference = summary.loc[summary.arm.eq("v8") & summary.period.eq("full")].iloc[0]
    if (reference.events != 95191 or reference.closed != 94746 or reference.censored != 445
            or reference.realized_ge10 != 532 or abs(reference.total_r - (-2021.78)) > .01):
        raise ValueError("full V8 aggregate differs from frozen report")
    detail, fixed, removed, added = attribution(trades, decisions)
    both = summary.loc[summary.period.eq("full")].set_index("arm")
    if abs(detail["serial_delta_r"]-(both.loc["v9", "total_r"]-both.loc["v8", "total_r"])) > 1e-7:
        raise ValueError("serial attribution does not reconcile")
    summary.to_csv(destination / "summary.csv", index=False)
    summary_rows(trades, controls, "timeframe_min").to_csv(destination / "by_timeframe.csv", index=False)
    summary_rows(trades, controls, "venue").to_csv(destination / "by_venue.csv", index=False)
    assets = trades.loc[~trades.censored].groupby(["asset", "arm"]).agg(trades=("net_r", "size"), net_r=("net_r", "sum"), gross_r=("gross_r", "sum")).reset_index()
    asset_delta = assets.pivot(index="asset", columns="arm", values="net_r").fillna(0)
    asset_delta["delta_r"] = asset_delta.v9-asset_delta.v8
    asset_delta.sort_values("delta_r", ascending=False).to_csv(destination / "asset_delta.csv")
    monthly = trades.loc[~trades.censored].copy()
    monthly["entry_month"] = monthly.entry_time.dt.strftime("%Y-%m")
    monthly.groupby(["entry_month", "arm"]).agg(trades=("net_r", "size"), total_r=("net_r", "sum")).reset_index().to_csv(destination / "monthly.csv", index=False)
    reject = decisions.assign(rejected=~decisions.v9).groupby("v9_bundle_reasons").agg(candidates=("v9", "size"), rejected=("rejected", "sum")).reset_index()
    reject.to_csv(destination / "gate_counts.csv", index=False)
    deletion_rows = []
    # One fixed bundle x three timeframes; do not inherit the old 27-arm family.
    for timeframe, part in fixed.loc[fixed.entry_time >= SPLIT].groupby("timeframe_min"):
        row = matched_deletion(part, iterations=REPS)
        row.pop("p_bonferroni_27", None)
        p = row["p_matched_deletion"]
        row["p_bonferroni_3"] = min(1., p*3) if np.isfinite(p) else np.nan
        deletion_rows.append({"timeframe_min": timeframe, **row})
    pd.DataFrame(deletion_rows).to_csv(destination / "matched_deletion.csv", index=False)
    for name, frame in (("trades", trades), ("decisions", decisions), ("controls", controls), ("removed", removed), ("added", added)):
        frame.to_csv(destination / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    (destination / "attribution.json").write_text(json.dumps(detail, indent=2)+"\n")
    stats = {"replay_manifest_sha256": digest(output / "manifest.json"), "streams": len(sources),
             "trades": len(trades), "candidates": len(decisions), "controls": len(controls),
             "configuration_exposure": 1, "history": "reused exposed history, not blind validation",
             "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
             "builder_sha256": digest(Path(__file__)), "statistics_dependencies": {str(p): digest(p) for p in statistics_sources}, "source_receipts": sources,
             "files": {p.name: digest(p) for p in destination.iterdir() if p.is_file() and p.name != "statistics_receipt.json"}}
    (destination / "statistics_receipt.json").write_text(json.dumps(stats, indent=2)+"\n")
    print(summary.to_string(index=False))
    print(json.dumps(detail, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.input, args.output)
