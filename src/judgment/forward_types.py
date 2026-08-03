"""Typed forward-log records and run summaries."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypedDict

import lightgbm as lgb
import pandas as pd

from src.judgment.frozen import FrozenArtifact

if TYPE_CHECKING:
    from src.judgment.protocol import StrategyProtocol

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
FORWARD_LOG_PATH: Final = PROJECT_DIR / "data" / "forward_log.csv"
# H1 scaled shadow paper book — never mixed into mainline 100-trade gate.
FORWARD_LOG_H1_SCALED_PATH: Final = PROJECT_DIR / "data" / "forward_log_h1_scaled.csv"
# A2 maker-entry trial bucket — isolated ledger; never mixed into mainline.
# Written only by scripts/forward_maker_trial.py (requires FABLE_MAKER_TRIAL=1).
FORWARD_LOG_MAKER_TRIAL_PATH: Final = PROJECT_DIR / "data" / "forward_log_maker_trial.csv"
# YOLO mainline cutover (owner 2026-07-15): new candidate source → new forward clock.
# Pre-cutover rule-scan log archived as data/forward_log_rules_pre_yolo_20260715.csv
# Owner 2026-07-18/19: clear pre-v11 mixed book and restart gate for clean retest.
# Archived: data/forward_log_pre_v11_retest_20260719.csv (VPS + local).
# Owner 2026-07-31: short-protocol reset (side-aware scan + ACTIVE authority).
# Archived polluted long-geometry book:
#   data/forward_log_pre_short_protocol_20260731.csv
# Protocol tag for dashboards/docs (not a CSV column yet):
#   protocol_version = short_v10_p0fix_20260731
# Use last *closed* bar open (not wall-clock "now") so live YOLO is not skipped
# while the current 15m candle is still forming.
FORWARD_START: Final = pd.Timestamp("2026-07-31 00:00:00", tz="UTC")
PROTOCOL_VERSION: Final = "short_v10_p0fix_20260731"
# Candidate provenance is part of the runtime safety contract. Production may
# only discover from the validated YOLO path; legacy rules remain available to
# explicitly marked offline/research callers.
RUNTIME_MODE: Final = os.environ.get("FABLE_RUNTIME_MODE", "production").strip().lower() or "production"
CANDIDATE_SOURCE: Final = os.environ.get("FABLE_CANDIDATE_SOURCE", "yolo").strip().lower() or "yolo"
VALID_RUNTIME_MODES: Final = frozenset({"production", "research"})
VALID_CANDIDATE_SOURCES: Final = frozenset({"yolo", "rules"})
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
    # Tiered sizing (owner 2026-07-20). Appended LAST so pre-tier readers of
    # positional CSVs are unaffected; legacy rows read back as NaN → 1x.
    "tier",
    "size_mult",
    # Direction contract. Legacy rows normalize to NaN and remain long-only;
    # explicit non-long rows are rejected by the current executor.
    "side",
    # Appended, never reordered: an old CSV must keep reading back correctly.
    "protocol_version",
    "strategy_id",
    "feature_semantics",
    "decision_at",
    "execution_eligible",
    # Immutable artifact identity. Appended for CSV compatibility.
    "model_sha256",
    "detector_sha256",
    # P0.6 causal execution timeline. These are appended for old CSV readers.
    # The historical entry_* fields above are legacy compatibility only; new
    # protocol rows never put a signal-close proxy or research next-open there.
    "candidate_detected_at",
    "signal_closed_at",
    "entry_mode",
    "entry_status",
    "entry_requested_at",
    "fill_source",
    "fill_at",
    "fill_px",
    "reference_px",
    # Research outcome is explicitly separate from actual broker/paper PnL.
    "research_status",
    "research_outcome",
    "research_label",
    "research_exit_offset",
    "research_exit_time",
    "research_gross_ret",
    "actual_outcome",
    "actual_exit_at",
    "actual_exit_px",
    "actual_realized_ret",
    "actual_return_semantics",
    "return_convention",
    "target_ret_column",
    "target_semantics",
    "target_cost_included",
    "reporting_route",
)
OUTCOME_COLUMNS: Final = (
    "status", "outcome", "label", "exit_offset", "exit_time", "realized_ret",
    "research_status", "research_outcome", "research_label",
    "research_exit_offset", "research_exit_time", "research_gross_ret",
    "actual_outcome", "actual_exit_at", "actual_exit_px", "actual_realized_ret",
)

# Rows written before provenance existed. Not a placeholder to be filled in later:
# it is the honest name for "produced under a protocol nobody recorded", and it
# keeps those rows out of any new protocol's 100-trade clock (acceptance H-01/H-02).
LEGACY_PROTOCOL: Final = "legacy_pre_20260803"
LEGACY_STRATEGY: Final = "legacy_unknown"
LEGACY_SEMANTICS: Final = "legacy_unaligned"


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
    # score→size tier of the frozen val distribution (q90_q95/q95_q99/q99_plus)
    tier: str
    size_mult: float
    side: str
    # Provenance (P0.3). Which contract produced this row, and when the decision
    # was actually made -- not when the batch that contained it started.
    protocol_version: str
    strategy_id: str
    feature_semantics: str
    decision_at: str
    execution_eligible: bool
    model_sha256: str
    detector_sha256: str
    candidate_detected_at: str
    signal_closed_at: str
    entry_mode: str
    entry_status: str
    entry_requested_at: str
    fill_source: str
    fill_at: str
    fill_px: float
    reference_px: float
    research_status: str
    research_outcome: str
    research_label: int
    research_exit_offset: int
    research_exit_time: str
    research_gross_ret: float
    actual_outcome: str
    actual_exit_at: str
    actual_exit_px: float
    actual_realized_ret: float
    actual_return_semantics: str
    return_convention: str
    target_ret_column: str
    target_semantics: str
    target_cost_included: bool
    reporting_route: str


def validate_candidate_source(candidate_source: str, runtime_mode: str) -> str:
    """Validate candidate provenance before any forward scan or model load.

    Production accepts only ``yolo``. ``rules`` is intentionally retained for
    reproducible offline/research diagnostics, which must opt in with
    ``FABLE_RUNTIME_MODE=research``.
    """
    source = str(candidate_source).strip().lower()
    mode = str(runtime_mode).strip().lower()
    if mode not in VALID_RUNTIME_MODES:
        raise ValueError(
            f"invalid FABLE_RUNTIME_MODE={runtime_mode!r}; "
            f"expected one of {sorted(VALID_RUNTIME_MODES)}"
        )
    if source not in VALID_CANDIDATE_SOURCES:
        raise ValueError(
            f"invalid FABLE_CANDIDATE_SOURCE={candidate_source!r}; "
            f"expected one of {sorted(VALID_CANDIDATE_SOURCES)}"
        )
    if mode == "production" and source != "yolo":
        raise RuntimeError(
            "production forward tracking requires candidate_source=yolo; "
            "legacy rules are research-only"
        )
    return source


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
    __slots__ = (
        "artifact", "booster", "detected_at", "start_time", "existing_log", "protocol"
    )

    artifact: FrozenArtifact
    booster: lgb.Booster
    detected_at: str
    start_time: pd.Timestamp
    existing_log: pd.DataFrame
    protocol: StrategyProtocol | None


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
