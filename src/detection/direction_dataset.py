"""Causal manifest primitives for the MA206 long/short/no-trade challenger.

Only labels may inspect the fixed future TP5/SL2 horizon. Every image and
feature row ends at the signal bar, and every manifest row is strictly before
the frozen judgment holdout after applying the barrier purge window.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import pandas as pd

from src.data.bars import purge_window
from src.judgment.labeling import BarrierOutcome
from src.judgment.train import HOLDOUT_START, TRAIN_FRACTION

DirectionLabel = Literal["long", "short", "no_trade"]


class HoldoutLeakError(RuntimeError):
    """Raised before output when a direction manifest reaches the holdout."""


class InsufficientHistoryError(RuntimeError):
    """Raised when a signal cannot provide the fixed causal lookback."""


def classify_direction(
    long_outcome: BarrierOutcome,
    short_outcome: BarrierOutcome,
) -> DirectionLabel:
    """Assign a class only when exactly one frozen directional label wins."""
    if long_outcome.label == 1 and short_outcome.label == 0:
        return "long"
    if short_outcome.label == 1 and long_outcome.label == 0:
        return "short"
    return "no_trade"


def dedupe_candidate_indices(
    long_indices: Iterable[int],
    short_indices: Iterable[int],
    *,
    min_gap_bars: int,
) -> list[int]:
    """Keep the earliest signal when either directional scanner fires nearby."""
    selected: list[int] = []
    for signal_i in sorted(set(long_indices) | set(short_indices)):
        if not selected or signal_i - selected[-1] >= min_gap_bars:
            selected.append(signal_i)
    return selected


def causal_window(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    lookback_bars: int,
) -> pd.DataFrame:
    """Return exactly `lookback_bars` ending at the signal bar, never after it."""
    start_i = signal_i - lookback_bars + 1
    if start_i < 0 or signal_i >= len(frame):
        raise InsufficientHistoryError(
            f"signal_i={signal_i} cannot provide lookback_bars={lookback_bars}"
        )
    return frame.iloc[start_i : signal_i + 1].copy().reset_index(drop=True)


def assign_temporal_splits(
    manifest: pd.DataFrame,
    *,
    horizon_bars: int,
    bar: str,
) -> pd.DataFrame:
    """Assign globally chronological train/val rows with both purge boundaries."""
    data = manifest.copy()
    data["signal_time"] = pd.to_datetime(data["signal_time"], utc=True, errors="raise")
    data = data.sort_values("signal_time").reset_index(drop=True)
    purge = purge_window(horizon_bars, bar)
    latest_allowed = HOLDOUT_START - purge
    if data.empty or data["signal_time"].max() >= latest_allowed:
        observed = None if data.empty else data["signal_time"].max()
        raise HoldoutLeakError(
            f"direction manifest reaches holdout purge boundary: {observed} >= {latest_allowed}"
        )

    split_i = int(len(data) * TRAIN_FRACTION)
    if split_i <= 0 or split_i >= len(data):
        raise InsufficientHistoryError(
            f"manifest rows={len(data)} cannot form chronological train/val splits"
        )
    val_start = data.iloc[split_i:]["signal_time"].min()
    train = data.iloc[:split_i]
    train = train[train["signal_time"] < val_start - purge].copy()
    val = data.iloc[split_i:].copy()
    train["split"] = "train"
    val["split"] = "val"
    return pd.concat([train, val], ignore_index=True)
