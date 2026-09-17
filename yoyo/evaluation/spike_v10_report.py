"""Aggregate the completed V10 receipts without reopening price history.

Periods use the engine's own split: `earlier` is 2024-09-10..2025-09-10,
`later` is 2025-09-10 up to the 2026-05-04 holdout cut, so `later` is 7.8
months here and not the full year the published V9 report shows. `full` is the
whole pre-holdout span. Nothing in this file can reach a holdout bar: it reads
only the receipts the replay wrote.

Matched-control pairs keep their own jointly-closed denominator, and the
month-block sign flip is computed on the paired excess, not on the pool's own
return. Both are the guards CLAUDE.md demands for a directional claim: a pool's
absolute R cannot see the beta it is standing on.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, periods, SPLIT
from yoyo.evaluation.spike_v10 import ARMS, VERSION
from yoyo.evaluation.spike_v10_full_replay import CONFIG, CUT, DEPENDENCIES, digest, engine, _committed
from yoyo.evaluation.spike_v9_full_report import block_statistics, boolean, control_metrics, event_keys

CROSSINGS = Path("experiments/active/exp-spike-v10-trendline-gate-20260918-v1/results/crossings.csv.gz")

REQUIRED = {f"{arm}.{kind}.csv.gz" for arm in ARMS for kind in ("trades", "fills")} | {"decisions.csv.gz", "controls.csv.gz"}
AGE_EDGES = [-2, -1, 0, 3, 12, 48, 200, 10 ** 9]
AGE_LABELS = ["no_break", "age_0", "age_1_3", "age_4_12", "age_13_48", "age_49_200", "age_gt_200"]


def age_buckets(age: pd.Series) -> pd.Series:
    """One bucketing of break age, shared by every table that reports it."""
    return pd.cut(age.astype(int), AGE_EDGES, labels=AGE_LABELS)


def load(output: Path):
    """Validate every receipt, then concatenate the arms' ledgers."""
    config = json.loads(CONFIG.read_text())
    if pd.Timestamp(config["exclusive_end_bar_close"]) != CUT or config["holdout_consumption"] != 0:
        raise ValueError("configured cut differs from the runner")
    manifest = json.loads((output / "manifest.json").read_text())
    if not manifest.get("complete") or manifest["streams"] != config["expected_streams"]:
        raise ValueError("full report requires every completed stream")
    identity = {str(p): digest(p) for p in DEPENDENCIES}
    identity["raw_manifest"] = digest(engine.RAW / "manifest.json")
    identity["cut"] = str(CUT)
    if json.loads((output / "identity.json").read_text()) != identity:
        raise ValueError("replay source identity drift")
    if hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != manifest["run_identity"]:
        raise ValueError("run identity hash drift")
    if digest(output / "stream_summary.csv") != manifest["summary_sha256"]:
        raise ValueError("aggregate stream summary hash drift")
    aggregate = pd.read_csv(output / "stream_summary.csv").set_index(["stream_key", "arm"])
    folders = sorted(p for p in (output / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != manifest["streams"]:
        raise ValueError("manifest coverage mismatch")
    trades, decisions, controls, receipts = [], [], [], []
    skipped = 0
    for folder in folders:
        receipt = json.loads((folder / "completion.json").read_text())
        if receipt.get("status") != "complete" or receipt.get("run_identity") != manifest["run_identity"]:
            raise ValueError(f"incomplete stream receipt: {folder.name}")
        for name, expected in receipt["files"].items():
            if digest(folder / name) != expected:
                raise ValueError(f"output hash drift: {folder / name}")
        if receipt.get("skipped") == "no_bar_before_cut":
            skipped += 1
            receipts.append({"key": folder.name, "receipt_sha256": digest(folder / "completion.json"), "skipped": True})
            continue
        if set(receipt["files"]) != REQUIRED:
            raise ValueError(f"incomplete stream file set: {folder.name}")
        if receipt["v9_parity"]["max_relative_float_drift"] > 1e-9:
            raise ValueError(f"V9 parity drift beyond CSV precision: {folder.name}")
        summaries = {row["arm"]: row for row in receipt["summaries"]}
        if set(summaries) != set(ARMS):
            raise ValueError(f"incomplete arm summaries: {folder.name}")
        candidate = pd.read_csv(folder / "decisions.csv.gz")
        for arm in ARMS:
            frame = pd.read_csv(folder / f"{arm}.trades.csv.gz")
            if len(frame) and (not frame.arm.eq(arm).all() or not frame.stream_key.eq(folder.name).all()):
                raise ValueError("trade identity mismatch")
            closed = frame.loc[~boolean(frame.censored)] if len(frame) else frame
            counts = {"events": len(frame), "closed": len(closed), "net_r": float(closed.net_r.sum()) if len(closed) else 0.,
                      "realized_ge10": int(closed.net_r.ge(10).sum()) if len(closed) else 0}
            for field, value in counts.items():
                for reference in (summaries[arm][field], aggregate.loc[(folder.name, arm), field]):
                    if not np.isclose(value, reference, atol=1e-7, rtol=1e-12):
                        raise ValueError(f"stream summary mismatch: {folder.name} {arm} {field}")
            trades.append(frame)
        decisions.append(candidate)
        controls.append(pd.read_csv(folder / "controls.csv.gz"))
        receipts.append({"key": folder.name, "receipt_sha256": digest(folder / "completion.json"),
                         "compared_v9_trades": receipt["v9_parity"]["compared_trades"],
                         "bars_kept": receipt["bars_kept"], "bars_dropped": receipt["bars_dropped"]})
    trades = pd.concat(trades, ignore_index=True)
    decisions = pd.concat(decisions, ignore_index=True)
    controls = pd.concat(controls, ignore_index=True)
    for table in (trades, decisions, controls):
        for column in ("signal_bar_open", "entry_time", "exit_time", "scheduled_open_utc",
                       "control_signal_time", "control_entry_time", "control_exit_time"):
            if column in table:
                table[column] = pd.to_datetime(table[column], utc=True)
        table["event_key"] = event_keys(table)
    trades["censored"] = boolean(trades.censored)
    if trades.exit_time.dropna().max() > CUT or trades.entry_time.max() > CUT:
        raise ValueError("a trade reached past the holdout cut")
    for column in ("v9", *[a for a in ARMS if a != "v9"]):
        decisions[column] = boolean(decisions[column])
    for column in ("matched", "control_censored"):
        controls[column] = boolean(controls[column])
    if trades.duplicated(["arm", "event_key"]).any() or len(trades) != len(controls):
        raise ValueError("event/control identity mismatch")
    return trades, decisions, controls, receipts, skipped


def summary_rows(trades, controls, group_column=None):
    """One row per group x arm x period, arms in their pre-registered order."""
    rows = []
    groups = [("all", trades)] if group_column is None else trades.groupby(group_column, sort=True)
    for group, table in groups:
        for arm in ARMS:
            one_arm = table.loc[table.arm.eq(arm)]
            for period, part in periods(one_arm):
                rows.append({"group": group, "arm": arm, "period": period,
                             **metrics(part), **control_metrics(part, controls, period)})
    return pd.DataFrame(rows)


def attribution(trades: pd.DataFrame, arm: str) -> dict:
    """Split one arm's delta against V9 into avoided loss, lost profit, new entries."""
    left = trades.loc[trades.arm.eq("v9")]
    right = trades.loc[trades.arm.eq(arm)]
    joined = left.merge(right, on="event_key", suffixes=("_v9", "_t"), how="inner", validate="one_to_one")
    if len(joined) and not np.allclose(joined.net_r_v9, joined.net_r_t, rtol=1e-9, atol=1e-9, equal_nan=True):
        raise ValueError(f"entry-only gate changed a shared event outcome: {arm}")
    removed = left.loc[~left.event_key.isin(right.event_key) & ~left.censored].net_r
    added = right.loc[~right.event_key.isin(left.event_key) & ~right.censored].net_r
    winners = left.loc[~left.censored & left.net_r.ge(10)]
    return {"arm": arm, "common_events": len(joined), "removed_events": int((~left.event_key.isin(right.event_key)).sum()),
            "added_events": int((~right.event_key.isin(left.event_key)).sum()),
            "avoided_loss_r": float(-removed.clip(upper=0).sum()),
            "foregone_profit_r": float(removed.clip(lower=0).sum()),
            "new_entry_net_r": float(added.sum()),
            "serial_delta_r": float(-removed.sum() + added.sum()),
            "v9_10r": len(winners), "retained_10r": int(winners.event_key.isin(right.event_key).sum())}


def age_profile(trades: pd.DataFrame, decisions: pd.DataFrame) -> pd.DataFrame:
    """Descriptive only: how V9's own trades did by trendline-break age bucket.

    This ignores serial interaction -- removing an entry frees the engine to
    take a later one -- so it ranks nothing and is not a tradeable read. The
    arms above are the honest comparison.
    """
    v9 = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    joined = v9.merge(decisions[["event_key", "trendline_break_age"]], on="event_key", how="left", validate="one_to_one")
    if joined.trendline_break_age.isna().any():
        raise ValueError("a V9 trade has no candidate decision row")
    age = joined.trendline_break_age.astype(int)
    frame = joined.assign(bucket=age_buckets(age)).groupby("bucket", observed=False)
    return frame.agg(trades=("net_r", "size"), net_r=("net_r", "sum"), mean_net_r=("net_r", "mean"),
                     gross_r=("gross_r", "sum"), win_rate=("net_r", lambda s: float(s.gt(0).mean()) if len(s) else np.nan),
                     realized_ge10=("net_r", lambda s: int(s.ge(10).sum()))).reset_index()


def crossing_outcomes(trades: pd.DataFrame, decisions: pd.DataFrame, crossings: pd.DataFrame) -> pd.DataFrame:
    """Descriptive: V9's own trades split by which side closed the gap.

    A crossing of a falling line has two causes that mean opposite things --
    price climbed, or the line descended into price that did not move. The gate
    cannot tell them apart, so this says what each kind was worth. It ranks
    nothing: serial interaction is ignored here, as in `age_profile`.
    """
    v9 = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    joined = v9.merge(decisions[["event_key", "local_i", "trendline_break_age"]], on="event_key",
                      validate="one_to_one")
    joined["break_i"] = joined.local_i.astype(int) - joined.trendline_break_age.astype(int)
    joined = joined.merge(crossings[["stream_key", "side", "break_i", "led_by", "price_move_atr",
                                     "line_drop_atr"]],
                          on=["stream_key", "side", "break_i"], how="left", validate="many_to_one")
    joined["led_by"] = np.where(joined.trendline_break_age.lt(0), "no_break", joined.led_by.fillna("unmatched"))
    joined["bucket"] = age_buckets(joined.trendline_break_age)
    grouped = joined.groupby(["led_by", "bucket"], observed=True)
    table = grouped.agg(trades=("net_r", "size"), net_r=("net_r", "sum"), mean_net_r=("net_r", "mean"),
                        gross_r=("gross_r", "sum"), win_rate=("net_r", lambda s: float(s.gt(0).mean())),
                        median_price_move_atr=("price_move_atr", "median"),
                        median_line_drop_atr=("line_drop_atr", "median")).reset_index()
    if int(table.trades.sum()) != len(v9):
        raise ValueError("crossing split lost trades")
    return table


def build(output: Path, destination: Path):
    sources = (Path(__file__), Path("tests/evaluation/test_spike_v10_report.py"))
    if not _committed(sources):
        raise ValueError("commit unchanged statistics builder/tests before aggregation")
    trades, decisions, controls, receipts, skipped = load(output)
    destination.mkdir(parents=True, exist_ok=True)
    summary = summary_rows(trades, controls)
    full = summary.loc[summary.period.eq("full")].set_index("arm")
    details = []
    for arm in ARMS:
        if arm == "v9":
            continue
        detail = attribution(trades, arm)
        if abs(detail["serial_delta_r"] - (full.loc[arm, "total_r"] - full.loc["v9", "total_r"])) > 1e-6:
            raise ValueError(f"serial attribution does not reconcile: {arm}")
        details.append(detail)
    summary.to_csv(destination / "summary.csv", index=False)
    summary_rows(trades, controls, "timeframe_min").to_csv(destination / "by_timeframe.csv", index=False)
    summary_rows(trades, controls, "side").to_csv(destination / "by_side.csv", index=False)
    pd.DataFrame(details).to_csv(destination / "attribution.csv", index=False)
    age_profile(trades, decisions).to_csv(destination / "age_profile.csv", index=False)
    admitted = pd.DataFrame([{"arm": arm, "candidates": len(decisions),
                              "v9_admitted": int(decisions.v9.sum()),
                              "arm_admitted": int(decisions[arm].sum()) if arm != "v9" else int(decisions.v9.sum())}
                             for arm in ARMS])
    admitted.to_csv(destination / "admissions.csv", index=False)
    monthly = trades.loc[~trades.censored].copy()
    monthly["entry_month"] = monthly.entry_time.dt.strftime("%Y-%m")
    monthly.groupby(["entry_month", "arm"]).agg(trades=("net_r", "size"), total_r=("net_r", "sum")).reset_index().to_csv(destination / "monthly.csv", index=False)
    # Group by the shared bucket, not by the per-age reason string: that one
    # produced one row per distinct age and was unreadable.
    gate = decisions.assign(bucket=age_buckets(decisions.trendline_break_age))
    gate.groupby("bucket", observed=False).agg(candidates=("v9", "size"), v9_admitted=("v9", "sum")).reset_index().to_csv(destination / "gate_counts.csv", index=False)
    crossings = pd.read_csv(CROSSINGS)
    crossing_outcomes(trades, decisions, crossings).to_csv(destination / "crossing_outcomes.csv", index=False)
    crossings.groupby(["timeframe_min", "side", "led_by"]).agg(
        crossings=("break_i", "size"), median_price_move_atr=("price_move_atr", "median"),
        median_line_drop_atr=("line_drop_atr", "median"), median_bars_held=("bars_held", "median")
    ).reset_index().to_csv(destination / "crossing_types.csv", index=False)
    for name, frame in (("trades", trades), ("decisions", decisions), ("controls", controls)):
        frame.to_csv(destination / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    stats = {"replay_manifest_sha256": digest(output / "manifest.json"), "strategy_version": VERSION,
             "streams": len(receipts), "streams_without_pre_cut_bars": skipped,
             "trades": len(trades), "candidates": len(decisions), "controls": len(controls),
             "holdout_consumption": 0, "cut_exclusive_bar_close": str(CUT),
             "history": "pre-holdout span already exposed by V8/V9 work; not blind validation",
             "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
             "builder_sha256": digest(Path(__file__)), "crossings_sha256": digest(CROSSINGS),
             "source_receipts": receipts,
             "files": {p.name: digest(p) for p in destination.iterdir() if p.is_file() and p.name != "statistics_receipt.json"}}
    (destination / "statistics_receipt.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(summary.to_string(index=False))
    print(pd.DataFrame(details).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.input, args.output)
