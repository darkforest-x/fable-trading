"""Causal multi-speed trend-quality features for the ETH 15-minute Pine arm.

At completed decision bar ``t`` the EWMAC components use ``close`` through
``t`` and Pine/Wilder ``atr[t]``.  The Donchian components use ``close[t]``
against channels formed only from ``high``/``low`` in ``[t-window, t-1]``.
The six-MA soft component consumes the already-causal
``dense_start_score_long/short`` values.  No feature reads a row after ``t``;
the caller continues to enter at ``open[t+1]``.

The external trend rules are independently implemented from their published
formulae.  No third-party strategy source is copied.  This module exposes
deterministic features and explicit thresholds only; it never trains a model,
changes a barrier, sizes a position, promotes an artifact or sends an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


DEFAULT_EWMAC_SPEED_PAIRS = ((8, 32), (16, 64), (32, 128))
DEFAULT_DONCHIAN_WINDOWS = (24, 48, 96)
DEFAULT_EWMAC_SATURATION_ATR = 2.0
DEFAULT_TREND_WEIGHT = 0.80
DEFAULT_DENSE_WEIGHT = 0.20


@dataclass(frozen=True)
class TrendEnsembleProfile:
    """One preregistered quality threshold for guarded V12F candidates."""

    profile_id: str
    minimum_quality: float

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id must be non-empty")
        if not np.isfinite(self.minimum_quality) or not 0.0 <= self.minimum_quality <= 1.0:
            raise ValueError("minimum_quality must be finite and in [0, 1]")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "TrendEnsembleProfile":
        """Parse a JSON-compatible preregistration row fail-closed."""

        return cls(
            profile_id=str(row["profile_id"]),
            minimum_quality=float(row["minimum_quality"]),
        )


def _validate_contract(
    ewmac_speed_pairs: Sequence[tuple[int, int]],
    donchian_windows: Sequence[int],
    ewmac_saturation_atr: float,
    trend_weight: float,
    dense_weight: float,
) -> None:
    if not ewmac_speed_pairs:
        raise ValueError("ewmac_speed_pairs must be non-empty")
    for pair in ewmac_speed_pairs:
        if len(pair) != 2:
            raise ValueError("each EWMAC speed pair must contain fast and slow")
        fast, slow = pair
        if (
            not isinstance(fast, int)
            or isinstance(fast, bool)
            or not isinstance(slow, int)
            or isinstance(slow, bool)
            or fast <= 0
            or slow <= fast
        ):
            raise ValueError("EWMAC speeds must be positive integers with fast < slow")
    if not donchian_windows:
        raise ValueError("donchian_windows must be non-empty")
    if any(
        not isinstance(window, int) or isinstance(window, bool) or window <= 1
        for window in donchian_windows
    ):
        raise ValueError("Donchian windows must be integers greater than one")
    if not np.isfinite(ewmac_saturation_atr) or ewmac_saturation_atr <= 0.0:
        raise ValueError("ewmac_saturation_atr must be finite and positive")
    weights = (float(trend_weight), float(dense_weight))
    if any(not np.isfinite(weight) or weight < 0.0 for weight in weights):
        raise ValueError("ensemble weights must be finite and non-negative")
    if not np.isclose(sum(weights), 1.0, atol=1e-12):
        raise ValueError("trend_weight and dense_weight must sum to one")


def add_trend_ensemble_features(
    frame: pd.DataFrame,
    *,
    ewmac_speed_pairs: Sequence[tuple[int, int]] = DEFAULT_EWMAC_SPEED_PAIRS,
    donchian_windows: Sequence[int] = DEFAULT_DONCHIAN_WINDOWS,
    ewmac_saturation_atr: float = DEFAULT_EWMAC_SATURATION_ATR,
    trend_weight: float = DEFAULT_TREND_WEIGHT,
    dense_weight: float = DEFAULT_DENSE_WEIGHT,
) -> pd.DataFrame:
    """Add causal EWMAC, prior-Donchian and side-quality feature columns.

    Source columns are ``high``, ``low``, ``close``, Pine/Wilder ``atr`` and
    ``dense_start_score_long/short`` plus ``dense_start_ready``.  EWMAC spans
    use all closes through ``t``.  Every Donchian window ends at ``t-1`` via
    an explicit one-row shift.  Outputs through ``t`` are invariant to any
    mutation after ``t``.
    """

    _validate_contract(
        ewmac_speed_pairs,
        donchian_windows,
        ewmac_saturation_atr,
        trend_weight,
        dense_weight,
    )
    required = {
        "high",
        "low",
        "close",
        "atr",
        "dense_start_ready",
        "dense_start_score_long",
        "dense_start_score_short",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing trend-ensemble columns: {missing}")

    out = frame.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    atr = out["atr"].astype(float).replace(0.0, np.nan)
    component_columns: list[str] = []
    ewmac_columns: list[str] = []
    donchian_columns: list[str] = []

    for fast, slow in ewmac_speed_pairs:
        fast_ema = close.ewm(span=fast, adjust=False).mean()
        slow_ema = close.ewm(span=slow, adjust=False).mean()
        raw_column = f"trend_ewmac_raw_atr_{fast}_{slow}"
        score_column = f"trend_ewmac_{fast}_{slow}"
        out[raw_column] = (fast_ema - slow_ema) / atr
        out[score_column] = np.tanh(out[raw_column] / ewmac_saturation_atr)
        component_columns.append(score_column)
        ewmac_columns.append(score_column)

    prior_high = high.shift(1)
    prior_low = low.shift(1)
    for window in donchian_windows:
        upper = prior_high.rolling(window, min_periods=window).max()
        lower = prior_low.rolling(window, min_periods=window).min()
        midpoint = (upper + lower) / 2.0
        half_range = ((upper - lower) / 2.0).replace(0.0, np.nan)
        score_column = f"trend_donchian_{window}"
        out[f"trend_donchian_upper_{window}"] = upper
        out[f"trend_donchian_lower_{window}"] = lower
        out[score_column] = ((close - midpoint) / half_range).clip(-1.0, 1.0)
        component_columns.append(score_column)
        donchian_columns.append(score_column)

    components = out.loc[:, component_columns].astype(float)
    all_components_ready = components.notna().all(axis=1)
    out["trend_ewmac_forecast"] = out.loc[:, ewmac_columns].mean(
        axis=1, skipna=False
    )
    out["trend_donchian_forecast"] = out.loc[:, donchian_columns].mean(
        axis=1, skipna=False
    )
    out["trend_ensemble_forecast"] = components.mean(axis=1, skipna=False)
    out["trend_ensemble_horizon_dispersion"] = components.std(
        axis=1, ddof=0, skipna=False
    )

    dense_ready = out["dense_start_ready"].fillna(False).astype(bool)
    for side, sign in (("long", 1.0), ("short", -1.0)):
        signed_components = components * sign
        out[f"trend_ewmac_support_{side}"] = (
            1.0 + sign * out["trend_ewmac_forecast"]
        ).div(2.0).clip(0.0, 1.0)
        out[f"trend_donchian_support_{side}"] = (
            1.0 + sign * out["trend_donchian_forecast"]
        ).div(2.0).clip(0.0, 1.0)
        out[f"trend_support_{side}"] = (
            1.0 + sign * out["trend_ensemble_forecast"]
        ).div(2.0).clip(0.0, 1.0)
        out[f"trend_component_consensus_{side}"] = signed_components.gt(0.0).mean(
            axis=1
        ).where(all_components_ready)
        dense_score = out[f"dense_start_score_{side}"].astype(float).clip(0.0, 1.0)
        out[f"trend_quality_{side}"] = (
            trend_weight * out[f"trend_support_{side}"]
            + dense_weight * dense_score
        )

    out["trend_ensemble_ready"] = (
        atr.notna()
        & atr.gt(0.0)
        & close.notna()
        & all_components_ready
        & dense_ready
        & out[["trend_quality_long", "trend_quality_short"]].notna().all(axis=1)
    )
    return out


def trend_ensemble_gate_mask(
    featured: pd.DataFrame,
    profile: TrendEnsembleProfile,
    *,
    side: str,
) -> pd.Series:
    """Return the single preregistered soft-quality threshold for one side."""

    if side not in {"long", "short"}:
        raise ValueError(f"side must be long|short, got {side!r}")
    required = {"trend_ensemble_ready", f"trend_quality_{side}"}
    missing = sorted(required - set(featured.columns))
    if missing:
        raise ValueError(f"missing trend-ensemble gate columns: {missing}")
    return (
        featured["trend_ensemble_ready"].fillna(False).astype(bool)
        & featured[f"trend_quality_{side}"].ge(profile.minimum_quality)
    ).fillna(False)
