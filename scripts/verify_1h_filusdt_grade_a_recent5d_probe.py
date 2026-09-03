#!/usr/bin/env python3
"""Independently replay the frozen FIL 1h five-day probe artifacts.

The verifier performs no network read.  It reloads the frozen OHLCV snapshot,
checks the source chronology and hash, rebuilds every exact W18/W19 image,
recomputes both actual- and flipped-direction causal semantic decisions, and
runs the first saved exact input once on CPU to cross-check the MPS prediction.
Rows after each candidate endpoint are never passed to the renderer or gate.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts import scan_crypto_grade_a_yolo_mtf_latest as latest  # noqa: E402
from scripts.probe_1h_filusdt_grade_a_recent5d import (  # noqa: E402
    BAR_DELTA,
    DEFAULT_OUT,
    DEFAULT_PREREG,
    ENDPOINTS,
    EXPERIMENT_ID,
    HOLDOUT_NUMBER,
    SCREENSHOT,
    SYMBOL,
    latest_closed_open,
    load_preregistration,
    pixel_sha256,
    sha256_file,
    utc,
    write_json,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.semantic_gate import (  # noqa: E402
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


class FilProbeVerificationError(RuntimeError):
    """Fail closed when a frozen result cannot be replayed."""


def assert_close(actual: float, expected: float, *, atol: float, field: str) -> None:
    """Raise a named verification error for one numeric mismatch."""

    if not np.isclose(float(actual), float(expected), rtol=0.0, atol=atol):
        raise FilProbeVerificationError(
            f"{field} mismatch: actual={actual} expected={expected} atol={atol}"
        )


def main() -> int:
    results = DEFAULT_OUT.resolve()
    prereg, gates = load_preregistration(DEFAULT_PREREG.resolve())
    summary_path = results / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("experiment_id") != EXPERIMENT_ID:
        raise FilProbeVerificationError("summary experiment identity drifted")
    if int(summary.get("holdout_consumption_number_for_checkpoint", -1)) != HOLDOUT_NUMBER:
        raise FilProbeVerificationError("summary holdout number drifted")

    candle_path = results / "candles" / f"{SYMBOL}.csv"
    if sha256_file(candle_path) != str(summary["source"]["csv_sha256"]):
        raise FilProbeVerificationError("frozen candle hash drifted")
    frame = pd.read_csv(candle_path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    if not bool((frame["open_time"].diff().iloc[1:] == BAR_DELTA).all()):
        raise FilProbeVerificationError("frozen candles are not contiguous hourly rows")
    frozen_at = utc(summary["frozen_at"])
    if utc(frame.iloc[-1]["open_time"]) != latest_closed_open(frozen_at):
        raise FilProbeVerificationError("frozen source does not end at its declared closed tip")
    scan_start = len(frame) - ENDPOINTS
    if scan_start < 0:
        raise FilProbeVerificationError("frozen source is shorter than the scored endpoint range")

    structural = pd.read_csv(results / "structural_candidates.csv")
    if len(structural) != int(summary["results"]["structural_candidates"]):
        raise FilProbeVerificationError("structural candidate count drifted")
    if len(structural) != 8:
        raise FilProbeVerificationError(f"expected frozen count 8, got {len(structural)}")
    if not bool((structural["window_end_i"] >= scan_start).all()):
        raise FilProbeVerificationError("a candidate precedes the five-day scored interval")
    if not bool((structural["window_end_i"] < len(frame)).all()):
        raise FilProbeVerificationError("a candidate endpoint exceeds the frozen source")

    enriched = latest.enrich_model_frames({SYMBOL: frame})[SYMBOL]
    pixel_checks = 0
    semantic_checks = 0
    flip_checks = 0
    feature_max_delta = 0.0
    for row in structural.to_dict("records"):
        start = int(row["window_start_i"])
        end = int(row["window_end_i"])
        if end - start + 1 != int(row["window_len"]):
            raise FilProbeVerificationError("model window length drifted")
        image, _ = render_chart(enriched.iloc[start : end + 1], out_path=None)
        digest = pixel_sha256(image)
        if digest != str(row["input_pixel_sha256"]):
            raise FilProbeVerificationError("exact model input pixels drifted")
        if digest != str(row["input_pixel_replay_sha256"]):
            raise FilProbeVerificationError("inference/replay pixel hashes disagree")
        pixel_checks += 1

        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        flipped = "SHORT" if direction == "LONG" else "LONG"
        causal_prefix = enriched.iloc[: end + 1]
        actual_features = compute_causal_core_semantics(
            causal_prefix,
            core_start_i=int(row["core_start_i"]),
            core_end_i=int(row["core_end_i"]),
            observed_end_i=end,
            direction=direction,
        )
        actual = evaluate_causal_semantic_gate(actual_features, gates)
        if bool(actual.passed) != bool(row["semantic_gate_pass"]):
            raise FilProbeVerificationError("actual-direction semantic decision drifted")
        for key, value in actual_features.to_dict().items():
            stored = float(row[f"semantic_{key}"])
            if value is None or pd.isna(value):
                if not pd.isna(stored):
                    raise FilProbeVerificationError(
                        f"semantic_{key} missingness mismatch: actual={value} stored={stored}"
                    )
                continue
            delta = abs(float(value) - stored)
            feature_max_delta = max(feature_max_delta, delta)
            assert_close(value, stored, atol=1e-12, field=f"semantic_{key}")
        semantic_checks += 1

        flipped_features = compute_causal_core_semantics(
            causal_prefix,
            core_start_i=int(row["core_start_i"]),
            core_end_i=int(row["core_end_i"]),
            observed_end_i=end,
            direction=flipped,
        )
        flipped_decision = evaluate_causal_semantic_gate(flipped_features, gates)
        if bool(flipped_decision.passed) != bool(row["flipped_semantic_gate_pass"]):
            raise FilProbeVerificationError("flipped-direction semantic decision drifted")
        flip_checks += 1

    first_event = pd.read_csv(results / "structural_episodes.csv").iloc[0]
    input_path = next((results / "model_inputs").glob("01_*_input.png"))
    decoded = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if decoded is None or pixel_sha256(decoded) != str(first_event["input_pixel_sha256"]):
        raise FilProbeVerificationError("saved first exact input does not decode to frozen pixels")

    from ultralytics import YOLO

    weights = ROOT / str(prereg["detector"]["weights"])
    prediction = YOLO(str(weights)).predict(
        source=str(input_path),
        imgsz=base.IMAGE_SIZE,
        conf=base.CONFIDENCE,
        iou=base.NMS_IOU,
        device="cpu",
        verbose=False,
    )[0]
    if prediction.boxes is None or len(prediction.boxes) != 1:
        raise FilProbeVerificationError("CPU replay did not return the one frozen first box")
    cpu_xywhn = prediction.boxes.xywhn.cpu().numpy()[0]
    cpu_class = int(prediction.boxes.cls.cpu().numpy()[0])
    cpu_confidence = float(prediction.boxes.conf.cpu().numpy()[0])
    if cpu_class != int(first_event["class_id"]):
        raise FilProbeVerificationError("CPU replay class differs from MPS")
    expected_xywhn = np.asarray(
        [
            first_event["prediction_cx_norm"],
            first_event["prediction_cy_norm"],
            first_event["prediction_w_norm"],
            first_event["prediction_h_norm"],
        ],
        dtype=float,
    )
    xywhn_delta = float(np.max(np.abs(cpu_xywhn - expected_xywhn)))
    confidence_delta = abs(cpu_confidence - float(first_event["confidence"]))
    if xywhn_delta > 1e-7 or confidence_delta > 1e-4:
        raise FilProbeVerificationError(
            f"CPU/MPS drift too large: xywhn={xywhn_delta} conf={confidence_delta}"
        )

    global_path = results / "review" / "FILUSDT_P_1h_recent5d_global.png"
    global_image = cv2.imread(str(global_path), cv2.IMREAD_COLOR)
    if global_image is None or tuple(global_image.shape) != (1160, 1920, 3):
        raise FilProbeVerificationError("global review chart is missing or has wrong dimensions")
    if sha256_file(SCREENSHOT) != str(prereg["reference_screenshot"]["sha256"]):
        raise FilProbeVerificationError("intake screenshot changed after preregistration")
    copied_reference = results / "reference" / "owner_screenshot.png"
    if sha256_file(copied_reference) != sha256_file(SCREENSHOT):
        raise FilProbeVerificationError("preserved screenshot differs from intake bytes")

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "network_reads": 0,
        "model_inputs_replayed": pixel_checks,
        "model_input_pixel_failures": 0,
        "actual_semantic_decisions_replayed": semantic_checks,
        "flipped_semantic_decisions_replayed": flip_checks,
        "semantic_feature_max_abs_delta": feature_max_delta,
        "candidate_count": len(structural),
        "candidate_classes": sorted(set(map(str, structural["class_name"]))),
        "pipeline_event_count": int(summary["results"]["pipeline_events"]),
        "source_rows": len(frame),
        "source_sha256": sha256_file(candle_path),
        "source_contiguous_hourly": True,
        "source_closed_tip_matches_freeze": True,
        "future_rows_in_model_inputs": 0,
        "review_surface_physically_separate": True,
        "cpu_crosscheck": {
            "boxes": 1,
            "class_id": cpu_class,
            "confidence": cpu_confidence,
            "confidence_abs_delta_vs_mps": confidence_delta,
            "xywhn_max_abs_delta_vs_mps": xywhn_delta,
        },
        "reference_screenshot_preserved": True,
        "passed": True,
    }
    write_json(results / "verification.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
