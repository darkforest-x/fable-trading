"""Frozen six-line admission comparison for five-minute two-sided SPIKE V9.

Source: owner 2026-09-20 request and the committed experiment PROJECT_PLAN.
MA features use only complete prior fifteen-minute close windows. All trade
paths and costs reuse the released serial engine; no production mutations.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v9_htf_sma_study as prior
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python

EXP = Path("experiments/active/exp-spike-v9-5m-15m-ma-20260920-v1")
CONFIG = EXP / "config.json"
source, engine = prior.source, prior.engine


def config():
    return json.loads(CONFIG.read_text())


def confirmed_ma(base5m, chart_index, lengths=(20, 60, 120), ema_warmup_multiple=10):
    """Use complete OHLCV triples and N/10N contiguous prior15m close windows.

    SMA needs N valid candles; EMA first-observation seeds per valid segment
    with alpha2/(N+1), and becomes eligible after10N bars by default. Data gaps
    restart the recurrence. Higher close_time must be <= chart BAR OPEN.
    """
    if ema_warmup_multiple < 1:
        raise ValueError("EMA warmup multiple must be positive")
    result = confirmed_sma(base5m, chart_index, 15, lengths)
    result["consecutive"] = 0
    for length in lengths:
        result[f"ema_{length}"] = np.nan
    if base5m.empty or len(chart_index) == 0:
        return result
    delta = pd.Timedelta(minutes=15)
    higher_clock = pd.date_range(base5m.index.min().floor("15min") + delta,
                                 base5m.index.max().floor("15min") + delta, freq="15min")
    # SMA1 is exactly the completed bucket close, including NaNs for invalid
    # or partial buckets. Its shared helper validates clocks and every OHLCV row.
    close = confirmed_sma(base5m, higher_clock, 15, (1,)).sma_1
    segments = close.isna().cumsum()
    consecutive = close.notna().astype(int).groupby(segments).cumsum()
    pos = higher_clock.searchsorted(chart_index, side="right") - 1
    safe = np.maximum(pos, 0)
    age = chart_index.asi8 - higher_clock.asi8[safe]
    available = (pos >= 0) & (age >= 0) & (age < delta.value)
    result["consecutive"] = np.where(available, consecutive.to_numpy()[safe], 0)
    for length in lengths:
        ma = close.groupby(segments).transform(
            lambda x: x.ewm(alpha=2. / (length + 1), adjust=False, ignore_na=True).mean())
        ma = ma.where(close.notna() & consecutive.ge(ema_warmup_multiple * length)).to_numpy()
        result[f"ema_{length}"] = np.where(available, ma[safe], np.nan)
    return result


def line_column(ma):
    family = "sma" if ma.startswith("sma") else "ema"
    return f"{family}_{int(ma[len(family):])}"


def execute(prepared, mask, scope, ma):
    """Keep unchanged next-open execution, including rejected raw reverse exits."""
    length = 0 if ma == "none" else int(ma[3:])
    trades, fills = prior.execute(prepared, mask, scope, length)
    for table in (trades, fills):
        table["ma"], table["arm"] = ma, ma
    return trades, fills


def one(args):
    symbol, item, identity_hash, output = args
    cfg = config()
    folder = Path(output) / "streams" / symbol
    receipt_path = folder / "completion.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        assert receipt["identity_hash"] == identity_hash
        for name, sha in receipt["files"].items():
            assert source.digest(folder / name) == sha
        return receipt
    began = time.monotonic()
    path, meta = Path(item["path"]), item["meta"]
    assert source.digest(path) == item["sha256"]
    base = prior.inc.guarded_5m(path, source.START - pd.Timedelta(days=source.WARMUP_BARS))
    chart = prior.v11.bars_for(base, 5)
    facts = source.v9_facts(chart, 5, meta["asset"], float(meta["tick"]))
    prepared = prior.build_prepared(facts, symbol, meta, "5m", 5)
    feature = confirmed_ma(base, prepared.frame.index, tuple(cfg["lengths"]), cfg["ema_warmup_multiple"])
    columns = [line_column(ma) for ma in cfg["arms"] if ma != "none"]
    common = feature[columns].notna().all(axis=1).to_numpy()
    indices = np.flatnonzero(prepared.allowed)
    evidence = feature.iloc[indices].copy()
    evidence["symbol"], evidence["timeframe"], evidence["htf"] = symbol, "5m", "15m"
    evidence["signal_i"], evidence["signal_bar_open"] = indices, prepared.frame.index[indices]
    evidence["signal_close"], evidence["side"] = prepared.close[indices], prepared.raw_side[indices]
    evidence["common_ready"] = common[indices]
    for ma in cfg["arms"]:
        evidence[f"pass_{ma}"] = True if ma == "none" else side_gate(
            prepared.close[indices], prepared.raw_side[indices], evidence[line_column(ma)])
    all_trades, all_fills, cached, reused = [], [], {}, 0
    for scope in ("actual", "common"):
        eligible = prepared.allowed & (common if scope == "common" else True)
        for ma in cfg["arms"]:
            mask = eligible if ma == "none" else eligible & side_gate(
                prepared.close, prepared.raw_side, feature[line_column(ma)])
            key = hashlib.sha256(mask.tobytes()).hexdigest()
            if key in cached:
                trades, fills = [table.copy() for table in cached[key]]
                for table in (trades, fills):
                    table["scope"], table["ma"], table["arm"] = scope, ma, ma
                    table["length"] = 0 if ma == "none" else int(ma[3:])
                reused += 1
            else:
                trades, fills = execute(prepared, mask, scope, ma)
                cached[key] = (trades, fills)
            all_trades.append(trades)
            all_fills.append(fills)
    trades, fills = pd.concat(all_trades, ignore_index=True), pd.concat(all_fills, ignore_index=True)
    admitted = prior.v9_admissions(prepared.context)
    np.testing.assert_array_equal(admitted.v9.to_numpy(bool) & source.in_window(prepared.frame.index, 5), prepared.allowed)
    legacy, _, _, _ = prior.replay_v9(prepared.context)
    actual = trades.loc[trades.scope.eq("actual") & trades.ma.eq("none")]
    fields = [*engine.KEY, "censored"]
    pd.testing.assert_frame_equal(actual[fields].reset_index(drop=True), legacy[fields].reset_index(drop=True),
                                  check_dtype=False, rtol=1e-9, atol=1e-9)
    controls = prior.controls(prepared, trades, cfg)
    assert not controls.event_key.duplicated().any()
    coverage = dict(symbol=symbol, timeframe="5m", htf="15m", chart_bars=len(chart),
                    first=str(chart.index.min()), last=str(chart.index.max()),
                    base_bars=len(base), base_first=str(base.index.min()),
                    v9_candidates=len(indices), common_candidates=int(common[indices].sum()),
                    chart_gaps=int(prepared.gap.sum()), chart_partial_buckets=0,
                    real_adapter_trade_parity=True, baseline_trades=len(actual), identical_mask_reuses=reused)
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, table in {"trades": trades, "fills": fills, "controls": controls,
                        "evidence": evidence.reset_index(drop=True)}.items():
        p = folder / f"{name}.csv.gz"
        table.to_csv(p, index=False, compression={"method": "gzip", "mtime": 0})
        files[p.name] = source.digest(p)
    assert source.digest(path) == item["sha256"]
    receipt = dict(symbol=symbol, identity_hash=identity_hash, source_sha256=item["sha256"],
                   files=files, coverage=[coverage], seconds=round(time.monotonic() - began, 2))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def run(output, workers=3, symbols=None):
    cfg = config()
    assert (cfg["start"], cfg["split"], cfg["end"]) == tuple(x.isoformat().replace("+00:00", "Z") for x in (source.START, source.SPLIT, source.DATA_END))
    declared = (*_local_transitive_python((Path(__file__),)), CONFIG, EXP / "PROJECT_PLAN.md",
                Path("tests/evaluation/test_spike_v9_5m_15m_ma.py"))
    if not engine._committed(tuple(declared)):
        raise ValueError("Commit builder, dependency closure, config, plan and tests before replay")
    mapping = {name: ("1000PEPE" if name == "PEPE" else name) + "USDT" for name in prior.NAMES}
    wanted = sorted(mapping.values()) if symbols is None else sorted(symbols)
    assert len(wanted) == len(set(wanted)) and set(wanted) <= set(mapping.values()) and len(mapping) == 29
    available, meta = source.series_files(), source.symbol_meta()
    inputs = {s: dict(path=str(available[s]), sha256=source.digest(available[s]), meta=meta[s]) for s in wanted}
    old_hashes = set(json.loads(prior.PRIOR.read_text())["input_sha"].values())
    assert {x["sha256"] for x in inputs.values()} <= old_hashes
    identity = dict(config=cfg, mapping=mapping, inputs=inputs,
                    source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    declared={str(p): source.digest(p) for p in declared},
                    metadata_sha256=source.digest(source.EXCHANGE_INFO))
    identity["identity_hash"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ip = output / "identity.json"
    if ip.exists():
        assert json.loads(ip.read_text()) == identity, "Changed code/config requires a new output directory"
    else:
        ip.write_text(json.dumps(identity, indent=2) + "\n")
    args = [(s, x, identity["identity_hash"], str(output)) for s, x in inputs.items()]
    receipts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, arg) for arg in args]):
            receipt = future.result()
            receipts.append(receipt)
            print(json.dumps(dict(completed=len(receipts), total=len(args), symbol=receipt["symbol"], seconds=receipt["seconds"])), flush=True)
    assert sorted(r["symbol"] for r in receipts) == wanted
    (output / "completion.json").write_text(json.dumps(dict(identity_hash=identity["identity_hash"], symbols=wanted, streams=len(wanted)), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EXP / "run_v1")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()
    run(args.output, args.workers, args.symbols)
