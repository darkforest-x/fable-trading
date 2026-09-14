"""Aggregate saved BE05 exit interventions without reading price history.

Inputs are completed receipt-bound native and common replay outputs. Each
comparison retains its strategy identity, frozen event key, cost and censoring.
Per-exit cumulative R is an event diagnostic, never a shared-account curve.
Weekly-block intervals describe this reused history; they are not a forecast.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2024-09-10", tz="UTC")
SPLIT = pd.Timestamp("2025-09-10", tz="UTC")
END = pd.Timestamp("2026-09-10", tz="UTC")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_saved(common: Path, native: Path) -> tuple[pd.DataFrame, list[dict]]:
    """Read only saved outcome files, preserving native versus common contracts."""
    frames, sources = [], []
    manifest = json.loads((common / "manifest.json").read_text())
    if not manifest.get("complete") or int(manifest["streams"]) != 3531:
        raise ValueError("common replay is not the complete requested scope")
    native_receipt = json.loads((native / "receipt.json").read_text())
    if native_receipt.get("status") != "complete" or int(native_receipt["paired_events"]) != 6185:
        raise ValueError("native replay is not the complete requested scope")
    receipts = sorted((common / "streams").glob("*/completion.json"))
    if len(receipts) != int(manifest["streams"]):
        raise ValueError("manifest stream count does not match saved receipts")
    for receipt_path in receipts:
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("status") != "complete":
            raise ValueError(f"unfinished stream {receipt_path}")
        for filename, expected in receipt["files"].items():
            path = receipt_path.parent / filename
            if digest(path) != expected:
                raise ValueError(f"saved outcome changed {path}")
            if not filename.endswith(".csv.gz"):
                continue
            parts = filename.split(".")
            kind = next((v for v in parts if v in ("fixed_baseline", "fixed_be05", "serial_baseline", "serial_be05")), None)
            if kind is None:
                continue
            system = "v1_common" if "v1_common_execution_long" in parts else "v8"
            frame = pd.read_csv(path)
            sources.append({"path": str(path), "sha256": expected, "rows": len(frame)})
            if frame.empty:
                continue
            frame["system"], frame["mode"], frame["rule"] = system, *kind.split("_")
            frame["event_key"] = frame.stream_key + ":" + frame.signal_i.astype(str) + ":" + frame.side.astype(str)
            frames.append(frame)
    native_path = native / "paired_trade_ledger.csv.gz"
    if digest(native_path) != native_receipt["files"][native_path.name]:
        raise ValueError("native paired ledger changed")
    n = pd.read_csv(native_path)
    sources.append({"path": str(native_path), "sha256": digest(native_path), "rows": len(n)})
    for rule in ("baseline", "be05"):
        frame = n[["event_id", "venue", "symbol", "timeframe_min", "entry_time", "entry_price", "initial_stop", "initial_risk"]].copy()
        for field in ("exit_time", "exit_price", "exit_reason", "net_r", "net_return", "mfe_r", "censored"):
            frame[field] = n[f"{rule}_{field}"]
        frame["system"], frame["mode"], frame["rule"], frame["side"] = "v1_native", "fixed", rule, 1
        frame["event_key"] = frame.event_id
        frames.append(frame)
    table = pd.concat(frames, ignore_index=True)
    for name in ("entry_time", "exit_time"):
        table[name] = pd.to_datetime(table[name], utc=True, errors="raise")
    if not table.censored.isin([True, False]).all():
        raise ValueError("ambiguous persisted censoring values")
    table["censored"] = table.censored.astype(bool)
    if table.duplicated(["system", "mode", "rule", "event_key"]).any():
        raise ValueError("duplicate saved trade identity")
    return table, sources


def metrics(table: pd.DataFrame) -> dict:
    """Compute closed-event metrics, ordered by actual recorded exit time."""
    closed = table.loc[~table.censored].sort_values(["exit_time", "entry_time", "event_key"], kind="stable")
    r = closed.net_r.astype(float)
    if r.isna().any():
        raise ValueError("closed outcome has undefined R")
    def pf(values):
        losses = -values[values < 0].sum()
        return float(values[values > 0].sum() / losses) if losses > 0 else None
    curve = np.r_[0., r.cumsum().to_numpy()]
    return dict(events=len(table), closed=len(closed), censored=int(table.censored.sum()),
                win_rate=float((r > 0).mean()) if len(r) else None,
                mean_r=float(r.mean()) if len(r) else None, total_r=float(r.sum()),
                pf_r=pf(r), pf_nominal=pf(closed.net_return.astype(float)),
                realized_ge10=int((r >= 10).sum()),
                event_drawdown_r=float((np.maximum.accumulate(curve) - curve).max()),
                total_r_without_best=float(r.sum()-r.max()) if len(r) else None)


def periods(table: pd.DataFrame):
    """Earlier-year outcomes crossing the cut are explicitly excluded, never leaked."""
    yield "full", table
    yield "earlier", table.loc[(table.entry_time < SPLIT) & (table.exit_time < SPLIT)]
    yield "later", table.loc[table.entry_time >= SPLIT]


def pair_saved(table: pd.DataFrame) -> pd.DataFrame:
    left = table.loc[(table["mode"] == "fixed") & (table.rule == "baseline")]
    right = table.loc[(table["mode"] == "fixed") & (table.rule == "be05")]
    ids = ["system", "event_key", "venue", "symbol", "timeframe_min", "side", "entry_time"]
    outcomes = ["net_r", "net_return", "exit_time", "exit_price", "censored"]
    pairs = left[ids+outcomes].merge(right[ids+outcomes], on=ids, suffixes=("_baseline", "_be05"), validate="one_to_one")
    if len(pairs) != len(left) or len(left) != len(right):
        raise ValueError("unmatched original-entry comparison")
    pairs["joint_closed"] = ~pairs.censored_baseline & ~pairs.censored_be05
    pairs["delta_r"] = pairs.net_r_be05 - pairs.net_r_baseline
    return pairs


def paired_stats(p: pd.DataFrame) -> dict:
    p = p.loc[p.joint_closed]
    delta = p.delta_r
    improved_loser = (p.net_r_baseline <= 0) & (delta > 1e-9)
    harmed_winner = (p.net_r_baseline > 0) & (delta < -1e-9)
    original_tail = p.net_r_baseline >= 10
    # Resample whole calendar-week groups so simultaneous venue/market events
    # are not falsely counted as independent trades in the interval.
    week = p.entry_time.dt.tz_localize(None).dt.to_period("W").astype(str)
    blocks = pd.DataFrame({"week": week, "delta": delta}).groupby("week").delta.agg(["sum", "size"])
    interval = [None, None]
    if len(blocks) >= 12:
        rng = np.random.default_rng(14092026)
        ix = rng.integers(0, len(blocks), size=(2000, len(blocks)))
        values = blocks["sum"].to_numpy()[ix].sum(axis=1) / blocks["size"].to_numpy()[ix].sum(axis=1)
        interval = np.quantile(values, [.025, .975]).tolist()
    outcome = {}
    for rule in ("baseline", "be05"):
        r = p[f"net_r_{rule}"]
        loss = -r.loc[r < 0].sum()
        outcome.update({f"{rule}_total_r":float(r.sum()), f"{rule}_mean_r":float(r.mean()) if len(r) else None,
                        f"{rule}_win_rate":float((r > 0).mean()) if len(r) else None,
                        f"{rule}_pf_r":float(r.loc[r > 0].sum()/loss) if loss > 0 else None})
    return dict(**outcome, joint_closed=len(p), delta_r=float(delta.sum()), mean_delta_r=float(delta.mean()) if len(p) else None,
                rescued_losers=int(improved_loser.sum()), rescue_delta_r=float(delta[improved_loser].sum()),
                harmed_winners=int(harmed_winner.sum()), harmed_delta_r=float(delta[harmed_winner].sum()),
                original_realized_ge10=int(original_tail.sum()),
                retained_original_ge10=int((original_tail & (p.net_r_be05 >= 10)).sum()),
                be_loss_from_original_win=int(((p.net_r_baseline > 0) & (p.net_r_be05 <= 0)).sum()),
                weekly_blocks=len(blocks), mean_delta_weekly_ci_low=interval[0], mean_delta_weekly_ci_high=interval[1])


def run(common: Path, native: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    table, sources = load_saved(common, native)
    pairs = pair_saved(table)
    rows, changes = [], []
    for group, frame in table.groupby(["system", "mode", "rule"], sort=True):
        meta = dict(zip(["system", "mode", "rule"], group))
        for period, part in periods(frame):
            rows.append({**meta, "period": period, "timeframe_min": "all", **metrics(part)})
            for minutes, tf in part.groupby("timeframe_min"):
                rows.append({**meta, "period": period, "timeframe_min": int(minutes), **metrics(tf)})
    for system, frame in pairs.groupby("system"):
        slices = [("full", frame), ("earlier", frame.loc[(frame.entry_time<SPLIT)&(frame.exit_time_baseline<SPLIT)&(frame.exit_time_be05<SPLIT)]), ("later", frame.loc[frame.entry_time>=SPLIT])]
        for period, part in slices:
            changes.append(dict(system=system, period=period, timeframe_min="all", **paired_stats(part)))
            for minutes, tf in part.groupby("timeframe_min"):
                changes.append(dict(system=system, period=period, timeframe_min=int(minutes), **paired_stats(tf)))
    pd.DataFrame(rows).to_csv(output / "metrics.csv", index=False)
    pd.DataFrame(changes).to_csv(output / "paired_changes.csv", index=False)
    pairs.to_csv(output / "paired_trades.csv.gz", index=False, compression={"method":"gzip","mtime":0})
    table.to_csv(output / "all_outcomes.csv.gz", index=False, compression={"method":"gzip","mtime":0})
    examples = pd.concat([pd.concat([p.nsmallest(5,"delta_r"), p.nlargest(5,"delta_r")])
                          for _, p in pairs.loc[pairs.joint_closed].groupby("system")], ignore_index=True)
    examples.to_csv(output / "delta_examples.csv", index=False)
    (output / "aggregation_receipt.json").write_text(json.dumps({"source_kind":"saved outcomes only", "sources":sources,
       "script_sha256":digest(Path(__file__)), "rows":len(table), "pairs":len(pairs), "weekly_bootstrap_seed":14092026,
       "outputs":{p.name:digest(p) for p in output.iterdir() if p.is_file()}},ensure_ascii=False,indent=2))
    print(pd.DataFrame(rows).query("period == 'full' and timeframe_min == 'all'").to_string(index=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common",type=Path,required=True)
    parser.add_argument("--native",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    run(args.common,args.native,args.output)
