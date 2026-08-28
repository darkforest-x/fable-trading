#!/usr/bin/env python3
"""Evaluate the frozen Grade-A chronological val split at one fixed threshold.

The evaluator reads only the immutable 1,200 positive and 3,600 empty-label
validation PNGs plus their manifest fields. It does not read OHLCV, tune a
threshold, consume holdout, or mutate runtime state. Predictions are preserved
one row per image so every aggregate can be independently recomputed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MANIFEST_SHA256 = (
    "22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2"
)
EXPECTED_VAL_COUNTS = {
    "positive": 1200,
    "positive_long": 531,
    "positive_short": 669,
    "negative": 3600,
    "negative_hard": 2400,
    "negative_easy": 1200,
    "positive_events": 155,
    "negative_events": 465,
}
CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
DIRECTION_CLASS = {"LONG": 0, "SHORT": 1}


class GradeAValError(ValueError):
    """Fail-closed frozen-validation contract error."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_yolo_label(path: Path) -> tuple[int, list[float]] | None:
    """Read one exact zero-or-one-box YOLO label."""

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    lines = text.splitlines()
    if len(lines) != 1:
        raise GradeAValError(f"expected one box in {path}, got {len(lines)}")
    fields = lines[0].split()
    if len(fields) != 5:
        raise GradeAValError(f"invalid YOLO label in {path}")
    raw_class = float(fields[0])
    class_id = int(raw_class)
    if raw_class != class_id or class_id not in CLASS_NAMES:
        raise GradeAValError(f"invalid class id in {path}: {fields[0]}")
    cx, cy, width, height = (float(value) for value in fields[1:])
    values = (cx, cy, width, height)
    if not all(math.isfinite(value) for value in values):
        raise GradeAValError(f"non-finite box in {path}")
    xyxy = [
        cx - width / 2,
        cy - height / 2,
        cx + width / 2,
        cy + height / 2,
    ]
    if not all(-1e-6 <= value <= 1.0 + 1e-6 for value in xyxy):
        raise GradeAValError(f"box outside image in {path}: {xyxy}")
    return class_id, xyxy


def load_val_rows(dataset: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Verify every frozen val file and return deterministic manifest rows."""

    manifest = dataset / "manifest.jsonl"
    if not manifest.is_file() or sha256_file(manifest) != EXPECTED_MANIFEST_SHA256:
        raise GradeAValError("Grade-A manifest identity drifted")
    rows = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line
    ]
    val = [row for row in rows if row.get("split") == "val"]
    positive = [row for row in val if row.get("sample_kind") == "positive"]
    negative = [row for row in val if row.get("sample_kind") == "negative"]
    actual = {
        "positive": len(positive),
        "positive_long": sum(row.get("direction") == "LONG" for row in positive),
        "positive_short": sum(row.get("direction") == "SHORT" for row in positive),
        "negative": len(negative),
        "negative_hard": sum(row.get("negative_kind") == "hard" for row in negative),
        "negative_easy": sum(row.get("negative_kind") == "easy" for row in negative),
        "positive_events": len({str(row["event_id"]) for row in positive}),
        "negative_events": len({str(row["negative_event_id"]) for row in negative}),
    }
    if actual != EXPECTED_VAL_COUNTS:
        raise GradeAValError(f"validation composition drifted: {actual}")

    sample_ids: set[str] = set()
    verified: list[dict[str, Any]] = []
    for source in val:
        row = dict(source)
        sample_id = str(row["dataset_sample_id"])
        if sample_id in sample_ids:
            raise GradeAValError(f"duplicate val sample id: {sample_id}")
        sample_ids.add(sample_id)
        image = dataset / str(row["image_path"])
        label = dataset / str(row["label_path"])
        if not image.is_file() or sha256_file(image) != str(row["image_sha256"]):
            raise GradeAValError(f"val image drifted: {sample_id}")
        if not label.is_file() or sha256_file(label) != str(row["label_sha256"]):
            raise GradeAValError(f"val label drifted: {sample_id}")
        parsed = _read_yolo_label(label)
        if row["sample_kind"] == "negative":
            if parsed is not None:
                raise GradeAValError(f"negative label is not empty: {sample_id}")
            row["ground_truth_class"] = None
            row["ground_truth_xyxy"] = None
        else:
            if parsed is None:
                raise GradeAValError(f"positive label is empty: {sample_id}")
            class_id, xyxy = parsed
            expected_class = DIRECTION_CLASS.get(str(row.get("direction")))
            if class_id != expected_class:
                raise GradeAValError(f"direction/class mismatch: {sample_id}")
            row["ground_truth_class"] = class_id
            row["ground_truth_xyxy"] = xyxy
        verified.append(row)
    return manifest, sorted(verified, key=lambda row: str(row["dataset_sample_id"]))


def normalized_iou(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute IoU for two normalized xyxy boxes."""

    x0 = max(float(left[0]), float(right[0]))
    y0 = max(float(left[1]), float(right[1]))
    x1 = min(float(left[2]), float(right[2]))
    y1 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, float(left[2]) - float(left[0])) * max(
        0.0, float(left[3]) - float(left[1])
    )
    right_area = max(0.0, float(right[2]) - float(right[0])) * max(
        0.0, float(right[3]) - float(right[1])
    )
    union = left_area + right_area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def score_prediction_row(
    row: Mapping[str, Any], predictions: Sequence[Mapping[str, Any]], *, match_iou: float
) -> dict[str, Any]:
    """Join one prediction list to its frozen label and event identity."""

    output: dict[str, Any] = {
        "dataset_sample_id": str(row["dataset_sample_id"]),
        "sample_kind": str(row["sample_kind"]),
        "boxes": len(predictions),
        "predictions": [dict(prediction) for prediction in predictions],
    }
    if row["sample_kind"] == "negative":
        output.update(
            {
                "negative_event_id": str(row["negative_event_id"]),
                "negative_kind": str(row["negative_kind"]),
            }
        )
        return output

    gt_class = int(row["ground_truth_class"])
    gt_xyxy = row["ground_truth_xyxy"]
    annotated: list[tuple[Mapping[str, Any], float]] = [
        (prediction, normalized_iou(gt_xyxy, prediction["xyxy_norm"]))
        for prediction in predictions
    ]
    same_class = [item for item in annotated if int(item[0]["class_id"]) == gt_class]
    qualifying = [item for item in same_class if item[1] >= match_iou]
    wrong_direction = [
        item
        for item in annotated
        if int(item[0]["class_id"]) != gt_class and item[1] >= match_iou
    ]
    best_same_iou = max((item[1] for item in same_class), default=0.0)
    best_hit_confidence = max(
        (float(item[0]["confidence"]) for item in qualifying), default=None
    )
    best_wrong_direction_confidence = max(
        (float(item[0]["confidence"]) for item in wrong_direction), default=None
    )
    output.update(
        {
            "event_id": str(row["event_id"]),
            "direction": str(row["direction"]),
            "post_bars": int(row["post_bars"]),
            "ground_truth_class": gt_class,
            "ground_truth_xyxy": [float(value) for value in gt_xyxy],
            "true_hit": bool(qualifying),
            "best_same_class_iou": float(best_same_iou),
            "best_hit_confidence": best_hit_confidence,
            "wrong_direction_overlap": bool(wrong_direction),
            "best_wrong_direction_confidence": best_wrong_direction_confidence,
            "extra_prediction_boxes": max(0, len(predictions) - (1 if qualifying else 0)),
        }
    )
    return output


def _confidence_summary(values: Iterable[float]) -> dict[str, float | None]:
    array = np.asarray(list(values), dtype=float)
    return {
        "min": None if not len(array) else float(array.min()),
        "median": None if not len(array) else float(np.median(array)),
        "p90": None if not len(array) else float(np.quantile(array, 0.9)),
        "max": None if not len(array) else float(array.max()),
    }


def summarize_negative_fires(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize false fires for one empty-label negative subset."""

    values = list(rows)
    if not values:
        raise GradeAValError("cannot summarize an empty negative subset")
    fired = [row for row in values if int(row["boxes"]) > 0]
    boxes = sum(int(row["boxes"]) for row in values)
    classes: Counter[str] = Counter()
    confidences: list[float] = []
    for row in fired:
        for prediction in row["predictions"]:
            classes[CLASS_NAMES[int(prediction["class_id"])]] += 1
            confidences.append(float(prediction["confidence"]))
    return {
        "images": len(values),
        "fired_images": len(fired),
        "fire_rate": len(fired) / len(values),
        "boxes": boxes,
        "false_boxes_per_1000_images": boxes / len(values) * 1000.0,
        "class_box_counts": dict(sorted(classes.items())),
        "confidence": _confidence_summary(confidences),
        "top_fired_samples": sorted(
            (
                {
                    "dataset_sample_id": str(row["dataset_sample_id"]),
                    "negative_kind": str(row["negative_kind"]),
                    "boxes": int(row["boxes"]),
                    "max_confidence": max(
                        float(item["confidence"]) for item in row["predictions"]
                    ),
                }
                for row in fired
            ),
            key=lambda row: (-row["max_confidence"], row["dataset_sample_id"]),
        )[:100],
    }


def summarize_positive_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize fixed-threshold label matches and duplicate predictions."""

    values = list(rows)
    if not values:
        raise GradeAValError("cannot summarize an empty positive subset")
    hits = [row for row in values if bool(row["true_hit"])]
    multiple = [row for row in values if int(row["boxes"]) > 1]
    return {
        "images": len(values),
        "true_hit_images": len(hits),
        "fixed_threshold_image_recall": len(hits) / len(values),
        "prediction_boxes": sum(int(row["boxes"]) for row in values),
        "images_with_multiple_boxes": len(multiple),
        "multiple_box_image_rate": len(multiple) / len(values),
        "extra_prediction_boxes": sum(int(row["extra_prediction_boxes"]) for row in values),
        "wrong_direction_overlap_images": sum(
            bool(row["wrong_direction_overlap"]) for row in values
        ),
        "hit_confidence": _confidence_summary(
            float(row["best_hit_confidence"]) for row in hits
        ),
        "best_same_class_iou": _confidence_summary(
            float(row["best_same_class_iou"]) for row in values
        ),
    }


def summarize_event_surface(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure earliest and any-variant event recall without counting variants as events."""

    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["event_id"])].append(row)
    if len(grouped) != EXPECTED_VAL_COUNTS["positive_events"]:
        raise GradeAValError(f"positive event count drifted: {len(grouped)}")

    first_hit_delays: list[int] = []
    earliest_available_hits = 0
    post2_events = post2_hits = 0
    per_post: defaultdict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for variants in grouped.values():
        variants = sorted(variants, key=lambda row: (int(row["post_bars"]), str(row["dataset_sample_id"])))
        posts = [int(row["post_bars"]) for row in variants]
        if len(posts) != len(set(posts)):
            raise GradeAValError("one event has duplicate post_bars variants")
        hits = [row for row in variants if bool(row["true_hit"])]
        if hits:
            first_hit_delays.append(min(int(row["post_bars"]) for row in hits))
        earliest_post = min(posts)
        earliest_available_hits += any(
            bool(row["true_hit"]) and int(row["post_bars"]) == earliest_post
            for row in variants
        )
        post2 = [row for row in variants if int(row["post_bars"]) == 2]
        if post2:
            post2_events += 1
            post2_hits += bool(post2[0]["true_hit"])
        for row in variants:
            per_post[int(row["post_bars"])].append(row)

    delay_array = np.asarray(first_hit_delays, dtype=int)
    events = len(grouped)
    return {
        "events": events,
        "events_with_post2_variant": post2_events,
        "post2_true_hit_events": post2_hits,
        "post2_true_hit_rate": post2_hits / post2_events,
        "earliest_available_true_hit_events": earliest_available_hits,
        "earliest_available_true_hit_rate": earliest_available_hits / events,
        "any_hit_events": len(first_hit_delays),
        "any_hit_event_recall": len(first_hit_delays) / events,
        "no_hit_events": events - len(first_hit_delays),
        "first_hit_post_bars": {
            "min": None if not len(delay_array) else int(delay_array.min()),
            "median": None if not len(delay_array) else float(np.median(delay_array)),
            "p90": None if not len(delay_array) else float(np.quantile(delay_array, 0.9)),
            "max": None if not len(delay_array) else int(delay_array.max()),
            "histogram": {
                str(post): int(np.sum(delay_array == post)) for post in sorted(set(first_hit_delays))
            },
        },
        "variant_recall_by_post_bars": {
            str(post): {
                "variants": len(values),
                "true_hits": sum(bool(row["true_hit"]) for row in values),
                "recall": sum(bool(row["true_hit"]) for row in values) / len(values),
            }
            for post, values in sorted(per_post.items())
        },
    }


def _two_sided_exact_sign_p(left_only: int, right_only: int) -> float:
    """Return the exact two-sided p-value for paired discordant outcomes."""

    discordant = int(left_only) + int(right_only)
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index) for index in range(min(left_only, right_only) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def summarize_direction_flip_null(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare correct-class hits with the same boxes under flipped direction labels."""

    values = list(rows)
    if not values:
        raise GradeAValError("cannot summarize an empty direction-flip null")
    actual_only = sum(
        bool(row["true_hit"]) and not bool(row["wrong_direction_overlap"])
        for row in values
    )
    flipped_only = sum(
        bool(row["wrong_direction_overlap"]) and not bool(row["true_hit"])
        for row in values
    )
    both = sum(
        bool(row["true_hit"]) and bool(row["wrong_direction_overlap"])
        for row in values
    )
    neither = len(values) - actual_only - flipped_only - both

    by_event: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in values:
        by_event[str(row["event_id"])].append(row)
    event_actual_only = event_flipped_only = event_both = 0
    for variants in by_event.values():
        actual = any(bool(row["true_hit"]) for row in variants)
        flipped = any(bool(row["wrong_direction_overlap"]) for row in variants)
        event_actual_only += actual and not flipped
        event_flipped_only += flipped and not actual
        event_both += actual and flipped
    event_neither = len(by_event) - event_actual_only - event_flipped_only - event_both

    actual_hits = actual_only + both
    flipped_hits = flipped_only + both
    event_actual_hits = event_actual_only + event_both
    event_flipped_hits = event_flipped_only + event_both
    return {
        "null_hypothesis": "Swap LONG and SHORT class ids while keeping every predicted box, confidence, image and IoU unchanged.",
        "image_level": {
            "images": len(values),
            "actual_correct_class_hits": actual_hits,
            "actual_correct_class_recall": actual_hits / len(values),
            "flipped_class_hits": flipped_hits,
            "flipped_class_recall": flipped_hits / len(values),
            "recall_delta": (actual_hits - flipped_hits) / len(values),
            "actual_only": actual_only,
            "flipped_only": flipped_only,
            "both": both,
            "neither": neither,
            "paired_exact_two_sided_p": _two_sided_exact_sign_p(
                actual_only, flipped_only
            ),
        },
        "event_level": {
            "events": len(by_event),
            "actual_correct_class_any_hit_events": event_actual_hits,
            "actual_correct_class_any_hit_recall": event_actual_hits / len(by_event),
            "flipped_class_any_hit_events": event_flipped_hits,
            "flipped_class_any_hit_recall": event_flipped_hits / len(by_event),
            "recall_delta": (event_actual_hits - event_flipped_hits) / len(by_event),
            "actual_only": event_actual_only,
            "flipped_only": event_flipped_only,
            "both": event_both,
            "neither": event_neither,
            "paired_exact_two_sided_p": _two_sided_exact_sign_p(
                event_actual_only, event_flipped_only
            ),
        },
    }


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic UTF-8 JSONL without changing prediction order."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def evaluate(
    *,
    weights: Path,
    expected_weights_sha256: str,
    dataset: Path,
    output: Path,
    predictions_output: Path,
    experiment_id: str,
    generator_commit: str,
    device: str,
    batch: int,
    imgsz: int,
    confidence: float,
    nms_iou: float,
    match_iou: float,
) -> dict[str, Any]:
    """Run the frozen fixed-threshold evaluation over all 4,800 val images."""

    for path in (output, predictions_output):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite evaluation artifact: {path}")
    actual_weights_sha = sha256_file(weights)
    if actual_weights_sha != expected_weights_sha256:
        raise GradeAValError("weight identity drifted")
    manifest, rows = load_val_rows(dataset)

    import torch
    import ultralytics
    from ultralytics import YOLO

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != CLASS_NAMES:
        raise GradeAValError(f"unexpected model classes: {names}")

    scored: list[dict[str, Any]] = []
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        results = model.predict(
            source=[str(dataset / str(row["image_path"])) for row in chunk],
            imgsz=imgsz,
            conf=confidence,
            iou=nms_iou,
            batch=batch,
            device=device,
            stream=False,
            verbose=False,
            save=False,
            max_det=300,
        )
        if len(results) != len(chunk):
            raise GradeAValError("prediction result count drifted")
        for row, result in zip(chunk, results):
            boxes = result.boxes
            predictions: list[dict[str, Any]] = []
            if boxes is not None and len(boxes):
                classes = boxes.cls.detach().cpu().numpy().astype(int)
                confidences = boxes.conf.detach().cpu().numpy().astype(float)
                coordinates = boxes.xyxyn.detach().cpu().numpy().astype(float)
                predictions = [
                    {
                        "class_id": int(class_id),
                        "class_name": CLASS_NAMES[int(class_id)],
                        "confidence": float(confidence_value),
                        "xyxy_norm": [float(value) for value in xyxy],
                    }
                    for class_id, confidence_value, xyxy in zip(
                        classes, confidences, coordinates
                    )
                ]
            scored.append(score_prediction_row(row, predictions, match_iou=match_iou))

    write_jsonl(predictions_output, scored)
    positives = [row for row in scored if row["sample_kind"] == "positive"]
    negatives = [row for row in scored if row["sample_kind"] == "negative"]
    hard = [row for row in negatives if row["negative_kind"] == "hard"]
    easy = [row for row in negatives if row["negative_kind"] == "easy"]
    long_rows = [row for row in positives if row["direction"] == "LONG"]
    short_rows = [row for row in positives if row["direction"] == "SHORT"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "generator_commit": generator_commit,
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "weights_path": str(weights),
        "weights_sha256": actual_weights_sha,
        "dataset": str(dataset),
        "manifest_sha256": sha256_file(manifest),
        "validation_counts": EXPECTED_VAL_COUNTS,
        "class_names": names,
        "imgsz": imgsz,
        "confidence_threshold": confidence,
        "nms_iou": nms_iou,
        "true_hit_iou": match_iou,
        "threshold_tuned": False,
        "positive_images": {
            "all": summarize_positive_rows(positives),
            "dense_long": summarize_positive_rows(long_rows),
            "dense_short": summarize_positive_rows(short_rows),
        },
        "event_surface": summarize_event_surface(positives),
        "direction_flip_null": summarize_direction_flip_null(positives),
        "negative_images": {
            "all": summarize_negative_fires(negatives),
            "hard": summarize_negative_fires(hard),
            "easy": summarize_negative_fires(easy),
        },
        "predictions_jsonl": str(predictions_output),
        "predictions_sha256": sha256_file(predictions_output),
        "predictions_size_bytes": predictions_output.stat().st_size,
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "numpy": np.__version__,
            "device": device,
            "batch": batch,
        },
        "holdout_consumed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "completed_history_not_live_tip": True,
        "production_eligible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--weights-sha256", required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_v1",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--predictions-output", type=Path, required=True)
    parser.add_argument(
        "--experiment-id",
        default="exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1",
    )
    parser.add_argument("--generator-commit", required=True)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.7)
    parser.add_argument("--match-iou", type=float, default=0.5)
    args = parser.parse_args()
    payload = evaluate(
        weights=args.weights.resolve(),
        expected_weights_sha256=args.weights_sha256,
        dataset=args.dataset.resolve(),
        output=args.output.resolve(),
        predictions_output=args.predictions_output.resolve(),
        experiment_id=args.experiment_id,
        generator_commit=args.generator_commit,
        device=args.device,
        batch=args.batch,
        imgsz=args.imgsz,
        confidence=args.confidence,
        nms_iou=args.nms_iou,
        match_iou=args.match_iou,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
