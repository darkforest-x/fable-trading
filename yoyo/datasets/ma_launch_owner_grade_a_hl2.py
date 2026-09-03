"""Re-render the frozen Grade-A YOLO dataset with HL2-derived six MAs.

The source dataset has 8,000 positives and 24,000 nuisance-matched negatives.
This ablation preserves every sample, event, chronological split, candle,
canvas transform, class and horizontal core boundary.  The six plotted
SMA/EMA 20/60/120 values change from ``close`` to causal
``hl2=(high+low)/2``; the positive vertical box is re-derived with the same
full-wick-plus-six-MA rule because copying a close-derived box can exclude the
treatment lines.

Source columns are ``open_time/open/high/low/close/volume``.  Each image reads
the same frozen pre-holdout source prefix and the same 18/19-bar window as its
baseline manifest row.  No value at or after 2026-05-04 is materialized.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix
from yoyo.datasets.ma_launch_owner_recrop_review import (
    HOLDOUT_START,
    RED,
    ROOT,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    core_box,
    encode_png,
)
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l1_detection.data import MA_PERIODS
from yoyo.layers.l1_detection.render import render_chart

EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-v1"
BASELINE_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_v1"
DEFAULT_DATASET = ROOT / "datasets" / "ma_launch_owner_grade_a8000_yolo_neg24000_hl2_v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = DEFAULT_PREREG.parent / "results"
MODULE_PATH = Path(__file__).resolve()
SCRIPT_PATH = ROOT / "scripts" / "build_15m_ma_launch_owner_grade_a_hl2.py"
EXACT_OVERLAY_RED = np.asarray(RED, dtype=np.uint8)


class GradeAHL2Error(ValueError):
    """Raised when the paired rendering or immutable-input contract drifts."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object."""

    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per non-empty line."""

    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: object) -> None:
    """Write deterministic pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic compact JSONL."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _repo_path(value: object) -> Path:
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise GradeAHL2Error(f"path escapes repository: {value}") from exc
    return resolved


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _verify_committed(paths: Sequence[Path]) -> str:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise GradeAHL2Error("HL2 dataset builder must run on main")
    relative = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise GradeAHL2Error(f"builder inputs are not committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if len(commit) != 40:
        raise GradeAHL2Error("could not resolve builder commit")
    return commit


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    """Load and fail closed on the exact one-variable treatment contract."""

    prereg = read_json(path)
    if str(prereg.get("experiment_id")) != EXPERIMENT_ID:
        raise GradeAHL2Error("experiment ID drift")
    authorization = prereg["owner_authorization"]
    if authorization.get("dataset_materialization_authorized") is not True:
        raise GradeAHL2Error("dataset materialization is not authorized")
    treatment = prereg["single_variable"]
    expected = {
        "name": "moving_average_price_source",
        "baseline": "close",
        "treatment": "hl2",
        "formula": "(high + low) / 2",
        "line_width_px": 1,
        "canvas_transform": "baseline_close_transform",
        "labels": "recomputed_same_core_contract",
    }
    for key, value in expected.items():
        if treatment.get(key) != value:
            raise GradeAHL2Error(f"single-variable contract drift: {key}")
    if int(prereg["sources"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise GradeAHL2Error("holdout allowance must remain zero")
    safety = prereg["safety"]
    for key in (
        "holdout_read",
        "training_started",
        "training_eligible",
        "production_eligible",
        "active_or_frozen_change",
        "promote",
        "deployment",
        "forward_state_change",
        "order_state_change",
        "remote_write",
    ):
        if safety.get(key) is not False:
            raise GradeAHL2Error(f"safety switch must remain false: {key}")
    return prereg


def _verify_baseline(prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    source = prereg["baseline_dataset"]
    dataset = _repo_path(source["dataset_dir"])
    required = {
        "manifest.jsonl": source["manifest_sha256"],
        "build_summary.json": source["build_summary_sha256"],
        "data.yaml": source["data_yaml_sha256"],
    }
    for name, expected in required.items():
        path = dataset / name
        if not path.is_file() or sha256_file(path) != str(expected):
            raise GradeAHL2Error(f"baseline {name} SHA drift")
    rows = read_jsonl(dataset / "manifest.jsonl")
    if len(rows) != int(source["images"]):
        raise GradeAHL2Error("baseline image count drift")
    if len({str(row["dataset_sample_id"]) for row in rows}) != len(rows):
        raise GradeAHL2Error("baseline sample IDs are not unique")
    return rows


def with_hl2_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Replace only SMA/EMA 20/60/120 with causal HL2-derived values."""

    output = frame.copy()
    source = (output["high"] + output["low"]) / 2.0
    for period in MA_PERIODS:
        output[f"sma{period}"] = source.rolling(period).mean()
        output[f"ema{period}"] = source.ewm(span=period, adjust=False).mean()
    return output


def _validate_window(row: Mapping[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    start, end = int(row["window_start_i"]), int(row["window_end_i"])
    if not 0 <= start <= end < len(frame):
        raise GradeAHL2Error(f"window index outside source: {row['dataset_sample_id']}")
    window = frame.iloc[start : end + 1].reset_index(drop=True)
    if len(window) != int(row["window_bars"]) or len(window) not in {18, 19}:
        raise GradeAHL2Error(f"window length drift: {row['dataset_sample_id']}")
    times = pd.to_datetime(window["open_time"], utc=True)
    if bool((times.diff().dropna() != pd.Timedelta(minutes=15)).any()):
        raise GradeAHL2Error(f"non-contiguous source window: {row['dataset_sample_id']}")
    if times.iloc[0] != pd.Timestamp(row["window_start_time"]):
        raise GradeAHL2Error(f"window start time drift: {row['dataset_sample_id']}")
    if times.iloc[-1] != pd.Timestamp(row["window_end_time"]):
        raise GradeAHL2Error(f"window end time drift: {row['dataset_sample_id']}")
    if times.iloc[-1] >= HOLDOUT_START:
        raise GradeAHL2Error(f"window reaches holdout: {row['dataset_sample_id']}")
    return window


def _treatment_box(
    row: Mapping[str, Any], window: pd.DataFrame, transform: Any
) -> dict[str, Any] | None:
    if row["sample_kind"] != "positive":
        return None
    start = int(row["pre_bars"])
    end = start + int(row["core_bars"]) - 1
    box = core_box(transform, window, start_local=start, end_local=end)
    baseline = row["box"]
    for key in ("x0", "x1", "cx_norm", "w_norm"):
        if not math.isclose(
            float(box[key]), float(baseline[key]), rel_tol=0.0, abs_tol=1e-12
        ):
            raise GradeAHL2Error(
                f"HL2 treatment changed horizontal label geometry: {row['dataset_sample_id']}"
            )
    if box["contains_core_wicks_and_six_mas"] is not True:
        raise GradeAHL2Error(f"HL2 treatment box lost its core: {row['dataset_sample_id']}")
    return box


def _label_bytes(direction: str, box: Mapping[str, Any]) -> bytes:
    if direction not in {"LONG", "SHORT"}:
        raise GradeAHL2Error(f"unsupported direction: {direction}")
    class_id = 0 if direction == "LONG" else 1
    text = (
        f"{class_id} {float(box['cx_norm']):.10f} {float(box['cy_norm']):.10f} "
        f"{float(box['w_norm']):.10f} {float(box['h_norm']):.10f}\n"
    )
    return text.encode("utf-8")


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _render_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_dataset: Path,
    building: Path,
    partial_path: Path,
) -> list[dict[str, Any]]:
    prior = read_jsonl(partial_path) if partial_path.exists() else []
    by_id = {str(row["dataset_sample_id"]): row for row in prior}
    if len(by_id) != len(prior):
        raise GradeAHL2Error("partial manifest has duplicate sample IDs")
    for row in prior:
        image_path = building / str(row["image_path"])
        label_path = building / str(row["label_path"])
        if sha256_file(image_path) != str(row["image_sha256"]):
            raise GradeAHL2Error("partial image SHA drift")
        if sha256_file(label_path) != str(row["label_sha256"]):
            raise GradeAHL2Error("partial label SHA drift")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row["dataset_sample_id"]) not in by_id:
            grouped[str(row["source_path"])].append(row)
    mode = "a" if prior else "w"
    with partial_path.open(mode, encoding="utf-8") as handle:
        completed = len(prior)
        for source_number, (source_path, source_rows) in enumerate(
            sorted(grouped.items()), 1
        ):
            frame, audit = read_preholdout_prefix(
                _repo_path(source_path), end_exclusive=HOLDOUT_START
            )
            if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
                raise GradeAHL2Error("source loader materialized holdout rows")
            close_frame = add_six_mas(frame)
            hl2_frame = with_hl2_mas(close_frame)
            for source_row in source_rows:
                row = dict(source_row)
                sample_id = str(row["dataset_sample_id"])
                baseline_image_path = baseline_dataset / str(row["image_path"])
                baseline_label_path = baseline_dataset / str(row["label_path"])
                if sha256_file(baseline_image_path) != str(row["image_sha256"]):
                    raise GradeAHL2Error(f"baseline image drift: {sample_id}")
                if sha256_file(baseline_label_path) != str(row["label_sha256"]):
                    raise GradeAHL2Error(f"baseline label drift: {sample_id}")

                close_window = _validate_window(row, close_frame)
                hl2_window = _validate_window(row, hl2_frame)
                baseline_image, baseline_transform = render_chart(
                    close_window,
                    width=SOURCE_WIDTH,
                    height=SOURCE_HEIGHT,
                    out_path=None,
                )
                baseline_bytes = encode_png(baseline_image)
                baseline_replay_sha = hashlib.sha256(baseline_bytes).hexdigest()
                if baseline_replay_sha != str(row["image_sha256"]):
                    raise GradeAHL2Error(f"baseline pixel replay drift: {sample_id}")

                treatment_image, returned_transform = render_chart(
                    hl2_window,
                    width=SOURCE_WIDTH,
                    height=SOURCE_HEIGHT,
                    out_path=None,
                    fixed_transform=baseline_transform,
                )
                if returned_transform is not baseline_transform:
                    raise AssertionError("fixed transform identity was not preserved")
                treatment_box = _treatment_box(row, hl2_window, baseline_transform)
                if int(np.all(treatment_image == EXACT_OVERLAY_RED, axis=2).sum()) != 0:
                    raise GradeAHL2Error(f"treatment image contains overlay red: {sample_id}")

                changed_mask = np.any(treatment_image != baseline_image, axis=2)
                changed_pixels = int(changed_mask.sum())
                abs_channel_delta = int(
                    np.abs(
                        treatment_image.astype(np.int16)
                        - baseline_image.astype(np.int16)
                    ).sum()
                )
                image_bytes = encode_png(treatment_image)
                baseline_label_bytes = baseline_label_path.read_bytes()
                label_bytes = (
                    baseline_label_bytes
                    if treatment_box is None
                    else _label_bytes(str(row["direction"]), treatment_box)
                )
                image_target = building / str(row["image_path"])
                label_target = building / str(row["label_path"])
                _write_bytes_atomic(image_target, image_bytes)
                _write_bytes_atomic(label_target, label_bytes)

                output = {
                    **row,
                    "baseline_dataset": _relative(baseline_dataset),
                    "baseline_image_sha256": str(row["image_sha256"]),
                    "baseline_label_sha256": str(row["label_sha256"]),
                    "baseline_replay_sha256": baseline_replay_sha,
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "label_sha256": hashlib.sha256(label_bytes).hexdigest(),
                    "baseline_box": row.get("box"),
                    "box": treatment_box,
                    "moving_average_price_source": "hl2",
                    "moving_average_price_source_formula": "(high + low) / 2",
                    "canvas_transform_source": "baseline_close_transform",
                    "line_width_px": 1,
                    "label_contract": "same_core_full_wick_plus_six_ma_with_4pct_padding",
                    "label_byte_identical_to_baseline": (
                        label_bytes == baseline_label_bytes
                    ),
                    "label_y0_delta_px": (
                        0.0
                        if treatment_box is None
                        else float(treatment_box["y0"] - row["box"]["y0"])
                    ),
                    "label_y1_delta_px": (
                        0.0
                        if treatment_box is None
                        else float(treatment_box["y1"] - row["box"]["y1"])
                    ),
                    "changed_pixels_vs_baseline": changed_pixels,
                    "absolute_channel_delta_vs_baseline": abs_channel_delta,
                    "training_eligible": False,
                    "production_eligible": False,
                }
                handle.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                by_id[sample_id] = output
                completed += 1
                if completed % 100 == 0:
                    os.fsync(handle.fileno())
                    print(f"HL2 render {completed:05d}/{len(rows):05d}", flush=True)
            if source_number % 25 == 0 or source_number == len(grouped):
                print(
                    f"HL2 sources {source_number:03d}/{len(grouped):03d}",
                    flush=True,
                )
    output_rows = [by_id[str(row["dataset_sample_id"])] for row in rows]
    if len(output_rows) != len(rows):
        raise GradeAHL2Error("rendered row count drift")
    return output_rows


def _full_file_qa(
    dataset: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_dataset: Path,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    image_hashes: set[str] = set()
    unchanged_images = 0
    changed_positive_labels = 0
    negative_label_parity = 0
    for number, row in enumerate(rows, 1):
        image_path = dataset / str(row["image_path"])
        label_path = dataset / str(row["label_path"])
        image_sha = sha256_file(image_path)
        label_sha = sha256_file(label_path)
        if image_sha != str(row["image_sha256"]):
            raise GradeAHL2Error(f"final image SHA drift: {row['dataset_sample_id']}")
        if label_sha != str(row["label_sha256"]):
            raise GradeAHL2Error(f"final label SHA drift: {row['dataset_sample_id']}")
        baseline_label = baseline_dataset / str(row["label_path"])
        baseline_label_bytes = baseline_label.read_bytes()
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (SOURCE_HEIGHT, SOURCE_WIDTH):
            raise GradeAHL2Error(f"invalid PNG: {row['dataset_sample_id']}")
        if image_sha in image_hashes:
            raise GradeAHL2Error(f"duplicate treatment image SHA: {image_sha}")
        image_hashes.add(image_sha)
        unchanged_images += image_sha == str(row["baseline_image_sha256"])
        counts[f"{row['split']}/{row['sample_kind']}"] += 1
        text = label_path.read_text(encoding="utf-8").strip()
        if row["sample_kind"] == "negative":
            if text:
                raise GradeAHL2Error("negative label is not empty")
            if label_path.read_bytes() != baseline_label_bytes:
                raise GradeAHL2Error("negative label byte parity failed")
            negative_label_parity += 1
        if row["sample_kind"] == "positive":
            fields = text.split()
            if len(fields) != 5 or fields[0] not in {"0", "1"}:
                raise GradeAHL2Error("positive label is invalid")
            box = row["box"]
            expected = _label_bytes(str(row["direction"]), box)
            if label_path.read_bytes() != expected:
                raise GradeAHL2Error("positive label does not match treatment box")
            baseline_box = row["baseline_box"]
            for key in ("x0", "x1", "cx_norm", "w_norm"):
                if float(box[key]) != float(baseline_box[key]):
                    raise GradeAHL2Error("positive horizontal box geometry changed")
            changed_positive_labels += label_path.read_bytes() != baseline_label_bytes
        if number % 8000 == 0:
            print(f"HL2 file QA {number:05d}/{len(rows):05d}", flush=True)
    expected = {
        "train/positive": 6800,
        "train/negative": 20400,
        "val/positive": 1200,
        "val/negative": 3600,
    }
    if dict(counts) != expected:
        raise GradeAHL2Error(f"split composition drift: {dict(counts)}")
    if len({str(row["dataset_sample_id"]) for row in rows}) != len(rows):
        raise GradeAHL2Error("dataset sample IDs are not unique")
    return {
        "passed": True,
        "rows": len(rows),
        "images_decoded": len(rows),
        "unique_image_hashes": len(image_hashes),
        "unchanged_image_hashes_vs_baseline": int(unchanged_images),
        "negative_label_byte_parity": int(negative_label_parity),
        "positive_labels_changed": int(changed_positive_labels),
        "positive_labels_unchanged": 8000 - int(changed_positive_labels),
        "baseline_replay_pixel_parity": len(rows),
        "split_counts": dict(counts),
    }


def build(
    prereg_path: Path = DEFAULT_PREREG,
    *,
    results_dir: Path = DEFAULT_RESULTS,
    dataset_dir: Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    """Build the exact 32,000-image paired HL2 treatment dataset."""

    prereg_path = prereg_path.resolve()
    results_dir = results_dir.resolve()
    dataset_dir = dataset_dir.resolve()
    prereg = load_preregistration(prereg_path)
    builder_commit = _verify_committed((MODULE_PATH, SCRIPT_PATH, prereg_path))
    baseline_dataset = _repo_path(prereg["baseline_dataset"]["dataset_dir"])
    rows = _verify_baseline(prereg)
    if dataset_dir.exists():
        raise FileExistsError(f"refusing to overwrite dataset: {dataset_dir}")
    building = dataset_dir.with_name(dataset_dir.name + ".building")
    building.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val"):
        (building / "images" / split).mkdir(parents=True, exist_ok=True)
        (building / "labels" / split).mkdir(parents=True, exist_ok=True)
    partial = building / "manifest.partial.jsonl"
    output_rows = _render_rows(
        rows,
        baseline_dataset=baseline_dataset,
        building=building,
        partial_path=partial,
    )
    by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in output_rows:
        by_source[str(row["source_path"])].append(row)
    source_audits = [
        {
            "source_path": source_path,
            "images": len(source_rows),
            "first_rendered_window_time": min(
                str(row["window_start_time"]) for row in source_rows
            ),
            "last_rendered_window_time": max(
                str(row["window_end_time"]) for row in source_rows
            ),
            "holdout_ohlcv_rows_materialized": 0,
        }
        for source_path, source_rows in sorted(by_source.items())
    ]
    write_jsonl(building / "manifest.jsonl", output_rows)
    (building / "data.yaml").write_text(
        f"path: {dataset_dir}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_long\n"
        "  1: dense_short\n",
        encoding="utf-8",
    )
    partial.unlink()
    os.replace(building, dataset_dir)
    full_qa = _full_file_qa(
        dataset_dir,
        output_rows,
        baseline_dataset=baseline_dataset,
    )
    changed = np.asarray(
        [int(row["changed_pixels_vs_baseline"]) for row in output_rows], dtype=float
    )
    absolute = np.asarray(
        [int(row["absolute_channel_delta_vs_baseline"]) for row in output_rows],
        dtype=float,
    )
    positive_rows = [row for row in output_rows if row["sample_kind"] == "positive"]
    label_edge_delta = np.asarray(
        [
            max(abs(float(row["label_y0_delta_px"])), abs(float(row["label_y1_delta_px"])))
            for row in positive_rows
        ],
        dtype=float,
    )
    pixel_count = SOURCE_WIDTH * SOURCE_HEIGHT
    summary = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "builder_sha256": sha256_file(MODULE_PATH),
        "preregistration_sha256": sha256_file(prereg_path),
        "dataset_path": _relative(dataset_dir),
        "baseline_dataset": _relative(baseline_dataset),
        "baseline_manifest_sha256": prereg["baseline_dataset"]["manifest_sha256"],
        "manifest_sha256": sha256_file(dataset_dir / "manifest.jsonl"),
        "data_yaml_sha256": sha256_file(dataset_dir / "data.yaml"),
        "moving_average_price_source": "hl2",
        "moving_average_price_source_formula": "(high + low) / 2",
        "canvas_transform_source": "baseline_close_transform",
        "line_width_px": 1,
        "label_contract": "same_core_full_wick_plus_six_ma_with_4pct_padding",
        "counts": full_qa["split_counts"],
        "positive_images": 8000,
        "negative_images": 24000,
        "positive_events": 1043,
        "negative_events": 3129,
        "source_files": len({str(row["source_path"]) for row in rows}),
        "source_audit_rows_written": len(source_audits),
        "pixel_difference_vs_baseline": {
            "images_changed": int((changed > 0).sum()),
            "images_unchanged": int((changed == 0).sum()),
            "mean_changed_pixel_fraction": float(changed.mean() / pixel_count),
            "median_changed_pixel_fraction": float(np.median(changed) / pixel_count),
            "p95_changed_pixel_fraction": float(np.quantile(changed, 0.95) / pixel_count),
            "mean_absolute_channel_delta_per_image": float(absolute.mean()),
        },
        "positive_label_geometry_vs_baseline": {
            "labels_changed": int(
                sum(not bool(row["label_byte_identical_to_baseline"]) for row in positive_rows)
            ),
            "labels_unchanged": int(
                sum(bool(row["label_byte_identical_to_baseline"]) for row in positive_rows)
            ),
            "horizontal_geometry_changed": 0,
            "median_max_vertical_edge_delta_px": float(np.median(label_edge_delta)),
            "p95_max_vertical_edge_delta_px": float(np.quantile(label_edge_delta, 0.95)),
            "max_vertical_edge_delta_px": float(label_edge_delta.max()),
        },
        "full_qa": full_qa,
        "null_control": {
            "name": "close_source_exact_replay",
            "images_tested": len(rows),
            "exact_png_sha_matches": len(rows),
            "failures": 0,
        },
        "holdout_ohlcv_rows_materialized": 0,
        "training_started": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(dataset_dir / "build_summary.json", summary)
    results_dir.mkdir(parents=True, exist_ok=True)
    write_json(results_dir / "dataset_build_receipt.json", summary)
    write_jsonl(results_dir / "source_read_audit.jsonl", source_audits)
    return summary
