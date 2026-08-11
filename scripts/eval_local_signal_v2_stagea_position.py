#!/usr/bin/env python3
"""Diagnose Stage-A detector invariance across real-candle X-position buckets.

Sources are the time-ordered validation rows of
``datasets/local_signal_v2_stagea_randomcrop_v1``.  Positive labels come from
the frozen YOLO files; easy negatives remain unassigned to a position bucket
and are reported as one global false-fire control.  The validation block was
used for early stopping, so every metric here is representation diagnostics,
never production acceptance.  No timestamp at or after the project holdout is
accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT / "datasets/local_signal_v2_stagea_randomcrop_v1"
DEFAULT_WEIGHTS = (
    PROJECT
    / "analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt"
)
DEFAULT_OUT = PROJECT / "analysis/output/p1_local_signal_v2_stagea_position_eval_20260811.json"
DEFAULT_PREDICTIONS = (
    PROJECT / "analysis/output/p1_local_signal_v2_stagea_position_predictions_20260811.json"
)
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
PREDICT_CONF_FLOOR = 0.01
PREDICT_IOU = 0.70
MATCH_IOU = 0.50
FIXED_DIAGNOSTIC_THRESHOLD = 0.05
THRESHOLDS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
BUCKETS = ("left_mid", "mid", "mid_right", "right")
MIN_BUCKET_RECALL = 0.25
MAX_BUCKET_RECALL_SPREAD = 0.20
MAX_ABS_POSITION_SCORE_SPEARMAN = 0.20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_label(path: Path) -> list[float]:
    parts = path.read_text().strip().split()
    if len(parts) != 5 or parts[0] != "0":
        raise ValueError(f"expected one class-0 label: {path}")
    values = [float(value) for value in parts[1:]]
    if any(value < 0.0 or value > 1.0 for value in values):
        raise ValueError(f"label out of bounds: {path}")
    return values


def load_validation_rows(dataset: Path) -> list[dict]:
    positives = json.loads((dataset / "w20_manifest.json").read_text())
    negatives = json.loads((dataset / "w20_neg_manifest.json").read_text())
    rows: list[dict] = []
    for row in positives:
        if row.get("split") != "val":
            continue
        end = pd.Timestamp(row["end_time"])
        if end >= HOLDOUT_START:
            raise ValueError(f"holdout positive refused: {row['event_id']} {end}")
        if row.get("stage") != "A" or row.get("production_eligible") is not False:
            raise ValueError(f"invalid Stage-A semantic flags: {row['event_id']}")
        bucket = str(row.get("position_bucket"))
        if bucket not in BUCKETS:
            raise ValueError(f"unknown position bucket: {bucket}")
        rows.append(
            {
                **row,
                "eval_id": str(row["event_id"]),
                "sample_type": "positive",
                "image_path": str(row["out_img"]),
                "gt_xywhn": read_label(PROJECT / row["out_lbl"]),
            }
        )
    for row in negatives:
        if row.get("split") != "val":
            continue
        end = pd.Timestamp(row["end_time"])
        if end >= HOLDOUT_START:
            raise ValueError(f"holdout negative refused: {row['event_id']} {end}")
        if row.get("stage") != "A" or row.get("production_eligible") is not False:
            raise ValueError(f"invalid Stage-A semantic flags: {row['event_id']}")
        rows.append(
            {
                **row,
                "eval_id": str(row["event_id"]),
                "sample_type": "easy_negative",
                "image_path": str(row["out_img"]),
                "gt_xywhn": None,
            }
        )
    eval_ids = [row["eval_id"] for row in rows]
    if len(eval_ids) != len(set(eval_ids)):
        raise ValueError("duplicate validation eval_id")
    return rows


def xywhn_iou(first: list[float], second: list[float]) -> float:
    def corners(box: list[float]) -> tuple[float, float, float, float]:
        x, y, width, height = box
        return x - width / 2, y - height / 2, x + width / 2, y + height / 2

    ax1, ay1, ax2, ay2 = corners(first)
    bx1, by1, bx2, by2 = corners(second)
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = inter_w * inter_h
    union = max((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection, 0.0)
    return intersection / union if union else 0.0


def predict(rows: list[dict], weights: Path, *, device: str, batch: int) -> dict[str, list[dict]]:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    predictions: dict[str, list[dict]] = {}
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        image_paths = [str(PROJECT / row["image_path"]) for row in chunk]
        results = model.predict(
            image_paths,
            conf=PREDICT_CONF_FLOOR,
            iou=PREDICT_IOU,
            imgsz=960,
            device=device,
            rect=True,
            verbose=False,
        )
        for row, result in zip(chunk, results):
            boxes: list[dict] = []
            if result.boxes is not None and len(result.boxes):
                xywhn = result.boxes.xywhn.cpu().numpy()
                confidence = result.boxes.conf.cpu().numpy()
                for box, score in zip(xywhn, confidence):
                    boxes.append(
                        {
                            "confidence": float(score),
                            "xywhn": [float(value) for value in box[:4]],
                        }
                    )
            predictions[row["eval_id"]] = boxes
        print(f"predict {min(start + batch, len(rows))}/{len(rows)}", flush=True)
    return predictions


def _position_score(row: dict, boxes: list[dict]) -> float:
    gt = row["gt_xywhn"]
    matching = [
        float(box["confidence"])
        for box in boxes
        if xywhn_iou(gt, box["xywhn"]) >= MATCH_IOU
    ]
    return max(matching, default=0.0)


def _spearman(values_x: list[float], values_y: list[float]) -> float:
    if len(values_x) < 2:
        return 0.0
    rank_x = pd.Series(values_x).rank(method="average").to_numpy(dtype=float)
    rank_y = pd.Series(values_y).rank(method="average").to_numpy(dtype=float)
    if np.std(rank_x) == 0.0 or np.std(rank_y) == 0.0:
        return 0.0
    return float(np.corrcoef(rank_x, rank_y)[0, 1])


def score_threshold(rows: list[dict], predictions: dict[str, list[dict]], threshold: float) -> dict:
    tp_events = 0
    false_positive_boxes = 0
    negative_fired = 0
    negative_boxes = 0
    bucket_stats: dict[str, dict[str, object]] = {
        bucket: {"events": 0, "tp_events": 0, "unmatched_boxes": 0, "matched_confidence": [], "center_error_bars": []}
        for bucket in BUCKETS
    }
    for row in rows:
        boxes = [
            box
            for box in predictions.get(row["eval_id"], [])
            if float(box["confidence"]) >= threshold
        ]
        if row["sample_type"] != "positive":
            negative_boxes += len(boxes)
            negative_fired += int(bool(boxes))
            false_positive_boxes += len(boxes)
            continue
        bucket = str(row["position_bucket"])
        stats = bucket_stats[bucket]
        stats["events"] = int(stats["events"]) + 1
        gt = row["gt_xywhn"]
        indexed = [(xywhn_iou(gt, box["xywhn"]), box) for box in boxes]
        best_iou, best_box = max(indexed, key=lambda item: item[0], default=(0.0, None))
        if best_box is not None and best_iou >= MATCH_IOU:
            tp_events += 1
            stats["tp_events"] = int(stats["tp_events"]) + 1
            unmatched = len(boxes) - 1
            stats["matched_confidence"].append(float(best_box["confidence"]))
            center_error = abs(float(best_box["xywhn"][0]) - float(gt[0])) * (int(row["win_len"]) - 1)
            stats["center_error_bars"].append(center_error)
        else:
            unmatched = len(boxes)
        stats["unmatched_boxes"] = int(stats["unmatched_boxes"]) + unmatched
        false_positive_boxes += unmatched
    n_positive = sum(row["sample_type"] == "positive" for row in rows)
    n_negative = len(rows) - n_positive
    precision_denominator = tp_events + false_positive_boxes
    precision = tp_events / precision_denominator if precision_denominator else 0.0
    recall = tp_events / n_positive if n_positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    buckets: dict[str, dict] = {}
    for bucket, raw in bucket_stats.items():
        events = int(raw["events"])
        tp = int(raw["tp_events"])
        unmatched = int(raw["unmatched_boxes"])
        positive_precision_denom = tp + unmatched
        confidences = list(raw["matched_confidence"])
        center_errors = list(raw["center_error_bars"])
        buckets[bucket] = {
            "events": events,
            "tp_events": tp,
            "event_recall": tp / events if events else None,
            "positive_image_precision": tp / positive_precision_denom if positive_precision_denom else 0.0,
            "unmatched_boxes": unmatched,
            "matched_confidence_mean": float(np.mean(confidences)) if confidences else None,
            "center_error_bars_mean": float(np.mean(center_errors)) if center_errors else None,
            "center_error_bars_p90": float(np.quantile(center_errors, 0.90)) if center_errors else None,
        }
    return {
        "threshold": threshold,
        "n_positive": n_positive,
        "n_easy_negative": n_negative,
        "tp_events": tp_events,
        "false_positive_boxes": false_positive_boxes,
        "event_precision": precision,
        "event_recall": recall,
        "event_f1": f1,
        "easy_negative_fired_endpoints": negative_fired,
        "easy_negative_fire_rate": negative_fired / n_negative if n_negative else None,
        "easy_negative_false_boxes": negative_boxes,
        "buckets": buckets,
    }


def position_diagnostic(rows: list[dict], predictions: dict[str, list[dict]], fixed: dict) -> dict:
    positives = [row for row in rows if row["sample_type"] == "positive"]
    position_scores = [
        _position_score(row, predictions.get(row["eval_id"], [])) for row in positives
    ]
    anchor_ratios = [float(row["anchor_x_ratio"]) for row in positives]
    score_spearman = _spearman(anchor_ratios, position_scores)
    recalls = [float(fixed["buckets"][bucket]["event_recall"]) for bucket in BUCKETS]
    recall_spread = max(recalls) - min(recalls)
    bucket_score_quantiles = {}
    for bucket in BUCKETS:
        scores = [
            score
            for row, score in zip(positives, position_scores)
            if row["position_bucket"] == bucket
        ]
        bucket_score_quantiles[bucket] = {
            "n": len(scores),
            "p25": float(np.quantile(scores, 0.25)),
            "p50": float(np.quantile(scores, 0.50)),
            "p75": float(np.quantile(scores, 0.75)),
        }
    gates = {
        "all_bucket_recall_gte_min": min(recalls) >= MIN_BUCKET_RECALL,
        "bucket_recall_spread_lte_max": recall_spread <= MAX_BUCKET_RECALL_SPREAD,
        "abs_position_score_spearman_lte_max": abs(score_spearman) <= MAX_ABS_POSITION_SCORE_SPEARMAN,
    }
    return {
        "fixed_threshold": FIXED_DIAGNOSTIC_THRESHOLD,
        "minimum_bucket_recall": MIN_BUCKET_RECALL,
        "maximum_bucket_recall_spread": MAX_BUCKET_RECALL_SPREAD,
        "maximum_abs_position_score_spearman": MAX_ABS_POSITION_SCORE_SPEARMAN,
        "bucket_recall_spread": recall_spread,
        "anchor_x_vs_iou_matched_score_spearman": score_spearman,
        "bucket_iou_matched_score_quantiles": bucket_score_quantiles,
        "gates": gates,
        "position_invariance_diagnostic_pass": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--predictions-out", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    args = parser.parse_args()
    rows = load_validation_rows(args.dataset)
    if not args.weights.exists():
        parser.error(f"missing weights: {args.weights}")
    if args.predictions:
        predictions_doc = json.loads(args.predictions.read_text())
        predictions = predictions_doc["predictions"]
    else:
        predictions = predict(rows, args.weights, device=args.device, batch=args.batch)
        args.predictions_out.parent.mkdir(parents=True, exist_ok=True)
        args.predictions_out.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "weights_sha256": sha256_file(args.weights),
                    "predict_conf_floor": PREDICT_CONF_FLOOR,
                    "predict_iou": PREDICT_IOU,
                    "predictions": predictions,
                },
                indent=2,
            )
        )
    threshold_rows = [score_threshold(rows, predictions, threshold) for threshold in THRESHOLDS]
    fixed = next(row for row in threshold_rows if row["threshold"] == FIXED_DIAGNOSTIC_THRESHOLD)
    best_f1 = max(threshold_rows, key=lambda row: (row["event_f1"], row["event_precision"], row["threshold"]))
    diagnostic = position_diagnostic(rows, predictions, fixed)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "stage_a_validation_position_diagnostic_only",
        "dataset": str(args.dataset.relative_to(PROJECT)),
        "weights": str(args.weights.relative_to(PROJECT)),
        "weights_sha256": sha256_file(args.weights),
        "validation_used_for_early_stopping": True,
        "independent_acceptance_set": False,
        "holdout_read": False,
        "production_eligible": False,
        "matching_iou": MATCH_IOU,
        "predict_conf_floor": PREDICT_CONF_FLOOR,
        "predict_iou": PREDICT_IOU,
        "fixed_diagnostic": fixed,
        "best_f1_same_validation_optimistic": best_f1,
        "position_diagnostic": diagnostic,
        "thresholds": threshold_rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "fixed_diagnostic": fixed,
                "best_f1_same_validation_optimistic": best_f1,
                "position_diagnostic": diagnostic,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
