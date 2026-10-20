"""Config-driven range/flip research using the installed Nautilus runtime.

Usage: scripts/backtest-range --config <json> [--output <new-run-directory>]
Inputs are existing read-only OHLC and frozen candidate IDs, never outcomes.
Completed 5m -> 15m/H1 feature preparation is reused from the audited study.
Stops use only the latest N completed 5m bars at entry. The cache is invalidated
by file path/size/mtime and preparation parameters, with no content hashing.
Only the selected native adapter is imported, and unchanged completed runs
return their small receipt without replaying bars or re-auditing every path.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT/"experiments/active/exp-range-nautilus-20261020-v1/config.json"
CODE = [
    "yoyo/layers/l3_backtest/nautilus_independent.py",
    "yoyo/evaluation/range_framework.py",
    "yoyo/evaluation/pepe_range_exits.py",
    "yoyo/evaluation/monday_range_v2_2_reference.py",
]
ENTRY_FIELDS = [
    "pair_id", "entry_i", "side", "segment", "week", "bucket", "end_i",
    "entry_time", "population",
]


def runtime_versions():
    return {name: version(name) for name in ("nautilus_trader", "pandas", "numpy")}


def file_stamp(path):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    return dict(path=str(path), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)+"\n")


def prepared(cfg, cache_dir):
    """Cache aligned OHLC/causal features and candidate metadata, never outcomes."""
    identity = dict(
        schema=1, runtime=runtime_versions(), data=file_stamp(ROOT/cfg["data"]),
        candidates=file_stamp(ROOT/cfg["entry_candidates"]),
        code=[file_stamp(ROOT/p) for p in CODE[1:]],
    )
    meta, path = cache_dir/"features.json", cache_dir/"features.npz"
    hit = meta.exists() and path.exists() and json.loads(meta.read_text()) == identity
    if hit:
        with np.load(path, allow_pickle=False) as z:
            ctx = {k: z[k] for k in ("bars", "atr", "hour")}
            frame = pd.DataFrame(ctx["bars"], columns=["open", "high", "low", "close"],
                                 index=pd.to_datetime(z["times"], utc=True))
        ctx["frame"] = frame
    else:
        from yoyo.evaluation.pepe_range_exits import prepare
        frame = pd.read_csv(ROOT/cfg["data"], float_precision="round_trip")
        times = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)+pd.Timedelta(minutes=5)
        if times.duplicated().any() or not times.is_monotonic_increasing:
            raise ValueError("Input bars must be unique and ordered")
        if not (times.diff().dropna() == pd.Timedelta(minutes=5)).all():
            raise ValueError("Missing 5m candles; do not silently bridge data gaps")
        frame.index = pd.DatetimeIndex(times).as_unit("ns")
        frame = frame[["open", "high", "low", "close"]]
        if ((frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
                or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()):
            raise ValueError("Invalid OHLC range")
        ctx = prepare(frame)
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, bars=ctx["bars"], atr=ctx["atr"],
                            hour=ctx["hour"], times=frame.index.asi8)
        dump(meta, identity)
    # Explicit projection prevents accidental reuse of expected exits or MFE.
    candidates = pd.read_csv(ROOT/cfg["entry_candidates"], usecols=ENTRY_FIELDS)
    from yoyo.evaluation.pepe_range_exits import stop_events
    entries, _ = stop_events(ctx, candidates, cfg["stop_window_5m"],
                             cfg["buffer_percent"], cfg["tick"])
    entries["trade_id"] = entries.population+"-"+entries.pair_id.astype(str)
    if cfg.get("maximum_risk_percent", 0) > 0:
        entries = entries[entries.risk/entries.entry*100 <= cfg["maximum_risk_percent"]+1e-10]
    return ctx, entries, hit


def compare_golden(rows, path, tick):
    """Fail closed on any path difference; expected results never enter engine."""
    golden = pd.read_csv(path, float_precision="round_trip")
    golden["trade_id"] = golden.population+"-"+golden.pair_id.astype(str)
    actual, expected = rows.set_index("trade_id"), golden.set_index("trade_id")
    if set(actual.index) != set(expected.index):
        raise AssertionError("Golden entry set differs")
    expected = expected.loc[actual.index]
    exact = ["entry_i", "side", "end_i", "exit_i", "reason", "arm_i",
             "be_trigger_i", "be_effective_i", "stop_kind", "deferred_closes", "steps"]
    prices = ["entry", "stop", "risk", "exit_price", "protection", "be_price"]
    returns = ["peak_r", "gross_r", "fee_r", "net_r", "net_notional_bp"]
    for col in exact:
        np.testing.assert_array_equal(actual[col], expected[col], err_msg=col)
    for col in prices:
        np.testing.assert_allclose(actual[col], expected[col], rtol=0, atol=tick*1e-7, err_msg=col)
    for col in returns:
        np.testing.assert_allclose(actual[col], expected[col], rtol=0, atol=1e-7, err_msg=col)
    expected_cost = actual.quantity * actual.fee_r * actual.risk
    np.testing.assert_allclose(actual.framework_commission, expected_cost, rtol=0, atol=2e-8)
    np.testing.assert_array_equal(
        pd.to_datetime(actual.exit_time, utc=True).dt.as_unit("ns").astype("int64"),
        pd.to_datetime(expected.exit_time, utc=True).dt.as_unit("ns").astype("int64"),
    )
    return dict(rows=len(rows), compared_fields=exact+prices+returns+["exit_time"],
                native_fee_checks=len(rows), status="passed")


def summarize(rows):
    result = []
    for segment in (-1, 0, 1, 2):
        part = rows if segment == -1 else rows[rows.segment == segment]
        for population in ("signal", "random"):
            p = part[part.population == population]
            if p.empty:
                continue
            result.append(dict(
                segment=segment, population=population, n=len(p),
                net_r=float(p.net_r.sum()), gross_r=float(p.gross_r.sum()),
                win_rate=float((p.net_r > 1e-9).mean()),
                gross_10r=int((p.gross_r >= 10-1e-7).sum()),
                gross_20r=int((p.gross_r >= 20-1e-7).sum()),
                boundary_marks=int((p.reason == 0).sum()),
                constant_1000_notional_pnl=float(p.net_notional_bp.sum()/10),
            ))
    return result


def run(config, output=None):
    started = time.perf_counter()
    cfg = json.loads(config.read_text())
    allowed = {"schema", "experiment_id", "engine", "data", "entry_candidates", "symbol",
               "tick", "market_tick", "stop_window_5m", "buffer_percent", "maximum_risk_percent",
               "round_trip_cost", "policies", "output"}
    if set(cfg)-allowed or cfg["schema"] != 1 or cfg["engine"] != "nautilus":
        raise ValueError("Unknown configuration fields or adapter")
    if (not isinstance(cfg["stop_window_5m"], int) or cfg["stop_window_5m"] < 1
            or cfg["buffer_percent"] < 0 or cfg.get("maximum_risk_percent", 0) < 0):
        raise ValueError("Invalid stop parameters")
    output = (ROOT/cfg["output"]) if output is None else output.resolve()
    identity = dict(config=cfg, runtime=runtime_versions(), code=[file_stamp(ROOT/p) for p in CODE],
                    inputs=[file_stamp(ROOT/cfg[k]) for k in ("data", "entry_candidates")],
                    goldens=[file_stamp(ROOT/p["golden_ledger"]) for p in cfg["policies"]
                             if p.get("golden_ledger")])
    receipt = output/"receipt.json"
    if receipt.exists():
        old = json.loads(receipt.read_text())
        if old["identity"] == identity:
            expected_files = [output/p["name"]/name for p in cfg["policies"]
                              for name in ("trades.csv", "fills.csv", "audit.json")]
            if not all(p.exists() and p.stat().st_size for p in expected_files):
                raise RuntimeError("Completed run outputs missing; choose a new output directory")
            return dict(cached=True, elapsed_seconds=time.perf_counter()-started,
                        output=str(output), results=old["results"])
        raise RuntimeError("Existing run differs; preserve it and select a new --output directory")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Incomplete run directory exists; use a new --output directory")
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *CODE], cwd=ROOT).returncode:
        raise RuntimeError("Commit the changed builder before generating research results")
    ctx, entries, cache_hit = prepared(cfg, config.parent/".cache")
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    names = [p["name"] for p in cfg["policies"]]
    if len(names) != len(set(names)) or any(not n.replace("_", "").isalnum() for n in names):
        raise ValueError("Policy names must be unique alphanumeric/underscore values")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for spec in cfg["policies"]:
        policy = ExitPolicy(round_trip_cost=cfg["round_trip_cost"],
                            **{k: v for k, v in spec.items() if k not in ("name", "golden_ledger")})
        rows, fills, audit = run_native(ctx["frame"], ctx["atr"], ctx["hour"], entries,
                                       policy, cfg["tick"], cfg["symbol"], cfg.get("market_tick"))
        if spec.get("golden_ledger"):
            audit["parity"] = compare_golden(rows, ROOT/spec["golden_ledger"], cfg["tick"])
        destination = output/spec["name"]
        destination.mkdir()
        rows.to_csv(destination/"trades.csv", index=False)
        fills.to_csv(destination/"fills.csv", index=False)
        dump(destination/"audit.json", audit)
        summary = summarize(rows)
        results.append(dict(name=spec["name"], summary=summary, audit=audit))
        signal = next(s for s in summary if s["segment"] == -1 and s["population"] == "signal")
        print(json.dumps(dict(policy=spec["name"], **signal,
                              replay_seconds=audit["replay_seconds"]), ensure_ascii=False), flush=True)
    record = dict(
        identity=identity, source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        data_rows=len(ctx["frame"]), data_first_close=str(ctx["frame"].index[0]),
        data_last_close=str(ctx["frame"].index[-1]), feature_cache_hit=cache_hit,
        elapsed_seconds=time.perf_counter()-started, results=results,
        production_eligible=False, training_eligible=False,
        limitations="Historical independent entry returns, not funded-account returns. "
                    "Frozen 20bp entry-notional cost; no funding, latency, spread or capacity model. "
                    "Engineering parity only; not a new profitability validation.",
    )
    dump(receipt, record)
    return dict(cached=False, elapsed_seconds=record["elapsed_seconds"],
                output=str(output), results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output)
    # Keep terminal/LLM context small; full ledgers and segment/control summaries
    # are saved once in the receipt and loaded only when explicitly needed.
    compact = {k: v for k, v in result.items() if k != "results"}
    if result["cached"]:
        compact["policies"] = [
            dict(name=p["name"], **next(s for s in p["summary"]
                 if s["segment"] == -1 and s["population"] == "signal"))
            for p in result["results"]
        ]
    print(json.dumps(compact, ensure_ascii=False))


if __name__ == "__main__":
    main()
