"""Q80 forward shadow and same-window candidate-to-score diagnostics.

The active q90 artifact, mainline ledger and exits remain unchanged. Q80 is
recovered once from the immutable pre-holdout validation dataset and is used
only in a separate diagnostic ledger explicitly approved by the owner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.forward_records import merge_forward_log, read_forward_log, write_forward_log
from src.judgment.forward_scan import forward_candidate_indices, scan_forward_records
from src.judgment.forward_types import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
    ForwardRunSummary,
    ForwardScanInput,
)
from src.judgment.frozen import (
    DEFAULT_FROZEN_CONFIG,
    FrozenArtifact,
    FrozenArtifactError,
    file_sha256,
    latest_artifact,
    read_dataset_before,
)
from src.judgment.train import HOLDOUT_START

Q80_QUANTILE: Final = 0.80
Q80_LOG_PATH: Final = Path(__file__).resolve().parents[2] / "data" / "forward_log_ma206_q80_shadow.csv"


class ThresholdShadowPathError(RuntimeError):
    """Raised before writes when a diagnostic path overlaps another book."""


@dataclass(frozen=True)
class ThresholdScoreSummary:
    candidates_after_start: int
    finite_scores: int
    q90_signals: int
    q80_signals: int
    q80_incremental_signals: int
    q90_pass_rate: float
    q80_pass_rate: float


@dataclass(frozen=True)
class ThresholdFunnel:
    start_time: str
    scanned_series: int
    series_with_forward_bars: int
    latest_bar_time: str
    q90_threshold: float
    q80_threshold: float
    score_summary: ThresholdScoreSummary

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["score_summary"] = asdict(self.score_summary)
        return payload


def summarize_threshold_scores(
    scores: np.ndarray,
    *,
    q90_threshold: float,
    q80_threshold: float,
) -> ThresholdScoreSummary:
    """Compare q90 and q80 on one identical set of forward candidates."""
    if q80_threshold > q90_threshold:
        raise ValueError(f"q80 threshold {q80_threshold} exceeds q90 threshold {q90_threshold}")
    finite = scores[np.isfinite(scores)]
    q90_signals = int((finite >= q90_threshold).sum())
    q80_signals = int((finite >= q80_threshold).sum())
    denominator = len(finite)
    return ThresholdScoreSummary(
        candidates_after_start=int(len(scores)),
        finite_scores=denominator,
        q90_signals=q90_signals,
        q80_signals=q80_signals,
        q80_incremental_signals=q80_signals - q90_signals,
        q90_pass_rate=q90_signals / denominator if denominator else 0.0,
        q80_pass_rate=q80_signals / denominator if denominator else 0.0,
    )


def validate_shadow_output(path: Path) -> Path:
    """Guarantee that q80 diagnostics cannot overwrite champion or H1 books."""
    resolved = path.resolve()
    protected = {Path(FORWARD_LOG_PATH).resolve(), Path(FORWARD_LOG_H1_SCALED_PATH).resolve()}
    if resolved in protected:
        raise ThresholdShadowPathError(f"q80 shadow must use an isolated output path: {resolved}")
    return resolved


def _reproducible_dataset_path(artifact: FrozenArtifact) -> Path:
    snapshot = (
        artifact.config.project_dir
        / "data"
        / "frozen_datasets"
        / f"{artifact.dataset_sha256}{artifact.dataset_path.suffix}"
    )
    if snapshot.is_file():
        return snapshot
    if artifact.dataset_path.is_file() and file_sha256(artifact.dataset_path) == artifact.dataset_sha256:
        return artifact.dataset_path
    raise FrozenArtifactError(artifact.dataset_path, "no byte-matching dataset for q80 derivation")


def derive_q80_threshold(artifact: FrozenArtifact, booster: lgb.Booster) -> float:
    """Derive the approved q80 from the artifact's original pre-holdout val."""
    if artifact.val_start is None:
        raise FrozenArtifactError(artifact.metadata_path, "artifact has no validation start")
    safe = read_dataset_before(_reproducible_dataset_path(artifact), end_before=HOLDOUT_START)
    val = safe[safe["signal_time"] >= artifact.val_start]
    if val.empty:
        raise FrozenArtifactError(artifact.dataset_path, "artifact validation rows are unavailable")
    scores = booster.predict(val[list(artifact.feature_columns)], num_iteration=artifact.best_iteration)
    return float(np.quantile(scores, Q80_QUANTILE))


def scan_threshold_funnel(
    artifact: FrozenArtifact,
    *,
    start_time: pd.Timestamp = FORWARD_START,
) -> ThresholdFunnel:
    """Count the real post-start funnel before any ledger merge or dedupe."""
    start = pd.Timestamp(start_time).tz_convert("UTC")
    booster = lgb.Booster(model_file=str(artifact.model_path))
    q80_threshold = derive_q80_threshold(artifact, booster)
    scores_after_start: list[float] = []
    scanned_series = 0
    series_with_forward_bars = 0
    latest_bar: pd.Timestamp | None = None
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP"):
            continue
        scanned_series += 1
        series_latest = pd.Timestamp(frame["open_time"].max())
        latest_bar = series_latest if latest_bar is None else max(latest_bar, series_latest)
        if series_latest < start:
            continue
        series_with_forward_bars += 1
        enriched = add_indicators(frame)
        indices = [
            signal_i
            for signal_i in forward_candidate_indices(enriched)
            if pd.Timestamp(enriched["open_time"].iloc[signal_i]) >= start
        ]
        if not indices:
            continue
        featured = add_features(enriched)
        rows = extract_feature_rows(featured, indices)
        scores = booster.predict(rows[FEATURE_COLUMNS], num_iteration=artifact.best_iteration)
        scores_after_start.extend(float(score) for score in scores)
    summary = summarize_threshold_scores(
        np.asarray(scores_after_start, dtype=float),
        q90_threshold=artifact.threshold,
        q80_threshold=q80_threshold,
    )
    return ThresholdFunnel(
        start_time=str(start),
        scanned_series=scanned_series,
        series_with_forward_bars=series_with_forward_bars,
        latest_bar_time=str(latest_bar) if latest_bar is not None else "",
        q90_threshold=artifact.threshold,
        q80_threshold=q80_threshold,
        score_summary=summary,
    )


def run_q80_shadow(
    *,
    output_path: Path = Q80_LOG_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    """Write the approved q80 diagnostic book without changing ACTIVE/mainline."""
    validate_shadow_output(output_path)
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen MA206 artifact")
    booster = lgb.Booster(model_file=str(artifact.model_path))
    q80_artifact = replace(artifact, threshold=derive_q80_threshold(artifact, booster))
    existing = read_forward_log(output_path)
    scan = scan_forward_records(
        ForwardScanInput(
            artifact=q80_artifact,
            booster=booster,
            detected_at=datetime.now(timezone.utc).isoformat(),
            start_time=pd.Timestamp(start_time).tz_convert("UTC"),
            existing_log=existing,
        )
    )
    merged = merge_forward_log(existing, scan.records)
    write_forward_log(output_path, merged.frame)
    open_rows = int((merged.frame["status"] != "closed").sum()) if not merged.frame.empty else 0
    closed_rows = int((merged.frame["status"] == "closed").sum()) if not merged.frame.empty else 0
    return ForwardRunSummary(
        artifact=q80_artifact,
        start_time=pd.Timestamp(start_time).tz_convert("UTC"),
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
