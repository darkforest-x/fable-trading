#!/usr/bin/env python3
"""Replay the frozen Grade-A YOLO11s checkpoint over two months of OKX 15m data.

The fetch phase freezes confirmed candles for every currently live, eligible
OKX USDT perpetual.  The scan phase uses the training chart renderer and the
checkpoint's native 1280 inference contract, retaining raw boxes and mapped
completed-shape candidates.  This is a retrospective detector replay: it does
not score labels, tune thresholds, train, promote, deploy, or trade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR if (SCRIPT_DIR / "yoyo").is_dir() else Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.evaluation.grade_a_full40_training_input_renderer.data import ALL_MA_COLS, WARMUP_BARS, add_mas
from yoyo.evaluation.grade_a_full40_training_input_renderer.render import IMG_HEIGHT, IMG_WIDTH, ChartTransform, render_chart


EXPERIMENT_ID = "exp-15m-grade-a-full40-two-month-okx-20261013-v1"
MODEL_REL = Path(
    "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/"
    "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt"
)
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
TRAINING_MANIFEST_REL = Path("datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl")
TRAINING_MANIFEST_SHA256 = "22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2"
TRAINING_EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-neg24000-train1280-full40-v1"
RENDERER_SHA256 = "0962812feea57e0a666c4da62acea830cbbf53a3d66f6dbd722ae3e580ead3e7"
TRAINING_DATA_SHA256 = "bce9af28b00d1deb8346cafb19654e18d4987d42b3849b420a9739b5e9aeb265"
TRAINING_RENDERER_REL = Path("yoyo/evaluation/grade_a_full40_training_input_renderer/render.py")
TRAINING_DATA_REL = Path("yoyo/evaluation/grade_a_full40_training_input_renderer/data.py")
START_UTC = pd.Timestamp("2026-08-13T11:00:00Z")
END_UTC = pd.Timestamp("2026-10-13T11:00:00Z")
BAR_DELTA = pd.Timedelta(minutes=15)
WARMUP_FETCH_BARS = 300
WINDOW_LENGTHS = (18, 19)
CONFIDENCE = 0.25
NMS_IOU = 0.70
IMGSZ = 1280
ALLOWED_CORE_LENGTHS = (4, 5)
ALLOWED_CONFIRMATIONS = tuple(range(2, 10))
EVENT_GAP_BARS = 5
CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
DEFAULT_OUT = ROOT / "analysis" / "output" / "grade_a_two_month_okx_20261013_v1"


class ReplayError(RuntimeError):
    """Fail-closed on model, snapshot, or inference identity drift."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one sorted UTF-8 JSON receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    """Write a deterministic CSV with an explicit empty schema."""

    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(row) for row in rows], columns=list(columns)).to_csv(path, index=False)


def source_commit() -> str:
    """Return the checked-out commit, or the frozen fetch commit on a worker."""

    frozen = os.environ.get("GRADE_A_REPLAY_SOURCE_COMMIT", "")
    if len(frozen) == 40 and all(char in "0123456789abcdef" for char in frozen):
        return frozen
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception as exc:  # noqa: BLE001 - a worker bundle has no Git checkout
        raise ReplayError("source commit missing; set GRADE_A_REPLAY_SOURCE_COMMIT") from exc
    if len(value) != 40:
        raise ReplayError("could not resolve a 40-character source commit")
    return value


def verify_committed_sources() -> str:
    """Require the exact runner and preregistration bytes committed on main."""

    paths = [
        Path(__file__).resolve().relative_to(ROOT.resolve()),
        Path("experiments/active") / EXPERIMENT_ID / "preregistration.json",
        TRAINING_RENDERER_REL,
        TRAINING_DATA_REL,
        Path("yoyo/evaluation/grade_a_full40_training_input_renderer/__init__.py"),
        Path("yoyo/__init__.py"),
        Path("yoyo/evaluation/__init__.py"),
    ]
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    if branch != "main":
        raise ReplayError("fetch is permitted only from main")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise ReplayError("runner and preregistration must be committed before fetching:\n%s" % dirty)
    for path in paths:
        tracked = subprocess.call(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if tracked != 0:
            raise ReplayError("untracked scan source: %s" % path)
    return source_commit()


def verify_sources(require_training_assets: bool = True) -> Dict[str, Any]:
    """Check the immutable checkpoint and the renderer used for training parity."""

    weights = ROOT / MODEL_REL
    renderer = ROOT / TRAINING_RENDERER_REL
    data_module = ROOT / TRAINING_DATA_REL
    training_manifest = ROOT / TRAINING_MANIFEST_REL
    if not renderer.is_file():
        raise ReplayError("training renderer is unavailable")
    renderer_sha = sha256_file(renderer)
    if renderer_sha != RENDERER_SHA256:
        raise ReplayError("training renderer identity mismatch")
    data_sha = sha256_file(data_module)
    if data_sha != TRAINING_DATA_SHA256:
        raise ReplayError("training MA calculation module identity mismatch")
    if weights.is_file():
        if sha256_file(weights) != MODEL_SHA256:
            raise ReplayError("Grade-A full40 native-1280 checkpoint identity mismatch")
    elif require_training_assets:
        raise ReplayError("Grade-A checkpoint is unavailable in the repository")
    if training_manifest.is_file():
        if sha256_file(training_manifest) != TRAINING_MANIFEST_SHA256:
            raise ReplayError("Grade-A training manifest identity mismatch")
    elif require_training_assets:
        raise ReplayError("Grade-A training manifest is unavailable in the repository")
    return {
        "weights_path": MODEL_REL.as_posix(),
        "weights_sha256": MODEL_SHA256,
        "training_manifest_path": TRAINING_MANIFEST_REL.as_posix(),
        "training_manifest_sha256": TRAINING_MANIFEST_SHA256,
        "training_experiment_id": TRAINING_EXPERIMENT_ID,
        "renderer_path": TRAINING_RENDERER_REL.as_posix(),
        "renderer_sha256": renderer_sha,
        "data_module_sha256": data_sha,
    }


def fetch_phase(out: Path, workers: int) -> Dict[str, Any]:
    """Freeze all eligible live OKX USDT-swap 15m candles over the fixed interval."""

    if out.exists():
        raise FileExistsError("refusing to overwrite replay output: %s" % out)
    immutable = verify_sources(require_training_assets=True)
    committed_source = verify_committed_sources()
    # These repository readers own OKX throttling and the frozen live-universe rules.
    from scripts import scan_15m_ma_launch_t3_daily_movers as common

    started = time.perf_counter()
    snapshot_dir = out / "kline_snapshot"
    snapshot_dir.mkdir(parents=True)
    ticker_payload = common._request(common.TICKERS_URL)  # noqa: SLF001 - shared repository reader
    instruments_payload = common._request(common.INSTRUMENTS_URL)  # noqa: SLF001
    instruments = common.eligible_instruments(
        list(ticker_payload.get("data") or []),
        list(instruments_payload.get("data") or []),
    )
    if not instruments:
        raise ReplayError("OKX returned an empty eligible live USDT-swap universe")

    fetch_start = START_UTC - WARMUP_FETCH_BARS * BAR_DELTA
    rows: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    print("eligible live OKX USDT swaps: %d" % len(instruments), flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(common.fetch_15m_frame, inst_id, start=fetch_start, end=END_UTC): inst_id
            for inst_id in instruments
        }
        for completed, future in enumerate(as_completed(futures), 1):
            inst_id = futures[future]
            returned_id, frame, page_count, error = future.result()
            if returned_id != inst_id:
                error = "instrument_identity_mismatch"
            filename = inst_id.replace("-", "_") + ".csv"
            if error:
                failures.append({"inst_id": inst_id, "error": error, "api_rows": str(page_count)})
                print("fetch failed %s: %s" % (inst_id, error), flush=True)
            else:
                frame = frame.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
                path = snapshot_dir / filename
                frame.to_csv(path, index=False)
                times = pd.to_datetime(frame["open_time"], utc=True) if len(frame) else pd.Series([], dtype="datetime64[ns, UTC]")
                in_window = int(((times >= START_UTC) & (times < END_UTC)).sum()) if len(times) else 0
                gap_count = common.frame_gap_count(frame)
                item = {
                    "inst_id": inst_id,
                    "symbol": inst_id.replace("-", "_"),
                    "filename": filename,
                    "sha256": sha256_file(path),
                    "rows_including_warmup": int(len(frame)),
                    "in_window_bars": in_window,
                    "first_open_time": times.iloc[0].isoformat() if len(times) else None,
                    "last_open_time": times.iloc[-1].isoformat() if len(times) else None,
                    "gap_count_including_warmup": gap_count,
                    "api_rows_received": int(page_count),
                }
                snapshots.append(item)
                rows.append({key: value for key, value in item.items() if key != "sha256"})
            if completed % 20 == 0 or completed == len(instruments):
                print("fetch [%d/%d] usable=%d failed=%d" % (completed, len(instruments), len(snapshots), len(failures)), flush=True)

    universe_payload = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "eligible_instruments": instruments,
        "ticker_payload": ticker_payload,
        "instrument_payload": instruments_payload,
    }
    write_json(out / "exchange_universe.json", universe_payload)
    write_csv(
        out / "fetch_symbols.csv",
        rows,
        ("inst_id", "symbol", "filename", "rows_including_warmup", "in_window_bars", "first_open_time", "last_open_time", "gap_count_including_warmup", "api_rows_received"),
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "source_commit": committed_source,
        "model_identity": immutable,
        "exchange": "OKX",
        "instrument_scope": "all currently live eligible crypto USDT swaps with positive current tickers; repository BLOCKED/STOCKISH exclusions",
        "bar": "15m",
        "interval_start_inclusive": START_UTC.isoformat(),
        "interval_end_exclusive": END_UTC.isoformat(),
        "warmup_start_inclusive": fetch_start.isoformat(),
        "warmup_bars_requested": WARMUP_FETCH_BARS,
        "expected_in_window_bars_per_full_history_instrument": 5856,
        "eligible_instruments": len(instruments),
        "usable_instruments": len(snapshots),
        "failed_instruments": failures,
        "snapshot_files": snapshots,
        "universe_file": "exchange_universe.json",
        "universe_sha256": sha256_file(out / "exchange_universe.json"),
        "symbols_file": "fetch_symbols.csv",
        "symbols_sha256": sha256_file(out / "fetch_symbols.csv"),
        "fetch_wall_seconds": round(time.perf_counter() - started, 3),
        "canonical_data_written": False,
        "training_or_tuning": False,
        "promotion_or_deployment": False,
        "orders_placed": False,
    }
    write_json(out / "fetch_receipt.json", payload)
    print("fetch complete: %d/%d instruments, %.1fs" % (len(snapshots), len(instruments), payload["fetch_wall_seconds"]), flush=True)
    return payload


def load_snapshot(snapshot_dir: Path) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Load and hash-check the frozen per-instrument candle snapshot."""

    receipt_path = snapshot_dir / "fetch_receipt.json"
    if not receipt_path.is_file():
        raise ReplayError("missing fetch_receipt.json in snapshot directory")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise ReplayError("snapshot experiment identity mismatch")
    if receipt.get("interval_start_inclusive") != START_UTC.isoformat() or receipt.get("interval_end_exclusive") != END_UTC.isoformat():
        raise ReplayError("snapshot interval differs from frozen replay interval")
    bundle_manifest = snapshot_dir.parent / "bundle_manifest.json"
    if bundle_manifest.is_file():
        bundle = json.loads(bundle_manifest.read_text(encoding="utf-8"))
        if bundle.get("experiment_id") != EXPERIMENT_ID or bundle.get("source_commit") != receipt.get("source_commit"):
            raise ReplayError("worker bundle source identity differs from fetch receipt")
        if bundle.get("fetch_receipt_sha256") != sha256_file(receipt_path):
            raise ReplayError("worker bundle fetch-receipt checksum mismatch")
        runner = Path(__file__).resolve()
        if bundle.get("runner_sha256") != sha256_file(runner):
            raise ReplayError("worker runner checksum differs from bundled source")
    frames: Dict[str, pd.DataFrame] = {}
    for item in receipt["snapshot_files"]:
        path = snapshot_dir / "kline_snapshot" / str(item["filename"])
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ReplayError("snapshot file missing or changed: %s" % item["filename"])
        frame = pd.read_csv(path)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        frame["ts"] = pd.to_numeric(frame["ts"], errors="raise").astype("int64")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frame = frame.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        if frame["open_time"].duplicated().any():
            raise ReplayError("duplicate timestamps: %s" % item["filename"])
        frames[str(item["inst_id"])] = frame
    if len(frames) != int(receipt["usable_instruments"]):
        raise ReplayError("usable instrument count differs from fetch receipt")
    return frames, receipt


def map_prediction(cx: float, width: float, transform: ChartTransform, start_i: int, end_i: int) -> Dict[str, int]:
    """Map normalized YOLO x coordinates to the nearest chart candle centers."""

    x0 = (float(cx) - float(width) / 2.0) * transform.width
    x1 = (float(cx) + float(width) / 2.0) * transform.width
    centers = np.asarray([transform.x_at(index) for index in range(transform.n_bars)])
    local_start = int(np.argmin(np.abs(centers - x0)))
    local_end = int(np.argmin(np.abs(centers - x1)))
    if local_end < local_start:
        local_start, local_end = local_end, local_start
    core_start = start_i + local_start
    core_end = start_i + local_end
    return {
        "core_start_i": core_start,
        "core_end_i": core_end,
        "core_length_bars": core_end - core_start + 1,
        "confirmation_bars": end_i - core_end,
    }


def deduplicate_events(candidates: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the highest-confidence candidate within five core-end bars per symbol."""

    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_symbol[str(row["inst_id"])].append(dict(row))
    kept: List[Dict[str, Any]] = []
    for symbol, rows in sorted(by_symbol.items()):
        ordered = sorted(rows, key=lambda row: (-float(row["confidence"]), int(row["core_end_i"]), str(row["class_name"])))
        accepted: List[Dict[str, Any]] = []
        for row in ordered:
            if any(abs(int(row["core_end_i"]) - int(old["core_end_i"])) < EVENT_GAP_BARS for old in accepted):
                continue
            accepted.append(row)
        accepted.sort(key=lambda row: (int(row["core_end_i"]), -float(row["confidence"])))
        for index, row in enumerate(accepted, 1):
            row["event_id"] = "%s_%s_%04d" % (symbol.replace("-", "_"), row["core_end_time"].replace(":", "").replace("-", ""), index)
            kept.append(row)
    return sorted(kept, key=lambda row: (str(row["inst_id"]), str(row["core_end_time"])))


def scan_phase(snapshot_dir: Path, out: Path, weights: Path, device: str, batch_size: int) -> Dict[str, Any]:
    """Run the immutable native-1280 model over every eligible input window."""

    if out.exists():
        raise FileExistsError("refusing to overwrite scan output: %s" % out)
    immutable = verify_sources(require_training_assets=False)
    if not weights.is_file() or sha256_file(weights) != MODEL_SHA256:
        raise ReplayError("worker checkpoint identity mismatch")
    frames, fetch_receipt = load_snapshot(snapshot_dir)
    if fetch_receipt.get("model_identity") != immutable:
        raise ReplayError("worker renderer/data source differs from the frozen fetch identity")
    from ultralytics import YOLO

    model = YOLO(str(weights))
    out.mkdir(parents=True)
    model_out = out / "grade_a_full40_1280"
    model_out.mkdir()
    raw_boxes: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    symbol_stats: List[Dict[str, Any]] = []
    total = Counter()
    started = time.perf_counter()

    for number, (inst_id, frame) in enumerate(sorted(frames.items()), 1):
        symbol = inst_id.replace("-", "_")
        times = pd.to_datetime(frame["open_time"], utc=True)
        counters = Counter()
        symbol_candidates: List[Dict[str, Any]] = []
        if frame.empty:
            symbol_stats.append({"inst_id": inst_id, "symbol": symbol, "rows": 0, "in_window_bars": 0, "windows_scored": 0, "raw_boxes": 0, "structural_candidates": 0, "events": 0, "gap_count": 0})
            continue

        millis = frame["ts"].to_numpy(dtype=np.int64)
        break_at = np.flatnonzero(np.diff(millis) != int(BAR_DELTA.total_seconds() * 1000)) + 1
        boundaries = [0] + break_at.tolist() + [len(frame)]
        for segment_number in range(len(boundaries) - 1):
            left, right = boundaries[segment_number], boundaries[segment_number + 1]
            segment = add_mas(frame.iloc[left:right].reset_index(drop=True))
            if len(segment) < WARMUP_BARS:
                continue
            seg_times = pd.to_datetime(segment["open_time"], utc=True)
            batch: List[Tuple[np.ndarray, ChartTransform, Dict[str, Any]]] = []
            for end_local in range(len(segment)):
                endpoint_time = seg_times.iloc[end_local]
                if endpoint_time < START_UTC or endpoint_time >= END_UTC:
                    continue
                for window_len in WINDOW_LENGTHS:
                    start_local = end_local - window_len + 1
                    if start_local < 0:
                        counters["skip_insufficient_window"] += 1
                        continue
                    window = segment.iloc[start_local : end_local + 1]
                    if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                        counters["skip_ma_warmup"] += 1
                        continue
                    image, transform = render_chart(window, out_path=None)
                    global_start, global_end = left + start_local, left + end_local
                    batch.append((image, transform, {
                        "inst_id": inst_id,
                        "symbol": symbol,
                        "window_len": int(window_len),
                        "window_start_i": int(global_start),
                        "window_end_i": int(global_end),
                        "window_start_time": times.iloc[global_start].isoformat(),
                        "window_end_time": times.iloc[global_end].isoformat(),
                        "segment": int(segment_number),
                    }))
                    if len(batch) >= batch_size:
                        run_batch(model, batch, frame, raw_boxes, symbol_candidates, counters, device)
                        batch = []
            if batch:
                run_batch(model, batch, frame, raw_boxes, symbol_candidates, counters, device)

        counters["raw_boxes"] = sum(1 for row in raw_boxes if row["inst_id"] == inst_id)
        counters["structural_candidates"] = len(symbol_candidates)
        events = deduplicate_events(symbol_candidates)
        counters["deduplicated_events"] = len(events)
        in_window_bars = int(((times >= START_UTC) & (times < END_UTC)).sum())
        counters["expected_windows_for_available_bars"] = len(WINDOW_LENGTHS) * in_window_bars
        counters["windows_not_scored_for_warmup_or_short_history"] = max(
            0, counters["expected_windows_for_available_bars"] - counters["windows_scored"]
        )
        candidates.extend(symbol_candidates)
        total.update(counters)
        symbol_stats.append({
            "inst_id": inst_id,
            "symbol": symbol,
            "rows_including_warmup": int(len(frame)),
            "in_window_bars": in_window_bars,
            "gap_count": int(len(boundaries) - 2),
            **dict(counters),
        })
        if number % 10 == 0 or number == len(frames):
            print("scan [%d/%d] windows=%d raw_boxes=%d candidates=%d elapsed=%.1fh" % (
                number, len(frames), total["windows_scored"], len(raw_boxes), len(candidates),
                (time.perf_counter() - started) / 3600.0), flush=True)

    raw_columns = (
        "inst_id", "symbol", "window_len", "window_start_time", "window_end_time",
        "class_id", "class_name", "confidence", "prediction_cx_norm", "prediction_cy_norm",
        "prediction_w_norm", "prediction_h_norm", "core_start_i", "core_end_i",
        "core_start_time", "core_end_time", "core_length_bars", "confirmation_bars",
        "geometry_accepted", "input_width", "input_height", "segment",
    )
    candidate_columns = raw_columns + ("core_high", "core_low", "candidate_id")
    event_columns = candidate_columns + ("event_id",)
    for index, row in enumerate(candidates, 1):
        row["candidate_id"] = "%s_%s_%04d" % (row["symbol"], row["window_end_time"].replace(":", "").replace("-", ""), index)
    events = deduplicate_events(candidates)
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    event_rows = []
    for event in events:
        candidate = candidate_by_id.get(event.get("candidate_id"))
        event_rows.append(event if candidate is None else {**candidate, "event_id": event["event_id"]})
    write_csv(model_out / "raw_boxes.csv", raw_boxes, raw_columns)
    write_csv(model_out / "structural_candidates.csv", candidates, candidate_columns)
    write_csv(model_out / "five_bar_events.csv", event_rows, event_columns)
    write_csv(model_out / "scan_stats.csv", symbol_stats, ("inst_id", "symbol", "rows_including_warmup", "in_window_bars", "gap_count", "windows_scored", "windows_not_scored_for_warmup_or_short_history", "expected_windows_for_available_bars", "raw_boxes", "structural_candidates", "deduplicated_events"))

    classes = Counter(str(row["class_name"]) for row in events)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": fetch_receipt["source_commit"],
        "fetch_receipt_sha256": sha256_file(snapshot_dir / "fetch_receipt.json"),
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "model_identity": immutable,
        "weights_path_on_worker": str(weights),
        "weights_sha256": MODEL_SHA256,
        "device": device,
        "batch_size": batch_size,
        "exchange": "OKX",
        "bar": "15m",
        "interval_start_inclusive": START_UTC.isoformat(),
        "interval_end_exclusive": END_UTC.isoformat(),
        "universe_definition": fetch_receipt["instrument_scope"],
        "eligible_instruments": int(fetch_receipt["eligible_instruments"]),
        "usable_instruments": int(fetch_receipt["usable_instruments"]),
        "unusable_instruments": fetch_receipt["failed_instruments"],
        "detector_contract": {
            "class_names": CLASS_NAMES,
            "window_lengths": list(WINDOW_LENGTHS),
            "confidence": CONFIDENCE,
            "nms_iou": NMS_IOU,
            "imgsz": IMGSZ,
            "allowed_core_lengths": list(ALLOWED_CORE_LENGTHS),
            "allowed_confirmations": list(ALLOWED_CONFIRMATIONS),
            "same_symbol_event_gap_bars": EVENT_GAP_BARS,
            "warmup_bars": WARMUP_FETCH_BARS,
            "future_bars_rendered_into_input": 0,
        },
        "input_windows_scored": int(total["windows_scored"]),
        "expected_windows_for_available_bars": int(total["expected_windows_for_available_bars"]),
        "windows_not_scored_for_warmup_or_short_history": int(total["windows_not_scored_for_warmup_or_short_history"]),
        "windows_skipped_ma_warmup": int(total["skip_ma_warmup"]),
        "windows_skipped_insufficient_window": int(total["skip_insufficient_window"]),
        "raw_boxes": len(raw_boxes),
        "structural_candidates": len(candidates),
        "five_bar_events": len(events),
        "event_classes": dict(sorted(classes.items())),
        "raw_boxes_csv": "grade_a_full40_1280/raw_boxes.csv",
        "raw_boxes_sha256": sha256_file(model_out / "raw_boxes.csv"),
        "candidates_csv": "grade_a_full40_1280/structural_candidates.csv",
        "candidates_sha256": sha256_file(model_out / "structural_candidates.csv"),
        "events_csv": "grade_a_full40_1280/five_bar_events.csv",
        "events_sha256": sha256_file(model_out / "five_bar_events.csv"),
        "scan_stats_csv": "grade_a_full40_1280/scan_stats.csv",
        "scan_stats_sha256": sha256_file(model_out / "scan_stats.csv"),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "labels_available_for_scanned_market_windows": False,
        "accuracy_or_profitability_claim": False,
        "training_or_tuning": False,
        "active_or_frozen_changed": False,
        "promoted_or_deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "production_eligible": False,
        "training_eligible": False,
    }
    write_json(out / "scan_receipt.json", receipt)
    print("scan complete: windows=%d raw=%d candidates=%d events=%d wall=%.1fh" % (
        receipt["input_windows_scored"], receipt["raw_boxes"], receipt["structural_candidates"],
        receipt["five_bar_events"], receipt["wall_seconds"] / 3600.0), flush=True)
    return receipt


def run_batch(model: Any, batch: Sequence[Tuple[np.ndarray, ChartTransform, Dict[str, Any]]], frame: pd.DataFrame,
              raw_boxes: List[Dict[str, Any]], candidates: List[Dict[str, Any]], counters: Counter,
              device: str) -> None:
    """Infer one memory-bounded image batch and preserve all returned boxes."""

    predictions = model.predict(
        source=[item[0] for item in batch], imgsz=IMGSZ, conf=CONFIDENCE, iou=NMS_IOU,
        batch=len(batch), device=device, verbose=False,
    )
    if len(predictions) != len(batch):
        raise ReplayError("prediction count differs from rendered input count")
    times = pd.to_datetime(frame["open_time"], utc=True)
    for prediction, (_, transform, meta) in zip(predictions, batch):
        counters["windows_scored"] += 1
        boxes = prediction.boxes
        if boxes is None or len(boxes) == 0:
            continue
        for xywhn, class_id, confidence in zip(boxes.xywhn.cpu().numpy(), boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy()):
            cid = int(class_id)
            if cid not in CLASS_NAMES:
                counters["unknown_classes"] += 1
                continue
            mapped = map_prediction(float(xywhn[0]), float(xywhn[2]), transform,
                                    int(meta["window_start_i"]), int(meta["window_end_i"]))
            core_start, core_end = int(mapped["core_start_i"]), int(mapped["core_end_i"])
            core_start_time, core_end_time = times.iloc[core_start], times.iloc[core_end]
            geometry_ok = mapped["core_length_bars"] in ALLOWED_CORE_LENGTHS and mapped["confirmation_bars"] in ALLOWED_CONFIRMATIONS
            raw = {
                **meta,
                **mapped,
                "class_id": cid,
                "class_name": CLASS_NAMES[cid],
                "confidence": float(confidence),
                "prediction_cx_norm": float(xywhn[0]),
                "prediction_cy_norm": float(xywhn[1]),
                "prediction_w_norm": float(xywhn[2]),
                "prediction_h_norm": float(xywhn[3]),
                "core_start_time": core_start_time.isoformat(),
                "core_end_time": core_end_time.isoformat(),
                "geometry_accepted": bool(geometry_ok),
                "input_width": int(transform.width),
                "input_height": int(transform.height),
            }
            raw_boxes.append(raw)
            counters["raw_boxes"] += 1
            if not geometry_ok:
                continue
            if core_end_time < START_UTC or core_end_time >= END_UTC:
                counters["reject_core_outside_interval"] += 1
                continue
            segment = frame.iloc[core_start : core_end + 1]
            candidate = {
                **raw,
                "core_high": float(segment["high"].max()),
                "core_low": float(segment["low"].min()),
            }
            candidates.append(candidate)
            counters["structural_candidates"] += 1


def bundle_phase(out: Path, snapshot_dir: Path) -> None:
    """Package the committed scan source and frozen candles for the 3060 worker."""

    if out.exists():
        raise FileExistsError("refusing to overwrite worker bundle: %s" % out)
    receipt = snapshot_dir / "fetch_receipt.json"
    if not receipt.is_file():
        raise ReplayError("cannot bundle without the frozen fetch receipt")
    verify_sources(require_training_assets=True)
    verify_committed_sources()
    bundle_commit = source_commit()
    source_files = [
        Path(__file__).resolve(),
        ROOT / TRAINING_DATA_REL,
        ROOT / TRAINING_RENDERER_REL,
        ROOT / "yoyo" / "__init__.py",
        ROOT / "yoyo" / "evaluation" / "__init__.py",
        ROOT / "yoyo" / "evaluation" / "grade_a_full40_training_input_renderer" / "__init__.py",
    ]
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "source_commit": bundle_commit,
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "fetch_receipt_sha256": sha256_file(receipt),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in source_files:
            if path.is_file():
                archive.write(path, path.resolve().relative_to(ROOT.resolve()).as_posix())
        for path in sorted(snapshot_dir.rglob("*")):
            if path.is_file():
                archive.write(path, (Path("snapshot") / path.relative_to(snapshot_dir)).as_posix())
        archive.writestr("bundle_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print("worker bundle: %s (%d bytes)" % (out, out.stat().st_size), flush=True)


def main() -> int:
    """Run exactly one immutable fetch, bundle, or scan phase."""

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fetch", action="store_true")
    modes.add_argument("--bundle", action="store_true")
    modes.add_argument("--scan", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--weights", type=Path, default=ROOT / MODEL_REL)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--device", default="0")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1:
        parser.error("--workers and --batch-size must be positive")
    if args.fetch:
        fetch_phase(args.out.resolve(), args.workers)
    elif args.bundle:
        if args.snapshot_dir is None:
            parser.error("--bundle requires --snapshot-dir")
        bundle_phase(args.out.resolve(), args.snapshot_dir.resolve())
    else:
        if args.snapshot_dir is None:
            parser.error("--scan requires --snapshot-dir")
        scan_phase(args.snapshot_dir.resolve(), args.out.resolve(), args.weights.resolve(), args.device, args.batch_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
