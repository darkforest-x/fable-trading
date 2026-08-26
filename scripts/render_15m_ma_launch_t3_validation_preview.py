#!/usr/bin/env python3
"""Render deterministic pre-holdout validation predictions for the t-3 model.

The preview selects four LONG positives, four SHORT positives and eight easy
backgrounds from the frozen validation manifest.  It reads only materialized
PNG/YOLO files, never OHLCV.  Ground truth and best-model predictions are drawn
on copies for report QA; the immutable training images are not changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np

from yoyo.datasets.fifteen_minute_launch_candidates import sha256_file


ROOT = Path(__file__).resolve().parents[1]
RUN_NAME = "ma_launch_t3_10000_v1_y11s_ft"
DEFAULT_RUN = ROOT / "analysis" / "output" / "ma_launch_t3_10000_v1" / RUN_NAME
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_t3_10000_v1"
DEFAULT_RESULTS = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-t3-yolo10000-v1"
    / "results"
)


class PreviewError(ValueError):
    """Fail-closed validation-preview error."""


def stable_rows(rows: Iterable[Mapping[str, Any]], count: int, salt: str) -> list[dict[str, Any]]:
    """Choose a deterministic identity sample independent of manifest order."""

    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: hashlib.sha256(
            f"{salt}|{row['sample_id']}".encode("utf-8")
        ).hexdigest(),
    )
    if len(ordered) < count:
        raise PreviewError(f"sample pool has {len(ordered)} rows, needs {count}")
    return ordered[:count]


def select_preview_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the frozen 4 LONG + 4 SHORT + 8 background preview mix."""

    val = [row for row in rows if row.get("split") == "val"]
    selected = []
    selected.extend(
        stable_rows(
            (row for row in val if row.get("sample_kind") == "positive_weak" and row.get("class_id") == 0),
            4,
            "preview-long",
        )
    )
    selected.extend(
        stable_rows(
            (row for row in val if row.get("sample_kind") == "positive_weak" and row.get("class_id") == 1),
            4,
            "preview-short",
        )
    )
    selected.extend(
        stable_rows(
            (row for row in val if row.get("sample_kind") == "negative_easy"),
            8,
            "preview-background",
        )
    )
    if len({row["sample_id"] for row in selected}) != 16:
        raise PreviewError("preview selection contains duplicate identities")
    return selected


def yolo_xywhn_to_xyxy(values: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
    """Convert normalized YOLO center geometry to clipping-safe pixels."""

    if len(values) != 4:
        raise PreviewError("YOLO geometry must have four values")
    cx, cy, box_w, box_h = (float(value) for value in values)
    x0 = int(round((cx - box_w / 2.0) * width))
    x1 = int(round((cx + box_w / 2.0) * width))
    y0 = int(round((cy - box_h / 2.0) * height))
    y1 = int(round((cy + box_h / 2.0) * height))
    return (
        int(np.clip(x0, 0, width - 1)),
        int(np.clip(y0, 0, height - 1)),
        int(np.clip(x1, 1, width)),
        int(np.clip(y1, 1, height)),
    )


def _put_label(image: np.ndarray, text: str, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
    x, y = origin
    (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    cv2.rectangle(image, (x, y - text_h - 8), (x + text_w + 8, y + 2), (250, 250, 250), -1)
    cv2.putText(image, text, (x + 4, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)


def _contact_sheet(images: Sequence[np.ndarray], columns: int = 4) -> np.ndarray:
    thumbs: list[np.ndarray] = []
    for image in images:
        target_width = 420
        scale = target_width / image.shape[1]
        thumbs.append(
            cv2.resize(
                image,
                (target_width, int(round(image.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        )
    cell_height = max(image.shape[0] for image in thumbs)
    canvas = np.full(
        (math.ceil(len(thumbs) / columns) * cell_height, columns * 420, 3),
        245,
        dtype=np.uint8,
    )
    for index, image in enumerate(thumbs):
        row, column = divmod(index, columns)
        y, x = row * cell_height, column * 420
        canvas[y : y + image.shape[0], x : x + image.shape[1]] = image
    return canvas


def render(
    *,
    run: Path,
    dataset: Path,
    out: Path,
    receipt: Path,
    device: str | None,
    conf: float,
) -> dict[str, Any]:
    """Predict the deterministic preview set and write image/hash evidence."""

    for target in (out, receipt):
        if target.exists():
            raise FileExistsError(f"refusing to overwrite preview artifact: {target}")
    manifest_path = dataset / "manifest.jsonl"
    weight_path = run / "weights" / "best.pt"
    if not manifest_path.is_file() or not weight_path.is_file():
        raise FileNotFoundError("frozen manifest or fetched best.pt is missing")
    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = select_preview_rows(rows)
    image_paths = [dataset / str(row["image_path"]) for row in selected]
    for row, path in zip(selected, image_paths):
        if sha256_file(path) != row["image_sha256"]:
            raise PreviewError(f"image hash drifted: {row['sample_id']}")

    from ultralytics import YOLO

    model = YOLO(str(weight_path))
    if {int(key): str(value) for key, value in model.names.items()} != {
        0: "dense_long",
        1: "dense_short",
    }:
        raise PreviewError("weight class names drifted")
    predictions = model.predict(
        source=[str(path) for path in image_paths],
        imgsz=960,
        conf=float(conf),
        iou=0.7,
        device=device,
        batch=8,
        verbose=False,
    )
    if len(predictions) != len(selected):
        raise PreviewError("prediction count differs from preview rows")

    rendered: list[np.ndarray] = []
    sample_receipts: list[dict[str, Any]] = []
    pred_colors = {0: (40, 170, 40), 1: (40, 40, 220)}
    for row, path, prediction in zip(selected, image_paths, predictions):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise PreviewError(f"failed to decode preview image: {path}")
        height, width = image.shape[:2]
        label_text = (dataset / str(row["label_path"])).read_text(encoding="utf-8").strip()
        if label_text:
            fields = label_text.split()
            gt_class = int(fields[0])
            x0, y0, x1, y1 = yolo_xywhn_to_xyxy(
                [float(value) for value in fields[1:]], width, height
            )
            cv2.rectangle(image, (x0, y0), (x1, y1), (0, 180, 255), 3, cv2.LINE_AA)
            _put_label(image, f"GT {model.names[gt_class]}", (x0, max(y0, 22)), (0, 110, 180))
        boxes = prediction.boxes
        prediction_rows: list[dict[str, Any]] = []
        if boxes is not None:
            for xyxy, class_id, confidence in zip(
                boxes.xyxy.cpu().numpy(),
                boxes.cls.cpu().numpy(),
                boxes.conf.cpu().numpy(),
            ):
                cid = int(class_id)
                px0, py0, px1, py1 = (int(round(value)) for value in xyxy)
                cv2.rectangle(image, (px0, py0), (px1, py1), pred_colors[cid], 2, cv2.LINE_AA)
                _put_label(
                    image,
                    f"P {model.names[cid]} {float(confidence):.2f}",
                    (px0, max(py0, 44)),
                    pred_colors[cid],
                )
                prediction_rows.append(
                    {
                        "class_id": cid,
                        "class_name": str(model.names[cid]),
                        "confidence": float(confidence),
                        "xyxy": [px0, py0, px1, py1],
                    }
                )
        title = f"{row['sample_kind']} | {row['symbol']} | predictions={len(prediction_rows)}"
        _put_label(image, title, (8, 22), (30, 30, 30))
        rendered.append(image)
        sample_receipts.append(
            {
                "sample_id": row["sample_id"],
                "symbol": row["symbol"],
                "sample_kind": row["sample_kind"],
                "class_name": row.get("class_name"),
                "image_sha256": row["image_sha256"],
                "predictions": prediction_rows,
            }
        )

    sheet = _contact_sheet(rendered)
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(out), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise OSError(f"OpenCV failed to write {out}")
    payload = {
        "experiment_id": "exp-15m-ma-launch-t3-yolo10000-v1",
        "selection": {"dense_long": 4, "dense_short": 4, "negative_easy": 8},
        "confidence_threshold": float(conf),
        "device": device,
        "weight_sha256": sha256_file(weight_path),
        "manifest_sha256": sha256_file(manifest_path),
        "preview_path": str(out),
        "preview_sha256": sha256_file(out),
        "preview_size_bytes": out.stat().st_size,
        "samples": sample_receipts,
        "holdout_consumed": False,
        "production_eligible": False,
    }
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS / "validation_preview.png")
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RESULTS / "validation_preview_receipt.json")
    parser.add_argument("--device", default=None)
    parser.add_argument("--conf", type=float, default=0.25)
    args = parser.parse_args()
    payload = render(
        run=args.run.resolve(),
        dataset=args.dataset.resolve(),
        out=args.out.resolve(),
        receipt=args.receipt.resolve(),
        device=args.device,
        conf=args.conf,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
