#!/usr/bin/env python3
"""Compare P1 B2 and P2 on the evaluation-only mined hard-negative bank.

The bank contains val-time empty windows that P1 B2 fired on at the frozen
``conf=0.35`` threshold. It is excluded from YOLO ``data.yaml`` and therefore
cannot affect gradient updates, early stopping, or best-epoch selection. This
script reads no market outcome and refuses timestamps at or after the project
holdout boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT / "datasets/local_signal_v2_p2_hardneg_r1"
DEFAULT_BASELINE = PROJECT / "analysis/output/p1_local_signal_v2/training/B2/weights/best.pt"
DEFAULT_OUT = PROJECT / "analysis/output/p2_local_signal_v2_hardneg_eval_20260811.json"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
CONFIDENCE = 0.35
PREDICT_IOU = 0.70


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bank(rows: list[dict], dataset: Path) -> None:
    """Prove the evaluation bank is pre-holdout and absent from training inputs."""
    if not rows:
        raise ValueError("held-out hard-negative bank is empty")
    if any(row.get("split") != "val" for row in rows):
        raise ValueError("held-out hard-negative bank contains a non-val row")
    if any(row.get("selection_visibility") != "evaluation_only_not_in_data_yaml" for row in rows):
        raise ValueError("held-out bank visibility marker is missing")
    if any(pd.Timestamp(row["end_time"]) >= HOLDOUT_START for row in rows):
        raise ValueError("held-out hard-negative bank touches project holdout")
    data_yaml = (dataset / "data.yaml").read_text()
    if "evaluation" in data_yaml or "heldout" in data_yaml:
        raise ValueError("data.yaml exposes the evaluation-only bank")
    training_manifest = (dataset / "manifest.jsonl").read_text()
    if "heldout_hard_negative" in training_manifest:
        raise ValueError("training manifest exposes the evaluation-only bank")


def predict(rows: list[dict], weights: Path, *, device: str, batch: int) -> dict[str, list[float]]:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    predictions: dict[str, list[float]] = {}
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        paths = [str(PROJECT / row["image_path"]) for row in chunk]
        results = model.predict(
            paths,
            conf=CONFIDENCE,
            iou=PREDICT_IOU,
            imgsz=960,
            device=device,
            verbose=False,
        )
        for row, result in zip(chunk, results):
            scores = []
            if result.boxes is not None and len(result.boxes):
                scores = [float(value) for value in result.boxes.conf.cpu().numpy()]
            predictions[str(row["stem"])] = scores
        print(f"predict {min(start + batch, len(rows))}/{len(rows)}", flush=True)
    return predictions


def score_negative_predictions(predictions: dict[str, list[float]]) -> dict:
    """Summarize endpoint-level false fires and box density at frozen conf."""
    counts = np.asarray([len(scores) for scores in predictions.values()], dtype=int)
    fired = counts > 0
    max_scores = [max(scores) for scores in predictions.values() if scores]
    n = len(counts)
    total_boxes = int(counts.sum())
    return {
        "endpoints": n,
        "fired_endpoints": int(fired.sum()),
        "endpoint_fire_rate": float(fired.mean()) if n else None,
        "total_false_positive_boxes": total_boxes,
        "false_positive_boxes_per_1000_endpoints": total_boxes / n * 1000 if n else None,
        "duplicate_boxes_after_first": int(np.maximum(counts - 1, 0).sum()),
        "fired_max_confidence_quantiles": (
            {
                "p50": float(np.quantile(max_scores, 0.50)),
                "p90": float(np.quantile(max_scores, 0.90)),
                "p99": float(np.quantile(max_scores, 0.99)),
            }
            if max_scores
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline-weights", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate-weights", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    args = parser.parse_args()

    bank_path = args.dataset / "heldout_hard_negative_bank.jsonl"
    rows = read_jsonl(bank_path)
    validate_bank(rows, args.dataset)
    baseline_predictions = predict(
        rows, args.baseline_weights, device=args.device, batch=args.batch
    )
    candidate_predictions = predict(
        rows, args.candidate_weights, device=args.device, batch=args.batch
    )
    baseline = score_negative_predictions(baseline_predictions)
    candidate = score_negative_predictions(candidate_predictions)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "evaluation_only_val_block_mined_hard_negatives",
        "selection_bias": "bank was selected because P1 B2 fired; baseline fire rate is 100% by construction",
        "dataset": str(args.dataset.relative_to(PROJECT)),
        "bank": str(bank_path.relative_to(PROJECT)),
        "threshold": CONFIDENCE,
        "threshold_changed": False,
        "predict_iou": PREDICT_IOU,
        "baseline_weights": str(args.baseline_weights.relative_to(PROJECT)),
        "baseline_weights_sha256": sha256_file(args.baseline_weights),
        "candidate_weights": str(args.candidate_weights.relative_to(PROJECT)),
        "candidate_weights_sha256": sha256_file(args.candidate_weights),
        "baseline": baseline,
        "candidate": candidate,
        "candidate_minus_baseline": {
            "fired_endpoints": candidate["fired_endpoints"] - baseline["fired_endpoints"],
            "endpoint_fire_rate": candidate["endpoint_fire_rate"] - baseline["endpoint_fire_rate"],
            "false_positive_boxes_per_1000_endpoints": (
                candidate["false_positive_boxes_per_1000_endpoints"]
                - baseline["false_positive_boxes_per_1000_endpoints"]
            ),
        },
        "future_outcome_used": False,
        "heldout_read": False,
        "promoted": False,
        "predictions": {
            "baseline": baseline_predictions,
            "candidate": candidate_predictions,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("baseline", "candidate", "candidate_minus_baseline")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
