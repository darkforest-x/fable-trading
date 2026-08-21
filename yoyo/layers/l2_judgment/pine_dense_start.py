"""Causal six-MA dense-start features for the ETH 15-minute Pine strategy.

Inputs are ``close``, Pine/Wilder ``atr`` and the close-derived
``SMA/EMA 20/60/120`` bundle.  At a decision bar ``t`` the formation stage is
strictly prior: pairwise order flips, directional cross counts and
ATR-normalized bandwidth use ``[t-window, t-1]``.  The release stage may use
the completed bar ``t``: current ordering, close outside the rope, ATR
expansion and MA slopes from ``t-slope_lag`` through ``t``.  No output uses a
future row; entry remains the caller's ``open[t+1]``.

The 15 unordered pairs measure direction-agnostic churn.  Direction uses only
the 12 cross-period fast/slow pairs from :mod:`pine_cross_features`; same-period
SMA/EMA relations never receive an invented golden/death-cross meaning.
Continuous component scores are transparent LR-interface diagnostics.  Gate
decisions use explicit :class:`DenseStartProfile` thresholds instead of a
trained model or a fitted score threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping

import numpy as np
import pandas as pd

from yoyo.layers.l2_judgment.pine_cross_features import (
    SIX_MA_COLUMNS,
    SIX_MA_DIRECTIONAL_PAIRS,
    add_six_ma_cross_count_features,
)


PAIRWISE_UNORDERED_PAIRS = tuple(combinations(SIX_MA_COLUMNS, 2))
DEFAULT_DENSITY_WINDOW = 12
DEFAULT_ATR_RELEASE_WINDOW = 8
DEFAULT_SLOPE_LAG = 3
SLOPE_SCORE_REFERENCE_ATR_PER_BAR = 0.10


@dataclass(frozen=True)
class DenseStartProfile:
    """One preregistered ordinal strictness profile for the composite gate."""

    profile_id: str
    min_pre_pairwise_crosses: int
    max_pre_bandwidth_atr_mean: float
    min_current_alignment: int
    min_pre_cross_imbalance: int
    min_slope_coherence: float
    min_atr_release_ratio: float

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id must be non-empty")
        if self.min_pre_pairwise_crosses < 0:
            raise ValueError("min_pre_pairwise_crosses must be non-negative")
        if not np.isfinite(self.max_pre_bandwidth_atr_mean) or self.max_pre_bandwidth_atr_mean <= 0:
            raise ValueError("max_pre_bandwidth_atr_mean must be finite and positive")
        if not 0 <= self.min_current_alignment <= len(SIX_MA_DIRECTIONAL_PAIRS):
            raise ValueError("min_current_alignment is outside the 12-pair range")
        if not -len(SIX_MA_DIRECTIONAL_PAIRS) <= self.min_pre_cross_imbalance <= len(
            SIX_MA_DIRECTIONAL_PAIRS
        ):
            raise ValueError("min_pre_cross_imbalance is outside the directional-pair range")
        if not 0.0 <= self.min_slope_coherence <= 1.0:
            raise ValueError("min_slope_coherence must be in [0, 1]")
        if not np.isfinite(self.min_atr_release_ratio) or self.min_atr_release_ratio <= 0:
            raise ValueError("min_atr_release_ratio must be finite and positive")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "DenseStartProfile":
        """Parse one JSON-compatible preregistration row fail-closed."""

        return cls(
            profile_id=str(row["profile_id"]),
            min_pre_pairwise_crosses=int(row["min_pre_pairwise_crosses"]),
            max_pre_bandwidth_atr_mean=float(row["max_pre_bandwidth_atr_mean"]),
            min_current_alignment=int(row["min_current_alignment"]),
            min_pre_cross_imbalance=int(row["min_pre_cross_imbalance"]),
            min_slope_coherence=float(row["min_slope_coherence"]),
            min_atr_release_ratio=float(row["min_atr_release_ratio"]),
        )


def _validate_windows(density_window: int, atr_release_window: int, slope_lag: int) -> None:
    for name, value in (
        ("density_window", density_window),
        ("atr_release_window", atr_release_window),
        ("slope_lag", slope_lag),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")


def _pairwise_flip_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return 15 causal undirected crossover/crossunder event columns."""

    events: dict[str, pd.Series] = {}
    for left, right in PAIRWISE_UNORDERED_PAIRS:
        difference = frame[left].astype(float) - frame[right].astype(float)
        crossed = (
            (difference.gt(0.0) & difference.shift(1).le(0.0))
            | (difference.lt(0.0) & difference.shift(1).ge(0.0))
        ).fillna(False)
        events[f"{left}__{right}"] = crossed.astype(int)
    return pd.DataFrame(events, index=frame.index)


def _binary_entropy(probability: pd.Series) -> pd.Series:
    """Return base-2 binary entropy in [0, 1] without log-at-zero warnings."""

    values = probability.to_numpy(dtype=float)
    entropy = np.full(len(values), np.nan, dtype=float)
    finite = np.isfinite(values)
    clipped = np.clip(values[finite], 0.0, 1.0)
    local = np.zeros(len(clipped), dtype=float)
    interior = (clipped > 0.0) & (clipped < 1.0)
    local[interior] = -(
        clipped[interior] * np.log2(clipped[interior])
        + (1.0 - clipped[interior]) * np.log2(1.0 - clipped[interior])
    )
    entropy[finite] = local
    return pd.Series(entropy, index=probability.index, dtype=float)


def add_six_ma_dense_start_features(
    frame: pd.DataFrame,
    *,
    density_window: int = DEFAULT_DENSITY_WINDOW,
    atr_release_window: int = DEFAULT_ATR_RELEASE_WINDOW,
    slope_lag: int = DEFAULT_SLOPE_LAG,
) -> pd.DataFrame:
    """Add causal dense, compression, direction and release feature columns.

    Source columns are ``close``, ``atr`` and ``SMA/EMA 20/60/120``.  The
    longest prior window is ``density_window`` and explicitly ends at ``t-1``;
    ATR release uses ``atr[t]`` over the mean of the preceding
    ``atr_release_window`` ATR values; slope features use MA values at ``t`` and
    ``t-slope_lag``.  All resulting features are invariant to rows after ``t``.
    """

    _validate_windows(density_window, atr_release_window, slope_lag)
    required = set(SIX_MA_COLUMNS) | {"close", "atr"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing dense-start columns: {missing}")

    out = add_six_ma_cross_count_features(frame, windows=(density_window,))
    mas = out.loc[:, list(SIX_MA_COLUMNS)].astype(float)
    atr = out["atr"].astype(float).replace(0.0, np.nan)
    close = out["close"].astype(float)
    ready = mas.notna().all(axis=1) & atr.notna() & atr.gt(0.0) & close.notna()

    pair_events = _pairwise_flip_frame(out)
    prior_pair_events = pair_events.shift(1)
    out[f"dense_pre_pairwise_cross_count_{density_window}"] = prior_pair_events.sum(
        axis=1
    ).rolling(density_window, min_periods=density_window).sum()
    out[f"dense_pre_pairwise_cross_breadth_{density_window}"] = prior_pair_events.rolling(
        density_window, min_periods=density_window
    ).max().sum(axis=1)

    rope_upper = mas.max(axis=1)
    rope_lower = mas.min(axis=1)
    bandwidth_atr = ((rope_upper - rope_lower) / atr).where(ready)
    prior_bandwidth = bandwidth_atr.shift(1)
    out["dense_rope_upper"] = rope_upper.where(ready)
    out["dense_rope_lower"] = rope_lower.where(ready)
    out["dense_bandwidth_atr"] = bandwidth_atr
    out[f"dense_pre_bandwidth_atr_mean_{density_window}"] = prior_bandwidth.rolling(
        density_window, min_periods=density_window
    ).mean()
    out[f"dense_pre_bandwidth_atr_max_{density_window}"] = prior_bandwidth.rolling(
        density_window, min_periods=density_window
    ).max()
    out[f"dense_pre_bandwidth_atr_change_{density_window}"] = (
        prior_bandwidth - bandwidth_atr.shift(density_window + 1)
    )

    up = out[f"six_ma_cross_up_count_{density_window}"].astype(float).shift(1)
    down = out[f"six_ma_cross_down_count_{density_window}"].astype(float).shift(1)
    out[f"dense_pre_cross_up_count_{density_window}"] = up
    out[f"dense_pre_cross_down_count_{density_window}"] = down
    out[f"dense_pre_cross_churn_{density_window}"] = up + down
    out[f"dense_pre_cross_imbalance_long_{density_window}"] = up - down
    out[f"dense_pre_cross_imbalance_short_{density_window}"] = down - up

    long_alignment = out["six_ma_up_alignment"].astype(float)
    short_alignment = out["six_ma_down_alignment"].astype(float)
    out["dense_current_alignment_long"] = long_alignment
    out["dense_current_alignment_short"] = short_alignment
    out["dense_order_entropy_long"] = _binary_entropy(
        long_alignment / float(len(SIX_MA_DIRECTIONAL_PAIRS))
    )
    out["dense_order_entropy_short"] = _binary_entropy(
        short_alignment / float(len(SIX_MA_DIRECTIONAL_PAIRS))
    )

    atr_prior_mean = atr.shift(1).rolling(
        atr_release_window, min_periods=atr_release_window
    ).mean()
    out[f"dense_atr_release_ratio_{atr_release_window}"] = atr / atr_prior_mean
    out["dense_breakout_distance_atr_long"] = (close - rope_upper) / atr
    out["dense_breakout_distance_atr_short"] = (rope_lower - close) / atr

    slopes_atr = (mas - mas.shift(slope_lag)).div(atr * float(slope_lag), axis=0)
    out[f"dense_slope_coherence_long_{slope_lag}"] = slopes_atr.gt(0.0).mean(axis=1).where(
        slopes_atr.notna().all(axis=1)
    )
    out[f"dense_slope_coherence_short_{slope_lag}"] = slopes_atr.lt(0.0).mean(axis=1).where(
        slopes_atr.notna().all(axis=1)
    )
    out[f"dense_signed_mean_slope_atr_long_{slope_lag}"] = slopes_atr.mean(axis=1)
    out[f"dense_signed_mean_slope_atr_short_{slope_lag}"] = -slopes_atr.mean(axis=1)

    density_score = (
        out[f"dense_pre_pairwise_cross_count_{density_window}"] / 4.0
    ).clip(0.0, 1.0)
    compression_score = (
        1.0 - out[f"dense_pre_bandwidth_atr_mean_{density_window}"] / 4.0
    ).clip(0.0, 1.0)
    out["dense_density_score"] = density_score
    out["dense_compression_score"] = compression_score

    churn = out[f"dense_pre_cross_churn_{density_window}"].replace(0.0, np.nan)
    directional_share_long = up.div(churn).fillna(0.0).clip(0.0, 1.0)
    directional_share_short = down.div(churn).fillna(0.0).clip(0.0, 1.0)
    direction_score_long = (
        long_alignment / float(len(SIX_MA_DIRECTIONAL_PAIRS)) + directional_share_long
    ) / 2.0
    direction_score_short = (
        short_alignment / float(len(SIX_MA_DIRECTIONAL_PAIRS)) + directional_share_short
    ) / 2.0
    out["dense_direction_score_long"] = direction_score_long.clip(0.0, 1.0)
    out["dense_direction_score_short"] = direction_score_short.clip(0.0, 1.0)

    atr_release_score = (
        (out[f"dense_atr_release_ratio_{atr_release_window}"] - 0.8) / 0.4
    ).clip(0.0, 1.0)
    long_slope_score = (
        out[f"dense_signed_mean_slope_atr_long_{slope_lag}"]
        / SLOPE_SCORE_REFERENCE_ATR_PER_BAR
    ).clip(0.0, 1.0)
    short_slope_score = (
        out[f"dense_signed_mean_slope_atr_short_{slope_lag}"]
        / SLOPE_SCORE_REFERENCE_ATR_PER_BAR
    ).clip(0.0, 1.0)
    release_score_long = (
        out["dense_breakout_distance_atr_long"].gt(0.0).astype(float)
        + out[f"dense_slope_coherence_long_{slope_lag}"]
        + long_slope_score
        + atr_release_score
    ) / 4.0
    release_score_short = (
        out["dense_breakout_distance_atr_short"].gt(0.0).astype(float)
        + out[f"dense_slope_coherence_short_{slope_lag}"]
        + short_slope_score
        + atr_release_score
    ) / 4.0
    out["dense_release_score_long"] = release_score_long
    out["dense_release_score_short"] = release_score_short
    out["dense_start_score_long"] = (
        density_score + compression_score + direction_score_long + release_score_long
    ) / 4.0
    out["dense_start_score_short"] = (
        density_score + compression_score + direction_score_short + release_score_short
    ) / 4.0

    readiness_columns = [
        f"dense_pre_pairwise_cross_count_{density_window}",
        f"dense_pre_pairwise_cross_breadth_{density_window}",
        f"dense_pre_bandwidth_atr_mean_{density_window}",
        f"dense_pre_bandwidth_atr_max_{density_window}",
        f"dense_pre_cross_imbalance_long_{density_window}",
        f"dense_pre_cross_imbalance_short_{density_window}",
        f"dense_atr_release_ratio_{atr_release_window}",
        f"dense_slope_coherence_long_{slope_lag}",
        f"dense_slope_coherence_short_{slope_lag}",
    ]
    out["dense_start_ready"] = ready & out[readiness_columns].notna().all(axis=1)
    return out


def dense_start_gate_mask(
    featured: pd.DataFrame,
    profile: DenseStartProfile,
    *,
    side: str,
    density_window: int = DEFAULT_DENSITY_WINDOW,
    atr_release_window: int = DEFAULT_ATR_RELEASE_WINDOW,
    slope_lag: int = DEFAULT_SLOPE_LAG,
) -> pd.Series:
    """Return the explicit composite gate for one side on an enriched frame."""

    _validate_windows(density_window, atr_release_window, slope_lag)
    if side not in {"long", "short"}:
        raise ValueError(f"side must be long|short, got {side!r}")
    release_motion = (
        featured[f"dense_slope_coherence_{side}_{slope_lag}"].ge(
            profile.min_slope_coherence
        )
        | featured[f"dense_atr_release_ratio_{atr_release_window}"].ge(
            profile.min_atr_release_ratio
        )
    )
    return (
        featured["dense_start_ready"].fillna(False).astype(bool)
        & featured[f"dense_pre_pairwise_cross_count_{density_window}"].ge(
            profile.min_pre_pairwise_crosses
        )
        & featured[f"dense_pre_bandwidth_atr_mean_{density_window}"].le(
            profile.max_pre_bandwidth_atr_mean
        )
        & featured[f"dense_current_alignment_{side}"].ge(profile.min_current_alignment)
        & featured[f"dense_pre_cross_imbalance_{side}_{density_window}"].ge(
            profile.min_pre_cross_imbalance
        )
        & featured[f"dense_breakout_distance_atr_{side}"].gt(0.0)
        & featured[f"dense_signed_mean_slope_atr_{side}_{slope_lag}"].gt(0.0)
        & release_motion
    ).fillna(False)
