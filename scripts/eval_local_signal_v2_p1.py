#!/usr/bin/env python3
"""Evaluate one P1 detector with event metrics on the common causal ruler.

Inputs are pre-rendered, pre-holdout decision windows produced by
``build_local_signal_v2_p1_eval.py``.  Predictions are collected once at a
low confidence floor, then scored on the preregistered threshold grid.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL = PROJECT / "analysis" / "output" / "local_signal_v2_p1_eval"
THRESHOLDS = tuple(round(x / 100, 2) for x in range(5, 100, 5))
MATCH_TOLERANCE_BARS = 2
PREDICT_CONF_FLOOR = 0.001
PREDICT_IOU = 0.70


def x_center_to_bar(x_center_norm: float, window_len: int, *, width: int = 1280) -> int:
    """Invert the renderer's x transform for a normalized box center."""
    left = 12
    plot_w = width - 24
    x_px = float(x_center_norm) * width
    value = round((x_px - left) / plot_w * (window_len - 1))
    return int(min(max(value, 0), window_len - 1))


def score_threshold(
    rows: list[dict],
    predictions: dict[str, list[dict]],
    threshold: float,
    *,
    tolerance: int = MATCH_TOLERANCE_BARS,
) -> dict:
    n_positive = sum(row["sample_type"] == "positive" for row in rows)
    tp_events = misses = false_positive_boxes = duplicates = 0
    latencies: list[int] = []
    for row in rows:
        boxes = [
            box
            for box in predictions.get(row["eval_id"], [])
            if float(box["confidence"]) >= threshold
        ]
        if row["sample_type"] != "positive":
            false_positive_boxes += len(boxes)
            continue
        anchor = int(row["anchor_local_bar"])
        matching = [box for box in boxes if abs(int(box["center_bar"]) - anchor) <= tolerance]
        if matching:
            tp_events += 1
            duplicates += max(0, len(matching) - 1)
            false_positive_boxes += len(boxes) - 1
            delay = row.get("confirm_delay")
            if delay is not None:
                latencies.append(int(delay))
        else:
            misses += 1
            false_positive_boxes += len(boxes)
    precision_denom = tp_events + false_positive_boxes
    precision = tp_events / precision_denom if precision_denom else 0.0
    recall = tp_events / n_positive if n_positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    n_endpoints = len(rows)
    return {
        "threshold": threshold,
        "n_endpoints": n_endpoints,
        "n_positive_events": n_positive,
        "tp_events": tp_events,
        "missed_events": misses,
        "false_positive_boxes": false_positive_boxes,
        "duplicate_detections": duplicates,
        "event_precision": precision,
        "event_recall": recall,
        "event_f1": f1,
        "fp_per_1000_bars": (
            false_positive_boxes / n_endpoints * 1000 if n_endpoints else 0.0
        ),
        "duplicates_per_detected_event": duplicates / tp_events if tp_events else 0.0,
        "mean_detection_latency_bars": float(np.mean(latencies)) if latencies else None,
    }


def score_thresholds(
    rows: list[dict], predictions: dict[str, list[dict]], thresholds: Iterable[float]
) -> list[dict]:
    return [score_threshold(rows, predictions, float(t)) for t in thresholds]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def predict(rows: list[dict], weights: Path, *, device: str, batch: int) -> dict[str, list[dict]]:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    output: dict[str, list[dict]] = {}
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        paths = [str(PROJECT / row["image_path"]) for row in chunk]
        results = model.predict(
            paths,
            conf=PREDICT_CONF_FLOOR,
            iou=PREDICT_IOU,
            imgsz=960,
            device=device,
            verbose=False,
        )
        for row, result in zip(chunk, results):
            boxes: list[dict] = []
            if result.boxes is not None and len(result.boxes):
                xywhn = result.boxes.xywhn.cpu().numpy()
                conf = result.boxes.conf.cpu().numpy()
                for box, score in zip(xywhn, conf):
                    center = float(box[0])
                    boxes.append(
                        {
                            "confidence": float(score),
                            "center_x_norm": center,
                            "center_bar": x_center_to_bar(center, int(row["window_len"])),
                            "xywhn": [float(value) for value in box[:4]],
                        }
                    )
            output[row["eval_id"]] = boxes
        print(f"predict {min(start + batch, len(rows))}/{len(rows)}", flush=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("A", "B1", "B2", "C3"), required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--eval-root", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=8)
    args = parser.parse_args()
    manifest = args.eval_root / args.arm / "manifest.jsonl"
    if not manifest.exists():
        parser.error(f"missing eval manifest: {manifest}")
    if not args.weights.exists():
        parser.error(f"missing weights: {args.weights}")
    rows = read_jsonl(manifest)
    predictions = predict(rows, args.weights, device=args.device, batch=args.batch)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "arm": args.arm,
        "weights": str(args.weights),
        "manifest": str(manifest),
        "predict_conf_floor": PREDICT_CONF_FLOOR,
        "predict_iou": PREDICT_IOU,
        "matching_tolerance_bars": MATCH_TOLERANCE_BARS,
        "thresholds": score_thresholds(rows, predictions, THRESHOLDS),
        "predictions": predictions,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
