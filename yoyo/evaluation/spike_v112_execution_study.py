"""Offline single-variable execution ablations on frozen V11.2 box candidates.

Source: owner 2026-09-19 'go research' after the fixed sample50 direction audit.
Reuses ALL receipt-bound original box candidates, not just original fills.
Features read OHLCV through each signal; parent stop uses a prior signal's
original stop. Future prices are read only by exit simulation. Each arm owns
its serial occupancy. See the committed experiment plan for random-anchor
controls, equal-notional versus risk-normalized metrics and temporal splits.
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

from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v112_execution_ablation import attempt_variant
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python, _validate_completion
from yoyo.evaluation.spike_v112_support_report import compare
from yoyo.evaluation.spike_v10_4_increment_report import PARITY
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v112-execution-20260919-v1")
CONFIG, PLAN = EXP / "config.json", EXP / "PROJECT_PLAN.md"
SOURCE = Path("experiments/active/exp-spike-v112-support-20260919-v1/results/run_v1")
ARMS = ("baseline", "age6", "parent_stop", "wick_arm")
TABLES = ("trades", "statuses", "controls", "fixed")


def accepted(arm: str, age: int) -> bool:
    """Use the already published six-bar age; no future outcome is consulted."""
    return arm != "age6" or 0 <= age <= 6


def serial_candidates(candidates: list[dict], arm: str, evaluate, n: int) -> tuple[list, list]:
    """Independent occupancy; a filtered first break still consumes its box."""
    trades, statuses, flat_from = [], [], -1
    for event in candidates:
        i, age = int(event["signal_i"]), int(event["bars_after_v9"])
        if not accepted(arm, age):
            statuses.append({**event, "arm": arm, "status": "rejected_age6"})
            continue
        if i < flat_from:
            statuses.append({**event, "arm": arm, "status": "skipped_in_position"})
            continue
        status, result = evaluate(arm, i, int(event["box_entry_i"]))
        statuses.append({**event, "arm": arm, "status": status})
        if result is not None:
            trades.append({**event, **{k: result.get(k) for k in study.TRADE_KEEP}, "arm": arm, "status": status})
            flat_from = n + 1 if status == "censored_boundary" else int(result["exit_i"])
    return trades, statuses


def matched_controls(prepared, trades: list[dict], ready: np.ndarray, minutes: int, evaluate) -> list[dict]:
    """Original random clock/bucket selection; policy-specific causal stop/exit."""
    frame = prepared.frame
    vol = np.searchsorted(study.VOL_BINS, prepared.atr / prepared.close, side="left")
    months = np.asarray(frame.index.strftime("%Y-%m"))
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    fold = np.where(np.asarray(close_time < study.SPLIT), "earlier", "later")
    eligible = (ready & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close)
                & (prepared.close > 0) & study.in_window(frame.index, minutes))
    pools, rows = {}, []
    for trade in trades:
        i, arm = int(trade["signal_i"]), trade["arm"]
        key = (months[i], int(vol[i]), fold[i])
        if key not in pools:
            pools[key] = np.flatnonzero(eligible & (months == key[0]) & (vol == key[1]) & (fold == key[2]))
        choices = pools[key][pools[key] != i]
        chosen, result, reason, parent = None, None, "empty_stratum", None
        if len(choices):
            digest = int(hashlib.sha256(f"{study.CONTROL_SEED}|{trade['trade_key']}".encode()).hexdigest(), 16)
            chosen = int(choices[digest % len(choices)])
            parent = chosen - int(trade["bars_after_v9"])
            status, result = evaluate(arm, chosen, parent)
            reason = status if result is None else "censored" if result["censored"] else "matched"
        matched = reason == "matched" and not trade["censored"]
        rows.append({"trade_key": trade["trade_key"], "arm": arm, "matched": matched,
                     "reason": "target_censored" if trade["censored"] else reason,
                     "control_signal_i": chosen, "control_parent_i": parent,
                     "control_signal_bar_open": None if chosen is None else frame.index[chosen],
                     "control_net_r": result["net_r"] if matched else math.nan,
                     "control_net_return": result["net_return"] if matched else math.nan,
                     "control_exit_time": None if result is None else result["exit_time"],
                     "month": key[0], "vol_bin": key[1], "fold": key[2]})
    return rows


def run_pair(symbol: str, base, meta: dict, tf: str, decisions: pd.DataFrame) -> dict:
    """Rebuild features, independently replay each arm, and audit original fills."""
    if decisions.empty:
        return {name: [] for name in TABLES}
    minutes, tick, asset = study.TIMEFRAMES[tf], float(meta["tick"]), meta["asset"]
    bars = v11.bars_for(base, minutes)
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    key = f"binance_um:{symbol}:{tf}"
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key,
        {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": tf, "timeframe_min": minutes}, minutes, tick)
    events = []
    for r in decisions.sort_values("signal_i").to_dict("records"):
        i, s = int(r["signal_i"]), int(r["box_entry_i"])
        assert 0 <= s <= i < len(frame) and i - s == int(r["bars_after_v9"])
        assert frame.index[i] == pd.Timestamp(r["signal_bar_open"])
        assert np.isclose(frame.close.iloc[i], r["close"], atol=1e-10, rtol=1e-10)
        assert bool(facts["v9_long"][s])
        events.append({"signal_i": i, "signal_bar_open": frame.index[i], "symbol": symbol, "asset": asset,
                       "timeframe": tf, "source": r["source"], "box_entry_i": s, "bars_after_v9": i - s,
                       "trade_key": f"{key}:box_any:{i}"})
    memo = {}

    def evaluate(arm, i, s):
        engine_arm = "baseline" if arm == "age6" else arm
        cache_key = (engine_arm, i, s if engine_arm == "parent_stop" else -1)
        if cache_key not in memo:
            memo[cache_key] = attempt_variant(prepared, i, s, engine_arm)
        return memo[cache_key]

    out = {name: [] for name in TABLES}
    for arm in ARMS:
        trades, statuses = serial_candidates(events, arm, evaluate, len(frame))
        for trade in trades:
            _, original = evaluate("baseline", int(trade["signal_i"]), int(trade["box_entry_i"]))
            fraction = float(original["initial_risk_frac"]) if original else math.nan
            trade["baseline_risk_frac"] = fraction
            trade["net_r_on_baseline_risk"] = float(trade["net_return"]) / fraction
        out["trades"].extend(trades)
        out["statuses"].extend(statuses)
    out["controls"] = matched_controls(prepared, out["trades"], facts["ready"], minutes, evaluate)
    baseline = [t for t in out["trades"] if t["arm"] == "baseline"]
    for original in baseline:
        for arm in ARMS:
            i, s = int(original["signal_i"]), int(original["box_entry_i"])
            if not accepted(arm, i - s):
                status, result = "rejected_age6", None
            else:
                status, result = evaluate(arm, i, s)
            item = {"trade_key": original["trade_key"], "symbol": symbol, "timeframe": tf,
                    "signal_i": i, "signal_bar_open": frame.index[i], "arm": arm, "status": status,
                    "baseline_risk_frac": original["initial_risk_frac"]}
            if result is not None:
                item.update({k: result.get(k) for k in study.TRADE_KEEP})
                item["net_r_on_baseline_risk"] = float(result["net_return"]) / original["initial_risk_frac"]
            elif status == "rejected_age6":
                item.update(net_return=0., gross_return=0., net_r_on_baseline_risk=0.)
            out["fixed"].append(item)
    return out


def validate_receipt(directory: Path, identity_hash: str, input_sha: str) -> dict:
    """Resume only complete files bound to the same input and executable code."""
    receipt = json.loads((directory / "completion.json").read_text())
    assert receipt["run_identity"] == identity_hash and receipt["input_sha256"] == input_sha
    assert set(receipt["files"]) == {f"{name}.csv.gz" for name in TABLES}
    for name, digest in receipt["files"].items():
        assert study.digest(directory / name) == digest
    return receipt


def run_symbol(args) -> dict:
    symbol, path, meta, output, identity_hash, input_sha = args
    final = Path(output) / "streams" / symbol
    if final.exists():
        return validate_receipt(final, identity_hash, input_sha)
    started = time.perf_counter()
    assert study.digest(Path(path)) == input_sha
    source = SOURCE / "streams" / symbol
    decisions = pd.read_csv(source / "decisions.csv.gz")
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = inc.guarded_5m(Path(path), earliest)
    parts = {name: [] for name in TABLES}
    for tf in ("15m", "1h"):
        out = run_pair(symbol, base, meta, tf, decisions[decisions.timeframe == tf])
        for name in TABLES:
            parts[name].extend(out[name])
    tables = {name: pd.DataFrame(rows) for name, rows in parts.items()}
    if not tables["trades"].empty:
        original = pd.read_csv(source / "trades.csv.gz").query("arm == 'box_any'")
        checks = compare(original, tables["trades"].query("arm == 'baseline'"),
                         [*PARITY, "initial_risk_frac", "gross_return", "net_return", "status"], "baseline")
        assert checks["passed"], checks
        old_control = pd.read_csv(source / "controls.csv.gz").query("arm == 'box_any'")
        checks_control = compare(old_control, tables["controls"].query("arm == 'baseline'"),
                                 ["matched", "control_signal_i", "control_net_r", "control_net_return"], "controls")
        assert checks_control["passed"], checks_control
    else:
        assert pd.read_csv(source / "trades.csv.gz").query("arm == 'box_any'").empty
        checks, checks_control = {"passed": True, "left": 0, "right": 0}, {"passed": True}
    got = {(r["timeframe"], int(r["signal_i"])): r["status"] for r in parts["statuses"] if r["arm"] == "baseline"}
    expected = {(r.timeframe, int(r.signal_i)): r.box_any_status for r in decisions.itertuples()}
    assert got == expected
    staging = Path(output) / "streams" / f".{symbol}.staging"
    staging.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        if table.empty:
            table = pd.DataFrame(columns=["arm", "trade_key", "symbol", "timeframe", "signal_i", "signal_bar_open", "status"])
        table.to_csv(staging / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {"symbol": symbol, "status": "complete", "run_identity": identity_hash, "input_sha256": input_sha,
               "baseline_parity": checks, "control_parity": checks_control, "candidate_status_parity": True,
               "files": {f"{name}.csv.gz": study.digest(staging / f"{name}.csv.gz") for name in TABLES},
               "wall_seconds": time.perf_counter() - started}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2) + "\n")
    staging.replace(final)
    return receipt


def run(output: Path, workers: int, symbols: list[str] | None) -> None:
    config = json.loads(CONFIG.read_text())
    assert config["arms"] == list(ARMS) and config["max_parent_age"] == 6
    spec = study.ExecutionSpec()
    assert spec.arm_r == config["arm_r"] == 2. and spec.trail_atr == config["trail_atr"] == 4.
    assert spec.round_trip_cost == config["roundtrip_cost"] == .002
    assert pd.Timestamp(config["split"]) == study.SPLIT and pd.Timestamp(config["start"]) == study.START
    assert pd.Timestamp(config["end_exclusive"]) == inc.DATA_END
    assert config["control_seed"] == study.CONTROL_SEED
    source_manifest = json.loads((SOURCE / "manifest.json").read_text())
    source_identity = json.loads((SOURCE / "identity.json").read_text())
    assert source_manifest["complete"] and not source_manifest["failures"]
    assert hashlib.sha256(json.dumps(source_identity, sort_keys=True).encode()).hexdigest() == source_manifest["run_identity"]
    files = study.series_files()
    assert len(files) == config["expected_symbols"] == 638 and set(files) == set(source_identity["inputs"])
    keys = sorted(symbols or files)
    assert set(keys) <= set(files)
    receipt_sha = {}
    for symbol in keys:
        _validate_completion(SOURCE / "streams" / symbol, source_manifest["run_identity"], source_identity["inputs"][symbol])
        receipt_sha[symbol] = study.digest(SOURCE / "streams" / symbol / "completion.json")
    code = _local_transitive_python((Path(__file__), Path("yoyo/evaluation/spike_v112_execution_ablation.py")))
    declared = (*code, CONFIG, PLAN, Path("tests/evaluation/test_spike_v112_execution_ablation.py"),
                Path("tests/evaluation/test_spike_v112_execution_study.py"))
    assert _committed(declared), "Commit all executable dependencies, focused tests, plan and config first."
    assert study.digest(study.EXCHANGE_INFO) == source_identity["exchange_info"]["sha256"]
    identity = {"schema": "v112-execution-ablation-v1", "declared": {str(p): study.digest(p) for p in declared},
                "source_identity_sha256": study.digest(SOURCE / "identity.json"), "source_receipts": receipt_sha,
                "inputs": {sym: source_identity["inputs"][sym] for sym in keys},
                "exchange_info_sha256": study.digest(study.EXCHANGE_INFO)}
    ih = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    identity_path = output / "identity.json"
    if identity_path.exists():
        assert json.loads(identity_path.read_text()) == identity
    else:
        identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    start_path = output / "evaluation_started.json"
    if not start_path.exists():
        start_path.write_text(json.dumps({"run_identity": ih, "started_at_unix": time.time(),
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}, indent=2) + "\n")
    meta, receipts, errors, start = study.symbol_meta(), [], {}, time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_symbol, (sym, str(files[sym]), meta[sym], str(output), ih, identity["inputs"][sym])): sym for sym in keys}
        for k, future in enumerate(as_completed(futures), 1):
            try:
                receipts.append(future.result())
            except Exception as exc:
                errors[futures[future]] = f"{type(exc).__name__}: {exc}"
                print(json.dumps({"error": futures[future], "detail": errors[futures[future]]}), flush=True)
            if k == 1 or k % 50 == 0 or k == len(keys):
                print(json.dumps({"completed": k, "target": len(keys), "errors": len(errors),
                                  "elapsed_seconds": round(time.perf_counter() - start, 1)}), flush=True)
    (output / "manifest.json").write_text(json.dumps({"complete": not errors and symbols is None and len(receipts) == 638,
        "symbols": len(keys), "completed": len(receipts), "errors": errors, "run_identity": ih,
        "wall_seconds": time.perf_counter() - start}, indent=2) + "\n")
    if errors:
        raise RuntimeError(f"{len(errors)} streams failed; retained as errors, not silently dropped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()
    run(args.output, args.workers, args.symbols)
