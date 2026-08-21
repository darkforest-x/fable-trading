"""Causal pre-cross path-efficiency feature for the ETH 15-minute Pine study.

The only market input is ``close``.  At a decision bar ``t`` with lookback
``N``, the feature reads closes from ``t-N-1`` through ``t-1``: its numerator
is the absolute displacement from ``close[t-N-1]`` to ``close[t-1]`` and its
denominator is the sum of the ``N`` absolute one-bar moves over that same
interval.  The signal bar and every future row are excluded.  Values approach
one for a one-way path and zero for a path that repeatedly retraces.

This module defines one continuous, direction-neutral research feature only.
It does not choose a threshold, fit a model, alter the frozen 28-feature schema,
or make any Pine strategy eligible for training, forward use, or production.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_PRE_CROSS_PATH_EFFICIENCY_LOOKBACK = 32


def path_efficiency_column(lookback: int) -> str:
    """Return the stable output name after validating the frozen window."""

    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback <= 1:
        raise ValueError("lookback must be an integer greater than one")
    return f"pre_cross_path_efficiency_{lookback}"


def add_pre_cross_path_efficiency(
    frame: pd.DataFrame,
    *,
    lookback: int = DEFAULT_PRE_CROSS_PATH_EFFICIENCY_LOOKBACK,
) -> pd.DataFrame:
    """Add one prior-only path-efficiency column without selecting a gate.

    ``close`` is the sole required column.  For each decision row ``t``, the
    trailing window ends at ``t-1`` and contains ``lookback`` price changes.
    A completely flat path is left missing instead of inventing a value; the
    caller must treat warmup and undefined rows as not ready.
    """

    column = path_efficiency_column(lookback)
    if "close" not in frame.columns:
        raise ValueError("missing pre-cross path-efficiency column: close")

    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    prior_close = close.shift(1)
    displacement = (prior_close - prior_close.shift(lookback)).abs()
    path_length = prior_close.diff().abs().rolling(
        lookback,
        min_periods=lookback,
    ).sum()
    out[column] = displacement.div(path_length.where(path_length.gt(0.0))).clip(
        lower=0.0,
        upper=1.0,
    )
    return out
