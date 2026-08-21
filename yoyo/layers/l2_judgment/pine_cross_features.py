"""Causal moving-average cross-count features for the ETH 15-minute Pine surface.

Inputs are the seven project EMA columns ``ema8/13/21/34/55/144/200``.
For each adjacent fast-to-slow pair, a golden/death cross at bar ``t`` uses
only the pair values at ``t`` and ``t-1``.  Rolling counts and breadth use the
current bar plus the preceding ``window-1`` bars; no future row is read.

The six adjacent relations are deliberately distinct from the existing
``order_score``: order_score says how the bundle is arranged *now*, whereas
these features count how many actual ordering transitions recently occurred.

The owner-visible dense-cluster contract is a different six-line bundle:
``SMA/EMA 20/60/120``.  Its directional cross family uses the 12 pairs whose
first period is strictly faster than the second (20x60, 20x120 and 60x120,
with all SMA/EMA combinations).  Same-period SMA/EMA pairs are excluded
because they have no canonical fast/slow or golden/death direction.  All six
MA values must be supplied by the caller using the renderer-contract close
price arithmetic; this module never imports another business layer.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


EMA_COLUMNS = ("ema8", "ema13", "ema21", "ema34", "ema55", "ema144", "ema200")
ADJACENT_PAIRS = tuple(zip(EMA_COLUMNS[:-1], EMA_COLUMNS[1:]))
SIX_MA_COLUMNS = ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")
SIX_MA_PERIOD_GROUPS = {
    20: ("sma20", "ema20"),
    60: ("sma60", "ema60"),
    120: ("sma120", "ema120"),
}
SIX_MA_DIRECTIONAL_PAIRS = tuple(
    (fast, slow)
    for fast_period, slow_period in ((20, 60), (20, 120), (60, 120))
    for fast in SIX_MA_PERIOD_GROUPS[fast_period]
    for slow in SIX_MA_PERIOD_GROUPS[slow_period]
)


def _add_pair_cross_features(
    frame: pd.DataFrame,
    *,
    pairs: tuple[tuple[str, str], ...],
    prefix: str,
    windows: tuple[int, ...],
) -> pd.DataFrame:
    """Add causal event, breadth and current-order features for ``pairs``."""

    out = frame.copy()
    up_events: dict[str, pd.Series] = {}
    down_events: dict[str, pd.Series] = {}
    for fast, slow in pairs:
        name = f"{fast}_{slow}"
        difference = out[fast].astype(float) - out[slow].astype(float)
        up_events[name] = (difference.gt(0.0) & difference.shift(1).le(0.0)).fillna(False)
        down_events[name] = (difference.lt(0.0) & difference.shift(1).ge(0.0)).fillna(False)

    up = pd.DataFrame(up_events, index=out.index).astype(int)
    down = pd.DataFrame(down_events, index=out.index).astype(int)
    out[f"{prefix}_up_alignment"] = sum(
        out[fast].ge(out[slow]).astype(int) for fast, slow in pairs
    )
    out[f"{prefix}_down_alignment"] = sum(
        out[fast].le(out[slow]).astype(int) for fast, slow in pairs
    )

    for window in windows:
        up_count = up.sum(axis=1).rolling(window, min_periods=1).sum()
        down_count = down.sum(axis=1).rolling(window, min_periods=1).sum()
        out[f"{prefix}_cross_up_count_{window}"] = up_count.astype(int)
        out[f"{prefix}_cross_down_count_{window}"] = down_count.astype(int)
        out[f"{prefix}_cross_churn_{window}"] = (up_count + down_count).astype(int)
        out[f"{prefix}_cross_up_breadth_{window}"] = (
            up.rolling(window, min_periods=1).max().sum(axis=1).astype(int)
        )
        out[f"{prefix}_cross_down_breadth_{window}"] = (
            down.rolling(window, min_periods=1).max().sum(axis=1).astype(int)
        )
    return out


def add_cross_count_features(
    frame: pd.DataFrame,
    *,
    windows: Iterable[int] = (8, 16, 32),
) -> pd.DataFrame:
    """Return ``frame`` with causal adjacent-EMA cross counts and breadth."""

    missing = sorted(set(EMA_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"missing EMA columns: {missing}")
    normalized_windows = tuple(dict.fromkeys(int(window) for window in windows))
    if not normalized_windows or any(window <= 0 for window in normalized_windows):
        raise ValueError("cross-count windows must be positive")

    return _add_pair_cross_features(
        frame,
        pairs=ADJACENT_PAIRS,
        prefix="ema",
        windows=normalized_windows,
    )


def add_six_ma_cross_count_features(
    frame: pd.DataFrame,
    *,
    windows: Iterable[int] = (8, 16, 32),
) -> pd.DataFrame:
    """Add causal directional crosses for the SMA/EMA 20/60/120 six-line bundle.

    Source columns are ``close`` plus the six renderer-contract MA columns.
    Cross events at ``t`` use only MA values at ``t`` and ``t-1``; rolling
    counts use ``[t-window+1, t]``.  ``six_ma_bandwidth`` and its four-bar
    change use close and bundle values through ``t`` only.
    """

    missing = sorted(set(SIX_MA_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"missing six-MA columns: {missing}")
    if "close" not in frame.columns:
        raise ValueError("missing close column for six-MA bandwidth")
    normalized_windows = tuple(dict.fromkeys(int(window) for window in windows))
    if not normalized_windows or any(window <= 0 for window in normalized_windows):
        raise ValueError("cross-count windows must be positive")

    out = _add_pair_cross_features(
        frame,
        pairs=SIX_MA_DIRECTIONAL_PAIRS,
        prefix="six_ma",
        windows=normalized_windows,
    )
    close = out["close"].astype(float).replace(0.0, np.nan)
    bundle = out.loc[:, list(SIX_MA_COLUMNS)].astype(float)
    out["six_ma_bandwidth"] = (bundle.max(axis=1) - bundle.min(axis=1)) / close
    out["six_ma_bandwidth_change_4"] = out["six_ma_bandwidth"].diff(4)
    return out


def _side_aligned_pair_frame(
    featured: pd.DataFrame,
    *,
    window: int,
    side: str,
    prefix: str,
) -> pd.DataFrame:
    """Return one side-aligned view for a named pair-cross feature family."""

    if side not in {"long", "short"}:
        raise ValueError(f"side must be long|short, got {side!r}")
    up = featured[f"{prefix}_cross_up_count_{window}"].astype(int)
    down = featured[f"{prefix}_cross_down_count_{window}"].astype(int)
    up_breadth = featured[f"{prefix}_cross_up_breadth_{window}"].astype(int)
    down_breadth = featured[f"{prefix}_cross_down_breadth_{window}"].astype(int)
    if side == "long":
        directional, opposite = up, down
        directional_breadth, opposite_breadth = up_breadth, down_breadth
        alignment = featured[f"{prefix}_up_alignment"].astype(int)
    else:
        directional, opposite = down, up
        directional_breadth, opposite_breadth = down_breadth, up_breadth
        alignment = featured[f"{prefix}_down_alignment"].astype(int)
    return pd.DataFrame(
        {
            "directional_cross_count": directional,
            "opposite_cross_count": opposite,
            "cross_imbalance": directional - opposite,
            "cross_churn": directional + opposite,
            "directional_cross_breadth": directional_breadth,
            "opposite_cross_breadth": opposite_breadth,
            "current_alignment": alignment,
        },
        index=featured.index,
    )


def side_aligned_cross_frame(
    featured: pd.DataFrame,
    *,
    window: int,
    side: str,
) -> pd.DataFrame:
    """Return full-length cross features whose positive direction matches ``side``."""

    return _side_aligned_pair_frame(
        featured,
        window=window,
        side=side,
        prefix="ema",
    )


def side_aligned_six_ma_cross_frame(
    featured: pd.DataFrame,
    *,
    window: int,
    side: str,
) -> pd.DataFrame:
    """Return side-aligned directional crosses for the true six-line bundle."""

    return _side_aligned_pair_frame(
        featured,
        window=window,
        side=side,
        prefix="six_ma",
    )
