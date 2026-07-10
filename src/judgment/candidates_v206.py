"""Compatibility exports for the now-canonical SMA/EMA 20/60/120 profile."""
from __future__ import annotations

import pandas as pd

from src.judgment.candidates import (
    ALL_MA_COLS,
    EXPANDED_THRESHOLDS,
    FAST_MA_COLS,
    MA_PERIODS,
    MIN_DENSE_BARS,
    MIN_GAP_BARS,
    SLOW_MA_COLS,
    STRICT_THRESHOLDS,
    WARMUP_BARS,
    add_indicators,
    scan_candidates as _scan_candidates,
    strict_mask,
)
from src.judgment.features import add_features, extract_feature_rows

V206_THRESHOLDS = EXPANDED_THRESHOLDS


def candidate_mask(enriched: pd.DataFrame) -> pd.Series:
    """Return the canonical expanded-pool candidate mask."""
    return strict_mask(enriched, mode="expanded")


def scan_candidates(enriched: pd.DataFrame, *, horizon_bars: int = 72) -> list[int]:
    """Preserve the historical v206 entry point as the expanded pool."""
    return _scan_candidates(enriched, horizon_bars=horizon_bars, mode="expanded")


__all__ = (
    "ALL_MA_COLS",
    "FAST_MA_COLS",
    "MA_PERIODS",
    "MIN_DENSE_BARS",
    "MIN_GAP_BARS",
    "SLOW_MA_COLS",
    "STRICT_THRESHOLDS",
    "V206_THRESHOLDS",
    "WARMUP_BARS",
    "add_features",
    "add_indicators",
    "candidate_mask",
    "extract_feature_rows",
    "scan_candidates",
)
