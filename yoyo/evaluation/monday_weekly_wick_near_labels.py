"""Re-label frozen Monday weekly-wick entries to their near weekly edge.

This consumes the already frozen entered rows from the score3r census. It does
not rescan weekly events: each eligible symbol tape is loaded once, then passed
to the same native first-hit, minute-refinement, and VectorBT fee adapter used
for the original 3R labels.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_weekly_wick_census3r as census

ROOT = Path(__file__).resolve().parents[2]
PARENT_EXP = ROOT / "experiments/active/exp-binance-monday-wick-score3r-20261116-v1"
EXP = ROOT / "experiments/active/exp-binance-monday-wick-near-netr-20261116-v1"
DATA_CACHE = ROOT / "data/research/binance-monday-wick-near-netr-20261116-v1"
EXPERIMENT_ID = "exp-binance-monday-wick-near-netr-20261116-v1"
BUILD_VERSION = "monday-weekly-wick-near-labels-v1"
CACHE_SCHEMA_VERSION = 1
FROZEN_INPUT = PARENT_EXP / "first_breaches.csv.gz"
OUTPUT_COLUMNS = census.LABEL_COLUMNS


def _event_target(event: Mapping[str, Any]) -> float:
    """Use the Monday range edge on the event's side: lower long, upper short."""
    side = int(event["side"])
    side_name = str(event["side_name"])
    if side == 1 and side_name == "lower":
        return float(event["week_low"])
    if side == -1 and side_name == "upper":
        return float(event["week_high"])
    raise ValueError(f"Unexpected side/side_name pair for {event.get('event_id')}: {side}/{side_name}")


def prepare_entered_events(frame: pd.DataFrame) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Group frozen entered rows by symbol and count rows excluded from labels."""
    required = {
        "event_id", "symbol", "status", "side", "side_name", "week_low", "week_high",
        "entry_i", "entry_ms", "entry", "stop", "risk_frac", "week", "tf", "bar_ms",
        "close_ms", "first_breach_ms", "anchor_start_ms", "anchor_ready_ms", "expires_ms",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Frozen first_breaches input is missing columns: {sorted(missing)}")
    if frame.event_id.duplicated().any():
        raise ValueError("Frozen first_breaches input has duplicate event_id values")

    groups: dict[str, list[dict[str, Any]]] = {}
    counts = {
        "frozen_event_rows": int(len(frame)),
        "entered_event_rows": int(frame.status.eq("entered").sum()),
        "skipped_not_entered_rows": int(frame.status.ne("entered").sum()),
        "skipped_invalid_target_rows": 0,
        "skipped_target_at_or_behind_entry_rows": 0,
    }
    entered = frame.loc[frame.status.eq("entered")]
    for event in entered.to_dict(orient="records"):
        entry = float(event["entry"])
        stop = float(event["stop"])
        side = int(event["side"])
        if (
            side not in (-1, 1)
            or not np.isfinite([entry, stop]).all()
            or entry <= 0
            or stop <= 0
            or side * (entry - stop) <= 0
        ):
            raise ValueError(f"Invalid frozen entered row: {event['event_id']}")
        target = _event_target(event)
        if not np.isfinite(target) or target <= 0:
            counts["skipped_invalid_target_rows"] += 1
            continue
        if side * (target - entry) <= 0:
            counts["skipped_target_at_or_behind_entry_rows"] += 1
            continue
        event["target_price"] = target
        groups.setdefault(str(event["symbol"]), []).append(event)
    counts["eligible_entered_rows"] = sum(map(len, groups.values()))
    counts["skipped_rows"] = counts["frozen_event_rows"] - counts["eligible_entered_rows"]
    return groups, counts


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


def _atomic_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    frame.reindex(columns=OUTPUT_COLUMNS).to_csv(
        tmp, index=False, compression={"method": "gzip", "compresslevel": 1}
    )
    os.replace(tmp, path)


def _input_signature() -> dict[str, Any]:
    stat = FROZEN_INPUT.stat()
    return {
        "path": str(FROZEN_INPUT.relative_to(ROOT)),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _symbol_cache_paths(symbol: str) -> tuple[Path, Path]:
    root = DATA_CACHE / "symbols" / str(symbol)
    return root / "outcomes_near.csv.gz", root / "receipt.json"


def _cached_symbol_frame(
    symbol: str,
    expected_ids: set[str],
    expected_rows: list[dict[str, Any]],
    signature: Mapping[str, Any],
) -> pd.DataFrame | None:
    data_path, receipt_path = _symbol_cache_paths(symbol)
    if not data_path.is_file() or not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text())
        if any(receipt.get(key) != value for key, value in {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "build_version": BUILD_VERSION,
            "experiment": EXPERIMENT_ID,
            "symbol": str(symbol),
            "input_signature": dict(signature),
            "eligible_rows": len(expected_ids),
        }.items()):
            return None
        frame = pd.read_csv(data_path, float_precision="round_trip")
        if (
            tuple(frame.columns) != OUTPUT_COLUMNS
            or frame.event_id.duplicated().any()
            or set(frame.event_id.astype(str)) != expected_ids
        ):
            return None
        expected = {str(row["event_id"]): row for row in expected_rows}
        by_id = frame.set_index("event_id")
        for event_id, event in expected.items():
            cached = by_id.loc[event_id]
            if not (
                np.isclose(float(cached.target_price), float(event["target_price"]), rtol=1e-12, atol=0)
                and np.isclose(float(cached.entry), float(event["entry"]), rtol=1e-12, atol=0)
                and np.isclose(float(cached.stop), float(event["stop"]), rtol=1e-12, atol=0)
            ):
                return None
        return frame
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_symbol_cache(
    symbol: str, frame: pd.DataFrame, signature: Mapping[str, Any]
) -> None:
    data_path, receipt_path = _symbol_cache_paths(symbol)
    _atomic_frame(data_path, frame)
    _atomic_json(receipt_path, {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "build_version": BUILD_VERSION,
        "experiment": EXPERIMENT_ID,
        "symbol": str(symbol),
        "input_signature": dict(signature),
        "eligible_rows": int(len(frame)),
        "outcome_counts": frame.outcome.value_counts().to_dict(),
    })


def _run_symbol(
    symbol: str,
    events: list[dict[str, Any]],
    signature: Mapping[str, Any],
    *,
    tape_loader: Callable[[str], np.ndarray] = census.load_frozen_tape,
    outcome_runner: Callable[..., list[dict[str, Any]]] = census.run_3r_outcomes,
    minute_lookup_fn: Callable[[str, np.ndarray], np.ndarray | None] | None = census.minute_lookup,
) -> tuple[pd.DataFrame, str]:
    expected_ids = {str(row["event_id"]) for row in events}
    if len(expected_ids) != len(events):
        raise ValueError(f"Frozen events contain duplicate IDs for {symbol}")
    cached = _cached_symbol_frame(str(symbol), expected_ids, events, signature)
    if cached is not None:
        return cached, "cache"

    raw = tape_loader(str(symbol))
    target_overrides = {str(row["event_id"]): float(row["target_price"]) for row in events}
    labels = outcome_runner(
        raw, events, minute_lookup_fn=minute_lookup_fn,
        target_price_overrides=target_overrides,
        target_label="near_week_edge",
    )
    frame = pd.DataFrame(labels).reindex(columns=OUTPUT_COLUMNS)
    if frame.event_id.duplicated().any() or set(frame.event_id.astype(str)) != expected_ids:
        raise RuntimeError(f"Near target engine did not return exactly one label per eligible event for {symbol}")
    expected = {str(row["event_id"]): row for row in events}
    for row in frame.to_dict(orient="records"):
        source = expected[str(row["event_id"])]
        if not (
            row["target"] == "near_week_edge"
            and row["label_id"] == f"{row['event_id']}/near_week_edge"
            and
            np.isclose(float(row["target_price"]), float(source["target_price"]), rtol=1e-12, atol=0)
            and np.isclose(float(row["entry"]), float(source["entry"]), rtol=1e-12, atol=0)
            and np.isclose(float(row["stop"]), float(source["stop"]), rtol=1e-12, atol=0)
        ):
            raise RuntimeError(f"Near target engine changed frozen entry/stop/target for {row['event_id']}")
    _write_symbol_cache(str(symbol), frame, signature)
    return frame, "computed"


def _load_frozen_input() -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if not FROZEN_INPUT.is_file():
        raise FileNotFoundError(f"Frozen entered-event input is missing: {FROZEN_INPUT}")
    receipt_path = PARENT_EXP / "receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Frozen parent receipt is missing: {receipt_path}")
    parent_receipt = json.loads(receipt_path.read_text())
    if parent_receipt.get("experiment") != "exp-binance-monday-wick-score3r-20261116-v1":
        raise ValueError("Frozen input receipt identifies an unexpected source experiment")
    events = pd.read_csv(FROZEN_INPUT, float_precision="round_trip")
    if len(events) != int(parent_receipt["event_rows"]):
        raise ValueError("Frozen first_breaches rows differ from the parent receipt")
    entered = int(events.status.eq("entered").sum())
    if entered != int(parent_receipt["entered_rows"]):
        raise ValueError("Frozen entered rows differ from the parent receipt")
    return events, parent_receipt, _input_signature()


def _write_run_outputs(
    labels: pd.DataFrame,
    counts: Mapping[str, int],
    *,
    parent_receipt: Mapping[str, Any],
    signature: Mapping[str, Any],
    workers: int,
    elapsed_seconds: float,
    cache_hits: int,
    symbol_count: int,
) -> dict[str, Any]:
    EXP.mkdir(parents=True, exist_ok=True)
    labels = labels.reindex(columns=OUTPUT_COLUMNS).sort_values("event_id", kind="mergesort")
    if labels.event_id.duplicated().any():
        raise AssertionError("Near labels contain duplicate event_id values")
    _atomic_frame(EXP / "outcomes_near.csv.gz", labels)
    config = {
        "experiment": EXPERIMENT_ID,
        "build_version": BUILD_VERSION,
        "frozen_source_experiment": parent_receipt["experiment"],
        "frozen_input": dict(signature),
        "entry_and_stop": "Frozen entered rows exactly as supplied: next H1 open entry; signal tip extended outward by 1% stop.",
        "target": "For lower-side long events use frozen week_low; for upper-side short events use frozen week_high. Rows at or behind entry in trade direction are skipped.",
        "execution": "Existing monday_wick_r_engine.first_hit and month_firstweek_12h_wick_exits minute resolver; H1 path includes entry hour, gaps and tape end censor, lifecycle/delisting from frozen tape.",
        "fees": {
            "entry_taker": census.ENTRY_TAKER_FEE,
            "stop_exit_taker": census.EXIT_TAKER_FEE,
            "target_exit_maker": census.EXIT_MAKER_FEE,
        },
        "slippage": 0,
        "funding": 0,
        "output_columns": list(OUTPUT_COLUMNS),
    }
    _atomic_json(EXP / "labels_config.json", config)
    receipt = {
        "experiment": EXPERIMENT_ID,
        "build_version": BUILD_VERSION,
        "frozen_source_experiment": parent_receipt["experiment"],
        "frozen_input": dict(signature),
        **{key: int(value) for key, value in counts.items()},
        "label_rows": int(len(labels)),
        "outcome_counts": labels.outcome.value_counts().to_dict(),
        "resolution_counts": labels.resolution.value_counts().to_dict(),
        "cache_symbol_hits": int(cache_hits),
        "cache_symbol_misses": int(symbol_count - cache_hits),
        "symbols_with_eligible_events": int(symbol_count),
        "workers": int(workers),
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "fee_rates": config["fees"],
    }
    _atomic_json(EXP / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8, help="parallel symbol workers (default: 8)")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")

    events_frame, parent_receipt, signature = _load_frozen_input()
    groups, counts = prepare_entered_events(events_frame)
    if counts["entered_event_rows"] != int(parent_receipt["entered_rows"]):
        raise AssertionError("Prepared entered count differs from frozen parent receipt")
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    frames: list[pd.DataFrame] = []
    cache_hits = 0
    symbols = sorted(groups)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_run_symbol, symbol, groups[symbol], signature): symbol
            for symbol in symbols
        }
        for completed, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            frame, source = future.result()
            frames.append(frame)
            cache_hits += int(source == "cache")
            if completed % 25 == 0 or completed == len(symbols):
                elapsed = time.monotonic() - start
                eta = elapsed / completed * (len(symbols) - completed) if completed else 0.0
                print(
                    f"{completed}/{len(symbols)} symbols; {sum(map(len, frames))} labels; "
                    f"cache {cache_hits}; elapsed {elapsed / 60:.1f}m; ETA {eta / 60:.1f}m",
                    flush=True,
                )

    labels = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OUTPUT_COLUMNS)
    expected_ids = {
        str(event["event_id"])
        for symbol_events in groups.values()
        for event in symbol_events
    }
    if set(labels.event_id.astype(str)) != expected_ids:
        raise AssertionError("Near label event IDs do not equal the eligible frozen entered set")
    receipt = _write_run_outputs(
        labels, counts, parent_receipt=parent_receipt, signature=signature,
        workers=args.workers, elapsed_seconds=time.monotonic() - start,
        cache_hits=cache_hits, symbol_count=len(symbols),
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
