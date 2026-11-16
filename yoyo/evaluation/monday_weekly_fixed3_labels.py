"""Re-label the frozen Monday weekly-wick entries with a 3% stop.

The eligible event set and near weekly-edge targets are fixed by the previous
near-label experiment. Only the stop distance changes: ``entry * (1 - side *
0.03)``. Resolution still uses the existing VectorBT fee adapter, native
first-hit kernel, minute lookup, and frozen hourly tapes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_weekly_wick_census3r as census
from yoyo.evaluation import monday_weekly_wick_near_labels as near

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-binance-monday-wick-fixed3-near-20261116-v1"
DATA_CACHE = ROOT / "data/research/binance-monday-wick-fixed3-near-20261116-v1"
EXPERIMENT_ID = "exp-binance-monday-wick-fixed3-near-20261116-v1"
BUILD_VERSION = "monday-weekly-fixed3-near-labels-v1"
CACHE_SCHEMA_VERSION = 1
STOP_FRAC = 0.03
FROZEN_INPUT = near.FROZEN_INPUT
REFERENCE_LABELS = near.EXP / "outcomes_near.csv.gz"
OUTPUT_COLUMNS = census.LABEL_COLUMNS


def prepare_fixed3_events(frame: pd.DataFrame) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Keep the previous near-target eligible rows and replace their stop risk.

    The old frozen stop and its ``risk_frac`` are used only to validate source
    entry integrity in ``prepare_entered_events``. They are then discarded;
    each event receives a new 3% stop and a risk fraction derived from it.
    """
    groups, counts = near.prepare_entered_events(frame)
    for symbol_events in groups.values():
        for event in symbol_events:
            entry = float(event["entry"])
            side = int(event["side"])
            stop = entry * (1.0 - side * STOP_FRAC)
            if not np.isfinite(stop) or stop <= 0 or side * (entry - stop) <= 0:
                raise ValueError(f"Invalid fixed 3% stop for {event['event_id']}")
            event.pop("risk_abs", None)
            event["stop"] = stop
            event["risk_frac"] = abs(entry - stop) / entry
    return groups, counts


def _stat_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.relative_to(ROOT)),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _input_signature() -> dict[str, Any]:
    return _stat_signature(FROZEN_INPUT)


def _reference_signature() -> dict[str, Any]:
    return _stat_signature(REFERENCE_LABELS)


def _symbol_cache_paths(symbol: str) -> tuple[Path, Path]:
    root = DATA_CACHE / "symbols" / str(symbol)
    return root / "outcomes_fixed3_near.csv.gz", root / "cache_receipt.json"


def _cached_symbol_frame(
    symbol: str,
    expected_ids: set[str],
    expected_rows: list[dict[str, Any]],
    signature: Mapping[str, Any],
    reference_signature: Mapping[str, Any],
) -> pd.DataFrame | None:
    data_path, receipt_path = _symbol_cache_paths(symbol)
    if not data_path.is_file() or not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text())
        expected_receipt = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "build_version": BUILD_VERSION,
            "experiment": EXPERIMENT_ID,
            "symbol": str(symbol),
            "input_signature": dict(signature),
            "reference_signature": dict(reference_signature),
            "eligible_rows": len(expected_ids),
        }
        if any(receipt.get(key) != value for key, value in expected_receipt.items()):
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
                str(cached.target) == "near_week_edge"
                and np.isclose(float(cached.target_price), float(event["target_price"]), rtol=1e-12, atol=0)
                and np.isclose(float(cached.entry), float(event["entry"]), rtol=1e-12, atol=0)
                and np.isclose(float(cached.stop), float(event["stop"]), rtol=1e-12, atol=0)
                and np.isclose(float(cached.risk_abs), abs(float(event["entry"]) - float(event["stop"])), rtol=1e-12, atol=1e-14)
                and np.isclose(float(cached.risk_frac), float(event["risk_frac"]), rtol=1e-12, atol=1e-14)
            ):
                return None
        return frame
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_symbol_cache(
    symbol: str,
    frame: pd.DataFrame,
    signature: Mapping[str, Any],
    reference_signature: Mapping[str, Any],
) -> None:
    data_path, receipt_path = _symbol_cache_paths(symbol)
    near._atomic_frame(data_path, frame)
    near._atomic_json(receipt_path, {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "build_version": BUILD_VERSION,
        "experiment": EXPERIMENT_ID,
        "symbol": str(symbol),
        "input_signature": dict(signature),
        "reference_signature": dict(reference_signature),
        "eligible_rows": int(len(frame)),
        "outcome_counts": frame.outcome.value_counts().to_dict(),
    })


def _run_symbol(
    symbol: str,
    events: list[dict[str, Any]],
    signature: Mapping[str, Any],
    reference_signature: Mapping[str, Any],
    *,
    tape_loader: Callable[[str], np.ndarray] = census.load_frozen_tape,
    outcome_runner: Callable[..., list[dict[str, Any]]] = census.run_3r_outcomes,
    minute_lookup_fn: Callable[[str, np.ndarray], np.ndarray | None] | None = census.minute_lookup,
) -> tuple[pd.DataFrame, str]:
    expected_ids = {str(row["event_id"]) for row in events}
    if len(expected_ids) != len(events):
        raise ValueError(f"Frozen events contain duplicate IDs for {symbol}")
    cached = _cached_symbol_frame(str(symbol), expected_ids, events, signature, reference_signature)
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
        raise RuntimeError(f"Fixed3 engine did not return one label per eligible event for {symbol}")
    expected = {str(row["event_id"]): row for row in events}
    for row in frame.to_dict(orient="records"):
        source = expected[str(row["event_id"])]
        expected_risk_abs = abs(float(source["entry"]) - float(source["stop"]))
        if not (
            row["target"] == "near_week_edge"
            and row["label_id"] == f"{row['event_id']}/near_week_edge"
            and np.isclose(float(row["target_price"]), float(source["target_price"]), rtol=1e-12, atol=0)
            and np.isclose(float(row["entry"]), float(source["entry"]), rtol=1e-12, atol=0)
            and np.isclose(float(row["stop"]), float(source["stop"]), rtol=1e-12, atol=0)
            and np.isclose(float(row["risk_abs"]), expected_risk_abs, rtol=1e-12, atol=1e-14)
            and np.isclose(float(row["risk_frac"]), float(source["risk_frac"]), rtol=1e-12, atol=1e-14)
        ):
            raise RuntimeError(f"Fixed3 engine changed frozen entry or 3% stop/near target for {row['event_id']}")
    _write_symbol_cache(str(symbol), frame, signature, reference_signature)
    return frame, "computed"


def _load_frozen_input() -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    events, parent_receipt, signature = near._load_frozen_input()
    if not REFERENCE_LABELS.is_file():
        raise FileNotFoundError(f"Prior near-label output is missing: {REFERENCE_LABELS}")
    reference_signature = _reference_signature()
    return events, parent_receipt, signature, reference_signature


def _validate_same_near_population(
    groups: Mapping[str, list[dict[str, Any]]],
    reference_signature: Mapping[str, Any],
) -> None:
    previous = pd.read_csv(REFERENCE_LABELS, float_precision="round_trip", usecols=["event_id", "target", "target_price", "entry"])
    expected = {
        str(event["event_id"]): event
        for symbol_events in groups.values()
        for event in symbol_events
    }
    if len(expected) != 59_622 or len(previous) != len(expected):
        raise ValueError(
            f"Fixed3 near population must contain 59622 rows; frozen={len(expected)}, prior={len(previous)}"
        )
    if previous.event_id.duplicated().any() or set(previous.event_id.astype(str)) != set(expected):
        raise ValueError("Fixed3 eligible event IDs differ from the prior near-label experiment")
    prior = previous.set_index("event_id")
    for event_id, event in expected.items():
        row = prior.loc[event_id]
        if not (
            str(row.target) == "near_week_edge"
            and np.isclose(float(row.target_price), float(event["target_price"]), rtol=1e-12, atol=0)
            and np.isclose(float(row.entry), float(event["entry"]), rtol=1e-12, atol=0)
        ):
            raise ValueError(f"Fixed3 near target/entry differs from prior labels for {event_id}")
    if dict(reference_signature) != _reference_signature():
        raise RuntimeError("Prior near-label reference changed during population validation")


def _write_run_outputs(
    labels: pd.DataFrame,
    counts: Mapping[str, int],
    *,
    parent_receipt: Mapping[str, Any],
    signature: Mapping[str, Any],
    reference_signature: Mapping[str, Any],
    workers: int,
    elapsed_seconds: float,
    cache_hits: int,
    symbol_count: int,
) -> dict[str, Any]:
    labels = labels.reindex(columns=OUTPUT_COLUMNS).sort_values("event_id", kind="mergesort")
    if labels.event_id.duplicated().any():
        raise AssertionError("Fixed3 near labels contain duplicate event_id values")
    labels_path = EXP / "outcomes.csv.gz"
    receipt_path = EXP / "label_receipt.json"
    if labels_path.exists() or receipt_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing fixed3 outputs in {EXP}")
    EXP.mkdir(parents=True, exist_ok=True)
    near._atomic_frame(labels_path, labels)
    receipt = {
        "experiment": EXPERIMENT_ID,
        "build_version": BUILD_VERSION,
        "frozen_source_experiment": parent_receipt["experiment"],
        "frozen_input": dict(signature),
        "prior_near_experiment": near.EXPERIMENT_ID,
        "prior_near_reference": dict(reference_signature),
        "stop_rule": "entry * (1 - side * 0.03); risk_abs and risk_frac are recomputed from this stop",
        "target_rule": "Same near weekly edge as prior near experiment: lower long uses week_low; upper short uses week_high",
        "execution": "census.run_3r_outcomes: VectorBT.from_orders fees + monday_wick_r_engine.first_hit + existing minute resolver; frozen H1 tapes",
        "fees": {
            "entry_taker": census.ENTRY_TAKER_FEE,
            "stop_exit_taker": census.EXIT_TAKER_FEE,
            "target_exit_maker": census.EXIT_MAKER_FEE,
        },
        "slippage": 0,
        "funding": 0,
        "output_columns": list(OUTPUT_COLUMNS),
        **{key: int(value) for key, value in counts.items()},
        "label_rows": int(len(labels)),
        "outcome_counts": labels.outcome.value_counts().to_dict(),
        "resolution_counts": labels.resolution.value_counts().to_dict(),
        "cache_symbol_hits": int(cache_hits),
        "cache_symbol_misses": int(symbol_count - cache_hits),
        "symbols_with_eligible_events": int(symbol_count),
        "workers": int(workers),
        "elapsed_seconds": round(float(elapsed_seconds), 3),
    }
    near._atomic_json(receipt_path, receipt)
    return receipt


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8, help="parallel symbol workers (default: 8)")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 10:
        parser.error("--workers must be between 1 and 10")

    events_frame, parent_receipt, signature, reference_signature = _load_frozen_input()
    groups, counts = prepare_fixed3_events(events_frame)
    if counts["entered_event_rows"] != int(parent_receipt["entered_rows"]):
        raise AssertionError("Prepared entered count differs from frozen parent receipt")
    _validate_same_near_population(groups, reference_signature)
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    symbols = sorted(groups)

    start = time.monotonic()
    frames: list[pd.DataFrame] = []
    cache_hits = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _run_symbol, symbol, groups[symbol], signature, reference_signature,
            ): symbol
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
        raise AssertionError("Fixed3 labels do not equal the prior near-label eligible event set")
    receipt = _write_run_outputs(
        labels, counts, parent_receipt=parent_receipt, signature=signature,
        reference_signature=reference_signature, workers=args.workers,
        elapsed_seconds=time.monotonic() - start, cache_hits=cache_hits,
        symbol_count=len(symbols),
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
