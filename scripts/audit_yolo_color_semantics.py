#!/usr/bin/env python3
"""Audit candle/MA color reliance of the frozen Grade-A YOLO detector.

This script is a read-only, fixed-weight, pre-holdout perturbation audit.  It
selects one earliest available positive variant per validation event and the
three matched negative rows for the same positive sample/variant.  Every image
is rebuilt from its causal CSV prefix, and the baseline PNG must match the
manifest SHA before any prediction is accepted.

Color treatments change either candle polarity colors or six-MA identity
colors while holding OHLC geometry, MA values, chart transform, labels, model,
thresholds and sample identities fixed.  The resulting effects measure what
the existing weights rely on under out-of-distribution color perturbations;
they do not estimate how a newly retrained monochrome model would perform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import tempfile
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from unittest.mock import patch

import cv2
import numpy as np
from scipy.stats import binomtest

from scripts.evaluate_15m_ma_launch_owner_grade_a8000_val import (
    CLASS_NAMES,
    load_val_rows,
    score_prediction_row,
    sha256_file,
    summarize_negative_fires,
    summarize_positive_rows,
    write_jsonl,
)
from yoyo.datasets.window_render import enrich, load_prefix, window_slice
from yoyo.layers.l1_detection import render as render_module
from yoyo.layers.l1_detection.data import ALL_MA_COLS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "analysis/output/p1_yolo_color_semantics_audit_20260902"
DEFAULT_PREREG = DEFAULT_OUTPUT_DIR / "preregistration.json"
DEFAULT_PREREG_AMENDMENT = DEFAULT_OUTPUT_DIR / "preregistration_amendment_1.json"
DEFAULT_DATASET = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
DEFAULT_WEIGHTS = (
    ROOT
    / "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1"
    / "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960/weights/best.pt"
)
DEFAULT_FROZEN_LEDGER = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1"
    / "results/frozen_val_predictions.jsonl"
)
EXPECTED_PREREG_SHA256 = "0d8a429ceadb057a2d6aa30320f610e9a59a53b607c2cc1818d27eac0aea7589"
EXPECTED_PREREG_AMENDMENT_SHA256 = "a4a9d8ce5906241251810567c9cf35be2c1ccd1eb274c985f9880f5f58976585"
EXPECTED_MANIFEST_SHA256 = "22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2"
EXPECTED_WEIGHTS_SHA256 = "0524e78086face6ccba0f2bb220dadada4555a914c64a4e6794f620fa0d9103f"
HOLDOUT_START = np.datetime64("2026-05-04T00:00:00")
SEED = 20260902
BOOTSTRAP_REPLICATES = 20_000

ORIGINAL_CANDLE_GREEN = tuple(render_module.CANDLE_GREEN)
ORIGINAL_CANDLE_RED = tuple(render_module.CANDLE_RED)
ORIGINAL_MA_COLORS = dict(render_module.MA_COLORS)
CANDLE_MONO = tuple(
    int(value)
    for value in np.rint(np.mean([ORIGINAL_CANDLE_GREEN, ORIGINAL_CANDLE_RED], axis=0))
)
MA_MONO = tuple(
    int(value)
    for value in np.rint(np.mean(list(ORIGINAL_MA_COLORS.values()), axis=0))
)
MA_COLOR_CYCLE = {
    column: ORIGINAL_MA_COLORS[ALL_MA_COLS[(index + 1) % len(ALL_MA_COLS)]]
    for index, column in enumerate(ALL_MA_COLS)
}
TREATMENTS = (
    "candle_monochrome",
    "candle_polarity_swap",
    "ma_monochrome",
    "ma_palette_cycle",
)
RENDER_CONDITIONS = ("baseline",) + TREATMENTS
RUN_ORDER = (
    "candle_monochrome",
    "ma_monochrome",
    "baseline",
    "ma_palette_cycle",
    "candle_polarity_swap",
    "baseline_repeat",
)


class ColorAuditError(RuntimeError):
    """A frozen audit contract or parity condition failed."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_preregistration(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ColorAuditError(f"missing preregistration: {path}")
    if EXPECTED_PREREG_SHA256 and sha256_file(path) != EXPECTED_PREREG_SHA256:
        raise ColorAuditError("preregistration identity drifted")
    prereg = json.loads(path.read_text(encoding="utf-8"))
    if tuple(prereg["seeded_run_order_with_baseline_repeat_forced_last"]) != RUN_ORDER:
        raise ColorAuditError("preregistered run order drifted")
    if int(prereg["seed"]) != SEED:
        raise ColorAuditError("preregistered seed drifted")
    return prereg


def select_audit_rows(dataset: Path) -> list[dict[str, Any]]:
    """Select 155 earliest positives plus their exact 2-hard/1-easy matches."""
    manifest, rows = load_val_rows(dataset)
    if sha256_file(manifest) != EXPECTED_MANIFEST_SHA256:
        raise ColorAuditError("manifest identity drifted")
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]

    earliest_by_event: dict[str, dict[str, Any]] = {}
    for row in sorted(
        positives,
        key=lambda item: (
            str(item["event_id"]),
            int(item["post_bars"]),
            str(item["dataset_sample_id"]),
        ),
    ):
        earliest_by_event.setdefault(str(row["event_id"]), row)
    selected_positives = list(earliest_by_event.values())
    positive_keys = {
        (str(row["sample_id"]), str(row["variant_id"])): str(row["event_id"])
        for row in selected_positives
    }
    selected_negatives = [
        row
        for row in negatives
        if (str(row["paired_positive_sample_id"]), str(row["variant_id"]))
        in positive_keys
    ]
    if len(selected_positives) != 155 or len(selected_negatives) != 465:
        raise ColorAuditError(
            f"selection drifted: positives={len(selected_positives)} negatives={len(selected_negatives)}"
        )

    per_block: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    selected: list[dict[str, Any]] = []
    for source in selected_positives + selected_negatives:
        row = dict(source)
        if row["sample_kind"] == "positive":
            block = str(row["event_id"])
        else:
            block = str(row["paired_positive_event_id"])
        row["audit_block_id"] = block
        per_block[block].append(row)
        selected.append(row)
    if len(per_block) != 155:
        raise ColorAuditError(f"audit block count drifted: {len(per_block)}")
    for block, members in per_block.items():
        kinds = [member["sample_kind"] for member in members]
        negative_kinds = sorted(
            str(member["negative_kind"])
            for member in members
            if member["sample_kind"] == "negative"
        )
        if kinds.count("positive") != 1 or negative_kinds != ["easy", "hard", "hard"]:
            raise ColorAuditError(f"bad 1+3 block {block}: {negative_kinds}")

    starts = np.asarray(
        [np.datetime64(str(row["window_start_time"]).replace("+00:00", "")) for row in selected]
    )
    ends = np.asarray(
        [np.datetime64(str(row["window_end_time"]).replace("+00:00", "")) for row in selected]
    )
    if ends.max() >= HOLDOUT_START:
        raise ColorAuditError(f"holdout boundary violated: max visible end {ends.max()}")
    if str(starts.min()) != "2025-12-02T22:00:00" or str(ends.max()) != "2026-05-03T18:45:00":
        raise ColorAuditError(f"visible time range drifted: {starts.min()} .. {ends.max()}")
    return sorted(selected, key=lambda row: str(row["dataset_sample_id"]))


def condition_palette(
    condition: str,
) -> tuple[tuple[int, int, int], tuple[int, int, int], dict[str, tuple[int, int, int]]]:
    if condition == "baseline":
        return ORIGINAL_CANDLE_GREEN, ORIGINAL_CANDLE_RED, dict(ORIGINAL_MA_COLORS)
    if condition == "candle_monochrome":
        return CANDLE_MONO, CANDLE_MONO, dict(ORIGINAL_MA_COLORS)
    if condition == "candle_polarity_swap":
        return ORIGINAL_CANDLE_RED, ORIGINAL_CANDLE_GREEN, dict(ORIGINAL_MA_COLORS)
    if condition == "ma_monochrome":
        return ORIGINAL_CANDLE_GREEN, ORIGINAL_CANDLE_RED, {
            column: MA_MONO for column in ALL_MA_COLS
        }
    if condition == "ma_palette_cycle":
        return ORIGINAL_CANDLE_GREEN, ORIGINAL_CANDLE_RED, dict(MA_COLOR_CYCLE)
    raise ColorAuditError(f"unknown render condition: {condition}")


def render_condition(window, condition: str) -> np.ndarray:
    candle_green, candle_red, ma_colors = condition_palette(condition)
    with ExitStack() as stack:
        stack.enter_context(patch.object(render_module, "CANDLE_GREEN", candle_green))
        stack.enter_context(patch.object(render_module, "CANDLE_RED", candle_red))
        stack.enter_context(patch.object(render_module, "MA_COLORS", ma_colors))
        image, _ = render_module.render_chart(window, out_path=None)
    return image


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise ColorAuditError(f"failed to write temporary PNG: {path}")


def render_all_conditions(
    rows: Sequence[dict[str, Any]], temp_root: Path, dataset: Path
) -> tuple[dict[str, dict[str, Path]], dict[str, Any]]:
    """Re-render every sample, enforcing baseline bytes and color-only support parity."""
    by_source: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[str(row["source_path"])].append(row)
    output_paths: dict[str, dict[str, Path]] = {
        condition: {} for condition in RENDER_CONDITIONS
    }
    changed_fractions: defaultdict[str, list[float]] = defaultdict(list)
    support_mismatches: defaultdict[str, int] = defaultdict(int)
    baseline_container_sha_matches = 0
    baseline_decoded_pixel_matches = 0

    for relative_source, source_rows in sorted(by_source.items()):
        required_end = max(int(row["window_end_i"]) for row in source_rows)
        enriched = enrich(load_prefix(ROOT / relative_source, required_end))
        for row in sorted(source_rows, key=lambda item: str(item["dataset_sample_id"])):
            sample_id = str(row["dataset_sample_id"])
            window = window_slice(
                enriched, int(row["window_start_i"]), int(row["window_end_i"])
            )
            images = {
                condition: render_condition(window, condition)
                for condition in RENDER_CONDITIONS
            }
            baseline = images["baseline"]
            original = cv2.imread(
                str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR
            )
            if original is None:
                raise ColorAuditError(f"cannot decode manifest PNG: {sample_id}")
            if (
                original.shape != baseline.shape
                or original.dtype != baseline.dtype
                or not np.array_equal(original, baseline)
            ):
                difference = (
                    -1
                    if original.shape != baseline.shape
                    else int(np.count_nonzero(np.any(original != baseline, axis=2)))
                )
                raise ColorAuditError(
                    f"baseline decoded pixel mismatch: {sample_id}, pixels={difference}"
                )
            baseline_decoded_pixel_matches += 1
            baseline_support = np.any(
                baseline != np.asarray(render_module.BG, dtype=np.uint8), axis=2
            )
            for condition, image in images.items():
                path = temp_root / condition / f"{sample_id}.png"
                write_png(path, image)
                output_paths[condition][sample_id] = path
                if condition == "baseline":
                    baseline_container_sha_matches += (
                        sha256_file(path) == str(row["image_sha256"])
                    )
                    continue
                support = np.any(
                    image != np.asarray(render_module.BG, dtype=np.uint8), axis=2
                )
                mismatch = int(np.count_nonzero(support != baseline_support))
                support_mismatches[condition] += mismatch
                changed_fractions[condition].append(
                    float(np.any(image != baseline, axis=2).mean())
                )

    if baseline_decoded_pixel_matches != len(rows):
        raise ColorAuditError(
            "baseline decoded pixel parity count drifted: "
            f"{baseline_decoded_pixel_matches}/{len(rows)}"
        )
    pixel_audit = {
        "baseline_decoded_pixel_matches": baseline_decoded_pixel_matches,
        "baseline_decoded_pixel_mismatches": len(rows) - baseline_decoded_pixel_matches,
        "rebuilt_png_container_sha_matches": int(baseline_container_sha_matches),
        "rebuilt_png_container_sha_mismatches": len(rows)
        - int(baseline_container_sha_matches),
        "conditions": {
            condition: {
                "foreground_support_mismatch_pixels": int(support_mismatches[condition]),
                "mean_changed_pixel_fraction": float(np.mean(changed_fractions[condition])),
                "median_changed_pixel_fraction": float(np.median(changed_fractions[condition])),
                "max_changed_pixel_fraction": float(np.max(changed_fractions[condition])),
            }
            for condition in TREATMENTS
        },
    }
    return output_paths, pixel_audit


def predictions_from_result(result) -> list[dict[str, Any]]:
    boxes = result.boxes
    if boxes is None or not len(boxes):
        return []
    classes = boxes.cls.detach().cpu().numpy().astype(int)
    confidences = boxes.conf.detach().cpu().numpy().astype(float)
    coordinates = boxes.xyxyn.detach().cpu().numpy().astype(float)
    return [
        {
            "class_id": int(class_id),
            "class_name": CLASS_NAMES[int(class_id)],
            "confidence": float(confidence),
            "xyxy_norm": [float(value) for value in xyxy],
        }
        for class_id, confidence, xyxy in zip(classes, confidences, coordinates)
    ]


def predict_condition(
    model,
    rows: Sequence[dict[str, Any]],
    paths: Mapping[str, Path],
    *,
    condition: str,
    device: str,
    batch: int,
    imgsz: int,
    confidence: float,
    nms_iou: float,
    match_iou: float,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        results = model.predict(
            source=[str(paths[str(row["dataset_sample_id"])]) for row in chunk],
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
            raise ColorAuditError(f"prediction count drifted for {condition}")
        for row, result in zip(chunk, results):
            output = score_prediction_row(
                row, predictions_from_result(result), match_iou=match_iou
            )
            output["condition"] = condition
            output["audit_block_id"] = str(row["audit_block_id"])
            scored.append(output)
    return scored


def condition_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]
    long_rows = [row for row in positives if row["direction"] == "LONG"]
    short_rows = [row for row in positives if row["direction"] == "SHORT"]
    hard = [row for row in negatives if row["negative_kind"] == "hard"]
    easy = [row for row in negatives if row["negative_kind"] == "easy"]
    return {
        "positive": {
            "all": summarize_positive_rows(positives),
            "long": summarize_positive_rows(long_rows),
            "short": summarize_positive_rows(short_rows),
            "any_box_images": sum(int(row["boxes"]) > 0 for row in positives),
            "wrong_direction_overlap_images": sum(
                bool(row["wrong_direction_overlap"]) for row in positives
            ),
        },
        "negative": {
            "all": summarize_negative_fires(negatives),
            "hard": summarize_negative_fires(hard),
            "easy": summarize_negative_fires(easy),
        },
    }


def paired_endpoint(row: Mapping[str, Any]) -> int:
    if row["sample_kind"] == "positive":
        return int(bool(row["true_hit"]))
    return int(int(row["boxes"]) > 0)


def bootstrap_delta_ci(
    baseline: Sequence[dict[str, Any]],
    treatment: Sequence[dict[str, Any]],
    *,
    sample_kind: str,
    seed: int,
) -> tuple[float, float]:
    base = {str(row["dataset_sample_id"]): row for row in baseline if row["sample_kind"] == sample_kind}
    treated = {
        str(row["dataset_sample_id"]): row
        for row in treatment
        if row["sample_kind"] == sample_kind
    }
    if set(base) != set(treated):
        raise ColorAuditError(f"paired {sample_kind} identities drifted")
    by_block: defaultdict[str, list[float]] = defaultdict(list)
    for sample_id, base_row in base.items():
        treated_row = treated[sample_id]
        if base_row["audit_block_id"] != treated_row["audit_block_id"]:
            raise ColorAuditError("audit block identity drifted")
        by_block[str(base_row["audit_block_id"])].append(
            float(paired_endpoint(treated_row) - paired_endpoint(base_row))
        )
    blocks = sorted(by_block)
    rng = np.random.default_rng(seed)
    boot = np.empty(BOOTSTRAP_REPLICATES, dtype=float)
    for index in range(BOOTSTRAP_REPLICATES):
        sampled = rng.integers(0, len(blocks), size=len(blocks))
        values = [delta for position in sampled for delta in by_block[blocks[position]]]
        boot[index] = float(np.mean(values))
    return float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))


def paired_comparison(
    baseline: Sequence[dict[str, Any]],
    treatment: Sequence[dict[str, Any]],
    *,
    sample_kind: str,
    seed: int,
) -> dict[str, Any]:
    base = {str(row["dataset_sample_id"]): row for row in baseline if row["sample_kind"] == sample_kind}
    treated = {
        str(row["dataset_sample_id"]): row
        for row in treatment
        if row["sample_kind"] == sample_kind
    }
    if set(base) != set(treated):
        raise ColorAuditError(f"paired {sample_kind} sample set drifted")
    base_values = np.asarray([paired_endpoint(base[key]) for key in sorted(base)], dtype=int)
    treated_values = np.asarray([paired_endpoint(treated[key]) for key in sorted(base)], dtype=int)
    base_only = int(np.sum((base_values == 1) & (treated_values == 0)))
    treatment_only = int(np.sum((base_values == 0) & (treated_values == 1)))
    both = int(np.sum((base_values == 1) & (treated_values == 1)))
    neither = int(len(base_values) - base_only - treatment_only - both)
    discordant = base_only + treatment_only
    exact_p = (
        1.0
        if discordant == 0
        else float(
            binomtest(
                k=min(base_only, treatment_only),
                n=discordant,
                p=0.5,
                alternative="two-sided",
            ).pvalue
        )
    )
    ci_low, ci_high = bootstrap_delta_ci(
        baseline, treatment, sample_kind=sample_kind, seed=seed
    )
    return {
        "n_images": int(len(base_values)),
        "baseline_rate": float(base_values.mean()),
        "treatment_rate": float(treated_values.mean()),
        "treatment_minus_baseline": float(treated_values.mean() - base_values.mean()),
        "block_bootstrap_ci95": [ci_low, ci_high],
        "baseline_only": base_only,
        "treatment_only": treatment_only,
        "both": both,
        "neither": neither,
        "mcnemar_exact_two_sided_p": exact_p,
    }


def holm_adjust(raw: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(raw, key=lambda key: (raw[key], key))
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, key in enumerate(ordered):
        candidate = min(1.0, float(raw[key]) * (total - rank))
        running = max(running, candidate)
        adjusted[key] = running
    return adjusted


def compare_prediction_ledgers(
    left: Sequence[dict[str, Any]], right: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    left_map = {str(row["dataset_sample_id"]): row for row in left}
    right_map = {str(row["dataset_sample_id"]): row for row in right}
    if set(left_map) != set(right_map):
        raise ColorAuditError("prediction ledger identities drifted")
    discrete_mismatches = 0
    numeric_unmatched = 0
    max_confidence_delta = 0.0
    max_coordinate_delta = 0.0
    for sample_id in sorted(left_map):
        left_row, right_row = left_map[sample_id], right_map[sample_id]
        discrete_fields = ["boxes", "sample_kind"]
        if left_row["sample_kind"] == "positive":
            discrete_fields.extend(["true_hit", "wrong_direction_overlap"])
        if any(left_row.get(field) != right_row.get(field) for field in discrete_fields):
            discrete_mismatches += 1
        left_predictions = left_row["predictions"]
        right_predictions = right_row["predictions"]
        if len(left_predictions) != len(right_predictions) or [
            int(item["class_id"]) for item in left_predictions
        ] != [int(item["class_id"]) for item in right_predictions]:
            numeric_unmatched += 1
            continue
        for left_prediction, right_prediction in zip(left_predictions, right_predictions):
            max_confidence_delta = max(
                max_confidence_delta,
                abs(
                    float(left_prediction["confidence"])
                    - float(right_prediction["confidence"])
                ),
            )
            max_coordinate_delta = max(
                max_coordinate_delta,
                float(
                    np.max(
                        np.abs(
                            np.asarray(left_prediction["xyxy_norm"], dtype=float)
                            - np.asarray(right_prediction["xyxy_norm"], dtype=float)
                        )
                    )
                ),
            )
    return {
        "images": len(left_map),
        "discrete_mismatch_images": discrete_mismatches,
        "prediction_sequence_unmatched_images": numeric_unmatched,
        "max_confidence_abs_delta_on_matched_sequences": max_confidence_delta,
        "max_coordinate_abs_delta_on_matched_sequences": max_coordinate_delta,
    }


def compare_to_frozen_reference(
    baseline: Sequence[dict[str, Any]], frozen_ledger: Path
) -> dict[str, Any]:
    frozen = read_jsonl(frozen_ledger)
    selected_ids = {str(row["dataset_sample_id"]) for row in baseline}
    selected_frozen = [row for row in frozen if str(row["dataset_sample_id"]) in selected_ids]
    if len(selected_frozen) != len(baseline):
        raise ColorAuditError("frozen reference does not cover the audit subset")
    return compare_prediction_ledgers(selected_frozen, baseline)


def build_contact_sheet(
    paths: Mapping[str, Mapping[str, Path]],
    rows: Sequence[dict[str, Any]],
    output: Path,
) -> None:
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]
    preview_rows = [
        next(row for row in positives if row["direction"] == "LONG"),
        next(row for row in positives if row["direction"] == "SHORT"),
        next(row for row in negatives if row["negative_kind"] == "hard"),
        next(row for row in negatives if row["negative_kind"] == "easy"),
    ]
    display_conditions = (
        "baseline",
        "candle_monochrome",
        "candle_polarity_swap",
        "ma_monochrome",
        "ma_palette_cycle",
    )
    cell_width = 384
    cell_height = int(round(render_module.IMG_HEIGHT * cell_width / render_module.IMG_WIDTH))
    header_height = 34
    rows_out = []
    for row in preview_rows:
        sample_id = str(row["dataset_sample_id"])
        cells = []
        for condition in display_conditions:
            image = cv2.imread(str(paths[condition][sample_id]), cv2.IMREAD_COLOR)
            if image is None:
                raise ColorAuditError(f"missing preview image: {condition}/{sample_id}")
            thumb = cv2.resize(image, (cell_width, cell_height), interpolation=cv2.INTER_AREA)
            header = np.full((header_height, cell_width, 3), 245, dtype=np.uint8)
            label = condition
            if condition == "baseline":
                kind = (
                    str(row["direction"])
                    if row["sample_kind"] == "positive"
                    else str(row["negative_kind"])
                )
                label = f"{kind} | {condition}"
            cv2.putText(
                header,
                label,
                (8, 23),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (30, 30, 30),
                1,
                cv2.LINE_AA,
            )
            cells.append(np.vstack([header, thumb]))
        rows_out.append(np.hstack(cells))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), np.vstack(rows_out)):
        raise ColorAuditError(f"failed to write contact sheet: {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--frozen-ledger", type=Path, default=DEFAULT_FROZEN_LEDGER)
    parser.add_argument("--preregistration", type=Path, default=DEFAULT_PREREG)
    parser.add_argument(
        "--preregistration-amendment",
        type=Path,
        default=DEFAULT_PREREG_AMENDMENT,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.7)
    parser.add_argument("--match-iou", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = args.dataset.resolve()
    weights = args.weights.resolve()
    frozen_ledger = args.frozen_ledger.resolve()
    prereg_path = args.preregistration.resolve()
    prereg_amendment_path = args.preregistration_amendment.resolve()
    output_dir = args.output_dir.resolve()
    results_path = output_dir / "results.json"
    predictions_dir = output_dir / "predictions"
    contact_sheet = output_dir / "color_conditions_contact_sheet.png"
    for path in (results_path, contact_sheet):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite audit artifact: {path}")
    if predictions_dir.exists():
        raise FileExistsError(f"refusing to overwrite audit predictions: {predictions_dir}")

    prereg = load_preregistration(prereg_path)
    if (
        not prereg_amendment_path.is_file()
        or sha256_file(prereg_amendment_path) != EXPECTED_PREREG_AMENDMENT_SHA256
    ):
        raise ColorAuditError("preregistration amendment identity drifted")
    prereg_amendment = json.loads(
        prereg_amendment_path.read_text(encoding="utf-8")
    )
    if not bool(prereg_amendment["created_before_any_model_prediction"]):
        raise ColorAuditError("preregistration amendment timing is invalid")
    if sha256_file(weights) != EXPECTED_WEIGHTS_SHA256:
        raise ColorAuditError("weight identity drifted")
    rows = select_audit_rows(dataset)

    import torch
    import ultralytics
    from ultralytics import YOLO

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != CLASS_NAMES:
        raise ColorAuditError(f"model class map drifted: {names}")

    ledgers: dict[str, list[dict[str, Any]]] = {}
    with tempfile.TemporaryDirectory(prefix="fable-color-audit-") as temp:
        temp_root = Path(temp)
        rendered_paths, pixel_audit = render_all_conditions(rows, temp_root, dataset)
        for condition in RUN_ORDER:
            render_key = "baseline" if condition == "baseline_repeat" else condition
            ledgers[condition] = predict_condition(
                model,
                rows,
                rendered_paths[render_key],
                condition=condition,
                device=args.device,
                batch=args.batch,
                imgsz=args.imgsz,
                confidence=args.confidence,
                nms_iou=args.nms_iou,
                match_iou=args.match_iou,
            )
        build_contact_sheet(rendered_paths, rows, contact_sheet)

    summaries = {condition: condition_summary(ledgers[condition]) for condition in RUN_ORDER}
    comparisons: dict[str, dict[str, Any]] = {}
    for treatment_index, treatment in enumerate(TREATMENTS):
        comparisons[treatment] = {
            "positive": paired_comparison(
                ledgers["baseline"],
                ledgers[treatment],
                sample_kind="positive",
                seed=SEED + 100 + treatment_index,
            ),
            "negative": paired_comparison(
                ledgers["baseline"],
                ledgers[treatment],
                sample_kind="negative",
                seed=SEED + 200 + treatment_index,
            ),
        }
    for endpoint in ("positive", "negative"):
        adjusted = holm_adjust(
            {
                treatment: float(
                    comparisons[treatment][endpoint]["mcnemar_exact_two_sided_p"]
                )
                for treatment in TREATMENTS
            }
        )
        for treatment in TREATMENTS:
            comparisons[treatment][endpoint]["holm_adjusted_p_across_four_treatments"] = float(
                adjusted[treatment]
            )

    determinism = compare_prediction_ledgers(
        ledgers["baseline"], ledgers["baseline_repeat"]
    )
    frozen_reference = compare_to_frozen_reference(ledgers["baseline"], frozen_ledger)

    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_receipts = {}
    for condition in RUN_ORDER:
        path = predictions_dir / f"{condition}.jsonl"
        write_jsonl(path, ledgers[condition])
        prediction_receipts[condition] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    payload = {
        "schema_version": 1,
        "audit_id": str(prereg["audit_id"]),
        "classification": str(prereg["classification"]),
        "preregistration": {
            "path": str(prereg_path.relative_to(ROOT)),
            "sha256": sha256_file(prereg_path),
            "amendment_path": str(prereg_amendment_path.relative_to(ROOT)),
            "amendment_sha256": sha256_file(prereg_amendment_path),
        },
        "generator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "dataset": {
            "path": str(dataset.relative_to(ROOT)),
            "manifest_sha256": sha256_file(dataset / "manifest.jsonl"),
            "positive_images": 155,
            "negative_images": 465,
            "hard_negative_images": 310,
            "easy_negative_images": 155,
            "audit_blocks": 155,
            "min_visible_start": "2025-12-02T22:00:00+00:00",
            "max_visible_end": "2026-05-03T18:45:00+00:00",
        },
        "weights": {
            "path": str(weights.relative_to(ROOT)),
            "sha256": sha256_file(weights),
        },
        "frozen_inference": {
            "imgsz": args.imgsz,
            "confidence": args.confidence,
            "nms_iou": args.nms_iou,
            "match_iou": args.match_iou,
            "device": args.device,
            "batch": args.batch,
            "max_det": 300,
            "seed": SEED,
            "run_order": list(RUN_ORDER),
        },
        "palettes_bgr": {
            "original_candle_up": list(ORIGINAL_CANDLE_GREEN),
            "original_candle_down": list(ORIGINAL_CANDLE_RED),
            "candle_monochrome": list(CANDLE_MONO),
            "original_ma": {key: list(value) for key, value in ORIGINAL_MA_COLORS.items()},
            "ma_monochrome": list(MA_MONO),
            "ma_palette_cycle": {key: list(value) for key, value in MA_COLOR_CYCLE.items()},
        },
        "pixel_audit": pixel_audit,
        "condition_summaries": summaries,
        "paired_comparisons": comparisons,
        "baseline_repeat_determinism": determinism,
        "baseline_vs_frozen_cuda_reference": frozen_reference,
        "predictions": prediction_receipts,
        "contact_sheet": {
            "path": str(contact_sheet.relative_to(ROOT)),
            "sha256": sha256_file(contact_sheet),
            "size_bytes": contact_sheet.stat().st_size,
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "numpy": np.__version__,
            "opencv": cv2.__version__,
        },
        "interpretation": {
            "fixed_weight_ood_only": True,
            "retraining_effect_estimated": False,
            "threshold_tuned": False,
            "holdout_consumed": False,
            "training_performed": False,
            "dataset_mutated": False,
            "active_or_frozen_changed": False,
            "production_eligible": False,
        },
    }
    results_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "results": str(results_path),
        "summaries": summaries,
        "paired_comparisons": comparisons,
        "baseline_repeat_determinism": determinism,
        "baseline_vs_frozen_cuda_reference": frozen_reference,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
