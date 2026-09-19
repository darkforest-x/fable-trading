"""V11.2 support gate study: require the box's first break to close over six-MA support.

The owner authorized exactly one V11.1 change on 2026-09-19: a box joint remains
an event only when its closed signal bar is strictly above
``max(SMA20, EMA20, SMA60, EMA60, SMA120, EMA120)``.  The published feature
engine stores precisely that maximum as ``ropeHigh``.  The gate is therefore
``finite(close, ropeHigh) and close > ropeHigh``; it reads only the signal bar.

The original box event is formed first.  Thus an unsupported first break still
consumes its box, and a later supported break cannot retry it.  ``box_any`` and
``box_support`` each perform a full independent serial replay, so filtering an
earlier trade can free a later candidate in the support arm.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v11_box_study as source
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v10_4 import V104Params, box_joints, joint_events, reference_long_exits

EXP = Path("experiments/active/exp-spike-v112-support-20260919-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST = Path("tests/evaluation/test_spike_v112_support.py")
ARMS = ("box_any", "box_support")
OUTPUT_TABLES = ("trades", "statuses", "controls", "decisions")


def support_gate(close: float, rope_high: float) -> bool:
    """Return the causal V11.2 support decision for one closed signal bar."""
    return bool(math.isfinite(close) and math.isfinite(rope_high) and close > rope_high)


def support_mask(close: np.ndarray, rope_high: np.ndarray) -> np.ndarray:
    """Vectorized form of :func:`support_gate`, with no history or future input."""
    close_a, rope_a = np.asarray(close, float), np.asarray(rope_high, float)
    return np.isfinite(close_a) & np.isfinite(rope_a) & (close_a > rope_a)


def serial(prepared, candidates: np.ndarray, key: str, arm: str) -> tuple[list[dict], list[dict]]:
    """Replay one arm's complete candidate sequence using its own position state."""
    # Reuse V11.1's serial loop byte-for-byte in behavior.  Passing box_any
    # intentionally preserves the old event key in both arms; arm remains a
    # separate column, including in the controls' composite identity.
    trades, statuses = source.serial(prepared, candidates, key, "box_any")
    if arm != "box_any":
        for row in trades:
            row["arm"] = arm
        for row in statuses:
            row["arm"] = arm
    return trades, statuses


def _empty_parts() -> dict[str, pd.DataFrame]:
    return {
        "trades": pd.DataFrame(columns=[*study.TRADE_KEEP, "status", "arm", "trade_key", "symbol", "asset", "timeframe"]),
        "statuses": pd.DataFrame(columns=["arm", "signal_i", "status", "blocking_trade", "symbol", "asset", "timeframe", "signal_bar_open"]),
        "controls": pd.DataFrame(columns=["trade_key", "arm", "matched", "reason"]),
        "decisions": pd.DataFrame(columns=["symbol", "timeframe", "signal_i", "signal_bar_open", "box_entry_i",
                                             "source", "bars_after_v9", "close", "ropeHigh", "support_pass",
                                             "distance_atr", "box_any_status", "box_support_status"]),
    }


def run_pair(symbol: str, base: pd.DataFrame, meta: dict, pair: tuple) -> dict:
    """Replay both V11.2 arms for one original V11.1 symbol/timeframe pair."""
    timeframe, minutes, htf_name, htf_minutes = pair
    tick, asset = float(meta["tick"]), meta["asset"]
    key = f"binance_um:{symbol}:{timeframe}"
    bars = v11.bars_for(base, minutes)
    if not len(bars) or not study.in_window(bars.index, minutes).any():
        return {"summary": {"symbol": symbol, "timeframe": timeframe, "skipped": "no_bar_in_window"}, **_empty_parts()}
    params = V104Params()
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    box: dict = {}
    again = reference_long_exits(frame.high, frame.low, frame.close, frame.atr, ready=facts["ready"], gap=facts["gap"],
                                 raw_side=facts["side"], signal_side=np.where(facts["v9"], facts["side"], 0),
                                 tick=tick, state=box)
    if not np.array_equal(again, facts["ref_long_exit"]):
        raise AssertionError("box state disagrees with the published V11.1 reference")
    htf, _ = v11.htf_inputs(frame.index, minutes, v11.bars_for(base, htf_minutes), htf_minutes, tick, params)
    chart = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr,
                         can_run=facts["can_run"], confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                         parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                         momentum=facts["momentum"], current_gate=facts["current_gate"], ref_long_exit=facts["ref_long_exit"],
                         tick=tick, params=params)
    htf_now, chart_now = htf["known"], chart.break_event
    window = study.in_window(frame.index, minutes)
    # This must precede support filtering: it encodes one original break per V9 box.
    baseline = box_joints(box["long_open"], box["box_entry"], htf_now | chart_now) & window
    close, rope_high, atr = frame.close.to_numpy(float), frame.ropeHigh.to_numpy(float), frame.atr.to_numpy(float)
    passed = support_mask(close, rope_high)
    masks = {"box_any": baseline, "box_support": baseline & passed}
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": timeframe,
                "timeframe_min": minutes}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    records, statuses = [], []
    for arm in ARMS:
        trade_rows, status_rows = serial(prepared, np.flatnonzero(masks[arm]), key, arm)
        records.extend(trade_rows)
        statuses.extend(status_rows)
    parts = _empty_parts()
    parts["trades"] = pd.DataFrame(records, columns=[*study.TRADE_KEEP, "status", "arm", "trade_key"])
    parts["statuses"] = pd.DataFrame(statuses, columns=["arm", "signal_i", "status", "blocking_trade"])
    index = frame.index
    for name in ("trades", "statuses"):
        table = parts[name]
        if len(table):
            table["symbol"], table["asset"], table["timeframe"] = symbol, asset, timeframe
            table["signal_bar_open"] = index[table.signal_i.to_numpy(int)]
    if len(parts["trades"]):
        parts["trades"]["censored"] = parts["trades"].status.ne("closed")
        trade_i = parts["trades"].signal_i.to_numpy(int)
        # Keep the original V11.1 executable-ledger fields and their meanings;
        # the support arm refers to the same underlying box event.
        parts["trades"]["source"] = np.where(htf_now[trade_i] & chart_now[trade_i], "both",
                                                np.where(htf_now[trade_i], "htf", "chart"))
        parts["trades"]["box_entry_i"] = box["box_entry"][trade_i]
        parts["trades"]["bars_after_v9"] = trade_i - box["box_entry"][trade_i]
        # Generate separately per arm.  Shared trade_key plus arm is the controls' composite identity.
        parts["controls"] = pd.concat(
            [study.controls(prepared, group, minutes, facts["ready"]) for _, group in parts["trades"].groupby("arm", sort=False)],
            ignore_index=True,
        )
    candidate_i = np.flatnonzero(baseline)
    if len(candidate_i):
        status_by_arm = {
            arm: dict(zip(group.signal_i.astype(int), group.status))
            for arm, group in parts["statuses"].groupby("arm", sort=False)
        }
        with np.errstate(invalid="ignore", divide="ignore"):
            distance = (close[candidate_i] - rope_high[candidate_i]) / atr[candidate_i]
        parts["decisions"] = pd.DataFrame({
            "symbol": symbol, "timeframe": timeframe, "signal_i": candidate_i,
            "signal_bar_open": index[candidate_i], "box_entry_i": box["box_entry"][candidate_i],
            "source": np.where(htf_now[candidate_i] & chart_now[candidate_i], "both",
                               np.where(htf_now[candidate_i], "htf", "chart")),
            "bars_after_v9": candidate_i - box["box_entry"][candidate_i], "close": close[candidate_i],
            "ropeHigh": rope_high[candidate_i], "support_pass": passed[candidate_i], "distance_atr": distance,
            "box_any_status": [status_by_arm.get("box_any", {}).get(int(i)) for i in candidate_i],
            "box_support_status": [status_by_arm.get("box_support", {}).get(int(i), "rejected_support") for i in candidate_i],
        })
    summary = {"symbol": symbol, "timeframe": timeframe, "htf": htf_name, "bars": len(frame),
               "baseline_candidates": int(baseline.sum()), "support_candidates": int(masks["box_support"].sum()),
               "rejected_support": int((baseline & ~passed).sum())}
    return {**parts, "summary": summary}


def _local_transitive_python(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Static local import closure restricted to evaluation/contracts/data Python sources."""
    queue, seen = [Path(p).resolve() for p in roots], set()
    while queue:
        path = queue.pop()
        if path in seen or path.suffix != ".py" or not path.is_file():
            continue
        seen.add(path)
        tree = ast.parse(path.read_text())
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        for module in modules:
            if not module.startswith(("yoyo.evaluation", "yoyo.contracts", "yoyo.data")):
                continue
            try:
                spec = importlib.util.find_spec(module)
            except (AttributeError, ImportError, ModuleNotFoundError, ValueError):
                # ``from module import Class`` is not a module lookup; the
                # parent module was already queued above.
                continue
            if spec and spec.origin and spec.origin.endswith(".py"):
                queue.append(Path(spec.origin))
    return tuple(sorted(seen))


def _identity(files: dict[str, Path], config: dict) -> tuple[dict, str, dict[str, str]]:
    """Fingerprint code closure, original source metadata, and every requested archive before replay."""
    code_roots = (Path(__file__), Path(source.__file__), Path(v11.__file__), Path(study.__file__), Path(inc.__file__))
    code = _local_transitive_python(code_roots)
    declared = (Path(__file__), TEST, CONFIG, PLAN, source.CONFIG, source.EXP / "PROJECT_PLAN.md", *code)
    input_sha = {symbol: study.digest(path) for symbol, path in sorted(files.items())}
    payload = {
        "schema": "spike-v112-support-v1",
        "declared": {str(path): study.digest(path) for path in declared},
        "code": {str(path): study.digest(path) for path in code},
        "original_source": {"module": str(Path(source.__file__)), "sha256": study.digest(Path(source.__file__)),
                            "config": str(source.CONFIG), "config_sha256": study.digest(source.CONFIG),
                            "pairs": [list(pair) for pair in source.v11.PAIRS]},
        "exchange_info": {"path": str(study.EXCHANGE_INFO), "sha256": study.digest(study.EXCHANGE_INFO)},
        "inputs": input_sha,
        "expected_symbols": config["expected_symbols"],
    }
    return payload, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), input_sha


def _validate_config(config: dict) -> None:
    """Reject a descriptive config that no longer names this frozen replay."""
    expected_pairs = {chart: higher for chart, _, higher, _ in source.v11.PAIRS}
    expected = {
        "experiment_id": EXP.name,
        "arms": list(ARMS),
        "timeframes": expected_pairs,
        "signal_close_start": study.START.isoformat().replace("+00:00", "Z"),
        "signal_close_end_exclusive": inc.DATA_END.isoformat().replace("+00:00", "Z"),
        "split": study.SPLIT.isoformat().replace("+00:00", "Z"),
        "roundtrip_cost": study.ExecutionSpec().round_trip_cost,
        "control_seed": study.CONTROL_SEED,
    }
    for name, value in expected.items():
        if config.get(name) != value:
            raise ValueError(f"frozen config mismatch for {name}: {config.get(name)!r} != {value!r}")


def _manifest_complete(*, explicit_symbols: bool, allow_uncommitted: bool, receipts: list[dict], expected: int,
                       failures: dict) -> bool:
    """A smoke run is never reportable completion, even when every task returns."""
    return not explicit_symbols and not allow_uncommitted and len(receipts) == expected and not failures


def _write_identity_once(output: Path, identity: dict) -> None:
    path = output / "identity.json"
    if path.exists():
        if json.loads(path.read_text()) != identity:
            raise ValueError(f"output identity mismatch: {path}; refuse to overwrite a different replay")
        return
    path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")


def _validate_completion(final: Path, identity_hash: str, input_sha: str) -> dict:
    receipt_path = final / "completion.json"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != "complete" or receipt.get("run_identity") != identity_hash:
        raise ValueError(f"completion identity mismatch: {final}")
    if receipt.get("input_sha256") != input_sha:
        raise ValueError(f"completion input mismatch: {final}")
    files = receipt.get("files")
    if not isinstance(files, dict) or set(files) != {f"{name}.csv.gz" for name in OUTPUT_TABLES}:
        raise ValueError(f"completion output inventory mismatch: {final}")
    for name, digest in files.items():
        path = final / name
        if not path.is_file() or study.digest(path) != digest:
            raise ValueError(f"completion output drift: {path}")
    return receipt


def run_symbol(args) -> dict:
    """Build a single symbol's atomic receipt, or validate an existing receipt on resume."""
    symbol, path_text, meta, output_text, identity_hash, input_sha = args
    output, final = Path(output_text), Path(output_text) / "streams" / symbol
    if (final / "completion.json").is_file():
        return _validate_completion(final, identity_hash, input_sha)
    if final.exists():
        raise ValueError(f"incomplete prior symbol output: {final}")
    started = time.perf_counter()
    staging = output / "streams" / f".{symbol}.staging"
    if staging.exists():
        for old in staging.iterdir():
            if old.is_file():
                old.unlink()
            else:
                raise ValueError(f"unexpected staging directory member: {old}")
    staging.mkdir(parents=True, exist_ok=True)
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    input_path = Path(path_text)
    if study.digest(input_path) != input_sha:
        raise ValueError(f"input changed after identity preflight: {input_path}")
    base = inc.guarded_5m(input_path, earliest)
    frames: dict[str, list[pd.DataFrame]] = {name: [] for name in OUTPUT_TABLES}
    summaries, failures = [], []
    for pair in source.v11.PAIRS:
        try:
            result = run_pair(symbol, base, meta, pair)
        except ValueError as exc:
            failures.append({"timeframe": pair[0], "error": str(exc)})
            continue
        summaries.append(result["summary"])
        for name in OUTPUT_TABLES:
            table = result[name].copy()
            table.insert(0, "timeframe_pair", f"{pair[0]}<-{pair[2]}")
            frames[name].append(table)
    for name, tables in frames.items():
        table = pd.concat(tables, ignore_index=True) if tables else _empty_parts()[name]
        table.to_csv(staging / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash, "input_sha256": input_sha,
               "summaries": summaries, "failures": failures,
               "files": {f"{name}.csv.gz": study.digest(staging / f"{name}.csv.gz") for name in OUTPUT_TABLES},
               "wall_seconds": round(time.perf_counter() - started, 2)}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 8, symbols: list[str] | None = None, allow_uncommitted: bool = False) -> None:
    """Run all 638 archives, or an explicitly named smoke subset, without fetching data."""
    config = json.loads(CONFIG.read_text())
    _validate_config(config)
    if allow_uncommitted and symbols is None:
        raise ValueError("--allow-uncommitted requires explicit --symbols smoke selection")
    files = study.series_files()
    if len(files) != config["expected_symbols"]:
        raise ValueError(f"source coverage changed: {len(files)}")
    keys = list(symbols) if symbols is not None else sorted(files)
    unknown = sorted(set(keys) - set(files))
    if unknown:
        raise ValueError(f"unknown requested symbols: {unknown}")
    for path in files.values():
        if pd.Timestamp(int(pd.read_csv(path, usecols=["ts"]).ts.max()), unit="ms", tz="UTC") >= inc.DATA_END:
            raise ValueError(f"boundary check failed before any computation: {path.name}")
    meta = study.symbol_meta()
    identity, identity_hash, input_sha = _identity({symbol: files[symbol] for symbol in keys}, config)
    if identity["exchange_info"]["sha256"] != study.digest(study.EXCHANGE_INFO):
        raise ValueError("exchange metadata changed during identity preflight")
    dependencies = tuple(Path(path) for path in identity["declared"])
    if not allow_uncommitted and not _committed(dependencies):
        raise ValueError("commit runner, focused test, config, plan and imported code before a reportable replay")
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    _write_identity_once(output, identity)
    started_path = output / "evaluation_started.json"
    if not started_path.exists():
        started_path.write_text(json.dumps({"started_at_unix": time.time(), "historical_window_only": True,
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "run_identity": identity_hash, "symbols": len(keys), "pairs": [f"{p[0]}<-{p[2]}" for p in source.v11.PAIRS]},
            indent=2) + "\n")
    start, receipts = time.perf_counter(), []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        tasks = [pool.submit(run_symbol, (symbol, str(files[symbol]), meta.get(symbol, {"asset": "", "tick": math.nan}),
                                          str(output), identity_hash, input_sha[symbol])) for symbol in keys]
        for number, future in enumerate(as_completed(tasks), 1):
            receipts.append(future.result())
            if number == 1 or number % 50 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys), "elapsed_seconds": round(time.perf_counter() - start, 1)}), flush=True)
    failures = {item["symbol"]: item["failures"] for item in receipts if item["failures"]}
    complete = _manifest_complete(explicit_symbols=symbols is not None, allow_uncommitted=allow_uncommitted,
                                  receipts=receipts, expected=config["expected_symbols"], failures=failures)
    (output / "manifest.json").write_text(json.dumps({"complete": complete, "symbols": len(keys),
        "expected_symbols": config["expected_symbols"], "run_identity": identity_hash, "historical_window_only": True,
        "failures": failures, "wall_seconds": round(time.perf_counter() - start, 1)}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--allow-uncommitted", action="store_true", help="smoke runs only; not reportable")
    args = parser.parse_args()
    run(args.output, workers=args.workers, symbols=args.symbols, allow_uncommitted=args.allow_uncommitted)
