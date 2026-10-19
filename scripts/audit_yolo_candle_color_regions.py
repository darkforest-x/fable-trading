#!/usr/bin/env python3
"""Localize frozen YOLO reliance on candle polarity color by chart region.

Source columns/window: OHLC plus close-derived SMA/EMA 20/60/120 from each
selected manifest row's ``window_start_i:window_end_i`` inclusive.  Positive
core bounds come from ``source_core_start_i/source_core_end_i``; matched
negative pseudo-core bounds come from ``core_start_i/core_end_i``.  The script
loads no bar after ``window_end_i`` and fails closed before the project holdout.

Decision use: this is a read-only, fixed-weight OOD diagnostic that separates
pre-core, owner-core and post-core candle-color sensitivity.  It cannot estimate
performance after retraining on a changed palette and does not confer training
or production eligibility.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts.audit_yolo_color_semantics import (
    CLASS_NAMES,
    CANDLE_MONO,
    DEFAULT_DATASET,
    DEFAULT_WEIGHTS,
    ROOT,
    ColorAuditError,
    condition_summary,
    holm_adjust,
    paired_comparison,
    predict_condition,
    read_jsonl,
    select_audit_rows,
    sha256_file,
    write_jsonl,
    write_png,
)
from yoyo.datasets.window_render import enrich, load_prefix, window_slice
from yoyo.layers.l1_detection import render as render_module
from yoyo.layers.l1_detection.data import ALL_MA_COLS


DEFAULT_OUTPUT_DIR = ROOT / "analysis/output/p1_yolo_candle_color_region_audit_20260902"
DEFAULT_PREREGISTRATION = DEFAULT_OUTPUT_DIR / "preregistration.json"
DEFAULT_BASELINE_LEDGER = (
    ROOT
    / "analysis/output/p1_yolo_color_semantics_audit_20260902"
    / "predictions/baseline.jsonl"
)
DEFAULT_ALL_MONO_LEDGER = (
    ROOT
    / "analysis/output/p1_yolo_color_semantics_audit_20260902"
    / "predictions/candle_monochrome.jsonl"
)
EXPECTED_PREREGISTRATION_SHA256 = (
    "bd8d98f599e1340a9e8bc43fa4caae98b59c2cc64b06f30134531844455f1b07"
)
EXPECTED_BASELINE_LEDGER_SHA256 = (
    "26c85c846ba6e4d6b825bccb3033726f9019df0be9d4e7b5cb8b0ad425e9e35d"
)
EXPECTED_ALL_MONO_LEDGER_SHA256 = (
    "db053a4eb3717e0143bcc83148ce299715ce657a16cfe1fe4828ee74b3945bee"
)
SEED = 20260903
TREATMENTS = (
    "candle_monochrome_pre",
    "candle_monochrome_post",
    "candle_monochrome_core",
)


def core_bounds(row: Mapping[str, Any]) -> tuple[int, int]:
    """Return inclusive absolute core indices for a positive or matched negative."""
    if row["sample_kind"] == "positive":
        start = row.get("source_core_start_i")
        end = row.get("source_core_end_i")
    else:
        start = row.get("core_start_i")
        end = row.get("core_end_i")
    if start is None or end is None:
        raise ColorAuditError(f"missing core bounds: {row['dataset_sample_id']}")
    start_i, end_i = int(start), int(end)
    if end_i < start_i or end_i - start_i + 1 != int(row["core_bars"]):
        raise ColorAuditError(f"invalid core bounds: {row['dataset_sample_id']}")
    return start_i, end_i


def is_treated_bar(condition: str, absolute_i: int, core_start: int, core_end: int) -> bool:
    if condition == "baseline":
        return False
    if condition == "all_monochrome":
        return True
    if condition == "candle_monochrome_pre":
        return absolute_i < core_start
    if condition == "candle_monochrome_core":
        return core_start <= absolute_i <= core_end
    if condition == "candle_monochrome_post":
        return absolute_i > core_end
    raise ColorAuditError(f"unknown condition: {condition}")


def render_region_condition(
    window: pd.DataFrame,
    row: Mapping[str, Any],
    condition: str,
) -> tuple[np.ndarray, set[int]]:
    """Reproduce the frozen renderer, neutralizing candle bodies in one region."""
    frame = window.reset_index(drop=True)
    transform = render_module.make_chart_transform(frame)
    core_start, core_end = core_bounds(row)
    window_start = int(row["window_start_i"])
    if not (
        window_start <= core_start <= core_end <= int(row["window_end_i"])
        and len(frame) == int(row["window_end_i"]) - window_start + 1
    ):
        raise ColorAuditError(f"core/window mismatch: {row['dataset_sample_id']}")

    image = np.full(
        (render_module.IMG_HEIGHT, render_module.IMG_WIDTH, 3),
        render_module.BG,
        dtype=np.uint8,
    )
    treated_columns: set[int] = set()
    for local_i in range(len(frame)):
        source_i = window_start + local_i
        candle = frame.iloc[local_i]
        x = transform.x_at(local_i)
        y_high = transform.y_at(candle["high"])
        y_low = transform.y_at(candle["low"])
        y_open = transform.y_at(candle["open"])
        y_close = transform.y_at(candle["close"])
        original = (
            render_module.CANDLE_GREEN
            if float(candle["close"]) >= float(candle["open"])
            else render_module.CANDLE_RED
        )
        treated = is_treated_bar(condition, source_i, core_start, core_end)
        color = CANDLE_MONO if treated else original
        cv2.line(
            image,
            (x, y_high),
            (x, y_low),
            render_module.WICK,
            1,
            cv2.LINE_AA,
        )
        y1, y2 = min(y_open, y_close), max(y_open, y_close)
        if y2 - y1 < 2:
            y2 = y1 + 2
        cv2.rectangle(
            image,
            (x - transform.candle_half_w, y1),
            (x + transform.candle_half_w, y2),
            color,
            -1,
            cv2.LINE_AA,
        )
        if treated:
            treated_columns.update(
                range(
                    max(0, x - transform.candle_half_w - 1),
                    min(render_module.IMG_WIDTH, x + transform.candle_half_w + 2),
                )
            )

    for column in ALL_MA_COLS:
        points = [
            (transform.x_at(i), transform.y_at(float(value)))
            for i, value in enumerate(frame[column])
            if pd.notna(value)
        ]
        if len(points) >= 2:
            cv2.polylines(
                image,
                [np.asarray(points, dtype=np.int32)],
                False,
                render_module.MA_COLORS[column],
                1,
                cv2.LINE_AA,
            )
    return image, treated_columns


def render_treatments(
    rows: Sequence[dict[str, Any]],
    dataset: Path,
    temp_root: Path,
) -> tuple[dict[str, dict[str, Path]], dict[str, Any]]:
    by_source: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[str(row["source_path"])].append(row)
    paths: dict[str, dict[str, Path]] = {condition: {} for condition in TREATMENTS}
    parity_matches = 0
    changed: defaultdict[str, list[float]] = defaultdict(list)
    support_mismatches: defaultdict[str, int] = defaultdict(int)
    outside_region_changes: defaultdict[str, int] = defaultdict(int)

    for source_path, source_rows in sorted(by_source.items()):
        required_end = max(int(row["window_end_i"]) for row in source_rows)
        enriched = enrich(load_prefix(ROOT / source_path, required_end))
        for row in sorted(source_rows, key=lambda value: str(value["dataset_sample_id"])):
            sample_id = str(row["dataset_sample_id"])
            window = window_slice(
                enriched,
                int(row["window_start_i"]),
                int(row["window_end_i"]),
            )
            baseline, _ = render_region_condition(window, row, "baseline")
            original = cv2.imread(
                str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR
            )
            if original is None or not np.array_equal(original, baseline):
                raise ColorAuditError(f"decoded baseline mismatch: {sample_id}")
            parity_matches += 1
            baseline_support = np.any(
                baseline != np.asarray(render_module.BG, dtype=np.uint8), axis=2
            )
            for condition in TREATMENTS:
                image, allowed_columns = render_region_condition(window, row, condition)
                difference = np.any(image != baseline, axis=2)
                support = np.any(
                    image != np.asarray(render_module.BG, dtype=np.uint8), axis=2
                )
                support_mismatches[condition] += int(
                    np.count_nonzero(support != baseline_support)
                )
                changed[condition].append(float(difference.mean()))
                if np.any(difference):
                    changed_columns = set(np.flatnonzero(np.any(difference, axis=0)).tolist())
                    outside_region_changes[condition] += len(changed_columns - allowed_columns)
                output = temp_root / condition / f"{sample_id}.png"
                write_png(output, image)
                paths[condition][sample_id] = output

    if parity_matches != len(rows):
        raise ColorAuditError(f"baseline parity count drifted: {parity_matches}")
    for condition in TREATMENTS:
        if support_mismatches[condition] or outside_region_changes[condition]:
            raise ColorAuditError(
                f"pixel contract failed for {condition}: "
                f"support={support_mismatches[condition]}, "
                f"outside_columns={outside_region_changes[condition]}"
            )
    return paths, {
        "baseline_decoded_pixel_matches": parity_matches,
        "baseline_decoded_pixel_mismatches": len(rows) - parity_matches,
        "conditions": {
            condition: {
                "foreground_support_mismatch_pixels": support_mismatches[condition],
                "changed_columns_outside_treated_region": outside_region_changes[condition],
                "mean_changed_pixel_fraction": float(np.mean(changed[condition])),
                "median_changed_pixel_fraction": float(np.median(changed[condition])),
                "max_changed_pixel_fraction": float(np.max(changed[condition])),
            }
            for condition in TREATMENTS
        },
    }


def loss_overlap(
    baseline: Sequence[dict[str, Any]],
    treatments: Mapping[str, Sequence[dict[str, Any]]],
) -> dict[str, Any]:
    base = {
        str(row["dataset_sample_id"]): bool(row["true_hit"])
        for row in baseline
        if row["sample_kind"] == "positive"
    }
    lost: dict[str, set[str]] = {}
    for condition, rows in treatments.items():
        treated = {
            str(row["dataset_sample_id"]): bool(row["true_hit"])
            for row in rows
            if row["sample_kind"] == "positive"
        }
        lost[condition] = {
            sample_id
            for sample_id, baseline_hit in base.items()
            if baseline_hit and not treated[sample_id]
        }
    conditions = list(TREATMENTS)
    return {
        "baseline_hit_images": sum(base.values()),
        "lost_counts": {key: len(value) for key, value in lost.items()},
        "lost_by_all_three": len(set.intersection(*(lost[key] for key in conditions))),
        "lost_by_any_region": len(set.union(*(lost[key] for key in conditions))),
        "exclusive_loss_counts": {
            condition: len(
                lost[condition]
                - set.union(
                    *(lost[other] for other in conditions if other != condition)
                )
            )
            for condition in conditions
        },
        "pairwise_loss_intersections": {
            f"{left}__{right}": len(lost[left] & lost[right])
            for index, left in enumerate(conditions)
            for right in conditions[index + 1 :]
        },
    }


def build_contact_sheet(
    rows: Sequence[dict[str, Any]],
    dataset: Path,
    paths: Mapping[str, Mapping[str, Path]],
    output: Path,
) -> None:
    positives = [row for row in rows if row["sample_kind"] == "positive"]
    preview = [
        next(row for row in positives if row["direction"] == "LONG"),
        next(row for row in positives if row["direction"] == "SHORT"),
    ]
    conditions = ("baseline",) + TREATMENTS + ("all_monochrome",)
    cell_width = 384
    cell_height = int(round(render_module.IMG_HEIGHT * cell_width / render_module.IMG_WIDTH))
    header_height = 34
    output_rows = []
    for row in preview:
        cells = []
        source = enrich(load_prefix(ROOT / str(row["source_path"]), int(row["window_end_i"])))
        window = window_slice(source, int(row["window_start_i"]), int(row["window_end_i"]))
        for condition in conditions:
            if condition == "baseline":
                image = cv2.imread(str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR)
            elif condition == "all_monochrome":
                image, _ = render_region_condition(window, row, condition)
            else:
                image = cv2.imread(
                    str(paths[condition][str(row["dataset_sample_id"])]),
                    cv2.IMREAD_COLOR,
                )
            if image is None:
                raise ColorAuditError(f"contact-sheet image missing: {condition}")
            thumbnail = cv2.resize(
                image, (cell_width, cell_height), interpolation=cv2.INTER_AREA
            )
            header = np.full((header_height, cell_width, 3), 245, dtype=np.uint8)
            label = condition if condition != "baseline" else f"{row['direction']} | baseline"
            cv2.putText(
                header,
                label,
                (8, 23),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (30, 30, 30),
                1,
                cv2.LINE_AA,
            )
            cells.append(np.vstack([header, thumbnail]))
        output_rows.append(np.hstack(cells))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), np.vstack(output_rows)):
        raise ColorAuditError(f"failed to write contact sheet: {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--preregistration", type=Path, default=DEFAULT_PREREGISTRATION)
    parser.add_argument("--baseline-ledger", type=Path, default=DEFAULT_BASELINE_LEDGER)
    parser.add_argument("--all-mono-ledger", type=Path, default=DEFAULT_ALL_MONO_LEDGER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.7)
    parser.add_argument("--match-iou", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = args.dataset.resolve()
    weights = args.weights.resolve()
    preregistration = args.preregistration.resolve()
    baseline_ledger = args.baseline_ledger.resolve()
    all_mono_ledger = args.all_mono_ledger.resolve()
    output_dir = args.output_dir.resolve()
    results_path = output_dir / "results.json"
    predictions_dir = output_dir / "predictions"
    contact_sheet = output_dir / "candle_color_regions_contact_sheet.png"
    for path in (results_path, contact_sheet):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite audit artifact: {path}")
    if predictions_dir.exists():
        raise FileExistsError(f"refusing to overwrite predictions: {predictions_dir}")

    identities = {
        preregistration: EXPECTED_PREREGISTRATION_SHA256,
        baseline_ledger: EXPECTED_BASELINE_LEDGER_SHA256,
        all_mono_ledger: EXPECTED_ALL_MONO_LEDGER_SHA256,
    }
    for path, expected in identities.items():
        if not path.is_file() or sha256_file(path) != expected:
            raise ColorAuditError(f"input identity drifted: {path}")
    prereg = json.loads(preregistration.read_text(encoding="utf-8"))
    if tuple(prereg["run_order"]) != TREATMENTS or not prereg[
        "created_before_any_region_model_prediction"
    ]:
        raise ColorAuditError("preregistration contract drifted")
    rows = select_audit_rows(dataset)
    baseline = read_jsonl(baseline_ledger)
    all_monochrome = read_jsonl(all_mono_ledger)
    selected_ids = {str(row["dataset_sample_id"]) for row in rows}
    if {str(row["dataset_sample_id"]) for row in baseline} != selected_ids:
        raise ColorAuditError("baseline ledger identities drifted")

    import torch
    import ultralytics
    from ultralytics import YOLO

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    model = YOLO(str(weights))
    if {int(key): str(value) for key, value in model.names.items()} != CLASS_NAMES:
        raise ColorAuditError("model class map drifted")

    ledgers: dict[str, list[dict[str, Any]]] = {}
    with tempfile.TemporaryDirectory(prefix="fable-candle-region-audit-") as temp:
        rendered, pixel_audit = render_treatments(rows, dataset, Path(temp))
        for condition in TREATMENTS:
            ledgers[condition] = predict_condition(
                model,
                rows,
                rendered[condition],
                condition=condition,
                device=args.device,
                batch=args.batch,
                imgsz=args.imgsz,
                confidence=args.confidence,
                nms_iou=args.nms_iou,
                match_iou=args.match_iou,
            )
        build_contact_sheet(rows, dataset, rendered, contact_sheet)

    comparisons = {
        condition: {
            "positive": paired_comparison(
                baseline,
                ledgers[condition],
                sample_kind="positive",
                seed=SEED + 100 + index,
            ),
            "negative": paired_comparison(
                baseline,
                ledgers[condition],
                sample_kind="negative",
                seed=SEED + 200 + index,
            ),
        }
        for index, condition in enumerate(TREATMENTS)
    }
    for endpoint in ("positive", "negative"):
        adjusted = holm_adjust(
            {
                condition: float(
                    comparisons[condition][endpoint]["mcnemar_exact_two_sided_p"]
                )
                for condition in TREATMENTS
            }
        )
        for condition in TREATMENTS:
            comparisons[condition][endpoint][
                "holm_adjusted_p_across_three_regions"
            ] = adjusted[condition]

    output_dir.mkdir(parents=True, exist_ok=True)
    receipts: dict[str, Any] = {}
    for condition in TREATMENTS:
        path = predictions_dir / f"{condition}.jsonl"
        write_jsonl(path, ledgers[condition])
        receipts[condition] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    all_mono_reference = {
        "summary": condition_summary(all_monochrome),
        "comparison": {
            endpoint: paired_comparison(
                baseline,
                all_monochrome,
                sample_kind=endpoint,
                seed=SEED + 900 + index,
            )
            for index, endpoint in enumerate(("positive", "negative"))
        },
        "source_ledger_sha256": sha256_file(all_mono_ledger),
    }
    payload = {
        "schema_version": 1,
        "audit_id": prereg["audit_id"],
        "classification": prereg["classification"],
        "preregistration": {
            "path": str(preregistration.relative_to(ROOT)),
            "sha256": sha256_file(preregistration),
        },
        "generator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
            "shared_audit_dependency": "scripts/audit_yolo_color_semantics.py",
            "shared_audit_dependency_sha256": sha256_file(
                ROOT / "scripts/audit_yolo_color_semantics.py"
            ),
        },
        "dataset": {
            "path": str(dataset.relative_to(ROOT)),
            "positive_images": 155,
            "negative_images": 465,
            "audit_blocks": 155,
            "earliest_variant_post_bars": {"2": 149, "3": 6},
            "min_visible_start": "2025-12-02T22:00:00+00:00",
            "max_visible_end": "2026-05-03T18:45:00+00:00",
        },
        "frozen_inference": {
            "imgsz": args.imgsz,
            "confidence": args.confidence,
            "nms_iou": args.nms_iou,
            "match_iou": args.match_iou,
            "max_det": 300,
            "device": args.device,
            "batch": args.batch,
            "seed": SEED,
            "run_order": list(TREATMENTS),
        },
        "pixel_audit": pixel_audit,
        "baseline": condition_summary(baseline),
        "condition_summaries": {
            condition: condition_summary(ledgers[condition])
            for condition in TREATMENTS
        },
        "paired_comparisons": comparisons,
        "positive_loss_overlap": loss_overlap(baseline, ledgers),
        "all_monochrome_prior_audit_reference": all_mono_reference,
        "predictions": receipts,
        "contact_sheet": {
            "path": str(contact_sheet.relative_to(ROOT)),
            "sha256": sha256_file(contact_sheet),
            "size_bytes": contact_sheet.stat().st_size,
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "numpy": np.__version__,
            "opencv": cv2.__version__,
        },
        "interpretation": {
            "fixed_weight_ood_only": True,
            "retraining_effect_estimated": False,
            "holdout_consumed": False,
            "training_performed": False,
            "threshold_tuned": False,
            "dataset_mutated": False,
            "production_eligible": False,
        },
    }
    results_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "results": str(results_path),
                "summaries": payload["condition_summaries"],
                "paired_comparisons": comparisons,
                "positive_loss_overlap": payload["positive_loss_overlap"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
