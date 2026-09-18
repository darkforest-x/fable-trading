"""SPIKE V11 backtest: chart SPIKE paired with chart and/or higher-timeframe line breaks.

Source: owner 2026-09-18 「回测一下」 on `pine/spike_burst_v11.pine` (15m reads 1h
breaks, 1h reads 4h breaks). Frozen plan/config in
`experiments/active/exp-spike-v11-mtf-joint-20260918-v1/`.

Pairs: (15m chart, 1h higher), (1h chart, 4h higher). Data, universe, window,
V9 engine, exit, cost, serial rule and controls are exactly those of the V10.4
six-timeframe run; both chart and higher bars are aggregated from the same 5m
archive by UTC-epoch buckets.

Arms (same exit, independent serial state):
  * v9_long       every final V9 long (reference);
  * v10_4         V10.4 joint, chart lines only (must reproduce the original run);
  * v11_htf_only  V11 "仅上级周期": chart SPIKE + higher-timeframe break only;
  * v11_both      V11 default "本周期+上级周期": either source, one SPIKE used once.

Higher-timeframe timing (Pine `request.security(..., f[1], lookahead_on)`): a
break on the higher bar that opens at T becomes visible only on the first chart
bar opening at T + higher period; if that chart bar is missing, the break is
never used (the Pine's freshness check). Columns used: higher OHLC and ATR up to
that close; chart V9 facts up to each chart bar. Nothing after a bar is read.
Holdout: every 5m row must open before 2026-05-01. No fetch, tuning, promotion.
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
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v6_wvf_study import _data_gap
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v10_4 import V104Params, htf_breaks, joint_events

EXP = Path("experiments/active/exp-spike-v11-mtf-joint-20260918-v1")
CONFIG = EXP / "config.json"
PAIRS = (("15m", 15, "1h", 60), ("1h", 60, "4h", 240))
ARMS = ("v9_long", "v10_4", "v11_htf_only", "v11_both")
# v10_4 keeps the original trade-key name so its trades compare 1:1 with run_v1.
KEY_NAME = {"v9_long": "v9_long", "v10_4": "joint", "v11_htf_only": "v11_htf_only", "v11_both": "v11_both"}
DEPENDENCIES = (Path(__file__), Path("yoyo/evaluation/spike_v10_4.py"), Path("yoyo/evaluation/spike_v10_4_study.py"),
                Path("yoyo/evaluation/spike_v10_4_increment.py"), Path("tests/evaluation/test_spike_v11.py"),
                Path("yoyo/evaluation/pine/spike_burst_v11.pine"), CONFIG, EXP / "PROJECT_PLAN.md")


def minutes_of(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.asi8 // 60_000_000_000).astype(float)


def bars_for(base: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """The original run's construction: 1500 bars of warm-up before the window, floored."""
    since = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * minutes)
    bars, _ = study.aggregate(base.loc[base.index >= since.floor(f"{minutes}min")], minutes)
    if len(bars) and bars.index.max() + pd.Timedelta(minutes=minutes) > inc.DATA_END:
        raise ValueError("a bar closes after DATA_END")
    return bars


def htf_inputs(chart_index: pd.DatetimeIndex, chart_minutes: int, htf: pd.DataFrame, htf_minutes: int,
               tick: float, params: V104Params) -> tuple[dict, list[dict]]:
    """Run the higher engine and place each break on the chart bar where it first becomes visible."""
    frame = features(htf)
    gap = _data_gap(frame, htf_minutes).to_numpy(bool)
    atr = frame.atr.to_numpy(float)
    can = ~gap & np.isfinite(atr) & (atr > 0)
    records = htf_breaks(frame.open, frame.high, frame.low, frame.close, atr, can_run=can, tick=tick, params=params)
    h_open = minutes_of(frame.index)
    c_open = minutes_of(chart_index)
    n = len(chart_index)
    out = {k: np.full(n, np.nan) for k in ("ax_t", "ap", "bx_t", "bp", "cx_t", "cp", "born_t", "break_t")}
    out["known"] = np.zeros(n, dtype=bool)
    placed = []
    for r in records:
        close_t = h_open[r["i"]] + htf_minutes
        j = int(np.searchsorted(c_open, close_t, side="left"))
        visible = bool(j < n and c_open[j] - close_t < chart_minutes)
        placed.append({**r, "break_open": frame.index[r["i"]], "visible": visible,
                       "known_i": j if visible else -1})
        if not visible:
            continue
        out["known"][j] = True
        out["ax_t"][j], out["ap"][j] = h_open[r["ax"]], r["ap"]
        out["bx_t"][j], out["bp"][j] = h_open[r["bx"]], r["bp"]
        out["cx_t"][j], out["cp"][j] = h_open[r["cx"]], r["cp"]
        out["born_t"][j] = h_open[r["born_i"]] + htf_minutes
        out["break_t"][j] = h_open[r["i"]]
    return out, placed


def serial(prepared, candidates: np.ndarray, key: str, arm: str) -> tuple[list[dict], list[dict]]:
    """The original serial rule with every candidate's status (as the 1h audit runner)."""
    trades, statuses = [], []
    flat_from, holder = -1, None
    for i in candidates.tolist():
        if i < flat_from:
            statuses.append({"arm": arm, "signal_i": i, "status": "skipped_in_position", "blocking_trade": holder})
            continue
        status, result = inc.attempt(prepared, i)
        statuses.append({"arm": arm, "signal_i": i, "status": status, "blocking_trade": None})
        if result is None:
            continue
        trade_key = f"{key}:{KEY_NAME[arm]}:{i}"
        trades.append({**{k: result.get(k) for k in study.TRADE_KEEP}, "status": status, "arm": arm,
                       "trade_key": trade_key})
        holder = trade_key
        flat_from = len(prepared.frame) + 1 if status == "censored_boundary" else int(result["exit_i"])
    return trades, statuses


def run_pair(symbol: str, base: pd.DataFrame, meta: dict, pair: tuple) -> dict:
    timeframe, minutes, htf_name, htf_minutes = pair
    tick, asset = float(meta["tick"]), meta["asset"]
    key = f"binance_um:{symbol}:{timeframe}"
    bars = bars_for(base, minutes)
    if not len(bars) or not study.in_window(bars.index, minutes).any():
        return {"summary": {"symbol": symbol, "timeframe": timeframe, "skipped": "no_bar_in_window"}}
    params = V104Params()
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    htf_bars = bars_for(base, htf_minutes)
    H, placed = htf_inputs(frame.index, minutes, htf_bars, htf_minutes, tick, params)
    H["bar_t"] = minutes_of(frame.index)
    H["gap"] = facts["gap"]
    inputs = dict(can_run=facts["can_run"], confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                  parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                  momentum=facts["momentum"], current_gate=facts["current_gate"],
                  ref_long_exit=facts["ref_long_exit"], tick=tick, params=params)
    ohlc = (frame.open, frame.high, frame.low, frame.close, frame.atr)
    r_v104 = joint_events(*ohlc, **inputs)
    r_htf = joint_events(*ohlc, use_chart=False, htf=H, **inputs)
    r_both = joint_events(*ohlc, htf=H, **inputs)
    window = study.in_window(frame.index, minutes)
    masks = {"v9_long": facts["v9_long"] & window, "v10_4": r_v104.joint_event & window,
             "v11_htf_only": r_htf.htf_joint_event & window,
             "v11_both": (r_both.joint_event | r_both.htf_joint_event) & window}
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": timeframe,
                "timeframe_min": minutes}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    trade_rows, status_rows = [], []
    for arm in ARMS:
        t, s = serial(prepared, np.flatnonzero(masks[arm]), key, arm)
        trade_rows += t
        status_rows += s
    trades, statuses = pd.DataFrame(trade_rows), pd.DataFrame(status_rows)
    index = frame.index
    source_both = np.where(r_both.htf_joint_event, "htf", np.where(r_both.joint_event, "chart", ""))
    htf_order = {j["i"]: j for j in r_htf.htf_joints}
    both_order = {j["i"]: j for j in r_both.htf_joints}
    chart_order = {j["i"]: j for j in r_both.joints}
    for table in (trades, statuses):
        if len(table):
            table["symbol"], table["asset"], table["timeframe"] = symbol, asset, timeframe
            table["signal_bar_open"] = index[table.signal_i.to_numpy(int)]
    controls = pd.DataFrame()
    if len(trades):
        trades["censored"] = trades.status.ne("closed")
        trades["source"] = [source_both[i] if arm == "v11_both" else ("htf" if arm == "v11_htf_only" else
                            "chart" if arm == "v10_4" else "v9") for arm, i in zip(trades.arm, trades.signal_i)]

        def order_of(arm, i):
            if arm == "v11_htf_only":
                return htf_order.get(i, {}).get("order")
            if arm == "v11_both":
                return (both_order.get(i) or chart_order.get(i) or {}).get("order")
            if arm == "v10_4":
                return next((j["order"] for j in r_v104.joints if j["i"] == i), None)
            return None
        trades["joint_order"] = [order_of(arm, int(i)) for arm, i in zip(trades.arm, trades.signal_i)]
        controls = study.controls(prepared, trades, minutes, facts["ready"])
    hj = pd.DataFrame([{**j, "variant": "htf_only"} for j in r_htf.htf_joints]
                      + [{**j, "variant": "both"} for j in r_both.htf_joints])
    if len(hj):
        hj["signal_bar_open"] = index[hj.i.to_numpy(int)]
        hj["in_window"] = window[hj.i.to_numpy(int)]
        hj["wait_bars_after_spike"] = hj.i - hj.spike_i
        for col in ("ax_t", "bx_t", "cx_t", "born_t", "break_t"):
            if col in hj:
                hj[col] = pd.to_datetime(hj[col] * 60, unit="s", utc=True)
    breaks = pd.DataFrame(placed)
    summary = {"symbol": symbol, "timeframe": timeframe, "htf": htf_name, "bars": len(frame),
               "htf_breaks_total": len(placed), "htf_breaks_visible": int(sum(p["visible"] for p in placed)),
               "htf_breaks_visible_in_window": int(sum(window[p["known_i"]] for p in placed if p["visible"])),
               "counts": {arm: int(m.sum()) for arm, m in masks.items()},
               "both_sources": {"chart": int((r_both.joint_event & window).sum()),
                                "htf": int((r_both.htf_joint_event & window).sum())},
               "htf_refusals_in_window": pd.Series([r["reason"] for r in r_htf.htf_refusals if window[r["i"]]],
                                                   dtype=object).value_counts().to_dict(),
               "v10_4_joint_events_differ_in_both": int(((r_v104.joint_event != r_both.joint_event) & window).sum())}
    return {"trades": trades, "statuses": statuses, "controls": controls, "htf_joints": hj, "htf_breaks": breaks,
            "summary": summary}


def run_symbol(args) -> dict:
    symbol, path, meta, output, identity_hash = args
    final = Path(output) / "streams" / symbol
    if (final / "completion.json").is_file():
        receipt = json.loads((final / "completion.json").read_text())
        if receipt.get("run_identity") != identity_hash:
            raise ValueError(f"completion identity mismatch: {final}")
        return receipt
    started = time.perf_counter()
    staging = Path(output) / "streams" / f".{symbol}.staging"
    staging.mkdir(parents=True, exist_ok=True)
    for old in staging.iterdir():
        old.unlink()
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = inc.guarded_5m(Path(path), earliest)
    parts: dict = {k: [] for k in ("trades", "statuses", "controls", "htf_joints", "htf_breaks")}
    summaries, failures = [], []
    for pair in PAIRS:
        try:
            out = run_pair(symbol, base, meta, pair)
        except ValueError as exc:
            failures.append({"timeframe": pair[0], "error": str(exc)})
            continue
        summaries.append(out["summary"])
        for name in parts:
            if name in out and len(out[name]):
                table = out[name].copy()
                table.insert(0, "timeframe_pair", f"{pair[0]}<-{pair[2]}")
                if "symbol" not in table:
                    table.insert(0, "symbol", symbol)
                parts[name].append(table)
    for name, frames in parts.items():
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(staging / f"{name}.csv.gz", index=False,
                                                        compression={"method": "gzip", "mtime": 0})
    receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash, "summaries": summaries,
               "failures": failures, "files": {p.name: study.digest(p) for p in staging.iterdir() if p.is_file()},
               "wall_seconds": round(time.perf_counter() - started, 2)}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 8, symbols: list[str] | None = None, allow_uncommitted: bool = False) -> None:
    if not allow_uncommitted and not _committed(DEPENDENCIES):
        raise ValueError("commit runner, port, tests, Pine, plan and config before any market replay")
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
    identity["params"] = json.dumps(vars(V104Params()), sort_keys=True)
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    (output / "identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    (output / "evaluation_started.json").write_text(json.dumps({
        "started_at_unix": time.time(), "holdout_consumption": 0,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "run_identity": identity_hash, "symbols": len(keys), "pairs": [f"{p[0]}<-{p[2]}" for p in PAIRS]},
        indent=2) + "\n")
    start = time.perf_counter()
    receipts = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        tasks = [pool.submit(run_symbol, (s, str(files[s]), meta.get(s, {"asset": "", "tick": math.nan}),
                                          str(output), identity_hash)) for s in keys]
        for number, future in enumerate(as_completed(tasks), 1):
            receipts.append(future.result())
            if number == 1 or number % 50 == 0 or number == len(keys):
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
