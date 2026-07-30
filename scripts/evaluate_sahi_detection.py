# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "opencv-python>=4.10",
#   "sahi==0.12.1",
#   "ultralytics==8.4.89",
# ]
# ///
"""Run the fixed E2.1b direct-versus-SAHI IoU50 diagnostic with checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Final

import cv2

from src.detection.consistency_check import _load_yolo_boxes, match_greedy
from src.detection.sahi_benchmark import (
    BenchmarkRecord,
    normalize_xyxy,
    select_image_paths,
    summarize_records,
)

CONFIDENCE: Final = 0.30
DIRECT_IMAGE_SIZE: Final = 960
DIRECT_NMS_IOU: Final = 0.70
MATCH_IOU: Final = 0.50
SLICE_WIDTH: Final = 640
SLICE_HEIGHT: Final = 371
OVERLAP_RATIO: Final = 0.20
SAHI_POSTPROCESS_TYPE: Final = "GREEDYNMM"
SAHI_POSTPROCESS_METRIC: Final = "IOS"
SAHI_POSTPROCESS_THRESHOLD: Final = 0.50


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkpoint_records(path: Path, *, mode: str, images: set[str]) -> dict[str, BenchmarkRecord]:
    if not path.exists():
        return {}
    records: dict[str, BenchmarkRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = BenchmarkRecord(**json.loads(line))
        if record.mode != mode or record.image not in images:
            raise RuntimeError(f"checkpoint {path} does not match the current {mode} image set")
        if record.image in records:
            raise RuntimeError(f"duplicate checkpoint row for {record.image}")
        records[record.image] = record
    return records


def _append_record(path: Path, record: BenchmarkRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def _image_shape(path: Path) -> tuple[int, int]:
    image = cv2.imread(str(path))
    if image is None:
        raise RuntimeError(f"cannot read image: {path}")
    height, width = image.shape[:2]
    return width, height


def _record(
    *,
    mode: str,
    image: Path,
    label_dir: Path,
    predictions: list[tuple[float, float, float, float]],
    elapsed_seconds: float,
) -> BenchmarkRecord:
    gt = _load_yolo_boxes(label_dir / f"{image.stem}.txt")
    matched, n_gt, n_pred = match_greedy(gt, predictions, iou_thr=MATCH_IOU)
    return BenchmarkRecord(mode, image.name, n_gt, n_pred, matched, elapsed_seconds)


def _run_direct(
    images: list[Path],
    *,
    weights: Path,
    label_dir: Path,
    checkpoint: Path,
    device: str,
) -> list[BenchmarkRecord]:
    from ultralytics import YOLO

    completed = _checkpoint_records(checkpoint, mode="direct", images={path.name for path in images})
    model = YOLO(str(weights))
    for index, image in enumerate(images, start=1):
        if image.name in completed:
            continue
        started = time.perf_counter()
        result = model.predict(
            source=str(image),
            conf=CONFIDENCE,
            iou=DIRECT_NMS_IOU,
            imgsz=DIRECT_IMAGE_SIZE,
            device=device,
            verbose=False,
        )[0]
        predictions = [tuple(float(value) for value in row) for row in result.boxes.xywhn.cpu().tolist()]
        record = _record(
            mode="direct",
            image=image,
            label_dir=label_dir,
            predictions=predictions,
            elapsed_seconds=time.perf_counter() - started,
        )
        _append_record(checkpoint, record)
        completed[image.name] = record
        if index % 25 == 0 or index == len(images):
            print(f"direct {index}/{len(images)}")
    return [completed[path.name] for path in images]


def _run_sahi(
    images: list[Path],
    *,
    weights: Path,
    label_dir: Path,
    checkpoint: Path,
    device: str,
) -> list[BenchmarkRecord]:
    from sahi import AutoDetectionModel
    from sahi.predict import get_sliced_prediction

    completed = _checkpoint_records(checkpoint, mode="sahi", images={path.name for path in images})
    model = AutoDetectionModel.from_pretrained(
        model_type="ultralytics",
        model_path=str(weights),
        confidence_threshold=CONFIDENCE,
        device=device,
        image_size=DIRECT_IMAGE_SIZE,
    )
    for index, image in enumerate(images, start=1):
        if image.name in completed:
            continue
        width, height = _image_shape(image)
        started = time.perf_counter()
        result = get_sliced_prediction(
            str(image),
            model,
            slice_height=SLICE_HEIGHT,
            slice_width=SLICE_WIDTH,
            overlap_height_ratio=OVERLAP_RATIO,
            overlap_width_ratio=OVERLAP_RATIO,
            perform_standard_pred=True,
            postprocess_type=SAHI_POSTPROCESS_TYPE,
            postprocess_match_metric=SAHI_POSTPROCESS_METRIC,
            postprocess_match_threshold=SAHI_POSTPROCESS_THRESHOLD,
            postprocess_class_agnostic=False,
            verbose=0,
        )
        predictions = [
            normalize_xyxy(tuple(float(value) for value in obj.bbox.to_xyxy()), width=width, height=height)
            for obj in result.object_prediction_list
        ]
        record = _record(
            mode="sahi",
            image=image,
            label_dir=label_dir,
            predictions=predictions,
            elapsed_seconds=time.perf_counter() - started,
        )
        _append_record(checkpoint, record)
        completed[image.name] = record
        if index % 10 == 0 or index == len(images):
            print(f"sahi {index}/{len(images)}")
    return [completed[path.name] for path in images]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("output/offline_tasks/sahi_e21b"))
    parser.add_argument("--limit", type=int, default=80, help="0 evaluates the full validation split")
    parser.add_argument("--seed", type=int, default=20260709)
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()

    image_dir = args.dataset / "images" / "val"
    label_dir = args.dataset / "labels" / "val"
    images = select_image_paths(list(image_dir.glob("*.png")), limit=args.limit, seed=args.seed)
    if not images or not label_dir.is_dir() or not args.weights.is_file():
        raise RuntimeError("dataset validation images, labels, and weights are required")
    run_name = f"sample_{len(images)}" if args.limit else "full_val"
    run_dir = args.out_dir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "dataset": str(args.dataset),
        "weights": str(args.weights),
        "weights_sha256": _sha256(args.weights),
        "holdout_used": False,
        "official_map": False,
        "images": len(images),
        "seed": args.seed if args.limit else None,
        "confidence": CONFIDENCE,
        "direct_imgsz": DIRECT_IMAGE_SIZE,
        "direct_nms_iou": DIRECT_NMS_IOU,
        "match_iou": MATCH_IOU,
        "slice_width": SLICE_WIDTH,
        "slice_height": SLICE_HEIGHT,
        "overlap_ratio": OVERLAP_RATIO,
        "perform_standard_pred": True,
        "sahi_postprocess": {
            "type": SAHI_POSTPROCESS_TYPE,
            "metric": SAHI_POSTPROCESS_METRIC,
            "threshold": SAHI_POSTPROCESS_THRESHOLD,
        },
    }
    config_path = run_dir / "config.json"
    if config_path.exists() and json.loads(config_path.read_text(encoding="utf-8")) != config:
        raise RuntimeError(f"existing SAHI run config differs: {config_path}")
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    direct = _run_direct(
        images,
        weights=args.weights,
        label_dir=label_dir,
        checkpoint=run_dir / "direct.jsonl",
        device=args.device,
    )
    sahi = _run_sahi(
        images,
        weights=args.weights,
        label_dir=label_dir,
        checkpoint=run_dir / "sahi.jsonl",
        device=args.device,
    )
    report = {"config": config, "direct": summarize_records(direct), "sahi": summarize_records(sahi)}
    report_path = run_dir / "summary.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
