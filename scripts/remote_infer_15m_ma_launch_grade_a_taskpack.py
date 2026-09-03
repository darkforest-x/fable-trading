#!/usr/bin/env python3
"""Run one immutable Grade-A daily-mover task pack on a CUDA worker.

The Mac coordinator has already selected complete UTC mover boards, computed
all causal indicators, and frozen the exact W18 endpoint specifications.  This
worker only reconstructs those 18-bar chart pixels, runs the pinned YOLO
checkpoint, and emits normalized raw boxes plus per-input pixel hashes.  It
does not fetch data, build rankings, apply labels, train, promote, deploy, or
write any trading state.

Input columns are ``open/high/low/close`` and causal SMA/EMA 20/60/120.  The
runtime source root must contain the repository's pinned
``yoyo.layers.l1_detection.render`` implementation; duplicating that renderer
inside this worker would create a second pixel contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np
import pandas as pd


FRAME_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "sma20",
    "sma60",
    "sma120",
    "ema20",
    "ema60",
    "ema120",
)
EXPECTED_CLASSES = {0: "dense_long", 1: "dense_short"}


class TaskPackError(RuntimeError):
    """Fail closed on task-pack, runtime, model, or output drift."""


def sha256_file(path: Path) -> str:
    """Return one streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    """Hash decoded BGR pixels instead of a PNG container."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def stable_json(value: Mapping[str, Any]) -> str:
    """Serialize one deterministic JSON object."""

    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a bounded task manifest."""

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Atomically publish deterministic JSON Lines."""

    temporary = path.with_suffix(path.suffix + ".building")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(stable_json(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically publish one readable receipt."""

    temporary = path.with_suffix(path.suffix + ".building")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_pack(
    pack_dir: Path,
    *,
    expected_pack_sha256: str,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]], dict[str, Any]]:
    """Load and validate one immutable coordinator task pack."""

    receipt_path = pack_dir / "pack_receipt.json"
    frames_path = pack_dir / "frames.npz"
    tasks_path = pack_dir / "tasks.jsonl"
    if not receipt_path.is_file() or not frames_path.is_file() or not tasks_path.is_file():
        raise TaskPackError("task pack is incomplete")
    if sha256_file(receipt_path) != expected_pack_sha256:
        raise TaskPackError("pack receipt SHA drifted")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if sha256_file(frames_path) != str(receipt["frames_sha256"]):
        raise TaskPackError("frames.npz SHA drifted")
    if sha256_file(tasks_path) != str(receipt["tasks_sha256"]):
        raise TaskPackError("tasks.jsonl SHA drifted")
    if tuple(receipt["frame_columns"]) != FRAME_COLUMNS:
        raise TaskPackError("frame column contract drifted")

    tasks = read_jsonl(tasks_path)
    if len(tasks) != int(receipt["task_count"]):
        raise TaskPackError("task count differs from receipt")
    task_ids = [str(row["task_id"]) for row in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise TaskPackError("duplicate task IDs")
    if any(int(row["window_len"]) != 18 for row in tasks):
        raise TaskPackError("only preregistered W18 tasks are allowed")

    frame_index = dict(receipt["frame_index"])
    frames: dict[str, pd.DataFrame] = {}
    with np.load(frames_path, allow_pickle=False) as archive:
        if set(archive.files) != set(frame_index.values()):
            raise TaskPackError("frame archive members differ from receipt")
        for symbol, key in sorted(frame_index.items()):
            values = np.asarray(archive[str(key)], dtype=np.float64)
            if values.ndim != 2 or values.shape[1] != len(FRAME_COLUMNS):
                raise TaskPackError(f"invalid frame shape for {symbol}: {values.shape}")
            if not bool(np.isfinite(values).all()):
                raise TaskPackError(f"non-finite frame values for {symbol}")
            frames[str(symbol)] = pd.DataFrame(values, columns=FRAME_COLUMNS)
    if set(frames) != {str(row["symbol"]) for row in tasks}:
        raise TaskPackError("task symbols differ from packed frames")
    return frames, tasks, receipt


def run(
    *,
    pack_dir: Path,
    weights: Path,
    runtime_source: Path,
    out_dir: Path,
    expected_pack_sha256: str,
    expected_weights_sha256: str,
    expected_render_sha256: str,
    batch_size: int,
    render_workers: int,
) -> dict[str, Any]:
    """Render, infer, and publish one CUDA shard without changing model state."""

    if batch_size < 1 or render_workers < 1:
        raise TaskPackError("batch size and render workers must be positive")
    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite {out_dir}")
    out_dir.mkdir(parents=True)
    started = time.perf_counter()
    try:
        render_path = runtime_source / "yoyo/layers/l1_detection/render.py"
        if sha256_file(render_path) != expected_render_sha256:
            raise TaskPackError("remote renderer SHA drifted")
        if sha256_file(weights) != expected_weights_sha256:
            raise TaskPackError("remote weights SHA drifted")
        sys.path.insert(0, str(runtime_source))
        from ultralytics import YOLO
        import torch
        import ultralytics
        from yoyo.layers.l1_detection.render import render_chart

        if not torch.cuda.is_available():
            raise TaskPackError("CUDA is unavailable")
        frames, tasks, pack_receipt = load_pack(
            pack_dir,
            expected_pack_sha256=expected_pack_sha256,
        )
        model = YOLO(str(weights))
        names = {int(key): str(value) for key, value in model.names.items()}
        if names != EXPECTED_CLASSES:
            raise TaskPackError(f"class map drifted: {names}")

        output_rows: list[dict[str, Any]] = []
        windows_with_box = 0
        raw_boxes = 0

        def render_one(task: Mapping[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
            frame = frames[str(task["symbol"])]
            start_i = int(task["window_start_i"])
            end_i = int(task["window_end_i"])
            if end_i - start_i + 1 != 18:
                raise TaskPackError("task is not exactly W18")
            image, transform = render_chart(frame.iloc[start_i : end_i + 1], out_path=None)
            meta = {
                "task_id": str(task["task_id"]),
                "input_pixel_sha256": pixel_sha256(image),
                "transform": {
                    "n_bars": int(transform.n_bars),
                    "width": int(transform.width),
                    "height": int(transform.height),
                    "left": int(transform.left),
                    "top": int(transform.top),
                    "plot_w": int(transform.plot_w),
                    "plot_h": int(transform.plot_h),
                    "price_min": float(transform.price_min),
                    "price_max": float(transform.price_max),
                    "candle_half_w": int(transform.candle_half_w),
                },
            }
            return image, meta

        pool = ThreadPoolExecutor(max_workers=render_workers)
        try:
            for start in range(0, len(tasks), batch_size):
                batch_tasks = tasks[start : start + batch_size]
                rendered = list(pool.map(render_one, batch_tasks))
                images = [row[0] for row in rendered]
                metas = [row[1] for row in rendered]
                predictions = model.predict(
                    source=images,
                    imgsz=1280,
                    conf=0.25,
                    iou=0.70,
                    batch=len(images),
                    device="0",
                    verbose=False,
                )
                if len(predictions) != len(metas):
                    raise TaskPackError("prediction/task count mismatch")
                for prediction, meta in zip(predictions, metas):
                    boxes = prediction.boxes
                    if boxes is None or len(boxes) == 0:
                        continue
                    windows_with_box += 1
                    for xywhn, class_id, confidence in zip(
                        boxes.xywhn.cpu().numpy(),
                        boxes.cls.cpu().numpy(),
                        boxes.conf.cpu().numpy(),
                    ):
                        raw_boxes += 1
                        output_rows.append(
                            {
                                **meta,
                                "class_id": int(class_id),
                                "confidence": float(confidence),
                                "prediction_cx_norm": float(xywhn[0]),
                                "prediction_cy_norm": float(xywhn[1]),
                                "prediction_w_norm": float(xywhn[2]),
                                "prediction_h_norm": float(xywhn[3]),
                            }
                        )
                done = min(start + batch_size, len(tasks))
                if done % (batch_size * 20) == 0 or done == len(tasks):
                    print(
                        f"inference {done}/{len(tasks)} raw_boxes={raw_boxes}",
                        flush=True,
                    )
        finally:
            pool.shutdown(wait=True)

        boxes_path = out_dir / "raw_boxes.jsonl"
        write_jsonl(boxes_path, output_rows)
        receipt = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "month": str(pack_receipt["month"]),
            "pack_receipt_sha256": expected_pack_sha256,
            "weights_sha256": expected_weights_sha256,
            "render_sha256": expected_render_sha256,
            "tasks": len(tasks),
            "windows_with_box": windows_with_box,
            "raw_boxes": raw_boxes,
            "raw_boxes_path": "raw_boxes.jsonl",
            "raw_boxes_sha256": sha256_file(boxes_path),
            "batch_size": batch_size,
            "render_workers": render_workers,
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "torch": str(torch.__version__),
                "ultralytics": str(ultralytics.__version__),
                "numpy": str(np.__version__),
                "pandas": str(pd.__version__),
                "opencv": str(cv2.__version__),
                "cuda_device": str(torch.cuda.get_device_name(0)),
            },
            "wall_seconds": round(time.perf_counter() - started, 3),
            "network_market_reads": 0,
            "trained": False,
            "labels_changed": False,
            "promoted": False,
            "deployed": False,
            "trading_state_changed": False,
        }
        write_json(out_dir / "receipt.json", receipt)
        return receipt
    except Exception as exc:
        write_json(
            out_dir / "failure_receipt.json",
            {
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}:{exc}",
                "trained": False,
                "trading_state_changed": False,
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--runtime-source", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-pack-sha256", required=True)
    parser.add_argument("--expected-weights-sha256", required=True)
    parser.add_argument("--expected-render-sha256", required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--render-workers", type=int, default=6)
    args = parser.parse_args()
    run(
        pack_dir=args.pack_dir,
        weights=args.weights,
        runtime_source=args.runtime_source,
        out_dir=args.out_dir,
        expected_pack_sha256=args.expected_pack_sha256,
        expected_weights_sha256=args.expected_weights_sha256,
        expected_render_sha256=args.expected_render_sha256,
        batch_size=args.batch_size,
        render_workers=args.render_workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
