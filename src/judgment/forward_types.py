"""Typed forward-log records and run summaries."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

import lightgbm as lgb
import pandas as pd

from src.judgment.frozen import FrozenArtifact

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
FORWARD_LOG_PATH: Final = PROJECT_DIR / "data" / "forward_log.csv"
# H1 scaled shadow paper book — never mixed into mainline 100-trade gate.
FORWARD_LOG_H1_SCALED_PATH: Final = PROJECT_DIR / "data" / "forward_log_h1_scaled.csv"
# YOLO mainline cutover (owner 2026-07-15): new candidate source → new forward clock.
# Pre-cutover rule-scan log archived as data/forward_log_rules_pre_yolo_20260715.csv
# Owner 2026-07-18/19: clear pre-v11 mixed book and restart gate for clean retest.
# Archived: data/forward_log_pre_v11_retest_20260719.csv (VPS + local).
# Use last *closed* bar open (not wall-clock "now") so live YOLO is not skipped
# while the current 15m candle is still forming.
FORWARD_START: Final = pd.Timestamp("2026-07-18 16:15:00", tz="UTC")
# "yolo" = detector proposes bars; "rules" = expanded dense-MA scan (legacy).
# Override with env FABLE_CANDIDATE_SOURCE=rules when VPS has no ultralytics/torch.
import os as _os
CANDIDATE_SOURCE: Final = _os.environ.get("FABLE_CANDIDATE_SOURCE", "yolo").strip().lower() or "yolo"
BAR: Final = pd.Timedelta(minutes=15)
TP_MULT: Final = 5.0
SL_MULT: Final = 2.0
# H1 scaled exit params (single-variable vs mainline TP5/SL2).
SCALED_TP1_MULT: Final = 2.5
SCALED_TRAIL_MULT: Final = 3.0
SCALED_SL_MULT: Final = 2.0
FORWARD_COLUMNS: Final = (
    "source",
    "symbol",
    "signal_time",
    "detected_at",
    "status",
    "score",
    "threshold",
    "model_path",
    "dataset_sha256",
    "signal_i",
    "entry_time",
    "entry_price",
    "maker_filled",
    "outcome",
    "label",
    "exit_offset",
    "exit_time",
    "realized_ret",
    "atr_pct",
    "dense_run_len",
)
OUTCOME_COLUMNS: Final = ("status", "outcome", "label", "exit_offset", "exit_time", "realized_ret")


class ForwardRecord(TypedDict):
    source: str
    symbol: str
    signal_time: str
    detected_at: str
    status: str
    score: float
    threshold: float
    model_path: str
    dataset_sha256: str
    signal_i: int
    entry_time: str
    entry_price: float
    # None while a tip-recorded row awaits its entry-bar backfill
    maker_filled: bool | None
    outcome: str
    label: int
    exit_offset: int
    exit_time: str
    realized_ret: float
    atr_pct: float
    dense_run_len: int


class ForwardSummaryJson(TypedDict):
    model_path: str
    threshold: float
    start_time: str
    scanned_series: int
    candidates_seen: int
    threshold_signals_seen: int
    new_signals: int
    closed_updates: int
    total_rows: int
    open_rows: int
    closed_rows: int
    output: str


@dataclass(frozen=True)
class ForwardExit:
    __slots__ = ("status", "outcome", "label", "exit_offset", "exit_time", "realized_ret")

    status: str
    outcome: str
    label: int
    exit_offset: int
    exit_time: str
    realized_ret: float


@dataclass(frozen=True)
class ForwardScanInput:
    __slots__ = ("artifact", "booster", "detected_at", "start_time", "existing_log")

    artifact: FrozenArtifact
    booster: lgb.Booster
    detected_at: str
    start_time: pd.Timestamp
    existing_log: pd.DataFrame


@dataclass(frozen=True)
class ForwardScanResult:
    __slots__ = ("records", "scanned_series", "candidates_seen", "threshold_signals_seen")

    records: list[ForwardRecord]
    scanned_series: int
    candidates_seen: int
    threshold_signals_seen: int


@dataclass(frozen=True)
class MergeResult:
    __slots__ = ("frame", "new_signals", "closed_updates")

    frame: pd.DataFrame
    new_signals: int
    closed_updates: int


@dataclass(frozen=True)
class ForwardRunSummary:
    __slots__ = (
        "artifact",
        "start_time",
        "scanned_series",
        "candidates_seen",
        "threshold_signals_seen",
        "new_signals",
        "closed_updates",
        "total_rows",
        "open_rows",
        "closed_rows",
        "output",
    )

    artifact: FrozenArtifact
    start_time: pd.Timestamp
    scanned_series: int
    candidates_seen: int
    threshold_signals_seen: int
    new_signals: int
    closed_updates: int
    total_rows: int
    open_rows: int
    closed_rows: int
    output: Path

    def to_json(self) -> ForwardSummaryJson:
        return {
            "model_path": self.artifact.relative_model_path,
            "threshold": self.artifact.threshold,
            "start_time": str(self.start_time),
            "scanned_series": self.scanned_series,
            "candidates_seen": self.candidates_seen,
            "threshold_signals_seen": self.threshold_signals_seen,
            "new_signals": self.new_signals,
            "closed_updates": self.closed_updates,
            "total_rows": self.total_rows,
            "open_rows": self.open_rows,
            "closed_rows": self.closed_rows,
            "output": str(self.output),
        }
