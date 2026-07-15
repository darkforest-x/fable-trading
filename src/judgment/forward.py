"""Forward tracking entrypoint for the frozen tp5_sl2 SWAP model.

Also provides H1 scaled *shadow* tracking: same mainline freeze for entry
scoring/threshold, but exit outcomes from scaled barrier math, written only to
`data/forward_log_h1_scaled.csv` (never mainline `forward_log.csv`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from src.judgment.forward_records import (
    merge_forward_log,
    read_forward_log,
    write_forward_log,
)
from src.judgment.forward_scan import (
    ExitResolver,
    forward_candidate_indices,
    resolve_forward_exit,
    resolve_forward_exit_scaled,
    scan_forward_records,
)
from src.judgment.forward_types import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
    ForwardRecord,
    ForwardRunSummary,
    ForwardScanInput,
    ForwardSummaryJson,
)
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact

__all__ = (
    "FORWARD_LOG_H1_SCALED_PATH",
    "FORWARD_LOG_PATH",
    "FORWARD_START",
    "ForwardRecord",
    "ForwardRunSummary",
    "ForwardSummaryJson",
    "forward_candidate_indices",
    "merge_forward_log",
    "normalize_start_time",
    "resolve_forward_exit",
    "resolve_forward_exit_scaled",
    "run_forward_tracking",
    "run_forward_tracking_h1_shadow",
    "summary_to_json",
)


def run_forward_tracking(
    output_path: Path = FORWARD_LOG_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    return _run_forward_tracking(
        output_path=output_path,
        start_time=start_time,
        exit_resolver=resolve_forward_exit,
    )


def run_forward_tracking_h1_shadow(
    output_path: Path = FORWARD_LOG_H1_SCALED_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    """Shadow paper book for H1 scaled exits.

    Entry signals: mainline frozen TP5/SL2 SWAP model + val-q90 threshold
    (identical candidate universe and score filter). Outcomes: scaled 2.5 bank
    + 3 trail via `resolve_forward_exit_scaled`.

    Refuses to write into the mainline log path. Legacy
    `models/frozen_scaled_25_t3_*` is a stub and is intentionally not loaded;
    a proper scaled-label freeze is a future owner step (see plan doc).
    """
    resolved = Path(output_path).resolve()
    if resolved == Path(FORWARD_LOG_PATH).resolve():
        raise ValueError(
            "H1 shadow must not write to mainline data/forward_log.csv; "
            f"use {FORWARD_LOG_H1_SCALED_PATH} (or another non-mainline path)"
        )
    return _run_forward_tracking(
        output_path=output_path,
        start_time=start_time,
        exit_resolver=resolve_forward_exit_scaled,
    )


def _run_forward_tracking(
    *,
    output_path: Path,
    start_time: pd.Timestamp,
    exit_resolver: ExitResolver,
) -> ForwardRunSummary:
    import os

    # OpenMP/thread clash between torch and lightgbm can hang multi-series YOLO scans.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

    normalized_start = normalize_start_time(start_time)
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen artifact; run scripts/freeze_model.py first")
    existing = read_forward_log(output_path)
    # Load YOLO *before* LightGBM booster when YOLO is the candidate source —
    # reverse order has hung on Apple Silicon mid-scan (0% CPU sleep).
    from src.judgment.forward_types import CANDIDATE_SOURCE
    from src.judgment.yolo_candidates import load_yolo_model

    if CANDIDATE_SOURCE == "yolo":
        load_yolo_model()
    scan = scan_forward_records(
        ForwardScanInput(
            artifact=artifact,
            booster=lgb.Booster(model_file=str(artifact.model_path)),
            detected_at=datetime.now(timezone.utc).isoformat(),
            start_time=normalized_start,
            existing_log=existing,
        ),
        exit_resolver=exit_resolver,
    )
    merged = merge_forward_log(existing, scan.records)
    write_forward_log(output_path, merged.frame)
    open_rows = int((merged.frame["status"] != "closed").sum()) if not merged.frame.empty else 0
    closed_rows = int((merged.frame["status"] == "closed").sum()) if not merged.frame.empty else 0
    return ForwardRunSummary(
        artifact=artifact,
        start_time=normalized_start,
        scanned_series=scan.scanned_series,
        candidates_seen=scan.candidates_seen,
        threshold_signals_seen=scan.threshold_signals_seen,
        new_signals=merged.new_signals,
        closed_updates=merged.closed_updates,
        total_rows=int(len(merged.frame)),
        open_rows=open_rows,
        closed_rows=closed_rows,
        output=output_path,
    )


def normalize_start_time(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def summary_to_json(summary: ForwardRunSummary) -> str:
    return json.dumps(summary.to_json(), ensure_ascii=False, indent=2)
