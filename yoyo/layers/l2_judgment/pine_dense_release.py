"""Literal confirmed-bar release features for the ETH 15m dense-start gate.

Inputs at decision bar ``t`` are ``high[t]``, ``low[t]``, ``close[t]``,
``close[t-1]``, Pine/Wilder ``atr[t]`` and ``atr[t-1]``, plus the causal six-MA
rope bounds and dense-start features already computed through ``t``.  True
range expansion is ``TR[t] / ATR[t-1]``.  Side-signed breakout expansion is
the current close-to-rope distance in ATR units minus the corresponding value
at ``t-1``.  No output uses a row after ``t``.

This module changes only release confirmation.  Formation, compression,
direction, execution and barrier semantics remain owned by their existing
contracts.  It exposes deterministic features and an explicit boolean gate;
it never fits or scores a statistical model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.layers.l2_judgment.pine_dense_start import (
    DEFAULT_ATR_RELEASE_WINDOW,
    DEFAULT_DENSITY_WINDOW,
    DEFAULT_SLOPE_LAG,
    DenseStartProfile,
    dense_start_gate_mask,
)


DEFAULT_MIN_TRUE_RANGE_ATR_RATIO = 1.0
DEFAULT_MIN_BREAKOUT_EXPANSION_ATR = 0.0


def add_dense_release_v2_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal true-range and breakout-distance release diagnostics."""

    required = {
        "high",
        "low",
        "close",
        "atr",
        "dense_rope_upper",
        "dense_rope_lower",
        "dense_breakout_distance_atr_long",
        "dense_breakout_distance_atr_short",
        "dense_start_score_long",
        "dense_start_score_short",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing dense-release columns: {missing}")
    out = frame.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    atr = out["atr"].astype(float).replace(0.0, np.nan)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["dense_release_true_range"] = true_range
    out["dense_release_true_range_atr_ratio"] = true_range / atr.shift(1)

    prior_long_distance = (
        previous_close - out["dense_rope_upper"].shift(1)
    ) / atr.shift(1)
    prior_short_distance = (
        out["dense_rope_lower"].shift(1) - previous_close
    ) / atr.shift(1)
    out["dense_release_prior_distance_atr_long"] = prior_long_distance
    out["dense_release_prior_distance_atr_short"] = prior_short_distance
    out["dense_release_breakout_expansion_atr_long"] = (
        out["dense_breakout_distance_atr_long"] - prior_long_distance
    )
    out["dense_release_breakout_expansion_atr_short"] = (
        out["dense_breakout_distance_atr_short"] - prior_short_distance
    )

    true_range_score = (
        (out["dense_release_true_range_atr_ratio"] - 0.5) / 1.5
    ).clip(0.0, 1.0)
    long_expansion_score = (
        out["dense_release_breakout_expansion_atr_long"] / 1.5
    ).clip(0.0, 1.0)
    short_expansion_score = (
        out["dense_release_breakout_expansion_atr_short"] / 1.5
    ).clip(0.0, 1.0)
    out["dense_release_v2_score_long"] = (
        out["dense_start_score_long"] * 4.0
        + (true_range_score + long_expansion_score) / 2.0
    ) / 5.0
    out["dense_release_v2_score_short"] = (
        out["dense_start_score_short"] * 4.0
        + (true_range_score + short_expansion_score) / 2.0
    ) / 5.0
    out["dense_release_v2_ready"] = out[
        [
            "dense_release_true_range_atr_ratio",
            "dense_release_breakout_expansion_atr_long",
            "dense_release_breakout_expansion_atr_short",
        ]
    ].notna().all(axis=1)
    return out


def dense_release_v2_gate_mask(
    featured: pd.DataFrame,
    profile: DenseStartProfile,
    *,
    side: str,
    min_true_range_atr_ratio: float = DEFAULT_MIN_TRUE_RANGE_ATR_RATIO,
    min_breakout_expansion_atr: float = DEFAULT_MIN_BREAKOUT_EXPANSION_ATR,
    density_window: int = DEFAULT_DENSITY_WINDOW,
    atr_release_window: int = DEFAULT_ATR_RELEASE_WINDOW,
    slope_lag: int = DEFAULT_SLOPE_LAG,
) -> pd.Series:
    """Require the V13 setup plus literal side-aligned release expansion."""

    if side not in {"long", "short"}:
        raise ValueError(f"side must be long|short, got {side!r}")
    if not np.isfinite(min_true_range_atr_ratio) or min_true_range_atr_ratio <= 0.0:
        raise ValueError("min_true_range_atr_ratio must be finite and positive")
    if not np.isfinite(min_breakout_expansion_atr):
        raise ValueError("min_breakout_expansion_atr must be finite")
    base = dense_start_gate_mask(
        featured,
        profile,
        side=side,
        density_window=density_window,
        atr_release_window=atr_release_window,
        slope_lag=slope_lag,
    )
    return (
        base
        & featured["dense_release_v2_ready"].fillna(False).astype(bool)
        & featured[f"dense_slope_coherence_{side}_{slope_lag}"].ge(
            profile.min_slope_coherence
        )
        & featured["dense_release_true_range_atr_ratio"].ge(
            min_true_range_atr_ratio
        )
        & featured[f"dense_release_breakout_expansion_atr_{side}"].gt(
            min_breakout_expansion_atr
        )
    ).fillna(False)
