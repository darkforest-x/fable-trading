"""Frozen29-symbol V9 SMA direction ablation with complete serial replay.

Source: owner 2026-09-20 SMA120/SMA50 request and experiment PROJECT_PLAN.
Only entry eligibility changes; the published two-sided serial engine retains
raw opposite exits, original next-open fills/stops/trailing and20bp cost.
No source fetch, production mutation, account-return claim or HTML generation.
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

from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation import spike_v9_full_replay as original
from yoyo.evaluation.spike_v9 import replay_v9, v9_admissions
from yoyo.evaluation.spike_v7_fast import v7_diagnostics
from yoyo.evaluation.spike_v112_selected_counts import NAMES
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate

EXP = Path("experiments/active/exp-spike-v9-htf-sma-20260920-v1")
CONFIG = EXP / "config.json"
PRIOR = Path("experiments/active/exp-spike-v112-entry-extension-20260920-v1/run/identity.json")
VOL_BINS = np.array([.005, .01, .02, .05, .1])


def config():
    return json.loads(CONFIG.read_text())


def build_prepared(facts, symbol, meta, tf, minutes):
    """Reuse published V9 facts and serial interface, retaining both raw sides."""
    f = facts["frame"]
    identity = {"venue": "binance_um", "symbol": symbol, "asset": meta["asset"],
                "timeframe": tf, "timeframe_min": minutes}
    p = source.prepared_arm(f, facts["gap"], facts["side"], f"binance_um:{symbol}:{tf}", identity, minutes, float(meta["tick"]))
    # The independent legacy adapter needs these same unfiltered raw inputs.
    cache = dict(p.context.cache)
    cache["signals"] = pd.DataFrame({"long_signal": facts["side"] == 1,
                                      "short_signal": facts["side"] == -1}, index=f.index)
    cache["bb"] = v7_diagnostics(f, data_gap=pd.Series(facts["gap"], index=f.index))
    cache["data_gap"] = pd.Series(facts["gap"], index=f.index)
    ledger = pd.DataFrame({"signal_bar_open": f.index, "signal_i": np.arange(len(f))})
    context = replace(p.context, cache=cache, signals_ledger=ledger)
    return replace(p, context=context, allowed=facts["v9"] & source.in_window(f.index, minutes))


def execute(prepared, allowed, scope, length):
    """Run a separate complete serial path, never delete trades from a ledger."""
    p = replace(prepared, allowed=np.asarray(allowed, bool))
    trades, fills, _ = engine.replay_serial(p.context, arm="v8", enable_be=False, prepared=p)
    for table in (trades, fills):
        table["scope"], table["length"] = scope, length
        table["timeframe"] = p.context.identity["timeframe"]
        table["arm"] = "v9" if length == 0 else f"sma{length}"
    if len(trades):
        trades["event_key"] = [f"{p.context.key}|{pd.Timestamp(t).isoformat()}|{s}" for t, s in zip(trades.signal_bar_open, trades.side)]
        closed = trades.loc[~trades.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-9)
    else:
        trades["event_key"] = pd.Series(dtype=str)
    return trades, fills


def controls(prepared, targets, cfg):
    """Fixed random entry per unique event, same side/month/fold/volatility bin.

    Eligibility reads current ready, OHLC/ATR and scheduled UTC weekday only.
    Prices after a deterministic draw are outcome data only. Failed/censored
    draws are retained, never replaced. Sampling is shared across all arms.
    """
    p, f = prepared, prepared.frame
    start, split, end = map(pd.Timestamp, (cfg["start"], cfg["split"], cfg["end"]))
    clock = f.index + pd.Timedelta(minutes=p.context.minutes)
    months = np.asarray(clock.strftime("%Y-%m"))
    folds = np.where(clock < split, "earlier", "later")
    bins = np.searchsorted(VOL_BINS, p.atr / p.close, side="left")
    valid = (f.ready.fillna(False).to_numpy(bool) & np.isfinite(f[["open", "high", "low", "close", "atr"]]).all(axis=1)
             & (p.atr > 0) & (p.close > 0) & (clock >= start) & (clock < end) & (clock.dayofweek != 6))
    pools, paths, rows = {}, {}, []
    for t in targets.drop_duplicates("event_key").itertuples(index=False):
        i, side = int(t.signal_i), int(t.side)
        key = (months[i], folds[i], int(bins[i]))
        if key not in pools:
            pools[key] = np.flatnonzero(valid & (months == key[0]) & (folds == key[1]) & (bins == key[2]))
        options = pools[key][pools[key] != i]
        row = {"event_key": t.event_key, "matched": False, "reason": "no_match",
               "control_signal_i": np.nan, "control_signal_time": pd.NaT,
               "control_entry_time": pd.NaT, "control_exit_time": pd.NaT,
               "control_censored": True, "control_net_r": np.nan, "control_net_return": np.nan}
        if len(options):
            token = f"{cfg['seed']}|{t.event_key}"
            j = int(options[int(hashlib.sha256(token.encode()).hexdigest(), 16) % len(options)])
            row.update(control_signal_i=j, control_signal_time=f.index[j])
            if (j, side) not in paths:
                initial = source._initial_position_fast(f.index, p.open, p.high, p.low, p.close, p.atr, p.gap, j, side, p.spec)
                if initial is not None and start <= initial["entry_time"] < end:
                    initial["initial_risk_frac"] = initial["initial_risk"] / initial["entry_price"]
                    paths[j, side] = engine.replay_fixed_entry(p.context, pd.Series(initial), arm="v8", enable_be=False, prepared=p)
                else:
                    paths[j, side] = None
            result = paths[j, side]
            if result is None:
                row["reason"] = "invalid_initial"
            else:
                row.update(control_entry_time=result["entry_time"], control_exit_time=result["exit_time"],
                           control_censored=result["censored"], control_net_r=result["net_r"],
                           control_net_return=result["net_return"], matched=not result["censored"],
                           reason="censored" if result["censored"] else "matched")
        rows.append(row)
    cols = ["event_key", "matched", "reason", "control_signal_i", "control_signal_time", "control_entry_time", "control_exit_time", "control_censored", "control_net_r", "control_net_return"]
    return pd.DataFrame(rows, columns=cols)


def one(args):
    symbol, path, meta, sha, identity_hash, output = args
    cfg = config(); folder = Path(output) / "streams" / symbol
    receipt_path = folder / "completion.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        assert receipt["identity_hash"] == identity_hash
        for name, digest in receipt["files"].items():
            assert source.digest(folder / name) == digest
        return receipt
    assert source.digest(Path(path)) == sha
    began = time.monotonic()
    base = inc.guarded_5m(Path(path), source.START - pd.Timedelta(days=source.WARMUP_BARS))
    all_trades, all_fills, all_controls, evidence, coverage = [], [], [], [], []
    for tf, minutes, htf, hminutes in cfg["pairs"]:
        bars = v11.bars_for(base, minutes)
        if len(bars) == 0:
            continue
        facts = source.v9_facts(bars, minutes, meta["asset"], float(meta["tick"]))
        p = build_prepared(facts, symbol, meta, tf, minutes)
        feature = confirmed_sma(base, p.frame.index, hminutes, tuple(cfg["lengths"]))
        common = np.isfinite(feature[f"sma_{max(cfg['lengths'])}"])
        indices = np.flatnonzero(p.allowed)
        d = feature.iloc[indices].copy()
        d["symbol"], d["timeframe"], d["htf"] = symbol, tf, htf
        d["signal_i"], d["signal_bar_open"] = indices, p.frame.index[indices]
        d["side"], d["signal_close"] = p.raw_side[indices], p.close[indices]
        for length in cfg["lengths"]:
            d[f"pass_{length}"] = side_gate(p.close[indices], p.raw_side[indices], d[f"sma_{length}"])
        d["common_ready"] = np.asarray(common)[indices]
        evidence.append(d.reset_index(drop=True))
        streams = []
        for scope in ("actual", "common"):
            eligibility = p.allowed & (np.asarray(common) if scope == "common" else True)
            for length in [0, *cfg["lengths"]]:
                mask = eligibility if length == 0 else eligibility & side_gate(p.close, p.raw_side, feature[f"sma_{length}"])
                trades, fills = execute(p, mask, scope, length)
                streams.append(trades); all_fills.append(fills)
                if scope == "actual" and length == 0:
                    # Gate-off facts must agree with the released adapter on real bars.
                    check = v9_admissions(p.context)
                    np.testing.assert_array_equal(check.v9.to_numpy(bool) & source.in_window(p.frame.index, minutes), p.allowed)
                    if symbol in ("BTCUSDT", "ETHUSDT"):
                        legacy, _, _, _ = replay_v9(p.context)
                        fields = [*engine.KEY, "censored"]
                        pd.testing.assert_frame_equal(trades[fields].reset_index(drop=True), legacy[fields].reset_index(drop=True), check_dtype=False, rtol=1e-9, atol=1e-9)
        t = pd.concat(streams, ignore_index=True)
        all_trades.append(t)
        all_controls.append(controls(p, t, cfg))
        partial_count = source.aggregate(base.loc[base.index >= bars.index.min()], minutes)[1]
        coverage.append({"symbol": symbol, "timeframe": tf, "htf": htf, "chart_bars": len(bars),
                         "first": str(bars.index.min()), "last": str(bars.index.max()),
                         "v9_candidates": len(indices), "common_candidates": int(d.common_ready.sum()),
                         "chart_partial_buckets": partial_count, "chart_gaps": int(p.gap.sum()),
                         "real_adapter_trade_parity": symbol in ("BTCUSDT", "ETHUSDT")})
    folder.mkdir(parents=True, exist_ok=True)
    tables = {"trades": all_trades, "fills": all_fills, "controls": all_controls, "evidence": evidence}
    files = {}
    for name, parts in tables.items():
        table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if name == "controls" and len(table):
            assert not table.event_key.duplicated().any()
        file = folder / f"{name}.csv.gz"
        table.to_csv(file, index=False, compression={"method": "gzip", "mtime": 0})
        files[file.name] = source.digest(file)
    assert source.digest(Path(path)) == sha
    receipt = {"symbol": symbol, "identity_hash": identity_hash, "source_sha256": sha,
               "files": files, "coverage": coverage, "seconds": round(time.monotonic() - began, 2)}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def run(output, workers=3, symbols=None):
    cfg = config()
    declared = (*_local_transitive_python((Path(__file__),)), CONFIG, EXP / "PROJECT_PLAN.md",
                Path("tests/evaluation/test_spike_v9_htf_sma.py"))
    if not engine._committed(tuple(declared)):
        raise ValueError("Commit builder, dependency closure, config, plan and tests before replay")
    mapping = {name: ("1000PEPE" if name == "PEPE" else name) + "USDT" for name in NAMES}
    available, meta = source.series_files(), source.symbol_meta()
    prior = json.loads(PRIOR.read_text())
    wanted = sorted(mapping.values()) if symbols is None else symbols
    assert set(wanted) <= set(mapping.values()) and len(mapping) == 29
    inputs = {s: {"path": str(available[s]), "sha256": source.digest(available[s]), "meta": meta[s]} for s in wanted}
    prior_hashes = set(prior["input_sha"].values())
    assert {item["sha256"] for item in inputs.values()} <= prior_hashes, "Selected source differs from frozen prior29 pool"
    identity = {"config": cfg, "mapping": mapping, "inputs": inputs,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "declared": {str(p): source.digest(p) for p in declared},
                "metadata_sha256": source.digest(source.EXCHANGE_INFO)}
    identity["identity_hash"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    ip = output / "identity.json"
    if ip.exists():
        assert json.loads(ip.read_text()) == identity, "Preserve prior run and choose new output after code changes"
    else:
        ip.write_text(json.dumps(identity, indent=2) + "\n")
    args = [(s, x["path"], x["meta"], x["sha256"], identity["identity_hash"], str(output)) for s, x in inputs.items()]
    receipts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, a) for a in args]):
            r = future.result(); receipts.append(r)
            print(json.dumps({"completed": len(receipts), "total": len(args), "symbol": r["symbol"], "seconds": r["seconds"]}), flush=True)
    assert sorted(r["symbol"] for r in receipts) == sorted(wanted)
    (output / "completion.json").write_text(json.dumps({"identity_hash": identity["identity_hash"], "symbols": sorted(wanted), "streams": sum(len(r["coverage"]) for r in receipts)}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EXP / "run_v1")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()
    run(args.output, args.workers, args.symbols)
