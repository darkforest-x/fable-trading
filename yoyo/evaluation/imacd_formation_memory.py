"""Causal six-MA formation memory for the startup-quality research comparison.

Source: the owner-defined formation process and the first study's finding that
six-MA compression can begin before the IMACD focus segment. This module adds
features to the chronological, gap-free output of ``build_features`` from
``imacd_startup_quality``. It reads only ``rope_high`` and ``rope_low``; their
difference is the raw six-MA width W, with no ATR or outcome normalization.

At decision row t, the recent width is median(W[t-12:t]), i.e. t-12 .. t-1.
The background width is median(W[t-132:t-12]), i.e. t-132 .. t-13, containing
exactly 120 earlier bars. Both complete, non-overlapping windows precede t;
the current candle, its release direction, focus start, future candles, and
outcomes cannot change these features. The windows are fixed at 12 and 120
bars, with no fitting or search. Initial rows with insufficient history fail
closed. Missing/nonfinite bounds or negative widths invalidate a window.

``formation_memory_ratio`` is recent / background; zero / zero is 1, positive
/ zero is infinity, and an unavailable window produces NaN. ``keep_memory``
requires both complete finite windows and recent <= background. Equal widths
pass; this measures compression relative to prior context, not absolute
narrowness or a guarantee of a trend. Release and unchanged proximity gates
belong to the caller. This module neither reads data/outcomes nor changes live
monitor, Pine, notification, or execution rules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


RECENT_BARS = 12
BACKGROUND_BARS = 120
MEMORY_COLUMNS = (
    "formation_memory_recent_width",
    "formation_memory_background_width",
    "formation_memory_ratio",
    "keep_memory",
)


def add_formation_memory(f: pd.DataFrame) -> pd.DataFrame:
    """Copy precomputed features and append the prior-only formation measures.

    Only the two rope bounds are required; any release/proximity columns are
    preserved without being interpreted. Row ordering is checked here; bar
    clock regularity remains the upstream ``build_features`` contract.
    See the module docstring for source columns and exact causal windows.
    """
    if not isinstance(f, pd.DataFrame):
        raise ValueError("features must be a pandas DataFrame")
    if not f.columns.is_unique or not {"rope_high", "rope_low"}.issubset(f.columns):
        raise ValueError("features must contain unique rope_high and rope_low columns")
    if not f.index.is_unique or not f.index.is_monotonic_increasing:
        raise ValueError("feature rows must be unique and chronological")
    try:
        bounds = f[["rope_high", "rope_low"]].to_numpy(dtype=float, na_value=np.nan)
    except (TypeError, ValueError) as exc:
        raise ValueError("rope bounds must be numeric") from exc

    with np.errstate(invalid="ignore", over="ignore"):
        raw_width = bounds[:, 0] - bounds[:, 1]
    valid = np.isfinite(bounds).all(axis=1) & np.isfinite(raw_width) & (raw_width >= 0)
    width = pd.Series(np.where(valid, raw_width, np.nan), index=f.index)
    recent = width.shift(1).rolling(RECENT_BARS, min_periods=RECENT_BARS).median()
    background = width.shift(RECENT_BARS + 1).rolling(
        BACKGROUND_BARS, min_periods=BACKGROUND_BARS,
    ).median()

    recent_values = recent.to_numpy()
    background_values = background.to_numpy()
    ratio = np.divide(
        recent_values, background_values,
        out=np.full(len(f), np.nan), where=background_values > 0,
    )
    ratio[(background_values == 0) & (recent_values == 0)] = 1.0
    ratio[(background_values == 0) & (recent_values > 0)] = np.inf
    complete = np.isfinite(recent_values) & np.isfinite(background_values)

    out = f.copy(deep=True)
    out[MEMORY_COLUMNS[0]] = recent
    out[MEMORY_COLUMNS[1]] = background
    out[MEMORY_COLUMNS[2]] = ratio
    out[MEMORY_COLUMNS[3]] = complete & (recent_values <= background_values)
    return out
