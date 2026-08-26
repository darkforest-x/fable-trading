#!/usr/bin/env python3
"""Compare frozen YOLO false fires on easy-val versus hard-val backgrounds."""

from __future__ import annotations

import argparse
import json
import platform
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from yoyo.datasets.fifteen_minute_launch_candidates import sha256_file
from yoyo.datasets.ma_launch_t3_hard_val import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    load_base_manifest,
    load_contract,
)


def _paths(
    rows: Iterable[Mapping[str, Any]], *, root: Path
) -> tuple[list[Path], list[str]]:
    selected = sorted(rows, key=lambda row: str(row["sample_id"]))
    return (
        [root / str(row["image_path"]) for row in selected],
        [str(row["sample_id"]) for row in selected],
    )


def _evaluate(
    model: Any,
    *,
    paths: list[Path],
    sample_ids: list[str],
    device: str,
    imgsz: int,
    confidence: float,
    batch: int,
) -> dict[str, Any]:
    if len(paths) != len(sample_ids):
        raise ValueError("path/sample identity count differs")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(missing[:3])
    fired = 0
    boxes_total = 0
    class_counts: Counter[int] = Counter()
    confidences: list[float] = []
    fired_rows: list[dict[str, Any]] = []
    seen = 0
    # A single list with thousands of paths is interpreted by Ultralytics as
    # one in-memory source batch before its own batch iterator runs.  Explicit
    # fixed-size chunks prevent a multi-GiB preprocessing buffer on MPS.
    for start in range(0, len(paths), batch):
        chunk_paths = paths[start : start + batch]
        chunk_ids = sample_ids[start : start + batch]
        predictions = model.predict(
            source=[str(path) for path in chunk_paths],
            imgsz=imgsz,
            conf=confidence,
            batch=batch,
            device=device,
            stream=False,
            verbose=False,
            save=False,
        )
        if len(predictions) != len(chunk_paths):
            raise RuntimeError(
                f"prediction chunk ended early: {len(predictions)}/{len(chunk_paths)}"
            )
        for sample_id, result in zip(chunk_ids, predictions):
            seen += 1
            boxes = result.boxes
            count = 0 if boxes is None else len(boxes)
            if count == 0:
                continue
            fired += 1
            boxes_total += count
            confs = boxes.conf.detach().cpu().numpy().astype(float).tolist()
            classes = boxes.cls.detach().cpu().numpy().astype(int).tolist()
            confidences.extend(confs)
            class_counts.update(classes)
            fired_rows.append(
                {
                    "sample_id": sample_id,
                    "boxes": count,
                    "max_confidence": max(confs),
                    "classes": classes,
                }
            )
    if seen != len(paths):
        raise RuntimeError(f"prediction stream ended early: {seen}/{len(paths)}")
    values = np.asarray(confidences, dtype=float)
    return {
        "images": len(paths),
        "fired_images": fired,
        "fire_rate": fired / len(paths),
        "boxes": boxes_total,
        "false_boxes_per_1000_images": boxes_total / len(paths) * 1000.0,
        "class_box_counts": {str(key): value for key, value in sorted(class_counts.items())},
        "confidence": {
            "min": None if not len(values) else float(values.min()),
            "median": None if not len(values) else float(np.median(values)),
            "p90": None if not len(values) else float(np.quantile(values, 0.9)),
            "max": None if not len(values) else float(values.max()),
        },
        "fired_samples": sorted(
            fired_rows, key=lambda row: (-float(row["max_confidence"]), row["sample_id"])
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--hard-dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--weights-sha256")
    parser.add_argument("--imgsz", type=int)
    parser.add_argument("--confidence", type=float)
    parser.add_argument("--experiment-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    contract, _ = load_contract(args.prereg.resolve())
    base_rows = load_base_manifest(contract)
    base_root = Path(contract["sources"]["base_dataset"]["root"]).resolve()
    hard_root = args.hard_dataset.resolve()
    hard_rows = [
        json.loads(line)
        for line in (hard_root / "manifest.jsonl").read_text().splitlines()
        if line
    ]
    easy_rows = [
        row
        for row in base_rows
        if row["sample_kind"] == "negative_easy" and row["split"] == "val"
    ]
    easy_paths, easy_ids = _paths(easy_rows, root=base_root)
    hard_paths, hard_ids = _paths(hard_rows, root=hard_root)
    weights = (
        args.weights.resolve()
        if args.weights is not None
        else Path(contract["sources"]["weights"]["path"]).resolve()
    )
    if args.weights is not None and not args.weights_sha256:
        raise ValueError("--weights requires --weights-sha256")
    expected_weights_sha256 = (
        str(args.weights_sha256)
        if args.weights_sha256
        else str(contract["sources"]["weights"]["sha256"])
    )
    if sha256_file(weights) != expected_weights_sha256:
        raise RuntimeError("frozen weight hash drifted")

    import torch
    import ultralytics
    from ultralytics import YOLO

    config = contract["evaluation"]
    confidence = (
        float(args.confidence)
        if args.confidence is not None
        else float(config["confidence_threshold"])
    )
    imgsz = int(args.imgsz) if args.imgsz is not None else int(config["imgsz"])
    model = YOLO(str(weights))
    easy = _evaluate(
        model,
        paths=easy_paths,
        sample_ids=easy_ids,
        device=args.device,
        imgsz=imgsz,
        confidence=confidence,
        batch=args.batch,
    )
    hard = _evaluate(
        model,
        paths=hard_paths,
        sample_ids=hard_ids,
        device=args.device,
        imgsz=imgsz,
        confidence=confidence,
        batch=args.batch,
    )
    receipt = {
        "experiment_id": args.experiment_id or contract["experiment_id"],
        "weights_path": str(weights),
        "weights_sha256": sha256_file(weights),
        "device": args.device,
        "batch": args.batch,
        "imgsz": imgsz,
        "confidence_threshold": confidence,
        "threshold_tuned": False,
        "easy_val": easy,
        "hard_val": hard,
        "hard_minus_easy_fire_rate_pp": 100.0 * (
            float(hard["fire_rate"]) - float(easy["fire_rate"])
        ),
        "hard_to_easy_false_boxes_per_1000_ratio": (
            None
            if float(easy["false_boxes_per_1000_images"]) == 0.0
            else float(hard["false_boxes_per_1000_images"])
            / float(easy["false_boxes_per_1000_images"])
        ),
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "numpy": np.__version__,
        },
        "holdout_consumed": False,
        "model_trained": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "production_eligible": False,
    }
    output = (
        args.output.resolve()
        if args.output is not None
        else args.results.resolve() / "hard_val_evaluation.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
