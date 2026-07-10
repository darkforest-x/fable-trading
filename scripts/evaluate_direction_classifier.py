# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
#   "ultralytics>=8.4",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/evaluate_direction_classifier.py --weights <best.pt>
"""Evaluate one fixed direction best.pt with argmax and fixed trading costs."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from ultralytics import YOLO

from src.data.bars import purge_window
from src.detection.evaluate_direction_classifier import (
    candidate_side_predictions,
    classification_metrics,
    ordered_model_names,
)
from src.judgment.direction_economics import (
    CLASS_TO_INDEX,
    DIRECTION_CLASSES,
    evaluate_direction_predictions,
    train_numeric_direction_baseline,
)
from src.judgment.labeling import HORIZON_BARS
from src.judgment.train import HOLDOUT_START


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _image_predictions(
    weights: Path,
    dataset: Path,
    val: pd.DataFrame,
    *,
    device: str,
) -> tuple[list[str], np.ndarray]:
    model = YOLO(str(weights))
    raw_names = ordered_model_names(model.names)
    paths = [str(dataset / value) for value in val["image_path"]]
    raw_probabilities: list[np.ndarray] = []
    for result in model.predict(
        source=paths,
        imgsz=320,
        batch=64,
        device=device,
        stream=True,
        verbose=False,
    ):
        if result.probs is None:
            raise RuntimeError(f"classification probabilities missing for {result.path}")
        raw_probabilities.append(result.probs.data.cpu().numpy())
    raw = np.vstack(raw_probabilities)
    canonical = np.zeros_like(raw)
    for raw_index, class_name in enumerate(raw_names):
        canonical[:, CLASS_TO_INDEX[class_name]] = raw[:, raw_index]
    predictions = [DIRECTION_CLASSES[index] for index in canonical.argmax(axis=1)]
    return predictions, canonical


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("datasets/ma206_direction_causal_v1"))
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("analysis/output"))
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()

    started = time.monotonic()
    manifest_path = args.dataset / "manifest.csv"
    manifest = pd.read_csv(manifest_path)
    manifest["signal_time"] = pd.to_datetime(manifest["signal_time"], utc=True, errors="raise")
    latest_allowed = HOLDOUT_START - purge_window(HORIZON_BARS, "15m")
    if manifest["signal_time"].max() >= latest_allowed:
        raise RuntimeError("direction evaluation manifest reaches holdout purge boundary")
    train = manifest[manifest["split"] == "train"].copy().reset_index(drop=True)
    val = manifest[manifest["split"] == "val"].copy().reset_index(drop=True)
    truth = val["direction_class"].astype(str).tolist()

    image_predictions, image_probabilities = _image_predictions(
        args.weights,
        args.dataset,
        val,
        device=args.device,
    )
    numeric = train_numeric_direction_baseline(train, val)
    side_predictions = candidate_side_predictions(val)
    prediction_sets = {
        "image_yolo11n": image_predictions,
        "numeric_lightgbm": numeric.predictions,
        "candidate_side": side_predictions,
    }
    evaluations = {
        name: {
            "classification": asdict(classification_metrics(truth, predictions)),
            "economics": asdict(evaluate_direction_predictions(val, predictions)),
        }
        for name, predictions in prediction_sets.items()
    }

    output = val[["source", "symbol", "signal_time", "image_path", "direction_class"]].copy()
    output = output.rename(columns={"direction_class": "true_class"})
    output["image_prediction"] = image_predictions
    output["numeric_prediction"] = numeric.predictions
    output["candidate_side_prediction"] = side_predictions
    for index, class_name in enumerate(DIRECTION_CLASSES):
        output[f"image_p_{class_name}"] = image_probabilities[:, index]
        output[f"numeric_p_{class_name}"] = numeric.probabilities[:, index]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = args.out_dir / "causal_direction_val_predictions.csv"
    output.to_csv(predictions_path, index=False)
    image_cost = next(
        item
        for item in evaluations["image_yolo11n"]["economics"]["cost_metrics"]
        if item["round_trip_cost"] == 0.002
    )
    gate_passed = bool(
        image_cost["net_mean_per_trade"] is not None
        and image_cost["net_mean_per_trade"] > 0
        and image_cost["profit_factor"] is not None
        and image_cost["profit_factor"] >= 1.3
        and evaluations["image_yolo11n"]["economics"]["n_trades"] >= 100
    )
    payload = {
        "dataset": str(args.dataset.resolve()),
        "manifest_sha256": _sha256(manifest_path),
        "weights": str(args.weights.resolve()),
        "weights_sha256": _sha256(args.weights),
        "holdout_used": False,
        "policy": "argmax_only",
        "train_rows": len(train),
        "val_rows": len(val),
        "val_time_range": [str(val["signal_time"].min()), str(val["signal_time"].max())],
        "class_order": list(DIRECTION_CLASSES),
        "evaluations": evaluations,
        "gate": {
            "net_at_0_2pct_positive": True,
            "profit_factor_min": 1.3,
            "minimum_trades": 100,
            "passed": gate_passed,
        },
        "elapsed_seconds": time.monotonic() - started,
        "predictions_csv": str(predictions_path),
    }
    metrics_path = args.out_dir / "causal_direction_profit_metrics.json"
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
