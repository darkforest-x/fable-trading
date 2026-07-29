"""Shared ETH 3m v2 diagnostic-pilot constants and source evidence readers.

This module is used only by the frozen v2a dataset audit/refactor path.  It
reads the pre-holdout OHLC prefix and owner evidence files; it does not train,
inspect holdout rows, promote models, or alter ACTIVE.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2]
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
BAR_MINUTES = 3
BAR_DELTA = pd.Timedelta(minutes=BAR_MINUTES)
WINDOW = 200
FUTURE_BARS = 60
TARGET_TRAIN_FRACTION = 0.75
WEAK_REVIEW_OFFSETS = (-1, 1, 2, 3)
MIN_LEAD_BARS = 2

DEFAULT_INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_DETAIL = PROJECT / "analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv"
DEFAULT_CALIBRATION = PROJECT / "datasets/eth_3m_entry_timing_calibration30/manifest.csv"
DEFAULT_CALIBRATION_MOBILE_HTML = (
    PROJECT
    / "datasets/eth_3m_entry_timing_calibration30/eth3m_entry_timing_calibration30_mobile.html"
)
DEFAULT_OUT = PROJECT / "datasets/eth_3m_short_pilot_v2"

DETAIL_COLUMNS = [
    "task_id",
    "candidate_time",
    "v10_conf",
    "owner_is_target",
    "owner_label",
    "box_start_time",
    "first_below_all_mas_lag_bars",
]
CALIBRATION_COLUMNS = [
    "task_id",
    "source_task_id",
    "entry_candidate_time",
    "original_v10_time",
    "causal_image_rel",
    "review_image_rel",
]

def _utc(value: Any) -> pd.Timestamp:
    """Return one timezone-aware UTC timestamp."""
    out = pd.Timestamp(value)
    return out.tz_localize("UTC") if out.tzinfo is None else out.tz_convert("UTC")


def load_pre_holdout_ohlc(
    path: Path,
    *,
    holdout_start: pd.Timestamp = HOLDOUT_START,
    bar_delta: pd.Timedelta = BAR_DELTA,
) -> pd.DataFrame:
    """Read only the exact continuous CSV prefix strictly before holdout.

    Columns used: ``ts/open/high/low/close/volume``.  The row budget is derived
    from the first timestamp, the known bar interval, and the frozen holdout
    boundary.  A missing/duplicate/off-grid row fails closed instead of being
    silently repaired from later data.
    """
    first = pd.read_csv(path, nrows=1, usecols=["ts"])
    if len(first) != 1:
        raise ValueError("OHLC source has no first row")
    start = pd.to_datetime(first.loc[0, "ts"], unit="ms", utc=True)
    span = _utc(holdout_start) - start
    quotient = span / bar_delta
    if quotient <= 0 or not float(quotient).is_integer():
        raise ValueError("OHLC start is not aligned to the frozen holdout boundary")
    expected_rows = int(quotient)
    frame = pd.read_csv(
        path,
        nrows=expected_rows,
        usecols=["ts", "open", "high", "low", "close", "volume"],
    )
    if len(frame) != expected_rows:
        raise ValueError(f"pre-holdout prefix rows {len(frame)} != expected {expected_rows}")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["open_time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("invalid OHLC value inside pre-holdout prefix")
    if frame["open_time"].duplicated().any():
        raise ValueError("duplicate pre-holdout OHLC timestamp")
    expected_index = pd.date_range(start, periods=expected_rows, freq=bar_delta)
    if not frame["open_time"].reset_index(drop=True).equals(pd.Series(expected_index)):
        raise ValueError("pre-holdout OHLC is not a continuous 3-minute grid")
    expected_last = _utc(holdout_start) - bar_delta
    if frame["open_time"].iloc[-1] != expected_last:
        raise ValueError("pre-holdout OHLC prefix does not end at holdout minus one bar")
    return frame.reset_index(drop=True)


def load_sources(detail_path: Path, calibration_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load only causal geometry/choice columns; future outcomes are excluded."""
    detail = pd.read_csv(detail_path, usecols=DETAIL_COLUMNS)
    calibration = pd.read_csv(calibration_path, usecols=CALIBRATION_COLUMNS)
    for column in ("candidate_time", "box_start_time"):
        detail[column] = pd.to_datetime(detail[column], utc=True)
    for column in ("entry_candidate_time", "original_v10_time"):
        calibration[column] = pd.to_datetime(calibration[column], utc=True)
    if detail["task_id"].duplicated().any() or calibration["source_task_id"].duplicated().any():
        raise ValueError("source task ids must be unique")
    if set(detail["owner_is_target"].unique()) - {0, 1}:
        raise ValueError("owner_is_target must be binary")
    if (detail["candidate_time"] >= HOLDOUT_START).any():
        raise ValueError("project-53 candidate enters holdout")
    return detail, calibration


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_owner_confirmation_receipt(
    *,
    calibration_path: Path,
    mobile_html_path: Path,
    calibration: pd.DataFrame,
) -> dict[str, Any]:
    """Bind the batch chat confirmation to the exact calibration pack files."""
    calibration_root = calibration_path.parent
    assets: list[dict[str, str]] = []
    for row in calibration.sort_values("task_id").itertuples(index=False):
        causal_rel = str(row.causal_image_rel)
        review_rel = str(row.review_image_rel)
        causal_path = calibration_root / causal_rel
        review_path = calibration_root / review_rel
        if not causal_path.is_file() or not review_path.is_file():
            raise FileNotFoundError(f"missing calibration image for task {row.task_id}")
        assets.append(
            {
                "task_id": int(row.task_id),
                "source_task_id": int(row.source_task_id),
                "causal_image_rel": causal_rel,
                "causal_image_sha256": _sha256(causal_path),
                "review_image_rel": review_rel,
                "review_image_sha256": _sha256(review_path),
            }
        )
    if len(assets) != 30:
        raise ValueError(f"confirmation receipt expected 30 calibration images, got {len(assets)}")
    return {
        "confirmation_scope": "batch_chat_confirmation",
        "not_row_level_label_studio": True,
        "owner_exact_words": "看过了都来的急",
        "source_calibration_manifest_rel": calibration_path.relative_to(PROJECT).as_posix(),
        "source_calibration_manifest_sha256": _sha256(calibration_path),
        "source_mobile_html_rel": mobile_html_path.relative_to(PROJECT).as_posix(),
        "source_mobile_html_sha256": _sha256(mobile_html_path),
        "confirmed_current_tip_image_count": len(assets),
        "calibration_images": assets,
    }
