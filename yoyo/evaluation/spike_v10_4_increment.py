"""SPIKE V10.4 1h increment audit: V9-only vs break-only vs joint, with full event ledgers.

Source: owner request 2026-09-18 「SPIKE V10.4 · 1h 复现与趋势线增量验证」; frozen plan
and config in `experiments/active/exp-spike-v10-4-1h-increment-20260918-v1/`.
It reuses, unchanged, the original runner's loader/aggregation (`spike_v10_4_study`),
the published V9 engine (`v9_facts`), the V10.4 port (`joint_events`, now with a
record-only trace hook) and the published fixed-entry exit (`replay_fixed_entry`).

Three arms share data, universe, window, cost and exit; each keeps its own serial
position state:
  * v9_only    every final V9 long (the original `v9_long` arm);
  * break_only every bar on which the frozen V10.4 engine reports a line break
               winner (Pine `v10BreakEvent`), with no V9, BB, MA, momentum or
               parent-range gate -- a module ablation reference, not a strategy;
  * joint      the original V10.4 joint event (6-bar window, all second-bar gates).

Besides trades it writes one row per V9 event, structure, break and joint with
stable IDs, the reason a V9 event did not pair (from the trace), per-arm candidate
status (taken/closed, censored at the boundary or at a gap, skipped in position,
no next bar, next bar is a gap, risk invalid), independent shadow exits for every
V9 event and every joint (overlapping, attribution only), and the bars inside
each data-gap window where the published V9 engine and the Pine differ.

Causality: signals use bars up to their close; exits start at the next bar's
open; the trace only records. Data boundary: every 5m row must open before
2026-05-01 00:00 UTC (`DATA_END`); anything later raises before any computation.
No fetching, tuning, promotion, holdout read or execution integration.
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
from yoyo.evaluation.spike_v7_fast import _initial_position_fast
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v10_4 import V104Params, joint_events

EXP = Path("experiments/active/exp-spike-v10-4-1h-increment-20260918-v1")
CONFIG = EXP / "config.json"
TIMEFRAME, MINUTES = "1h", 60
DATA_END = pd.Timestamp("2026-05-01T00:00:00Z")
ARM_KEYS = {"v9_only": "v9_long", "break_only": "break_only", "joint": "joint"}
# Published V9 engine vs Pine at a data gap: Pine clears its 20-volume median
# and needs segmentIndex >= 12 for ready/dense; the engine does neither. The
# furthest bar that can still differ is 20 volumes + 3 bars of volumeMedian[3].
GAP_AUDIT_BARS = 24
DEPENDENCIES = (Path(__file__), Path("yoyo/evaluation/spike_v10_4.py"), Path("yoyo/evaluation/spike_v10_4_study.py"),
                Path("tests/evaluation/test_spike_v10_4.py"), Path("tests/evaluation/test_spike_v10_4_increment.py"),
                CONFIG, EXP / "plan.md", Path("yoyo/evaluation/spike_v7_fast.py"),
                Path("yoyo/evaluation/spike_burst_replay.py"), Path("yoyo/evaluation/spike_v1_v8_be05.py"))


def guarded_5m(path: Path, since: pd.Timestamp) -> pd.DataFrame:
    """The original loader plus this round's boundary: nothing opening at/after DATA_END."""
    raw_last = pd.read_csv(path, usecols=["ts"]).ts.max()
    if pd.Timestamp(int(raw_last), unit="ms", tz="UTC") >= DATA_END:
        raise ValueError(f"{path.name}: a 5m row opens at or after {DATA_END}; refusing to read")
    frame = study.load_5m(path, since)
    if len(frame) and frame.index.max() >= DATA_END:
        raise ValueError(f"{path.name}: loaded bar at or after {DATA_END}")
    return frame


def bars_1h(path: Path) -> pd.DataFrame:
    """Exactly the original run's 1h construction (same earliest load and floor)."""
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = guarded_5m(path, earliest)
    since = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * MINUTES)
    bars, _ = study.aggregate(base.loc[base.index >= since.floor(f"{MINUTES}min")], MINUTES)
    if len(bars) and bars.index.max() + pd.Timedelta(minutes=MINUTES) > DATA_END:
        raise ValueError("an aggregated 1h bar closes after DATA_END")
    return bars


def stamp(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y%m%dT%H%MZ")


def attempt(prepared: fixed.PreparedArm, i: int) -> tuple[str, dict | None]:
    """One long signalled at bar i's close; the status names why no trade exists."""
    n = len(prepared.frame)
    if i + 1 >= n:
        return "no_next_bar", None
    if prepared.gap[i + 1]:
        return "next_bar_is_gap", None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                 prepared.close, prepared.atr, prepared.gap, i, 1, prepared.spec)
    if row is None:
        return "risk_invalid", None
    result = fixed.replay_fixed_entry(prepared.context, pd.Series(row), arm="v8", enable_be=False, prepared=prepared)
    if not result["censored"]:
        return "closed", result
    return ("censored_boundary" if result["exit_reason"] == "boundary_mark" else "censored_gap"), result


def serial(prepared: fixed.PreparedArm, candidates: np.ndarray, key: str, arm: str) -> tuple[list[dict], list[dict]]:
    """The original serial rule (take only when flat at the signal close), with every status kept."""
    trades, statuses = [], []
    flat_from, holder = -1, None
    for i in candidates.tolist():
        if i < flat_from:
            statuses.append({"arm": arm, "signal_i": i, "status": "skipped_in_position", "blocking_trade": holder})
            continue
        status, result = attempt(prepared, i)
        statuses.append({"arm": arm, "signal_i": i, "status": status, "blocking_trade": None})
        if result is None:
            continue
        trade_key = f"{key}:{ARM_KEYS[arm]}:{i}"
        trades.append({**{k: result.get(k) for k in study.TRADE_KEEP}, "status": status, "arm": arm,
                       "trade_key": trade_key})
        holder = trade_key
        flat_from = len(prepared.frame) + 1 if status == "censored_boundary" else int(result["exit_i"])
    return trades, statuses


def shadow(prepared: fixed.PreparedArm, indices, label: str) -> list[dict]:
    """Independent (overlapping) exits for attribution; never an executable account."""
    rows = []
    for i in indices:
        status, result = attempt(prepared, int(i))
        row = {"path": label, "signal_i": int(i), "status": status}
        if result is not None:
            row.update({k: result.get(k) for k in ("entry_i", "entry_time", "entry_price", "initial_stop",
                                                   "initial_risk", "initial_risk_frac", "exit_i", "exit_time",
                                                   "exit_price", "exit_reason", "gross_return", "net_return",
                                                   "gross_r", "net_r", "mfe_r")})
        rows.append(row)
    return rows


def classify(spike_i: int, joint_spikes: set, snapshots: dict, events: pd.DataFrame, attempts: pd.DataFrame,
             last_i: int, window: int) -> str:
    """Why one V9 event did or did not become a joint, from the trace only."""
    if spike_i in joint_spikes:
        return "joint"
    snap = snapshots.get(spike_i)
    if snap is None or not snap["can_run"]:
        return "engine_not_running"
    if not snap["available"]:
        return "no_available_line"
    tried = attempts.loc[(attempts.spike_i == spike_i) & ~attempts.eligible] if len(attempts) else attempts
    if len(tried):
        return "pair_refused:" + str(tried.iloc[-1].reason)
    mine = events.loc[(events.spike_i == spike_i) & (events.event != "saved")] if len(events) else events
    if len(mine) and (mine.event == "pending_at_data_end").any():
        return "pending_at_data_end"
    if spike_i + window >= last_i and not len(mine):
        return "pending_at_data_end"
    if not len(mine):
        return "unresolved"
    last = mine.sort_values("i", kind="stable").iloc[-1].event
    return str(last).replace("dropped:", "")


def run_symbol(args) -> dict:
    symbol, path, meta, output, identity_hash, pivot_ties = args
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
    tick, asset = float(meta["tick"]), meta["asset"]
    key = f"binance_um:{symbol}:{TIMEFRAME}"
    bars = bars_1h(Path(path))
    window_any = len(bars) and study.in_window(bars.index, MINUTES).any()
    if not window_any:
        receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash, "skipped": "no_bar_in_window",
                   "files": {}, "wall_seconds": round(time.perf_counter() - started, 2)}
        (staging / "completion.json").write_text(json.dumps(receipt, indent=2) + "\n")
        staging.replace(final)
        return receipt
    params = V104Params(pivot_ties=pivot_ties)
    facts = study.v9_facts(bars, MINUTES, asset, tick)
    frame = facts["frame"]
    inputs = dict(can_run=facts["can_run"], confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                  parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                  momentum=facts["momentum"], current_gate=facts["current_gate"],
                  ref_long_exit=facts["ref_long_exit"], tick=tick, params=params)
    plain = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr, **inputs)
    trace: dict = {}
    joint = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr, trace=trace, **inputs)
    for name in ("born_event", "break_event", "joint_event"):
        if not np.array_equal(getattr(plain, name), getattr(joint, name)):
            raise AssertionError(f"trace changed {name} in {symbol}")
    if plain.joints != [{k: v for k, v in j.items() if k != "displayed_main_before"} for j in joint.joints]:
        raise AssertionError(f"trace changed joint records in {symbol}")

    index = frame.index
    window = study.in_window(index, MINUTES)
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": TIMEFRAME,
                "timeframe_min": MINUTES}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key, identity, MINUTES, tick)
    masks = {"v9_only": facts["v9_long"] & window, "break_only": joint.break_event & window,
             "joint": joint.joint_event & window}
    trade_rows, status_rows = [], []
    for arm, mask in masks.items():
        t, s = serial(prepared, np.flatnonzero(mask), key, arm)
        trade_rows += t
        status_rows += s
    trades = pd.DataFrame(trade_rows)
    statuses = pd.DataFrame(status_rows)
    for frame_ in (trades, statuses):
        if len(frame_):
            frame_["symbol"], frame_["asset"], frame_["stream_key"] = symbol, asset, key
            frame_["signal_bar_open"] = index[frame_.signal_i.to_numpy(int)]
    controls = pd.DataFrame()
    if len(trades):
        trades["censored"] = trades.status.ne("closed")
        controls = study.controls(prepared, trades, MINUTES, facts["ready"])

    lines = pd.DataFrame(trace["lines"])
    line_events = pd.DataFrame(trace["line_events"])
    spike_events = pd.DataFrame(trace["spike_events"])
    attempts = pd.DataFrame(trace["pair_attempts"])
    snapshots = {s["i"]: s for s in trace["v9_snapshots"]}
    if len(lines):
        lines["line_id"] = [f"{symbol}|1h|L|{stamp(index[b])}|s{src}|A{stamp(index[a])}|B{stamp(index[bb])}|C{stamp(index[c])}"
                            for b, src, a, bb, c in zip(lines.born_i, lines.source, lines.ax, lines.bx, lines.cx)]
        lines["born_time"] = index[lines.born_i.to_numpy(int)]
        uid_to_line = dict(zip(lines.uid, lines.line_id))
        if len(line_events):
            ended = line_events.groupby("uid").last()
            lines["end_event"] = lines.uid.map(ended.event)
            lines["end_i"] = lines.uid.map(ended.i)
    else:
        uid_to_line = {}

    joints = pd.DataFrame(joint.joints)
    joint_spikes = set(joints.spike_i.tolist()) if len(joints) else set()
    if len(joints):
        joints["joint_id"] = [f"{symbol}|1h|J|{stamp(index[i])}" for i in joints.i]
        joints["v9_event_id"] = [f"{symbol}|1h|V9L|{stamp(index[s])}" for s in joints.spike_i]
        joints["line_id"] = joints.uid.map(uid_to_line)
        joints["signal_bar_open"] = index[joints.i.to_numpy(int)]
        joints["in_window"] = window[joints.i.to_numpy(int)]
        joints["wait_bars_after_v9"] = joints.i - joints.spike_i
        joints["line_displayed_before_joint"] = joints.displayed_main_before.eq(joints.uid)

    v9_idx = np.flatnonzero(facts["v9_long"] & window)
    last_i = len(frame) - 1
    v9 = pd.DataFrame({"signal_i": v9_idx})
    v9["v9_event_id"] = [f"{symbol}|1h|V9L|{stamp(index[i])}" for i in v9_idx]
    v9["signal_bar_open"] = index[v9_idx]
    v9["available_lines"] = [len(snapshots.get(i, {"available": []})["available"]) for i in v9_idx]
    v9["pair_status"] = [classify(int(i), joint_spikes, snapshots, spike_events, attempts, last_i, params.window)
                         for i in v9_idx]
    if len(joints):
        by_spike = joints.drop_duplicates("spike_i").set_index("spike_i")
        v9["joint_id"] = v9.signal_i.map(by_spike.joint_id)
        v9["joint_order"] = v9.signal_i.map(by_spike.order)
        v9["joint_i"] = v9.signal_i.map(by_spike.i)
    else:
        v9["joint_id"] = v9["joint_order"] = v9["joint_i"] = None

    breaks = pd.DataFrame({"signal_i": np.flatnonzero(joint.break_event & window)})
    breaks["break_id"] = [f"{symbol}|1h|BRK|{stamp(index[i])}" for i in breaks.signal_i]
    breaks["winner_line_id"] = [uid_to_line.get(int(trace["break_winner_uid"][i])) for i in breaks.signal_i]
    breaks["signal_bar_open"] = index[breaks.signal_i.to_numpy(int)]

    shadows = pd.DataFrame(shadow(prepared, v9_idx, "v9_event")
                           + shadow(prepared, joints.loc[joints.in_window, "i"].tolist() if len(joints) else [], "joint_event"))
    gap_rows = []
    for g in np.flatnonzero(facts["gap"]):
        lo, hi = int(g), min(int(g) + GAP_AUDIT_BARS - 1, last_i)
        span = slice(lo, hi + 1)
        gap_rows.append({"gap_i": lo, "gap_bar_open": index[lo], "window_end_i": hi,
                         "raw_signals": int((facts["side"][span] != 0).sum()),
                         "v9_final_long": int(facts["v9_long"][span].sum()),
                         "v9_final_any": int(facts["v9"][span].sum()),
                         "joint_events": int(joint.joint_event[span].sum()),
                         "break_events": int(joint.break_event[span].sum()),
                         "in_eval_window": bool(window[span].any())})
    tables = {"trades": trades, "statuses": statuses, "controls": controls, "v9_events": v9, "joints": joints,
              "lines": lines, "line_events": line_events, "spike_events": spike_events, "pair_attempts": attempts,
              "breaks": breaks, "shadows": shadows, "gap_audit": pd.DataFrame(gap_rows)}
    for name, table in tables.items():
        if len(table):
            table = table.copy()
            if "symbol" not in table:
                table.insert(0, "symbol", symbol)
            table.to_csv(staging / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    snaps = [{**s, "signal_bar_open": str(index[s["i"]])} for s in trace["v9_snapshots"] if window[s["i"]]]
    (staging / "v9_snapshots.json").write_text(json.dumps(snaps) + "\n")
    receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash, "bars": len(frame),
               "first_bar": str(index[0]), "last_bar": str(index[-1]), "pivot_ties": pivot_ties,
               "counts": {arm: int(m.sum()) for arm, m in masks.items()}, "gaps": int(facts["gap"].sum()),
               "files": {p.name: study.digest(p) for p in staging.iterdir() if p.is_file()},
               "wall_seconds": round(time.perf_counter() - started, 2)}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 8, symbols: list[str] | None = None, pivot_ties: str = "strict",
        allow_uncommitted: bool = False) -> None:
    if not allow_uncommitted and not _committed(DEPENDENCIES):
        raise ValueError("commit runner, port, tests, plan and config before any market replay")
    config = json.loads(CONFIG.read_text())
    files = study.series_files()
    if len(files) != config["expected_symbols"]:
        raise ValueError(f"source coverage changed: {len(files)}")
    if study.digest(study.EXCHANGE_INFO) != config["exchange_info_sha256"]:
        raise ValueError("exchangeInfo snapshot changed")
    for path in files.values():
        last = pd.read_csv(path, usecols=["ts"]).ts.max()
        if pd.Timestamp(int(last), unit="ms", tz="UTC") >= DATA_END:
            raise ValueError(f"boundary check failed before any computation: {path.name}")
    meta = study.symbol_meta()
    keys = symbols or sorted(files)
    identity = {str(p): study.digest(p) for p in DEPENDENCIES}
    identity["params"] = json.dumps(vars(V104Params(pivot_ties=pivot_ties)), sort_keys=True)
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    ipath = output / "identity.json"
    if ipath.exists() and json.loads(ipath.read_text()) != identity:
        raise ValueError("run identity drift; choose a new output directory")
    ipath.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    started_path = output / "evaluation_started.json"
    if not started_path.exists():
        started_path.write_text(json.dumps({
            "started_at_unix": time.time(), "holdout_consumption": 0, "run_id": config["run_id"],
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "run_identity": identity_hash, "symbols": len(keys), "pivot_ties": pivot_ties,
            "boundary": f"every 5m row opens before {DATA_END}; checked for all {len(files)} files"}, indent=2) + "\n")
    start = time.perf_counter()
    receipts = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        tasks = {pool.submit(run_symbol, (s, str(files[s]), meta.get(s, {"asset": "", "tick": math.nan}),
                                          str(output), identity_hash, pivot_ties)): s for s in keys}
        for number, future in enumerate(as_completed(tasks), 1):
            receipts.append(future.result())
            if number == 1 or number % 50 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys),
                                  "elapsed_seconds": round(time.perf_counter() - start, 1)}), flush=True)
    (output / "manifest.json").write_text(json.dumps({
        "complete": symbols is None, "symbols": len(keys), "run_identity": identity_hash, "run_id": config["run_id"],
        "pivot_ties": pivot_ties, "holdout_consumption": 0, "wall_seconds": round(time.perf_counter() - start, 1),
        "receipts_sha256": hashlib.sha256(json.dumps({r["symbol"]: r["files"] for r in receipts},
                                                     sort_keys=True).encode()).hexdigest(),
        }, indent=2, default=str) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--pivot-ties", choices=["strict", "right_inclusive"], default="strict")
    parser.add_argument("--allow-uncommitted", action="store_true", help="smoke runs only; not reportable")
    args = parser.parse_args()
    run(args.output, workers=args.workers, symbols=args.symbols, pivot_ties=args.pivot_ties,
        allow_uncommitted=args.allow_uncommitted)
