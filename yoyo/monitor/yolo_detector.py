"""Pinned, causal image inference for the owner's notification-only monitor.

Source: the frozen IMACD confirmation/timeframe experiments dated 2026-09-08.
At an endpoint (a candle OPEN timestamp), inputs are only confirmed OHLCV and
the monitor's close-source SMA/EMA 20/60/120 through that candle. Each image
contains the last 18 or 19 candles, using the original L1 renderer unchanged.
No following candle, outcome, dataset reader or execution layer is accessed.

This adapter emits detector proposals, not trade decisions. The caller owns
the arrow/setup association, direction agreement, permanent md invalidation,
nine-bar waiting budget, freshness, persistence and notification delivery.
Absolute candle timestamps make proposal identity stable across restarts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any

import numpy as np
import pandas as pd

from yoyo.monitor import MONITORED_TIMEFRAMES, TIMEFRAMES


ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / (
    "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/"
    "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt"
)
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
MODEL_CLASSES = {0: "dense_long", 1: "dense_short"}
TIMEFRAME_MS = {period: TIMEFRAMES[period] for period in MONITORED_TIMEFRAMES}
MA_COLUMNS = ("sma20", "sma60", "sma120", "ema20", "ema60", "ema120")
PREDICT_PARAMETERS = dict(imgsz=1280, conf=.25, iou=.70, verbose=False,
                          rect=True, half=False, agnostic_nms=False,
                          max_det=300, save=False)


class YoloDetectorError(RuntimeError):
    """Inference is unavailable or input/output violates the frozen contract."""


@dataclass(frozen=True)
class RenderedWindow:
    image: np.ndarray
    transform: Any
    times: tuple[int, ...]
    input_pixel_sha256: str


def _timestamp(value: Any) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise YoloDetectorError("invalid_candle_timestamp")
    try:
        numeric = float(value)
        result = int(numeric)
    except (TypeError, ValueError, OverflowError) as exc:
        raise YoloDetectorError("invalid_candle_timestamp") from exc
    if not np.isfinite(numeric) or numeric != result or result < 0:
        raise YoloDetectorError("invalid_candle_timestamp")
    return result


def prepare_windows(candles: list[dict], timeframe: str,
                    endpoint_ms: int) -> list[RenderedWindow]:
    """Validate the causal prefix and render W18/W19 without future OHLC reads.

    Source fields: t/o/h/l/c/v and six precomputed close-source MAs. OHLCV
    validation spans the supplied prefix; MA readiness is required in both
    image windows. The endpoint is an OPEN timestamp for an already closed
    candle. When supplied, confirm/bar_close_ms must agree with that contract.
    """
    from yoyo.layers.l1_detection.render import render_chart

    if timeframe not in TIMEFRAME_MS:
        raise YoloDetectorError("unsupported_timeframe")
    endpoint = _timestamp(endpoint_ms)
    duration = TIMEFRAME_MS[timeframe]
    prefix: list[tuple[int, dict]] = []
    for candle in candles:
        try:
            stamp = _timestamp(candle["t"])
        except (KeyError, TypeError) as exc:
            raise YoloDetectorError("invalid_candle_timestamp") from exc
        if stamp <= endpoint:
            prefix.append((stamp, candle))
    if len(prefix) < 19 or prefix[-1][0] != endpoint:
        raise YoloDetectorError("incomplete_input_window")
    previous = None
    for stamp, candle in prefix:
        if stamp % duration or (previous is not None and stamp - previous != duration):
            raise YoloDetectorError("candle_gap_or_unaligned_timestamp")
        previous = stamp
        try:
            o, h, low, c, volume = (float(candle[k]) for k in ("o", "h", "l", "c", "v"))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise YoloDetectorError("invalid_ohlcv") from exc
        if (not all(np.isfinite(x) for x in (o, h, low, c, volume))
                or low <= 0 or volume < 0 or h < max(o, c) or low > min(o, c)):
            raise YoloDetectorError("invalid_ohlcv")
        if "confirm" in candle and str(candle["confirm"]) not in ("1", "True"):
            raise YoloDetectorError("unconfirmed_candle")
        if "bar_close_ms" in candle and _timestamp(candle["bar_close_ms"]) != stamp + duration:
            raise YoloDetectorError("invalid_candle_close_clock")
    windows = []
    for length in (18, 19):
        rows = []
        for _, candle in prefix[-length:]:
            row = {target: float(candle[source]) for source, target in
                   (("o", "open"), ("h", "high"), ("l", "low"),
                    ("c", "close"), ("v", "volume"))}
            try:
                row.update({name: float(candle[name]) for name in MA_COLUMNS})
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise YoloDetectorError("ma_warmup_missing") from exc
            if not all(np.isfinite(row[name]) for name in MA_COLUMNS):
                raise YoloDetectorError("ma_warmup_missing")
            rows.append(row)
        image, transform = render_chart(pd.DataFrame(rows), out_path=None)
        pixel_sha = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()
        windows.append(RenderedWindow(image, transform,
                                      tuple(t for t, _ in prefix[-length:]), pixel_sha))
    return windows


def parse_prediction(xywhn, classes, confidences, window: RenderedWindow,
                     symbol: str, timeframe: str) -> list[dict]:
    """Map normalized box edges to nearest candle centers, as in frozen infer.

    Only box outputs and that input window's transform/timestamps are read.
    Geometry never uses the next candle. Core/post filtering does not enforce
    a relationship to an IMACD arrow; that belongs to the caller's event gate.
    """
    coordinates = np.asarray(xywhn, dtype=float).reshape(-1, 4)
    labels = np.asarray(classes, dtype=float).reshape(-1)
    scores = np.asarray(confidences, dtype=float).reshape(-1)
    if len(coordinates) != len(labels) or len(labels) != len(scores):
        raise YoloDetectorError("detector_box_count_mismatch")
    centers = np.asarray([window.transform.x_at(k) for k in range(len(window.times))])
    proposals = []
    for xywh, label, confidence in zip(coordinates, labels, scores):
        if (not np.isfinite(label) or label not in MODEL_CLASSES
                or not np.isfinite(confidence) or not 0 <= confidence <= 1
                or not np.isfinite(xywh).all() or np.any(xywh < 0)
                or np.any(xywh > 1)):
            raise YoloDetectorError("invalid_detector_box")
        if confidence < PREDICT_PARAMETERS["conf"]:
            continue
        cx, cy, width, height = map(float, xywh)
        a = int(np.argmin(np.abs(centers - (cx - width / 2) * window.transform.width)))
        b = int(np.argmin(np.abs(centers - (cx + width / 2) * window.transform.width)))
        a, b = sorted((a, b))
        core_length, post = b - a + 1, len(window.times) - 1 - b
        identity = [MODEL_SHA256, symbol, timeframe, window.times,
                    window.input_pixel_sha256, int(label),
                    [round(float(x), 10) for x in xywh]]
        detection_id = "yolo_" + hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        proposals.append(dict(
            model_sha256=MODEL_SHA256, confidence=float(confidence),
            detection_id=detection_id, input_pixel_sha256=window.input_pixel_sha256,
            window_len=len(window.times), window_start_ms=window.times[0],
            window_end_ms=window.times[-1], core_start_ms=window.times[a],
            core_end_ms=window.times[b], core_length_bars=core_length,
            post_bars=post, side="long" if int(label) == 0 else "short",
            structural_pass=core_length in (4, 5) and 2 <= post <= 9,
            prediction_cx_norm=cx, prediction_cy_norm=cy,
            prediction_w_norm=width, prediction_h_norm=height,
        ))
    return proposals


class YoloDetector:
    """Lazy pinned model; a single lock serializes initialization and inference."""

    def __init__(self):
        self._lock = threading.RLock()
        self._model = None
        self._device = None
        self._error = None
        self._windows_scored = 0
        self._last_duration = None

    def _load(self):
        if self._model is not None:
            return
        try:
            digest = hashlib.sha256()
            with MODEL_PATH.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != MODEL_SHA256:
                raise YoloDetectorError("model_sha256_mismatch")
            import torch
            from ultralytics import YOLO

            model = YOLO(str(MODEL_PATH))
            if model.names != MODEL_CLASSES:
                raise YoloDetectorError("model_classes_mismatch")
            self._device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._model = model
            self._error = None
        except YoloDetectorError:
            raise
        except Exception as exc:
            raise YoloDetectorError("model_load_failed:" + type(exc).__name__) from exc

    def warmup(self) -> dict:
        """Load and verify bytes/classes without scoring market data."""
        with self._lock:
            try:
                self._load()
            except YoloDetectorError as exc:
                self._error = str(exc)
                raise
            return self.status()

    def status(self) -> dict:
        # Do not block a health request on the inference lock.
        return dict(ready=self._model is not None, device=self._device,
                    model_sha256=MODEL_SHA256, error=self._error,
                    windows_scored=self._windows_scored,
                    last_duration_seconds=self._last_duration)

    def predict(self, candles: list[dict], symbol: str, timeframe: str,
                endpoint_ms: int) -> list[dict]:
        """Score only two windows ending at the requested closed candle."""
        windows = prepare_windows(candles, timeframe, endpoint_ms)
        with self._lock:
            started = time.monotonic()
            try:
                self._load()
                predictions = self._model.predict(
                    source=[window.image for window in windows], batch=len(windows),
                    device=self._device, **PREDICT_PARAMETERS)
                if len(predictions) != len(windows):
                    raise YoloDetectorError("detector_output_count_mismatch")
                proposals = []
                for prediction, window in zip(predictions, windows):
                    boxes = prediction.boxes
                    if boxes is not None and len(boxes):
                        proposals.extend(parse_prediction(
                            boxes.xywhn.cpu().numpy(), boxes.cls.cpu().numpy(),
                            boxes.conf.cpu().numpy(), window, symbol, timeframe))
                self._windows_scored += len(windows)
                self._error = None
                return proposals
            except YoloDetectorError as exc:
                self._error = str(exc)
                raise
            except Exception as exc:
                self._error = "inference_failed:" + type(exc).__name__
                raise YoloDetectorError(self._error) from exc
            finally:
                self._last_duration = round(time.monotonic() - started, 4)
