"""Read-only, pre-holdout replay for the latest research L1/L2 pair.

The console is a serving smoke, not an evaluation. It selects one immutable
event from the latest causal L2 feature dataset, reconstructs the exact YOLO
input pixels from the hash-pinned snapshot, runs the Grade-A full40 native-1280
detector, and then scores the same event with the tune-selected side-specific
LightGBM model.

The feature-addition L2 experiment was rejected and remains research-only. No
live bars, holdout rows, ACTIVE bundle, forward ledger, or execution module is
read or changed. Heavy runtimes are imported lazily inside the worker process.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from yoyo.contracts.holdout import HOLDOUT_START, HOLDOUT_START_ISO, assert_pre_holdout
from yoyo.contracts.protocol import file_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DETECTOR_REL = Path(
    "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/"
    "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt"
)
DEFAULT_L1_CONTRACT_REL = Path(
    "experiments/active/exp-15m-ma-launch-l2-global-context-v1/preregistration.json"
)
DEFAULT_L2_PREREGISTRATION_REL = Path(
    "experiments/active/exp-15m-ma-launch-l2-feature-addition-v1/preregistration.json"
)
DEFAULT_L2_SELECTION_REL = Path(
    "experiments/active/exp-15m-ma-launch-l2-feature-addition-v1/results/selection_receipt.json"
)
DEFAULT_L2_TRAINING_REL = Path(
    "experiments/active/exp-15m-ma-launch-l2-feature-addition-v1/results/training_receipt.json"
)
DEFAULT_L2_FEATURE_RECEIPT_REL = Path(
    "experiments/active/exp-15m-ma-launch-l2-feature-addition-v1/results/feature_dataset_receipt.json"
)
ADAPTER_ID = "latest-research-preholdout-replay"
L2_EXPERIMENT_ID = "exp-15m-ma-launch-l2-feature-addition-v1"
EXPECTED_L1_KEY = "grade_a8k_neg24k_full40_1280"
EXPECTED_L2_PROTOCOL = "15m_grade_a_l2_causal_feature_addition_v1"
EXPECTED_SIDES = ("long", "short")

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}_USDT_SWAP$")


class ReplayError(RuntimeError):
    """An artifact or replay input cannot prove the frozen safety contract."""


@dataclass(frozen=True)
class ReplayEvent:
    """One L1/L2-aligned event selected from the immutable feature ledger."""

    symbol: str
    row: Any
    image: Any
    window: Any
    transform: Any
    image_path: Path
    snapshot_path: Path
    dataset_max_time: str


def normalize_symbol(raw: str) -> str:
    """Normalize lenient UI symbols to one SWAP identifier; reject path syntax."""
    symbol = str(raw or "").strip().upper().replace("-", "_").replace("/", "_")
    if "_" not in symbol:
        for suffix in ("USDTSWAP", "USDT"):
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                symbol = symbol[: -len(suffix)]
                break
    if symbol.endswith("_USDT_SWAP"):
        normalized = symbol
    elif symbol.endswith("_USDT"):
        normalized = f"{symbol}_SWAP"
    else:
        normalized = f"{symbol}_USDT_SWAP"
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ReplayError(f"unsupported symbol: {raw!r}")
    return normalized


def _configured_path(env_name: str, default: Path, *, project_root: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return project_root / default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else project_root / path


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise ReplayError(f"{label} is missing: {path}")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _require_file(path, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplayError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReplayError(f"{label} must be a JSON object")
    return payload


def _project_path(project_root: Path, value: object) -> Path:
    path = Path(str(value).replace("\\", "/"))
    return path if path.is_absolute() else project_root / path


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _parse_utc(value: object, label: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayError(f"{label} is not an ISO-8601 instant: {value!r}") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ReplayError(f"{label} has no timezone: {value!r}")
    return moment


def detector_path(project_root: Path = PROJECT_ROOT) -> Path:
    """Return the explicit console L1; never consult owner_best or ACTIVE."""
    return _require_file(
        _configured_path(
            "FABLE_SIGNAL_CONSOLE_YOLO_WEIGHTS",
            DEFAULT_DETECTOR_REL,
            project_root=project_root,
        ),
        "console detector",
    )


def l1_contract_path(project_root: Path = PROJECT_ROOT) -> Path:
    return _require_file(
        _configured_path(
            "FABLE_SIGNAL_CONSOLE_L1_CONTRACT",
            DEFAULT_L1_CONTRACT_REL,
            project_root=project_root,
        ),
        "L1 research contract",
    )


def l2_preregistration_path(project_root: Path = PROJECT_ROOT) -> Path:
    return _require_file(
        _configured_path(
            "FABLE_SIGNAL_CONSOLE_L2_PREREGISTRATION",
            DEFAULT_L2_PREREGISTRATION_REL,
            project_root=project_root,
        ),
        "L2 preregistration",
    )


def l2_selection_path(project_root: Path = PROJECT_ROOT) -> Path:
    return _require_file(
        _configured_path(
            "FABLE_SIGNAL_CONSOLE_L2_SELECTION",
            DEFAULT_L2_SELECTION_REL,
            project_root=project_root,
        ),
        "L2 selection receipt",
    )


def l2_training_path(project_root: Path = PROJECT_ROOT) -> Path:
    return _require_file(
        _configured_path(
            "FABLE_SIGNAL_CONSOLE_L2_TRAINING",
            DEFAULT_L2_TRAINING_REL,
            project_root=project_root,
        ),
        "L2 training receipt",
    )


def l2_feature_receipt_path(project_root: Path = PROJECT_ROOT) -> Path:
    return _require_file(
        _configured_path(
            "FABLE_SIGNAL_CONSOLE_L2_FEATURE_RECEIPT",
            DEFAULT_L2_FEATURE_RECEIPT_REL,
            project_root=project_root,
        ),
        "L2 feature receipt",
    )


def _selected_record(selection: Mapping[str, Any], side: str) -> dict[str, Any]:
    try:
        side_selection = selection["selections"][side]
        arm = str(side_selection["selected_arm"])
        records = side_selection["arms"]
    except (KeyError, TypeError) as exc:
        raise ReplayError(f"L2 selection is missing side {side}") from exc
    record = next((item for item in records if str(item.get("arm")) == arm), None)
    if not isinstance(record, dict):
        raise ReplayError(f"L2 selection has no selected record for {side}/{arm}")
    if list(record.get("feature_columns") or []) != list(
        side_selection.get("feature_columns") or []
    ):
        raise ReplayError(f"L2 selected feature list drifted for {side}/{arm}")
    return record


def load_latest_bundle(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Verify the exact latest-research artifacts without importing ML runtimes."""
    weights = detector_path(project_root)
    l1_path = l1_contract_path(project_root)
    l2_prereg_path = l2_preregistration_path(project_root)
    selection_path = l2_selection_path(project_root)
    training_path = l2_training_path(project_root)
    feature_path = l2_feature_receipt_path(project_root)

    l1_contract = _read_json(l1_path, "L1 research contract")
    l2_prereg = _read_json(l2_prereg_path, "L2 preregistration")
    selection = _read_json(selection_path, "L2 selection receipt")
    training = _read_json(training_path, "L2 training receipt")
    feature_receipt = _read_json(feature_path, "L2 feature receipt")

    l1 = l1_contract.get("l1") or {}
    declared_weights = _project_path(project_root, l1.get("weights", ""))
    if str(l1.get("key")) != EXPECTED_L1_KEY:
        raise ReplayError("L1 contract does not select the latest Grade-A full40 model")
    if declared_weights.resolve() != weights.resolve():
        raise ReplayError("configured L1 weights differ from the L1 research contract")
    if file_sha256(weights) != str(l1.get("weights_sha256", "")):
        raise ReplayError("L1 weights hash differs from the L1 research contract")
    if (
        int(l1.get("imgsz", 0)) != 1280
        or float(l1.get("confidence", -1)) != 0.25
        or float(l1.get("nms_iou", -1)) != 0.70
    ):
        raise ReplayError("L1 native-1280 inference contract drifted")
    if int(l1.get("future_bars_rendered_into_inference", -1)) != 0:
        raise ReplayError("L1 contract permits future pixels")

    if str(selection.get("experiment_id")) != L2_EXPERIMENT_ID:
        raise ReplayError("unexpected L2 selection experiment")
    if str(selection.get("protocol")) != EXPECTED_L2_PROTOCOL:
        raise ReplayError("unexpected L2 selection protocol")
    if int(selection.get("holdout_rows_read", -1)) != 0:
        raise ReplayError("L2 selection receipt consumed holdout")
    if selection.get("production_eligible") is not False or selection.get("promoted") is not False:
        raise ReplayError("L2 research-only selection flags drifted")
    if file_sha256(feature_path) != str(selection.get("feature_dataset_receipt_sha256", "")):
        raise ReplayError("L2 feature receipt hash differs from selection")

    if file_sha256(selection_path) != str(training.get("selection_receipt_sha256", "")):
        raise ReplayError("L2 selection receipt hash differs from training receipt")
    if str(training.get("decision")) != "reject":
        raise ReplayError("latest L2 experiment decision is no longer the recorded reject")
    if int(training.get("holdout_rows_read", -1)) != 0:
        raise ReplayError("L2 training receipt consumed holdout")
    if training.get("production_eligible") is not False or training.get("promoted") is not False:
        raise ReplayError("L2 training safety flags drifted")
    if training.get("active_or_frozen_changed") is not False:
        raise ReplayError("L2 training receipt reports a production pointer mutation")

    if int(feature_receipt.get("holdout_rows_read", -1)) != 0:
        raise ReplayError("L2 feature receipt consumed holdout")
    if int(feature_receipt.get("future_rows_in_features", -1)) != 0:
        raise ReplayError("L2 feature receipt reports future feature rows")
    dataset = _require_file(
        _project_path(project_root, feature_receipt.get("dataset_path", "")),
        "L2 feature dataset",
    )
    if file_sha256(dataset) != str(feature_receipt.get("dataset_sha256", "")):
        raise ReplayError("L2 feature dataset hash differs from its receipt")
    if str(feature_receipt.get("source_dataset_sha256")) != str(
        (l2_prereg.get("source") or {}).get("dataset_sha256")
    ):
        raise ReplayError("L2 source dataset lineage drifted")

    source = l2_prereg.get("source") or {}
    snapshot_receipt_path = _require_file(
        _project_path(project_root, source.get("snapshot_receipt", "")),
        "L1 snapshot receipt",
    )
    if file_sha256(snapshot_receipt_path) != str(source.get("snapshot_receipt_sha256", "")):
        raise ReplayError("L1 snapshot receipt hash differs from L2 preregistration")
    snapshot_receipt = _read_json(snapshot_receipt_path, "L1 snapshot receipt")
    if int(snapshot_receipt.get("holdout_rows_read", -1)) != 0:
        raise ReplayError("L1 snapshot receipt consumed holdout")
    snapshot_end = _parse_utc(snapshot_receipt.get("snapshot_end_exclusive"), "snapshot end")
    if snapshot_end > HOLDOUT_START:
        raise ReplayError("L1 snapshot extends beyond the frozen holdout boundary")
    snapshot_base = _project_path(project_root, source.get("snapshot_base_dir", ""))

    selected: dict[str, dict[str, Any]] = {}
    for side in EXPECTED_SIDES:
        record = _selected_record(selection, side)
        model = _require_file(
            _project_path(project_root, record.get("model_path", "")),
            f"{side} L2 model",
        )
        tune_scores = _require_file(
            _project_path(project_root, record.get("tune_scores_path", "")),
            f"{side} tune scores",
        )
        if file_sha256(model) != str(record.get("model_sha256", "")):
            raise ReplayError(f"{side} L2 model hash drifted")
        if file_sha256(tune_scores) != str(record.get("tune_scores_sha256", "")):
            raise ReplayError(f"{side} tune-score hash drifted")
        selected[side] = {**record, "model": model, "tune_scores": tune_scores}

    return {
        "weights": weights,
        "l1_contract_path": l1_path,
        "l1": l1,
        "l2_preregistration_path": l2_prereg_path,
        "selection_path": selection_path,
        "selection": selection,
        "training_path": training_path,
        "training": training,
        "feature_receipt_path": feature_path,
        "feature_receipt": feature_receipt,
        "dataset": dataset,
        "snapshot_receipt_path": snapshot_receipt_path,
        "snapshot_receipt": snapshot_receipt,
        "snapshot_base": snapshot_base,
        "selected": selected,
    }


def describe_replay(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Describe the exact replaceable artifacts without loading model runtimes."""
    bundle = load_latest_bundle(project_root)
    side_status: dict[str, Any] = {}
    for side, record in bundle["selected"].items():
        side_status[side] = {
            "arm": record["arm"],
            "feature_count": int(record["feature_count"]),
            "best_iteration": int(record["best_iteration"]),
            "threshold": float(record["tune_q90_threshold"]),
            "model_path": _display_path(record["model"], project_root),
            "model_sha256": record["model_sha256"],
        }
    return {
        "id": ADAPTER_ID,
        "label": "Latest research L1 + side-specific L2 (2026-09-02)",
        "replaceable": True,
        "data_mode": "pre_holdout_aligned_replay",
        "research_decision": "REJECT",
        "production_eligible": False,
        "l1": {
            "key": bundle["l1"]["key"],
            "label": "Grade-A full40 native-1280",
            "weights_path": _display_path(bundle["weights"], project_root),
            "weights_sha256": bundle["l1"]["weights_sha256"],
            "imgsz": int(bundle["l1"]["imgsz"]),
            "confidence": float(bundle["l1"]["confidence"]),
            "nms_iou": float(bundle["l1"]["nms_iou"]),
            "contract_path": _display_path(bundle["l1_contract_path"], project_root),
        },
        "l2": {
            "label": "Feature-addition tune selection, split by side",
            "experiment_id": L2_EXPERIMENT_ID,
            "decision": "reject",
            "selection_path": _display_path(bundle["selection_path"], project_root),
            "selection_sha256": file_sha256(bundle["selection_path"]),
            "dataset_path": _display_path(bundle["dataset"], project_root),
            "dataset_sha256": bundle["feature_receipt"]["dataset_sha256"],
            "rows": int(bundle["feature_receipt"]["rows"]),
            "sides": side_status,
        },
        "switches": {
            "adapter": "FABLE_SIGNAL_CONSOLE_ADAPTER",
            "l1_weights": "FABLE_SIGNAL_CONSOLE_YOLO_WEIGHTS",
            "l1_contract": "FABLE_SIGNAL_CONSOLE_L1_CONTRACT",
            "l2_preregistration": "FABLE_SIGNAL_CONSOLE_L2_PREREGISTRATION",
            "l2_selection": "FABLE_SIGNAL_CONSOLE_L2_SELECTION",
            "l2_training": "FABLE_SIGNAL_CONSOLE_L2_TRAINING",
            "l2_feature_receipt": "FABLE_SIGNAL_CONSOLE_L2_FEATURE_RECEIPT",
        },
        "safety": {
            "holdout_boundary": HOLDOUT_START_ISO,
            "holdout_rows_opened": 0,
            "live_market_data": False,
            "writes_forward_log": False,
            "touches_active_bundle": False,
            "execution_eligible": False,
            "same_event_l1_l2": True,
            "performance_evidence": False,
        },
    }


def _runtime_image_path(symbol: str) -> Path:
    configured = os.environ.get("FABLE_SIGNAL_CONSOLE_RUNTIME_DIR", "").strip()
    directory = (
        Path(configured).expanduser()
        if configured
        else Path(tempfile.gettempdir()) / "fable-signal-console-model-replay"
    )
    return directory / f"{normalize_symbol(symbol)}.png"


def replay_image_output_path(symbol: str) -> Path:
    path = _runtime_image_path(symbol)
    if not path.is_file():
        raise ReplayError(f"replay image is not ready for {normalize_symbol(symbol)}")
    return path


def _load_event(symbol: str, bundle: Mapping[str, Any]) -> tuple[Any, str]:
    import pandas as pd  # noqa: PLC0415

    feature_columns = sorted(
        {
            column
            for record in bundle["selected"].values()
            for column in record["feature_columns"]
        }
    )
    identifiers = [
        "episode_id",
        "symbol",
        "side",
        "class_id",
        "split",
        "available_at",
        "signal_time",
        "window_len",
        "window_start_i",
        "window_end_i",
        "prediction_cx_norm",
        "prediction_cy_norm",
        "prediction_w_norm",
        "prediction_h_norm",
        "l1_confidence",
        "input_pixel_sha256",
        "dependency_representative",
    ]
    frame = pd.read_csv(bundle["dataset"], usecols=[*identifiers, *feature_columns])
    if len(frame) != int(bundle["feature_receipt"]["rows"]):
        raise ReplayError("L2 feature dataset row count differs from its receipt")
    parsed_times = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    if parsed_times.isna().any():
        raise ReplayError("L2 feature dataset contains invalid available_at values")
    max_time = parsed_times.max().isoformat()
    assert_pre_holdout(max_time, what="console L2 replay dataset maximum available_at")

    representative = frame["dependency_representative"].astype(str).str.lower().eq("true")
    candidates = frame.loc[frame["symbol"].eq(symbol) & representative].copy()
    if candidates.empty:
        candidates = frame.loc[frame["symbol"].eq(symbol)].copy()
    if candidates.empty:
        raise ReplayError(f"latest L2 dataset has no replay event for {symbol}")
    candidates["_available_at"] = pd.to_datetime(candidates["available_at"], utc=True)
    candidates = candidates.sort_values(["_available_at", "episode_id"], kind="stable")
    row = candidates.iloc[-1]
    assert_pre_holdout(row["_available_at"], what=f"console replay event for {symbol}")
    side = str(row["side"]).lower()
    if side not in bundle["selected"]:
        raise ReplayError(f"unsupported L2 side in replay event: {side!r}")
    return row, max_time


def _render_exact_input(
    symbol: str,
    row: Any,
    bundle: Mapping[str, Any],
) -> tuple[Any, Any, Any, Path, Path]:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: PLC0415
    from yoyo.layers.l1_detection.render import render_chart  # noqa: PLC0415

    snapshot_record = next(
        (
            item
            for item in bundle["snapshot_receipt"].get("files", [])
            if str(item.get("symbol")) == symbol
        ),
        None,
    )
    if not isinstance(snapshot_record, dict):
        raise ReplayError(f"snapshot receipt has no file for {symbol}")
    snapshot_path = _require_file(
        bundle["snapshot_base"] / str(snapshot_record["snapshot_path"]),
        f"{symbol} frozen snapshot",
    )
    if file_sha256(snapshot_path) != str(snapshot_record.get("sha256", "")):
        raise ReplayError(f"{symbol} frozen snapshot hash drifted")
    frame = pd.read_csv(snapshot_path)
    required = ["open_time", "open", "high", "low", "close", "volume"]
    if any(column not in frame.columns for column in required):
        raise ReplayError(f"{symbol} frozen snapshot schema drifted")
    frame = frame[required].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.isna().any().any():
        raise ReplayError(f"{symbol} frozen snapshot has invalid rows")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    assert_pre_holdout(frame["open_time"].iloc[-1], what=f"{symbol} snapshot final bar")
    enriched = add_mas(frame)
    start_i, end_i = int(row["window_start_i"]), int(row["window_end_i"])
    if start_i < 0 or end_i >= len(enriched) or end_i - start_i + 1 != int(row["window_len"]):
        raise ReplayError(f"{symbol} replay window indices drifted")
    window = enriched.iloc[start_i : end_i + 1]
    if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
        raise ReplayError(f"{symbol} replay window has incomplete moving averages")
    image, transform = render_chart(window, out_path=None)
    pixel_hash = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()
    if pixel_hash != str(row["input_pixel_sha256"]):
        raise ReplayError(f"{symbol} exact YOLO input pixel hash drifted")

    output = _runtime_image_path(symbol)
    output.parent.mkdir(parents=True, exist_ok=True)
    building = output.with_name(f"{output.stem}.building.png")
    if not cv2.imwrite(str(building), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise ReplayError(f"cannot write replay image for {symbol}")
    building.replace(output)
    return image, window, transform, output, snapshot_path


def _resolve_event(symbol: str, bundle: Mapping[str, Any]) -> ReplayEvent:
    row, max_time = _load_event(symbol, bundle)
    image, window, transform, image_path, snapshot_path = _render_exact_input(symbol, row, bundle)
    return ReplayEvent(
        symbol=symbol,
        row=row,
        image=image,
        window=window,
        transform=transform,
        image_path=image_path,
        snapshot_path=snapshot_path,
        dataset_max_time=max_time,
    )


def _chart_box_geometry(box: Mapping[str, Any], transform: Any) -> dict[str, float]:
    """Invert exact-render pixels into chart logical/price coordinates.

    Inputs are the detector's normalized ``xywh`` coordinates and the causal
    render transform for the same input window. The result lets a display-only
    interactive chart follow pan, zoom, and resize without changing inference.
    """
    cx, cy, width, height = (float(value) for value in box["xywhn"])
    x0 = (cx - width / 2) * transform.width
    x1 = (cx + width / 2) * transform.width
    y0 = (cy - height / 2) * transform.height
    y1 = (cy + height / 2) * transform.height
    logical_span = max(transform.n_bars - 1, 1)
    price_span = transform.price_max - transform.price_min

    def logical_at(pixel_x: float) -> float:
        return (pixel_x - transform.left) / transform.plot_w * logical_span

    def price_at(pixel_y: float) -> float:
        return transform.price_max - (pixel_y - transform.top) / transform.plot_h * price_span

    return {
        "logical_from": logical_at(x0),
        "logical_to": logical_at(x1),
        "price_top": price_at(y0),
        "price_bottom": price_at(y1),
    }


def _replay_chart_payload(event: ReplayEvent, boxes: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialize the verified replay window for a display-only interactive chart."""
    from yoyo.layers.l1_detection.data import ALL_MA_COLS  # noqa: PLC0415

    timestamps = [int(value.timestamp()) for value in event.window["open_time"]]
    candles = [
        {
            "time": timestamp,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for timestamp, row in zip(timestamps, event.window.itertuples(index=False))
    ]
    moving_averages = {
        column: [
            {"time": timestamp, "value": float(value)}
            for timestamp, value in zip(timestamps, event.window[column])
        ]
        for column in ALL_MA_COLS
    }
    chart_boxes = [
        {
            **_chart_box_geometry(box, event.transform),
            "confidence": float(box["confidence"]),
            "class_id": int(box["class_id"]),
            "class_name": str(box["class_name"]),
        }
        for box in boxes
    ]
    return {
        "mode": "verified_pre_holdout_replay",
        "display_only": True,
        "bar": "15m",
        "candles": candles,
        "moving_averages": moving_averages,
        "boxes": chart_boxes,
        "input_pixel_sha256": str(event.row["input_pixel_sha256"]),
        "window_bars": len(candles),
    }


def _run_detector(event: ReplayEvent, bundle: Mapping[str, Any]) -> dict[str, Any]:
    # Ultralytics must load before LightGBM on dual-libomp hosts.
    from ultralytics import YOLO  # noqa: PLC0415
    from yoyo.layers.l1_detection.candidates import resolve_predict_device  # noqa: PLC0415

    contract = bundle["l1"]
    model = YOLO(str(bundle["weights"]))
    result = model.predict(
        source=[event.image],
        imgsz=int(contract["imgsz"]),
        conf=float(contract["confidence"]),
        iou=float(contract["nms_iou"]),
        batch=1,
        device=resolve_predict_device(),
        verbose=False,
    )[0]
    boxes: list[dict[str, Any]] = []
    class_names = {0: "dense_long", 1: "dense_short"}
    if result.boxes is not None:
        for xywhn, confidence, class_id in zip(
            result.boxes.xywhn.cpu().numpy(),
            result.boxes.conf.cpu().numpy(),
            result.boxes.cls.cpu().numpy(),
        ):
            cid = int(class_id)
            boxes.append(
                {
                    "xywhn": [round(float(value), 6) for value in xywhn[:4]],
                    "confidence": round(float(confidence), 6),
                    "class_id": cid,
                    "class_name": class_names.get(cid, f"class_{cid}"),
                }
            )
    height, width = (int(value) for value in result.orig_shape)
    expected_class = int(event.row["class_id"])
    return {
        "model_key": contract["key"],
        "model_label": "Grade-A full40 native-1280",
        "weights_path": _display_path(bundle["weights"], PROJECT_ROOT),
        "box_count": len(boxes),
        "max_confidence": max((box["confidence"] for box in boxes), default=None),
        "boxes": boxes,
        "reproduced_expected_class": any(box["class_id"] == expected_class for box in boxes),
        "expected": {
            "class_id": expected_class,
            "class_name": class_names.get(expected_class, f"class_{expected_class}"),
            "confidence": float(event.row["l1_confidence"]),
            "xywhn": [
                float(event.row["prediction_cx_norm"]),
                float(event.row["prediction_cy_norm"]),
                float(event.row["prediction_w_norm"]),
                float(event.row["prediction_h_norm"]),
            ],
        },
        "input": {
            "episode_id": str(event.row["episode_id"]),
            "available_at": str(event.row["available_at"]),
            "split": str(event.row["split"]),
            "pixel_sha256": str(event.row["input_pixel_sha256"]),
            "png_sha256": file_sha256(event.image_path),
            "width": width,
            "height": height,
            "window_bars": int(event.row["window_len"]),
            "snapshot_path": _display_path(event.snapshot_path, PROJECT_ROOT),
        },
    }


def _run_judgment(event: ReplayEvent, bundle: Mapping[str, Any]) -> dict[str, Any]:
    # This import intentionally remains after the Ultralytics prediction.
    import lightgbm as lgb  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    side = str(event.row["side"]).lower()
    record = bundle["selected"][side]
    columns = list(record["feature_columns"])
    values = pd.DataFrame([event.row[columns].astype(float).to_dict()], columns=columns)
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ReplayError(f"{event.symbol} L2 replay features are not finite")
    booster = lgb.Booster(model_file=str(record["model"]))
    score = float(
        booster.predict(values, num_iteration=int(record["best_iteration"]))[0]
    )
    tune_scores = pd.read_csv(record["tune_scores"], usecols=["score"])["score"].to_numpy(
        dtype=float
    )
    if tune_scores.size == 0 or not np.isfinite(tune_scores).all():
        raise ReplayError(f"{side} tune scores are empty or non-finite")
    percentile = float(
        np.searchsorted(np.sort(tune_scores), score, side="right") / tune_scores.size
    )
    threshold = float(record["tune_q90_threshold"])
    return {
        "available": True,
        "side": side,
        "arm": str(record["arm"]),
        "model_label": f"{side.upper()} {record['arm']}",
        "model_path": _display_path(record["model"], PROJECT_ROOT),
        "score": score,
        "percentile": percentile,
        "threshold": threshold,
        "passed": bool(score >= threshold),
        "operator": ">=",
        "available_at": str(event.row["available_at"]),
        "signal_time": str(event.row["signal_time"]),
        "episode_id": str(event.row["episode_id"]),
        "split": str(event.row["split"]),
        "feature_count": len(columns),
        "best_iteration": int(record["best_iteration"]),
        "dataset_max_time": event.dataset_max_time,
        "experiment_decision": "reject",
        "production_eligible": False,
    }


def run_replay(raw_symbol: str) -> dict[str, Any]:
    """Run both latest research models on one exact, aligned frozen event."""
    started = time.monotonic()
    symbol = normalize_symbol(raw_symbol)
    bundle = load_latest_bundle()
    event = _resolve_event(symbol, bundle)
    detector = _run_detector(event, bundle)
    judgment = _run_judgment(event, bundle)
    replay_chart = _replay_chart_payload(event, detector["boxes"])

    if detector["reproduced_expected_class"] and judgment["passed"]:
        tone, title = "pass", "最新 L1 + L2 服务链路已跑通"
    elif detector["reproduced_expected_class"]:
        tone, title = "watch", "L1 已复现，L2 未过该侧冻结阈值"
    else:
        tone, title = "block", "L1 未复现已记录候选类别"

    return {
        "adapter_id": ADAPTER_ID,
        "symbol": symbol,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "detector": detector,
        "judgment": judgment,
        "replay_chart": replay_chart,
        "verdict": {
            "tone": tone,
            "title": title,
            "action": "同一 pre-holdout 事件服务烟测；实验结论 REJECT，不写 forward_log，不进入执行器。",
        },
        "safety": {
            "data_mode": "pre_holdout_aligned_replay",
            "same_event_l1_l2": True,
            "holdout_rows_opened": 0,
            "live_market_data": False,
            "writes_forward_log": False,
            "touches_active_bundle": False,
            "execution_eligible": False,
            "performance_evidence": False,
            "research_decision": "reject",
        },
    }
