"""Forward tracking entrypoint for the frozen tp5_sl2 SWAP model."""
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
    forward_candidate_indices,
    resolve_forward_exit,
    scan_forward_records,
)
from src.judgment.forward_types import (
    FORWARD_LOG_PATH,
    FORWARD_START,
    ForwardRecord,
    ForwardRunSummary,
    ForwardScanInput,
    ForwardSummaryJson,
)
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact

__all__ = (
    "FORWARD_LOG_PATH",
    "FORWARD_START",
    "ForwardRecord",
    "ForwardRunSummary",
    "ForwardSummaryJson",
    "forward_candidate_indices",
    "merge_forward_log",
    "normalize_start_time",
    "resolve_forward_exit",
    "run_forward_tracking",
    "summary_to_json",
)


def run_forward_tracking(
    output_path: Path = FORWARD_LOG_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    normalized_start = normalize_start_time(start_time)
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen artifact; run scripts/freeze_model.py first")
    existing = read_forward_log(output_path)
    scan = scan_forward_records(
        ForwardScanInput(
            artifact=artifact,
            booster=lgb.Booster(model_file=str(artifact.model_path)),
            detected_at=datetime.now(timezone.utc).isoformat(),
            start_time=normalized_start,
            existing_log=existing,
        )
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
