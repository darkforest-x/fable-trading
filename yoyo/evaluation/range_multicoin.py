"""Frozen cross-coin research and native preview, using existing local inputs.

Read confirmed-month OKX 1m archives and confirmed public-API extensions, or
explicitly configured Binance native 5m sources; retain exchange identity.
Require complete continuous bars. No fetching or hashing.
Features are computed only from completed 5m/15m/H1 candles by the existing
preparer. Controls match each symbol, side, UTC week, segment and causal ATR bucket.
Changing the symbol is the only new strategy comparison; baseline and BE3 are
pre-existing frozen policies. Three time segments are descriptive, not untouched.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import prepare, entries, inference
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_native_preview import preview

EXP = ROOT / "experiments/active/exp-range-multicoin-20261020-v1"
SOURCE = ROOT / "data/crypto/market/kline_lowtf_20260922"
PEPE = ROOT / "experiments/active/exp-pepe-range-exits-20261020-v1/inputs/okx_pepe_5m.csv.gz"
NATIVE = ROOT / "experiments/active/exp-range-nautilus-20261020-v1/results/baseline"
CODE = ["yoyo/evaluation/range_multicoin.py", "yoyo/evaluation/range_native_preview.py"]


def aggregate_minutes(data, start, end):
    """Reject conflicting duplicates, missing minutes and incomplete 5m bins."""
    start_ms, end_ms = int(pd.Timestamp(start).timestamp()*1000), int(pd.Timestamp(end).timestamp()*1000)
    data = data.loc[(data.ts >= start_ms) & (data.ts < end_ms),
                    ["ts", "open", "high", "low", "close"]].copy()
    data = data.drop_duplicates().sort_values("ts")
    if data.ts.duplicated().any():
        raise ValueError("Conflicting duplicate minutes")
    expected = np.arange(start_ms, end_ms, 60_000, dtype=np.int64)
    if not np.array_equal(data.ts.to_numpy(), expected):
        raise ValueError("Missing or extra minute candles")
    if (not np.isfinite(data.to_numpy()).all() or
            (data[["open", "high", "low", "close"]] <= 0).any().any() or
            (data.high < data[["open", "close", "low"]].max(axis=1)).any() or
            (data.low > data[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("Invalid minute OHLC")
    data.index = pd.to_datetime(data.ts, unit="ms", utc=True)
    groups = data.resample("5min")
    if not (groups.close.count() == 5).all():
        raise ValueError("Incomplete 5m aggregation")
    five = groups.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    five.insert(0, "ts", five.index.as_unit("ns").asi8 // 1_000_000)
    return five.reset_index(drop=True)


def load_symbol(symbol, cfg):
    if symbol in cfg.get("native_5m_sources", {}):
        from yoyo.evaluation.pepe_range_data import validate
        spec = cfg["native_5m_sources"][symbol]
        paths = [ROOT/p for p in spec["paths"]]
        start = int(pd.Timestamp(cfg["warmup"]).timestamp()*1000)
        end = int(pd.Timestamp(cfg["end"]).timestamp()*1000)
        parts = []
        for path in paths:
            part = pd.read_csv(path, float_precision="round_trip",
                               usecols=["ts", "open", "high", "low", "close"])
            parts.append(part[(part.ts >= start) & (part.ts < end)])
        # Exact duplicates are benign; overlapping conflicting OHLC is not.
        frame = validate(pd.concat(parts, ignore_index=True))
        if not np.isfinite(frame.to_numpy()).all() or not np.array_equal(
                frame.ts.to_numpy(), np.arange(start, end, 300_000)):
            raise ValueError("Native 5m source has missing/invalid candles")
        return frame, [file_stamp(p) for p in paths + [ROOT/p for p in spec["receipts"]]]
    if symbol == "PEPE":
        frame = pd.read_csv(PEPE, float_precision="round_trip")
        ts = pd.to_datetime(frame.ts, unit="ms", utc=True)
        return frame[(ts >= pd.Timestamp(cfg["warmup"])) & (ts < pd.Timestamp(cfg["end"]))], [file_stamp(PEPE)]
    archive = SOURCE / f"okx_{symbol}_USDT_SWAP_1m_1402560.csv"
    extension = SOURCE / f"okx_api_{symbol}_USDT_SWAP_1m_20260831T1600_20260922T1400.csv"
    audit_path = SOURCE / "archive_fetch_summary.json"
    meta = json.loads(audit_path.read_text())
    audit = next(r for r in meta["results"] if r["contract"]["symbol"] == f"{symbol}_USDT_SWAP")
    months = [m for m in audit["monthly_audits"] if m["month"] >= "2024-08"]
    expected_months = pd.period_range("2024-08", "2026-08", freq="M").astype(str).tolist()
    if [m["month"] for m in months] != expected_months or any(m["confirm_values"] != ["1"] for m in months):
        raise ValueError("Requested archive months lack all-confirmed lineage")
    api_path = SOURCE / f"api_{symbol}_USDT_SWAP.json"
    api_meta = json.loads(api_path.read_text())
    if not api_meta.get("confirm_only"):
        raise ValueError("API extension lacks confirmation guarantee")
    raw = pd.concat([pd.read_csv(p, float_precision="round_trip") for p in (archive, extension)])
    return aggregate_minutes(raw, cfg["warmup"], cfg["end"]), [
        file_stamp(p) for p in (archive, extension, audit_path, api_path)]


def make_preview():
    output = EXP / "preview/pepe-native.html"
    if output.exists():
        return json.loads((output.parent / "receipt.json").read_text())
    _, receipt = preview(PEPE, NATIVE / "trades.csv", NATIVE / "fills.csv", output,
                         "PEPE", "2026-10-06T00:00:00Z", "2026-10-20T06:00:00Z")
    dump(output.parent / "receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--config", type=Path, default=EXP/"config.json")
    args = parser.parse_args()
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *CODE], cwd=ROOT).returncode:
        raise RuntimeError("Commit builder before generating")
    if args.preview_only:
        print(json.dumps(make_preview(), ensure_ascii=False), flush=True)
        return
    config_file = args.config.resolve()
    destination = config_file.parent
    cfg = json.loads(config_file.read_text())
    if destination == EXP:
        print(json.dumps(make_preview(), ensure_ascii=False), flush=True)
    started = time.perf_counter()
    summary, tests, paths, runs = [], [], [], []
    for symbol, ticks in cfg["symbols"].items():
        reuse = cfg.get("reuse_configs", {}).get(symbol)
        if reuse:
            config_path = ROOT/reuse
            work = config_path.parent
            old_study = json.loads((work.parent/"config.json").read_text())
            for key in ("warmup", "start", "splits", "end", "windows_5m",
                        "seed", "volatility_edges", "policies"):
                if cfg[key] != old_study[key]:
                    raise ValueError(f"Cannot reuse different study {key}")
            if ticks != old_study["symbols"][symbol]:
                raise ValueError("Cannot reuse different precision")
        else:
            work = destination / symbol.lower()
            work.mkdir(exist_ok=True)
            config_path = work / "config.json"
        if not config_path.exists():
            five, sources = load_symbol(symbol, cfg)
            five.to_csv(work / "candles.csv.gz", index=False)
            close_frame = five[["open", "high", "low", "close"]].copy()
            close_frame.index = pd.DatetimeIndex(pd.to_datetime(five.ts, unit="ms", utc=True)
                                                 + pd.Timedelta(minutes=5)).as_unit("ns")
            ctx = prepare(close_frame)
            signal, control, unmatched = entries(ctx, cfg)
            for population, rows in (("signal", signal), ("random", control)):
                rows["population"] = population
                rows["entry_time"] = close_frame.index[rows.entry_i.to_numpy(int)].astype(str)
            pd.concat([signal, control]).to_csv(work / "entries.csv", index=False)
            dump(work / "input_receipt.json", dict(
                symbol=symbol, rows=len(five), sources=sources, signals=len(signal),
                controls=len(control), unmatched_ids=unmatched,
                start=cfg["start"], end=cfg["end"], warmup=cfg["warmup"],
                venue=cfg.get("venues", {}).get(symbol, "OKX"),
                source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                confirmation=cfg.get("native_5m_sources", {}).get(symbol, {}).get(
                    "confirmation", "Existing monthly audit: all 1 from Aug2024; extension confirm_only. "
                                    "PEPE uses prior repaired native 5m receipt."),
            ))
            native = dict(
                schema=1, experiment_id=cfg["experiment_id"], engine="nautilus",
                symbol=symbol, **ticks, data=str((work/"candles.csv.gz").relative_to(ROOT)),
                entry_candidates=str((work/"entries.csv").relative_to(ROOT)),
                stop_window_5m=5, buffer_percent=.05, maximum_risk_percent=0,
                round_trip_cost=.002, policies=cfg["policies"],
                output=str((work/"results").relative_to(ROOT)),
            )
            dump(config_path, native)
        result = run(config_path)
        runs.append(dict(symbol=symbol, cached=result["cached"],
                         elapsed_seconds=result["elapsed_seconds"], config=str(config_path)))
        for policy in result["results"]:
            rows = pd.read_csv(work/"results"/policy["name"]/"trades.csv")
            for segment in (-1, 0, 1, 2):
                part = rows if segment == -1 else rows[rows.segment == segment]
                ss, rr = part[part.population == "signal"], part[part.population == "random"]
                paired = ss.merge(rr[["pair_id", "net_r"]], on="pair_id", suffixes=("", "_random"),
                                  validate="one_to_one")
                test = inference(paired, cfg["seed"])
                tests.append(dict(symbol=symbol, policy=policy["name"], segment=segment,
                                  paired_n=len(paired), **test))
                for stats in policy["summary"]:
                    if stats["segment"] == segment:
                        group = ss if stats["population"] == "signal" else rr
                        summary.append(dict(
                            symbol=symbol, venue=cfg.get("venues", {}).get(symbol, "OKX"),
                            policy=policy["name"], **stats,
                            max_peak_r=float(group.peak_r.max()), max_gross_r=float(group.gross_r.max()),
                            mean_fee_r=float(group.fee_r.mean()),
                            peak_10r=int((group.peak_r >= 10).sum()),
                            peak_20r=int((group.peak_r >= 20).sum()),
                        ))
            paths.append(str(work/"results"/policy["name"]/"trades.csv"))
    pd.DataFrame(summary).to_csv(destination/"summary.csv", index=False)
    dump(destination/"inference.json", tests)
    dump(destination/"receipt.json", dict(
        experiment_id=cfg["experiment_id"], elapsed_seconds=time.perf_counter()-started,
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(), ledgers=paths, runs=runs,
        venues=cfg.get("venues", {}),
        training_eligible=False, production_eligible=False,
        limitations="Independent overlapping R, not account returns. Frozen 20bp entry-notional cost; "
                    "no funding/spread/latency. Nominal weekly inference, no parameter selection. "
                    "Historical MFE excludes stop-hit bars. Segment-end marks reported explicitly.",
    ))
    print(pd.DataFrame(summary).query("segment == -1 and population == 'signal'")[
        ["symbol", "policy", "n", "net_r", "win_rate", "gross_10r", "gross_20r"]].to_string(index=False))


if __name__ == "__main__":
    main()
