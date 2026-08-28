"""Build 8,000 strict Grade-A 15m MA-launch positive images.

Grade-A is the exact frozen ``PERFECT_CANDIDATE`` contract from
``ma_launch_owner_perfect_filter``.  Capacity is expanded with official
Binance USD-M perpetual 15m archives; no gate or score is relaxed.  Each
independent event contributes seven or eight *different* 18/19-candle render
windows.  Every variant of an event is forced into the same chronological
split, so crop diversity cannot leak an event across train and validation.

The builder writes clean 1280x742 PNG model inputs and one YOLO label per
image.  Review boxes are CSS overlays in the HTML gallery and are never
painted into model inputs.  It does not train, read holdout OHLCV, promote a
model, alter ACTIVE/frozen, touch forward state, or place orders.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.data.binance_um_archives import sha256_file
from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
    read_preholdout_prefix,
)
from yoyo.datasets.ma_launch_owner_autofill10000 import (
    event_nms,
    load_reference_profiles,
    scan_source,
)
from yoyo.datasets.ma_launch_owner_perfect_filter import (
    PerfectFilterError,
    _load_pinned_rows,
    _profile_key,
    _score_all,
    extract_profile,
)
from yoyo.datasets.ma_launch_owner_recrop_review import (
    HOLDOUT_START,
    RED,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    core_box,
    encode_png,
    verify_builder_committed,
)
from yoyo.datasets.ma_launch_owner_yolo_dataset import interval_split
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = DEFAULT_PREREG.parent / "results"
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_v1"


class GradeA8000Error(RuntimeError):
    """Raised when source, score, geometry, split, or output contracts drift."""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _repo_path(value: object) -> Path:
    path = Path(str(value))
    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise GradeA8000Error(f"path escapes repository: {value}") from exc
    return path


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _stable_id(*parts: object, length: int = 24) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _time_block(value: object) -> str:
    stamp = pd.Timestamp(value)
    return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"


def _validate_prereg(prereg: Mapping[str, Any]) -> None:
    if str(prereg.get("experiment_id")) != EXPERIMENT_ID:
        raise GradeA8000Error("experiment ID drift")
    if pd.Timestamp(prereg["scope"]["holdout_start_exclusive"]) != HOLDOUT_START:
        raise GradeA8000Error("repository holdout boundary drift")
    ceiling = pd.Timestamp(prereg["scope"]["archive_max_exclusive"])
    if ceiling > HOLDOUT_START:
        raise GradeA8000Error("archive ceiling crosses holdout")
    output = prereg["output"]
    if int(output["target_images"]) != 8_000:
        raise GradeA8000Error("target image count drift")
    windows = [tuple(map(int, pair)) for pair in output["window_pre_post_pairs"]]
    maximum_variants = int(output["maximum_variants_per_event"])
    minimum_variants = int(output["minimum_variants_per_event"])
    if len(windows) != maximum_variants or len(set(windows)) != maximum_variants:
        raise GradeA8000Error("render variants must be unique and match the maximum")
    if not 1 <= minimum_variants <= maximum_variants:
        raise GradeA8000Error("invalid per-event variant bounds")
    if any(pre + post != 14 for pre, post in windows):
        raise GradeA8000Error("each render variant must preserve 18/19 total bars")
    if min(pre for pre, _ in windows) < 5 or min(post for _, post in windows) < 2:
        raise GradeA8000Error("render context is too short")
    safety = prereg["safety"]
    forbidden = (
        "start_training",
        "read_holdout",
        "training_eligible",
        "production_eligible",
        "active_or_frozen_change",
        "forward_or_order_state_change",
    )
    if any(safety.get(name) is not False for name in forbidden):
        raise GradeA8000Error("one or more safety switches are not false")


def _verify_pinned(path: Path, expected_sha: object) -> None:
    if sha256_file(path) != str(expected_sha):
        raise GradeA8000Error(f"pinned input SHA drift: {path}")


def _source_specs(prereg: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = prereg["sources"]["binance_um"]
    summary_path = _repo_path(source["fetch_summary_path"])
    if not summary_path.exists():
        raise GradeA8000Error(
            "Binance fetch summary is missing; run fetch_binance_um_preholdout_15m.py"
        )
    summary = read_json(summary_path)
    if int(summary.get("holdout_ohlcv_rows_materialized", -1)) != 0:
        raise GradeA8000Error("fetch summary does not prove zero holdout rows")
    if pd.Timestamp(summary["archive_max_exclusive"]) != pd.Timestamp(
        prereg["scope"]["archive_max_exclusive"]
    ):
        raise GradeA8000Error("fetch archive ceiling drift")
    specs: list[dict[str, Any]] = []
    for row in summary["results"]:
        if row["status"] != "complete":
            continue
        path = _repo_path(row["output_path"])
        if sha256_file(path) != str(row["output_sha256"]):
            raise GradeA8000Error(f"Binance source SHA drift: {path}")
        if pd.Timestamp(row["last_time"]) >= pd.Timestamp(
            prereg["scope"]["archive_max_exclusive"]
        ):
            raise GradeA8000Error(f"Binance source crosses archive ceiling: {path}")
        specs.append(
            {
                "venue": "binance_um",
                "exchange_symbol": str(row["symbol"]),
                "symbol": f"{str(row['symbol']).removesuffix('USDT')}_USDT_SWAP",
                "source_path": _relative(path),
                "source_sha256": str(row["output_sha256"]),
                "rows": int(row["rows"]),
                "first_time": str(row["first_time"]),
                "last_time": str(row["last_time"]),
            }
        )
    specs.sort(key=lambda row: str(row["exchange_symbol"]))
    if not specs:
        raise GradeA8000Error("Binance fetch has no complete sources")
    return specs, summary


def _load_exact_contract(
    prereg: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    sources = prereg["sources"]
    autofill_path = _repo_path(sources["autofill_preregistration_path"])
    perfect_path = _repo_path(sources["perfect_preregistration_path"])
    _verify_pinned(autofill_path, sources["autofill_preregistration_sha256"])
    _verify_pinned(perfect_path, sources["perfect_preregistration_sha256"])
    autofill = read_json(autofill_path)
    perfect = read_json(perfect_path)
    scan_references, audits = load_reference_profiles(autofill)
    if sum(int(row["holdout_ohlcv_rows_materialized"]) for row in audits) != 0:
        raise GradeA8000Error("reference loader materialized holdout")
    _, references, accepted_family, _ = _load_pinned_rows(perfect)
    return autofill, perfect, scan_references, references, accepted_family


def _scan_one(
    spec: Mapping[str, Any],
    *,
    autofill: Mapping[str, Any],
    scan_references: Sequence[Any],
    archive_ceiling: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _repo_path(spec["source_path"])
    frame, audit = read_preholdout_prefix(path, end_exclusive=archive_ceiling)
    if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
        raise GradeA8000Error("Binance scan materialized holdout")
    if len(frame) != int(spec["rows"]):
        raise GradeA8000Error(f"Binance source row drift: {path}")
    rows, counts = scan_source(
        frame,
        source_path=str(spec["source_path"]),
        symbol=str(spec["symbol"]),
        prereg=autofill,
        references=scan_references,
    )
    for row in rows:
        row.update(
            {
                "venue": "binance_um",
                "exchange_symbol": str(spec["exchange_symbol"]),
                "sample_id": _stable_id(
                    EXPERIMENT_ID,
                    "binance_um",
                    row["symbol"],
                    row["direction"],
                    row["core_start_time"],
                    row["core_end_time"],
                    row["core_bars"],
                ),
                "box": {"h_norm": 0.0},
                "time_block": _time_block(row["core_end_time"]),
                "training_data_yaml_exposed": True,
                "training_eligible": False,
                "production_eligible": False,
            }
        )
    audit.update(
        {
            "venue": "binance_um",
            "symbol": str(spec["symbol"]),
            "exchange_symbol": str(spec["exchange_symbol"]),
            "source_sha256": str(spec["source_sha256"]),
            "candidate_counts": counts,
            "candidates": len(rows),
        }
    )
    return rows, audit


def _scan_binance(
    prereg: Mapping[str, Any],
    *,
    specs: Sequence[Mapping[str, Any]],
    autofill: Mapping[str, Any],
    scan_references: Sequence[Any],
    building: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scan_dir = building / "source_scans"
    scan_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    ceiling = pd.Timestamp(prereg["scope"]["archive_max_exclusive"])
    for index, spec in enumerate(specs, 1):
        stem = str(spec["exchange_symbol"])
        rows_path = scan_dir / f"{stem}.jsonl"
        audit_path = scan_dir / f"{stem}.json"
        if rows_path.exists() and audit_path.exists():
            audit = read_json(audit_path)
            if str(audit.get("source_sha256")) != str(spec["source_sha256"]):
                raise GradeA8000Error(f"resumed scan source SHA drift: {stem}")
            rows = read_jsonl(rows_path)
        else:
            rows, audit = _scan_one(
                spec,
                autofill=autofill,
                scan_references=scan_references,
                archive_ceiling=ceiling,
            )
            write_jsonl(rows_path, rows)
            write_json(audit_path, audit)
        all_rows.extend(rows)
        audits.append(audit)
        counts.update(audit["candidate_counts"])
        counts["source_rows_materialized"] += int(audit["rows_materialized"])
        if index == 1 or index % 20 == 0 or index == len(specs):
            print(
                f"grade-a scan {index:03d}/{len(specs):03d} {stem:<18} "
                f"weak={len(all_rows):>6}",
                flush=True,
            )
    nms = event_nms(
        all_rows,
        gap_bars=int(autofill["scan"]["same_symbol_direction_nms_bars"]),
    )
    receipt = {
        "sources": len(specs),
        "source_rows_materialized": int(counts["source_rows_materialized"]),
        "holdout_ohlcv_rows_materialized": 0,
        "profile_gate_counts": dict(counts),
        "n_before_one_hour_nms": len(all_rows),
        "n_after_one_hour_nms": len(nms),
        "direction_after_one_hour_nms": dict(Counter(str(row["direction"]) for row in nms)),
    }
    write_jsonl(building / "binance_weak_after_nms.jsonl", nms)
    write_jsonl(building / "binance_source_audit.jsonl", audits)
    write_json(building / "scan_receipt.json", receipt)
    return nms, audits, receipt


def _reference_profiles(
    references: Sequence[Mapping[str, Any]],
    accepted_family: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in (*references, *accepted_family):
        grouped[str(row["source_path"])].append(row)
    profiles: dict[str, Any] = {}
    for source_path, rows in grouped.items():
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=HOLDOUT_START
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise GradeA8000Error("score reference materialized holdout")
        enriched = add_candidate_features(frame)
        for row in rows:
            key = _profile_key(row)
            if key not in profiles:
                profiles[key] = extract_profile(enriched, row)
    return profiles


def extract_scorable_candidate_profiles(
    enriched: pd.DataFrame,
    rows: Sequence[Mapping[str, Any]],
    profiles: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract candidate profiles while auditing data-invalid windows.

    Reference profiles remain fail-closed. Candidate windows that cannot meet
    the profile's finite-data, continuity, ATR, direction, or boundary
    contract are explicit rejects rather than fatal errors. This only removes
    candidates; it cannot turn a failed candidate into Grade-A.
    """

    scorable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        key = _profile_key(row)
        try:
            profiles[key] = extract_profile(enriched, row)
        except PerfectFilterError as exc:
            rejected.append(
                {
                    **dict(row),
                    "profile_reject_reason": str(exc),
                    "training_eligible": False,
                    "production_eligible": False,
                }
            )
            continue
        scorable.append(dict(row))
    return scorable, rejected


def _score_binance(
    prereg: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    references: Sequence[Mapping[str, Any]],
    accepted_family: Sequence[Mapping[str, Any]],
    perfect_prereg: Mapping[str, Any],
    building: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scored_path = building / "binance_scored.jsonl"
    calibration_path = building / "calibration.json"
    rejected_path = building / "binance_profile_rejected.jsonl"
    if scored_path.exists() and calibration_path.exists():
        return read_jsonl(scored_path), read_json(calibration_path)
    profiles = _reference_profiles(references, accepted_family)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[str(row["source_path"])].append(row)
    ceiling = pd.Timestamp(prereg["scope"]["archive_max_exclusive"])
    scorable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, (source_path, rows) in enumerate(sorted(grouped.items()), 1):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=ceiling
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise GradeA8000Error("score source materialized holdout")
        enriched = add_candidate_features(frame)
        source_scorable, source_rejected = extract_scorable_candidate_profiles(
            enriched, rows, profiles
        )
        scorable.extend(source_scorable)
        rejected.extend(source_rejected)
        if index == 1 or index % 20 == 0 or index == len(grouped):
            print(
                f"grade-a profile {index:03d}/{len(grouped):03d} "
                f"profiles={len(profiles):>6}",
                flush=True,
            )
    scored, calibration = _score_all(
        scorable,
        references,
        accepted_family,
        profiles,
        perfect_prereg,
    )
    calibration = {
        **calibration,
        "candidate_profile_scorable_count": len(scorable),
        "candidate_profile_rejected_count": len(rejected),
        "candidate_profile_reject_reasons": dict(
            sorted(Counter(row["profile_reject_reason"] for row in rejected).items())
        ),
    }
    write_jsonl(scored_path, scored)
    write_jsonl(rejected_path, rejected)
    write_json(calibration_path, calibration)
    return scored, calibration


def cross_venue_event_nms(
    rows: Sequence[Mapping[str, Any]], *, gap_minutes: int
) -> list[dict[str, Any]]:
    """Greedily keep the best event across venues within a four-hour cluster."""

    gap = pd.Timedelta(minutes=int(gap_minutes))
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["symbol"]), str(row["direction"]))].append(dict(row))
    kept: list[dict[str, Any]] = []
    for group in grouped.values():
        winners: list[dict[str, Any]] = []
        for row in sorted(
            group,
            key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
        ):
            stamp = pd.Timestamp(row["core_end_time"])
            if any(abs(stamp - pd.Timestamp(prior["core_end_time"])) <= gap for prior in winners):
                continue
            winners.append(row)
        kept.extend(winners)
    return kept


def cap_and_order_events(
    rows: Sequence[Mapping[str, Any]],
    *,
    per_symbol_direction: int,
    per_symbol_direction_time_block: int,
) -> list[dict[str, Any]]:
    """Apply diversity caps, then round-robin direction and half-year blocks."""

    by_block: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        row["time_block"] = _time_block(row["core_end_time"])
        by_block[(str(row["symbol"]), str(row["direction"]), row["time_block"])].append(row)
    block_kept: list[dict[str, Any]] = []
    for group in by_block.values():
        block_kept.extend(
            sorted(
                group,
                key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
            )[: int(per_symbol_direction_time_block)]
        )
    by_symbol: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in block_kept:
        by_symbol[(str(row["symbol"]), str(row["direction"]))].append(row)
    capped: list[dict[str, Any]] = []
    for group in by_symbol.values():
        capped.extend(
            sorted(
                group,
                key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
            )[: int(per_symbol_direction)]
        )
    queues: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in capped:
        queues[(str(row["direction"]), str(row["time_block"]))].append(row)
    for group in queues.values():
        group.sort(
            key=lambda value: (-float(value["quality_score"]), str(value["sample_id"]))
        )
    ordered: list[dict[str, Any]] = []
    while any(queues.values()):
        for key in sorted(queues):
            if queues[key]:
                ordered.append(queues[key].pop(0))
    return ordered


def _assign_split(row: Mapping[str, Any], prereg: Mapping[str, Any]) -> str:
    windows = [tuple(map(int, pair)) for pair in prereg["output"]["window_pre_post_pairs"]]
    dependency_start = pd.Timestamp(row["core_start_time"]) - pd.Timedelta(
        minutes=15 * max(pre for pre, _ in windows)
    )
    dependency_end = pd.Timestamp(row["core_end_time"]) + pd.Timedelta(
        minutes=15 * max(5, max(post for _, post in windows))
    )
    split = interval_split(
        dependency_start,
        dependency_end,
        cutoff=prereg["split"]["cutoff"],
        purge_bars=int(prereg["split"]["purge_bars_each_side"]),
        bar_minutes=15,
    )
    return split


def _variant_geometry(
    enriched: pd.DataFrame,
    row: Mapping[str, Any],
    *,
    pre_bars: int,
    post_bars: int,
) -> dict[str, Any] | None:
    core_start = int(row["source_core_start_i"])
    core_end = int(row["source_core_end_i"])
    start = core_start - int(pre_bars)
    end = core_end + int(post_bars)
    if start < 0 or end >= len(enriched):
        return None
    window = enriched.iloc[start : end + 1].reset_index(drop=True)
    expected = int(pre_bars) + int(row["core_bars"]) + int(post_bars)
    if len(window) != expected:
        return None
    times = pd.to_datetime(window["open_time"], utc=True)
    if not (times.diff().iloc[1:] == pd.Timedelta(minutes=15)).all():
        return None
    transform = make_chart_transform(window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT)
    box = core_box(
        transform,
        window,
        start_local=int(pre_bars),
        end_local=int(pre_bars) + int(row["core_bars"]) - 1,
    )
    return {
        "pre_bars": int(pre_bars),
        "post_bars": int(post_bars),
        "window_start_i": start,
        "window_end_i": end,
        "window_start_time": times.iloc[0].isoformat(),
        "window_end_time": times.iloc[-1].isoformat(),
        "window_bars": len(window),
        "box": box,
    }


def _preflight_variants(
    prereg: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    windows = [tuple(map(int, pair)) for pair in prereg["output"]["window_pre_post_pairs"]]
    max_height = float(prereg["output"]["max_box_height_norm"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        row["split"] = _assign_split(row, prereg)
        if row["split"] in {"train", "val"}:
            grouped[str(row["source_path"])].append(row)
    ceiling = pd.Timestamp(prereg["scope"]["archive_max_exclusive"])
    output: list[dict[str, Any]] = []
    for index, (source_path, source_rows) in enumerate(sorted(grouped.items()), 1):
        boundary = ceiling if all(str(row["venue"]) == "binance_um" for row in source_rows) else HOLDOUT_START
        frame, audit = read_preholdout_prefix(_repo_path(source_path), end_exclusive=boundary)
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise GradeA8000Error("variant preflight materialized holdout")
        enriched = add_candidate_features(frame)
        for row in source_rows:
            variants: list[dict[str, Any]] = []
            for variant_index, (pre_bars, post_bars) in enumerate(windows, 1):
                geometry = _variant_geometry(
                    enriched,
                    row,
                    pre_bars=pre_bars,
                    post_bars=post_bars,
                )
                if geometry is None:
                    continue
                if float(geometry["box"]["h_norm"]) > max_height:
                    continue
                geometry["variant_index"] = variant_index
                variants.append(geometry)
            row["valid_variants"] = variants
            if len(variants) >= int(prereg["output"]["minimum_variants_per_event"]):
                output.append(row)
        if index == 1 or index % 20 == 0 or index == len(grouped):
            print(
                f"grade-a geometry {index:03d}/{len(grouped):03d} "
                f"eligible_events={len(output):>5}",
                flush=True,
            )
    return output


def allocate_variants(
    ordered_events: Sequence[Mapping[str, Any]],
    *,
    target_images: int,
    minimum_unique_events: int,
    preferred_unique_events: int,
    minimum_variants_per_event: int = 5,
    maximum_variants_per_event: int = 6,
) -> list[dict[str, Any]]:
    """Allocate an exact image target within frozen per-event variant bounds."""

    minimum_variants = int(minimum_variants_per_event)
    maximum_variants = int(maximum_variants_per_event)
    if not 1 <= minimum_variants <= maximum_variants:
        raise GradeA8000Error("invalid allocation variant bounds")
    eligible = [
        dict(row)
        for row in ordered_events
        if len(row["valid_variants"]) >= minimum_variants
    ]
    if len(eligible) < int(minimum_unique_events):
        raise GradeA8000Error(
            f"only {len(eligible)} events have {minimum_variants} valid variants; "
            f"need {minimum_unique_events}"
        )
    n_events = min(int(preferred_unique_events), len(eligible))
    while n_events >= int(minimum_unique_events):
        selected = eligible[:n_events]
        capacity = sum(
            min(maximum_variants, len(row["valid_variants"])) for row in selected
        )
        if minimum_variants * n_events <= int(target_images) <= capacity:
            break
        n_events -= 1
    else:
        raise GradeA8000Error(
            "event/variant capacity cannot reach target within frozen variant bounds"
        )

    selected = eligible[:n_events]
    plans: list[dict[str, Any]] = []
    extras: list[list[dict[str, Any]]] = []
    for event_order, row in enumerate(selected, 1):
        variants = sorted(
            row["valid_variants"], key=lambda value: int(value["variant_index"])
        )[:maximum_variants]
        if len(variants) < minimum_variants:
            raise AssertionError("eligible event lost variant capacity")
        rotation = (
            int(hashlib.sha256(str(row["sample_id"]).encode()).digest()[0])
            % len(variants)
        )
        rotated = variants[rotation:] + variants[:rotation]
        chosen = rotated[:minimum_variants]
        chosen_ids = {int(value["variant_index"]) for value in chosen}
        extras.append(
            [
                value
                for value in rotated
                if int(value["variant_index"]) not in chosen_ids
            ]
        )
        for value in chosen:
            plans.append(
                {
                    **{key: val for key, val in row.items() if key != "valid_variants"},
                    **value,
                    "event_order": event_order,
                }
            )
    extra_needed = int(target_images) - len(plans)
    for extra_round in range(maximum_variants - minimum_variants):
        for event_order, (row, remaining) in enumerate(zip(selected, extras), 1):
            if extra_needed <= 0:
                break
            if extra_round >= len(remaining):
                continue
            plans.append(
                {
                    **{key: val for key, val in row.items() if key != "valid_variants"},
                    **remaining[extra_round],
                    "event_order": event_order,
                }
            )
            extra_needed -= 1
        if extra_needed <= 0:
            break
    if extra_needed != 0 or len(plans) != int(target_images):
        raise GradeA8000Error("exact 8,000-image allocation failed")
    plans.sort(
        key=lambda row: (
            int(row["event_order"]),
            int(row["variant_index"]),
        )
    )
    for image_order, row in enumerate(plans, 1):
        row["image_order"] = image_order
        row["variant_id"] = f"v{int(row['variant_index'])}"
        row["dataset_sample_id"] = _stable_id(
            EXPERIMENT_ID,
            row["sample_id"],
            row["variant_id"],
        )
    return plans


def _label_text(direction: str, box: Mapping[str, Any]) -> str:
    class_id = 0 if direction == "LONG" else 1
    if direction not in {"LONG", "SHORT"}:
        raise GradeA8000Error(f"unsupported direction: {direction}")
    return (
        f"{class_id} {float(box['cx_norm']):.10f} {float(box['cy_norm']):.10f} "
        f"{float(box['w_norm']):.10f} {float(box['h_norm']):.10f}\n"
    )


def _render_dataset(
    prereg: Mapping[str, Any],
    plans: Sequence[Mapping[str, Any]],
    *,
    dataset_dir: Path,
) -> list[dict[str, Any]]:
    building = dataset_dir.with_name(dataset_dir.name + ".building")
    if dataset_dir.exists():
        raise FileExistsError(f"refusing to overwrite dataset: {dataset_dir}")
    for split in ("train", "val"):
        (building / "images" / split).mkdir(parents=True, exist_ok=True)
        (building / "labels" / split).mkdir(parents=True, exist_ok=True)
    partial_path = building / "manifest.partial.jsonl"
    prior = read_jsonl(partial_path) if partial_path.exists() else []
    by_id = {str(row["dataset_sample_id"]): row for row in prior}
    if len(by_id) != len(prior):
        raise GradeA8000Error("partial render manifest has duplicate IDs")
    for row in prior:
        image_path = building / str(row["image_path"])
        label_path = building / str(row["label_path"])
        if sha256_file(image_path) != str(row["image_sha256"]):
            raise GradeA8000Error("partial image SHA drift")
        if sha256_file(label_path) != str(row["label_sha256"]):
            raise GradeA8000Error("partial label SHA drift")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in plans:
        if str(row["dataset_sample_id"]) not in by_id:
            grouped[str(row["source_path"])].append(row)
    ceiling = pd.Timestamp(prereg["scope"]["archive_max_exclusive"])
    with partial_path.open("a", encoding="utf-8") as handle:
        rendered = len(prior)
        for source_index, (source_path, source_rows) in enumerate(sorted(grouped.items()), 1):
            boundary = ceiling if all(str(row["venue"]) == "binance_um" for row in source_rows) else HOLDOUT_START
            frame, audit = read_preholdout_prefix(_repo_path(source_path), end_exclusive=boundary)
            if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
                raise GradeA8000Error("render materialized holdout")
            enriched = add_candidate_features(frame)
            for plan in sorted(source_rows, key=lambda value: int(value["image_order"])):
                start = int(plan["window_start_i"])
                end = int(plan["window_end_i"])
                window = enriched.iloc[start : end + 1].reset_index(drop=True)
                clean, transform = render_chart(
                    window,
                    width=SOURCE_WIDTH,
                    height=SOURCE_HEIGHT,
                    out_path=None,
                )
                box = core_box(
                    transform,
                    window,
                    start_local=int(plan["pre_bars"]),
                    end_local=int(plan["pre_bars"]) + int(plan["core_bars"]) - 1,
                )
                for key in ("cx_norm", "cy_norm", "w_norm", "h_norm"):
                    if not math.isclose(
                        float(box[key]), float(plan["box"][key]), rel_tol=0.0, abs_tol=1e-12
                    ):
                        raise GradeA8000Error(f"preflight/render box drift: {plan['dataset_sample_id']}")
                split = str(plan["split"])
                stem = (
                    f"A{int(plan['image_order']):05d}_{plan['symbol']}_{plan['direction']}_"
                    f"{plan['dataset_sample_id']}"
                )
                image_rel = Path("images") / split / f"{stem}.png"
                label_rel = Path("labels") / split / f"{stem}.txt"
                image_path = building / image_rel
                label_path = building / label_rel
                image_path.write_bytes(encode_png(clean))
                label_path.write_text(
                    _label_text(str(plan["direction"]), box), encoding="utf-8"
                )
                exact_overlay_red = int(np.all(clean == np.asarray(RED), axis=2).sum())
                if exact_overlay_red:
                    raise GradeA8000Error("clean model input contains overlay-red pixels")
                output = {
                    **{key: value for key, value in plan.items() if key != "box"},
                    "box": box,
                    "image_path": str(image_rel),
                    "label_path": str(label_rel),
                    "image_sha256": sha256_file(image_path),
                    "label_sha256": sha256_file(label_path),
                    "image_width": SOURCE_WIDTH,
                    "image_height": SOURCE_HEIGHT,
                    "boxes_per_image": 1,
                    "annotation_drawn_into_image": False,
                    "exact_overlay_red_pixels": exact_overlay_red,
                    "training_eligible": False,
                    "production_eligible": False,
                }
                handle.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                by_id[str(plan["dataset_sample_id"])] = output
                rendered += 1
                if rendered % 100 == 0:
                    os.fsync(handle.fileno())
                    print(f"grade-a render {rendered:05d}/{len(plans):05d}", flush=True)
            if source_index % 20 == 0:
                print(
                    f"grade-a render sources {source_index}/{len(grouped)}",
                    flush=True,
                )
    rows = sorted(by_id.values(), key=lambda value: int(value["image_order"]))
    if len(rows) != len(plans):
        raise GradeA8000Error(f"rendered {len(rows)}, expected {len(plans)}")
    write_jsonl(building / "manifest.jsonl", rows)
    data_yaml = (
        f"path: {dataset_dir.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_long\n"
        "  1: dense_short\n"
    )
    (building / "data.yaml").write_text(data_yaml, encoding="utf-8")
    partial_path.unlink()
    os.replace(building, dataset_dir)
    return rows


def _reuse_completed_dataset(
    dataset_dir: Path, plans: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Validate and reuse a completed dataset after a later-phase interruption."""

    manifest_path = dataset_dir / "manifest.jsonl"
    yaml_path = dataset_dir / "data.yaml"
    if not manifest_path.exists() or not yaml_path.exists():
        raise GradeA8000Error("completed dataset directory is missing manifest/data.yaml")
    rows = read_jsonl(manifest_path)
    expected = {str(row["dataset_sample_id"]) for row in plans}
    actual = {str(row["dataset_sample_id"]) for row in rows}
    if len(actual) != len(rows) or actual != expected:
        raise GradeA8000Error("completed dataset does not match resumed render plan")
    for row in rows:
        image_path = dataset_dir / str(row["image_path"])
        label_path = dataset_dir / str(row["label_path"])
        if sha256_file(image_path) != str(row["image_sha256"]):
            raise GradeA8000Error("completed dataset image SHA drift")
        if sha256_file(label_path) != str(row["label_sha256"]):
            raise GradeA8000Error("completed dataset label SHA drift")
    return sorted(rows, key=lambda value: int(value["image_order"]))


def _gallery_card(row: Mapping[str, Any], image_href: str) -> str:
    box = row["box"]
    left = 100.0 * (float(box["cx_norm"]) - float(box["w_norm"]) / 2.0)
    top = 100.0 * (float(box["cy_norm"]) - float(box["h_norm"]) / 2.0)
    return (
        "<article><div class='chart'>"
        f"<img loading='lazy' src='{html.escape(image_href)}'>"
        f"<span style='left:{left:.6f}%;top:{top:.6f}%;width:{100*float(box['w_norm']):.6f}%;"
        f"height:{100*float(box['h_norm']):.6f}%'></span></div>"
        f"<p><b>{int(row['image_order']):05d}</b> {html.escape(str(row['symbol']))} "
        f"{html.escape(str(row['direction']))} · {html.escape(str(row['venue']))} · "
        f"{html.escape(str(row['core_end_time']))}</p>"
        f"<small>event {html.escape(str(row['sample_id']))} · {html.escape(str(row['variant_id']))} · "
        f"PRE {int(row['pre_bars'])} / POST {int(row['post_bars'])} · "
        f"x={float(box['cx_norm']):.3f} · {html.escape(str(row['split']))}</small></article>"
    )


def _write_gallery(rows: Sequence[Mapping[str, Any]], *, dataset_dir: Path, results: Path) -> None:
    public = results / "public"
    pages = public / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    style = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f2f3f5;color:#171717}
header{position:sticky;top:0;z-index:3;background:#111;color:#fff;padding:14px 20px}main{padding:16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:16px}
article{background:#fff;border-radius:10px;padding:10px;box-shadow:0 1px 5px #0002}.chart{position:relative;width:100%}.chart img{display:block;width:100%;height:auto}.chart span{position:absolute;border:3px solid #f00;box-sizing:border-box;pointer-events:none}p{margin:8px 0 2px}small{color:#555}.nav a{color:#9fd3ff;margin-right:14px}
"""
    page_size = 100
    page_count = math.ceil(len(rows) / page_size)
    for page in range(1, page_count + 1):
        chunk = rows[(page - 1) * page_size : page * page_size]
        cards = []
        for row in chunk:
            image = dataset_dir / str(row["image_path"])
            href = os.path.relpath(image, pages).replace(os.sep, "/")
            cards.append(_gallery_card(row, href))
        nav = "".join(
            f"<a href='page_{number:03d}.html'>{number}</a>"
            for number in range(max(1, page - 3), min(page_count, page + 3) + 1)
        )
        (pages / f"page_{page:03d}.html").write_text(
            f"<!doctype html><html><head><meta charset='utf-8'><style>{style}</style></head>"
            f"<body><header><h2>A级完美正样本 · {page}/{page_count}</h2><div class='nav'>"
            f"<a href='../index.html'>首页</a>{nav}</div></header><main>{''.join(cards)}</main></body></html>",
            encoding="utf-8",
        )
    summary = read_json(results / "summary.json")
    links = "".join(
        f"<a href='pages/page_{page:03d}.html'>第 {page} 页</a> "
        for page in range(1, page_count + 1)
    )
    (public / "index.html").write_text(
        f"<!doctype html><html><head><meta charset='utf-8'><style>{style}"
        ".facts{display:flex;gap:16px;flex-wrap:wrap}.fact{background:#222;padding:12px;border-radius:8px}"
        "section{padding:20px;background:#fff;margin:18px;border-radius:10px;line-height:1.7}"
        "</style></head><body><header><h1>15m 均线密集 A级完美正样本 8,000 张</h1>"
        "<div class='facts'>"
        f"<div class='fact'>图片 <b>{summary['images']:,}</b></div>"
        f"<div class='fact'>独立事件 <b>{summary['unique_events']:,}</b></div>"
        f"<div class='fact'>LONG / SHORT <b>{summary['directions'].get('LONG',0):,} / {summary['directions'].get('SHORT',0):,}</b></div>"
        "</div></header><section><p>每张展示的是模型实际使用的无框 1280×742 PNG；红框由 HTML CSS 叠加，未写进训练图。"
        "同一事件只有不同的 18/19 根 K 短窗，全部强制进入同一时间 split。</p>"
        f"<p class='nav'>{links}</p></section></body></html>",
        encoding="utf-8",
    )


def _qa(rows: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]) -> dict[str, Any]:
    target = int(prereg["output"]["target_images"])
    if len(rows) != target:
        raise GradeA8000Error("dataset row count drift")
    event_splits: dict[str, set[str]] = defaultdict(set)
    event_variants: Counter[str] = Counter()
    for row in rows:
        event_splits[str(row["sample_id"])].add(str(row["split"]))
        event_variants[str(row["sample_id"])] += 1
    if any(len(values) != 1 for values in event_splits.values()):
        raise GradeA8000Error("one event crosses train/val")
    minimum_variants = int(prereg["output"]["minimum_variants_per_event"])
    maximum_variants = int(prereg["output"]["maximum_variants_per_event"])
    if (
        min(event_variants.values()) < minimum_variants
        or max(event_variants.values()) > maximum_variants
    ):
        raise GradeA8000Error("event variant count left the frozen contract")
    if len(event_splits) < int(prereg["output"]["minimum_unique_events"]):
        raise GradeA8000Error("unique event floor failed")
    image_shas = {str(row["image_sha256"]) for row in rows}
    if len(image_shas) != len(rows):
        raise GradeA8000Error("exact duplicate model inputs exist")
    return {
        "images": len(rows),
        "unique_events": len(event_splits),
        "unique_images": len(image_shas),
        "event_variants_min": min(event_variants.values()),
        "event_variants_max": max(event_variants.values()),
        "directions": dict(Counter(str(row["direction"]) for row in rows)),
        "venues": dict(Counter(str(row["venue"]) for row in rows)),
        "splits": dict(Counter(str(row["split"]) for row in rows)),
        "variant_indices": dict(Counter(str(row["variant_index"]) for row in rows)),
        "core_bars": dict(Counter(str(row["core_bars"]) for row in rows)),
        "window_bars": dict(Counter(str(row["window_bars"]) for row in rows)),
        "box_center_x_min": min(float(row["box"]["cx_norm"]) for row in rows),
        "box_center_x_max": max(float(row["box"]["cx_norm"]) for row in rows),
        "max_box_height_norm": max(float(row["box"]["h_norm"]) for row in rows),
        "all_dimensions_1280x742": all(
            int(row["image_width"]) == SOURCE_WIDTH and int(row["image_height"]) == SOURCE_HEIGHT
            for row in rows
        ),
        "all_one_box": all(int(row["boxes_per_image"]) == 1 for row in rows),
        "all_clean_model_inputs": all(
            not bool(row["annotation_drawn_into_image"])
            and int(row["exact_overlay_red_pixels"]) == 0
            for row in rows
        ),
        "same_event_single_split": True,
        "holdout_ohlcv_rows_materialized": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }


def build(
    prereg_path: Path = DEFAULT_PREREG,
    *,
    results_dir: Path = DEFAULT_RESULTS,
    dataset_dir: Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    """Build the frozen Grade-A event pool, 8,000 images, labels, and gallery."""

    prereg_path = prereg_path.resolve()
    results_dir = results_dir.resolve()
    dataset_dir = dataset_dir.resolve()
    prereg = read_json(prereg_path)
    _validate_prereg(prereg)
    building = results_dir.with_name(results_dir.name + ".building")
    if results_dir.exists():
        raise FileExistsError(f"refusing to overwrite results: {results_dir}")
    builder_commit = verify_builder_committed(
        [
            Path(__file__),
            ROOT / "yoyo" / "data" / "binance_um_archives.py",
            ROOT / "scripts" / "fetch_binance_um_preholdout_15m.py",
            ROOT / "scripts" / "build_15m_ma_launch_owner_grade_a8000.py",
            prereg_path,
        ]
    )
    prereg_sha = sha256_file(prereg_path)
    state_path = building / "build_state.json"
    if building.exists():
        if not state_path.exists():
            raise GradeA8000Error("results building directory has no resumable state")
        state = read_json(state_path)
        if str(state.get("preregistration_sha256")) != prereg_sha:
            raise GradeA8000Error("resumed results preregistration SHA drift")
        if str(state.get("builder_commit")) != builder_commit:
            raise GradeA8000Error("resumed results builder commit drift")
    else:
        building.mkdir(parents=True)
        write_json(
            state_path,
            {
                "experiment_id": EXPERIMENT_ID,
                "preregistration_sha256": prereg_sha,
                "builder_commit": builder_commit,
                "phase": "started",
            },
        )
    specs, fetch_summary = _source_specs(prereg)
    autofill, perfect, scan_references, references, accepted_family = _load_exact_contract(prereg)
    candidates, source_audits, scan_receipt = _scan_binance(
        prereg,
        specs=specs,
        autofill=autofill,
        scan_references=scan_references,
        building=building,
    )
    scored_binance, calibration = _score_binance(
        prereg,
        candidates,
        references=references,
        accepted_family=accepted_family,
        perfect_prereg=perfect,
        building=building,
    )
    binance_a = [row for row in scored_binance if str(row["quality_tier"]) == "PERFECT_CANDIDATE"]

    existing_contract = prereg["sources"]["existing_grade_a"]
    existing_path = _repo_path(existing_contract["ranked_manifest_path"])
    _verify_pinned(existing_path, existing_contract["ranked_manifest_sha256"])
    existing_a = [
        {**row, "venue": "okx", "exchange_symbol": str(row["symbol"])}
        for row in read_jsonl(existing_path)
        if str(row["quality_tier"]) == "PERFECT_CANDIDATE"
    ]
    combined = cross_venue_event_nms(
        [*existing_a, *binance_a],
        gap_minutes=int(prereg["deduplication"]["same_symbol_direction_event_gap_minutes"]),
    )
    ordered = cap_and_order_events(
        combined,
        per_symbol_direction=int(prereg["deduplication"]["max_events_per_symbol_direction"]),
        per_symbol_direction_time_block=int(
            prereg["deduplication"]["max_events_per_symbol_direction_time_block"]
        ),
    )
    preflight = _preflight_variants(prereg, ordered)
    plans = allocate_variants(
        preflight,
        target_images=int(prereg["output"]["target_images"]),
        minimum_unique_events=int(prereg["output"]["minimum_unique_events"]),
        preferred_unique_events=int(prereg["output"]["preferred_unique_events"]),
        minimum_variants_per_event=int(
            prereg["output"]["minimum_variants_per_event"]
        ),
        maximum_variants_per_event=int(
            prereg["output"]["maximum_variants_per_event"]
        ),
    )
    write_jsonl(building / "selected_event_variants.jsonl", plans)
    rows = (
        _reuse_completed_dataset(dataset_dir, plans)
        if dataset_dir.exists()
        else _render_dataset(prereg, plans, dataset_dir=dataset_dir)
    )
    summary = _qa(rows, prereg)
    summary.update(
        {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "builder_commit": builder_commit,
            "binance_source_rows": int(fetch_summary["rows"]),
            "binance_sources": int(fetch_summary["symbols_complete"]),
            "binance_weak_after_one_hour_nms": int(scan_receipt["n_after_one_hour_nms"]),
            "binance_hard_gate_pass": sum(bool(row["hard_gate_pass"]) for row in scored_binance),
            "binance_grade_a_events": len(binance_a),
            "existing_okx_grade_a_events": len(existing_a),
            "cross_venue_dedup_events": len(combined),
            "geometry_eligible_events": len(preflight),
            "dataset_path": _relative(dataset_dir),
            "manifest_sha256": sha256_file(dataset_dir / "manifest.jsonl"),
            "data_yaml_sha256": sha256_file(dataset_dir / "data.yaml"),
            "perfect_score_threshold": calibration["perfect_score_threshold"],
            "reference_gate_unchanged": True,
            "negative_samples_changed": False,
        }
    )
    write_json(building / "summary.json", summary)
    write_json(
        building / "build_receipt.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "builder_commit": builder_commit,
            "preregistration_path": _relative(prereg_path),
            "preregistration_sha256": sha256_file(prereg_path),
            "summary": summary,
            "holdout_ohlcv_rows_materialized": 0,
            "training_started": False,
            "active_or_frozen_changed": False,
            "forward_or_order_state_changed": False,
        },
    )
    write_json(
        state_path,
        {
            "experiment_id": EXPERIMENT_ID,
            "preregistration_sha256": prereg_sha,
            "builder_commit": builder_commit,
            "phase": "complete",
        },
    )
    _write_gallery(rows, dataset_dir=dataset_dir, results=building)
    os.replace(building, results_dir)
    return {
        **summary,
        "results_path": _relative(results_dir),
        "html_path": _relative(results_dir / "public" / "index.html"),
    }
