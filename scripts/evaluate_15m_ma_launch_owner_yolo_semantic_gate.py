#!/usr/bin/env python3
"""Paired pre-holdout A/B for a causal semantic gate after YOLO proposals.

The control is the existing completed-history scanner contract: confidence
threshold, NMS, and box-x mapping to a 4/5-bar core with 2--9 visible
confirmation bars.  The treatment changes one thing only: every structural
proposal must also pass the available causal prefix of the frozen numeric
morphology gate used to generate positive training examples.

OHLCV sources are read with the repository's prefix reader, which materializes
no row at or after 2026-05-04.  Core features use only open/high/low/close, ATR,
and SMA/EMA 20/60/120 through the image endpoint.  Raw box vertical coverage is
recorded as a diagnostic and is not a tuned acceptance threshold.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.evaluate_15m_ma_launch_owner_grade_a8000_val import (
    CLASS_NAMES,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_VAL_COUNTS,
    load_val_rows,
    normalized_iou,
    score_prediction_row,
    sha256_file,
    summarize_event_surface,
    summarize_negative_fires,
    summarize_positive_rows,
)
from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
    read_preholdout_prefix,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.render import (
    IMG_HEIGHT,
    IMG_WIDTH,
    MARGIN,
    ChartTransform,
    make_chart_transform,
    render_chart,
)
from yoyo.layers.l1_detection.semantic_gate import (
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = DEFAULT_PREREG.parent / "results" / "semantic_gate"
AUTOFILL_PREREG = (
    ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
)
DATASET = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
ALLOWED_CORES = frozenset((4, 5))
ALLOWED_CONFIRMATIONS = frozenset(range(2, 10))
MATCH_IOU = 0.5


class SemanticGateEvaluationError(RuntimeError):
    """Raised when frozen identity, chronology, pairing, or output drifts."""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def repo_path(value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def repo_relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def verify_preregistration(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the immutable experiment and copied training-gate thresholds."""

    prereg = read_json(path)
    if prereg.get("experiment_id") != EXPERIMENT_ID or path.parent.name != EXPERIMENT_ID:
        raise SemanticGateEvaluationError("experiment identity drifted")
    if prereg["dataset"]["manifest_sha256"] != EXPECTED_MANIFEST_SHA256:
        raise SemanticGateEvaluationError("dataset identity in preregistration drifted")
    if utc(prereg["dataset"]["latest_allowed_candle_exclusive"]) != HOLDOUT_START:
        raise SemanticGateEvaluationError("pre-holdout boundary drifted")
    for name, value in prereg["safety"].items():
        if value is not False:
            raise SemanticGateEvaluationError(f"safety switch drifted: {name}")
    for name, item in prereg["frozen_inputs"].items():
        source = repo_path(item["path"])
        if not source.is_file() or sha256_file(source) != str(item["sha256"]):
            raise SemanticGateEvaluationError(f"frozen input SHA drift: {name} -> {source}")

    training_prereg = read_json(AUTOFILL_PREREG)
    source_gates = training_prereg["morphology_gate"]
    frozen_gates = prereg["treatment"]["frozen_morphology_gate"]
    if frozen_gates != source_gates:
        raise SemanticGateEvaluationError("treatment thresholds differ from training source")
    if prereg["treatment"]["raw_box_vertical_coverage_is_gate"] is not False:
        raise SemanticGateEvaluationError("raw y geometry must remain diagnostic-only in v1")
    return prereg, source_gates


def verify_raw_prediction_artifacts(
    evaluation_path: Path, predictions_path: Path, prereg: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind the paired analysis to one immutable inference pass."""

    evaluation = read_json(evaluation_path)
    expected = prereg["raw_inference"]
    checks = {
        "weights_sha256": str(evaluation.get("weights_sha256"))
        == str(expected["weights_sha256"]),
        "manifest_sha256": str(evaluation.get("manifest_sha256"))
        == str(prereg["dataset"]["manifest_sha256"]),
        "imgsz": int(evaluation.get("imgsz", -1)) == int(expected["imgsz"]),
        "confidence": math.isclose(
            float(evaluation.get("confidence_threshold", -1.0)),
            float(expected["confidence"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ),
        "nms_iou": math.isclose(
            float(evaluation.get("nms_iou", -1.0)),
            float(expected["nms_iou"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ),
        "match_iou": math.isclose(
            float(evaluation.get("true_hit_iou", -1.0)),
            float(expected["match_iou"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ),
        "holdout_not_consumed": evaluation.get("holdout_consumed") is False,
        "predictions_sha": str(evaluation.get("predictions_sha256"))
        == sha256_file(predictions_path),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SemanticGateEvaluationError(f"raw prediction binding failed: {failed}")
    rows = read_jsonl(predictions_path)
    if len(rows) != EXPECTED_VAL_COUNTS["positive"] + EXPECTED_VAL_COUNTS["negative"]:
        raise SemanticGateEvaluationError("raw prediction row count drifted")
    if len({str(row["dataset_sample_id"]) for row in rows}) != len(rows):
        raise SemanticGateEvaluationError("raw predictions contain duplicate sample ids")
    return evaluation, rows


def x_only_transform(n_bars: int) -> ChartTransform:
    """Return exact renderer x geometry without reading prices."""

    if int(n_bars) <= 1:
        raise SemanticGateEvaluationError("window must contain at least two bars")
    return ChartTransform(
        n_bars=int(n_bars),
        width=IMG_WIDTH,
        height=IMG_HEIGHT,
        left=MARGIN,
        top=MARGIN,
        plot_w=IMG_WIDTH - 2 * MARGIN,
        plot_h=IMG_HEIGHT - 2 * MARGIN,
        price_min=0.0,
        price_max=1.0,
        candle_half_w=max(1, int((IMG_WIDTH - 2 * MARGIN) / int(n_bars) * 0.34)),
    )


def map_prediction_to_core(
    prediction: Mapping[str, Any], row: Mapping[str, Any]
) -> dict[str, int | bool | str]:
    """Reproduce the current scanner's box-x-only structural acceptance."""

    xyxy = [float(value) for value in prediction["xyxy_norm"]]
    if len(xyxy) != 4 or not np.isfinite(xyxy).all():
        raise SemanticGateEvaluationError("prediction has invalid normalized geometry")
    window_bars = int(row["window_bars"])
    tf = x_only_transform(window_bars)
    x0 = xyxy[0] * tf.width
    x1 = xyxy[2] * tf.width
    centers = np.asarray([tf.x_at(index) for index in range(tf.n_bars)], dtype=float)
    local_start = int(np.argmin(np.abs(centers - x0)))
    local_end = int(np.argmin(np.abs(centers - x1)))
    if local_end < local_start:
        local_start, local_end = local_end, local_start
    core_start = int(row["window_start_i"]) + local_start
    core_end = int(row["window_start_i"]) + local_end
    core_bars = core_end - core_start + 1
    confirmation = int(row["window_end_i"]) - core_end
    reason = ""
    if core_bars not in ALLOWED_CORES:
        reason = "core_length"
    elif confirmation not in ALLOWED_CONFIRMATIONS:
        reason = "confirmation_bars"
    return {
        "structural_pass": not reason,
        "structural_rejection_reason": reason,
        "core_start_i": core_start,
        "core_end_i": core_end,
        "core_start_local": local_start,
        "core_end_local": local_end,
        "core_length_bars": core_bars,
        "confirmation_bars": confirmation,
    }


def _inverse_y(transform: ChartTransform, y: float) -> float:
    return float(
        transform.price_max
        - ((float(y) - transform.top) / transform.plot_h)
        * (transform.price_max - transform.price_min)
    )


def interval_coverage(
    container_low: float, container_high: float, target_low: float, target_high: float
) -> float:
    """Return the share of one target interval contained by another."""

    if not container_low < container_high or not target_low < target_high:
        return 0.0
    overlap = max(
        0.0,
        min(float(container_high), float(target_high))
        - max(float(container_low), float(target_low)),
    )
    return float(overlap / (target_high - target_low))


def _attach_score_metadata(
    score: dict[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    score.update(
        {
            "symbol": str(manifest["symbol"]),
            "time_block": str(manifest["time_block"]),
            "post_bars": int(manifest["post_bars"]),
            "window_end_time": str(manifest["window_end_time"]),
        }
    )
    return score


def _two_sided_exact_sign_p(left_only: int, right_only: int) -> float:
    discordant = int(left_only) + int(right_only)
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(int(left_only), int(right_only)) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def paired_binary_summary(
    left: Sequence[bool], right: Sequence[bool], *, left_name: str, right_name: str
) -> dict[str, Any]:
    """Summarize matched binary outcomes without pretending rows are independent."""

    if len(left) != len(right) or not left:
        raise SemanticGateEvaluationError("paired binary inputs must be equal and non-empty")
    left_only = sum(bool(a) and not bool(b) for a, b in zip(left, right))
    right_only = sum(bool(b) and not bool(a) for a, b in zip(left, right))
    both = sum(bool(a) and bool(b) for a, b in zip(left, right))
    neither = len(left) - left_only - right_only - both
    left_total = left_only + both
    right_total = right_only + both
    return {
        "pairs": len(left),
        f"{left_name}_positive": left_total,
        f"{left_name}_rate": left_total / len(left),
        f"{right_name}_positive": right_total,
        f"{right_name}_rate": right_total / len(left),
        "rate_delta_right_minus_left": (right_total - left_total) / len(left),
        "left_only": left_only,
        "right_only": right_only,
        "both": both,
        "neither": neither,
        "paired_exact_two_sided_p": _two_sided_exact_sign_p(left_only, right_only),
    }


def paired_event_summary(
    left_rows: Sequence[Mapping[str, Any]],
    right_rows: Sequence[Mapping[str, Any]],
    *,
    event_key: str,
    outcome_key: str,
    left_name: str,
    right_name: str,
) -> dict[str, Any]:
    """Collapse variants inside each event before matched comparison."""

    def collapse(rows: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
        grouped: defaultdict[str, list[bool]] = defaultdict(list)
        for row in rows:
            grouped[str(row[event_key])].append(bool(row[outcome_key]))
        return {key: any(values) for key, values in grouped.items()}

    left = collapse(left_rows)
    right = collapse(right_rows)
    if set(left) != set(right):
        raise SemanticGateEvaluationError("paired event identities drifted")
    keys = sorted(left)
    return paired_binary_summary(
        [left[key] for key in keys],
        [right[key] for key in keys],
        left_name=left_name,
        right_name=right_name,
    )


def _qstats(values: Iterable[float]) -> dict[str, float | None]:
    data = np.asarray(list(values), dtype=float)
    if not len(data):
        return {key: None for key in ("min", "p25", "median", "mean", "p75", "max")}
    if not np.isfinite(data).all():
        raise SemanticGateEvaluationError("non-finite metric distribution")
    return {
        "min": float(data.min()),
        "p25": float(np.quantile(data, 0.25)),
        "median": float(np.median(data)),
        "mean": float(data.mean()),
        "p75": float(np.quantile(data, 0.75)),
        "max": float(data.max()),
    }


def _prediction_price_diagnostics(
    prediction: Mapping[str, Any],
    *,
    frame: pd.DataFrame,
    row: Mapping[str, Any],
    core_start_i: int,
    core_end_i: int,
) -> dict[str, float]:
    window = frame.iloc[int(row["window_start_i"]) : int(row["window_end_i"]) + 1]
    transform = make_chart_transform(window)
    xyxy = [float(value) for value in prediction["xyxy_norm"]]
    prediction_high = _inverse_y(transform, xyxy[1] * IMG_HEIGHT)
    prediction_low = _inverse_y(transform, xyxy[3] * IMG_HEIGHT)
    if prediction_low > prediction_high:
        prediction_low, prediction_high = prediction_high, prediction_low
    core = frame.iloc[int(core_start_i) : int(core_end_i) + 1]
    ma = core.loc[:, list(ALL_MA_COLS)].to_numpy(dtype=float)
    ma_low, ma_high = float(ma.min()), float(ma.max())
    candle_low, candle_high = float(core["low"].min()), float(core["high"].max())
    atr = float(frame.iloc[int(core_end_i) + 2]["atr"])
    return {
        "prediction_price_low": prediction_low,
        "prediction_price_high": prediction_high,
        "ma_price_low": ma_low,
        "ma_price_high": ma_high,
        "candle_price_low": candle_low,
        "candle_price_high": candle_high,
        "prediction_height_atr": (prediction_high - prediction_low) / atr,
        "prediction_coverage_of_ma": interval_coverage(
            prediction_low, prediction_high, ma_low, ma_high
        ),
        "prediction_coverage_of_candles": interval_coverage(
            prediction_low, prediction_high, candle_low, candle_high
        ),
    }


def enrich_structural_predictions(
    *,
    manifest_rows: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
    gates: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Map boxes, read bounded sources once, and attach causal gate audits."""

    manifest_by_id = {str(row["dataset_sample_id"]): row for row in manifest_rows}
    raw_by_id = {str(row["dataset_sample_id"]): row for row in raw_rows}
    if set(manifest_by_id) != set(raw_by_id):
        raise SemanticGateEvaluationError("prediction/manifest sample identities differ")

    all_predictions: dict[str, list[dict[str, Any]]] = {}
    pending_by_source: defaultdict[str, list[tuple[Mapping[str, Any], dict[str, Any]]]] = defaultdict(list)
    structural_rejections: list[dict[str, Any]] = []
    for sample_id in sorted(manifest_by_id):
        manifest = manifest_by_id[sample_id]
        raw = raw_by_id[sample_id]
        if int(raw["boxes"]) != len(raw["predictions"]):
            raise SemanticGateEvaluationError(f"raw box count drifted: {sample_id}")
        output: list[dict[str, Any]] = []
        for index, source in enumerate(raw["predictions"]):
            prediction = dict(source)
            prediction["prediction_id"] = f"{sample_id}::{index}"
            mapping = map_prediction_to_core(prediction, manifest)
            prediction.update(mapping)
            output.append(prediction)
            if bool(mapping["structural_pass"]):
                pending_by_source[str(manifest["source_path"])].append((manifest, prediction))
            else:
                structural_rejections.append(
                    {
                        "dataset_sample_id": sample_id,
                        "prediction_id": prediction["prediction_id"],
                        "reason": str(mapping["structural_rejection_reason"]),
                        "class_id": int(prediction["class_id"]),
                        "confidence": float(prediction["confidence"]),
                    }
                )
        all_predictions[sample_id] = output

    source_audits: list[dict[str, Any]] = []
    semantic_boxes: list[dict[str, Any]] = []
    for source_path in sorted(pending_by_source):
        source = repo_path(source_path)
        frame, audit = read_preholdout_prefix(source, end_exclusive=HOLDOUT_START)
        enriched = add_candidate_features(frame)
        pending = pending_by_source[source_path]
        sample_manifest = pending[0][0]
        sample_window = enriched.iloc[
            int(sample_manifest["window_start_i"]) : int(sample_manifest["window_end_i"]) + 1
        ]
        rerendered, _ = render_chart(sample_window, out_path=None)
        actual = cv2.imread(
            str(DATASET / str(sample_manifest["image_path"])), cv2.IMREAD_COLOR
        )
        if actual is None or not np.array_equal(actual, rerendered):
            raise SemanticGateEvaluationError(
                f"source/render pixel parity failed: {source_path}"
            )
        audit = dict(audit)
        audit.update(
            {
                "source_path": source_path,
                "structural_predictions": len(pending),
                "sample_pixel_parity": True,
                "sample_dataset_sample_id": str(sample_manifest["dataset_sample_id"]),
            }
        )
        source_audits.append(audit)

        for manifest, prediction in pending:
            start = int(prediction["core_start_i"])
            end = int(prediction["core_end_i"])
            observed = int(manifest["window_end_i"])
            if utc(enriched.iloc[observed]["open_time"]) != utc(manifest["window_end_time"]):
                raise SemanticGateEvaluationError(
                    f"source index/time drift: {manifest['dataset_sample_id']}"
                )
            direction = "LONG" if int(prediction["class_id"]) == 0 else "SHORT"
            flipped = "SHORT" if direction == "LONG" else "LONG"
            features = compute_causal_core_semantics(
                enriched,
                core_start_i=start,
                core_end_i=end,
                observed_end_i=observed,
                direction=direction,
            )
            result = evaluate_causal_semantic_gate(features, gates)
            flipped_features = compute_causal_core_semantics(
                enriched,
                core_start_i=start,
                core_end_i=end,
                observed_end_i=observed,
                direction=flipped,
            )
            flipped_result = evaluate_causal_semantic_gate(flipped_features, gates)
            prediction.update(
                {
                    "semantic_pass": result.passed,
                    "semantic_checks": result.checks,
                    "semantic_failed_checks": list(result.failed_checks),
                    "semantic_features": features.to_dict(),
                    "flipped_semantic_pass": flipped_result.passed,
                    "flipped_semantic_failed_checks": list(flipped_result.failed_checks),
                    **_prediction_price_diagnostics(
                        prediction,
                        frame=enriched,
                        row=manifest,
                        core_start_i=start,
                        core_end_i=end,
                    ),
                }
            )
            semantic_boxes.append(
                {
                    "dataset_sample_id": str(manifest["dataset_sample_id"]),
                    "sample_kind": str(manifest["sample_kind"]),
                    "negative_kind": str(manifest.get("negative_kind") or ""),
                    "event_id": str(
                        manifest.get("event_id") or manifest.get("negative_event_id")
                    ),
                    "symbol": str(manifest["symbol"]),
                    "time_block": str(manifest["time_block"]),
                    "window_end_time": str(manifest["window_end_time"]),
                    "post_bars": int(manifest["post_bars"]),
                    **prediction,
                }
            )
        del enriched, frame

    if any(int(audit["holdout_ohlcv_rows_materialized"]) != 0 for audit in source_audits):
        raise SemanticGateEvaluationError("a source reader materialized holdout OHLCV")
    return all_predictions, semantic_boxes, source_audits + structural_rejections


def build_scored_rows(
    manifest_rows: Sequence[Mapping[str, Any]],
    all_predictions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Score control, treatment and direction-flip null on identical labels."""

    paired_rows: list[dict[str, Any]] = []
    structural_scores: list[dict[str, Any]] = []
    gated_scores: list[dict[str, Any]] = []
    flipped_scores: list[dict[str, Any]] = []
    for manifest in manifest_rows:
        sample_id = str(manifest["dataset_sample_id"])
        raw = [dict(row) for row in all_predictions[sample_id]]
        structural = [row for row in raw if bool(row["structural_pass"])]
        gated = [row for row in structural if bool(row["semantic_pass"])]
        flipped = [row for row in structural if bool(row["flipped_semantic_pass"])]
        structural_score = _attach_score_metadata(
            score_prediction_row(manifest, structural, match_iou=MATCH_IOU), manifest
        )
        gated_score = _attach_score_metadata(
            score_prediction_row(manifest, gated, match_iou=MATCH_IOU), manifest
        )
        flipped_score = _attach_score_metadata(
            score_prediction_row(manifest, flipped, match_iou=MATCH_IOU), manifest
        )
        structural_scores.append(structural_score)
        gated_scores.append(gated_score)
        flipped_scores.append(flipped_score)
        paired_rows.append(
            {
                "dataset_sample_id": sample_id,
                "sample_kind": str(manifest["sample_kind"]),
                "symbol": str(manifest["symbol"]),
                "time_block": str(manifest["time_block"]),
                "post_bars": int(manifest["post_bars"]),
                "window_end_time": str(manifest["window_end_time"]),
                "raw_predictions": raw,
                "structural_control": structural_score,
                "semantic_gate_treatment": gated_score,
                "flipped_direction_null": flipped_score,
            }
        )
    return paired_rows, structural_scores, gated_scores, flipped_scores


def _surface_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    negatives = [row for row in rows if row["sample_kind"] == "negative"]
    return {
        "positive_images": {
            "all": summarize_positive_rows(positives),
            "dense_long": summarize_positive_rows(
                row for row in positives if row["direction"] == "LONG"
            ),
            "dense_short": summarize_positive_rows(
                row for row in positives if row["direction"] == "SHORT"
            ),
        },
        "positive_events": summarize_event_surface(positives),
        "negative_images": {
            "all": summarize_negative_fires(negatives),
            "hard": summarize_negative_fires(
                row for row in negatives if row["negative_kind"] == "hard"
            ),
            "easy": summarize_negative_fires(
                row for row in negatives if row["negative_kind"] == "easy"
            ),
        },
    }


def paired_ab_summary(
    structural: Sequence[Mapping[str, Any]], gated: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Return image/event paired effects for positives and empty-label controls."""

    positive_pairs = [
        (left, right)
        for left, right in zip(structural, gated)
        if left["sample_kind"] == "positive"
    ]
    negative_pairs = [
        (left, right)
        for left, right in zip(structural, gated)
        if left["sample_kind"] == "negative"
    ]
    return {
        "positive_image_true_hit": paired_binary_summary(
            [bool(left["true_hit"]) for left, _ in positive_pairs],
            [bool(right["true_hit"]) for _, right in positive_pairs],
            left_name="structural",
            right_name="semantic",
        ),
        "positive_event_any_hit": paired_event_summary(
            [left for left, _ in positive_pairs],
            [right for _, right in positive_pairs],
            event_key="event_id",
            outcome_key="true_hit",
            left_name="structural",
            right_name="semantic",
        ),
        "negative_image_fire": paired_binary_summary(
            [int(left["boxes"]) > 0 for left, _ in negative_pairs],
            [int(right["boxes"]) > 0 for _, right in negative_pairs],
            left_name="structural",
            right_name="semantic",
        ),
        "negative_event_any_fire": paired_event_summary(
            [dict(left, fired=int(left["boxes"]) > 0) for left, _ in negative_pairs],
            [dict(right, fired=int(right["boxes"]) > 0) for _, right in negative_pairs],
            event_key="negative_event_id",
            outcome_key="fired",
            left_name="structural",
            right_name="semantic",
        ),
    }


def direction_flip_null_summary(
    gated: Sequence[Mapping[str, Any]], flipped: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Compare gate survival with only the semantic direction inverted."""

    pairs = [
        (actual, null)
        for actual, null in zip(gated, flipped)
        if actual["sample_kind"] == "positive"
    ]
    return {
        "null_hypothesis": (
            "Keep every image, raw YOLO box, class, confidence and IoU fixed; "
            "invert only LONG/SHORT inside the numeric semantic gate."
        ),
        "image_level": paired_binary_summary(
            [bool(actual["true_hit"]) for actual, _ in pairs],
            [bool(null["true_hit"]) for _, null in pairs],
            left_name="actual_direction",
            right_name="flipped_direction",
        ),
        "event_level": paired_event_summary(
            [actual for actual, _ in pairs],
            [null for _, null in pairs],
            event_key="event_id",
            outcome_key="true_hit",
            left_name="actual_direction",
            right_name="flipped_direction",
        ),
    }


def semantic_box_summary(boxes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failures: Counter[str] = Counter()
    for box in boxes:
        failures.update(str(name) for name in box["semantic_failed_checks"])
    passed = [box for box in boxes if bool(box["semantic_pass"])]
    rejected = [box for box in boxes if not bool(box["semantic_pass"])]
    metric_names = (
        "ma_envelope_atr",
        "ma_spread_end_atr",
        "minimum_close_to_ma_atr",
        "max_close_to_ma_envelope_atr",
        "max_body_to_ma_envelope_atr",
    )
    return {
        "structural_boxes": len(boxes),
        "semantic_pass_boxes": len(passed),
        "semantic_rejected_boxes": len(rejected),
        "semantic_rejection_rate": len(rejected) / len(boxes) if boxes else 0.0,
        "gate_failure_counts": dict(failures.most_common()),
        "pass_rate_by_confirmation_bars": {
            str(post): {
                "boxes": len(values),
                "passed": sum(bool(value["semantic_pass"]) for value in values),
                "pass_rate": sum(bool(value["semantic_pass"]) for value in values)
                / len(values),
            }
            for post in sorted({int(box["confirmation_bars"]) for box in boxes})
            if (values := [box for box in boxes if int(box["confirmation_bars"]) == post])
        },
        "metric_distributions": {
            name: {
                "all": _qstats(
                    float(box["semantic_features"][name]) for box in boxes
                ),
                "passed": _qstats(
                    float(box["semantic_features"][name]) for box in passed
                ),
                "rejected": _qstats(
                    float(box["semantic_features"][name]) for box in rejected
                ),
            }
            for name in metric_names
        },
        "raw_box_vertical_diagnostics_not_gated": {
            "prediction_coverage_of_ma": _qstats(
                float(box["prediction_coverage_of_ma"]) for box in boxes
            ),
            "prediction_coverage_of_candles": _qstats(
                float(box["prediction_coverage_of_candles"]) for box in boxes
            ),
        },
    }


def build_overview(
    *,
    summary: Mapping[str, Any],
    boxes: Sequence[Mapping[str, Any]],
    output: Path,
) -> None:
    """Render the paired outcome and semantic failure overview."""

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(13.4, 9.2), dpi=170)
    fig.suptitle("YOLO proposal + causal semantic gate · pre-holdout paired A/B", fontsize=17)
    blue, gold, red, green = "#3b6ea8", "#dda63a", "#c84d4d", "#3c8b65"

    control = summary["surfaces"]["structural_control"]
    treatment = summary["surfaces"]["semantic_gate_treatment"]
    ax = axes[0, 0]
    values = [
        control["positive_events"]["any_hit_events"],
        treatment["positive_events"]["any_hit_events"],
    ]
    bars = ax.bar(["structural control", "semantic gate"], values, color=[blue, green])
    ax.set_title("Positive events with any hit (155 events)")
    ax.set_ylabel("events")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 1, str(value), ha="center")

    ax = axes[0, 1]
    values = [
        control["negative_images"]["all"]["fired_images"],
        treatment["negative_images"]["all"]["fired_images"],
    ]
    bars = ax.bar(["structural control", "semantic gate"], values, color=[blue, gold])
    ax.set_title("Empty-label validation images that fired (3,600)")
    ax.set_ylabel("images")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.2, str(value), ha="center")

    ax = axes[1, 0]
    failure_counts = summary["semantic_boxes"]["gate_failure_counts"]
    ordered = list(failure_counts.items())[:10][::-1]
    ax.barh([name for name, _ in ordered], [value for _, value in ordered], color=red)
    ax.set_title("Why structural boxes were rejected")
    ax.set_xlabel("boxes")

    ax = axes[1, 1]
    for passed, color, label in ((False, red, "rejected"), (True, green, "passed")):
        values = [box for box in boxes if bool(box["semantic_pass"]) is passed]
        ax.scatter(
            [float(box["semantic_features"]["ma_envelope_atr"]) for box in values],
            [float(box["semantic_features"]["max_close_to_ma_envelope_atr"]) for box in values],
            s=15,
            alpha=0.55,
            color=color,
            label=label,
        )
    ax.axvline(1.5, color="#555", linestyle="--", linewidth=1.4)
    ax.axhline(1.9, color="#555", linestyle="--", linewidth=1.4)
    ax.set_xlabel("six-MA envelope / ATR")
    ax.set_ylabel("max close distance outside MA envelope / ATR")
    ax.set_title("Density and candle proximity")
    ax.legend()

    fig.text(
        0.012,
        0.012,
        "Same images, weights, predictions and IoU. No holdout, threshold search, training, promotion or deployment.",
        fontsize=8.5,
        color="#515862",
    )
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor="white")
    plt.close(fig)


def _draw_rejected_example(
    *, image_path: Path, box: Mapping[str, Any], output: Path, category: str
) -> None:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise SemanticGateEvaluationError(f"cannot decode example image: {image_path}")
    header = np.full((66, image.shape[1], 3), 255, dtype=np.uint8)
    canvas = np.vstack((header, image))
    xyxy = [float(value) for value in box["xyxy_norm"]]
    x0, y0, x1, y1 = (
        int(round(xyxy[0] * IMG_WIDTH)),
        int(round(xyxy[1] * IMG_HEIGHT)) + 66,
        int(round(xyxy[2] * IMG_WIDTH)),
        int(round(xyxy[3] * IMG_HEIGHT)) + 66,
    )
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (45, 55, 225), 4, cv2.LINE_AA)
    text = (
        f"{category} | conf={float(box['confidence']):.3f} | "
        f"fail={','.join(box['semantic_failed_checks'])[:95]}"
    )
    cv2.putText(
        canvas,
        text,
        (14, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.61,
        (30, 35, 40),
        2,
        cv2.LINE_AA,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise OSError(f"failed to write example: {output}")


def build_rejected_gallery(
    *,
    boxes: Sequence[Mapping[str, Any]],
    manifest_rows: Sequence[Mapping[str, Any]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Show removed empty-label fires and any true-hit recall cost."""

    manifest_by_id = {str(row["dataset_sample_id"]): row for row in manifest_rows}
    candidates: list[dict[str, Any]] = []
    for box in boxes:
        if bool(box["semantic_pass"]):
            continue
        manifest = manifest_by_id[str(box["dataset_sample_id"])]
        true_hit = False
        if manifest["sample_kind"] == "positive":
            true_hit = (
                int(box["class_id"]) == int(manifest["ground_truth_class"])
                and normalized_iou(manifest["ground_truth_xyxy"], box["xyxy_norm"])
                >= MATCH_IOU
            )
        category = (
            "REMOVED_NEGATIVE_FIRE"
            if manifest["sample_kind"] == "negative"
            else "LOST_TRUE_HIT"
            if true_hit
            else "REMOVED_EXTRA_BOX"
        )
        candidates.append({**box, "category": category, "true_hit": true_hit})

    selected: list[dict[str, Any]] = []
    quotas = {"REMOVED_NEGATIVE_FIRE": 10, "LOST_TRUE_HIT": 8, "REMOVED_EXTRA_BOX": 6}
    for category, quota in quotas.items():
        values = sorted(
            (row for row in candidates if row["category"] == category),
            key=lambda row: (-float(row["confidence"]), str(row["prediction_id"])),
        )
        seen: set[str] = set()
        for row in values:
            sample_id = str(row["dataset_sample_id"])
            if sample_id in seen:
                continue
            seen.add(sample_id)
            selected.append(row)
            if len(seen) >= quota:
                break

    cards: list[str] = []
    output_rows: list[dict[str, Any]] = []
    image_dir = output_dir / "images"
    for index, box in enumerate(selected, start=1):
        manifest = manifest_by_id[str(box["dataset_sample_id"])]
        filename = f"{index:02d}_{box['category']}_{box['dataset_sample_id']}.png"
        output = image_dir / filename
        _draw_rejected_example(
            image_path=DATASET / str(manifest["image_path"]),
            box=box,
            output=output,
            category=str(box["category"]),
        )
        record = {
            "order": index,
            "category": str(box["category"]),
            "dataset_sample_id": str(box["dataset_sample_id"]),
            "symbol": str(box["symbol"]),
            "confidence": float(box["confidence"]),
            "failed_checks": list(box["semantic_failed_checks"]),
            "ma_envelope_atr": float(box["semantic_features"]["ma_envelope_atr"]),
            "max_close_to_ma_envelope_atr": float(
                box["semantic_features"]["max_close_to_ma_envelope_atr"]
            ),
            "image_path": f"images/{filename}",
            "image_sha256": sha256_file(output),
        }
        output_rows.append(record)
        cards.append(
            "<article><h2>"
            + html.escape(f"{record['category']} · {record['symbol']}")
            + "</h2><p>"
            + html.escape(
                f"conf {record['confidence']:.3f} · MA {record['ma_envelope_atr']:.2f} ATR · "
                f"K/MA {record['max_close_to_ma_envelope_atr']:.2f} ATR · "
                f"fail {','.join(record['failed_checks'])}"
            )
            + f"</p><a href='{record['image_path']}'><img loading='lazy' src='{record['image_path']}'></a></article>"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(
        """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>语义门拒绝样本</title><style>
body{margin:0;background:#eef1f4;color:#19212b;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1600px;margin:auto;padding:20px}header{background:#fff;border-bottom:1px solid #ccd2d8}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}article{background:#fff;border-radius:10px;padding:13px;box-shadow:0 2px 10px #20304016}h1,h2{margin:0 0 8px}p{color:#59616b}img{width:100%;height:auto;border:1px solid #d2d7dc}@media(max-width:900px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>因果语义门：被拒绝的原始 YOLO 框</h1><p>红框是完全相同的原始预测。这里优先展示被消掉的空标签误报，并诚实列出任何因此损失的真命中。纵向覆盖只展示，不参与 v1 放行。</p></header><main>"""
        + "".join(cards)
        + "</main></body></html>\n",
        encoding="utf-8",
    )
    return output_rows


def analyze(
    *,
    prereg_path: Path,
    raw_evaluation_path: Path,
    raw_predictions_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the preregistered paired analysis and atomically publish artifacts."""

    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite semantic evaluation: {output_dir}")
    prereg, gates = verify_preregistration(prereg_path)
    raw_evaluation, raw_rows = verify_raw_prediction_artifacts(
        raw_evaluation_path, raw_predictions_path, prereg
    )
    _, manifest_rows = load_val_rows(DATASET)
    all_predictions, semantic_boxes, source_and_structural_audits = enrich_structural_predictions(
        manifest_rows=manifest_rows,
        raw_rows=raw_rows,
        gates=gates,
    )
    paired_rows, structural_scores, gated_scores, flipped_scores = build_scored_rows(
        manifest_rows, all_predictions
    )

    structural_surface = _surface_summary(structural_scores)
    gated_surface = _surface_summary(gated_scores)
    paired = paired_ab_summary(structural_scores, gated_scores)
    direction_null = direction_flip_null_summary(gated_scores, flipped_scores)
    boxes_summary = semantic_box_summary(semantic_boxes)
    structural_boxes = boxes_summary["structural_boxes"]
    treatment_boxes = boxes_summary["semantic_pass_boxes"]
    if treatment_boxes > structural_boxes:
        raise SemanticGateEvaluationError("treatment is not a subset of control")

    baseline_event_hits = structural_surface["positive_events"]["any_hit_events"]
    gated_event_hits = gated_surface["positive_events"]["any_hit_events"]
    event_recall_retention = (
        gated_event_hits / baseline_event_hits if baseline_event_hits else 0.0
    )
    baseline_negative_boxes = structural_surface["negative_images"]["all"]["boxes"]
    gated_negative_boxes = gated_surface["negative_images"]["all"]["boxes"]
    negative_box_reduction = (
        (baseline_negative_boxes - gated_negative_boxes) / baseline_negative_boxes
        if baseline_negative_boxes
        else None
    )
    criteria = prereg["decision_rule"]
    checks = {
        "treatment_is_strict_subset_or_equal": treatment_boxes <= structural_boxes,
        "positive_event_recall_retention": event_recall_retention
        >= float(criteria["min_positive_event_recall_retention"]),
        "negative_box_count_not_increased": gated_negative_boxes <= baseline_negative_boxes,
        "negative_box_reduction": negative_box_reduction is not None
        and negative_box_reduction >= float(criteria["min_negative_box_reduction"]),
        "actual_direction_beats_flip": (
            direction_null["event_level"]["actual_direction_positive"]
            > direction_null["event_level"]["flipped_direction_positive"]
            and direction_null["event_level"]["paired_exact_two_sided_p"]
            < float(criteria["direction_flip_p_max"])
        ),
        "holdout_ohlcv_rows_materialized_zero": all(
            int(row.get("holdout_ohlcv_rows_materialized", 0)) == 0
            for row in source_and_structural_audits
            if "holdout_ohlcv_rows_materialized" in row
        ),
    }
    status = "pass" if all(checks.values()) else "fail"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "status": status,
        "question": prereg["question"],
        "single_variable": prereg["single_variable"],
        "raw_inference": {
            "evaluation_path": repo_relative(raw_evaluation_path),
            "evaluation_sha256": sha256_file(raw_evaluation_path),
            "predictions_path": repo_relative(raw_predictions_path),
            "predictions_sha256": sha256_file(raw_predictions_path),
            "weights_sha256": str(raw_evaluation["weights_sha256"]),
            "imgsz": int(raw_evaluation["imgsz"]),
            "confidence": float(raw_evaluation["confidence_threshold"]),
            "nms_iou": float(raw_evaluation["nms_iou"]),
        },
        "dataset": {
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "validation_counts": EXPECTED_VAL_COUNTS,
            "time_min": min(str(row["window_start_time"]) for row in manifest_rows),
            "time_max": max(str(row["window_end_time"]) for row in manifest_rows),
            "holdout_start_exclusive": HOLDOUT_START.isoformat(),
            "source_files_read_as_bounded_prefixes": sum(
                "rows_materialized" in row for row in source_and_structural_audits
            ),
            "holdout_ohlcv_rows_materialized": 0,
        },
        "treatment": {
            "description": prereg["treatment"]["description"],
            "frozen_morphology_gate": gates,
            "post3_checked_only_when_visible": True,
            "post5_checked_only_when_visible": True,
            "raw_box_vertical_coverage_is_gate": False,
        },
        "surfaces": {
            "structural_control": structural_surface,
            "semantic_gate_treatment": gated_surface,
        },
        "paired_ab": paired,
        "semantic_boxes": boxes_summary,
        "direction_flip_null": direction_null,
        "decision": {
            "status": status,
            "checks": checks,
            "positive_event_recall_retention": event_recall_retention,
            "negative_box_reduction": negative_box_reduction,
            "no_post_result_retuning": True,
        },
        "economic_metrics": {
            "applicable": False,
            "reason": "This is an object-detection semantic-filter experiment with no entry, exit, TP/SL, cost or return series.",
        },
        "safety": {
            "holdout_consumed": False,
            "training_started": False,
            "weights_modified": False,
            "threshold_tuned": False,
            "labels_modified": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "training_eligible": False,
            "production_eligible": False,
            "completed_history_not_live_tip": True,
        },
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix="semantic_gate.building.", dir=output_dir.parent))
    try:
        write_jsonl(building / "paired_predictions.jsonl", paired_rows)
        write_jsonl(building / "semantic_boxes.jsonl", semantic_boxes)
        write_jsonl(building / "source_and_structural_audit.jsonl", source_and_structural_audits)
        overview = building / "paired_ab_overview.png"
        build_overview(summary=summary, boxes=semantic_boxes, output=overview)
        gallery_rows = build_rejected_gallery(
            boxes=semantic_boxes,
            manifest_rows=manifest_rows,
            output_dir=building / "rejected_examples",
        )
        write_jsonl(building / "rejected_examples.jsonl", gallery_rows)
        summary["visuals"] = {
            "overview": "paired_ab_overview.png",
            "rejected_gallery": "rejected_examples/index.html",
            "rejected_examples": len(gallery_rows),
        }
        write_json(building / "summary.json", summary)
        write_json(
            building / "chart_map.json",
            {
                "paired_ab_overview.png": {
                    "question": "How much false-fire reduction and true-hit cost does the frozen semantic gate create on identical predictions?",
                    "palette": "blue control, green pass, red rejection, gold treatment false fires",
                },
                "rejected_examples/index.html": {
                    "question": "Which original raw boxes were removed, including both benefits and recall costs?",
                    "type": "lossless source-image gallery with raw red boxes",
                },
            },
        )
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "preregistration_sha256": sha256_file(prereg_path),
            "summary_sha256": sha256_file(building / "summary.json"),
            "paired_predictions_sha256": sha256_file(building / "paired_predictions.jsonl"),
            "semantic_boxes_sha256": sha256_file(building / "semantic_boxes.jsonl"),
            "source_audit_sha256": sha256_file(building / "source_and_structural_audit.jsonl"),
            "overview_sha256": sha256_file(overview),
            "gallery_sha256": sha256_file(building / "rejected_examples/index.html"),
            "status": status,
            "passed_integrity_checks": True,
        }
        write_json(building / "receipt.json", receipt)
        os.replace(building, output_dir)
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise
    return {**summary, "results_path": repo_relative(output_dir)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--raw-evaluation", type=Path, required=True)
    parser.add_argument("--raw-predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    result = analyze(
        prereg_path=args.prereg.resolve(),
        raw_evaluation_path=args.raw_evaluation.resolve(),
        raw_predictions_path=args.raw_predictions.resolve(),
        output_dir=args.output.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
