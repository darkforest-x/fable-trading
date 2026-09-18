"""SPIKE V11.1 box rule: a break while the chart's V9 long box is open is 突破+spike.

Source: owner 2026-09-18, answering whether "15m 已经出多头信号框、叠加上级突破就算"
means "as long as the 15m long box is still open, no 6-bar limit, no check on the
break bar": 「不要任何限制」「是的」「单独回测 突破➕spike的」. Frozen plan/config in
`experiments/active/exp-spike-v11-box-joint-20260918-v1/`.

Rule: on a chart bar where a break is seen -- the higher timeframe's break first
visible on this bar (15m<-1h, 1h<-4h; `spike_v11_study.htf_inputs`) and/or the
chart's own V10.4 line break -- if the V9 long reference box (the indicator's
own risk box, opened by a V9 final long at that close, ended by its protection,
a raw V9 short or a data gap) is open after this bar's close, that bar is a
突破+spike. No window, no second-bar gate. One joint per box (its first break).
The joint is traded on its own: next bar's open, risk frozen at the joint bar,
the published V9 exit, 0.2% round trip, one position per stream.

Arms: v9_long and v10_4 (references; must reproduce earlier ledgers), box_htf
(higher break only), box_chart (own-timeframe break only), box_any (either;
primary). Causality: the box state and both break sources use only bars up to
the current close. Holdout: every 5m row must open before 2026-05-01.
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
from yoyo.evaluation.spike_v10_4 import V104Params, box_joints, joint_events, reference_long_exits

EXP = Path("experiments/active/exp-spike-v11-box-joint-20260918-v1")
CONFIG = EXP / "config.json"
ARMS = ("v9_long", "v10_4", "box_htf", "box_chart", "box_any")
KEY_NAME = {"v9_long": "v9_long", "v10_4": "joint", "box_htf": "box_htf", "box_chart": "box_chart", "box_any": "box_any"}
DEPENDENCIES = (Path(__file__), Path("yoyo/evaluation/spike_v10_4.py"), Path("yoyo/evaluation/spike_v11_study.py"),
                Path("yoyo/evaluation/spike_v10_4_study.py"), Path("yoyo/evaluation/spike_v10_4_increment.py"),
                Path("tests/evaluation/test_spike_v11_box.py"), CONFIG, EXP / "PROJECT_PLAN.md")


def serial(prepared, candidates: np.ndarray, key: str, arm: str) -> tuple[list[dict], list[dict]]:
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
    bars = v11.bars_for(base, minutes)
    if not len(bars) or not study.in_window(bars.index, minutes).any():
        return {"summary": {"symbol": symbol, "timeframe": timeframe, "skipped": "no_bar_in_window"}}
    params = V104Params()
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    box: dict = {}
    again = reference_long_exits(frame.high, frame.low, frame.close, frame.atr, ready=facts["ready"], gap=facts["gap"],
                                 raw_side=facts["side"], signal_side=np.where(facts["v9"], facts["side"], 0),
                                 tick=tick, state=box)
    if not np.array_equal(again, facts["ref_long_exit"]):
        raise AssertionError("box state disagrees with the reference used by V10.4")
    H, placed = v11.htf_inputs(frame.index, minutes, v11.bars_for(base, htf_minutes), htf_minutes, tick, params)
    r_v104 = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr,
                          can_run=facts["can_run"], confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                          parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                          momentum=facts["momentum"], current_gate=facts["current_gate"],
                          ref_long_exit=facts["ref_long_exit"], tick=tick, params=params)
    htf_now, chart_now = H["known"], r_v104.break_event
    window = study.in_window(frame.index, minutes)
    joints = {"box_htf": box_joints(box["long_open"], box["box_entry"], htf_now),
              "box_chart": box_joints(box["long_open"], box["box_entry"], chart_now),
              "box_any": box_joints(box["long_open"], box["box_entry"], htf_now | chart_now)}
    masks = {"v9_long": facts["v9_long"] & window, "v10_4": r_v104.joint_event & window,
             **{arm: m & window for arm, m in joints.items()}}
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
    for table in (trades, statuses):
        if len(table):
            table["symbol"], table["asset"], table["timeframe"] = symbol, asset, timeframe
            table["signal_bar_open"] = index[table.signal_i.to_numpy(int)]
    controls = pd.DataFrame()
    if len(trades):
        i_arr = trades.signal_i.to_numpy(int)
        trades["censored"] = trades.status.ne("closed")
        trades["source"] = np.where(htf_now[i_arr] & chart_now[i_arr], "both",
                                    np.where(htf_now[i_arr], "htf", np.where(chart_now[i_arr], "chart", "")))
        trades["box_entry_i"] = box["box_entry"][i_arr]
        trades["bars_after_v9"] = np.where(trades.box_entry_i >= 0, i_arr - trades.box_entry_i, -1)
        controls = study.controls(prepared, trades, minutes, facts["ready"])
    boxes_in_window = np.unique(box["box_entry"][(box["box_entry"] >= 0) & window])
    summary = {"symbol": symbol, "timeframe": timeframe, "htf": htf_name, "bars": len(frame),
               "boxes_in_window": int(len(boxes_in_window)),
               "counts": {arm: int(m.sum()) for arm, m in masks.items()},
               "htf_breaks_visible_in_window": int((htf_now & window).sum()),
               "chart_breaks_in_window": int((chart_now & window).sum())}
    return {"trades": trades, "statuses": statuses, "controls": controls, "summary": summary}


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
    parts: dict = {k: [] for k in ("trades", "statuses", "controls")}
    summaries, failures = [], []
    for pair in v11.PAIRS:
        try:
            out = run_pair(symbol, base, meta, pair)
        except ValueError as exc:
            failures.append({"timeframe": pair[0], "error": str(exc)})
            continue
        summaries.append(out["summary"])
        for name in parts:
            if name in out and len(out[name]):
                table = out[name].copy()
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
        raise ValueError("commit runner, port, tests, plan and config before any market replay")
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
