"""Receipt-bound, fixed-family trend comparison authorized on 2026-09-20.

See exp-trend-baselines-20260920-v1/PROJECT_PLAN.md. Reuses frozen V9 prices,
ATR, next-open fills, stop/trail and cost. Only the categorical signal family
changes; its raw opposite events also exit positions. Feature columns/windows
are documented in trend_baseline_signals and the existing HTF-SMA module.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v9_htf_sma_study as old
from yoyo.evaluation.trend_baseline_signals import signal_families

EXP = Path("experiments/active/exp-trend-baselines-20260920-v1")
PRIOR = old.EXP / "run_v1"
TEST = Path("tests/evaluation/test_trend_baseline_study.py")
ARMS = ("v9", "v9_1", "dc20", "dc55", "sma20_60")


def config():
    return json.loads((EXP / "config.json").read_text())


def preparation(facts, symbol, meta, tf, minutes):
    """Share readiness/guards without filtering opposite exits by entry guards."""
    p = old.build_prepared(facts, symbol, meta, tf, minutes)
    signals = signal_families(p.frame, minutes)
    rv = p.frame.rv.to_numpy(float)
    clock = p.frame.index + pd.Timedelta(minutes=minutes)
    guards = (np.isfinite(rv) & (rv >= 0) & (rv <= 50)
              & (meta["asset"] not in ("", "USDC")) & (clock.dayofweek != 6))
    common = signals.common_ready.to_numpy(bool) & p.frame.ready.fillna(False).to_numpy(bool)
    window = old.source.in_window(p.frame.index, minutes)
    # The control sampler reads ready; this also makes its pools obey guards.
    frame = p.frame.copy()
    frame["ready"] = common & guards
    cache = dict(p.context.cache); cache["bars"] = frame
    shared = replace(p, frame=frame, context=replace(p.context, cache=cache),
                     ordinal=dict(zip(frame.index, range(len(frame)))))
    return p, shared, signals, common, np.asarray(guards), window


def execute(prepared, allowed, raw_side, arm):
    """Run an independent serial position path with family-owned raw exits."""
    p = replace(prepared, allowed=np.asarray(allowed, bool), raw_side=np.asarray(raw_side, int))
    trades, fills, _ = old.engine.replay_serial(p.context, arm="v8", enable_be=False, prepared=p)
    for table in (trades, fills):
        table["arm"] = arm
        table["timeframe"] = p.context.identity["timeframe"]
        if len(table):
            table["trade_id"] = table.trade_id.str.replace(":v8:baseline:", f":{arm}:baseline:", regex=False)
    trades["event_key"] = [f"{p.context.key}|{pd.Timestamp(t).isoformat()}|{int(s)}"
                           for t, s in zip(trades.signal_bar_open, trades.side)]
    if arm.startswith("dc") or arm.startswith("sma"):
        trades["exit_reason"] = trades.exit_reason.replace("opposite_v6_next_open", "opposite_signal_next_open")
        if len(fills):
            fills["reason"] = fills.reason.replace("opposite_v6_next_open", "opposite_signal_next_open")
    closed = trades.loc[~trades.censored.astype(bool)]
    np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
    np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-9)
    return p, trades, fills


def compare_prior(actual, expected):
    fields = [*old.engine.KEY, "censored", "entry_time", "exit_time"]
    a, b = actual[fields].copy(), expected[fields].copy()
    for col in ("entry_time", "exit_time"):
        a[col] = pd.to_datetime(a[col], utc=True); b[col] = pd.to_datetime(b[col], utc=True)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True),
                                  check_dtype=False, rtol=1e-9, atol=1e-9)


def one(args):
    symbol, item, identity_hash, output = args
    folder = Path(output) / "streams" / symbol
    if (folder / "completion.json").exists():
        receipt = json.loads((folder / "completion.json").read_text())
        assert receipt["identity_hash"] == identity_hash
        for name, sha in receipt["files"].items(): assert old.source.digest(folder / name) == sha
        return receipt
    began = time.monotonic(); cfg = config(); path = Path(item["path"])
    assert old.source.digest(path) == item["sha256"]
    prior_folder = PRIOR / "streams" / symbol
    old_receipt = json.loads((prior_folder / "completion.json").read_text())
    assert old.source.digest(prior_folder / "trades.csv.gz") == old_receipt["files"]["trades.csv.gz"]
    prior_trades = pd.read_csv(prior_folder / "trades.csv.gz")
    base = old.inc.guarded_5m(path, old.source.START - pd.Timedelta(days=old.source.WARMUP_BARS))
    parts = {name: [] for name in ("trades", "fills", "controls", "signals")}
    coverage = []
    for tf, minutes in cfg["timeframes"]:
        bars = old.v11.bars_for(base, minutes)
        if not len(bars): raise ValueError(f"Missing required stream {symbol}/{tf}")
        facts = old.source.v9_facts(bars, minutes, item["meta"]["asset"], float(item["meta"]["tick"]))
        legacy, shared, signals, common, guards, window = preparation(facts, symbol, item["meta"], tf, minutes)
        _, original, _ = execute(legacy, legacy.allowed, legacy.raw_side, "v9_original")
        expected = prior_trades.loc[prior_trades.timeframe.eq(tf) & prior_trades.scope.eq("actual") & prior_trades.length.eq(0)]
        compare_prior(original, expected)
        h1 = old.confirmed_sma(base, shared.frame.index, 60, (60,)) if minutes == 15 else None
        h1gate = old.side_gate(shared.close, shared.raw_side, h1.sma_60) if h1 is not None else np.ones(len(bars), bool)
        _, released, _ = execute(legacy, legacy.allowed & h1gate, legacy.raw_side, "v9_1_original")
        prior_length = 60 if minutes == 15 else 0
        prior_released = prior_trades.loc[prior_trades.timeframe.eq(tf) & prior_trades.scope.eq("actual") & prior_trades.length.eq(prior_length)]
        compare_prior(released, prior_released)
        for arm in ARMS:
            raw = legacy.raw_side if arm in ("v9", "v9_1") else signals[arm].to_numpy(int)
            allowed = legacy.allowed & common if arm in ("v9", "v9_1") else (raw != 0) & common & guards & window
            if arm == "v9_1": allowed &= h1gate
            p, trades, fills = execute(shared, allowed, raw, arm)
            controls = old.controls(p, trades, cfg)
            controls["arm"] = arm
            if len(trades):
                # Verify a deterministic realized path against independent fixed-entry evaluation.
                row = trades.iloc[len(trades) // 2]
                check = old.engine.replay_fixed_entry(p.context, row, arm="v8", enable_be=False, prepared=p)
                for field in ("net_r", "net_return", "exit_price", "exit_i"):
                    np.testing.assert_allclose(check[field], row[field], equal_nan=True, atol=1e-9)
                assert bool(check["censored"]) == bool(row.censored)
            for name, table in (("trades", trades), ("fills", fills), ("controls", controls)):
                parts[name].append(table)
            idx = np.flatnonzero(allowed)
            parts["signals"].append(pd.DataFrame({"symbol":symbol,"timeframe":tf,"arm":arm,
                "signal_i":idx,"signal_bar_open":shared.frame.index[idx],"side":raw[idx]}))
            coverage.append({"symbol":symbol,"timeframe":tf,"arm":arm,"bars":len(bars),
                "window_bars":int(window.sum()),"common_bars":int((common & window).sum()),
                "candidates":len(idx),"entries":len(trades),"first":str(bars.index.min()),"last":str(bars.index.max()),
                "partial_buckets":old.source.aggregate(base.loc[base.index>=bars.index.min()],minutes)[1],
                "gaps":int(shared.gap.sum()),"baseline_parity":True,"released_parity":True,
                "legacy_v9_entries":len(original),"common_v9_entries":len(trades) if arm=="v9" else None,
                "h1_unknown_window":int((~np.isfinite(h1.sma_60.to_numpy()) & window).sum()) if h1 is not None else 0})
    assert old.source.digest(path) == item["sha256"]
    folder.mkdir(parents=True, exist_ok=False)
    for name, tables in parts.items():
        table = pd.concat(tables, ignore_index=True)
        if name == "controls": assert not table.duplicated(["arm","event_key"]).any()
        table.to_csv(folder/f"{name}.csv.gz", index=False, compression={"method":"gzip","mtime":0})
    receipt = {"identity_hash":identity_hash,"symbol":symbol,"coverage":coverage,
        "files":{p.name:old.source.digest(p) for p in folder.iterdir()},"seconds":time.monotonic()-began,
        "prior_receipt_sha256":old.source.digest(prior_folder/"completion.json")}
    (folder/"completion.json").write_text(json.dumps(receipt,indent=2)+"\n")
    return receipt


def run(output, workers=3, symbols=None):
    cfg = config()
    declared = (*old._local_transitive_python((Path(__file__),)), TEST,
                Path("tests/evaluation/test_trend_baseline_signals.py"),EXP/"config.json",EXP/"PROJECT_PLAN.md")
    if not old.engine._committed(tuple(declared)):
        raise ValueError("Commit all unchanged builder dependencies/config/tests/plan before market replay")
    prior = json.loads((PRIOR/"identity.json").read_text())
    old_complete = json.loads((PRIOR/"completion.json").read_text())
    assert old_complete["identity_hash"] == prior["identity_hash"]
    expected = sorted(("1000PEPE" if name=="PEPE" else name)+"USDT" for name in old.NAMES)
    assert sorted(prior["inputs"]) == expected and sorted(old_complete["symbols"]) == expected
    assert old.source.digest(old.source.EXCHANGE_INFO) == prior["metadata_sha256"]
    available, metadata = old.source.series_files(), old.source.symbol_meta()
    wanted = expected if symbols is None else sorted(symbols)
    assert len(set(wanted))==len(wanted) and set(wanted)<=set(expected)
    inputs = {}
    for symbol in wanted:
        item = prior["inputs"][symbol]
        assert Path(item["path"]).resolve()==available[symbol].resolve()
        assert metadata[symbol]==item["meta"] and old.source.digest(available[symbol])==item["sha256"]
        inputs[symbol] = item
    identity = {"config":cfg,"inputs":inputs,"expected_symbols":expected,"full_pool":wanted==expected,
        "declared":{str(p):old.source.digest(p) for p in declared},
        "source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        "prior_identity_sha256":old.source.digest(PRIOR/"identity.json"),"metadata_sha256":prior["metadata_sha256"]}
    identity["identity_hash"] = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    ip=output/"identity.json"
    if ip.exists(): assert json.loads(ip.read_text())==identity,"Use a new output after any identity change"
    else: ip.write_text(json.dumps(identity,indent=2)+"\n")
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(one,(s,item,identity["identity_hash"],str(output))) for s,item in inputs.items()]
        for future in as_completed(futures):
            row=future.result();results.append(row)
            print(json.dumps({"done":len(results),"total":len(inputs),"symbol":row["symbol"],"seconds":row["seconds"]}),flush=True)
    assert sorted(r["symbol"] for r in results)==wanted
    (output/"completion.json").write_text(json.dumps({"identity_hash":identity["identity_hash"],"symbols":wanted,
        "full_pool":wanted==expected,"stream_arms":sum(len(r["coverage"]) for r in results)},indent=2)+"\n")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=EXP/"run_v1")
    parser.add_argument("--workers",type=int,default=3)
    parser.add_argument("--symbols",nargs="+")
    args=parser.parse_args();run(args.output,args.workers,args.symbols)
