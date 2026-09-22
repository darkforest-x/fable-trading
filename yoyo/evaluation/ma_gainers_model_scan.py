"""Scan frozen OKX top-gainer OHLC inputs with the morphology Arm-A detector.

This is an offline, research-only raw-prediction recorder.  Each model input
ends at ``core_end + 5`` and is rendered with the same 1,200-bar HL2 support,
window geometry, and ``visible_range_v1`` scaling as Arm A training.  It does
not use a rule prefilter, future outcomes, training boxes, or a semantic
acceptance gate.  A model box is a raw observation, not a fresh signal: the
input includes five post-core confirmation bars.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.ma_profit_dataset import (
    SUPPORT_BARS,
    VISIBLE_RANGE_PRICE_SCALE,
    _window_asset,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXP = ROOT / "experiments/active/exp-ma-morphology-top20-20260922-v1"
DEFAULT_MODEL = (
    ROOT
    / "experiments/active/exp-ma-morphology-negatives-20260922-v3"
    / "evaluation_diagnosis/arm_A_best.pt"
)
MODEL_SHA256 = "17aee5679b73dfd9183339c580c2caac323fc2b6c0759da38827834d72f2742b"
EXPECTED_MODEL_NAMES = {0: "dense_launch_long", 1: "dense_launch_short"}
CANVAS_WIDTH, CANVAS_HEIGHT = 1280, 742
TENSOR_HEIGHT, TENSOR_WIDTH = 768, 1280
PLOT_LEFT, PLOT_WIDTH = 12, 1256
WINDOW_SPECS = ((18, 4), (19, 5))
POST_BARS, PRE_BARS = 5, 9
CONFIDENCE, NMS_IOU, BATCH_SIZE = 0.25, 0.5, 8
REQUIRED_COLUMNS = ("ts", "open_time", "open", "high", "low", "close", "volume")


class GainerScanError(RuntimeError):
    """Raised when frozen inputs cannot safely produce raw predictions."""


def sha256_file(path: Path) -> str:
    """Return a streaming file SHA without loading model weights into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value


def _repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise GainerScanError(f"input path must be repo-relative: {value!r}")
    return ROOT / path


def _timestamp_from_ms(value: object, *, field: str) -> pd.Timestamp:
    try:
        return pd.to_datetime(int(value), unit="ms", utc=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GainerScanError(f"{field} must be an epoch millisecond timestamp") from exc


def _as_utc(value: object, *, field: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise GainerScanError(f"invalid {field}: {value!r}") from exc
    if stamp.tzinfo is None:
        raise GainerScanError(f"{field} must include timezone: {value!r}")
    return stamp.tz_convert("UTC")


def load_native_ohlc(path: Path, *, minutes: int, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Load only bars whose closes are frozen at or before ``cutoff``.

    The caller cannot accidentally render a later CSV row: the returned frame
    is cut at the frozen close before endpoint selection or MA calculation.
    """

    if minutes not in (15, 30, 60):
        raise GainerScanError(f"unsupported timeframe minutes: {minutes}")
    frame = pd.read_csv(path)
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise GainerScanError(f"{path}: missing columns {missing}")
    frame = frame.loc[:, list(REQUIRED_COLUMNS)].copy()
    frame["ts"] = pd.to_numeric(frame["ts"], errors="raise").astype("int64")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    expected = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    if not bool((frame["open_time"] == expected).all()):
        raise GainerScanError(f"{path}: ts/open_time mismatch")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if not bool(np.isfinite(frame.loc[:, ["open", "high", "low", "close", "volume"]].to_numpy(float)).all()):
        raise GainerScanError(f"{path}: non-finite OHLCV")
    frame = frame.sort_values("ts", kind="stable").drop_duplicates("ts", keep="last").reset_index(drop=True)
    close_times = frame["open_time"] + pd.Timedelta(minutes=minutes)
    return frame.loc[close_times <= cutoff].reset_index(drop=True)


def is_continuous(frame: pd.DataFrame, *, start_i: int, end_i: int, minutes: int) -> bool:
    """Check the exact MA support through the visible decision endpoint."""

    if start_i < 0 or end_i >= len(frame) or end_i < start_i:
        return False
    times = frame["open_time"].iloc[start_i : end_i + 1]
    return len(times) == end_i - start_i + 1 and bool(
        (times.diff().iloc[1:] == pd.Timedelta(minutes=minutes)).all()
    )


def closed_endpoint_indices(
    frame: pd.DataFrame, *, minutes: int, day_start_utc: pd.Timestamp, cutoff: pd.Timestamp
) -> list[int]:
    """Return frozen endpoints whose right-edge close is strictly after Beijing 00:00."""

    closes = frame["open_time"] + pd.Timedelta(minutes=minutes)
    return [
        int(index)
        for index, close in enumerate(closes)
        if close > day_start_utc and close <= cutoff
    ]


@dataclass(frozen=True)
class RenderTask:
    """One inference input whose data ends at its own confirmation endpoint."""

    image: np.ndarray
    metadata: dict[str, Any]


def window_stem(symbol: str, minutes: int, endpoint_ms: int, n_bars: int) -> str:
    """Create a deterministic, collision-resistant output stem for one window."""

    clean_symbol = "".join(char if char.isalnum() else "_" for char in symbol)
    return f"{clean_symbol}_{minutes}m_end{endpoint_ms}_n{n_bars}"


def build_render_task(
    frame: pd.DataFrame, *, symbol: str, minutes: int, endpoint_i: int, n_bars: int, core_bars: int
) -> RenderTask:
    """Render a causal W18/W19 input, never exposing rows after ``endpoint_i``."""

    if n_bars != PRE_BARS + core_bars + POST_BARS:
        raise GainerScanError("window length does not match Arm-A pre/core/post geometry")
    core_end_i = endpoint_i - POST_BARS
    core_start_i = core_end_i - core_bars + 1
    support_start_i = core_start_i - 11 - SUPPORT_BARS
    if not is_continuous(frame, start_i=support_start_i, end_i=endpoint_i, minutes=minutes):
        raise GainerScanError("insufficient_or_gapped_1200_bar_support")
    # This slice is the no-future boundary.  `_window_asset` cannot inspect a
    # later row, even if the original input CSV contains one.
    causal_frame = frame.iloc[: endpoint_i + 1].copy()
    png, _ignored_training_box, visible = _window_asset(
        causal_frame,
        core_start_i=core_start_i,
        core_end_i=core_end_i,
        pre_bars=PRE_BARS,
        post_bars=POST_BARS,
        support_start_i=support_start_i,
        price_scale=VISIBLE_RANGE_PRICE_SCALE,
    )
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape != (CANVAS_HEIGHT, CANVAS_WIDTH, 3):
        raise GainerScanError("renderer did not produce the expected 742x1280 canvas")
    endpoint_open = pd.Timestamp(causal_frame["open_time"].iloc[endpoint_i])
    endpoint_close = endpoint_open + pd.Timedelta(minutes=minutes)
    endpoint_ms = int(endpoint_open.value // 1_000_000)
    metadata = {
        "symbol": symbol,
        "minutes": int(minutes),
        "endpoint_i": int(endpoint_i),
        "endpoint_open_time_utc": endpoint_open.isoformat(),
        "decision_time_utc": endpoint_close.isoformat(),
        "core_start_i": int(core_start_i),
        "core_end_i": int(core_end_i),
        "core_bars": int(core_bars),
        "n_bars": int(n_bars),
        "window_start_i": int(core_start_i - PRE_BARS),
        "window_end_i": int(endpoint_i),
        "support_start_i": int(support_start_i),
        "visible": visible,
        "stem": window_stem(symbol, minutes, endpoint_ms, n_bars),
        "input_png_sha256": hashlib.sha256(png).hexdigest(),
        "tensor_geometry": {
            "canvas_height": CANVAS_HEIGHT,
            "canvas_width": CANVAS_WIDTH,
            "observed_original_shape": [int(image.shape[0]), int(image.shape[1])],
            "expected_rect_preprocess_shape": [TENSOR_HEIGHT, TENSOR_WIDTH],
            "stride": 32,
            "left": PLOT_LEFT,
            "plot_width": PLOT_WIDTH,
        },
    }
    return RenderTask(image=image, metadata=metadata)


def map_x_to_local_bars(x0: float, x1: float, *, n_bars: int) -> tuple[int, int, dict[str, float]]:
    """Map a clipped raw prediction to nearest actual renderer candle centers."""

    if n_bars not in (18, 19):
        raise GainerScanError(f"only Arm-A W18/W19 mapping is valid, got {n_bars}")
    if not all(math.isfinite(value) for value in (x0, x1)):
        raise GainerScanError("non-finite raw prediction x coordinate")
    clipped0 = min(max(float(x0), float(PLOT_LEFT)), float(PLOT_LEFT + PLOT_WIDTH))
    clipped1 = min(max(float(x1), float(PLOT_LEFT)), float(PLOT_LEFT + PLOT_WIDTH))
    left, right = sorted((clipped0, clipped1))
    centers = np.asarray(
        [int(PLOT_LEFT + index / (n_bars - 1) * PLOT_WIDTH) for index in range(n_bars)],
        dtype=float,
    )
    start = int(np.argmin(np.abs(centers - left)))
    end = int(np.argmin(np.abs(centers - right)))
    return min(start, end), max(start, end), {
        "raw_x0": float(x0), "raw_x1": float(x1), "clipped_x0": left, "clipped_x1": right,
    }


def _direction(model_names: Mapping[Any, Any], class_id: int) -> str:
    label = str(model_names.get(class_id, model_names.get(str(class_id), f"class_{class_id}")))
    lowered = label.lower()
    if "long" in lowered:
        return "long"
    if "short" in lowered:
        return "short"
    return label


def verify_model_names(model_names: Mapping[Any, Any]) -> dict[int, str]:
    """Require the fixed Arm-A class identity before recording class directions."""

    normalized = {int(key): str(value) for key, value in model_names.items()}
    if normalized != EXPECTED_MODEL_NAMES:
        raise GainerScanError(f"Arm-A class identity drift: {normalized!r}")
    return normalized


def install_preprocess_shape_observer(model: Any) -> None:
    """Assert actual post-letterbox tensor geometry without changing prediction pixels.

    Ultralytics warms its backend before ``on_predict_start``.  The callback
    therefore wraps the real per-batch preprocessing path, rather than mistaking
    warmup's synthetic tensor for the submitted chart geometry.
    """

    def on_predict_start(predictor: Any) -> None:
        if getattr(predictor, "_ma_gainers_preprocess_observer_installed", False):
            return
        original = predictor.preprocess

        def observed_preprocess(batch: Any) -> Any:
            tensor = original(batch)
            shape = tuple(int(value) for value in tensor.shape[-2:])
            if shape != (TENSOR_HEIGHT, TENSOR_WIDTH):
                raise GainerScanError(
                    f"unexpected post-letterbox tensor geometry: {shape}; expected {(TENSOR_HEIGHT, TENSOR_WIDTH)}"
                )
            predictor._ma_gainers_observed_preprocess_shape = shape
            return tensor

        predictor.preprocess = observed_preprocess
        predictor._ma_gainers_preprocess_observer_installed = True

    model.add_callback("on_predict_start", on_predict_start)


def raw_boxes(prediction: Any, task: RenderTask, frame: pd.DataFrame, model_names: Mapping[Any, Any]) -> list[dict[str, Any]]:
    """Serialize all detector boxes without filtering them by predicted geometry."""

    boxes = getattr(prediction, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    output: list[dict[str, Any]] = []
    for xyxy, xywhn, confidence, class_id in zip(
        boxes.xyxy.cpu().numpy(), boxes.xywhn.cpu().numpy(), boxes.conf.cpu().numpy(), boxes.cls.cpu().numpy()
    ):
        x0, y0, x1, y1 = (float(value) for value in xyxy)
        local_start, local_end, x_mapping = map_x_to_local_bars(
            x0, x1, n_bars=int(task.metadata["n_bars"])
        )
        global_start = int(task.metadata["window_start_i"]) + local_start
        global_end = int(task.metadata["window_start_i"]) + local_end
        direction = _direction(model_names, int(class_id))
        output.append(
            {
                "class_id": int(class_id),
                "class_name": str(model_names.get(int(class_id), model_names.get(str(int(class_id)), f"class_{int(class_id)}"))),
                "mapped_direction": direction,
                "confidence": float(confidence),
                "xyxy_pixels": [x0, y0, x1, y1],
                "xywh_normalized": [float(value) for value in xywhn],
                "x_mapping": x_mapping,
                "predicted_core_start_local": local_start,
                "predicted_core_end_local": local_end,
                "predicted_core_start_i": global_start,
                "predicted_core_end_i": global_end,
                "predicted_core_start_open_time_utc": pd.Timestamp(frame["open_time"].iloc[global_start]).isoformat(),
                "predicted_core_end_close_time_utc": (
                    pd.Timestamp(frame["open_time"].iloc[global_end]) + pd.Timedelta(minutes=int(task.metadata["minutes"]))
                ).isoformat(),
            }
        )
    return output


def draw_overlay(image: np.ndarray, boxes: Iterable[Mapping[str, Any]]) -> np.ndarray:
    """Draw a display-only copy of raw model boxes with confidence and direction."""

    overlay = image.copy()
    for box in boxes:
        x0, y0, x1, y1 = (int(round(float(value))) for value in box["xyxy_pixels"])
        x0, x1 = sorted((max(0, min(CANVAS_WIDTH - 1, x0)), max(0, min(CANVAS_WIDTH - 1, x1))))
        y0, y1 = sorted((max(0, min(CANVAS_HEIGHT - 1, y0)), max(0, min(CANVAS_HEIGHT - 1, y1))))
        color = (20, 150, 20) if box["mapped_direction"] == "long" else (40, 80, 230)
        cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 2, cv2.LINE_AA)
        label = f"{box['mapped_direction']} {box['confidence']:.2f} [{box['predicted_core_start_local']}-{box['predicted_core_end_local']}]"
        cv2.putText(overlay, label, (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, .38, color, 1, cv2.LINE_AA)
    return overlay


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise GainerScanError(f"failed to write PNG: {path}")


def _scan_tasks(model: Any, tasks: list[RenderTask], frame: pd.DataFrame, model_names: Mapping[Any, Any], *, device: str) -> list[tuple[RenderTask, list[dict[str, Any]]]]:
    """Batch equal-size 742x1280 inputs; rect preprocessing deterministically uses 768x1280."""

    if not tasks:
        return []
    if any(task.image.shape != (CANVAS_HEIGHT, CANVAS_WIDTH, 3) for task in tasks):
        raise GainerScanError("mixed or unexpected model input shape")
    predictions = model.predict(
        source=[task.image for task in tasks],
        imgsz=1280,
        conf=CONFIDENCE,
        iou=NMS_IOU,
        batch=len(tasks),
        device=device,
        rect=True,
        augment=False,
        verbose=False,
        save=False,
    )
    if len(predictions) != len(tasks):
        raise GainerScanError("detector output count differs from submitted inputs")
    observed_preprocess = tuple(getattr(getattr(model, "predictor", None), "_ma_gainers_observed_preprocess_shape", ()))
    if observed_preprocess != (TENSOR_HEIGHT, TENSOR_WIDTH):
        raise GainerScanError(f"predictor did not observe expected tensor geometry: {observed_preprocess}")
    results = []
    for task, prediction in zip(tasks, predictions):
        observed = tuple(int(value) for value in getattr(prediction, "orig_shape", task.image.shape[:2]))
        if observed != (CANVAS_HEIGHT, CANVAS_WIDTH):
            raise GainerScanError(f"detector observed unexpected original shape: {observed}")
        geometry = dict(task.metadata["tensor_geometry"])
        geometry["observed_preprocess_shape"] = list(observed_preprocess)
        checked_task = RenderTask(image=task.image, metadata={**task.metadata, "tensor_geometry": geometry})
        results.append((checked_task, raw_boxes(prediction, checked_task, frame, model_names)))
    return results


def _coverage_streams(coverage: Any) -> list[dict[str, Any]]:
    """Accept the frozen contract's top-level list (and legacy ``rows`` wrapper)."""

    rows = coverage.get("rows") if isinstance(coverage, dict) else coverage
    if not isinstance(rows, list):
        raise GainerScanError("coverage.json requires rows list")
    streams: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise GainerScanError("coverage row must be an object")
        symbol, minutes, status = str(row.get("symbol", "")), int(row.get("minutes", 0)), str(row.get("status", ""))
        if not symbol or minutes not in (15, 30, 60) or status not in {"ok", "error"}:
            raise GainerScanError(f"invalid coverage row: {row!r}")
        if (symbol, minutes) in seen:
            raise GainerScanError(f"duplicate coverage stream: {symbol}/{minutes}")
        seen.add((symbol, minutes))
        streams.append(dict(row, symbol=symbol, minutes=minutes, status=status))
    return streams


def run(inputs: Path, out: Path, model_path: Path, *, device: str = "cpu") -> dict[str, Any]:
    """Write a fresh raw-prediction ledger from frozen input and ranking receipts."""

    inputs, out, model_path = inputs.resolve(), out.resolve(), model_path.resolve()
    coverage_path, ranking_path = inputs / "coverage.json", inputs / "ranking.json"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite scan results: {out}")
    coverage, ranking = _read_json(coverage_path), _read_json(ranking_path)
    if not isinstance(ranking, dict):
        raise GainerScanError("ranking.json requires an object")
    cutoff = _timestamp_from_ms(ranking.get("cutoff_ms"), field="cutoff_ms")
    day_start = _as_utc(ranking.get("day_start_utc"), field="day_start_utc")
    if cutoff <= day_start:
        raise GainerScanError("cutoff must be after Beijing-day start")
    streams = _coverage_streams(coverage)
    if not model_path.is_file() or sha256_file(model_path) != MODEL_SHA256:
        raise GainerScanError("Arm-A model SHA drift")
    from ultralytics import YOLO  # Keep module imports testable without the runtime.

    import torch

    torch.set_num_threads(4)
    model = YOLO(str(model_path))
    model_names = verify_model_names(model.names)
    install_preprocess_shape_observer(model)
    rank_context = {
        str(row.get("symbol")): {
            key: row[key]
            for key in ("rank", "change_today_pct", "last", "sodUtc8")
            if key in row
        }
        for row in ranking.get("ranked", [])
        if isinstance(row, dict) and row.get("symbol")
    }
    out.mkdir(parents=True)
    (out / "raw").mkdir()
    (out / "overlay").mkdir()
    predictions_path = out / "predictions.jsonl"
    metadata = {
        "schema": "ma-gainers-raw-predictions-v1",
        "inputs": {
            "coverage_path": coverage_path.relative_to(ROOT).as_posix(),
            "coverage_sha256": sha256_file(coverage_path),
            "ranking_path": ranking_path.relative_to(ROOT).as_posix(),
            "ranking_sha256": sha256_file(ranking_path),
        },
        "model_path": model_path.relative_to(ROOT).as_posix(),
        "model_sha256": MODEL_SHA256,
        "device": device,
        "cpu_threads": 4,
        "detector": {"imgsz": 1280, "conf": CONFIDENCE, "iou": NMS_IOU, "rect": True, "augment": False, "batch_size": BATCH_SIZE},
        "semantic_notice": "Raw predictions only; model input includes core+5 confirmation and is not a fresh signal.",
        "day_start_utc": day_start.isoformat(),
        "cutoff_utc": cutoff.isoformat(),
    }
    _write_json(out / "run_metadata.json", metadata)
    stream_summaries: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    with predictions_path.open("w", encoding="utf-8") as ledger:
        for stream in streams:
            base = {"symbol": stream["symbol"], "minutes": stream["minutes"], "ranking": rank_context.get(stream["symbol"], {})}
            if stream["status"] != "ok":
                ledger.write(json.dumps({**base, "status": "skipped_stream", "reason": stream.get("error", "coverage_error")}, sort_keys=True) + "\n")
                stream_summaries.append({**base, "status": "coverage_error", "reason": stream.get("error")})
                totals["coverage_error_streams"] += 1
                continue
            source = _repo_path(str(stream.get("path", "")))
            expected_sha = str(stream.get("sha256", ""))
            if not source.is_file() or sha256_file(source) != expected_sha:
                raise GainerScanError(f"coverage source SHA drift: {source}")
            frame = load_native_ohlc(source, minutes=stream["minutes"], cutoff=cutoff)
            endpoints = closed_endpoint_indices(frame, minutes=stream["minutes"], day_start_utc=day_start, cutoff=cutoff)
            latest_endpoint = endpoints[-1] if endpoints else None
            stream_counts: Counter[str] = Counter(endpoints=len(endpoints))
            latest_rows: list[dict[str, Any]] = []
            tasks: list[RenderTask] = []
            def flush() -> None:
                nonlocal tasks, latest_rows
                for task, boxes in _scan_tasks(model, tasks, frame, model_names, device=device):
                    record = {**task.metadata, "status": "scored", "raw_box_count": len(boxes), "boxes": boxes}
                    ledger.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    stream_counts["scored_windows"] += 1
                    stream_counts["raw_boxes"] += len(boxes)
                    is_latest = int(task.metadata["endpoint_i"]) == latest_endpoint
                    if boxes or is_latest:
                        _write_png(out / "raw" / f"{task.metadata['stem']}.png", task.image)
                    if boxes:
                        _write_png(out / "overlay" / f"{task.metadata['stem']}.png", draw_overlay(task.image, boxes))
                        stream_counts["windows_with_boxes"] += 1
                    if is_latest:
                        latest_rows.append({"n_bars": task.metadata["n_bars"], "detected": bool(boxes), "raw_box_count": len(boxes), "stem": task.metadata["stem"]})
                tasks = []
            for endpoint_i in endpoints:
                for n_bars, core_bars in WINDOW_SPECS:
                    try:
                        tasks.append(build_render_task(frame, symbol=stream["symbol"], minutes=stream["minutes"], endpoint_i=endpoint_i, n_bars=n_bars, core_bars=core_bars))
                    except GainerScanError as exc:
                        ledger.write(json.dumps({**base, "endpoint_i": endpoint_i, "n_bars": n_bars, "status": "skipped", "reason": str(exc)}, sort_keys=True) + "\n")
                        stream_counts["skipped_windows"] += 1
                        if endpoint_i == latest_endpoint:
                            latest_rows.append({"n_bars": n_bars, "detected": False, "raw_box_count": 0, "status": "skipped", "reason": str(exc)})
                    if len(tasks) == BATCH_SIZE:
                        flush()
            flush()
            latest_detected = any(row["detected"] for row in latest_rows)
            no_scorable_history = (
                stream_counts["scored_windows"] == 0
                and stream_counts["skipped_windows"] > 0
                and stream_counts["skipped_windows"] == len(endpoints) * len(WINDOW_SPECS)
            )
            stream_summaries.append({**base, "status": "insufficient_history" if no_scorable_history else "ok", "source_path": str(stream["path"]), "source_rows_frozen": len(frame), **dict(stream_counts), "latest_endpoint_i": latest_endpoint, "latest_endpoint_detected": latest_detected, "latest_rows": latest_rows})
            totals.update(stream_counts)
            print(json.dumps({"symbol": stream["symbol"], "minutes": stream["minutes"], "scored_windows": stream_counts["scored_windows"], "skipped_windows": stream_counts["skipped_windows"], "raw_boxes": stream_counts["raw_boxes"]}, sort_keys=True), flush=True)
    summary = {"schema": "ma-gainers-raw-predictions-summary-v1", "streams": stream_summaries, "totals": dict(totals), "raw_prediction_semantics": "No rule prefilter, future outcome, GT-box gate, or semantic detection filter. Display-only deduplication is intentionally deferred."}
    _write_json(out / "summary.json", summary)
    return summary


def main() -> None:
    """Run the frozen-input scanner from the command line."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_EXP / "inputs")
    parser.add_argument("--out", type=Path, default=DEFAULT_EXP / "results")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    print(json.dumps(run(args.inputs, args.out, args.model, device=args.device), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
