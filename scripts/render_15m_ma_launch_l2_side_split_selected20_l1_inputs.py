#!/usr/bin/env python3
"""Export the exact 18/19-bar L1 images behind the 20 L2 selections.

Raw PNGs reproduce the unannotated BGR pixels passed to YOLO.  Detected PNGs
are review copies of those pixels with the preserved YOLO rectangle added;
the rectangle is model output and was not present in the inference input.
All windows end at the frozen feature bar and contain no future bars.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts.render_15m_ma_launch_l2_side_split_selected20 import (
    ROOT,
    EXPECTED_SCORED_SHA256,
    Selected20RenderError,
    load_sources,
    repo_relative,
    write_json,
)
from scripts.research_15m_ma_launch_l2_global_context import (
    CLASS_COLORS,
    normalized_box_corners,
    pixel_sha256,
    sha256_file,
    utc,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


OUTPUT = ROOT / "analysis/output/ma_launch_l2_side_split_v1/selected20_l1_inputs"
CONTACT_COLUMNS = 2
TILE_WIDTH = 640
TILE_HEIGHT = 371
CAPTION_HEIGHT = 42
HEADER_HEIGHT = 96


def render_exact_l1_views(
    row: Mapping[str, Any], frame: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray]:
    """Return exact raw L1 pixels and a separate detection-overlay copy."""

    start_i = int(row["window_start_i"])
    end_i = int(row["feature_bar_i"])
    if end_i != int(row["window_end_i"]):
        raise Selected20RenderError("feature bar and L1 window end drifted")
    enriched = add_mas(frame)
    window = enriched.iloc[start_i : end_i + 1]
    expected_bars = int(row["window_len"])
    if len(window) != expected_bars or expected_bars not in (18, 19):
        raise Selected20RenderError(
            f"unexpected L1 window length: rendered={len(window)} declared={expected_bars}"
        )
    raw, transform = render_chart(window, out_path=None)
    actual_pixel_sha = pixel_sha256(raw)
    if actual_pixel_sha != str(row["input_pixel_sha256"]):
        raise Selected20RenderError(
            f"L1 input pixel parity failed for {row['episode_id']}: {actual_pixel_sha}"
        )
    detected = raw.copy()
    x0, y0, x1, y1 = normalized_box_corners(row, transform.width, transform.height)
    cv2.rectangle(
        detected,
        (x0, y0),
        (x1, y1),
        CLASS_COLORS[int(row["class_id"])],
        5,
        cv2.LINE_AA,
    )
    return raw, detected


def contact_sheet(
    images: Sequence[np.ndarray], records: Sequence[Mapping[str, Any]], *, side: str
) -> np.ndarray:
    """Build a labelled sheet without presenting overlays as model inputs."""

    if not images or len(images) != len(records):
        raise Selected20RenderError(f"invalid contact inputs for {side}")
    rows = math.ceil(len(images) / CONTACT_COLUMNS)
    row_height = CAPTION_HEIGHT + TILE_HEIGHT
    canvas = np.full(
        (HEADER_HEIGHT + rows * row_height, CONTACT_COLUMNS * TILE_WIDTH, 3),
        255,
        dtype=np.uint8,
    )
    title = (
        f"ACTUAL YOLO WINDOWS | {side.upper()} | 18/19 BARS | "
        "BOX = OUTPUT OVERLAY (NOT INPUT)"
    )
    cv2.putText(
        canvas,
        title,
        (18, 57),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (20, 20, 20),
        2,
        cv2.LINE_AA,
    )
    for index, (image, record) in enumerate(zip(images, records)):
        row_i, column = divmod(index, CONTACT_COLUMNS)
        x0 = column * TILE_WIDTH
        y0 = HEADER_HEIGHT + row_i * row_height
        caption = (
            f"{index + 1:02d} {record['symbol']} | {int(record['window_len'])}K | "
            f"conf={float(record['l1_confidence']):.3f}"
        )
        cv2.putText(
            canvas,
            caption,
            (x0 + 12, y0 + 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.54,
            (35, 35, 35),
            1,
            cv2.LINE_AA,
        )
        thumb = cv2.resize(image, (TILE_WIDTH, TILE_HEIGHT), interpolation=cv2.INTER_AREA)
        canvas[y0 + CAPTION_HEIGHT : y0 + row_height, x0 : x0 + TILE_WIDTH] = thumb
    return canvas


def render_selected_l1(output: Path = OUTPUT) -> dict[str, Any]:
    """Write exact raw inputs, review overlays, contact sheets, and lineage."""

    if output.exists():
        raise FileExistsError(f"refusing to replace existing L1 gallery: {output}")
    building = output.with_name(output.name + ".building")
    if building.exists():
        raise FileExistsError(f"stale building directory requires inspection: {building}")
    building.mkdir(parents=True)
    selected, frames = load_sources()
    manifest_rows: list[dict[str, Any]] = []
    side_images: dict[str, list[np.ndarray]] = {"long": [], "short": []}
    side_records: dict[str, list[dict[str, Any]]] = {"long": [], "short": []}
    side_orders: Counter[str] = Counter()
    try:
        for row in selected.to_dict("records"):
            side = str(row["side"])
            side_orders[side] += 1
            symbol_frame = frames[str(row["symbol"])]
            raw, detected = render_exact_l1_views(row, symbol_frame)
            filename = (
                f"{side_orders[side]:02d}_{side.upper()}_{row['symbol']}_"
                f"{utc(row['available_at']):%Y%m%dT%H%M}_{int(row['window_len'])}K.png"
            )
            raw_building = building / "raw" / side / filename
            detected_building = building / "detected" / side / filename
            raw_building.parent.mkdir(parents=True, exist_ok=True)
            detected_building.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(raw_building), raw, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise Selected20RenderError(f"could not write {raw_building}")
            if not cv2.imwrite(
                str(detected_building), detected, [cv2.IMWRITE_PNG_COMPRESSION, 3]
            ):
                raise Selected20RenderError(f"could not write {detected_building}")
            record = {
                "side_order": int(side_orders[side]),
                "episode_id": str(row["episode_id"]),
                "symbol": str(row["symbol"]),
                "side": side,
                "available_at": str(row["available_at"]),
                "window_len": int(row["window_len"]),
                "window_start_time": str(
                    symbol_frame.iloc[int(row["window_start_i"])]["open_time"]
                ),
                "window_end_time": str(
                    symbol_frame.iloc[int(row["feature_bar_i"])]["open_time"]
                ),
                "l1_confidence": float(row["l1_confidence"]),
                "raw_path": repo_relative(output / "raw" / side / filename),
                "raw_png_sha256": sha256_file(raw_building),
                "raw_pixel_sha256": pixel_sha256(raw),
                "declared_input_pixel_sha256": str(row["input_pixel_sha256"]),
                "detected_path": repo_relative(output / "detected" / side / filename),
                "detected_png_sha256": sha256_file(detected_building),
                "detected_pixel_sha256": pixel_sha256(detected),
                "box_is_model_output_not_input": True,
            }
            manifest_rows.append(record)
            side_images[side].append(detected)
            side_records[side].append(record)

        contacts: dict[str, dict[str, Any]] = {}
        for side in ("long", "short"):
            sheet = contact_sheet(side_images[side], side_records[side], side=side)
            name = f"contact_actual_l1_{side}_{len(side_images[side]):02d}.png"
            path = building / name
            if not cv2.imwrite(str(path), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise Selected20RenderError(f"could not write {path}")
            contacts[side] = {
                "path": repo_relative(output / name),
                "png_sha256": sha256_file(path),
                "pixel_sha256": pixel_sha256(sheet),
            }
        manifest_path = building / "manifest.csv"
        pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
        receipt = {
            "protocol": "15m_l2_selected20_exact_l1_inputs_v1",
            "source_scored_sha256": EXPECTED_SCORED_SHA256,
            "events": len(manifest_rows),
            "side_counts": dict(Counter(row["side"] for row in manifest_rows)),
            "window_len_counts": dict(
                sorted(Counter(str(row["window_len"]) for row in manifest_rows).items())
            ),
            "raw_dimensions": [1280, 742],
            "raw_images_are_exact_yolo_pixels": True,
            "detected_boxes_are_separate_review_overlays": True,
            "future_bars_rendered": 0,
            "manifest_sha256": sha256_file(manifest_path),
            "contacts": contacts,
            "holdout_rows_read": 0,
            "training_or_tuning": False,
            "promoted_or_deployed": False,
        }
        write_json(building / "receipt.json", receipt)
        building.replace(output)
        return receipt
    except Exception:
        raise


def verify_selected_l1(output: Path = OUTPUT) -> dict[str, Any]:
    """Re-render all exact inputs and overlays and compare every pixel hash."""

    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    manifest_path = output / "manifest.csv"
    if sha256_file(manifest_path) != receipt["manifest_sha256"]:
        raise Selected20RenderError("L1 manifest SHA drifted")
    manifest = pd.read_csv(manifest_path)
    selected, frames = load_sources()
    source_by_id = {str(row["episode_id"]): row for row in selected.to_dict("records")}
    failures: list[str] = []
    for record in manifest.to_dict("records"):
        episode_id = str(record["episode_id"])
        source = source_by_id[episode_id]
        raw, detected = render_exact_l1_views(source, frames[str(source["symbol"])])
        raw_path, detected_path = ROOT / record["raw_path"], ROOT / record["detected_path"]
        if pixel_sha256(raw) != str(record["raw_pixel_sha256"]):
            failures.append(f"raw-pixels:{episode_id}")
        if sha256_file(raw_path) != str(record["raw_png_sha256"]):
            failures.append(f"raw-png:{episode_id}")
        if pixel_sha256(detected) != str(record["detected_pixel_sha256"]):
            failures.append(f"detected-pixels:{episode_id}")
        if sha256_file(detected_path) != str(record["detected_png_sha256"]):
            failures.append(f"detected-png:{episode_id}")
    result = {
        "passed": not failures,
        "events_checked": len(manifest),
        "window_len_counts": dict(sorted(Counter(manifest["window_len"]).items())),
        "failures": failures,
        "future_bars_rendered": 0,
        "holdout_rows_read": 0,
    }
    if failures:
        raise Selected20RenderError(f"L1 gallery verification failed: {failures[:10]}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--render", action="store_true")
    actions.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = render_selected_l1(args.output) if args.render else verify_selected_l1(args.output)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
