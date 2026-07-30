from __future__ import annotations

import json
import random
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[2]
OUT_DIR: Final = REPO / "output" / "offline_tasks"
DATASET: Final = REPO / "datasets" / "dense_15m_full"
DATA_YAML: Final = OUT_DIR / "dense_15m_full_data.yaml"
WEIGHTS: Final = REPO / "runs" / "detect" / "runs" / "detect" / "dense_15m_full_s" / "weights" / "best.pt"


@dataclass(frozen=True)
class Box:
    __slots__ = ("x1", "y1", "x2", "y2")

    x1: float
    y1: float
    x2: float
    y2: float


def write_data_yaml() -> None:
    DATA_YAML.write_text(
        "\n".join(
            [
                f"path: {DATASET}",
                "train: images/train",
                "val: images/val",
                "names:",
                "  0: dense_cluster",
                "",
            ]
        )
    )


def load_gt(label_path: Path, width: int, height: int) -> list[Box]:
    boxes: list[Box] = []
    if not label_path.exists():
        return boxes
    for line in label_path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        _, cx_raw, cy_raw, w_raw, h_raw = parts
        cx = float(cx_raw) * width
        cy = float(cy_raw) * height
        bw = float(w_raw) * width
        bh = float(h_raw) * height
        boxes.append(Box(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2))
    return boxes


def iou(a: Box, b: Box) -> float:
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = max(0.0, a.x2 - a.x1) * max(0.0, a.y2 - a.y1)
    area_b = max(0.0, b.x2 - b.x1) * max(0.0, b.y2 - b.y1)
    return inter / (area_a + area_b - inter)


def run_fiftyone_probe() -> dict[str, str | int | bool]:
    import fiftyone as fo

    dataset_name = "fable_dense_15m_full_probe"
    if dataset_name in fo.list_datasets():
        fo.delete_dataset(dataset_name)
    dataset = fo.Dataset.from_dir(
        dataset_dir=str(DATASET),
        dataset_type=fo.types.YOLOv5Dataset,
        yaml_path=str(DATA_YAML),
        name=dataset_name,
    )
    return {
        "ok": True,
        "dataset_name": dataset.name,
        "samples": len(dataset),
        "persistent": dataset.persistent,
    }


def _prediction_box(obj) -> Box:
    minx, miny, maxx, maxy = obj.bbox.to_xyxy()
    return Box(float(minx), float(miny), float(maxx), float(maxy))


def sample_val_images(sample_size: int) -> list[Path]:
    images = sorted((DATASET / "images" / "val").glob("*.png"))
    rng = random.Random(20260709)
    return rng.sample(images, min(sample_size, len(images)))


def score_predictions(picks: list[Path], predictions: list[list[Box]]) -> dict[str, object]:
    import cv2

    total_gt = 0
    total_pred = 0
    matched = 0
    per_image: list[dict[str, object]] = []
    for path, pred_boxes in zip(picks, predictions):
        img = cv2.imread(str(path))
        height, width = img.shape[:2]
        gt = load_gt(DATASET / "labels" / "val" / f"{path.stem}.txt", width, height)
        used: set[int] = set()
        image_matches = 0
        for gt_box in gt:
            best_i = -1
            best_score = 0.0
            for i, pred_box in enumerate(pred_boxes):
                if i in used:
                    continue
                score = iou(gt_box, pred_box)
                if score > best_score:
                    best_i = i
                    best_score = score
            if best_i >= 0 and best_score >= 0.5:
                used.add(best_i)
                image_matches += 1
        total_gt += len(gt)
        total_pred += len(pred_boxes)
        matched += image_matches
        per_image.append(
            {
                "image": path.name,
                "gt": len(gt),
                "pred": len(pred_boxes),
                "matched_iou50": image_matches,
            }
        )
    return {
        "sample_size": len(picks),
        "gt_boxes": total_gt,
        "pred_boxes": total_pred,
        "matched_iou50": matched,
        "recall_like_iou50": round(matched / total_gt, 4) if total_gt else None,
        "pred_per_gt": round(total_pred / total_gt, 4) if total_gt else None,
        "per_image": per_image,
    }


def run_direct_sample(sample_size: int = 80) -> dict[str, object]:
    from ultralytics import YOLO

    if not WEIGHTS.exists():
        return {"ok": False, "error": f"missing weights: {WEIGHTS}"}
    picks = sample_val_images(sample_size)
    model = YOLO(str(WEIGHTS))
    predictions: list[list[Box]] = []
    for path in picks:
        results = model.predict(str(path), conf=0.30, device="mps", verbose=False)
        boxes = []
        for row in results[0].boxes.xyxy.cpu().tolist():
            x1, y1, x2, y2 = row
            boxes.append(Box(float(x1), float(y1), float(x2), float(y2)))
        predictions.append(boxes)
    scored = score_predictions(picks, predictions)
    return {"ok": True, "weights": str(WEIGHTS), **scored}


def run_sahi_sample(sample_size: int = 80) -> dict[str, object]:
    import cv2
    from sahi import AutoDetectionModel
    from sahi.predict import get_sliced_prediction

    if not WEIGHTS.exists():
        return {"ok": False, "error": f"missing weights: {WEIGHTS}"}
    picks = sample_val_images(sample_size)
    model = AutoDetectionModel.from_pretrained(
        model_type="ultralytics",
        model_path=str(WEIGHTS),
        confidence_threshold=0.30,
        device="mps",
    )
    predictions: list[list[Box]] = []
    for path in picks:
        img = cv2.imread(str(path))
        height, width = img.shape[:2]
        pred = get_sliced_prediction(
            str(path),
            model,
            slice_height=max(1, height // 2),
            slice_width=max(1, width // 2),
            overlap_height_ratio=0.2,
            overlap_width_ratio=0.2,
            verbose=0,
        )
        predictions.append([_prediction_box(obj) for obj in pred.object_prediction_list])
    scored = score_predictions(picks, predictions)
    return {"ok": True, "weights": str(WEIGHTS), **scored}


def capture_step(name: str, fn) -> dict[str, object]:
    try:
        return {"name": name, "result": fn()}
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK
        return {
            "name": name,
            "result": {
                "ok": False,
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            },
        }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_data_yaml()
    report = {
        "data_yaml": str(DATA_YAML),
        "dataset": str(DATASET),
        "weights": str(WEIGHTS),
        "steps": [
            capture_step("fiftyone_import_probe", run_fiftyone_probe),
            capture_step("direct_yolo_sample_eval", run_direct_sample),
            capture_step("sahi_sliced_sample_eval", run_sahi_sample),
        ],
    }
    out = OUT_DIR / "yolo_tooling_eval_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
