#!/usr/bin/env python3
"""Independently verify the Grade-A 8k + matched-negative 24k dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_v1"
DEFAULT_POSITIVE_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_v1"
DEFAULT_PLAN = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-owner-grade-a8000-neg24000-v1"
    / "results"
    / "negative_event_plan.jsonl"
)
DEFAULT_OUTPUT = DEFAULT_PLAN.parent / "independent_qa_receipt.json"
HOLDOUT = pd.Timestamp("2026-05-04T00:00:00Z")
CUTOFF = pd.Timestamp("2025-12-01T00:00:00Z")
PURGE = pd.Timedelta(minutes=150 * 15)


class IndependentQaError(ValueError):
    """Raised when the independent dataset audit fails."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nuisance_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["venue"]),
        str(row["symbol"]),
        str(row["time_block"]),
        int(row["core_bars"]),
        int(row["pre_bars"]),
        int(row["post_bars"]),
        int(row["window_bars"]),
    )


def event_pair_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    variants = tuple(
        sorted(
            (str(value[0]), int(value[1]), int(value[2]), int(value[3]))
            for value in row["variants"]
        )
    )
    return (
        str(row["source_path"]),
        str(row["venue"]),
        str(row["symbol"]),
        str(row["time_block"]),
        str(row["split"]),
        int(row["core_bars"]),
        variants,
    )


def event_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["event_id"])].append(row)
    return grouped


def pairing_permutation_null(
    positive_rows: Sequence[Mapping[str, Any]],
    negative_plan: Sequence[Mapping[str, Any]],
    *,
    permutations: int = 1000,
    seed: int = 20260829,
) -> dict[str, Any]:
    """Compare exact event-pair matching with random positive assignments."""

    positives = event_rows(positive_rows)
    positive_ids = sorted(positives)
    positive_keys = {
        event_id: (
            str(rows[0]["source_path"]),
            str(rows[0]["venue"]),
            str(rows[0]["symbol"]),
            str(rows[0]["time_block"]),
            str(rows[0]["split"]),
            int(rows[0]["core_bars"]),
            tuple(
                sorted(
                    (
                        str(row["variant_id"]),
                        int(row["variant_index"]),
                        int(row["pre_bars"]),
                        int(row["post_bars"]),
                    )
                    for row in rows
                )
            ),
        )
        for event_id, rows in positives.items()
    }
    slot_one = {
        str(row["paired_positive_event_id"]): row
        for row in negative_plan
        if int(row["pair_slot"]) == 1
    }
    if set(slot_one) != set(positive_ids):
        raise IndependentQaError("slot-one negative coverage drift")
    actual = sum(
        event_pair_key(slot_one[event_id]) == positive_keys[event_id]
        for event_id in positive_ids
    )
    rng = np.random.default_rng(seed)
    null_counts: list[int] = []
    ids = np.asarray(positive_ids, dtype=object)
    for _ in range(permutations):
        permuted = rng.permutation(ids)
        null_counts.append(
            sum(
                event_pair_key(slot_one[source_id]) == positive_keys[target_id]
                for source_id, target_id in zip(positive_ids, permuted)
            )
        )
    return {
        "actual_exact_matches": actual,
        "denominator_events": len(positive_ids),
        "actual_match_rate": actual / len(positive_ids),
        "permutations": permutations,
        "null_mean_matches": float(np.mean(null_counts)),
        "null_max_matches": int(max(null_counts)),
        "one_sided_p": (1 + sum(value >= actual for value in null_counts))
        / (permutations + 1),
        "seed": seed,
    }


def verify(
    dataset: Path,
    positive_dataset: Path,
    plan_path: Path,
    output: Path,
) -> dict[str, Any]:
    rows = read_jsonl(dataset / "manifest.jsonl")
    positive_source = read_jsonl(positive_dataset / "manifest.jsonl")
    plan = read_jsonl(plan_path)
    if len(rows) != 32_000 or len(positive_source) != 8_000 or len(plan) != 3_129:
        raise IndependentQaError("row-count contract drift")
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]
    if len(positives) != 8_000 or len(negatives) != 24_000:
        raise IndependentQaError("class-presence row counts drift")
    source_by_id = {str(row["dataset_sample_id"]): row for row in positive_source}
    actual_image_hashes: set[str] = set()
    image_ids: set[str] = set()
    split_counts: Counter[str] = Counter()
    for number, row in enumerate(rows, 1):
        image_path = dataset / str(row["image_path"])
        label_path = dataset / str(row["label_path"])
        image_hash = sha256(image_path)
        label_hash = sha256(label_path)
        if image_hash != str(row["image_sha256"]) or label_hash != str(row["label_sha256"]):
            raise IndependentQaError("manifest SHA disagrees with disk")
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape != (742, 1280, 3):
            raise IndependentQaError("image decode/dimension drift")
        actual_image_hashes.add(image_hash)
        identity = str(row["dataset_sample_id"])
        if identity in image_ids:
            raise IndependentQaError("duplicate dataset sample ID")
        image_ids.add(identity)
        split_counts[f"{row['split']}/{row['sample_kind']}"] += 1
        if row["sample_kind"] == "negative":
            if label_path.read_bytes() != b"":
                raise IndependentQaError("negative label is not empty")
        else:
            original = source_by_id[identity]
            if image_hash != str(original["image_sha256"]):
                raise IndependentQaError("positive image is not byte-identical")
            if label_hash != str(original["label_sha256"]):
                raise IndependentQaError("positive label is not byte-identical")
        if number % 8_000 == 0:
            print(f"independent file QA {number:>5}/{len(rows)}", flush=True)
    if len(actual_image_hashes) != 32_000:
        raise IndependentQaError("actual model-input pixels are not unique")

    positive_nuisance = Counter(nuisance_key(row) for row in positives)
    negative_nuisance = Counter(nuisance_key(row) for row in negatives)
    if negative_nuisance != Counter(
        {key: count * 3 for key, count in positive_nuisance.items()}
    ):
        raise IndependentQaError("nuisance distribution is not exact 3x")
    paired_positions = Counter(
        (str(row["paired_positive_event_id"]), str(row["variant_id"]))
        for row in negatives
    )
    expected_positions = {
        (str(row["event_id"]), str(row["variant_id"])) for row in positives
    }
    if set(paired_positions) != expected_positions or set(paired_positions.values()) != {3}:
        raise IndependentQaError("positive-position pairing is not exactly three")

    event_splits: dict[str, set[str]] = defaultdict(set)
    for row in positives:
        event_splits[f"p:{row['event_id']}"] .add(str(row["split"]))
    for row in negatives:
        event_splits[f"n:{row['negative_event_id']}"] .add(str(row["split"]))
    if any(len(values) != 1 for values in event_splits.values()):
        raise IndependentQaError("one event crosses train/val")

    metric_names = (
        "ma_envelope_atr",
        "ma_spread_end_atr",
        "max_body_atr",
        "candle_envelope_atr",
        "minimum_close_to_ma_atr",
        "abs_close_progress_atr_core_plus_2",
        "abs_close_progress_atr_core_plus_3",
        "abs_close_progress_atr_core_plus_5",
        "two_sided_excursion_atr_core_plus_1_to_5",
    )
    if not all(
        math.isfinite(float(row[name])) for row in plan for name in metric_names
    ):
        raise IndependentQaError("negative plan contains non-finite metrics")
    by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in plan:
        by_source[str(row["source_path"])].append(row)
        dependency_end = pd.Timestamp(row["dependency_end_time"])
        if dependency_end.tzinfo is None:
            dependency_end = dependency_end.tz_localize("UTC")
        if dependency_end >= HOLDOUT:
            raise IndependentQaError("negative dependency touches holdout")
    activity_failures = 0
    overlap_failures = 0
    for source_number, (source_path, source_rows) in enumerate(sorted(by_source.items()), 1):
        frame = pd.read_csv(ROOT / source_path)
        for row in source_rows:
            start, end = int(row["widest_window_start_i"]), int(row["dependency_end_i"])
            window = frame.iloc[start : end + 1]
            closes = pd.to_numeric(window["close"], errors="coerce").to_numpy(dtype=float)
            highs = pd.to_numeric(window["high"], errors="coerce").to_numpy(dtype=float)
            lows = pd.to_numeric(window["low"], errors="coerce").to_numpy(dtype=float)
            if len(np.unique(closes)) < 4 or int(np.count_nonzero(highs > lows)) < 4:
                activity_failures += 1
        intervals = sorted(
            (
                int(row["widest_window_start_i"]) - 2,
                int(row["dependency_end_i"]) + 2,
            )
            for row in source_rows
        )
        overlap_failures += sum(
            intervals[index][0] <= intervals[index - 1][1]
            for index in range(1, len(intervals))
        )
        if source_number % 100 == 0 or source_number == len(by_source):
            print(f"independent source QA {source_number:>3}/{len(by_source)}", flush=True)
    if activity_failures or overlap_failures:
        raise IndependentQaError(
            f"activity/negative-overlap failures: {activity_failures}/{overlap_failures}"
        )

    for row in rows:
        start = pd.Timestamp(row["window_start_time"])
        end = pd.Timestamp(
            row.get("dependency_end_time", row["window_end_time"])
        )
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        if end.tzinfo is None:
            end = end.tz_localize("UTC")
        expected_split = (
            "train"
            if end <= CUTOFF - PURGE
            else "val"
            if start >= CUTOFF + PURGE
            else "excluded"
        )
        if expected_split != str(row["split"]):
            raise IndependentQaError("chronological split/purge drift")

    null = pairing_permutation_null(positive_source, plan)
    receipt = {
        "schema_version": 1,
        "experiment_id": "exp-15m-ma-launch-owner-grade-a8000-neg24000-v1",
        "passed": True,
        "rows": len(rows),
        "actual_unique_image_hashes": len(actual_image_hashes),
        "unique_dataset_sample_ids": len(image_ids),
        "positive_image_and_label_byte_parity": len(positives),
        "negative_empty_labels": len(negatives),
        "split_counts": dict(split_counts),
        "nuisance_distribution_exact_3x": True,
        "positive_event_positions_with_exactly_three_negatives": len(expected_positions),
        "same_event_single_split": True,
        "negative_plan_metrics_all_finite": True,
        "activity_gate_failures": activity_failures,
        "negative_interval_overlap_failures": overlap_failures,
        "holdout_dependency_rows": 0,
        "pairing_permutation_null": null,
        "manifest_sha256": sha256(dataset / "manifest.jsonl"),
        "negative_event_plan_sha256": sha256(plan_path),
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--positive-dataset", type=Path, default=DEFAULT_POSITIVE_DATASET)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    receipt = verify(
        args.dataset.resolve(),
        args.positive_dataset.resolve(),
        args.plan.resolve(),
        args.output.resolve(),
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
