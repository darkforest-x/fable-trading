#!/usr/bin/env python3
"""Audit 2026-08-27 Owner-YOLO boxes against their actual training target.

Sources are the hash-pinned 10,000-positive accepted manifest, the immutable
10k-positive/30k-negative YOLO manifest, the frozen 2026-08-27 event ledger,
and the already-fetched holdout snapshot.  The audit recomputes the exact
14-feature morphology gate using only each mapped core through core+5, which
is the same completed-history definition used to retrieve the training
positives.  It also compares the raw prediction y interval with the six-MA
envelope and renders every actual model input beside a same-direction training
positive selected by feature-only distance for visualization.

This module performs no model inference, threshold search, label mutation,
training, promotion, deployment, forward mutation, or order action.  The
paired training example and cyan MA-only rectangle are audit aids, not Gold
labels.
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
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_launch_owner_autofill10000 import load_reference_profiles
from yoyo.datasets.ma_launch_owner_autofill_review import (
    FEATURE_NAMES,
    frame_arrays,
    morphology_profile,
    passes_gate,
    profile_distance,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS
from yoyo.layers.l1_detection.render import render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-20260827-training-parity-audit-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = DEFAULT_PREREG.parent / "results"
AUTOFILL_PREREG = (
    ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
)
TRAIN_ACCEPTED = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/review_manifest.jsonl"
)
TRAIN_DATASET = ROOT / "datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2"
TRAIN_MANIFEST = TRAIN_DATASET / "manifest.jsonl"
EVENT_ANALYSIS = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-20260827-fullcontext-v3/results/detailed_event_analysis.csv"
)
FULLCONTEXT_MANIFEST = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-20260827-fullcontext-v3/results/manifest.jsonl"
)
FETCH_RECEIPT = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-recent5d-v1/results/fetch_receipt.json"
)
SNAPSHOT_ROOT = ROOT / "analysis/output/ma_launch_owner_yolo_recent5d_v1/kline_snapshot"

EXPECTED_EVENTS = 43
EXPECTED_TRAIN_POSITIVES = 10_000
EXPECTED_TRAIN_NEGATIVES = 30_000
EXPECTED_WIDTH = 1280
EXPECTED_HEIGHT = 742

# BGR colors.  Red/orange are existing boxes; cyan is an audit-only MA envelope.
PREDICTION_COLOR = (45, 55, 225)
TRAIN_LABEL_COLOR = (0, 170, 245)
MA_ONLY_COLOR = (210, 175, 20)
PASS_COLOR = (150, 95, 20)
INK = (30, 34, 38)
MUTED = (100, 105, 112)
WHITE = (255, 255, 255)


class TrainingParityAuditError(RuntimeError):
    """Raised when frozen identity, parity, or semantic checks drift."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


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


def repo_path(value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def repo_relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def display_path(path: Path) -> str:
    """Prefer a repository-relative artifact path, retaining external test paths."""

    try:
        return repo_relative(path)
    except ValueError:
        return str(path.resolve())


def qstats(values: Sequence[float] | np.ndarray | pd.Series) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    if data.size == 0 or not np.isfinite(data).all():
        raise TrainingParityAuditError("quantile input is empty or non-finite")
    return {
        "min": float(np.min(data)),
        "p05": float(np.quantile(data, 0.05)),
        "p25": float(np.quantile(data, 0.25)),
        "median": float(np.median(data)),
        "mean": float(np.mean(data)),
        "p75": float(np.quantile(data, 0.75)),
        "p95": float(np.quantile(data, 0.95)),
        "max": float(np.max(data)),
    }


def verify_preregistration(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if payload.get("experiment_id") != EXPERIMENT_ID or path.parent.name != EXPERIMENT_ID:
        raise TrainingParityAuditError("experiment identity drifted")
    auth = payload["owner_authorization"]
    if int(auth.get("holdout_consumption_number_for_this_configuration", -1)) != 4:
        raise TrainingParityAuditError("holdout-use number must be four")
    if auth.get("existing_snapshot_read_authorized") is not True:
        raise TrainingParityAuditError("existing snapshot read was not authorized")
    for field in (
        "new_network_read_authorized",
        "new_model_inference_authorized",
        "threshold_or_weight_change_authorized",
        "label_change_authorized",
        "training_or_tuning_authorized",
        "production_or_promotion_authorized",
    ):
        if auth.get(field) is not False:
            raise TrainingParityAuditError(f"authorization safety switch drifted: {field}")
    for field, value in payload["safety"].items():
        if value is not False:
            raise TrainingParityAuditError(f"safety switch drifted: {field}")
    for name, item in payload["frozen_inputs"].items():
        if not isinstance(item, Mapping) or "path" not in item or "sha256" not in item:
            continue
        source = repo_path(item["path"])
        if not source.is_file() or sha256_file(source) != str(item["sha256"]):
            raise TrainingParityAuditError(f"frozen input SHA drift: {name} -> {source}")
    return payload


def feature_gate_checks(features: Mapping[str, float], gates: Mapping[str, float]) -> dict[str, bool]:
    """Return the exact positive-retrieval morphology predicates."""

    return {
        "ma_envelope": float(features["ma_envelope_atr"]) <= float(gates["max_ma_envelope_atr"]),
        "ma_spread_end": float(features["ma_spread_end_atr"]) <= float(gates["max_ma_spread_end_atr"]),
        "max_body": float(features["max_body_atr"]) <= float(gates["max_core_body_atr"]),
        "core_progress": float(gates["min_core_progress_atr"])
        <= float(features["core_progress_atr"])
        <= float(gates["max_core_progress_atr"]),
        "post1": float(features["post1_progress_atr"]) >= float(gates["min_post1_progress_atr"]),
        "post2": float(features["post2_progress_atr"]) >= float(gates["min_post2_progress_atr"]),
        "post3": float(features["post3_progress_atr"]) >= float(gates["min_post3_progress_atr"]),
        "post5": float(features["post5_progress_atr"]) >= float(gates["min_post5_progress_atr"]),
        "ma_slope": float(features["aligned_ma_slope_atr"]) >= float(gates["min_aligned_ma_slope_atr"]),
        "minimum_close_to_ma": float(features["minimum_close_to_ma_atr"])
        <= float(gates["max_minimum_close_to_ma_atr"]),
        "close_to_ma_envelope": float(features["max_close_to_ma_envelope_atr"])
        <= float(gates["max_close_to_ma_envelope_atr"]),
        "body_to_ma_envelope": float(features["max_body_to_ma_envelope_atr"])
        <= float(gates["max_body_to_ma_envelope_atr"]),
    }


def gate_failures(features: Mapping[str, float], gates: Mapping[str, float]) -> list[str]:
    return [name for name, passed in feature_gate_checks(features, gates).items() if not passed]


def vertical_iou(low_a: float, high_a: float, low_b: float, high_b: float) -> float:
    if not low_a < high_a or not low_b < high_b:
        raise TrainingParityAuditError("invalid vertical interval")
    overlap = max(0.0, min(high_a, high_b) - max(low_a, low_b))
    union = max(high_a, high_b) - min(low_a, low_b)
    return float(overlap / union) if union > 0 else 0.0


def interval_coverage(container_low: float, container_high: float, low: float, high: float) -> float:
    if not container_low < container_high or not low < high:
        raise TrainingParityAuditError("invalid coverage interval")
    overlap = max(0.0, min(container_high, high) - max(container_low, low))
    return float(overlap / (high - low))


def ma_price_envelope(arrays: Mapping[str, np.ndarray], start_i: int, end_i: int) -> tuple[float, float]:
    matrix = np.stack(
        [arrays[column][start_i : end_i + 1] for column in SIX_MA_COLUMNS], axis=1
    )
    if matrix.size == 0 or not np.isfinite(matrix).all():
        raise TrainingParityAuditError("non-finite MA envelope")
    return float(matrix.min()), float(matrix.max())


def candle_price_envelope(
    arrays: Mapping[str, np.ndarray], start_i: int, end_i: int
) -> tuple[float, float]:
    low = arrays["low"][start_i : end_i + 1]
    high = arrays["high"][start_i : end_i + 1]
    if low.size == 0 or not np.isfinite(np.r_[low, high]).all():
        raise TrainingParityAuditError("non-finite candle envelope")
    return float(low.min()), float(high.max())


def box_corners_from_normalized(
    cx: float, cy: float, width: float, height: float, *, image_width: int, image_height: int
) -> tuple[int, int, int, int]:
    values = np.asarray([cx, cy, width, height], dtype=float)
    if not np.isfinite(values).all() or not np.all((values > 0) & (values <= 1)):
        raise TrainingParityAuditError(f"invalid normalized box: {values.tolist()}")
    return (
        int(round((cx - width / 2.0) * image_width)),
        int(round((cy - height / 2.0) * image_height)),
        int(round((cx + width / 2.0) * image_width)),
        int(round((cy + height / 2.0) * image_height)),
    )


def padded_ma_box(
    transform: Any,
    *,
    x0: float,
    x1: float,
    ma_low: float,
    ma_high: float,
    pad_fraction: float = 0.04,
) -> tuple[int, int, int, int]:
    span = ma_high - ma_low
    if span <= 0:
        raise TrainingParityAuditError("MA-only box has zero price span")
    pad = span * pad_fraction
    y0 = transform.y_at(ma_high + pad)
    y1 = transform.y_at(ma_low - pad)
    return (
        int(round(max(0.0, min(float(transform.width - 1), x0)))),
        int(round(max(0.0, min(float(transform.height - 1), min(y0, y1))))),
        int(round(max(0.0, min(float(transform.width - 1), x1)))),
        int(round(max(0.0, min(float(transform.height - 1), max(y0, y1))))),
    )


def draw_labeled_box(
    image: np.ndarray,
    box: Sequence[int | float],
    *,
    color: tuple[int, int, int],
    label: str,
    thickness: int = 4,
) -> None:
    x0, y0, x1, y1 = [int(round(float(value))) for value in box]
    x0 = max(0, min(image.shape[1] - 1, x0))
    x1 = max(0, min(image.shape[1] - 1, x1))
    y0 = max(0, min(image.shape[0] - 1, y0))
    y1 = max(0, min(image.shape[0] - 1, y1))
    cv2.rectangle(image, (x0, y0), (x1, y1), color, thickness, cv2.LINE_AA)
    (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
    label_y0 = max(0, y0 - th - baseline - 7)
    cv2.rectangle(image, (x0, label_y0), (min(image.shape[1] - 1, x0 + tw + 10), y0), color, -1)
    cv2.putText(
        image,
        label,
        (x0 + 5, max(th + 2, y0 - baseline - 4)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        WHITE,
        2,
        cv2.LINE_AA,
    )


def put_text(
    image: np.ndarray,
    text_value: str,
    origin: tuple[int, int],
    *,
    color: tuple[int, int, int] = INK,
    scale: float = 0.62,
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        text_value,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def profile_features(profile: Any) -> dict[str, float]:
    return dict(zip(FEATURE_NAMES, (float(value) for value in profile.features)))


def training_profiles(
    *, accepted_rows: Sequence[Mapping[str, Any]], dataset_rows: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]
) -> tuple[pd.DataFrame, dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    dataset_positive = {
        str(row["source_sample_id"]): row
        for row in dataset_rows
        if row.get("sample_kind") == "positive"
    }
    dataset_negative_count = sum(row.get("sample_kind") == "negative" for row in dataset_rows)
    if len(accepted_rows) != EXPECTED_TRAIN_POSITIVES or len(dataset_positive) != EXPECTED_TRAIN_POSITIVES:
        raise TrainingParityAuditError("training positive count drifted")
    if dataset_negative_count != EXPECTED_TRAIN_NEGATIVES:
        raise TrainingParityAuditError("training negative count drifted")
    gates = prereg["morphology_gate"]
    threshold = float(prereg["reference_family"]["max_distance"])
    profiles: list[dict[str, Any]] = []
    accepted_by_id: dict[str, Mapping[str, Any]] = {}
    for accepted in accepted_rows:
        sample_id = str(accepted["sample_id"])
        dataset = dataset_positive.get(sample_id)
        if dataset is None:
            raise TrainingParityAuditError(f"training lineage missing: {sample_id}")
        if str(dataset["direction"]) != str(accepted["direction"]):
            raise TrainingParityAuditError(f"direction lineage drift: {sample_id}")
        for key in ("cx_norm", "cy_norm", "w_norm", "h_norm"):
            if not math.isclose(
                float(dataset["box"][key]), float(accepted["box"][key]), rel_tol=0.0, abs_tol=1e-12
            ):
                raise TrainingParityAuditError(f"box lineage drift: {sample_id}/{key}")
        features = {name: float(accepted["features"][name]) for name in FEATURE_NAMES}
        failures = gate_failures(features, gates)
        distance = float(accepted["similarity_distance"])
        if failures or distance > threshold + 1e-12:
            raise TrainingParityAuditError(f"accepted training row violates frozen gate: {sample_id}")
        ma_span = float(features["ma_envelope_atr"])
        candle_span = float(features["candle_envelope_atr"])
        if ma_span <= 0:
            raise TrainingParityAuditError(f"zero MA span: {sample_id}")
        box = accepted["box"]
        if box.get("contains_core_wicks_and_six_mas") is not True:
            raise TrainingParityAuditError(f"training label does not contain declared values: {sample_id}")
        profiles.append(
            {
                "sample_id": sample_id,
                "source_order": int(accepted["source_order"]),
                "symbol": str(accepted["symbol"]),
                "direction": str(accepted["direction"]),
                "source_path": str(accepted["source_path"]),
                "image_path": str(dataset["image_path"]),
                "image_sha256": str(dataset["image_sha256"]),
                "label_path": str(dataset["label_path"]),
                "core_start_i": int(accepted["source_core_start_i"]),
                "core_end_i": int(accepted["source_core_end_i"]),
                "window_start_i": int(accepted["window_start_i"]),
                "window_end_i": int(accepted["window_end_i"]),
                "core_bars": int(accepted["core_bars"]),
                "similarity_distance": distance,
                "label_w_norm": float(box["w_norm"]),
                "label_h_norm": float(box["h_norm"]),
                "candle_to_ma_span_ratio": candle_span / ma_span,
                "candle_span_exceeds_ma_span": bool(candle_span > ma_span),
                "label_includes_full_wicks_and_six_mas": True,
                **features,
            }
        )
        accepted_by_id[sample_id] = accepted
    return pd.DataFrame(profiles), accepted_by_id, dataset_positive


def nearest_training_index(
    feature_vector: np.ndarray,
    *,
    direction: str,
    training: pd.DataFrame,
    feature_scales: np.ndarray,
) -> tuple[int, float]:
    subset = training.index[training["direction"] == direction].to_numpy(dtype=int)
    matrix = training.loc[subset, list(FEATURE_NAMES)].to_numpy(dtype=float)
    distances = np.sqrt(np.mean(((matrix - feature_vector) / feature_scales) ** 2, axis=1))
    best_local = int(np.argmin(distances))
    return int(subset[best_local]), float(distances[best_local])


def strict_alternative(
    arrays: Mapping[str, np.ndarray],
    *,
    window_end_i: int,
    current_core_start_i: int,
    current_core_end_i: int,
    direction: str,
    gates: Mapping[str, Any],
    references: Sequence[Any],
    feature_scales: np.ndarray,
    feature_weight: float,
    sequence_weight: float,
    distance_threshold: float,
) -> dict[str, Any] | None:
    choices: list[dict[str, Any]] = []
    for confirmation in (4, 5, 6):
        core_end = int(window_end_i) - confirmation
        for core_bars in (4, 5):
            core_start = core_end - core_bars + 1
            if core_start == current_core_start_i and core_end == current_core_end_i:
                continue
            profile = morphology_profile(
                arrays,
                anchor_i=core_end + 2,
                direction=direction,
                core_start_offset=-core_bars - 1,
                core_end_offset=-2,
            )
            if profile is None or not passes_gate(profile, gates):
                continue
            distance = profile_distance(
                profile,
                references,
                feature_scales=feature_scales,
                feature_weight=feature_weight,
                sequence_weight=sequence_weight,
            )
            if distance <= distance_threshold:
                choices.append(
                    {
                        "core_start_i": core_start,
                        "core_end_i": core_end,
                        "core_bars": core_bars,
                        "confirmation_bars": confirmation,
                        "similarity_distance": float(distance),
                    }
                )
    return min(choices, key=lambda row: (row["similarity_distance"], row["confirmation_bars"])) if choices else None


def render_current_events(
    *,
    events: pd.DataFrame,
    full_rows: Sequence[Mapping[str, Any]],
    fetch_receipt: Mapping[str, Any],
    autofill_prereg: Mapping[str, Any],
    references: Sequence[Any],
    training: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[int, dict[str, Any]], dict[str, Any]]:
    full_by_order = {int(row["event_order"]): row for row in full_rows}
    if len(full_by_order) != EXPECTED_EVENTS:
        raise TrainingParityAuditError("full-context event count drifted")
    snapshot_meta = {str(row["symbol"]): row for row in fetch_receipt["snapshot_files"]}
    gates = autofill_prereg["morphology_gate"]
    family = autofill_prereg["reference_family"]
    feature_scales = np.asarray(family["feature_scales"], dtype=float)
    threshold = float(family["max_distance"])
    rendered: dict[int, dict[str, Any]] = {}
    output_rows: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    for symbol, group in events.groupby("symbol", sort=True):
        snapshot = SNAPSHOT_ROOT / f"{symbol}.csv"
        expected = snapshot_meta.get(symbol)
        if expected is None or not snapshot.is_file():
            raise TrainingParityAuditError(f"snapshot lineage missing: {symbol}")
        actual_sha = sha256_file(snapshot)
        if actual_sha != str(expected["sha256"]):
            raise TrainingParityAuditError(f"snapshot SHA drift: {symbol}")
        source_hashes[repo_relative(snapshot)] = actual_sha
        frame = add_candidate_features(pd.read_csv(snapshot))
        arrays = frame_arrays(frame)
        for event in group.sort_values("event_order").to_dict("records"):
            order = int(event["event_order"])
            full = full_by_order.get(order)
            if full is None or str(full["symbol"]) != symbol:
                raise TrainingParityAuditError(f"event/full-context identity drift: {order}")
            direction = "LONG" if int(event["class_id"]) == 0 else "SHORT"
            core_start = int(event["core_start_i"])
            core_end = int(event["core_end_i"])
            core_bars = int(event["core_length_bars"])
            profile = morphology_profile(
                arrays,
                anchor_i=core_end + 2,
                direction=direction,
                core_start_offset=-core_bars - 1,
                core_end_offset=-2,
            )
            if profile is None:
                raise TrainingParityAuditError(f"current morphology is invalid: {order}")
            features = profile_features(profile)
            failures = gate_failures(features, gates)
            distance = profile_distance(
                profile,
                references,
                feature_scales=feature_scales,
                feature_weight=float(family["feature_weight"]),
                sequence_weight=float(family["sequence_weight"]),
            )
            gate_ok = not failures
            distance_ok = distance <= threshold
            strict_current = gate_ok and distance_ok
            alternative = strict_alternative(
                arrays,
                window_end_i=int(event["window_end_i"]),
                current_core_start_i=core_start,
                current_core_end_i=core_end,
                direction=direction,
                gates=gates,
                references=references,
                feature_scales=feature_scales,
                feature_weight=float(family["feature_weight"]),
                sequence_weight=float(family["sequence_weight"]),
                distance_threshold=threshold,
            )
            if strict_current:
                classification = "TRAIN_SPEC_MATCH"
            elif alternative is not None:
                classification = "LOCALIZATION_MISMATCH"
            else:
                classification = "OUT_OF_TRAINING_SPEC"

            start_i, end_i = int(event["window_start_i"]), int(event["window_end_i"])
            image, transform = render_chart(
                frame.iloc[start_i : end_i + 1].reset_index(drop=True), out_path=None
            )
            if image.shape != (EXPECTED_HEIGHT, EXPECTED_WIDTH, 3):
                raise TrainingParityAuditError(f"current input dimensions drifted: {order}")
            if pixel_sha256(image) != str(event["input_pixel_sha256"]):
                raise TrainingParityAuditError(f"current input pixel parity failed: {order}")

            ma_low, ma_high = ma_price_envelope(arrays, core_start, core_end)
            candle_low, candle_high = candle_price_envelope(arrays, core_start, core_end)
            prediction_low = float(full["price_low"])
            prediction_high = float(full["price_high"])
            atr = float(arrays["atr"][core_end + 2])
            if not np.isfinite(atr) or atr <= 0:
                raise TrainingParityAuditError(f"current ATR is invalid: {order}")
            pred_box = box_corners_from_normalized(
                float(event["prediction_cx_norm"]),
                float(event["prediction_cy_norm"]),
                float(event["prediction_w_norm"]),
                float(event["prediction_h_norm"]),
                image_width=EXPECTED_WIDTH,
                image_height=EXPECTED_HEIGHT,
            )
            ma_box = padded_ma_box(
                transform,
                x0=pred_box[0],
                x1=pred_box[2],
                ma_low=ma_low,
                ma_high=ma_high,
            )
            nearest_index, nearest_distance = nearest_training_index(
                profile.features,
                direction=direction,
                training=training,
                feature_scales=feature_scales,
            )
            nearest = training.loc[nearest_index]
            output_rows.append(
                {
                    "event_order": order,
                    "symbol": symbol,
                    "direction": direction,
                    "confidence": float(event["confidence"]),
                    "window_start_i": start_i,
                    "window_end_i": end_i,
                    "window_len": int(event["window_len"]),
                    "core_start_i": core_start,
                    "core_end_i": core_end,
                    "core_bars": core_bars,
                    "confirmation_bars": int(event["confirmation_bars"]),
                    "similarity_distance_to_owner50": float(distance),
                    "distance_threshold": threshold,
                    "distance_ok": bool(distance_ok),
                    "morphology_gate_ok": bool(gate_ok),
                    "strict_training_spec_match": bool(strict_current),
                    "same_input_strict_alternative_exists": bool(alternative is not None),
                    "same_input_best_alternative": json.dumps(alternative, sort_keys=True) if alternative else "",
                    "semantic_classification": classification,
                    "failed_gate_count": len(failures),
                    "failed_gates": ",".join(failures),
                    "prediction_price_low": prediction_low,
                    "prediction_price_high": prediction_high,
                    "ma_price_low": ma_low,
                    "ma_price_high": ma_high,
                    "candle_price_low": candle_low,
                    "candle_price_high": candle_high,
                    "prediction_height_atr": (prediction_high - prediction_low) / atr,
                    "prediction_height_over_ma_span": (prediction_high - prediction_low) / (ma_high - ma_low),
                    "prediction_vs_ma_vertical_iou": vertical_iou(
                        prediction_low, prediction_high, ma_low, ma_high
                    ),
                    "prediction_coverage_of_ma": interval_coverage(
                        prediction_low, prediction_high, ma_low, ma_high
                    ),
                    "candle_to_ma_span_ratio": (candle_high - candle_low) / (ma_high - ma_low),
                    "prediction_w_norm": float(event["prediction_w_norm"]),
                    "prediction_h_norm": float(event["prediction_h_norm"]),
                    "nearest_training_sample_id": str(nearest["sample_id"]),
                    "nearest_training_source_order": int(nearest["source_order"]),
                    "nearest_training_symbol": str(nearest["symbol"]),
                    "nearest_training_feature_rms": nearest_distance,
                    **features,
                }
            )
            rendered[order] = {
                "image": image,
                "transform": transform,
                "prediction_box": pred_box,
                "ma_box": ma_box,
                "classification": classification,
            }
    return pd.DataFrame(output_rows).sort_values("event_order"), rendered, source_hashes


def render_training_match(
    sample_id: str,
    *,
    accepted_by_id: Mapping[str, Mapping[str, Any]],
    dataset_by_id: Mapping[str, Mapping[str, Any]],
    source_cache: dict[str, tuple[pd.DataFrame, Mapping[str, np.ndarray]]],
) -> dict[str, Any]:
    accepted = accepted_by_id[sample_id]
    dataset = dataset_by_id[sample_id]
    source_path = str(accepted["source_path"])
    if source_path not in source_cache:
        frame = add_candidate_features(pd.read_csv(repo_path(source_path)))
        source_cache[source_path] = (frame, frame_arrays(frame))
    frame, arrays = source_cache[source_path]
    start_i, end_i = int(accepted["window_start_i"]), int(accepted["window_end_i"])
    rerendered, transform = render_chart(
        frame.iloc[start_i : end_i + 1].reset_index(drop=True), out_path=None
    )
    actual_path = TRAIN_DATASET / str(dataset["image_path"])
    if not actual_path.is_file() or sha256_file(actual_path) != str(dataset["image_sha256"]):
        raise TrainingParityAuditError(f"actual training input SHA drift: {sample_id}")
    actual = cv2.imread(str(actual_path), cv2.IMREAD_COLOR)
    if actual is None or not np.array_equal(actual, rerendered):
        raise TrainingParityAuditError(f"training input rerender parity failed: {sample_id}")
    box = accepted["box"]
    label_box = tuple(int(round(float(box[key]))) for key in ("x0", "y0", "x1", "y1"))
    core_start, core_end = int(accepted["source_core_start_i"]), int(accepted["source_core_end_i"])
    ma_low, ma_high = ma_price_envelope(arrays, core_start, core_end)
    ma_box = padded_ma_box(
        transform,
        x0=label_box[0],
        x1=label_box[2],
        ma_low=ma_low,
        ma_high=ma_high,
    )
    return {
        "image": actual,
        "label_box": label_box,
        "ma_box": ma_box,
        "source_path": source_path,
        "image_path": repo_relative(actual_path),
        "image_sha256": str(dataset["image_sha256"]),
    }


def comparison_canvas(
    event: Mapping[str, Any],
    current: Mapping[str, Any],
    training: Mapping[str, Any],
) -> np.ndarray:
    left = current["image"].copy()
    right = training["image"].copy()
    draw_labeled_box(left, current["prediction_box"], color=PREDICTION_COLOR, label="RAW PREDICTION")
    draw_labeled_box(left, current["ma_box"], color=MA_ONLY_COLOR, label="MA-ONLY SAME-X", thickness=3)
    draw_labeled_box(right, training["label_box"], color=TRAIN_LABEL_COLOR, label="TRAIN LABEL")
    draw_labeled_box(right, training["ma_box"], color=MA_ONLY_COLOR, label="MA-ONLY SAME-X", thickness=3)

    header_h, footer_h = 108, 96
    canvas = np.full((header_h + EXPECTED_HEIGHT + footer_h, EXPECTED_WIDTH * 2, 3), 255, dtype=np.uint8)
    canvas[header_h : header_h + EXPECTED_HEIGHT, :EXPECTED_WIDTH] = left
    canvas[header_h : header_h + EXPECTED_HEIGHT, EXPECTED_WIDTH:] = right
    cv2.line(canvas, (EXPECTED_WIDTH, header_h), (EXPECTED_WIDTH, header_h + EXPECTED_HEIGHT), (165, 165, 165), 2)
    status = str(event["semantic_classification"])
    status_color = PASS_COLOR if status == "TRAIN_SPEC_MATCH" else PREDICTION_COLOR
    put_text(
        canvas,
        f"#{int(event['event_order']):02d} {event['symbol']} {event['direction']} conf={float(event['confidence']):.3f}",
        (22, 35),
        scale=0.82,
        thickness=2,
    )
    put_text(canvas, status, (22, 76), color=status_color, scale=0.76, thickness=2)
    put_text(canvas, "ACTUAL 2026-08-27 MODEL INPUT", (22, 102), color=MUTED, scale=0.56)
    put_text(
        canvas,
        f"ACTUAL TRAIN POSITIVE #{int(event['nearest_training_source_order']):05d} {event['nearest_training_symbol']}",
        (EXPECTED_WIDTH + 22, 102),
        color=MUTED,
        scale=0.56,
    )
    footer_y = header_h + EXPECTED_HEIGHT
    cv2.rectangle(canvas, (0, footer_y), (canvas.shape[1], canvas.shape[0]), (247, 248, 250), -1)
    failed = str(event["failed_gates"]) or "none"
    put_text(
        canvas,
        f"MA envelope={float(event['ma_envelope_atr']):.2f} ATR | owner50 distance={float(event['similarity_distance_to_owner50']):.2f} | failed: {failed[:80]}",
        (22, footer_y + 33),
        scale=0.57,
        thickness=1,
    )
    put_text(
        canvas,
        f"raw-box / MA-span={float(event['prediction_height_over_ma_span']):.2f}x | MA coverage={float(event['prediction_coverage_of_ma']):.0%} | same-input strict alternative={'YES' if event['same_input_strict_alternative_exists'] else 'NO'}",
        (22, footer_y + 68),
        color=MUTED,
        scale=0.57,
        thickness=1,
    )
    put_text(
        canvas,
        f"display match: same-direction feature RMS={float(event['nearest_training_feature_rms']):.2f}; not a Gold pair",
        (EXPECTED_WIDTH + 22, footer_y + 68),
        color=MUTED,
        scale=0.53,
    )
    return canvas


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise OSError(f"OpenCV failed to write {path}")


def representative_orders(events: pd.DataFrame) -> list[int]:
    passed = events.loc[events["strict_training_spec_match"]].sort_values("event_order")
    failed = events.loc[~events["strict_training_spec_match"]].copy()
    selected = list(passed["event_order"].astype(int).head(2))
    candidates = [
        int(failed.sort_values("similarity_distance_to_owner50", ascending=False).iloc[0]["event_order"]),
        int(failed.sort_values("ma_envelope_atr", ascending=False).iloc[0]["event_order"]),
        int(
            failed.loc[failed["distance_ok"]]
            .sort_values(["confidence", "event_order"], ascending=[False, True])
            .iloc[0]["event_order"]
        )
        if bool(failed["distance_ok"].any())
        else int(failed.sort_values("confidence", ascending=False).iloc[0]["event_order"]),
        int(
            failed.iloc[
                np.argmin(
                    np.abs(
                        failed["similarity_distance_to_owner50"].to_numpy(dtype=float)
                        - failed["similarity_distance_to_owner50"].median()
                    )
                )
            ]["event_order"]
        ),
    ]
    for order in candidates:
        if order not in selected:
            selected.append(order)
    for order in failed.sort_values("event_order")["event_order"].astype(int):
        if len(selected) >= 6:
            break
        if order not in selected:
            selected.append(order)
    return selected[:6]


def build_representative_sheet(paths: Sequence[Path], output: Path) -> None:
    if len(paths) != 6:
        raise TrainingParityAuditError("representative sheet requires six comparisons")
    cell_w, cell_h = 1220, 458
    canvas = np.full((cell_h * 3, cell_w * 2, 3), 255, dtype=np.uint8)
    for index, path in enumerate(paths):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise TrainingParityAuditError(f"comparison image cannot be decoded: {path}")
        resized = cv2.resize(image, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        row, column = divmod(index, 2)
        canvas[row * cell_h : (row + 1) * cell_h, column * cell_w : (column + 1) * cell_w] = resized
    write_png(output, canvas)


def build_overview(training: pd.DataFrame, events: pd.DataFrame, output: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(13.2, 9.4), dpi=170)
    fig.suptitle(
        "Training labels vs 2026-08-27 raw predictions",
        fontsize=18,
        fontweight="bold",
    )
    blue, gold, grey = "#2f6fa7", "#d89a27", "#656d78"

    ax = axes[0, 0]
    bins = np.linspace(0, max(8.5, float(events["ma_envelope_atr"].max()) + 0.2), 35)
    ax.hist(training["ma_envelope_atr"], bins=bins, density=True, alpha=0.72, color=blue, label="train positives (10k)")
    ax.hist(events["ma_envelope_atr"], bins=bins, density=True, alpha=0.72, color=gold, label="08-27 predictions (43)")
    ax.axvline(1.5, color=grey, linestyle="--", linewidth=2, label="frozen maximum 1.5 ATR")
    ax.set_xlabel("six-MA envelope across mapped core (ATR)")
    ax.set_ylabel("density")
    ax.set_title("MA-envelope distribution")
    ax.legend(fontsize=8)

    ax = axes[0, 1]
    bins = np.linspace(0, max(4.8, float(events["similarity_distance_to_owner50"].max()) + 0.2), 34)
    ax.hist(training["similarity_distance"], bins=bins, density=True, alpha=0.72, color=blue, label="train positives (10k)")
    ax.hist(events["similarity_distance_to_owner50"], bins=bins, density=True, alpha=0.72, color=gold, label="08-27 predictions (43)")
    ax.axvline(0.5, color=grey, linestyle="--", linewidth=2, label="frozen maximum 0.5")
    ax.set_xlabel("nearest Owner-50 morphology distance")
    ax.set_ylabel("density")
    ax.set_title("Reference-family distance")
    ax.legend(fontsize=8)

    ax = axes[1, 0]
    failure_counts: Counter[str] = Counter()
    for value in events["failed_gates"]:
        failure_counts.update(name for name in str(value).split(",") if name)
    ordered = failure_counts.most_common()
    labels = [name for name, _ in ordered][::-1]
    values = [count for _, count in ordered][::-1]
    ax.barh(labels, values, color=gold, edgecolor="#8b651d")
    ax.set_xlim(0, EXPECTED_EVENTS)
    ax.set_xlabel("failed events out of 43")
    ax.set_title("Frozen morphology-gate failures")
    for index, value in enumerate(values):
        ax.text(value + 0.5, index, str(value), va="center", fontsize=8)

    ax = axes[1, 1]
    counts = [
        EXPECTED_EVENTS,
        int(events["distance_ok"].sum()),
        int(events["morphology_gate_ok"].sum()),
        int(events["strict_training_spec_match"].sum()),
    ]
    labels = ["raw 5-bar events", "distance <= 0.5", "morphology gate", "both / train-spec"]
    bars = ax.bar(labels, counts, color=[grey, blue, gold, "#23384f"])
    ax.set_ylim(0, EXPECTED_EVENTS + 4)
    ax.set_ylabel("events")
    ax.set_title("Semantic parity funnel")
    ax.tick_params(axis="x", rotation=14)
    for bar, value in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.7, str(value), ha="center", fontsize=10)

    fig.text(
        0.01,
        0.012,
        "Frozen completed-history audit. The current scan filtered box geometry only; cyan rectangles in paired images are diagnostic MA envelopes, not Gold labels.",
        fontsize=8.5,
        color="#4f5660",
    )
    fig.tight_layout(rect=(0, 0.035, 1, 0.955))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor="white")
    plt.close(fig)


def write_gallery(
    path: Path, *, comparison_rows: Sequence[Mapping[str, Any]], overview: Path, representative: Path
) -> None:
    cards = []
    for row in comparison_rows:
        image_name = Path(str(row["comparison_path"])).name
        status = str(row["semantic_classification"])
        cards.append(
            "<article><h2>"
            + f"#{int(row['event_order']):02d} {html.escape(str(row['symbol']))} {html.escape(str(row['direction']))}"
            + "</h2><p>"
            + f"{html.escape(status)} · conf {float(row['confidence']):.3f} · MA {float(row['ma_envelope_atr']):.2f} ATR · distance {float(row['similarity_distance_to_owner50']):.2f}"
            + f"</p><a href='comparisons/{html.escape(image_name)}'><img loading='lazy' src='comparisons/{html.escape(image_name)}'></a></article>"
        )
    path.write_text(
        """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>08-27 预测框 vs 实际训练图</title><style>
body{margin:0;background:#edf1f4;color:#18202a;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1800px;margin:auto;padding:22px}header{background:white;border-bottom:1px solid #ccd3da}h1{margin:0 0 10px}header img{max-width:100%;border:1px solid #c8cfd6;margin-top:12px}main{display:grid;grid-template-columns:1fr 1fr;gap:18px}article{background:white;border-radius:12px;padding:14px;box-shadow:0 2px 10px #20304016}article h2{margin:0 0 6px;font-size:18px}article p{color:#5b6470;margin:0 0 10px}article img{width:100%;height:auto;border:1px solid #d3d8de}@media(max-width:980px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>2026-08-27：43 个实际预测框 vs 模型实际训练图</h1><p>左图始终是当时模型真正看到的 W18–25 输入；右图是同方向、14项特征最近的实际训练正例。红框=原始预测，橙框=训练标签，青框=同横坐标的六均线包络（审计辅助，不是新 Gold）。</p>"""
        + f"<img src='{html.escape(overview.name)}'><img src='{html.escape(representative.name)}'></header><main>"
        + "".join(cards)
        + "</main></body></html>\n",
        encoding="utf-8",
    )


def analyze(*, prereg_path: Path = DEFAULT_PREREG, results_path: Path = DEFAULT_RESULTS) -> dict[str, Any]:
    prereg = verify_preregistration(prereg_path)
    if results_path.exists():
        raise FileExistsError(f"refusing to overwrite existing audit results: {results_path}")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix="results.building.", dir=results_path.parent))
    try:
        autofill_prereg = read_json(AUTOFILL_PREREG)
        accepted_rows = read_jsonl(TRAIN_ACCEPTED)
        dataset_rows = read_jsonl(TRAIN_MANIFEST)
        training, accepted_by_id, dataset_by_id = training_profiles(
            accepted_rows=accepted_rows,
            dataset_rows=dataset_rows,
            prereg=autofill_prereg,
        )
        events = pd.read_csv(EVENT_ANALYSIS).sort_values("event_order").reset_index(drop=True)
        full_rows = read_jsonl(FULLCONTEXT_MANIFEST)
        if len(events) != EXPECTED_EVENTS:
            raise TrainingParityAuditError(f"event count drifted: {len(events)}")
        references, reference_audits = load_reference_profiles(autofill_prereg)
        fetch_receipt = read_json(FETCH_RECEIPT)
        event_audit, current_rendered, snapshot_hashes = render_current_events(
            events=events,
            full_rows=full_rows,
            fetch_receipt=fetch_receipt,
            autofill_prereg=autofill_prereg,
            references=references,
            training=training,
        )

        training_csv = building / "training_positive_semantic_profile.csv"
        event_csv = building / "event_semantic_audit.csv"
        training.to_csv(training_csv, index=False)
        event_audit.to_csv(event_csv, index=False)

        comparisons = building / "comparisons"
        comparisons.mkdir(parents=True, exist_ok=True)
        source_cache: dict[str, tuple[pd.DataFrame, Mapping[str, np.ndarray]]] = {}
        training_render_cache: dict[str, dict[str, Any]] = {}
        comparison_rows: list[dict[str, Any]] = []
        comparison_paths: dict[int, Path] = {}
        for event in event_audit.to_dict("records"):
            order = int(event["event_order"])
            sample_id = str(event["nearest_training_sample_id"])
            if sample_id not in training_render_cache:
                training_render_cache[sample_id] = render_training_match(
                    sample_id,
                    accepted_by_id=accepted_by_id,
                    dataset_by_id=dataset_by_id,
                    source_cache=source_cache,
                )
            pair = comparison_canvas(event, current_rendered[order], training_render_cache[sample_id])
            filename = (
                f"{order:02d}_{event['symbol']}_{event['direction']}_"
                f"{event['semantic_classification']}.png"
            )
            output = comparisons / filename
            write_png(output, pair)
            comparison_paths[order] = output
            comparison_rows.append(
                {
                    **event,
                    "comparison_path": f"comparisons/{filename}",
                    "comparison_sha256": sha256_file(output),
                    "comparison_width": int(pair.shape[1]),
                    "comparison_height": int(pair.shape[0]),
                }
            )

        overview = building / "training_vs_prediction_overview.png"
        build_overview(training, event_audit, overview)
        selected_orders = representative_orders(event_audit)
        representative = building / "representative_comparisons.png"
        build_representative_sheet([comparison_paths[order] for order in selected_orders], representative)
        gallery = building / "comparison_gallery.html"
        write_gallery(
            gallery,
            comparison_rows=comparison_rows,
            overview=overview,
            representative=representative,
        )
        write_jsonl(building / "comparison_manifest.jsonl", comparison_rows)

        classification_counts = Counter(event_audit["semantic_classification"])
        failure_counts: Counter[str] = Counter()
        for value in event_audit["failed_gates"]:
            failure_counts.update(name for name in str(value).split(",") if name)
        training_candle_dominant = int(training["candle_span_exceeds_ma_span"].sum())
        summary = {
            "experiment_id": EXPERIMENT_ID,
            "status": "confirmed_prediction_training_semantic_mismatch",
            "training": {
                "positive_rows": len(training),
                "negative_rows": EXPECTED_TRAIN_NEGATIVES,
                "all_positive_rows_pass_frozen_gate_and_distance": True,
                "label_definition": "4-5 core full wicks plus six MAs, then 4% price-span padding",
                "labels_include_full_wicks_and_six_mas": int(
                    training["label_includes_full_wicks_and_six_mas"].sum()
                ),
                "candle_span_exceeds_ma_span": training_candle_dominant,
                "candle_span_exceeds_ma_span_share": training_candle_dominant / len(training),
                "candle_to_ma_span_ratio": qstats(training["candle_to_ma_span_ratio"]),
                "ma_envelope_atr": qstats(training["ma_envelope_atr"]),
                "similarity_distance": qstats(training["similarity_distance"]),
                "label_h_norm": qstats(training["label_h_norm"]),
            },
            "predictions": {
                "events": len(event_audit),
                "morphology_gate_ok": int(event_audit["morphology_gate_ok"].sum()),
                "distance_ok": int(event_audit["distance_ok"].sum()),
                "strict_training_spec_match": int(event_audit["strict_training_spec_match"].sum()),
                "strict_training_spec_match_orders": event_audit.loc[
                    event_audit["strict_training_spec_match"], "event_order"
                ].astype(int).tolist(),
                "same_input_strict_alternative_exists": int(
                    event_audit["same_input_strict_alternative_exists"].sum()
                ),
                "classification_counts": dict(classification_counts),
                "gate_failure_counts": dict(failure_counts),
                "ma_envelope_atr": qstats(event_audit["ma_envelope_atr"]),
                "similarity_distance_to_owner50": qstats(
                    event_audit["similarity_distance_to_owner50"]
                ),
                "prediction_h_norm": qstats(event_audit["prediction_h_norm"]),
                "prediction_height_over_ma_span": qstats(
                    event_audit["prediction_height_over_ma_span"]
                ),
                "prediction_coverage_of_ma": qstats(event_audit["prediction_coverage_of_ma"]),
            },
            "root_causes": [
                "The YOLO label is not an MA-only knot: it encloses full candle wicks and all six MAs with padding.",
                "The recent scan accepted any predicted x geometry mapping to 4-5 core bars with 4-6 confirmation bars; it did not rerun the frozen morphology gate or Owner-50 distance.",
                "The post-close Top20 mover universe is distribution-shifted toward large completed moves; same-generator validation mAP does not establish semantic precision on this surface.",
            ],
            "null_control": {
                "statement": "If current boxes preserve the training generator's semantics, mapped cores should pass the same frozen gate and distance used by all 10,000 positives.",
                "training_rows_passing": len(training),
                "training_rows_total": len(training),
                "current_rows_passing": int(event_audit["strict_training_spec_match"].sum()),
                "current_rows_total": len(event_audit),
            },
            "visuals": {
                "overview": "training_vs_prediction_overview.png",
                "representative": "representative_comparisons.png",
                "gallery": "comparison_gallery.html",
                "comparison_images": len(comparison_rows),
                "representative_orders": selected_orders,
            },
            "lineage": {
                "current_model_input_pixel_rerenders_exact": len(current_rendered),
                "actual_training_inputs_rerendered_exact": len(training_render_cache),
                "snapshot_hashes": snapshot_hashes,
                "reference_source_audits": reference_audits,
            },
            "holdout": {
                "consumption_number_for_this_configuration": 4,
                "existing_snapshot_only": True,
                "network_read": False,
                "new_model_inference": False,
                "training_or_tuning": False,
            },
            "safety": {
                "labels_modified": False,
                "weights_modified": False,
                "threshold_modified": False,
                "active_or_frozen_modified": False,
                "promoted": False,
                "deployed": False,
                "forward_state_changed": False,
                "orders_placed": False,
                "training_eligible": False,
                "production_eligible": False,
            },
        }
        write_json(building / "summary.json", summary)
        chart_map = {
            "training_vs_prediction_overview.png": {
                "question": "Do the current mapped cores retain the morphology and distance distribution of the 10,000 training positives?",
                "families": ["distribution", "comparison", "funnel"],
                "palette": "blue training, gold current, neutral thresholds",
            },
            "representative_comparisons.png": {
                "question": "What do exact current inputs and actual training positives look like side by side?",
                "type": "static paired image evidence",
                "palette": "red raw prediction, orange training label, cyan MA-only diagnostic",
            },
        }
        write_json(building / "chart_map.json", chart_map)
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "preregistration_sha256": sha256_file(prereg_path),
            "summary_sha256": sha256_file(building / "summary.json"),
            "event_csv_sha256": sha256_file(event_csv),
            "training_csv_sha256": sha256_file(training_csv),
            "overview_sha256": sha256_file(overview),
            "representative_sha256": sha256_file(representative),
            "gallery_sha256": sha256_file(gallery),
            "comparison_manifest_sha256": sha256_file(building / "comparison_manifest.jsonl"),
            "comparison_images": len(comparison_rows),
            "comparison_image_sha_unique": len({row["comparison_sha256"] for row in comparison_rows}),
            "passed": True,
        }
        write_json(building / "receipt.json", receipt)
        os.replace(building, results_path)
        return {**summary, "receipt": receipt, "results_path": display_path(results_path)}
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    result = analyze(prereg_path=args.prereg.resolve(), results_path=args.results.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
