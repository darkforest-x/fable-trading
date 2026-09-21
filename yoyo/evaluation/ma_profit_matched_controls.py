"""Freeze causal, time-matched random-entry controls for Profit3R evaluation.

The extractor reads source OHLC only through each proposed control's five-bar
confirmation close.  Later source rows are read for their timestamps alone to
prove that a complete label horizon exists; their prices and outcomes never
participate in matching or ordering.  The emitted rows deliberately contain no
``profit`` field.  A later ``ma_profit_pipeline label`` invocation must run the
shared :func:`yoyo.contracts.ma_profit_filter.resolve_ma_profit_event` on them.

Controls are descriptive matching candidates, not a portfolio, a new signal,
or authorization to train or deploy a model.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from yoyo.datasets.ma_profit_cohort import canonical_asset


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-ma-profit3r-20260922-v1"
CONFIRMATION_BARS = 5
HORIZON_HOURS = 12
WARMUP_BARS = 1200
INPUT_PRE_BARS = 11


class MatchedControlError(ValueError):
    """Raised when a frozen control input or causal boundary is invalid."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise MatchedControlError(f"timestamp requires timezone: {value!r}")
    return stamp.tz_convert("UTC")


def _iso(value: pd.Timestamp) -> str:
    return _utc(value).isoformat()


def _repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    return _sha(path)


def _write_json(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _sha(path)


def _week(stamp: pd.Timestamp) -> tuple[int, int]:
    iso = _utc(stamp).isocalendar()
    return int(iso.year), int(iso.week)


def _split_bounds(plan: Mapping[str, Any]) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    splits = plan.get("splits")
    if not isinstance(splits, Mapping):
        raise MatchedControlError("plan missing splits")
    train_end = _utc(splits["train_end_exclusive"])
    val_end = _utc(splits["validation_end_exclusive"])
    test_end = _utc(splits["test_end_exclusive"])
    if not train_end < val_end < test_end:
        raise MatchedControlError("invalid split boundaries")
    return {
        "train": (pd.Timestamp("1900-01-01T00:00:00Z"), train_end),
        "val": (train_end, val_end),
        "test": (val_end, test_end),
    }


def volatility_bucket(value: float, edges: Sequence[float]) -> int:
    """Return the frozen TR14/close bin, with an edge included in its lower bin."""

    if not math.isfinite(value) or value < 0:
        raise MatchedControlError("volatility must be finite and non-negative")
    numeric = [float(edge) for edge in edges]
    if not numeric or any(not math.isfinite(edge) or edge <= 0 for edge in numeric) or numeric != sorted(numeric):
        raise MatchedControlError("invalid volatility bucket edges")
    # [0,e0], (e0,e1], ..., (last,+inf); boundary semantics are stable.
    return int(np.searchsorted(np.asarray(numeric), value, side="left"))


def _tr14_sma(values: Sequence[tuple[float, float, float]]) -> float | None:
    """Use only high/low/close ending at a closed decision candle."""

    if len(values) != 15:
        return None
    closes = np.asarray([row[2] for row in values], dtype=float)
    high = np.asarray([row[0] for row in values[1:]], dtype=float)
    low = np.asarray([row[1] for row in values[1:]], dtype=float)
    previous = closes[:-1]
    current = closes[-1]
    if not np.isfinite(np.r_[high, low, closes]).all() or np.any(high <= 0) or np.any(low <= 0) or np.any(closes <= 0):
        return None
    if np.any(high < low) or np.any(closes[1:] < low) or np.any(closes[1:] > high):
        return None
    tr = np.maximum(high - low, np.maximum(np.abs(high - previous), np.abs(low - previous)))
    if np.any(tr < 0) or current <= 0:
        return None
    result = float(np.mean(tr) / current)
    return result if math.isfinite(result) else None


def _timestamp_index(path: Path) -> pd.DatetimeIndex:
    """Vector-read the one timestamp column, without materializing source OHLC."""

    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        header = next(csv.reader([handle.readline()]), None)
        if not header:
            raise MatchedControlError(f"empty source: {path}")
        time_name = "ts" if "ts" in header else "open_time" if "open_time" in header else None
        if time_name is None:
            raise MatchedControlError(f"source has no timestamp column: {path}")
    try:
        raw_times = pd.read_csv(path, usecols=[time_name], dtype={time_name: "string"})[time_name]
        if time_name == "ts":
            raw_times = raw_times.astype("int64")
        times = pd.DatetimeIndex(pd.to_datetime(raw_times, unit="ms" if time_name == "ts" else None, utc=True, errors="raise"))
    except (OSError, ValueError, TypeError) as exc:
        raise MatchedControlError(f"invalid source timestamp column: {path}") from exc
    if times.empty or not times.is_monotonic_increasing or times.has_duplicates:
        raise MatchedControlError(f"source timestamps must be strictly ascending: {path}")
    return times


def _causal_ohlc_candidates(
    path: Path,
    times: pd.DatetimeIndex,
    decision_indices: set[int],
    *,
    expected_sha: str,
    candidate_at_decision: Callable[[int, Sequence[tuple[float, float, float]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Stream source prices and emit each candidate immediately at its decision.

    A row's OHLC is converted only when it belongs to a 14-bar true-range
    window ending at a requested decision.  In particular, this never builds a
    source-wide price frame or reads a later candle to decide an earlier one.
    """

    required_values = {index for d in decision_indices for index in range(max(0, d - 14), d + 1)}
    values: dict[int, tuple[float, float, float]] = {}
    emitted: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        header = next(csv.reader([handle.readline()]), None)
        if not header:
            raise MatchedControlError(f"empty source: {path}")
        columns = {name: index for index, name in enumerate(header)}
        time_name = "ts" if "ts" in columns else "open_time" if "open_time" in columns else None
        missing = [name for name in ("high", "low", "close") if name not in columns]
        if time_name is None or missing:
            raise MatchedControlError(f"source schema missing timestamp/OHLC: {path}")
        epoch_ms = time_name == "ts"
        for index, raw in enumerate(handle):
            row = next(csv.reader([raw]))
            if len(row) != len(header) or index >= len(times):
                raise MatchedControlError(f"malformed or changed CSV row in {path}")
            if index in required_values:
                try:
                    stamp = pd.to_datetime(int(row[columns[time_name]]), unit="ms", utc=True) if epoch_ms else pd.to_datetime(row[columns[time_name]], utc=True, errors="raise")
                    if stamp != times[index]:
                        raise MatchedControlError(f"source timestamp lineage drift: {path}")
                    values[index] = tuple(float(row[columns[name]]) for name in ("high", "low", "close"))
                except ValueError as exc:
                    raise MatchedControlError(f"invalid causal OHLC in {path} at row {index}") from exc
            if index in decision_indices:
                window = [values.get(item) for item in range(index - 14, index + 1)]
                if all(item is not None for item in window):
                    emitted.extend(candidate_at_decision(index, window))
                for old in [item for item in values if item < index - 14]:
                    del values[old]
        if len(times) and len(times) != index + 1:
            raise MatchedControlError(f"source row count changed: {path}")
    if _sha(path) != expected_sha:
        raise MatchedControlError(f"source changed while reading: {path}")
    return emitted


def _continuity_prefix(times: pd.DatetimeIndex, minutes: int) -> np.ndarray:
    gaps = np.zeros(len(times), dtype=np.int64)
    if len(times) > 1:
        gaps[1:] = np.asarray((times[1:] - times[:-1]) != pd.Timedelta(minutes=minutes), dtype=np.int64)
    return np.r_[0, np.cumsum(gaps)]


def _continuous(prefix: np.ndarray, start_i: int, end_i: int) -> bool:
    return start_i >= 0 and end_i >= start_i and end_i + 1 < len(prefix) and int(prefix[end_i + 1] - prefix[start_i + 1]) == 0


def _source_specs(value: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = value.get("sources", value) if isinstance(value, Mapping) else value
    if not isinstance(rows, Sequence):
        raise MatchedControlError("source manifest must be a sources list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise MatchedControlError("invalid source specification")
        source = str(row.get("source_path") or row.get("path") or "")
        sha = str(row.get("sha256") or row.get("source_sha256") or "")
        if not source or not sha or source in result:
            raise MatchedControlError("sources require unique source_path and sha256")
        result[source] = dict(row)
    return result


def _reference_anchors(value: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[tuple[str, pd.Timestamp]]:
    rows = value.get("events", value) if isinstance(value, Mapping) else value
    if not isinstance(rows, Sequence):
        raise MatchedControlError("reference exclusion must contain events")
    return [(canonical_asset(row["symbol"]), _utc(row.get("anchor_time") or row.get("core_end_time"))) for row in rows]


def _validate_contract(plan: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[int, list[float]]:
    if plan.get("experiment_id") != EXPERIMENT_ID or contract.get("experiment_id") != EXPERIMENT_ID:
        raise MatchedControlError("wrong Profit3R experiment")
    matched = contract.get("matched_random_control")
    if not isinstance(matched, Mapping):
        raise MatchedControlError("evaluation contract lacks matched_random_control")
    desired = int(matched.get("desired_per_event", 0))
    edges = matched.get("bucket_edges")
    labels = plan.get("label_contract", {})
    if desired <= 0 or not isinstance(edges, list):
        raise MatchedControlError("invalid matched control contract")
    if (int(labels.get("confirmation_bars", -1)), int(labels.get("horizon_hours", -1)), float(labels.get("target_r", -1)), float(labels.get("round_trip_cost", -1))) != (CONFIRMATION_BARS, HORIZON_HOURS, 3.0, 0.002):
        raise MatchedControlError("plan label contract drift")
    volatility_bucket(0.0, edges)
    return desired, [float(edge) for edge in edges]


def _event_anchor(row: Mapping[str, Any]) -> tuple[str, pd.Timestamp]:
    return canonical_asset(row.get("canonical_asset") or row.get("symbol")), _utc(row["core_end_time"])


def _validate_events(rows: Sequence[Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    required = ("event_id", "source_path", "symbol", "direction", "core_start_time", "core_end_time", "core_bars", "split")
    for raw in rows:
        if any(key not in raw for key in required):
            raise MatchedControlError("full cohort row missing matching fields")
        row = dict(raw)
        event_id = str(row["event_id"])
        if not event_id or event_id in seen:
            raise MatchedControlError("full cohort event_id must be unique")
        seen.add(event_id)
        source = str(row["source_path"])
        if source not in sources:
            raise MatchedControlError(f"event source missing from source manifest: {source}")
        row_sha = row.get("source_sha256")
        pinned_sha = sources[source].get("sha256") or sources[source].get("source_sha256")
        if row_sha is not None and str(row_sha) != str(pinned_sha):
            raise MatchedControlError(f"event source SHA differs from frozen source manifest: {source}")
        if row["direction"] not in {"LONG", "SHORT"} or int(row["core_bars"]) not in {4, 5}:
            raise MatchedControlError("controls require LONG/SHORT four-or-five-bar cores")
        if str(row["split"]) not in {"train", "val", "test", "purged"}:
            raise MatchedControlError("event split is invalid or absent")
        row["bar_minutes"] = int(row.get("bar_minutes", sources[source].get("bar_minutes", 15)))
        if row["bar_minutes"] <= 0:
            raise MatchedControlError("bar_minutes must be positive")
        row["canonical_asset"] = canonical_asset(row.get("canonical_asset") or row["symbol"])
        row["core_start_time"] = _iso(_utc(row["core_start_time"]))
        row["core_end_time"] = _iso(_utc(row["core_end_time"]))
        if _utc(row["core_end_time"]) < _utc(row["core_start_time"]):
            raise MatchedControlError("core timestamps are reversed")
        result.append(row)
    return result


def _candidate_rows_for_source(
    source: str,
    spec: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    *,
    plan: Mapping[str, Any],
    edges: Sequence[float],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Generate candidate controls with values available at their confirmation close."""

    path = _repo_path(source)
    expected_sha = str(spec.get("sha256") or spec.get("source_sha256") or "")
    actual_sha = _sha(path)
    if actual_sha != expected_sha:
        raise MatchedControlError(f"source SHA drift or unpinned source: {source}")
    minutes = int(spec.get("bar_minutes", targets[0]["bar_minutes"]))
    if any(int(row["bar_minutes"]) != minutes for row in targets):
        raise MatchedControlError(f"source timeframe mismatch: {source}")
    assets = {str(row["canonical_asset"]) for row in targets}
    if len(assets) != 1:
        raise MatchedControlError(f"a source must map to one canonical asset: {source}")
    source_symbol = spec.get("symbol")
    if source_symbol is not None and canonical_asset(source_symbol) != next(iter(assets)):
        raise MatchedControlError(f"source symbol does not match event asset: {source}")
    source_venue = spec.get("venue")
    if source_venue is not None and any(str(row.get("venue", source_venue)) != str(source_venue) for row in targets):
        raise MatchedControlError(f"source venue does not match event lineage: {source}")
    times = _timestamp_index(path)
    prefix = _continuity_prefix(times, minutes)
    bounds = _split_bounds(plan)
    requested = {(str(row["split"]), str(row["direction"]), int(row["core_bars"]), _week(_utc(row["core_end_time"]))) for row in targets}
    if not requested:
        return [], {}
    # c is core end; d=c+5 is the decision candle.  Limit price parsing to
    # candidate TR14 windows in requested weeks.  Timestamp rows stay available
    # for label-horizon checks without opening their OHLC fields.
    core_week_specs: dict[tuple[int, int], list[tuple[str, str, int]]] = defaultdict(list)
    for split, direction, bars, week in requested:
        core_week_specs[week].append((split, direction, bars))
    request_weeks = {item[3] for item in requested}
    iso = times.isocalendar()
    core_mask = np.zeros(len(times), dtype=bool)
    for year, week in request_weeks:
        core_mask |= (np.asarray(iso.year, dtype=np.int64) == year) & (np.asarray(iso.week, dtype=np.int64) == week)
    core_indices = np.flatnonzero(core_mask)
    decision_week = {int(c + CONFIRMATION_BARS): (int(iso.year.iloc[c]), int(iso.week.iloc[c])) for c in core_indices if c + CONFIRMATION_BARS < len(times)}
    decision_indices: set[int] = set(decision_week)
    target_ids_by_decision: dict[int, list[str]] = defaultdict(list)
    for target in targets:
        end_stamp = _utc(target["core_end_time"])
        c = int(times.get_indexer([end_stamp])[0])
        bars = int(target["core_bars"])
        if c < 0 or c - bars + 1 < 0 or times[c - bars + 1] != _utc(target["core_start_time"]):
            raise MatchedControlError(f"target core timestamp/index lineage drift: {target['event_id']}")
        if "source_core_end_i" in target and int(target["source_core_end_i"]) != c:
            raise MatchedControlError(f"target source_core_end_i drift: {target['event_id']}")
        d = c + CONFIRMATION_BARS
        if d >= len(times) or not _continuous(prefix, c, d):
            continue
        decision_indices.add(d)
        target_ids_by_decision[d].append(str(target["event_id"]))
    if not decision_indices:
        return [], {}
    target_buckets: dict[str, int] = {}
    def candidate_at_decision(d: int, values: Sequence[tuple[float, float, float]]) -> list[dict[str, Any]]:
        c = d - CONFIRMATION_BARS
        if not _continuous(prefix, d-14, d):
            return []
        volatility = _tr14_sma(values)
        if volatility is None:
            return []
        bucket = volatility_bucket(volatility, edges)
        for event_id in target_ids_by_decision.get(d, []):
            target_buckets[event_id] = bucket
        if c < 0 or d not in decision_week:
            return []
        decision_close = times[d] + pd.Timedelta(minutes=minutes)
        emitted: list[dict[str, Any]] = []
        for split, direction, bars in core_week_specs[decision_week[d]]:
            start = c - bars + 1
            input_start = start - INPUT_PRE_BARS
            support_start = input_start - WARMUP_BARS
            last_label_bar = c + CONFIRMATION_BARS + 1 + int(HORIZON_HOURS * 60 // minutes) - 1
            label_end = decision_close + pd.Timedelta(hours=HORIZON_HOURS)
            left, right = bounds[split]
            if not _continuous(prefix, support_start, last_label_bar):
                continue
            if times[input_start] < left or not label_end < right:
                continue
            if times[d] + pd.Timedelta(minutes=minutes) != decision_close:
                raise MatchedControlError("unreachable confirmation timing")
            emitted.append({
                "source_path": source,
                "source_sha256": actual_sha,
                "symbol": str(spec.get("symbol") or targets[0]["symbol"]),
                "venue": str(spec.get("venue") or targets[0].get("venue") or ""),
                "canonical_asset": targets[0]["canonical_asset"],
                "bar_minutes": minutes,
                "direction": direction,
                "core_bars": bars,
                "split": split,
                "source_core_start_i": start,
                "source_core_end_i": c,
                "core_start_time": _iso(times[start]),
                "core_end_time": _iso(times[c]),
                "core_utc_week": {"year": decision_week[d][0], "week": decision_week[d][1]},
                "decision_index": d,
                "decision_close_time_utc": _iso(decision_close),
                "entry_open_time_utc": _iso(decision_close),
                "label_window_end_utc": _iso(label_end),
                "volatility_sma14": volatility,
                "volatility_bucket": bucket,
                "volatility_bucket_edges": list(edges),
            })
        return emitted
    candidates = _causal_ohlc_candidates(path, times, decision_indices, expected_sha=actual_sha, candidate_at_decision=candidate_at_decision)
    return candidates, target_buckets


def _control_identity(candidate: Mapping[str, Any]) -> str:
    return "|".join((str(candidate["source_path"]), str(candidate["direction"]), str(candidate["source_core_start_i"]), str(candidate["source_core_end_i"])))


def _stable_key(event_id: str, candidate: Mapping[str, Any]) -> str:
    raw = f"{event_id}|{candidate['decision_close_time_utc']}|{_control_identity(candidate)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_resolved_target(row: Mapping[str, Any]) -> bool:
    profit = row.get("profit")
    return isinstance(profit, Mapping) and str(profit.get("outcome", "")).upper() in {"TP", "SL", "TIMEOUT"}


def _target_rows(
    full_cohort: Sequence[Mapping[str, Any]], target_events: Sequence[Mapping[str, Any]] | None,
    sources: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Require a frozen resolved val/test target ledger; retained is irrelevant."""

    full_by_id = {str(row["event_id"]): row for row in full_cohort}
    selected = _validate_events(target_events, sources) if target_events is not None else list(full_cohort)
    result: list[dict[str, Any]] = []
    for row in selected:
        if row["split"] == "train":
            continue  # The frozen dataset ledger also contains its training arm.
        original = full_by_id.get(str(row["event_id"]))
        if original is None:
            raise MatchedControlError(f"target event absent from full cohort: {row['event_id']}")
        if row.get("profit") != original.get("profit"):
            raise MatchedControlError(f"target/full cohort outcome drift: {row['event_id']}")
        for key in ("source_path", "canonical_asset", "direction", "bar_minutes", "core_bars", "core_start_time", "core_end_time", "split"):
            if row[key] != original[key]:
                raise MatchedControlError(f"target/full cohort lineage mismatch for {row['event_id']}: {key}")
        if row["split"] not in {"val", "test"} or not _is_resolved_target(row):
            raise MatchedControlError(f"target ledger must contain resolved val/test events only: {row['event_id']}")
        result.append(row)
    if not result:
        raise MatchedControlError("target ledger has no resolved val/test events")
    return result


def build_matched_controls(
    plan: Mapping[str, Any],
    contract: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    reference_exclusion: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    out: Path | str,
    *,
    target_events: Sequence[Mapping[str, Any]] | None = None,
    inputs: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Freeze val/test controls without invoking a future-aware outcome resolver."""

    output = Path(out)
    if output.exists():
        raise MatchedControlError(f"refusing to overwrite existing output: {output}")
    desired, edges = _validate_contract(plan, contract)
    specs = _source_specs(sources)
    cohort = _validate_events(events, specs)
    targets = _target_rows(cohort, target_events, specs)
    anchors = _reference_anchors(reference_exclusion)
    radius = pd.Timedelta(hours=4)
    if isinstance(reference_exclusion, Mapping) and float(reference_exclusion.get("radius_hours", 4)) != 4:
        raise MatchedControlError("reference exclusion radius must remain four hours")
    all_event_anchors = [_event_anchor(row) for row in cohort]
    for target in targets:
        asset, stamp = _event_anchor(target)
        if any(anchor_asset == asset and abs(stamp - anchor_time) <= radius for anchor_asset, anchor_time in anchors):
            raise MatchedControlError(f"target ledger includes reference-neighborhood event: {target['event_id']}")
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in targets:
        by_source[str(row["source_path"])].append(row)
    selected: list[dict[str, Any]] = []
    shortages: list[dict[str, Any]] = []
    for source, source_targets in sorted(by_source.items()):
        candidates, target_buckets = _candidate_rows_for_source(source, specs[source], source_targets, plan=plan, edges=edges)
        for event in sorted(source_targets, key=lambda row: str(row["event_id"])):
            asset, target_time = _event_anchor(event)
            event_week = _week(target_time)
            target_bucket = target_buckets.get(str(event["event_id"]))
            if target_bucket is None:
                shortages.append({"matched_event_id": event["event_id"], "requested": desired, "eligible_before_exclusion": 0, "eligible_after_exclusion": 0, "selected": 0, "shortage": desired, "reason": "target_volatility_unknown"})
                continue
            matching_shape = [
                candidate for candidate in candidates
                if candidate["canonical_asset"] == asset
                and candidate["direction"] == event["direction"]
                and candidate["bar_minutes"] == event["bar_minutes"]
                and candidate["core_bars"] == event["core_bars"]
                and candidate["split"] == event["split"]
                and candidate["core_utc_week"] == {"year": event_week[0], "week": event_week[1]}
                and candidate["volatility_bucket"] == target_bucket
            ]
            allowed: list[dict[str, Any]] = []
            for candidate in matching_shape:
                candidate_time = _utc(candidate["core_end_time"])
                if any(anchor_asset == asset and abs(candidate_time - anchor_time) <= radius for anchor_asset, anchor_time in all_event_anchors):
                    continue
                if any(anchor_asset == asset and abs(candidate_time - anchor_time) <= radius for anchor_asset, anchor_time in anchors):
                    continue
                allowed.append(candidate)
            chosen = sorted(allowed, key=lambda candidate: _stable_key(str(event["event_id"]), candidate))[:desired]
            shortages.append({"matched_event_id": event["event_id"], "requested": desired, "eligible_before_exclusion": len(matching_shape), "eligible_after_exclusion": len(allowed), "selected": len(chosen), "shortage": desired - len(chosen), "reason": "ok" if len(chosen) == desired else "insufficient_exact_candidates"})
            for order, candidate in enumerate(chosen, 1):
                identity = _control_identity(candidate)
                control_id = hashlib.sha256(f"matched-control-v1|{event['event_id']}|{identity}".encode()).hexdigest()[:24]
                selected.append({
                    **candidate,
                    "event_id": f"control_{control_id}",
                    "frozen_event_id": f"control_{control_id}",
                    "origin": "matched_random_control",
                    "matched_event_id": event["event_id"],
                    "control_identity": identity,
                    "match_order": order,
                    "matched_utc_week": {"year": event_week[0], "week": event_week[1]},
                    "matched_volatility_bucket": target_bucket,
                    "training_eligible": False,
                    "production_eligible": False,
                })
        del candidates
    selected.sort(key=lambda row: (str(row["matched_event_id"]), int(row["match_order"])))
    if len({row["event_id"] for row in selected}) != len(selected):
        raise MatchedControlError("control event IDs collided")
    output.mkdir(parents=True)
    events_sha = _write_jsonl(output / "frozen_events.jsonl", selected)
    used_sources = sorted({str(row["source_path"]) for row in selected})
    source_rows = [dict(specs[source]) for source in used_sources]
    sources_sha = _write_json(output / "frozen_sources.json", {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "sources": source_rows})
    reuse = Counter(str(row["control_identity"]) for row in selected)
    receipt = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "selection": "stable_sha256(event_id|candidate_decision_close_time_utc|candidate_identity), before any outcome computation",
        "desired_per_event": desired,
        "population_events": len(cohort),
        "matched_events": len(targets),
        "controls": len(selected),
        "shortages": shortages,
        "events_with_shortage": sum(row["shortage"] > 0 for row in shortages),
        "control_reuse": {"unique_raw_controls": len(reuse), "reused_raw_controls": sum(count > 1 for count in reuse.values()), "max_reuse": max(reuse.values(), default=0), "counts": dict(sorted(reuse.items()))},
        "estimand": "Per-event averaged matched controls; raw-control reuse is retained and controls are not independent trade observations.",
        "outcomes_used_for_selection": False,
        "frozen_events_sha256": events_sha,
        "frozen_sources_sha256": sources_sha,
        "inputs": list(inputs or []),
        "training_eligible": False,
        "production_eligible": False,
    }
    _write_json(output / "receipt.json", receipt)
    return receipt


def _formal_guard(paths: Sequence[Path]) -> str:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise MatchedControlError("main branch required")
    names = [_relative(path) for path in paths]
    status = subprocess.check_output(["git", "status", "--porcelain", "--", *names], cwd=ROOT, text=True)
    if status.strip():
        raise MatchedControlError("commit builder and frozen inputs before formal control build: " + status.strip())
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _load(path: Path, *, jsonl: bool = False) -> Any:
    return _read_jsonl(path) if jsonl else json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze causal matched random controls for Profit3R evaluation")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True, help="full labelled cohort with train/val/test split")
    parser.add_argument("--target-events", type=Path, required=True, help="frozen resolved non-reference val/test ledger")
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--reference-exclusion", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    all_paths = [Path(__file__), args.plan, args.contract, args.events, args.target_events, args.sources, args.reference_exclusion]
    commit = _formal_guard(all_paths)
    if args.out.exists():
        raise MatchedControlError(f"refusing to overwrite existing output: {args.out}")
    plan, contract = _load(args.plan), _load(args.contract)
    if contract.get("original_plan_sha256") != _sha(args.plan):
        raise MatchedControlError("evaluation contract plan SHA drift")
    inputs = [{"path": _relative(path), "sha256": _sha(path)} for path in all_paths[1:]]
    receipt = build_matched_controls(plan, contract, _load(args.events, jsonl=True), _load(args.sources), _load(args.reference_exclusion), args.out, target_events=_load(args.target_events, jsonl=True), inputs=inputs)
    receipt["builder_commit"] = commit
    _write_json(args.out / "receipt.json", receipt)
    print(json.dumps({"matched_events": receipt["matched_events"], "controls": receipt["controls"], "events_with_shortage": receipt["events_with_shortage"]}, sort_keys=True))


if __name__ == "__main__":
    main()
