"""SPIKE V11.2 position management on V9 longs: add on the first break, or leave if no break.

Source: owner 2026-09-19 「都跑跑」 after the check that V9 longs whose box later
saw a break did well from the V9 entry (+0.49R 15m / +0.53R 1h, hindsight grouping)
while entering fresh at the break lost. These two rules use that without hindsight.
Frozen plan/config in `experiments/active/exp-spike-v112-manage-20260919-v1/`.

Base: every final V9 long, serial per stream, the published fixed-entry exit
(next open, 5-bar low/2ATR stop, 4ATR trail after 2R, raw V9 short exits at the
next open, 0.2% round trip). "Break" = a higher-timeframe break first visible on
this chart bar (15m<-1h, 1h<-4h) or the chart's own V10.4 line break.

  * add:  the first break on a bar k with signal_i <= k < exit_i adds one more unit
          of the same notional at open[k+1]; it shares the original stop and exits
          with the original trade. P&L of the add unit is reported in the original
          trade's R (same notional) and in bp.
  * exit_N: if no break on bars signal_i..signal_i+N and the trade is still open
          after that close, exit at open[signal_i+N+1]. Serial re-run, so leaving
          earlier frees the stream for later V9 longs. N=6 primary; 3/12/24/48 shown.
  * exit_6_add: both together (descriptive).

Every decision uses bars up to its close; fills are the next open. Holdout: every
5m row must open before 2026-05-01. No fetch, tuning of the base, or promotion.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v10_4 import V104Params, joint_events

EXP = Path("experiments/active/exp-spike-v112-manage-20260919-v1")
CONFIG = EXP / "config.json"
EXIT_NS = (3, 6, 12, 24, 48)
PRIMARY_N = 6
COST = 0.002
DEPENDENCIES = (Path(__file__), Path("yoyo/evaluation/spike_v10_4.py"), Path("yoyo/evaluation/spike_v11_study.py"),
                Path("yoyo/evaluation/spike_v10_4_study.py"), Path("yoyo/evaluation/spike_v10_4_increment.py"),
                Path("tests/evaluation/test_spike_v112_manage.py"), CONFIG, EXP / "PROJECT_PLAN.md")


def cut_if_no_break(result: dict, signal_i: int, breaks: np.ndarray, open_: np.ndarray, gap: np.ndarray,
                    index: pd.DatetimeIndex, n_wait: int) -> dict:
    """Leave at open[s+N+1] when bars s..s+N saw no break and the trade was still open after s+N."""
    decide, fill = signal_i + n_wait, signal_i + n_wait + 1
    if fill >= len(open_) or gap[fill] or breaks[signal_i:decide + 1].any():
        return result
    exit_i = result.get("exit_i")
    if exit_i is not None and not pd.isna(exit_i) and int(exit_i) <= fill and result["exit_reason"] != "boundary_mark":
        return result
    entry, frac = float(result["entry_price"]), float(result["initial_risk_frac"])
    price = float(open_[fill])
    gross = price / entry - 1
    net = gross - COST
    return {**result, "exit_i": fill, "exit_time": index[fill], "exit_price": price, "exit_reason": "no_break_exit",
            "gross_return": gross, "net_return": net, "gross_r": gross / frac, "net_r": net / frac, "censored": False}


def add_unit(result: dict, signal_i: int, breaks: np.ndarray, open_: np.ndarray) -> dict | None:
    """One extra unit at the first break while the trade is open; it exits with the trade."""
    if result["censored"] or result.get("exit_i") is None:
        return None
    exit_i = int(result["exit_i"])
    hits = np.flatnonzero(breaks[signal_i:exit_i]) + signal_i
    if not len(hits):
        return None
    k = int(hits[0])
    add_price = float(open_[k + 1])
    gross = float(result["exit_price"]) / add_price - 1
    net = gross - COST
    frac = float(result["initial_risk_frac"])
    return {"add_break_i": k, "add_bars_after_v9": k - signal_i, "add_price": add_price,
            "add_gross_return": gross, "add_net_return": net, "add_net_r": net / frac,
            "add_price_in_r": (add_price - float(result["entry_price"])) / float(result["initial_risk"])}


def serial(prepared, candidates: np.ndarray, *, breaks: np.ndarray, n_wait: int | None, add: bool,
           key: str, arm: str) -> list[dict]:
    open_, gap, index = prepared.open, prepared.gap, prepared.frame.index
    rows, flat_from = [], -1
    for i in candidates.tolist():
        if i < flat_from:
            continue
        status, result = inc.attempt(prepared, i)
        if result is None:
            continue
        if n_wait is not None:
            result = cut_if_no_break(result, i, breaks, open_, gap, index, n_wait)
        status = "closed" if not result["censored"] else status
        row = {**{k: result.get(k) for k in study.TRADE_KEEP}, "status": status, "arm": arm,
               "trade_key": f"{key}:{arm}:{i}"}
        if add:
            extra = add_unit(result, i, breaks, open_)
            row.update(extra or {"add_break_i": None})
        rows.append(row)
        flat_from = len(prepared.frame) + 1 if status == "censored_boundary" else int(result["exit_i"])
    return rows


def run_pair(symbol: str, base: pd.DataFrame, meta: dict, pair: tuple) -> dict:
    timeframe, minutes, htf_name, htf_minutes = pair
    tick, asset = float(meta["tick"]), meta["asset"]
    key = f"binance_um:{symbol}:{timeframe}"
    bars = v11.bars_for(base, minutes)
    if not len(bars) or not study.in_window(bars.index, minutes).any():
        return {"trades": pd.DataFrame()}
    params = V104Params()
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    H, _ = v11.htf_inputs(frame.index, minutes, v11.bars_for(base, htf_minutes), htf_minutes, tick, params)
    chart = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr,
                         can_run=facts["can_run"], confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                         parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                         momentum=facts["momentum"], current_gate=facts["current_gate"],
                         ref_long_exit=facts["ref_long_exit"], tick=tick, params=params)
    breaks = H["known"] | chart.break_event
    window = study.in_window(frame.index, minutes)
    candidates = np.flatnonzero(facts["v9_long"] & window)
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": timeframe,
                "timeframe_min": minutes}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    rows = serial(prepared, candidates, breaks=breaks, n_wait=None, add=True, key=key, arm="v9_add")
    # v9_add's own exits are the plain V9 exits, so it doubles as the reproduction arm.
    for n_wait in EXIT_NS:
        rows += serial(prepared, candidates, breaks=breaks, n_wait=n_wait, add=False, key=key, arm=f"v9_exit{n_wait}")
    rows += serial(prepared, candidates, breaks=breaks, n_wait=PRIMARY_N, add=True, key=key,
                   arm=f"v9_exit{PRIMARY_N}_add")
    trades = pd.DataFrame(rows)
    if len(trades):
        trades["symbol"], trades["asset"], trades["timeframe"] = symbol, asset, timeframe
        trades["signal_bar_open"] = frame.index[trades.signal_i.to_numpy(int)]
        trades["censored"] = trades.status.ne("closed")
    return {"trades": trades}


def run_symbol(args) -> dict:
    symbol, path, meta, output, identity_hash = args
    final = Path(output) / "streams" / symbol
    if (final / "completion.json").is_file():
        return json.loads((final / "completion.json").read_text())
    started = time.perf_counter()
    staging = Path(output) / "streams" / f".{symbol}.staging"
    staging.mkdir(parents=True, exist_ok=True)
    for old in staging.iterdir():
        old.unlink()
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = inc.guarded_5m(Path(path), earliest)
    frames, failures = [], []
    for pair in v11.PAIRS:
        try:
            out = run_pair(symbol, base, meta, pair)
        except ValueError as exc:
            failures.append({"timeframe": pair[0], "error": str(exc)})
            continue
        if len(out["trades"]):
            frames.append(out["trades"])
    if frames:
        pd.concat(frames, ignore_index=True).to_csv(staging / "trades.csv.gz", index=False,
                                                    compression={"method": "gzip", "mtime": 0})
    receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash, "failures": failures,
               "files": {p.name: study.digest(p) for p in staging.iterdir() if p.is_file()},
               "wall_seconds": round(time.perf_counter() - started, 2)}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 8, symbols: list[str] | None = None, allow_uncommitted: bool = False) -> None:
    if not allow_uncommitted and not _committed(DEPENDENCIES):
        raise ValueError("commit runner, tests, plan and config before any market replay")
    config = json.loads(CONFIG.read_text())
    files = study.series_files()
    if len(files) != config["expected_symbols"]:
        raise ValueError(f"source coverage changed: {len(files)}")
    for path in files.values():
        if pd.Timestamp(int(pd.read_csv(path, usecols=["ts"]).ts.max()), unit="ms", tz="UTC") >= inc.DATA_END:
            raise ValueError(f"boundary check failed before any computation: {path.name}")
    meta = study.symbol_meta()
    keys = symbols or sorted(files)
    identity = {str(p): study.digest(p) for p in DEPENDENCIES}
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    (output / "identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    (output / "evaluation_started.json").write_text(json.dumps({
        "started_at_unix": time.time(), "holdout_consumption": 0,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "run_identity": identity_hash, "symbols": len(keys)}, indent=2) + "\n")
    start = time.perf_counter()
    receipts = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        tasks = [pool.submit(run_symbol, (s, str(files[s]), meta.get(s, {"asset": "", "tick": math.nan}),
                                          str(output), identity_hash)) for s in keys]
        for number, future in enumerate(as_completed(tasks), 1):
            receipts.append(future.result())
            if number == 1 or number % 100 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys),
                                  "elapsed_seconds": round(time.perf_counter() - start, 1)}), flush=True)
    (output / "manifest.json").write_text(json.dumps({
        "complete": symbols is None, "symbols": len(keys), "run_identity": identity_hash, "holdout_consumption": 0,
        "failures": {r["symbol"]: r["failures"] for r in receipts if r["failures"]},
        "wall_seconds": round(time.perf_counter() - start, 1)}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--allow-uncommitted", action="store_true", help="smoke runs only; not reportable")
    args = parser.parse_args()
    run(args.output, workers=args.workers, symbols=args.symbols, allow_uncommitted=args.allow_uncommitted)
