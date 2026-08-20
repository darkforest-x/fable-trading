"""Time splits with the purge that makes them honest.

A chronological split is not enough. A label computed over the next `horizon`
bars is still being written while the next split has already begun, so a
training event whose label window reaches past the split point has seen test
bars. The fix is arithmetic, not judgement: shrink the right edge of train by
the horizon, and hold an embargo gap on the left edge of test wide enough to
cover the longest feature lookback.

Anchored (expanding) folds rather than rolling ones, because the question this
project asks is "does the edge survive into the next regime", and a rolling
window answers a different question by also changing how much history the model
saw.

Consolidated from yoyo-eth's walkforward.build_folds (fold layout, inner-val
early stopping, horizon-shrunk right edge) and darkforest-one's purge design.
The leakage checks are new: both source repositories relied on the split
function being written correctly, and neither asserted it afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np


class SplitLeakageError(ValueError):
    """A split that lets a training label see test bars. Never a warning."""


@dataclass(frozen=True)
class Fold:
    """One anchored fold, in bar positions. Half-open intervals [lo, hi)."""

    index: int
    train_end: int
    test_lo: int
    test_hi: int

    @property
    def n_test_bars(self) -> int:
        return self.test_hi - self.test_lo


def build_anchored_folds(
    n_bars: int, n_folds: int, initial_train_frac: float
) -> List[Fold]:
    """Expanding-train folds over the tail (1 - initial_train_frac) of the bars.

    Fold k trains on everything before its test slice, so later folds see more
    history -- which is what a live system would have had.
    """
    if n_folds < 1:
        raise ValueError("n_folds must be at least 1")
    if not 0.0 < initial_train_frac < 1.0:
        raise ValueError("initial_train_frac must be strictly between 0 and 1")
    if n_bars < n_folds:
        raise ValueError(f"{n_bars} bars cannot be split into {n_folds} folds")

    tail = 1.0 - initial_train_frac
    folds: List[Fold] = []
    for k in range(n_folds):
        lo = initial_train_frac + tail * k / n_folds
        hi = initial_train_frac + tail * (k + 1) / n_folds
        test_lo = int(n_bars * lo)
        folds.append(
            Fold(index=k, train_end=test_lo, test_lo=test_lo, test_hi=int(n_bars * hi))
        )
    return folds


def assign_splits(
    decision_positions: Sequence[int],
    fold: Fold,
    *,
    horizon_bars: int,
    gap_bars: int,
    inner_val_start: int,
) -> np.ndarray:
    """Label each event train / val / test / dropped for one fold.

    Two separate subtractions, and they do different jobs:

      train and val stop `horizon_bars` before their right edge, so no label
      window reaches into what comes next
      test starts `gap_bars` after its left edge, so no test event's *features*
      look back into training bars

    Anything satisfying neither is "dropped". Dropping events is the correct
    outcome at a boundary; assigning them to whichever side is nearer is how a
    split quietly leaks.
    """
    if horizon_bars < 0 or gap_bars < 0:
        raise ValueError("horizon_bars and gap_bars must be non-negative")
    if inner_val_start > fold.train_end:
        raise SplitLeakageError(
            f"inner_val_start={inner_val_start} is past train_end={fold.train_end}"
        )

    pos = np.asarray(decision_positions, dtype=np.int64)
    is_train = pos < inner_val_start
    is_val = (pos >= inner_val_start + gap_bars) & (pos < fold.train_end - horizon_bars)
    is_test = (pos >= fold.test_lo + gap_bars) & (pos < fold.test_hi)
    return np.select([is_train, is_val, is_test], ["train", "val", "test"], default="dropped")


def assert_no_split_leakage(
    decision_positions: Sequence[int],
    splits: Sequence[str],
    fold: Fold,
    *,
    horizon_bars: int,
    gap_bars: int,
) -> None:
    """Re-derive the property from the assignment rather than trusting it.

    Checks what the split *claims* against what the positions actually are. A
    split function with an off-by-one still produces plausible group sizes, so
    the sizes are not evidence.
    """
    pos = np.asarray(decision_positions, dtype=np.int64)
    split = np.asarray(splits, dtype=object)

    train_like = pos[(split == "train") | (split == "val")]
    if train_like.size and train_like.max() + horizon_bars >= fold.test_lo:
        offender = int(train_like.max())
        raise SplitLeakageError(
            f"a train/val event at bar {offender} has a {horizon_bars}-bar label window "
            f"reaching bar {offender + horizon_bars}, at or past test_lo={fold.test_lo}"
        )

    test_pos = pos[split == "test"]
    if test_pos.size:
        if test_pos.min() < fold.test_lo + gap_bars:
            raise SplitLeakageError(
                f"a test event at bar {int(test_pos.min())} sits inside the {gap_bars}-bar "
                f"embargo after test_lo={fold.test_lo}"
            )
        if test_pos.max() >= fold.test_hi:
            raise SplitLeakageError(
                f"a test event at bar {int(test_pos.max())} is past test_hi={fold.test_hi}"
            )

    overlap = set(train_like.tolist()) & set(test_pos.tolist())
    if overlap:
        raise SplitLeakageError(f"{len(overlap)} events are in both train/val and test")
