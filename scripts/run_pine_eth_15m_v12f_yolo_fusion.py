#!/usr/bin/env python3
"""Fuse frozen ETH 15m V12F Pine events with the Grade-A YOLO detector.

This is a research-only, pre-holdout integration surface.  It first reproduces
the frozen V12F Pine candidates, then runs the SHA-pinned Grade-A 8k-positive +
24k-negative, full-40, native-1280 YOLO11s checkpoint only on detector windows
that could geometrically overlap a Pine event.  Structurally valid YOLO boxes
also pass through the already validated causal semantic gate.

Fusion is intentionally tolerant about location: same-symbol, same-direction
closed event intervals need only overlap.  Exact bar equality is not required.
The fused event becomes available at the later of the Pine close and the full
YOLO input-window close.  The detector consumes 2--9 post-core bars, so every
result remains a delayed completed-history proposal and is never a tip signal.

Outputs contain ledgers only: ``pine_signals.csv``, ``model_proposals.csv``,
``fused_signals.csv`` and ``summary.json``.  This program does not calculate a
fused PnL, load rejected LightGBM artifacts, modify ACTIVE/frozen/forward state,
promote or deploy a model, or place an order.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from scripts.backtest_pine_eth_15m_v12_preholdout import build_v12_feature_frame
from scripts.run_pine_eth_15m_v12f import (
    BAR,
    HOLDOUT_START,
    PINE_SHA256,
    build_signal_ledger,
    file_sha256,
    validate_15m_frame,
    validate_frozen_pine,
    validate_period,
)
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.render import ChartTransform, render_chart
from yoyo.layers.l1_detection.semantic_gate import (
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame

MODEL_KEY = "grade_a8k_neg24k_full40_1280"
MODEL_PATH = (
    PROJECT
    / "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/"
    "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt"
)
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
RENDERER_PATH = PROJECT / "yoyo/layers/l1_detection/render.py"
RENDERER_SHA256 = "0962812feea57e0a666c4da62acea830cbbf53a3d66f6dbd722ae3e580ead3e7"
SEMANTIC_GATE_PATH = PROJECT / "yoyo/layers/l1_detection/semantic_gate.py"
SEMANTIC_GATE_SHA256 = "eda78c7a2a548bb7485aa1c7ca1f6a8ce6ee7805846aa6ba2e8e09789130fe5f"
GATE_PREREG_PATH = (
    PROJECT
    / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/"
    "preregistration.json"
)
GATE_PREREG_SHA256 = "09523b1a9b9ba3847d974bea36608418060c1534b63bc1f06a3d4976c4bda4da"

CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
WINDOW_LENGTHS = (18, 19)
ALLOWED_CORE_LENGTHS = frozenset((4, 5))
ALLOWED_CONFIRMATION_BARS = frozenset(range(2, 10))
MODEL_CONFIDENCE = 0.25
MODEL_NMS_IOU = 0.70
MODEL_IMGSZ = 1280
FUSION_STAGES = ("raw", "structural", "semantic")
OUTPUT_NAMES = (
    "pine_signals.csv",
    "model_proposals.csv",
    "fused_signals.csv",
    "summary.json",
)

PROPOSAL_COLUMNS = (
    "detection_id",
    "model_key",
    "symbol",
    "direction",
    "class_id",
    "class_name",
    "confidence",
    "window_len",
    "window_start_i",
    "window_end_i",
    "window_start_time",
    "window_end_bar_open",
    "available_at",
    "core_start_i",
    "core_end_i",
    "core_length_bars",
    "confirmation_bars",
    "interval_start",
    "interval_end",
    "prediction_cx_norm",
    "prediction_cy_norm",
    "prediction_w_norm",
    "prediction_h_norm",
    "input_pixel_sha256",
    "structural_pass",
    "structural_rejection_reason",
    "semantic_pass",
    "semantic_failed_checks",
    "semantic_checks",
    "semantic_features",
)

FUSED_COLUMNS = (
    "fusion_id",
    "symbol",
    "direction",
    "pine_signal_id",
    "model_detection_id",
    "pine_signal_i",
    "pine_interval_start",
    "pine_interval_end",
    "pine_available_at",
    "model_core_start_i",
    "model_core_end_i",
    "model_interval_start",
    "model_interval_end",
    "model_available_at",
    "combined_available_at",
    "availability_delay_bars_from_pine",
    "overlap_start",
    "overlap_end",
    "overlap_seconds",
    "exact_same_start_bar",
    "model_confidence",
    "model_confirmation_bars",
    "model_window_len",
    "eligible_model_matches",
)


class FusionError(RuntimeError):
    """Fail closed on model identity, inference, or time-contract drift."""


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def validate_frozen_detector() -> dict[str, Any]:
    """Verify every executable detector and semantic-gate identity."""

    expected = {
        MODEL_PATH: MODEL_SHA256,
        RENDERER_PATH: RENDERER_SHA256,
        SEMANTIC_GATE_PATH: SEMANTIC_GATE_SHA256,
        GATE_PREREG_PATH: GATE_PREREG_SHA256,
    }
    for path, digest in expected.items():
        if not path.is_file():
            raise FileNotFoundError(f"frozen detector input is missing: {path}")
        actual = file_sha256(path)
        if actual != digest:
            raise FusionError(
                f"frozen detector input drifted: path={path} expected={digest} actual={actual}"
            )
    payload = json.loads(GATE_PREREG_PATH.read_text(encoding="utf-8"))
    gates = payload.get("morphology_gate")
    if not isinstance(gates, dict):
        raise FusionError("frozen morphology_gate is missing from its preregistration")
    return dict(gates)


def choose_device(requested: str) -> str:
    """Resolve an explicit device or choose MPS/CUDA/CPU without fallback."""

    if requested != "auto":
        return requested
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "0"
    return "cpu"


def load_frozen_model() -> Any:
    """Load only the SHA-pinned checkpoint and validate its class contract."""

    from ultralytics import YOLO

    model = YOLO(str(MODEL_PATH))
    names = {int(class_id): str(name) for class_id, name in model.names.items()}
    if names != CLASS_NAMES:
        raise FusionError(f"detector class names drifted: {names}")
    return model


def map_prediction_to_core(
    *,
    cx: float,
    width: float,
    transform: ChartTransform,
    window_start_i: int,
    window_end_i: int,
) -> dict[str, int]:
    """Map normalized YOLO x geometry to the nearest source candle centers."""

    x0 = (float(cx) - float(width) / 2.0) * transform.width
    x1 = (float(cx) + float(width) / 2.0) * transform.width
    centers = np.asarray([transform.x_at(index) for index in range(transform.n_bars)])
    local_start = int(np.argmin(np.abs(centers - x0)))
    local_end = int(np.argmin(np.abs(centers - x1)))
    if local_end < local_start:
        local_start, local_end = local_end, local_start
    core_start_i = int(window_start_i) + local_start
    core_end_i = int(window_start_i) + local_end
    return {
        "core_start_i": core_start_i,
        "core_end_i": core_end_i,
        "core_length_bars": core_end_i - core_start_i + 1,
        "confirmation_bars": int(window_end_i) - core_end_i,
    }


def candidate_endpoint_indices(
    pine_signal_indices: Sequence[int], *, frame_length: int
) -> list[int]:
    """Return every detector endpoint able to overlap a Pine bar interval.

    With a 4--5 bar model core, 2--9 confirmation bars, and closed-interval
    matching, a core can touch the Pine interval at either boundary.  Therefore
    every possible detection endpoint lies from Pine ``signal_i + 1`` through
    ``signal_i + 14``.  The last source bar is excluded because its close is not
    present inside the bounded replay frame.
    """

    if frame_length < 0:
        raise ValueError("frame_length must be non-negative")
    latest_endpoint = frame_length - 2
    endpoints: set[int] = set()
    for raw_index in pine_signal_indices:
        signal_i = int(raw_index)
        for endpoint in range(signal_i + 1, signal_i + 15):
            if 0 <= endpoint <= latest_endpoint:
                endpoints.add(endpoint)
    return sorted(endpoints)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _detection_id(
    *, symbol: str, meta: Mapping[str, Any], class_id: int, xywhn: Sequence[float]
) -> str:
    identity = {
        "model_sha256": MODEL_SHA256,
        "symbol": symbol,
        "window_start_i": int(meta["window_start_i"]),
        "window_end_i": int(meta["window_end_i"]),
        "class_id": int(class_id),
        "xywhn": [round(float(value), 10) for value in xywhn],
    }
    return "yolo_" + sha256(_json(identity).encode("utf-8")).hexdigest()[:24]


def _process_prediction_batch(
    *,
    model: Any,
    tasks: Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]],
    enriched: pd.DataFrame,
    symbol: str,
    gates: Mapping[str, Any],
    device: str,
    batch_size: int,
    stats: Counter[str],
) -> list[dict[str, Any]]:
    """Infer one bounded batch and retain an auditable row for every raw box."""

    predictions = model.predict(
        source=[item[0] for item in tasks],
        imgsz=MODEL_IMGSZ,
        conf=MODEL_CONFIDENCE,
        iou=MODEL_NMS_IOU,
        batch=min(batch_size, len(tasks)),
        device=device,
        verbose=False,
    )
    if len(predictions) != len(tasks):
        raise FusionError("prediction count differs from model input count")
    times = pd.to_datetime(enriched["open_time"], utc=True)
    rows: list[dict[str, Any]] = []
    for prediction, (image, transform, meta) in zip(predictions, tasks):
        stats["windows_scored"] += 1
        boxes = prediction.boxes
        if boxes is None or len(boxes) == 0:
            continue
        stats["windows_with_boxes"] += 1
        for xywhn_raw, class_raw, confidence_raw in zip(
            boxes.xywhn.cpu().numpy(),
            boxes.cls.cpu().numpy(),
            boxes.conf.cpu().numpy(),
        ):
            stats["raw_boxes"] += 1
            xywhn = [float(value) for value in xywhn_raw]
            class_id = int(class_raw)
            if class_id not in CLASS_NAMES:
                raise FusionError(f"detector emitted unknown class id: {class_id}")
            mapped = map_prediction_to_core(
                cx=xywhn[0],
                width=xywhn[2],
                transform=transform,
                window_start_i=int(meta["window_start_i"]),
                window_end_i=int(meta["window_end_i"]),
            )
            structural_reasons: list[str] = []
            if mapped["core_length_bars"] not in ALLOWED_CORE_LENGTHS:
                structural_reasons.append("core_length")
            if mapped["confirmation_bars"] not in ALLOWED_CONFIRMATION_BARS:
                structural_reasons.append("confirmation_bars")
            structural_pass = not structural_reasons
            direction = "long" if class_id == 0 else "short"
            semantic_pass = False
            semantic_checks: dict[str, bool] = {}
            semantic_failed: list[str] = []
            semantic_features: dict[str, float | None] = {}
            if structural_pass:
                features = compute_causal_core_semantics(
                    enriched,
                    core_start_i=mapped["core_start_i"],
                    core_end_i=mapped["core_end_i"],
                    observed_end_i=int(meta["window_end_i"]),
                    direction=direction.upper(),
                )
                result = evaluate_causal_semantic_gate(features, gates)
                semantic_pass = bool(result.passed)
                semantic_checks = dict(result.checks)
                semantic_failed = list(result.failed_checks)
                semantic_features = features.to_dict()
                stats["structural_boxes"] += 1
                if semantic_pass:
                    stats["semantic_pass_boxes"] += 1
                else:
                    stats["semantic_rejected_boxes"] += 1
            else:
                stats["structural_rejected_boxes"] += 1

            core_start = _utc(times.iloc[mapped["core_start_i"]])
            core_end_open = _utc(times.iloc[mapped["core_end_i"]])
            window_end_open = _utc(times.iloc[int(meta["window_end_i"])])
            available_at = window_end_open + BAR
            rows.append(
                {
                    "detection_id": _detection_id(
                        symbol=symbol,
                        meta=meta,
                        class_id=class_id,
                        xywhn=xywhn,
                    ),
                    "model_key": MODEL_KEY,
                    "symbol": symbol,
                    "direction": direction,
                    "class_id": class_id,
                    "class_name": CLASS_NAMES[class_id],
                    "confidence": float(confidence_raw),
                    "window_len": int(meta["window_len"]),
                    "window_start_i": int(meta["window_start_i"]),
                    "window_end_i": int(meta["window_end_i"]),
                    "window_start_time": _utc(
                        times.iloc[int(meta["window_start_i"])]
                    ),
                    "window_end_bar_open": window_end_open,
                    "available_at": available_at,
                    "core_start_i": mapped["core_start_i"],
                    "core_end_i": mapped["core_end_i"],
                    "core_length_bars": mapped["core_length_bars"],
                    "confirmation_bars": mapped["confirmation_bars"],
                    "interval_start": core_start,
                    "interval_end": core_end_open + BAR,
                    "prediction_cx_norm": xywhn[0],
                    "prediction_cy_norm": xywhn[1],
                    "prediction_w_norm": xywhn[2],
                    "prediction_h_norm": xywhn[3],
                    "input_pixel_sha256": sha256(
                        np.ascontiguousarray(image).tobytes()
                    ).hexdigest(),
                    "structural_pass": structural_pass,
                    "structural_rejection_reason": ",".join(structural_reasons),
                    "semantic_pass": semantic_pass,
                    "semantic_failed_checks": _json(semantic_failed),
                    "semantic_checks": _json(semantic_checks),
                    "semantic_features": _json(semantic_features),
                }
            )
    return rows


def infer_model_proposals(
    raw: pd.DataFrame,
    pine_signals: pd.DataFrame,
    *,
    symbol: str,
    model: Any,
    gates: Mapping[str, Any],
    device: str,
    batch_size: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Run the detector only where a legal box could overlap a Pine event."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    enriched = add_candidate_features(raw)
    if not pd.to_datetime(enriched["open_time"], utc=True).equals(
        pd.to_datetime(raw["open_time"], utc=True)
    ):
        raise FusionError("detector feature frame changed source time alignment")
    endpoints = candidate_endpoint_indices(
        pine_signals.get("signal_i", pd.Series(dtype=int)).astype(int).tolist(),
        frame_length=len(enriched),
    )
    stats: Counter[str] = Counter(
        {
            "pine_signals_conditioning_scan": len(pine_signals),
            "candidate_endpoints": len(endpoints),
        }
    )
    tasks: list[tuple[np.ndarray, ChartTransform, dict[str, Any]]] = []
    rows: list[dict[str, Any]] = []

    def flush() -> None:
        if not tasks:
            return
        rows.extend(
            _process_prediction_batch(
                model=model,
                tasks=tasks,
                enriched=enriched,
                symbol=symbol,
                gates=gates,
                device=device,
                batch_size=batch_size,
                stats=stats,
            )
        )
        tasks.clear()

    for endpoint in endpoints:
        for window_len in WINDOW_LENGTHS:
            start = int(endpoint) - int(window_len) + 1
            if start < 0:
                stats["skip_insufficient_history"] += 1
                continue
            window = enriched.iloc[start : endpoint + 1]
            if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                stats["skip_ma_warmup"] += 1
                continue
            image, transform = render_chart(window, out_path=None)
            tasks.append(
                (
                    image,
                    transform,
                    {
                        "window_len": int(window_len),
                        "window_start_i": start,
                        "window_end_i": int(endpoint),
                    },
                )
            )
            if len(tasks) >= batch_size:
                flush()
    flush()
    proposals = pd.DataFrame(rows, columns=PROPOSAL_COLUMNS)
    if not proposals.empty and proposals["detection_id"].duplicated().any():
        raise FusionError("detector emitted duplicate proposal identities")
    return proposals, {key: int(value) for key, value in sorted(stats.items())}


def proposal_stage_mask(proposals: pd.DataFrame, stage: str) -> pd.Series:
    """Return the frozen proposal eligibility mask for one diagnostic stage."""

    if stage not in FUSION_STAGES:
        raise ValueError(f"stage must be one of {FUSION_STAGES}, got {stage!r}")
    if proposals.empty:
        return pd.Series(False, index=proposals.index, dtype=bool)
    if stage == "raw":
        return pd.Series(True, index=proposals.index, dtype=bool)
    structural = proposals["structural_pass"].astype(bool)
    if stage == "structural":
        return structural
    return structural & proposals["semantic_pass"].astype(bool)


def fuse_signal_intervals(
    pine_signals: pd.DataFrame,
    proposals: pd.DataFrame,
    *,
    stage: str = "semantic",
) -> pd.DataFrame:
    """Choose the earliest causal proposal eligible at one frozen stage."""

    eligible = proposal_stage_mask(proposals, stage)
    if pine_signals.empty or proposals.empty:
        return pd.DataFrame(columns=FUSED_COLUMNS)
    model = proposals.loc[eligible].copy()
    for column in ("interval_start", "interval_end", "available_at"):
        model[column] = pd.to_datetime(model[column], utc=True)
    rows: list[dict[str, Any]] = []
    for pine in pine_signals.to_dict(orient="records"):
        pine_start = _utc(pine["interval_start"])
        pine_end = _utc(pine["interval_end"])
        pine_available = _utc(pine["available_at"])
        options = model.loc[
            model["symbol"].eq(str(pine["symbol"]))
            & model["direction"].eq(str(pine["direction"]))
            & model["interval_start"].le(pine_end)
            & model["interval_end"].ge(pine_start)
        ].copy()
        if options.empty:
            continue
        options = options.sort_values(
            [
                "available_at",
                "confidence",
                "confirmation_bars",
                "window_len",
                "detection_id",
            ],
            ascending=[True, False, True, True, True],
            kind="stable",
        )
        chosen = options.iloc[0]
        model_available = _utc(chosen["available_at"])
        combined = max(pine_available, model_available)
        overlap_start = max(pine_start, _utc(chosen["interval_start"]))
        overlap_end = min(pine_end, _utc(chosen["interval_end"]))
        if overlap_start > overlap_end:
            raise FusionError("selected intervals do not overlap")
        identity = (
            f"{pine['signal_id']}|{chosen['detection_id']}|{combined.isoformat()}"
        ).encode()
        rows.append(
            {
                "fusion_id": "fusion_" + sha256(identity).hexdigest()[:24],
                "symbol": pine["symbol"],
                "direction": pine["direction"],
                "pine_signal_id": pine["signal_id"],
                "model_detection_id": chosen["detection_id"],
                "pine_signal_i": int(pine["signal_i"]),
                "pine_interval_start": pine_start,
                "pine_interval_end": pine_end,
                "pine_available_at": pine_available,
                "model_core_start_i": int(chosen["core_start_i"]),
                "model_core_end_i": int(chosen["core_end_i"]),
                "model_interval_start": _utc(chosen["interval_start"]),
                "model_interval_end": _utc(chosen["interval_end"]),
                "model_available_at": model_available,
                "combined_available_at": combined,
                "availability_delay_bars_from_pine": float(
                    (combined - pine_available) / BAR
                ),
                "overlap_start": overlap_start,
                "overlap_end": overlap_end,
                "overlap_seconds": float((overlap_end - overlap_start).total_seconds()),
                "exact_same_start_bar": bool(
                    pine_start == _utc(chosen["interval_start"])
                ),
                "model_confidence": float(chosen["confidence"]),
                "model_confirmation_bars": int(chosen["confirmation_bars"]),
                "model_window_len": int(chosen["window_len"]),
                "eligible_model_matches": len(options),
            }
        )
    return pd.DataFrame(rows, columns=FUSED_COLUMNS)


def validate_fusion_ledgers(
    pine_signals: pd.DataFrame,
    proposals: pd.DataFrame,
    fused: pd.DataFrame,
    *,
    stage: str = "semantic",
) -> None:
    """Fail closed if an emitted ledger violates identity or causal timing."""

    eligible = proposal_stage_mask(proposals, stage)
    if pine_signals.get("signal_id", pd.Series(dtype=str)).duplicated().any():
        raise FusionError("Pine signal identities are not unique")
    if proposals.get("detection_id", pd.Series(dtype=str)).duplicated().any():
        raise FusionError("detector proposal identities are not unique")
    if fused.get("fusion_id", pd.Series(dtype=str)).duplicated().any():
        raise FusionError("fusion identities are not unique")
    if fused.get("pine_signal_id", pd.Series(dtype=str)).duplicated().any():
        raise FusionError("more than one fused row was emitted for a Pine signal")

    if not pine_signals.empty:
        pine_start = pd.to_datetime(pine_signals["interval_start"], utc=True)
        pine_end = pd.to_datetime(pine_signals["interval_end"], utc=True)
        pine_available = pd.to_datetime(pine_signals["available_at"], utc=True)
        if not (pine_end.eq(pine_start + BAR) & pine_available.eq(pine_end)).all():
            raise FusionError("Pine interval or availability contract drifted")

    if not proposals.empty:
        proposal_available = pd.to_datetime(proposals["available_at"], utc=True)
        proposal_window_end = pd.to_datetime(
            proposals["window_end_bar_open"], utc=True
        )
        proposal_start = pd.to_datetime(proposals["interval_start"], utc=True)
        proposal_end = pd.to_datetime(proposals["interval_end"], utc=True)
        structural = proposals["structural_pass"].astype(bool)
        semantic = proposals["semantic_pass"].astype(bool)
        expected_directions = proposals["class_id"].map({0: "long", 1: "short"})
        arithmetic_ok = (
            proposals["core_length_bars"].astype(int)
            == proposals["core_end_i"].astype(int)
            - proposals["core_start_i"].astype(int)
            + 1
        ) & (
            proposals["confirmation_bars"].astype(int)
            == proposals["window_end_i"].astype(int)
            - proposals["core_end_i"].astype(int)
        )
        window_arithmetic_ok = (
            proposals["window_len"].astype(int)
            == proposals["window_end_i"].astype(int)
            - proposals["window_start_i"].astype(int)
            + 1
        ) & proposals["window_len"].isin(WINDOW_LENGTHS)
        core_inside_window = (
            proposals["core_start_i"].astype(int)
            >= proposals["window_start_i"].astype(int)
        ) & (
            proposals["core_end_i"].astype(int)
            <= proposals["window_end_i"].astype(int)
        )
        expected_core_duration = pd.to_timedelta(
            proposals["core_length_bars"].astype(int) * int(BAR.total_seconds()),
            unit="s",
        )
        if not proposal_available.eq(proposal_window_end + BAR).all():
            raise FusionError("model availability precedes the full-window close")
        if not proposal_end.gt(proposal_start).all():
            raise FusionError("model interval is empty or reversed")
        if not proposal_end.eq(proposal_start + expected_core_duration).all():
            raise FusionError("model interval duration disagrees with its core length")
        if not proposal_available.ge(proposal_end).all():
            raise FusionError("model availability precedes its detected interval end")
        if semantic.loc[~structural].any():
            raise FusionError("semantic pass exists without structural pass")
        if not expected_directions.eq(proposals["direction"]).all():
            raise FusionError("model class and direction disagree")
        if not arithmetic_ok.all():
            raise FusionError("model core/confirmation index arithmetic drifted")
        if not window_arithmetic_ok.all() or not core_inside_window.all():
            raise FusionError("model window/core index arithmetic drifted")
        structural_contract = (
            proposals["core_length_bars"].isin(ALLOWED_CORE_LENGTHS)
            & proposals["confirmation_bars"].isin(ALLOWED_CONFIRMATION_BARS)
        )
        if not structural.eq(structural_contract).all():
            raise FusionError("structural-pass flag disagrees with frozen geometry")

    if fused.empty:
        return

    pine_by_id = pine_signals.set_index("signal_id", drop=False)
    proposal_by_id = proposals.set_index("detection_id", drop=False)
    eligibility_by_id = pd.Series(
        eligible.to_numpy(dtype=bool),
        index=proposals["detection_id"],
    )
    missing_pine = set(fused["pine_signal_id"]) - set(pine_by_id.index)
    missing_model = set(fused["model_detection_id"]) - set(proposal_by_id.index)
    if missing_pine or missing_model:
        raise FusionError(
            f"fused references are missing: pine={sorted(missing_pine)} "
            f"model={sorted(missing_model)}"
        )

    for row in fused.to_dict(orient="records"):
        pine = pine_by_id.loc[row["pine_signal_id"]]
        model = proposal_by_id.loc[row["model_detection_id"]]
        if not bool(eligibility_by_id.loc[row["model_detection_id"]]):
            raise FusionError(f"fused row references a proposal rejected at {stage!r}")
        if str(pine["symbol"]) != str(model["symbol"]) or str(pine["symbol"]) != str(
            row["symbol"]
        ):
            raise FusionError("fused row crosses symbols")
        if str(pine["direction"]) != str(model["direction"]) or str(
            pine["direction"]
        ) != str(row["direction"]):
            raise FusionError("fused row crosses directions")

        pine_start = _utc(pine["interval_start"])
        pine_end = _utc(pine["interval_end"])
        model_start = _utc(model["interval_start"])
        model_end = _utc(model["interval_end"])
        overlap_start = max(pine_start, model_start)
        overlap_end = min(pine_end, model_end)
        if overlap_start > overlap_end:
            raise FusionError("fused row references non-overlapping intervals")

        pine_available = _utc(pine["available_at"])
        model_available = _utc(model["available_at"])
        combined = max(pine_available, model_available)
        expected_delay = float((combined - pine_available) / BAR)
        if _utc(row["combined_available_at"]) != combined:
            raise FusionError("combined availability is not the later input availability")
        if _utc(row["overlap_start"]) != overlap_start or _utc(
            row["overlap_end"]
        ) != overlap_end:
            raise FusionError("recorded overlap differs from source intervals")
        if not np.isclose(
            float(row["overlap_seconds"]),
            float((overlap_end - overlap_start).total_seconds()),
        ):
            raise FusionError("recorded overlap duration is inconsistent")
        if not np.isclose(
            float(row["availability_delay_bars_from_pine"]), expected_delay
        ):
            raise FusionError("recorded availability delay is inconsistent")


def build_summary(
    *,
    raw: pd.DataFrame,
    pine_signals: pd.DataFrame,
    proposals: pd.DataFrame,
    fused: pd.DataFrame,
    inference_stats: Mapping[str, int],
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    device: str,
) -> dict[str, Any]:
    """Describe the integration without turning proposal counts into PnL claims."""

    structural = (
        int(proposals["structural_pass"].astype(bool).sum())
        if not proposals.empty
        else 0
    )
    semantic = (
        int(proposals["semantic_pass"].astype(bool).sum())
        if not proposals.empty
        else 0
    )
    return {
        "pipeline": "V12F Pine + Grade-A full40 native-1280 YOLO + causal semantic gate",
        "symbol": symbol,
        "timeframe": "15m",
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "pine": {
            "sha256": PINE_SHA256,
            "signals": len(pine_signals),
        },
        "detector": {
            "model_key": MODEL_KEY,
            "weights_path": str(MODEL_PATH.relative_to(PROJECT)),
            "weights_sha256": MODEL_SHA256,
            "renderer_sha256": RENDERER_SHA256,
            "semantic_gate_sha256": SEMANTIC_GATE_SHA256,
            "morphology_prereg_sha256": GATE_PREREG_SHA256,
            "class_names": CLASS_NAMES,
            "window_lengths": list(WINDOW_LENGTHS),
            "core_lengths": sorted(ALLOWED_CORE_LENGTHS),
            "confirmation_bars": sorted(ALLOWED_CONFIRMATION_BARS),
            "confidence": MODEL_CONFIDENCE,
            "nms_iou": MODEL_NMS_IOU,
            "imgsz": MODEL_IMGSZ,
            "device": device,
            "raw_proposals": len(proposals),
            "structural_proposals": structural,
            "semantic_proposals": semantic,
            "inference_stats": dict(inference_stats),
            "pine_conditioned_scan": True,
            "full_universe_scan": False,
        },
        "fusion": {
            "matching": "same symbol + same direction + closed interval overlap",
            "exact_same_bar_required": False,
            "selection": "earliest model available_at, then highest confidence",
            "combined_available_at": "max(pine.available_at, model.available_at)",
            "fused_signals": len(fused),
            "unmatched_pine_signals": len(pine_signals) - len(fused),
            "non_exact_start_matches": (
                int((~fused["exact_same_start_bar"].astype(bool)).sum())
                if not fused.empty
                else 0
            ),
        },
        "data": {
            "rows_read": len(raw),
            "first_bar": _utc(raw["open_time"].iloc[0]).isoformat(),
            "last_bar": _utc(raw["open_time"].iloc[-1]).isoformat(),
            "holdout_rows_read": 0,
        },
        "runtime_versions": {
            name: _version(name)
            for name in ("numpy", "pandas", "torch", "ultralytics", "opencv-python")
        },
        "economic_metrics": {
            "applicable": False,
            "reason": (
                "This step validates detector/fusion identities and causal timing only; "
                "a delayed-entry risk contract has not been frozen."
            ),
        },
        "safety": {
            "preholdout_only": True,
            "completed_history_detector": True,
            "tip_signal": False,
            "loads_lightgbm": False,
            "calculates_fused_pnl": False,
            "changes_active_or_frozen": False,
            "writes_forward_log": False,
            "promotes_or_deploys": False,
            "places_orders": False,
            "training_eligible": False,
            "production_eligible": False,
        },
    }


def run(
    *,
    input_path: Path,
    output_dir: Path,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    requested_device: str,
    batch_size: int,
) -> dict[str, Any]:
    """Execute one bounded detector/fusion run and write inspectable ledgers."""

    validate_frozen_pine()
    validate_period(start, end)
    gates = validate_frozen_detector()
    existing = [output_dir / name for name in OUTPUT_NAMES if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing outputs: {existing}")
    raw = load_development_frame(
        input_path,
        safe_end=end,
        holdout_start=HOLDOUT_START,
    )
    validate_15m_frame(raw)
    featured = build_v12_feature_frame(raw)
    pine_signals = build_signal_ledger(
        featured,
        symbol=symbol,
        start=start,
        end=end,
    )
    device = choose_device(requested_device)
    model = load_frozen_model()
    proposals, inference_stats = infer_model_proposals(
        raw,
        pine_signals,
        symbol=symbol,
        model=model,
        gates=gates,
        device=device,
        batch_size=batch_size,
    )
    fused = fuse_signal_intervals(pine_signals, proposals)
    validate_fusion_ledgers(pine_signals, proposals, fused)
    summary = build_summary(
        raw=raw,
        pine_signals=pine_signals,
        proposals=proposals,
        fused=fused,
        inference_stats=inference_stats,
        symbol=symbol,
        start=start,
        end=end,
        device=device,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    pine_signals.to_csv(output_dir / "pine_signals.csv", index=False)
    proposals.to_csv(output_dir / "model_proposals.csv", index=False)
    fused.to_csv(output_dir / "fused_signals.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", type=Path, required=True, help="15m OHLCV CSV")
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--symbol", default="ETH_USDT_SWAP")
    result.add_argument("--start", required=True, help="inclusive UTC timestamp")
    result.add_argument("--end-exclusive", required=True, help="exclusive UTC timestamp")
    result.add_argument("--device", default="auto", help="auto, cpu, mps, or CUDA index")
    result.add_argument("--batch-size", type=int, default=8)
    return result


def main() -> int:
    args = parser().parse_args()
    summary = run(
        input_path=args.input,
        output_dir=args.output_dir,
        symbol=str(args.symbol),
        start=_utc(args.start),
        end=_utc(args.end_exclusive),
        requested_device=str(args.device),
        batch_size=int(args.batch_size),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
