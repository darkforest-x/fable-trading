#!/usr/bin/env python3
"""Render a deterministic visual-QA sheet from the actual Grade-A inputs.

The script reads clean model PNGs plus their manifest boxes, draws red boxes
only on in-memory preview copies, and writes a contact sheet and receipt outside
the dataset. It never modifies model inputs or YOLO labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_v1"
DEFAULT_OUTPUT = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-owner-grade-a8000-v1"
    / "results"
    / "qa"
)


class GradeAVisualQAError(RuntimeError):
    """Raised when the dataset or preview geometry is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def select_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Select two chronological quantiles for every variant/split/direction."""

    selected: list[dict[str, Any]] = []
    for variant in range(1, 9):
        variant_rows: list[dict[str, Any]] = []
        for split in ("train", "val"):
            for direction in ("LONG", "SHORT"):
                pool = sorted(
                    (
                        dict(row)
                        for row in rows
                        if int(row["variant_index"]) == variant
                        and str(row["split"]) == split
                        and str(row["direction"]) == direction
                    ),
                    key=lambda row: (str(row["core_end_time"]), str(row["sample_id"])),
                )
                if len(pool) < 2:
                    raise GradeAVisualQAError(
                        f"visual stratum is too small: v{variant} {split} {direction}"
                    )
                for quantile in (0.25, 0.75):
                    index = int(round((len(pool) - 1) * quantile))
                    variant_rows.append(pool[index])
        if len(variant_rows) != 8:
            raise AssertionError("each variant must contribute eight preview rows")
        selected.extend(variant_rows)
    return selected


def draw_preview(image: np.ndarray, row: Mapping[str, Any]) -> np.ndarray:
    output = image.copy()
    height, width = output.shape[:2]
    box = row["box"]
    x0 = int(round((float(box["cx_norm"]) - float(box["w_norm"]) / 2.0) * width))
    x1 = int(round((float(box["cx_norm"]) + float(box["w_norm"]) / 2.0) * width))
    y0 = int(round((float(box["cy_norm"]) - float(box["h_norm"]) / 2.0) * height))
    y1 = int(round((float(box["cy_norm"]) + float(box["h_norm"]) / 2.0) * height))
    cv2.rectangle(output, (x0, y0), (x1, y1), (0, 0, 255), 5, cv2.LINE_AA)
    return output


def build_sheet(rows: Sequence[Mapping[str, Any]], dataset: Path) -> np.ndarray:
    tile_width, chart_height, caption_height = 400, 232, 32
    columns = 8
    rows_per_sheet = math.ceil(len(rows) / columns)
    canvas = np.full(
        (rows_per_sheet * (chart_height + caption_height), columns * tile_width, 3),
        244,
        dtype=np.uint8,
    )
    for slot, row in enumerate(rows):
        image = cv2.imread(str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (742, 1280):
            raise GradeAVisualQAError(f"unreadable model input: {row['image_path']}")
        preview = cv2.resize(
            draw_preview(image, row),
            (tile_width, chart_height),
            interpolation=cv2.INTER_AREA,
        )
        grid_y, grid_x = divmod(slot, columns)
        x0 = grid_x * tile_width
        y0 = grid_y * (chart_height + caption_height)
        canvas[y0 + caption_height : y0 + caption_height + chart_height, x0 : x0 + tile_width] = preview
        caption = (
            f"v{int(row['variant_index'])} {row['split']} {row['direction']} "
            f"{str(row['symbol']).replace('_USDT_SWAP', '')[:13]}"
        )
        cv2.putText(
            canvas,
            caption,
            (x0 + 5, y0 + 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (25, 32, 38),
            1,
            cv2.LINE_AA,
        )
    return canvas


def write_full_resolution_overlays(
    rows: Sequence[Mapping[str, Any]],
    dataset: Path,
    output: Path,
    *,
    directory_name: str = "full_resolution_overlays",
) -> list[str]:
    overlay_dir = output / directory_name
    overlay_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for order, row in enumerate(rows, 1):
        image = cv2.imread(str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (742, 1280):
            raise GradeAVisualQAError(f"unreadable model input: {row['image_path']}")
        path = overlay_dir / (
            f"{order:02d}_v{int(row['variant_index'])}_{row['split']}_"
            f"{row['direction']}_{row['dataset_sample_id']}.jpg"
        )
        if not cv2.imwrite(
            str(path), draw_preview(image, row), [cv2.IMWRITE_JPEG_QUALITY, 98]
        ):
            raise GradeAVisualQAError(f"could not write {path}")
        paths.append(str(path.relative_to(ROOT)))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    rows = read_jsonl(dataset / "manifest.jsonl")
    if len(rows) != 8_000:
        raise GradeAVisualQAError(f"expected 8000 rows, found {len(rows)}")
    selected = select_rows(rows)
    sheet = build_sheet(selected, dataset)
    output.mkdir(parents=True, exist_ok=True)
    sheet_path = output / "stratified_64_box_overlay.jpg"
    if not cv2.imwrite(
        str(sheet_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 96]
    ):
        raise GradeAVisualQAError(f"could not write {sheet_path}")
    overlay_paths = write_full_resolution_overlays(selected, dataset, output)
    boundary: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for row in sorted(rows, key=lambda value: float(value["box"]["h_norm"]), reverse=True):
        event_id = str(row["sample_id"])
        if event_id in seen_events:
            continue
        boundary.append(row)
        seen_events.add(event_id)
        if len(boundary) == 16:
            break
    boundary_sheet_path = output / "largest_box_height_16.jpg"
    if not cv2.imwrite(
        str(boundary_sheet_path),
        build_sheet(boundary, dataset),
        [cv2.IMWRITE_JPEG_QUALITY, 96],
    ):
        raise GradeAVisualQAError(f"could not write {boundary_sheet_path}")
    boundary_overlay_paths = write_full_resolution_overlays(
        boundary,
        dataset,
        output,
        directory_name="largest_box_height_overlays",
    )
    receipt = {
        "schema_version": 1,
        "dataset_manifest_sha256": sha256_file(dataset / "manifest.jsonl"),
        "selection": "two chronological quantiles per variant x split x direction",
        "selected_images": len(selected),
        "selected_dataset_sample_ids": [str(row["dataset_sample_id"]) for row in selected],
        "contact_sheet_path": str(sheet_path.relative_to(ROOT)),
        "contact_sheet_sha256": sha256_file(sheet_path),
        "full_resolution_overlay_paths": overlay_paths,
        "largest_box_height_contact_sheet_path": str(
            boundary_sheet_path.relative_to(ROOT)
        ),
        "largest_box_height_contact_sheet_sha256": sha256_file(boundary_sheet_path),
        "largest_box_height_overlay_paths": boundary_overlay_paths,
        "overlay_is_preview_only": True,
        "training_images_changed": False,
        "labels_changed": False,
    }
    receipt_path = output / "visual_qa_receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
