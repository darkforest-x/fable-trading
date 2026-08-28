#!/usr/bin/env python3
"""Independently verify the 8,000-image Grade-A positive dataset.

This verifier reads only the published dataset and its manifest. It decodes
every PNG, recomputes every file SHA-256, parses every YOLO label, checks event
grouped chronological splits, and runs two fixed permutation nulls. It does not
read OHLCV, train, change labels, or touch runtime/production state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_v1"
DEFAULT_OUTPUT = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-owner-grade-a8000-v1"
    / "results"
    / "qa"
    / "independent_qa_receipt.json"
)
NULL_SEED = 20_260_828
NULL_REPEATS = 1_000


class GradeAIndependentQAError(RuntimeError):
    """Raised when any published dataset contract fails."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def expected_label(row: Mapping[str, Any]) -> str:
    class_id = 0 if str(row["direction"]) == "LONG" else 1
    if str(row["direction"]) not in {"LONG", "SHORT"}:
        raise GradeAIndependentQAError(f"invalid direction: {row['direction']}")
    box = row["box"]
    return (
        f"{class_id} {float(box['cx_norm']):.10f} {float(box['cy_norm']):.10f} "
        f"{float(box['w_norm']):.10f} {float(box['h_norm']):.10f}\n"
    )


def permutation_nulls(
    rows: Sequence[Mapping[str, Any]], labels: Sequence[str]
) -> dict[str, Any]:
    rng = np.random.default_rng(NULL_SEED)
    count = len(rows)
    observed_label_matches = sum(
        label == expected_label(row) for row, label in zip(rows, labels)
    )
    label_matches: list[int] = []
    split_crossings: list[int] = []
    event_indices: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        event_indices[str(row["sample_id"])].append(index)
    split_values = np.asarray([str(row["split"]) == "train" for row in rows])
    observed_crossings = sum(
        len({str(rows[index]["split"]) for index in indices}) > 1
        for indices in event_indices.values()
    )
    base = np.arange(count)
    for _ in range(NULL_REPEATS):
        label_order = rng.permutation(base)
        label_matches.append(
            sum(labels[int(source)] == expected_label(rows[target]) for target, source in enumerate(label_order))
        )
        random_split = split_values[rng.permutation(base)]
        split_crossings.append(
            sum(
                bool(random_split[indices].any())
                and bool((~random_split[indices]).any())
                for indices in event_indices.values()
            )
        )
    return {
        "seed": NULL_SEED,
        "repeats": NULL_REPEATS,
        "label_pairing": {
            "observed_exact_matches": observed_label_matches,
            "random_exact_matches_min": min(label_matches),
            "random_exact_matches_median": float(np.median(label_matches)),
            "random_exact_matches_max": max(label_matches),
            "one_sided_permutation_p": (
                1 + sum(value >= observed_label_matches for value in label_matches)
            )
            / (NULL_REPEATS + 1),
        },
        "event_split_grouping": {
            "observed_cross_split_events": observed_crossings,
            "random_cross_split_events_min": min(split_crossings),
            "random_cross_split_events_median": float(np.median(split_crossings)),
            "random_cross_split_events_max": max(split_crossings),
            "one_sided_permutation_p": (
                1 + sum(value <= observed_crossings for value in split_crossings)
            )
            / (NULL_REPEATS + 1),
        },
    }


def verify(dataset: Path) -> dict[str, Any]:
    manifest_path = dataset / "manifest.jsonl"
    rows = read_jsonl(manifest_path)
    images = sorted((dataset / "images").glob("*/*.png"))
    labels_on_disk = sorted((dataset / "labels").glob("*/*.txt"))
    if (len(rows), len(images), len(labels_on_disk)) != (8_000, 8_000, 8_000):
        raise GradeAIndependentQAError("row/image/label cardinality drift")
    ids = [str(row["dataset_sample_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise GradeAIndependentQAError("duplicate dataset sample IDs")

    exact_red_pixels = 0
    image_shas: set[str] = set()
    label_shas: set[str] = set()
    label_texts: list[str] = []
    dimensions: Counter[str] = Counter()
    for row in rows:
        image_path = dataset / str(row["image_path"])
        label_path = dataset / str(row["label_path"])
        if image_path.parent.name != str(row["split"]):
            raise GradeAIndependentQAError("image path/split mismatch")
        if label_path.parent.name != str(row["split"]):
            raise GradeAIndependentQAError("label path/split mismatch")
        image_bytes = image_path.read_bytes()
        label_bytes = label_path.read_bytes()
        image_sha = sha256_bytes(image_bytes)
        label_sha = sha256_bytes(label_bytes)
        if image_sha != str(row["image_sha256"]):
            raise GradeAIndependentQAError(f"image SHA mismatch: {image_path}")
        if label_sha != str(row["label_sha256"]):
            raise GradeAIndependentQAError(f"label SHA mismatch: {label_path}")
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise GradeAIndependentQAError(f"unreadable PNG: {image_path}")
        dimensions[f"{image.shape[1]}x{image.shape[0]}"] += 1
        exact_red_pixels += int(
            ((image[:, :, 2] == 255) & (image[:, :, 1] == 0) & (image[:, :, 0] == 0)).sum()
        )
        label_text = label_bytes.decode("utf-8")
        if label_text != expected_label(row):
            raise GradeAIndependentQAError(f"YOLO label/manifest mismatch: {label_path}")
        image_shas.add(image_sha)
        label_shas.add(label_sha)
        label_texts.append(label_text)
    if dimensions != {"1280x742": 8_000}:
        raise GradeAIndependentQAError(f"dimension drift: {dict(dimensions)}")
    if exact_red_pixels != 0:
        raise GradeAIndependentQAError("preview-red pixels leaked into model inputs")
    if len(image_shas) != 8_000:
        raise GradeAIndependentQAError("exact duplicate model inputs exist")

    event_splits: dict[str, set[str]] = defaultdict(set)
    event_variants: Counter[str] = Counter()
    for row in rows:
        event_splits[str(row["sample_id"])].add(str(row["split"]))
        event_variants[str(row["sample_id"])] += 1
    if any(len(splits) != 1 for splits in event_splits.values()):
        raise GradeAIndependentQAError("one event crosses train/val")
    if min(event_variants.values()) != 7 or max(event_variants.values()) != 8:
        raise GradeAIndependentQAError("event variants are outside 7-8")

    nulls = permutation_nulls(rows, label_texts)
    if nulls["label_pairing"]["observed_exact_matches"] != 8_000:
        raise GradeAIndependentQAError("observed label pairing is incomplete")
    if nulls["event_split_grouping"]["observed_cross_split_events"] != 0:
        raise GradeAIndependentQAError("observed event split grouping failed")
    builder_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "schema_version": 1,
        "verifier_commit": builder_commit,
        "dataset_path": str(dataset.relative_to(ROOT)),
        "manifest_sha256": sha256_file(manifest_path),
        "data_yaml_sha256": sha256_file(dataset / "data.yaml"),
        "rows": len(rows),
        "images": len(images),
        "labels": len(labels_on_disk),
        "unique_image_sha256": len(image_shas),
        "unique_label_sha256": len(label_shas),
        "dimensions": dict(dimensions),
        "exact_overlay_red_pixels": exact_red_pixels,
        "unique_events": len(event_splits),
        "event_variant_counts": dict(Counter(event_variants.values())),
        "split_image_counts": dict(Counter(str(row["split"]) for row in rows)),
        "split_event_counts": dict(
            Counter(next(iter(splits)) for splits in event_splits.values())
        ),
        "direction_image_counts": dict(
            Counter(str(row["direction"]) for row in rows)
        ),
        "venue_image_counts": dict(Counter(str(row["venue"]) for row in rows)),
        "core_bar_counts": dict(Counter(str(row["core_bars"]) for row in rows)),
        "variant_index_counts": dict(
            sorted(Counter(str(row["variant_index"]) for row in rows).items())
        ),
        "box_center_x_range": [
            min(float(row["box"]["cx_norm"]) for row in rows),
            max(float(row["box"]["cx_norm"]) for row in rows),
        ],
        "box_width_range": [
            min(float(row["box"]["w_norm"]) for row in rows),
            max(float(row["box"]["w_norm"]) for row in rows),
        ],
        "box_height_range": [
            min(float(row["box"]["h_norm"]) for row in rows),
            max(float(row["box"]["h_norm"]) for row in rows),
        ],
        "null_controls": nulls,
        "holdout_ohlcv_rows_materialized": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    receipt = verify(args.dataset.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
