#!/usr/bin/env python3
"""Independently re-open and verify the 15m t-3 weak-label YOLO dataset."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import sha256_file, utc
from yoyo.datasets.ma_launch_t3_training import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    T3DatasetError,
    load_candidate_union,
    load_preregistration,
    position_bin,
    verify_builder_committed,
)


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATHS = (
    ROOT / "yoyo" / "datasets" / "ma_launch_t3_training.py",
    ROOT / "scripts" / "build_15m_ma_launch_t3_dataset.py",
    Path(__file__).resolve(),
    ROOT / "tests" / "test_ma_launch_t3_training.py",
    DEFAULT_PREREG,
)


def intervals_disjoint(intervals: Iterable[tuple[int, int]]) -> bool:
    """Return true when closed integer intervals never overlap."""

    ordered = sorted((int(start), int(end)) for start, end in intervals)
    return all(right_start > left_end for (_, left_end), (right_start, _) in zip(ordered, ordered[1:]))


def overlaps_sorted_guards(
    start: int, end: int, guards: list[tuple[int, int]], guard_starts: list[int]
) -> bool:
    """Test one closed interval against start-sorted positive guards."""

    at = bisect.bisect_right(guard_starts, int(end))
    return any(guard_end >= int(start) for _, guard_end in guards[:at])


def _positive_guards(
    candidates: Iterable[Mapping[str, Any]], prereg: Mapping[str, Any]
) -> dict[str, list[tuple[int, int]]]:
    contract = prereg["negative_sampling"]["positive_guard"]
    core = prereg["positive_geometry"]
    max_core = max(int(value) for value in core["core_length_choices"])
    core_end_offset = int(core["core_end_offset_from_t_bars"])
    latest_end = int(core["maximum_window_end_offset_from_t_bars"])
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in candidates:
        anchor = int(row["source_anchor_i"])
        core_start = anchor + core_end_offset - max_core + 1
        out[str(row["source_path"])].append(
            (
                core_start - int(contract["before_core_bars"]),
                anchor + latest_end + int(contract["after_latest_possible_window_end_bars"]),
            )
        )
    return {source: sorted(values) for source, values in out.items()}


def _hash_sample_ids(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(str(row["sample_id"]).encode("utf-8")).hexdigest(),
    )[:count]


def verify_dataset(
    *,
    dataset: Path,
    prereg_path: Path,
    results: Path,
    verifier_commit: str,
    decode_sample: int = 512,
) -> dict[str, Any]:
    """Rehash every asset and re-evaluate geometry, split and exclusion gates."""

    prereg = load_preregistration(prereg_path)
    candidates = load_candidate_union(prereg)
    manifest_path = dataset / "manifest.jsonl"
    build_summary_path = dataset / "build_summary.json"
    data_yaml = dataset / "data.yaml"
    for path in (manifest_path, build_summary_path, data_yaml):
        if not path.is_file():
            raise FileNotFoundError(path)
    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    build = json.loads(build_summary_path.read_text(encoding="utf-8"))
    if len(rows) != int(build["manifest_rows"]):
        raise T3DatasetError("manifest row count differs from build summary")

    image_paths: set[str] = set()
    label_paths: set[str] = set()
    counts: Counter[str] = Counter()
    position_bins: Counter[str] = Counter()
    center_fractions: list[float] = []
    negative_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    dependency_times: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    no_launch = prereg["negative_sampling"]["completed_no_launch_condition"]
    hard_limit = float(
        prereg["negative_sampling"]["hard_definition"]["six_ma_bandwidth_pct_max"]
    )
    holdout = utc(prereg["sources"]["holdout_start"])
    label_errors: list[str] = []
    hash_errors: list[str] = []

    for row in rows:
        sample_id = str(row["sample_id"])
        split = str(row["split"])
        if split not in {"train", "val"}:
            raise T3DatasetError(f"unexpected split: {split}")
        image_rel, label_rel = str(row["image_path"]), str(row["label_path"])
        if image_rel in image_paths or label_rel in label_paths:
            raise T3DatasetError(f"duplicate asset path: {sample_id}")
        image_paths.add(image_rel)
        label_paths.add(label_rel)
        image_path, label_path = dataset / image_rel, dataset / label_rel
        if not image_path.is_file() or not label_path.is_file():
            raise FileNotFoundError(f"missing sample asset: {sample_id}")
        if sha256_file(image_path) != str(row["image_sha256"]):
            hash_errors.append(f"image:{sample_id}")
        if sha256_file(label_path) != str(row["label_sha256"]):
            hash_errors.append(f"label:{sample_id}")

        label = label_path.read_text(encoding="utf-8").strip()
        kind = str(row["sample_kind"])
        counts[f"{split}/{kind}"] += 1
        if kind == "positive_weak":
            fields = label.split()
            if len(fields) != 5:
                label_errors.append(sample_id)
            else:
                class_id = int(fields[0])
                coords = [float(value) for value in fields[1:]]
                if class_id != int(row["class_id"]) or not all(0.0 < value <= 1.0 for value in coords):
                    label_errors.append(sample_id)
            geometry = row["geometry"]
            if int(row["core_end_i"]) != int(row["source_anchor_i"]) - 3:
                raise T3DatasetError(f"positive is not anchored at t-3: {sample_id}")
            core_len = int(row["core_end_i"]) - int(row["core_start_i"]) + 1
            window_len = int(row["window_end_i"]) - int(row["window_start_i"]) + 1
            if core_len not in {4, 5, 6, 7} or window_len not in set(range(14, 23)):
                raise T3DatasetError(f"positive geometry drifted: {sample_id}")
            if int(geometry["confirmation_bars"]) not in {3, 4, 5}:
                raise T3DatasetError(f"confirmation count drifted: {sample_id}")
            center = float(row["center_fraction"])
            if str(row["position_bin"]) != position_bin(center):
                raise T3DatasetError(f"position bin drifted: {sample_id}")
            center_fractions.append(center)
            position_bins[str(row["position_bin"])] += 1
            dependency_start = utc(row["render_start_time"])
            dependency_end = utc(row["selection_label_end_time"])
        elif kind in {"negative_easy", "negative_hard"}:
            if label:
                label_errors.append(sample_id)
            close_abs = float(row["close_abs_atr"])
            favorable = float(row["two_sided_favorable_abs_atr"])
            if close_abs > float(no_launch["pseudo_t_close_abs_atr_max_over_12_bars"]):
                raise T3DatasetError(f"negative close no-launch gate failed: {sample_id}")
            if favorable > float(
                no_launch["pseudo_t_two_sided_favorable_abs_atr_max_over_12_bars"]
            ):
                raise T3DatasetError(f"negative favorable no-launch gate failed: {sample_id}")
            if kind == "negative_hard" and float(row["bandwidth_pct"]) > hard_limit:
                raise T3DatasetError(f"hard-negative rope gate failed: {sample_id}")
            pseudo_t = utc(row["pseudo_t_time"])
            window_start_offset = int(row["window_start_i"]) - int(row["pseudo_t_i"])
            label_end_offset = int(row["label_future_end_i"]) - int(row["pseudo_t_i"])
            dependency_start = pseudo_t + pd.Timedelta(minutes=15 * window_start_offset)
            dependency_end = pseudo_t + pd.Timedelta(minutes=15 * label_end_offset)
            if int(row["window_end_i"]) > int(row["pseudo_t_i"]) + 2:
                raise T3DatasetError(f"negative input exceeds five confirmations: {sample_id}")
            negative_intervals[str(row["source_path"])].append(
                (int(row["window_start_i"]), int(row["window_end_i"]))
            )
        else:
            raise T3DatasetError(f"unknown sample kind: {kind}")
        if dependency_end >= holdout:
            raise T3DatasetError(f"sample dependency touches holdout: {sample_id}")
        dependency_times[split].append((dependency_start, dependency_end))

    if label_errors or hash_errors:
        raise T3DatasetError(
            f"asset verification failed: labels={label_errors[:5]} hashes={hash_errors[:5]}"
        )

    actual_images = {
        str(path.relative_to(dataset)) for path in (dataset / "images").glob("*/*.png")
    }
    actual_labels = {
        str(path.relative_to(dataset)) for path in (dataset / "labels").glob("*/*.txt")
    }
    if actual_images != image_paths or actual_labels != label_paths:
        raise T3DatasetError("filesystem asset set differs from manifest")

    guards = _positive_guards(candidates, prereg)
    guard_failures = 0
    for source, intervals in negative_intervals.items():
        source_guards = guards[source]
        starts = [start for start, _ in source_guards]
        if not intervals_disjoint(intervals):
            raise T3DatasetError(f"negative windows overlap on source: {source}")
        guard_failures += sum(
            overlaps_sorted_guards(start, end, source_guards, starts)
            for start, end in intervals
        )
    if guard_failures:
        raise T3DatasetError(f"negative windows overlap positive guards: {guard_failures}")

    split_cfg = prereg["split"]
    cutoff = utc(split_cfg["cutoff"])
    purge = pd.Timedelta(
        minutes=int(split_cfg["purge_bars"]) * int(prereg["sources"]["bar_minutes"])
    )
    train_max = max(end for _, end in dependency_times["train"])
    val_min = min(start for start, _ in dependency_times["val"])
    if train_max > cutoff - purge or val_min < cutoff + purge or train_max >= val_min:
        raise T3DatasetError("chronological split/purge audit failed")

    positive_count = counts["train/positive_weak"] + counts["val/positive_weak"]
    fractions = np.asarray(center_fractions, dtype=float)
    gate = prereg["positive_geometry"]["position_gate"]
    max_bin_share = max(position_bins.values()) / positive_count
    if max_bin_share > float(gate["maximum_single_bin_share"]):
        raise T3DatasetError("independent position audit failed")
    if float(fractions.std()) < float(gate["minimum_center_fraction_std"]):
        raise T3DatasetError("independent position std audit failed")

    decoded = 0
    for row in _hash_sample_ids(rows, min(int(decode_sample), len(rows))):
        image = cv2.imread(str(dataset / row["image_path"]), cv2.IMREAD_COLOR)
        if image is None or image.shape != (742, 1280, 3):
            raise T3DatasetError(f"image decode/shape failed: {row['sample_id']}")
        decoded += 1

    # Exercise Ultralytics' own dataset parser without starting a model.
    from ultralytics.data.utils import check_det_dataset

    checked = check_det_dataset(str(data_yaml), autodownload=False)
    names = checked.get("names")
    if names != {0: "dense_long", 1: "dense_short"}:
        raise T3DatasetError(f"Ultralytics class map drifted: {names}")

    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": prereg["experiment_id"],
        "verifier_commit": verifier_commit,
        "dataset_path": str(dataset),
        "manifest_rows": len(rows),
        "manifest_sha256": sha256_file(manifest_path),
        "data_yaml_sha256": sha256_file(data_yaml),
        "build_summary_sha256": sha256_file(build_summary_path),
        "asset_counts": {
            "images": len(actual_images),
            "labels": len(actual_labels),
            "by_kind": dict(sorted(counts.items())),
        },
        "all_asset_hashes_match": True,
        "decoded_sample_images": decoded,
        "decoded_shape": [742, 1280, 3],
        "ultralytics_dataset_parser_passed": True,
        "negative_windows_pairwise_disjoint": True,
        "negative_positive_guard_overlaps": 0,
        "position_bins": dict(sorted(position_bins.items())),
        "maximum_position_bin_share": max_bin_share,
        "center_fraction_std": float(fractions.std()),
        "train_dependency_end_max": train_max.isoformat(),
        "val_dependency_start_min": val_min.isoformat(),
        "split_cutoff": cutoff.isoformat(),
        "purge_bars": int(split_cfg["purge_bars"]),
        "holdout_ohlcv_rows_materialized_by_builder": 0,
        "holdout_touched_by_any_sample_dependency": False,
        "production_eligible": False,
        "passed": True,
    }
    results.mkdir(parents=True, exist_ok=True)
    output = results / "dataset_qa_receipt.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite QA receipt: {output}")
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--decode-sample", type=int, default=512)
    args = parser.parse_args()
    commit = verify_builder_committed((*BUILDER_PATHS[:-1], args.prereg.resolve()))
    receipt = verify_dataset(
        dataset=args.dataset.resolve(),
        prereg_path=args.prereg.resolve(),
        results=args.results.resolve(),
        verifier_commit=commit,
        decode_sample=args.decode_sample,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
