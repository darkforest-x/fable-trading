"""Build nuisance-matched negatives for the Grade-A 15-minute dataset.

The positive inputs and labels are copied byte-for-byte from the frozen
Grade-A 8,000-image dataset.  Negative labels may inspect OHLC through pseudo
core ``+5`` to prove that no completed launch occurred; rendered pixels use
only the matched 18/19-bar context variant.  Every negative event is paired to
one positive event by source, venue, symbol, half-year, chronological split,
core length and the exact set of horizontal context positions.  Binance weak
launch candidates and every rediscovered OKX strict candidate are protected
before sampling.  This module never reads holdout, trains, promotes, deploys,
or changes live state.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
    read_preholdout_prefix,
    sha256_file,
)
from yoyo.datasets.ma_launch_owner_autofill10000 import (
    load_reference_profiles,
    scan_source,
)
from yoyo.datasets.ma_launch_owner_recrop_review import (
    HOLDOUT_START,
    RED,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    encode_png,
)
from yoyo.datasets.ma_launch_owner_yolo_dataset import (
    calendar_halfyear,
    interval_split,
    negative_feature_masks,
    stable_id,
    stable_int,
)
from yoyo.layers.l1_detection.render import render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-neg24000-v1"
DEFAULT_PREREG = (
    ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
)
DEFAULT_RESULTS = DEFAULT_PREREG.parent / "results"
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_v1"
MODULE_PATH = Path(__file__).resolve()
SCRIPT_PATH = ROOT / "scripts" / "build_15m_ma_launch_owner_grade_a_neg24000.py"
EXACT_OVERLAY_RED = np.asarray(RED, dtype=np.uint8)


class GradeANegativeError(ValueError):
    """Raised when negative sampling, lineage, or parity contracts drift."""


@dataclass(frozen=True)
class PositiveEvent:
    """One independent positive event and its seven/eight render variants."""

    event_id: str
    sample_id: str
    event_order: int
    source_path: str
    venue: str
    symbol: str
    exchange_symbol: str
    direction: str
    split: str
    time_block: str
    core_bars: int
    core_start_i: int
    core_end_i: int
    core_start_time: str
    core_end_time: str
    variants: tuple[tuple[str, int, int, int], ...]


@dataclass(frozen=True)
class NegativeEvent:
    """One empty-label event rendered at every paired positive position."""

    negative_event_id: str
    paired_positive_event_id: str
    paired_positive_sample_id: str
    paired_positive_event_order: int
    pair_slot: int
    paired_direction: str
    source_path: str
    venue: str
    symbol: str
    exchange_symbol: str
    split: str
    time_block: str
    negative_kind: str
    requested_kind: str
    core_bars: int
    core_start_i: int
    core_end_i: int
    core_start_time: str
    core_end_time: str
    widest_window_start_i: int
    widest_window_end_i: int
    dependency_end_i: int
    dependency_end_time: str
    variants: tuple[tuple[str, int, int, int], ...]
    ma_envelope_atr: float
    ma_spread_end_atr: float
    max_body_atr: float
    candle_envelope_atr: float
    minimum_close_to_ma_atr: float
    abs_close_progress_atr_core_plus_2: float
    abs_close_progress_atr_core_plus_3: float
    abs_close_progress_atr_core_plus_5: float
    two_sided_excursion_atr_core_plus_1_to_5: float


def _repo_path(value: object) -> Path:
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise GradeANegativeError(f"path escapes repository: {value}") from exc
    return resolved


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _verify_pinned(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256_file(path) != str(expected):
        raise GradeANegativeError(f"{label} SHA drift: {path}")


def _verify_builder_committed(paths: Sequence[Path]) -> str:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise GradeANegativeError("dataset builder must run on main")
    relative = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise GradeANegativeError(f"builder inputs are not committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative[0]],
        cwd=ROOT,
        text=True,
    ).strip()
    if len(commit) != 40:
        raise GradeANegativeError("could not resolve builder commit")
    return commit


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    prereg = _read_json(path)
    if str(prereg.get("experiment_id")) != path.resolve().parent.name:
        raise GradeANegativeError("experiment_id must match its directory")
    authorization = prereg["owner_authorization"]
    if authorization.get("negative_materialization_authorized") is not True:
        raise GradeANegativeError("negative materialization is not authorized")
    if authorization.get("training_run_authorized") is not False:
        raise GradeANegativeError("this P1 builder must not authorize training")
    safety = prereg["safety"]
    for key in (
        "holdout_read",
        "training_started",
        "training_eligible",
        "production_eligible",
        "active_or_frozen_change",
        "promote",
        "deployment",
        "forward_state_change",
        "order_state_change",
        "remote_write",
    ):
        if safety.get(key) is not False:
            raise GradeANegativeError(f"safety switch must remain false: {key}")
    if int(prereg["sources"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise GradeANegativeError("holdout row allowance must be zero")
    negative = prereg["negative_sampling"]
    if int(negative["negative_events_per_positive_event"]) != 3:
        raise GradeANegativeError("this contract requires three negative events")
    if list(negative["target_kinds_per_positive_event"]) != ["hard", "hard", "easy"]:
        raise GradeANegativeError("target negative kinds drift")
    if int(negative["target_negative_images"]) != 24000:
        raise GradeANegativeError("negative image target drift")
    if int(negative["target_negative_events"]) != 3129:
        raise GradeANegativeError("negative event target drift")
    return prereg


def load_positive_rows(prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    source = prereg["positive_source"]
    prereg_path = _repo_path(source["preregistration_path"])
    manifest_path = _repo_path(source["manifest_path"])
    receipt_path = _repo_path(source["build_receipt_path"])
    _verify_pinned(prereg_path, source["preregistration_sha256"], "positive prereg")
    _verify_pinned(manifest_path, source["manifest_sha256"], "positive manifest")
    _verify_pinned(receipt_path, source["build_receipt_sha256"], "positive receipt")
    rows = _read_jsonl(manifest_path)
    if len(rows) != int(source["images"]):
        raise GradeANegativeError("positive image count drift")
    if len({str(row["dataset_sample_id"]) for row in rows}) != len(rows):
        raise GradeANegativeError("duplicate positive dataset sample ID")
    if any(str(row["split"]) not in {"train", "val"} for row in rows):
        raise GradeANegativeError("positive manifest exposes a non-train/val row")
    return rows


def group_positive_events(
    rows: Sequence[Mapping[str, Any]], *, expected_events: int | None = None
) -> list[PositiveEvent]:
    """Collapse render variants while preserving their exact position ledger."""

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["event_id"])].append(row)
    if expected_events is not None and len(grouped) != expected_events:
        raise GradeANegativeError(f"positive event count drift: {len(grouped)}")
    events: list[PositiveEvent] = []
    stable_fields = (
        "sample_id",
        "event_order",
        "source_path",
        "venue",
        "symbol",
        "exchange_symbol",
        "direction",
        "split",
        "time_block",
        "core_bars",
        "source_core_start_i",
        "source_core_end_i",
        "core_start_time",
        "core_end_time",
    )
    for event_id, variants in grouped.items():
        first = variants[0]
        for field in stable_fields:
            if len({str(row[field]) for row in variants}) != 1:
                raise GradeANegativeError(f"event {event_id} varies in {field}")
        ordered = sorted(variants, key=lambda row: int(row["variant_index"]))
        variant_ledger = tuple(
            (
                str(row["variant_id"]),
                int(row["variant_index"]),
                int(row["pre_bars"]),
                int(row["post_bars"]),
            )
            for row in ordered
        )
        if len(variant_ledger) not in {7, 8}:
            raise GradeANegativeError(f"event {event_id} has {len(variant_ledger)} variants")
        if len({value[1] for value in variant_ledger}) != len(variant_ledger):
            raise GradeANegativeError(f"event {event_id} repeats a variant index")
        core_bars = int(first["core_bars"])
        if any(pre + core_bars + post not in {18, 19} for _, _, pre, post in variant_ledger):
            raise GradeANegativeError(f"event {event_id} left the 18/19-bar contract")
        events.append(
            PositiveEvent(
                event_id=event_id,
                sample_id=str(first["sample_id"]),
                event_order=int(first["event_order"]),
                source_path=str(first["source_path"]),
                venue=str(first["venue"]),
                symbol=str(first["symbol"]),
                exchange_symbol=str(first["exchange_symbol"]),
                direction=str(first["direction"]),
                split=str(first["split"]),
                time_block=str(first["time_block"]),
                core_bars=core_bars,
                core_start_i=int(first["source_core_start_i"]),
                core_end_i=int(first["source_core_end_i"]),
                core_start_time=str(first["core_start_time"]),
                core_end_time=str(first["core_end_time"]),
                variants=variant_ledger,
            )
        )
    return sorted(events, key=lambda value: value.event_order)


def legacy_negative_compatibility_audit(
    positive_rows: Sequence[Mapping[str, Any]], legacy_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Quantify whether the prior negatives can be a drop-in cohort."""

    negatives = [row for row in legacy_rows if row.get("sample_kind") == "negative"]
    positive_sources = {str(row["source_path"]) for row in positive_rows}
    negative_sources = {str(row["source_path"]) for row in negatives}
    legacy_windows = Counter(
        int(row["window_end_i"]) - int(row["window_start_i"]) + 1 for row in negatives
    )
    new_venues = Counter(str(row["venue"]) for row in positive_rows)
    return {
        "legacy_negative_rows": len(negatives),
        "legacy_negative_sources": len(negative_sources),
        "legacy_okx_source_rows": sum(
            str(row["source_path"]).split("/")[-1].startswith("okx_") for row in negatives
        ),
        "legacy_window_bars": dict(sorted(legacy_windows.items())),
        "legacy_18_or_19_rows": legacy_windows[18] + legacy_windows[19],
        "legacy_18_or_19_share": (legacy_windows[18] + legacy_windows[19]) / len(negatives),
        "new_positive_venues": dict(new_venues),
        "new_positive_sources": len(positive_sources),
        "common_source_paths": len(positive_sources & negative_sources),
        "new_sources_without_legacy_negatives": len(positive_sources - negative_sources),
        "drop_in_compatible": False,
        "reason": "venue, source, window-length and horizontal-context nuisance distributions differ",
    }


def _mark_interval(mask: np.ndarray, start: int, end: int) -> None:
    left = max(0, int(start))
    right = min(len(mask) - 1, int(end))
    if left <= right:
        mask[left : right + 1] = True


def _contiguous(segment: np.ndarray, start: int, end: int) -> bool:
    return 0 <= start <= end < len(segment) and segment[start] == segment[end]


def window_activity_pass(
    enriched: pd.DataFrame,
    start: int,
    end: int,
    *,
    minimum_unique_closes: int,
    minimum_nonzero_ranges: int,
) -> bool:
    """Reject post-settlement flat spans using only rendered OHLC columns."""

    if not 0 <= int(start) <= int(end) < len(enriched):
        return False
    window = enriched.iloc[int(start) : int(end) + 1]
    closes = pd.to_numeric(window["close"], errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(window["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(window["low"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(np.stack([closes, highs, lows], axis=1)).all():
        return False
    return (
        len(np.unique(closes)) >= int(minimum_unique_closes)
        and int(np.count_nonzero(highs > lows)) >= int(minimum_nonzero_ranges)
    )


def _block_mask(times: pd.Series, block: str) -> np.ndarray:
    year = int(block[:4])
    month = 1 if int(block[-1]) == 1 else 7
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    end = start + pd.DateOffset(months=6)
    return ((times >= start) & (times < end)).to_numpy(dtype=bool)


def select_source_negative_events(
    enriched: pd.DataFrame,
    *,
    source_path: str,
    positives: Sequence[PositiveEvent],
    protected_candidates: Sequence[Mapping[str, Any]],
    prereg: Mapping[str, Any],
) -> tuple[list[NegativeEvent], dict[str, Any]]:
    """Select three source-matched non-overlapping negative events per positive."""

    times = pd.to_datetime(enriched["open_time"], utc=True)
    segment = enriched["_segment_id"].to_numpy(dtype=int)
    occupied = np.zeros(len(enriched), dtype=bool)
    negative_cfg = prereg["negative_sampling"]
    guard = negative_cfg["positive_guard"]
    before = int(guard["before_core_bars"])
    after = int(guard["after_dependency_end_bars"])
    for row in protected_candidates:
        core_start = int(row["source_core_start_i"])
        core_end = int(row["source_core_end_i"])
        _mark_interval(occupied, core_start - before, core_end + 5 + after)
    for event in positives:
        max_post = max(value[3] for value in event.variants)
        _mark_interval(occupied, event.core_start_i - before, event.core_end_i + max(5, max_post) + after)

    masks = {
        core_len: negative_feature_masks(enriched, core_len=core_len, prereg=prereg)
        for core_len in {event.core_bars for event in positives}
    }
    pools: dict[tuple[int, str, str], np.ndarray] = {}
    cursors: Counter[tuple[int, str, str]] = Counter()
    rejections: Counter[str] = Counter()
    fallbacks: Counter[str] = Counter()
    separation = int(negative_cfg["negative_separation_bars"])
    activity = negative_cfg["activity_gate"]

    def pool_for(core_bars: int, kind: str, block: str) -> np.ndarray:
        key = (core_bars, kind, block)
        if key not in pools:
            eligible = masks[core_bars][kind] & _block_mask(times, block)
            indices = np.flatnonzero(eligible)
            rng = np.random.default_rng(
                stable_int(prereg["experiment_id"], source_path, core_bars, kind, block)
            )
            pools[key] = rng.permutation(indices)
        return pools[key]

    selected: list[NegativeEvent] = []
    split_cfg = prereg["split"]
    for positive in sorted(positives, key=lambda value: value.event_order):
        max_pre = max(value[2] for value in positive.variants)
        max_post = max(value[3] for value in positive.variants)
        requested_kinds = [str(value) for value in negative_cfg["target_kinds_per_positive_event"]]
        for pair_slot, requested_kind in enumerate(requested_kinds, 1):
            search_kinds = [requested_kind]
            if requested_kind == "easy" and bool(negative_cfg["easy_to_hard_fallback_allowed"]):
                search_kinds.append("hard")
            found: NegativeEvent | None = None
            for actual_kind in search_kinds:
                key = (positive.core_bars, actual_kind, positive.time_block)
                pool = pool_for(*key)
                while cursors[key] < len(pool):
                    core_end = int(pool[cursors[key]])
                    cursors[key] += 1
                    core_start = core_end - positive.core_bars + 1
                    window_start = core_start - max_pre
                    window_end = core_end + max_post
                    dependency_end = max(window_end, core_end + 5)
                    if not _contiguous(segment, window_start, dependency_end):
                        rejections["gap_or_bounds"] += 1
                        continue
                    if not window_activity_pass(
                        enriched,
                        window_start,
                        dependency_end,
                        minimum_unique_closes=int(
                            activity["minimum_unique_close_values_in_widest_dependency"]
                        ),
                        minimum_nonzero_ranges=int(
                            activity["minimum_nonzero_high_low_bars_in_widest_dependency"]
                        ),
                    ):
                        rejections["inactive_flat_window"] += 1
                        continue
                    assigned = interval_split(
                        times.iloc[window_start],
                        times.iloc[dependency_end],
                        cutoff=split_cfg["cutoff"],
                        purge_bars=int(split_cfg["purge_bars_each_side"]),
                        bar_minutes=int(split_cfg["bar_minutes"]),
                    )
                    if assigned != positive.split:
                        rejections["split_mismatch"] += 1
                        continue
                    if calendar_halfyear(times.iloc[core_end]) != positive.time_block:
                        rejections["time_block_mismatch"] += 1
                        continue
                    guarded_start = window_start - separation
                    guarded_end = dependency_end + separation
                    if occupied[max(0, guarded_start) : min(len(occupied), guarded_end + 1)].any():
                        rejections["protected_or_reused"] += 1
                        continue
                    metrics = masks[positive.core_bars]
                    negative_event_id = stable_id(
                        prereg["experiment_id"],
                        source_path,
                        positive.event_id,
                        pair_slot,
                        core_end,
                        actual_kind,
                    )
                    found = NegativeEvent(
                        negative_event_id=negative_event_id,
                        paired_positive_event_id=positive.event_id,
                        paired_positive_sample_id=positive.sample_id,
                        paired_positive_event_order=positive.event_order,
                        pair_slot=pair_slot,
                        paired_direction=positive.direction,
                        source_path=source_path,
                        venue=positive.venue,
                        symbol=positive.symbol,
                        exchange_symbol=positive.exchange_symbol,
                        split=positive.split,
                        time_block=positive.time_block,
                        negative_kind=actual_kind,
                        requested_kind=requested_kind,
                        core_bars=positive.core_bars,
                        core_start_i=core_start,
                        core_end_i=core_end,
                        core_start_time=times.iloc[core_start].isoformat(),
                        core_end_time=times.iloc[core_end].isoformat(),
                        widest_window_start_i=window_start,
                        widest_window_end_i=window_end,
                        dependency_end_i=dependency_end,
                        dependency_end_time=times.iloc[dependency_end].isoformat(),
                        variants=positive.variants,
                        ma_envelope_atr=float(metrics["ma_envelope_atr"][core_end]),
                        ma_spread_end_atr=float(metrics["ma_spread_end_atr"][core_end]),
                        max_body_atr=float(metrics["max_body_atr"][core_end]),
                        candle_envelope_atr=float(metrics["candle_envelope_atr"][core_end]),
                        minimum_close_to_ma_atr=float(metrics["minimum_close_to_ma_atr"][core_end]),
                        abs_close_progress_atr_core_plus_2=float(metrics["close2"][core_end]),
                        abs_close_progress_atr_core_plus_3=float(metrics["close3"][core_end]),
                        abs_close_progress_atr_core_plus_5=float(metrics["close5"][core_end]),
                        two_sided_excursion_atr_core_plus_1_to_5=float(metrics["excursion"][core_end]),
                    )
                    _mark_interval(occupied, guarded_start, guarded_end)
                    if actual_kind != requested_kind:
                        fallbacks[f"{requested_kind}_to_{actual_kind}"] += 1
                    break
                if found is not None:
                    break
            if found is None:
                raise GradeANegativeError(
                    "insufficient negative capacity without weakening hard constraints: "
                    f"source={source_path} event={positive.event_id} slot={pair_slot} "
                    f"requested={requested_kind}"
                )
            selected.append(found)
    return selected, {
        "source_path": source_path,
        "venue": positives[0].venue,
        "symbol": positives[0].symbol,
        "positive_events": len(positives),
        "protected_candidates": len(protected_candidates),
        "negative_events": len(selected),
        "negative_images": sum(len(event.variants) for event in selected),
        "negative_kinds": dict(Counter(event.negative_kind for event in selected)),
        "fallbacks": dict(fallbacks),
        "rejections": dict(rejections),
        "pool_rows_total": sum(len(pool) for pool in pools.values()),
    }


def _load_binance_guards(prereg: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    guard = prereg["negative_sampling"]["positive_guard"]
    path = _repo_path(guard["binance_candidates_path"])
    _verify_pinned(path, guard["binance_candidates_sha256"], "Binance candidate guard")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(path):
        grouped[str(row["source_path"])].append(row)
    return grouped


def _plan_paths(results_path: Path) -> tuple[Path, Path, Path]:
    return (
        results_path / "negative_event_plan.jsonl",
        results_path / "negative_source_audit.jsonl",
        results_path / "plan_receipt.json",
    )


def plan_dataset(
    *, prereg_path: Path = DEFAULT_PREREG, results_path: Path = DEFAULT_RESULTS
) -> dict[str, Any]:
    """Select and freeze every negative event without rendering images."""

    prereg_path = prereg_path.resolve()
    results_path = results_path.resolve()
    builder_commit = _verify_builder_committed((MODULE_PATH, SCRIPT_PATH, prereg_path))
    prereg = load_preregistration(prereg_path)
    positive_rows = load_positive_rows(prereg)
    positives = group_positive_events(
        positive_rows, expected_events=int(prereg["positive_source"]["events"])
    )
    plan_path, audit_path, receipt_path = _plan_paths(results_path)
    if any(path.exists() for path in (plan_path, audit_path, receipt_path)):
        raise FileExistsError(f"refusing to overwrite existing plan under {results_path}")

    legacy_manifest = _repo_path(prereg["legacy_negative_audit"]["dataset_dir"]) / "manifest.jsonl"
    legacy_audit = legacy_negative_compatibility_audit(
        positive_rows, _read_jsonl(legacy_manifest)
    )
    binance_guards = _load_binance_guards(prereg)
    source_cfg = prereg["sources"]
    autofill_path = _repo_path(source_cfg["autofill_preregistration_path"])
    _verify_pinned(
        autofill_path,
        source_cfg["autofill_preregistration_sha256"],
        "autofill preregistration",
    )
    autofill = _read_json(autofill_path)
    references, reference_audits = load_reference_profiles(autofill)
    if sum(int(row["holdout_ohlcv_rows_materialized"]) for row in reference_audits) != 0:
        raise AssertionError("reference loading touched holdout")

    by_source: dict[str, list[PositiveEvent]] = defaultdict(list)
    for event in positives:
        by_source[event.source_path].append(event)
    negative_events: list[NegativeEvent] = []
    source_audits: list[dict[str, Any]] = []
    holdout_rows = 0
    for number, (source_path, source_positives) in enumerate(sorted(by_source.items()), 1):
        frame, source_audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=HOLDOUT_START
        )
        holdout_rows += int(source_audit["holdout_ohlcv_rows_materialized"])
        enriched = add_candidate_features(frame)
        venue = source_positives[0].venue
        if venue == "binance_um":
            protected = binance_guards.get(source_path, [])
            protected_ids = {
                (str(row["direction"]), int(row["source_core_end_i"])) for row in protected
            }
            missing = [
                event.event_id
                for event in source_positives
                if (event.direction, event.core_end_i) not in protected_ids
            ]
            scan_counts: dict[str, Any] = {"loaded_binance_weak_candidates": len(protected)}
        elif venue == "okx":
            protected, scan_counts = scan_source(
                frame,
                source_path=source_path,
                symbol=source_positives[0].symbol,
                prereg=autofill,
                references=references,
            )
            protected_ids = {
                (str(row["direction"]), int(row["source_core_end_i"])) for row in protected
            }
            missing = [
                event.event_id
                for event in source_positives
                if (event.direction, event.core_end_i) not in protected_ids
            ]
        else:
            raise GradeANegativeError(f"unknown venue: {venue}")
        if missing:
            raise GradeANegativeError(
                f"selected positives missing from protected candidate universe: {missing[:3]}"
            )
        selected, audit = select_source_negative_events(
            enriched,
            source_path=source_path,
            positives=source_positives,
            protected_candidates=protected,
            prereg=prereg,
        )
        negative_events.extend(selected)
        source_audits.append(
            {
                **audit,
                "rows_materialized": int(source_audit["rows_materialized"]),
                "holdout_ohlcv_rows_materialized": int(
                    source_audit["holdout_ohlcv_rows_materialized"]
                ),
                "candidate_guard_audit": scan_counts,
            }
        )
        if number == 1 or number % 25 == 0 or number == len(by_source):
            print(
                f"negative plan {number:03d}/{len(by_source)} "
                f"{source_positives[0].symbol:<24} pos_events={len(source_positives):>2} "
                f"neg_events={len(negative_events):>4}",
                flush=True,
            )
    if holdout_rows != 0:
        raise AssertionError("negative planning materialized holdout OHLCV")
    expected_events = int(prereg["negative_sampling"]["target_negative_events"])
    expected_images = int(prereg["negative_sampling"]["target_negative_images"])
    if len(negative_events) != expected_events:
        raise GradeANegativeError("negative event target drift")
    if sum(len(event.variants) for event in negative_events) != expected_images:
        raise GradeANegativeError("negative image target drift")
    by_pair = Counter(event.paired_positive_event_id for event in negative_events)
    if set(by_pair) != {event.event_id for event in positives} or set(by_pair.values()) != {3}:
        raise GradeANegativeError("negative-event pairing multiplicity drift")
    hard_events = sum(event.negative_kind == "hard" for event in negative_events)
    if hard_events / len(negative_events) < 2 / 3:
        raise GradeANegativeError("hard-negative share fell below two thirds")

    results_path.mkdir(parents=True, exist_ok=True)
    _write_jsonl(plan_path, (asdict(event) for event in negative_events))
    _write_jsonl(audit_path, source_audits)
    receipt = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "positive_images": len(positive_rows),
        "positive_events": len(positives),
        "negative_images": expected_images,
        "negative_events": len(negative_events),
        "negative_event_kinds": dict(Counter(event.negative_kind for event in negative_events)),
        "negative_image_kinds": dict(
            Counter(
                event.negative_kind
                for event in negative_events
                for _variant in event.variants
            )
        ),
        "fallbacks": dict(
            Counter(
                f"{event.requested_kind}_to_{event.negative_kind}"
                for event in negative_events
                if event.requested_kind != event.negative_kind
            )
        ),
        "sources": len(by_source),
        "venues": dict(Counter(event.venue for event in positives)),
        "protected_candidates": sum(int(row["protected_candidates"]) for row in source_audits),
        "legacy_negative_compatibility": legacy_audit,
        "negative_event_plan_sha256": sha256_file(plan_path),
        "negative_source_audit_sha256": sha256_file(audit_path),
        "holdout_ohlcv_rows_materialized": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    _write_json(receipt_path, receipt)
    return receipt


def _negative_event_from_dict(row: Mapping[str, Any]) -> NegativeEvent:
    payload = dict(row)
    payload["variants"] = tuple(tuple(value) for value in payload["variants"])
    return NegativeEvent(**payload)


def nuisance_key(row: Mapping[str, Any]) -> tuple[str, str, str, int, int, int, int]:
    """Return the label-independent matching coordinates used by QA."""

    return (
        str(row["venue"]),
        str(row["symbol"]),
        str(row["time_block"]),
        int(row["core_bars"]),
        int(row["pre_bars"]),
        int(row["post_bars"]),
        int(row["window_bars"]),
    )


def _write_image_and_label(
    building: Path, *, split: str, stem: str, image: np.ndarray, label: bytes
) -> tuple[str, str, str, str]:
    image_rel = Path("images") / split / f"{stem}.png"
    label_rel = Path("labels") / split / f"{stem}.txt"
    image_path, label_path = building / image_rel, building / label_rel
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = encode_png(image)
    temporary = image_path.with_suffix(".png.part")
    temporary.write_bytes(image_bytes)
    os.replace(temporary, image_path)
    label_path.write_bytes(label)
    return (
        str(image_rel),
        str(label_rel),
        hashlib.sha256(image_bytes).hexdigest(),
        hashlib.sha256(label).hexdigest(),
    )


def _copy_positive_rows(
    rows: Sequence[Mapping[str, Any]], *, source_dataset: Path, building: Path
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        image_source = source_dataset / str(row["image_path"])
        label_source = source_dataset / str(row["label_path"])
        image_rel = Path(str(row["image_path"]))
        label_rel = Path(str(row["label_path"]))
        image_target, label_target = building / image_rel, building / label_rel
        image_target.parent.mkdir(parents=True, exist_ok=True)
        label_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image_source, image_target)
        shutil.copyfile(label_source, label_target)
        if sha256_file(image_target) != str(row["image_sha256"]):
            raise GradeANegativeError("positive image byte parity failed")
        if sha256_file(label_target) != str(row["label_sha256"]):
            raise GradeANegativeError("positive label byte parity failed")
        copied.append(
            {
                **dict(row),
                "sample_kind": "positive",
                "source_positive_dataset": _relative(source_dataset),
                "training_eligible": False,
                "production_eligible": False,
            }
        )
        if number % 2000 == 0 or number == len(rows):
            print(f"positive byte copy {number:>4}/{len(rows)}", flush=True)
    return copied


def _render_negative_rows(
    events: Sequence[NegativeEvent], *, building: Path
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_source: dict[str, list[NegativeEvent]] = defaultdict(list)
    for event in events:
        by_source[event.source_path].append(event)
    image_order = 0
    for source_number, (source_path, source_events) in enumerate(sorted(by_source.items()), 1):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=HOLDOUT_START
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("negative render touched holdout")
        enriched = add_candidate_features(frame)
        for event in sorted(
            source_events, key=lambda value: (value.paired_positive_event_order, value.pair_slot)
        ):
            for variant_id, variant_index, pre_bars, post_bars in event.variants:
                image_order += 1
                window_start = event.core_start_i - int(pre_bars)
                window_end = event.core_end_i + int(post_bars)
                window = enriched.iloc[window_start : window_end + 1].reset_index(drop=True)
                expected = int(pre_bars) + event.core_bars + int(post_bars)
                if len(window) != expected or expected not in {18, 19}:
                    raise GradeANegativeError("negative render geometry drift")
                image, _transform = render_chart(
                    window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None
                )
                if int(np.all(image == EXACT_OVERLAY_RED, axis=2).sum()) != 0:
                    raise GradeANegativeError("negative input contains exact overlay red")
                stem = (
                    f"N{event.paired_positive_event_order:05d}_s{event.pair_slot}_"
                    f"{event.negative_kind[0]}_{variant_id}_{event.negative_event_id}"
                )
                image_rel, label_rel, image_sha, label_sha = _write_image_and_label(
                    building,
                    split=event.split,
                    stem=stem,
                    image=image,
                    label=b"",
                )
                rows.append(
                    {
                        **{
                            key: value
                            for key, value in asdict(event).items()
                            if key != "variants"
                        },
                        "sample_kind": "negative",
                        "dataset_sample_id": stable_id(
                            event.negative_event_id, variant_id, variant_index
                        ),
                        "variant_id": variant_id,
                        "variant_index": int(variant_index),
                        "pre_bars": int(pre_bars),
                        "post_bars": int(post_bars),
                        "window_bars": expected,
                        "window_start_i": window_start,
                        "window_end_i": window_end,
                        "window_start_time": pd.Timestamp(
                            enriched.iloc[window_start]["open_time"]
                        ).isoformat(),
                        "window_end_time": pd.Timestamp(
                            enriched.iloc[window_end]["open_time"]
                        ).isoformat(),
                        "image_path": image_rel,
                        "label_path": label_rel,
                        "image_sha256": image_sha,
                        "label_sha256": label_sha,
                        "class_id": None,
                        "class_name": None,
                        "boxes_per_image": 0,
                        "annotation_drawn_into_image": False,
                        "exact_overlay_red_pixels": 0,
                        "image_width": SOURCE_WIDTH,
                        "image_height": SOURCE_HEIGHT,
                        "training_eligible": False,
                        "production_eligible": False,
                    }
                )
        if source_number == 1 or source_number % 25 == 0 or source_number == len(by_source):
            print(
                f"negative render {source_number:03d}/{len(by_source)} rows={len(rows):>5}",
                flush=True,
            )
    return rows


def _full_qa(
    dataset: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    positive_source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(rows) != 32000:
        raise GradeANegativeError("full dataset row target drift")
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]
    if len(positives) != 8000 or len(negatives) != 24000:
        raise GradeANegativeError("positive/negative count drift")
    source_by_id = {str(row["dataset_sample_id"]): row for row in positive_source_rows}
    image_shas: set[str] = set()
    split_counts: Counter[str] = Counter()
    for number, row in enumerate(rows, 1):
        image_path = dataset / str(row["image_path"])
        label_path = dataset / str(row["label_path"])
        if sha256_file(image_path) != str(row["image_sha256"]):
            raise GradeANegativeError("dataset image SHA drift")
        if sha256_file(label_path) != str(row["label_sha256"]):
            raise GradeANegativeError("dataset label SHA drift")
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (SOURCE_HEIGHT, SOURCE_WIDTH):
            raise GradeANegativeError("dataset image decode/dimension drift")
        if int(np.all(image == EXACT_OVERLAY_RED, axis=2).sum()) != 0:
            raise GradeANegativeError("dataset image contains exact overlay red")
        image_shas.add(str(row["image_sha256"]))
        split_counts[f"{row['split']}/{row['sample_kind']}"] += 1
        if row["sample_kind"] == "negative" and label_path.read_bytes() != b"":
            raise GradeANegativeError("negative label is not byte-empty")
        if row["sample_kind"] == "positive":
            original = source_by_id[str(row["dataset_sample_id"])]
            if str(row["image_sha256"]) != str(original["image_sha256"]):
                raise GradeANegativeError("positive image lineage drift")
            if str(row["label_sha256"]) != str(original["label_sha256"]):
                raise GradeANegativeError("positive label lineage drift")
        if number % 8000 == 0 or number == len(rows):
            print(f"full QA {number:>5}/{len(rows)}", flush=True)
    if len(image_shas) != len(rows):
        raise GradeANegativeError("duplicate model-input pixels exist")

    positive_nuisance = Counter(nuisance_key(row) for row in positives)
    negative_nuisance = Counter(nuisance_key(row) for row in negatives)
    expected_negative = Counter({key: value * 3 for key, value in positive_nuisance.items()})
    if negative_nuisance != expected_negative:
        raise GradeANegativeError("positive/negative nuisance distribution mismatch")
    pair_variants = Counter(
        (str(row["paired_positive_event_id"]), str(row["variant_id"])) for row in negatives
    )
    expected_pair_variants = {
        (str(row["event_id"]), str(row["variant_id"])) for row in positives
    }
    if set(pair_variants) != expected_pair_variants or set(pair_variants.values()) != {3}:
        raise GradeANegativeError("each positive event-position needs exactly three negatives")
    event_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        identity = (
            str(row["event_id"])
            if row["sample_kind"] == "positive"
            else str(row["negative_event_id"])
        )
        event_splits[identity].add(str(row["split"]))
    if any(len(values) != 1 for values in event_splits.values()):
        raise GradeANegativeError("one event crosses train/val")
    return {
        "passed": True,
        "rows": len(rows),
        "images_decoded": len(rows),
        "unique_image_hashes": len(image_shas),
        "positive_byte_parity": len(positives),
        "negative_empty_labels": len(negatives),
        "split_counts": dict(split_counts),
        "nuisance_distribution_exact_3x": True,
        "positive_event_position_negative_multiplicity": 3,
        "same_event_single_split": True,
        "exact_overlay_red_pixels": 0,
    }


def _write_preview_html(
    path: Path, *, rows: Sequence[Mapping[str, Any]], dataset: Path
) -> None:
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]
    indices = np.linspace(0, len(positives) - 1, 50, dtype=int)
    selected_positive = [positives[int(index)] for index in indices]
    by_pair: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in negatives:
        by_pair[(str(row["paired_positive_event_id"]), str(row["variant_id"]))].append(row)
    cards: list[str] = []
    for positive in selected_positive:
        key = (str(positive["event_id"]), str(positive["variant_id"]))
        group = [positive, *sorted(by_pair[key], key=lambda row: int(row["pair_slot"]))]
        panes = []
        for row in group:
            image_path = dataset / str(row["image_path"])
            href = os.path.relpath(image_path, path.parent).replace(os.sep, "/")
            box = row.get("box") if row["sample_kind"] == "positive" else None
            overlay = ""
            if box:
                left = 100 * (float(box["cx_norm"]) - float(box["w_norm"]) / 2)
                top = 100 * (float(box["cy_norm"]) - float(box["h_norm"]) / 2)
                overlay = (
                    f"<span style='left:{left:.5f}%;top:{top:.5f}%;"
                    f"width:{100*float(box['w_norm']):.5f}%;height:{100*float(box['h_norm']):.5f}%'></span>"
                )
            label = "POS" if row["sample_kind"] == "positive" else f"NEG {row['negative_kind']}"
            panes.append(
                f"<div><div class='chart'><img src='{html.escape(href)}'>{overlay}</div>"
                f"<small>{html.escape(label)} · {html.escape(str(row['venue']))} · "
                f"PRE {int(row['pre_bars'])}/POST {int(row['post_bars'])}</small></div>"
            )
        cards.append(
            f"<article><h3>{html.escape(str(positive['symbol']))} "
            f"{html.escape(str(positive['direction']))} · {html.escape(str(positive['core_end_time']))}</h3>"
            f"<div class='row'>{''.join(panes)}</div></article>"
        )
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{font-family:-apple-system,sans-serif;background:#eee;margin:16px}"
        "article{background:#fff;padding:12px;margin:0 0 16px;border-radius:10px}"
        ".row{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.chart{position:relative}"
        ".chart img{width:100%;display:block}.chart span{position:absolute;border:3px solid red;box-sizing:border-box}"
        "small{display:block;margin-top:5px;color:#444}@media(max-width:900px){.row{grid-template-columns:1fr}}"
        "</style></head><body><h1>模型实际输入：1 正 + 3 匹配负样本（50组）</h1>"
        "<p>PNG 均为 1280×742 无损模型输入；仅正样本的红框由 HTML 临时叠加。</p>"
        f"{''.join(cards)}</body></html>",
        encoding="utf-8",
    )


def build_dataset(
    *,
    prereg_path: Path = DEFAULT_PREREG,
    results_path: Path = DEFAULT_RESULTS,
    dataset_path: Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    """Materialize 8,000 byte-identical positives plus 24,000 matched negatives."""

    prereg_path = prereg_path.resolve()
    results_path = results_path.resolve()
    dataset_path = dataset_path.resolve()
    builder_commit = _verify_builder_committed((MODULE_PATH, SCRIPT_PATH, prereg_path))
    prereg = load_preregistration(prereg_path)
    plan_path, _audit_path, plan_receipt_path = _plan_paths(results_path)
    if not plan_path.exists() or not plan_receipt_path.exists():
        plan_dataset(prereg_path=prereg_path, results_path=results_path)
    plan_receipt = _read_json(plan_receipt_path)
    if str(plan_receipt["builder_commit"]) != builder_commit:
        raise GradeANegativeError("negative plan builder commit drift")
    if sha256_file(plan_path) != str(plan_receipt["negative_event_plan_sha256"]):
        raise GradeANegativeError("negative event plan SHA drift")
    positive_rows = load_positive_rows(prereg)
    negative_events = [_negative_event_from_dict(row) for row in _read_jsonl(plan_path)]
    if dataset_path.exists():
        raise FileExistsError(f"refusing to overwrite dataset: {dataset_path}")
    building = dataset_path.with_name(dataset_path.name + ".building")
    if building.exists():
        raise FileExistsError(f"refusing to overwrite partial dataset: {building}")
    building.mkdir(parents=True)

    source_dataset = _repo_path(prereg["positive_source"]["dataset_dir"])
    copied_positives = _copy_positive_rows(
        positive_rows, source_dataset=source_dataset, building=building
    )
    rendered_negatives = _render_negative_rows(negative_events, building=building)
    rows = sorted(
        [*copied_positives, *rendered_negatives],
        key=lambda row: (
            str(row["split"]),
            0 if row["sample_kind"] == "positive" else 1,
            int(row.get("image_order", row.get("paired_positive_event_order", 0))),
            int(row.get("pair_slot", 0)),
            int(row.get("variant_index", 0)),
        ),
    )
    _write_jsonl(building / "manifest.jsonl", rows)
    (building / "data.yaml").write_text(
        f"path: {dataset_path}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_long\n"
        "  1: dense_short\n",
        encoding="utf-8",
    )
    os.replace(building, dataset_path)
    final_rows = _read_jsonl(dataset_path / "manifest.jsonl")
    full_qa = _full_qa(
        dataset_path, final_rows, positive_source_rows=positive_rows
    )
    preview_path = results_path / "actual_model_inputs_matched_sample50.html"
    _write_preview_html(preview_path, rows=final_rows, dataset=dataset_path)
    summary = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "dataset_path": _relative(dataset_path),
        "positive_images": 8000,
        "positive_events": 1043,
        "negative_images": 24000,
        "negative_events": 3129,
        "negative_event_kinds": plan_receipt["negative_event_kinds"],
        "negative_image_kinds": plan_receipt["negative_image_kinds"],
        "fallbacks": plan_receipt["fallbacks"],
        "counts": full_qa["split_counts"],
        "positive_image_and_label_byte_parity": 8000,
        "nuisance_distribution_exact_3x": True,
        "manifest_sha256": sha256_file(dataset_path / "manifest.jsonl"),
        "data_yaml_sha256": sha256_file(dataset_path / "data.yaml"),
        "preview_html": _relative(preview_path),
        "preview_html_sha256": sha256_file(preview_path),
        "full_qa": full_qa,
        "holdout_ohlcv_rows_materialized": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    _write_json(dataset_path / "build_summary.json", summary)
    _write_json(results_path / "dataset_build_receipt.json", summary)
    return summary
