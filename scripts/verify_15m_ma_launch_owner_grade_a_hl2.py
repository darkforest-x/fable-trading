#!/usr/bin/env python3
"""Independently verify the paired Grade-A HL2 render dataset.

This verifier reads dataset metadata and pre-holdout PNG/TXT artifacts only.
It never loads model weights or market OHLCV.  The baseline-versus-treatment
join is exact by ordered sample identity, so split, label and cohort drift
cannot be hidden by aggregate counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_v1"
TREATMENT_DATASET = (
    ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_hl2_v1"
)
DEFAULT_RESULTS = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-v1"
    / "results"
)
HOLDOUT_START = "2026-05-04T00:00:00+00:00"
EXPECTED_COUNTS = {
    "train/positive": 6800,
    "train/negative": 20400,
    "val/positive": 1200,
    "val/negative": 3600,
}
IDENTITY_FIELDS = (
    "dataset_sample_id",
    "sample_kind",
    "split",
    "source_path",
    "window_start_i",
    "window_end_i",
    "window_start_time",
    "window_end_time",
    "window_bars",
    "pre_bars",
    "post_bars",
    "core_bars",
    "direction",
    "event_id",
    "negative_event_id",
    "negative_kind",
    "paired_positive_event_id",
    "image_path",
    "label_path",
)


class HL2VerificationError(ValueError):
    """Raised when any paired dataset invariant fails."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise HL2VerificationError(
                    f"manifest line {line_number} is not an object"
                )
            yield value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise HL2VerificationError(f"invalid PNG header: {path}")
    return struct.unpack(">II", header[16:24])


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in IDENTITY_FIELDS)


def verify(
    *,
    baseline: Path,
    treatment: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite verification: {output}")
    baseline_manifest = baseline / "manifest.jsonl"
    treatment_manifest = treatment / "manifest.jsonl"
    baseline_rows = list(iter_jsonl(baseline_manifest))
    treatment_rows = list(iter_jsonl(treatment_manifest))
    if len(baseline_rows) != 32000 or len(treatment_rows) != 32000:
        raise HL2VerificationError("manifest row count drift")
    summary = read_json(treatment / "build_summary.json")
    if sha256_file(treatment_manifest) != str(summary["manifest_sha256"]):
        raise HL2VerificationError("treatment manifest SHA does not match summary")
    if sha256_file(baseline_manifest) != str(summary["baseline_manifest_sha256"]):
        raise HL2VerificationError("baseline manifest SHA does not match summary")

    counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    image_hashes: set[str] = set()
    sample_ids: set[str] = set()
    changed_images = 0
    changed_pixels: list[int] = []
    absolute_deltas: list[int] = []
    max_window_end = ""
    max_dependency_end = ""
    for number, (source, row) in enumerate(zip(baseline_rows, treatment_rows), 1):
        if not _same_identity(source, row):
            drift = {
                field: {"baseline": source.get(field), "treatment": row.get(field)}
                for field in IDENTITY_FIELDS
                if source.get(field) != row.get(field)
            }
            raise HL2VerificationError(
                f"ordered sample identity drift at row {number}: {drift}"
            )
        sample_id = str(row["dataset_sample_id"])
        if sample_id in sample_ids:
            raise HL2VerificationError(f"duplicate sample ID: {sample_id}")
        sample_ids.add(sample_id)
        if row.get("moving_average_price_source") != "hl2":
            raise HL2VerificationError(f"non-HL2 treatment row: {sample_id}")
        if row.get("canvas_transform_source") != "baseline_close_transform":
            raise HL2VerificationError(f"unfrozen axis: {sample_id}")
        if int(row.get("line_width_px", -1)) != 1:
            raise HL2VerificationError(f"line width drift: {sample_id}")
        if row.get("label_contract") != (
            "same_core_full_wick_plus_six_ma_with_4pct_padding"
        ):
            raise HL2VerificationError(f"label contract drift: {sample_id}")
        if row.get("baseline_image_sha256") != source.get("image_sha256"):
            raise HL2VerificationError(f"baseline image binding drift: {sample_id}")
        if row.get("baseline_label_sha256") != source.get("label_sha256"):
            raise HL2VerificationError(f"baseline label binding drift: {sample_id}")
        if row.get("baseline_replay_sha256") != source.get("image_sha256"):
            raise HL2VerificationError(f"close replay null failed: {sample_id}")

        base_image_path = baseline / str(source["image_path"])
        image_path = treatment / str(row["image_path"])
        base_label_path = baseline / str(source["label_path"])
        label_path = treatment / str(row["label_path"])
        if png_dimensions(image_path) != (1280, 742):
            raise HL2VerificationError(f"PNG dimension drift: {sample_id}")
        image_sha = sha256_file(image_path)
        if image_sha != str(row["image_sha256"]):
            raise HL2VerificationError(f"treatment image SHA drift: {sample_id}")
        if image_sha in image_hashes:
            raise HL2VerificationError(f"duplicate treatment pixels: {sample_id}")
        image_hashes.add(image_sha)
        if sha256_file(base_image_path) != str(source["image_sha256"]):
            raise HL2VerificationError(f"baseline image SHA drift: {sample_id}")
        baseline_label_bytes = base_label_path.read_bytes()
        treatment_label_bytes = label_path.read_bytes()
        if sha256_file(label_path) != str(row["label_sha256"]):
            raise HL2VerificationError(f"treatment label SHA drift: {sample_id}")

        baseline_image = cv2.imread(str(base_image_path), cv2.IMREAD_COLOR)
        treatment_image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if baseline_image is None or treatment_image is None:
            raise HL2VerificationError(f"PNG decode failed: {sample_id}")
        difference = treatment_image.astype(np.int16) - baseline_image.astype(np.int16)
        changed = int(np.any(difference != 0, axis=2).sum())
        absolute = int(np.abs(difference).sum())
        if changed != int(row["changed_pixels_vs_baseline"]):
            raise HL2VerificationError(f"changed-pixel ledger drift: {sample_id}")
        if absolute != int(row["absolute_channel_delta_vs_baseline"]):
            raise HL2VerificationError(f"absolute-delta ledger drift: {sample_id}")
        changed_images += changed > 0
        changed_pixels.append(changed)
        absolute_deltas.append(absolute)

        split_kind = f"{row['split']}/{row['sample_kind']}"
        counts[split_kind] += 1
        label = label_path.read_text(encoding="utf-8").strip()
        if row["sample_kind"] == "negative":
            if label:
                raise HL2VerificationError(f"negative label not empty: {sample_id}")
            if treatment_label_bytes != baseline_label_bytes:
                raise HL2VerificationError(
                    f"negative label bytes changed: {sample_id}"
                )
        else:
            fields = label.split()
            if len(fields) != 5:
                raise HL2VerificationError(f"positive label malformed: {sample_id}")
            class_id = fields[0]
            expected_class = "0" if row["direction"] == "LONG" else "1"
            if class_id != expected_class:
                raise HL2VerificationError(f"direction/class drift: {sample_id}")
            coords = [float(value) for value in fields[1:]]
            if not all(math.isfinite(value) for value in coords):
                raise HL2VerificationError(f"non-finite label: {sample_id}")
            baseline_fields = baseline_label_bytes.decode("utf-8").split()
            if len(baseline_fields) != 5:
                raise HL2VerificationError(f"baseline label malformed: {sample_id}")
            baseline_coords = [float(value) for value in baseline_fields[1:]]
            if class_id != baseline_fields[0]:
                raise HL2VerificationError(f"positive class changed: {sample_id}")
            if coords[0] != baseline_coords[0] or coords[2] != baseline_coords[2]:
                raise HL2VerificationError(
                    f"positive horizontal label geometry changed: {sample_id}"
                )
            box = row.get("box")
            baseline_box = row.get("baseline_box")
            if not isinstance(box, dict) or not isinstance(baseline_box, dict):
                raise HL2VerificationError(f"positive box ledger missing: {sample_id}")
            for key in ("x0", "x1", "cx_norm", "w_norm"):
                if float(box[key]) != float(baseline_box[key]):
                    raise HL2VerificationError(
                        f"positive horizontal box ledger changed: {sample_id}"
                    )
            if box.get("contains_core_wicks_and_six_mas") is not True:
                raise HL2VerificationError(f"treatment box lost core: {sample_id}")
            class_counts[f"{row['split']}/{class_id}"] += 1

        max_window_end = max(max_window_end, str(row["window_end_time"]))
        if row.get("dependency_end_time"):
            max_dependency_end = max(max_dependency_end, str(row["dependency_end_time"]))
        if str(row["window_end_time"]) >= HOLDOUT_START:
            raise HL2VerificationError(f"render window reaches holdout: {sample_id}")
        if row.get("dependency_end_time") and str(row["dependency_end_time"]) >= HOLDOUT_START:
            raise HL2VerificationError(f"label dependency reaches holdout: {sample_id}")
        if number % 4000 == 0:
            print(f"HL2 independent QA {number:05d}/32000", flush=True)

    if dict(counts) != EXPECTED_COUNTS:
        raise HL2VerificationError(f"split composition drift: {dict(counts)}")
    array = np.asarray(changed_pixels, dtype=float)
    absolute_array = np.asarray(absolute_deltas, dtype=float)
    payload = {
        "schema_version": 1,
        "experiment_id": "exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-v1",
        "passed": True,
        "rows": len(treatment_rows),
        "ordered_sample_identity_parity": len(treatment_rows),
        "unique_dataset_sample_ids": len(sample_ids),
        "unique_treatment_image_hashes": len(image_hashes),
        "negative_label_byte_parity": 24000,
        "positive_labels_changed": sum(
            row["sample_kind"] == "positive"
            and not bool(row["label_byte_identical_to_baseline"])
            for row in treatment_rows
        ),
        "positive_horizontal_label_geometry_parity": 8000,
        "close_replay_null_exact_matches": len(treatment_rows),
        "images_changed_by_hl2": int(changed_images),
        "images_unchanged_by_hl2": len(treatment_rows) - int(changed_images),
        "changed_pixels": {
            "mean": float(array.mean()),
            "median": float(np.median(array)),
            "p95": float(np.quantile(array, 0.95)),
            "max": int(array.max()),
        },
        "absolute_channel_delta": {
            "mean": float(absolute_array.mean()),
            "median": float(np.median(absolute_array)),
            "p95": float(np.quantile(absolute_array, 0.95)),
            "max": int(absolute_array.max()),
        },
        "split_counts": dict(counts),
        "class_counts": dict(class_counts),
        "baseline_manifest_sha256": sha256_file(baseline_manifest),
        "treatment_manifest_sha256": sha256_file(treatment_manifest),
        "build_summary_sha256": sha256_file(treatment / "build_summary.json"),
        "max_rendered_window_end": max_window_end,
        "max_label_dependency_end": max_dependency_end,
        "holdout_start_exclusive": HOLDOUT_START,
        "holdout_dependency_rows": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE_DATASET)
    parser.add_argument("--treatment", type=Path, default=TREATMENT_DATASET)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESULTS / "independent_qa_receipt.json",
    )
    args = parser.parse_args()
    result = verify(
        baseline=args.baseline.resolve(),
        treatment=args.treatment.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
