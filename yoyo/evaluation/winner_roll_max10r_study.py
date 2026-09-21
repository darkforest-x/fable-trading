"""Receipt-bound runner for the future-informed V9 ``net_r > 10`` capacity labels.

This is deliberately an outcome-label calculation, not a tradable strategy.
It reads the frozen V9 trade ledger and authenticated cached OHLC bars only.
Original trade ordinals are never used as cache positions: timestamps locate the
bars and the independently recorded ordinal distance is then checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as frozen
from yoyo.evaluation.winner_roll_max10r import optimize_path


EXP = Path("experiments/active/exp-winner-roll-max10r-20260921-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
STATS = Path("experiments/active/exp-spike-v9-full-backtest-20260915-v1/statistics/full_v1")
STATS_RECEIPT = STATS / "statistics_receipt.json"
TRADES = STATS / "trades.csv.gz"
TEST = Path("tests/evaluation/test_winner_roll_max10r_study.py")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
ARMS = ("none", "one", "two")


def digest(path: Path) -> str:
    """Hash an input or artifact without trusting its filename."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"


def _strict_bool(value: object) -> bool:
    """Parse receipt CSV booleans without treating arbitrary strings as true."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    raise ValueError(f"invalid censored flag: {value!r}")


def select_winners(table: pd.DataFrame) -> pd.DataFrame:
    """Apply only the owner-approved strict V9 long outcome predicate."""
    required = {"arm", "side", "censored", "net_r", "stream_key", "event_key", "entry_time",
                "exit_time", "entry_price", "entry_i", "exit_i", "venue", "symbol", "asset",
                "timeframe_min"}
    missing = required - set(table.columns)
    if missing:
        raise ValueError(f"trade ledger columns missing: {sorted(missing)}")
    censored = table.censored.map(_strict_bool)
    net_r = pd.to_numeric(table.net_r, errors="raise")
    selected = table.loc[table.arm.eq("v9") & table.side.eq(1) & ~censored & net_r.gt(10)].copy()
    selected["entry_time"] = pd.to_datetime(selected.entry_time, utc=True, errors="raise")
    selected["exit_time"] = pd.to_datetime(selected.exit_time, utc=True, errors="raise")
    if selected.event_key.duplicated().any():
        raise ValueError("selected V9 event keys are not unique")
    if (selected.exit_time <= selected.entry_time).any():
        raise ValueError("selected V9 trade has nonpositive clock duration")
    return selected.sort_values(["stream_key", "entry_time", "event_key"], kind="mergesort").reset_index(drop=True)


def _verified_stats_receipt() -> dict[str, Any]:
    """Verify every original statistics artifact named by its own receipt."""
    receipt = json.loads(STATS_RECEIPT.read_text())
    files = receipt.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("statistics receipt has no file-hash mapping")
    for name, expected in files.items():
        path = STATS / name
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"statistics receipt mismatch: {name}")
    return receipt


def _receipt_hashes_for(selected: pd.DataFrame, receipt: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Bind selected streams to the original V9 source-receipt SHA mapping."""
    expected = {str(row["key"]): str(row["receipt_sha256"])
                for row in receipt.get("source_receipts", [])}
    keys = sorted(selected.stream_key.unique())
    absent = set(keys) - set(expected)
    if absent:
        raise ValueError(f"selected streams absent from statistics receipt: {sorted(absent)[:3]}")
    actual: dict[str, dict[str, str]] = {}
    for key in keys:
        v9_completion = (STATS.parent.parent / "results" / "full_v1" / "streams" / key / "completion.json")
        if not v9_completion.is_file() or digest(v9_completion) != expected[key]:
            raise ValueError(f"V9 stream completion receipt mismatch: {key}")
        completion = json.loads(v9_completion.read_text())
        cache_receipt = frozen.SOURCE_STREAMS / key / "control_cache.receipt.json"
        cache_path = frozen.SOURCE_STREAMS / key / "control_cache.pkl.gz"
        if not (cache_receipt.is_file() and cache_path.is_file()):
            raise ValueError(f"frozen stream cache missing: {key}")
        cache = json.loads(cache_receipt.read_text())
        cache_sha = digest(cache_path)
        if (cache_sha != str(cache.get("cache_sha256")) or
                cache_sha != str(completion.get("cache_sha256"))):
            raise ValueError(f"frozen cache mismatch: {key}")
        actual[key] = {"v9_completion_sha256": digest(v9_completion),
                       "raw_completion_sha256": str(completion.get("raw_completion_sha256")),
                       "control_cache_receipt_sha256": digest(cache_receipt), "cache_sha256": cache_sha}
    return actual


def verified_inputs() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read selected rows only after the upstream statistics and streams authenticate."""
    receipt = _verified_stats_receipt()
    selected = select_winners(pd.read_csv(TRADES))
    source_receipts = _receipt_hashes_for(selected, receipt)
    return selected, {
        "statistics_receipt_sha256": digest(STATS_RECEIPT),
        "statistics_files": dict(receipt["files"]),
        "stream_receipts": source_receipts,
    }


def _row_value(row: Any, name: str) -> Any:
    return row[name] if isinstance(row, (pd.Series, dict)) else getattr(row, name)


def frame_for_trade(context: frozen.StreamContext, row: Any) -> tuple[pd.DataFrame, int, int]:
    """Locate a V9 trade by clock, assert ordinal parity, and return [entry, exit)."""
    identity = context.identity
    for name in ("venue", "symbol", "asset"):
        if str(_row_value(row, name)) != str(identity[name]):
            raise ValueError(f"stream identity mismatch ({name}): {context.key}")
    if int(_row_value(row, "timeframe_min")) != int(context.minutes):
        raise ValueError(f"stream identity mismatch (timeframe): {context.key}")
    if str(_row_value(row, "stream_key")) != context.key:
        raise ValueError("stream key mismatch")
    entry = pd.Timestamp(_row_value(row, "entry_time"))
    exit_ = pd.Timestamp(_row_value(row, "exit_time"))
    if entry.tzinfo is None or exit_.tzinfo is None:
        raise ValueError("original trade clock must be UTC-aware")
    index = context.cache["bars"].index
    if not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError(f"invalid frozen cache clock: {context.key}")
    try:
        entry_i, exit_i = int(index.get_loc(entry)), int(index.get_loc(exit_))
    except KeyError as error:
        raise ValueError(f"original trade clock missing from cache: {context.key}") from error
    source_distance = int(_row_value(row, "exit_i")) - int(_row_value(row, "entry_i"))
    if exit_i <= entry_i or exit_i - entry_i != source_distance:
        raise ValueError(f"original/cache ordinal distance mismatch: {context.key}")
    expected = pd.date_range(entry, exit_, freq=pd.Timedelta(minutes=context.minutes), tz="UTC")
    if not index[entry_i:exit_i + 1].equals(expected):
        raise ValueError(f"incomplete frozen cache clock: {context.key}")
    bars = context.cache["bars"]
    cached_entry = float(bars.open.iloc[entry_i])
    tick = float(context.cache["tick"])
    if not np.isclose(cached_entry, float(_row_value(row, "entry_price")), rtol=1e-12,
                      atol=max(tick * 1e-6, 1e-12)):
        raise ValueError(f"original entry price mismatch: {context.key}")
    gap = context.cache["data_gap"].iloc[entry_i:exit_i]
    if gap.isna().any() or gap.astype(bool).any():
        raise ValueError(f"data gap inside permitted trade horizon: {context.key}")
    # Copy makes the no-source-mutation boundary explicit for the pure engine.
    return bars.iloc[entry_i:exit_i][["open", "high", "low", "close"]].copy(), entry_i, exit_i


def _records_for_stream(key: str, rows: list[dict[str, Any]], cfg: dict[str, Any], output: str,
                        identity_hash: str) -> dict[str, Any]:
    """Run one authenticated cache once and write independently receipted artifacts."""
    began = perf_counter()
    folder = Path(output) / "streams" / key
    if folder.exists():
        raise ValueError(f"refusing to overwrite stream output: {key}")
    trades: list[dict[str, Any]] = []
    legs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    context: frozen.StreamContext | None = None
    context_error: Exception | None = None
    try:
        context = frozen.load_verified_stream(frozen.SOURCE_STREAMS / key)
    except Exception as error:
        context_error = error
    for payload in rows:
        try:
            if context_error is not None:
                raise context_error
            assert context is not None
            path, cache_entry_i, cache_exit_i = frame_for_trade(context, payload)
            result = optimize_path(path, capital=float(cfg["capital"]), leverage=float(cfg["leverage"]),
                                   fee=float(cfg["fee_each_side"]), maintenance_rate=float(cfg["maintenance_rate"]),
                                   liquidation_fee=float(cfg["liquidation_fee_reserve_rate"]),
                                   buffer=float(cfg["maintenance_buffer_usd"]))
            common = {"event_key": payload["event_key"], "stream_key": payload["stream_key"],
                      "venue": payload["venue"], "symbol": payload["symbol"], "asset": payload["asset"],
                      "timeframe_min": payload["timeframe_min"], "original_entry_time": payload["entry_time"],
                      "original_exit_time": payload["exit_time"], "original_entry_price": payload["entry_price"],
                      "original_entry_i": payload["entry_i"], "original_exit_i": payload["exit_i"],
                      "original_net_r": payload["net_r"]}
            common["period"] = "earlier" if pd.Timestamp(payload["entry_time"]) < SPLIT else "later"
            common["cache_entry_i"], common["cache_exit_i"] = cache_entry_i, cache_exit_i
            for arm in ARMS:
                answer = result[arm]
                fields = {k: v for k, v in answer.items() if k not in {"legs", "exit_i", "exit_price"}}
                trades.append({**common, "arm": arm, **fields, "optimizer_exit_i": answer["exit_i"],
                               "optimizer_exit_price": answer["exit_price"],
                               "optimizer_exit_bar_open": path.index[int(answer["exit_i"])].isoformat()})
                for leg_no, leg in enumerate(answer["legs"]):
                    legs.append({**common, "arm": arm, "leg_no": leg_no, **leg,
                                 "time_utc": path.index[int(leg["bar_i"])].isoformat()})
        except Exception as error:  # Failure is an output, never a silent omitted winner.
            failures.append({"event_key": payload.get("event_key"), "stream_key": key,
                             "error_type": type(error).__name__, "error": str(error)})
    folder.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    for name, records in (("trades.csv.gz", trades), ("legs.csv.gz", legs), ("failures.csv.gz", failures)):
        path = folder / name
        pd.DataFrame(records).to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        files[name] = digest(path)
    completion = {"status": "complete", "stream_key": key, "identity_hash": identity_hash,
                  "source_receipt_sha256": (None if context is None else digest(context.path / "control_cache.receipt.json")),
                  "cache_sha256": (None if context is None else str(context.receipt["cache_sha256"])), "input_trades": len(rows),
                  "result_rows": len(trades), "failure_rows": len(failures), "seconds": perf_counter() - began,
                  "files": files}
    (folder / "completion.json").write_text(_json(completion))
    return completion


def _verify_stream_completion(folder: Path, identity_hash: str) -> dict[str, Any]:
    receipt = json.loads((folder / "completion.json").read_text())
    if receipt.get("status") != "complete" or receipt.get("identity_hash") != identity_hash:
        raise ValueError(f"incompatible stream completion: {folder.name}")
    for name, expected in receipt.get("files", {}).items():
        if digest(folder / name) != expected:
            raise ValueError(f"stream artifact changed: {folder.name}/{name}")
    return receipt


def _per_asset(trades: pd.DataFrame, selected: pd.DataFrame, failures: pd.DataFrame) -> pd.DataFrame:
    """Keep the best complete two-arm event, while exposing incomplete assets."""
    counts = selected.groupby("asset", as_index=False).agg(selected_trades=("event_key", "size"))
    if failures.empty:
        failed = pd.DataFrame({"asset": pd.Series(dtype=str), "failed_trades": pd.Series(dtype=int)})
    else:
        failed = (failures[["event_key"]].merge(selected[["event_key", "asset"]], on="event_key",
                 validate="one_to_one").groupby("asset", as_index=False).agg(failed_trades=("event_key", "size")))
    coverage = counts.merge(failed, on="asset", how="left")
    coverage["failed_trades"] = coverage.failed_trades.fillna(0).astype(int)
    coverage["asset_complete"] = coverage.failed_trades.eq(0)
    if trades.empty:
        # _aggregate has already established that every selected event failed.
        result = coverage.loc[~coverage.asset_complete].copy()
        result["event_key"] = pd.NA
        result["arm"] = pd.NA
        result["status"] = "incomplete_failure"
        return result.sort_values("asset", kind="mergesort").reset_index(drop=True)
    two = trades.loc[trades.arm.eq("two") & trades.status.eq("conditional_optimal")].copy()
    winner = (two.merge(coverage.loc[coverage.asset_complete, ["asset"]], on="asset", validate="many_to_one")
              .sort_values(["asset", "final_balance", "event_key"], ascending=[True, False, True],
                           kind="mergesort").drop_duplicates("asset"))
    chosen = winner[["asset", "event_key"]]
    complete = trades.merge(chosen, on=["asset", "event_key"], how="inner", validate="many_to_one")
    complete = complete.merge(coverage, on="asset", validate="many_to_one")
    incomplete = coverage.loc[~coverage.asset_complete].copy()
    incomplete["event_key"] = pd.NA
    incomplete["arm"] = pd.NA
    incomplete["status"] = "incomplete_failure"
    for column in complete.columns:
        if column not in incomplete:
            incomplete[column] = pd.NA
    result = complete if incomplete.empty else pd.concat([complete, incomplete[complete.columns]], ignore_index=True)
    return result.sort_values(["asset", "event_key", "arm"], kind="mergesort", na_position="last").reset_index(drop=True)


def _aggregate(output: Path, identity: dict[str, Any], selected: pd.DataFrame) -> dict[str, Any]:
    """Materialize top-level tables only from verified per-stream completions."""
    parts, leg_parts, fail_parts, completions = [], [], [], []
    for folder in sorted((output / "streams").glob("*")):
        if not folder.is_dir():
            continue
        completions.append(_verify_stream_completion(folder, identity["identity_hash"]))
        for name, sink in (("trades.csv.gz", parts), ("legs.csv.gz", leg_parts), ("failures.csv.gz", fail_parts)):
            path = folder / name
            try:
                sink.append(pd.read_csv(path))
            except pd.errors.EmptyDataError:
                pass
    trades = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    legs = pd.concat(leg_parts, ignore_index=True) if leg_parts else pd.DataFrame()
    failures = pd.concat(fail_parts, ignore_index=True) if fail_parts else pd.DataFrame()
    expected_events = set(selected.event_key)
    seen = set(trades.event_key) if len(trades) else set()
    failed = set(failures.event_key) if len(failures) else set()
    if seen | failed != expected_events or seen & failed:
        raise ValueError("stream outputs do not account for every selected event exactly once")
    for name, table in (("trades.csv", trades), ("per_asset.csv", _per_asset(trades, selected, failures)),
                        ("legs.csv", legs), ("failures.csv", failures)):
        table.to_csv(output / name, index=False)
    scope = {"selected_trades": int(len(selected)), "selected_assets": int(selected.asset.nunique()),
             "selected_streams": int(selected.stream_key.nunique()), "completed_streams": len(completions),
             "successful_trades": int(len(seen)), "failed_trades": int(len(failed)),
             "earlier": int((selected.entry_time < SPLIT).sum()), "later": int((selected.entry_time >= SPLIT).sum())}
    summary = {"experiment_id": identity["config"]["experiment_id"], "conditional_history_only": True,
               "real_exchange_solvency": "unknown", "scope": scope, "identity": identity,
               "files": {name: digest(output / name) for name in ("trades.csv", "per_asset.csv", "legs.csv", "failures.csv")}}
    (output / "summary.json").write_text(_json(summary))
    return summary


def _committed(paths: tuple[Path, ...]) -> bool:
    """Require tracked sources to byte-match HEAD before any historical run."""
    for original in paths:
        path = Path(original).resolve()
        try:
            relative = path.relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            return False
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", relative], capture_output=True).returncode == 0
        if not tracked:
            return False
        head = subprocess.run(["git", "show", f"HEAD:{relative}"], capture_output=True).stdout
        current = path.read_bytes() if path.is_file() else b""
        if not head or hashlib.sha256(head).digest() != hashlib.sha256(current).digest():
            return False
    return True


def source_receipt() -> dict[str, Any]:
    """Freeze executable source identities after enforcing the pre-run commit gate."""
    declared = (Path(__file__), Path("yoyo/evaluation/winner_roll_max10r.py"),
                Path("tests/evaluation/test_winner_roll_max10r.py"), CONFIG, PLAN, TEST,
                Path("yoyo/evaluation/spike_exit_policy_study.py"))
    if not _committed(declared):
        raise ValueError("commit unchanged builder, engine, tests, config and protocol before market replay")
    return {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "declared": {str(path): digest(path) for path in declared}}


def run(output: Path, *, workers: int = 1, resume: bool = False) -> dict[str, Any]:
    """Run the selected 367 labels; a real run is blocked until its sources commit."""
    if workers not in (1, 2):
        raise ValueError("workers must be 1 or 2")
    code = source_receipt()
    cfg = json.loads(CONFIG.read_text())
    selected, input_receipt = verified_inputs()
    if len(selected) != int(cfg["expected_trades"]) or selected.asset.nunique() != int(cfg["expected_assets"]):
        raise ValueError("frozen winner selection count changed")
    identity = {**code, "config": cfg, "config_sha256": digest(CONFIG), "inputs": input_receipt,
                "selection_counts": {"trades": len(selected), "assets": int(selected.asset.nunique()),
                                     "streams": int(selected.stream_key.nunique())}}
    identity["identity_hash"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(output)
    if output.exists():
        if not resume or not (output / "identity.json").is_file():
            raise ValueError("refusing to overwrite output; use a new directory")
        if json.loads((output / "identity.json").read_text()) != identity:
            raise ValueError("resume identity differs; use a new output directory")
        summary_path = output / "summary.json"
        if summary_path.is_file():
            prior_summary = json.loads(summary_path.read_text())
            for name, expected in prior_summary.get("files", {}).items():
                if digest(output / name) != expected:
                    raise ValueError("resume artifact differs from its receipt; use a new output directory")
    else:
        output.mkdir(parents=True)
        (output / "identity.json").write_text(_json(identity))
    groups = [(key, group.to_dict("records")) for key, group in selected.groupby("stream_key", sort=True)]
    pending = []
    for key, rows in groups:
        folder = output / "streams" / key
        if folder.exists():
            _verify_stream_completion(folder, identity["identity_hash"])
        else:
            pending.append((key, rows, cfg, str(output), identity["identity_hash"]))
    completions = []
    if workers == 1:
        for payload in pending:
            completions.append(_records_for_stream(*payload))
            if len(completions) % 20 == 0 or len(completions) == len(pending):
                print(json.dumps({"completed_now": len(completions), "remaining": len(pending)}), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_records_for_stream, *payload) for payload in pending]
            for future in as_completed(futures):
                completions.append(future.result())
                if len(completions) % 20 == 0 or len(completions) == len(pending):
                    print(json.dumps({"completed_now": len(completions), "remaining": len(pending)}), flush=True)
    # Re-hash receipts and all named upstream statistic files after the long read.
    _, after = verified_inputs()
    if after != input_receipt:
        raise ValueError("frozen input mutated during run")
    for path, expected in code["declared"].items():
        if digest(Path(path)) != expected:
            raise ValueError("executable source mutated during run")
    return _aggregate(output, identity, selected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EXP / "run_v1")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    options = parser.parse_args()
    result = run(options.output, workers=options.workers, resume=options.resume)
    print(json.dumps({"output": str(options.output), "scope": result["scope"]}, ensure_ascii=False))
