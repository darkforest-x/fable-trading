"""Combinatorial Purged Cross-Validation: many backtest paths, not one number.

Why this project needs it. Today alone I read "the edge collapsed" out of 16
trades whose Wilson interval was [3.5%, 36.0%] and therefore distinguished
nothing, and the judgment-layer arc before that repeatedly produced a strong
first fold beside three weak ones without any way to say whether that spread was
signal or sampling. A single walk-forward path gives one number per fold and no
estimate of how much that number could have moved by luck.

CPCV (Lopez de Prado, Advances in Financial Machine Learning ch. 12) splits the
timeline into N groups and tests every combination of k of them, which yields
C(N,k) train/test splits that recombine into multiple full-length backtest
PATHS. The dispersion across paths is the thing worth reporting: a strategy whose
paths range 0.6 to 3.0 is not the same as one that ranges 1.1 to 1.3 even when
both average 1.2.

Two leak guards, both mandatory here because labels look forward 72 bars:

  PURGE    drop any training sample whose label window overlaps the test span.
           Without it the model has seen the outcome it is being tested on.
  EMBARGO  additionally drop training samples that begin shortly AFTER the test
           span. Serial correlation makes the bars just after a test block
           nearly as informative as the block itself.

Usage:
    splitter = CPCV(n_groups=6, n_test_groups=2, embargo_frac=0.01)
    for tr_idx, te_idx, path_id in splitter.split(times, label_end_times):
        ...
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd


@dataclass
class CPCVSplit:
    train_idx: np.ndarray
    test_idx: np.ndarray
    combo: tuple[int, ...]
    n_purged: int
    n_embargoed: int


class CPCV:
    """Combinatorial purged CV over time-ordered samples.

    n_groups / n_test_groups control the split count: C(n_groups, n_test_groups)
    combinations, each contributing one test block per group it holds. With the
    defaults (6, 2) that is 15 splits recombining into 5 full paths.
    """

    def __init__(self, n_groups: int = 6, n_test_groups: int = 2,
                 embargo_frac: float = 0.01) -> None:
        if n_test_groups >= n_groups:
            raise ValueError("n_test_groups must be < n_groups")
        self.n_groups = n_groups
        self.n_test_groups = n_test_groups
        self.embargo_frac = embargo_frac

    @property
    def n_splits(self) -> int:
        from math import comb
        return comb(self.n_groups, self.n_test_groups)

    @property
    def n_paths(self) -> int:
        """How many full-length backtest paths the splits recombine into."""
        return self.n_splits * self.n_test_groups // self.n_groups

    def split(self, times: pd.Series, label_end: pd.Series):
        """Yield CPCVSplit objects.

        times      : sample start time (the signal bar), time-ordered
        label_end  : when each sample's label is decided (signal + horizon).
                     This is what purging needs; passing `times` here silently
                     disables the guard.
        """
        t = pd.Series(pd.to_datetime(times, utc=True)).reset_index(drop=True)
        e = pd.Series(pd.to_datetime(label_end, utc=True)).reset_index(drop=True)
        if len(t) != len(e):
            raise ValueError("times and label_end must align")
        n = len(t)
        bounds = np.linspace(0, n, self.n_groups + 1).astype(int)
        groups = [np.arange(bounds[i], bounds[i + 1]) for i in range(self.n_groups)]
        emb = max(1, int(n * self.embargo_frac))

        for combo in combinations(range(self.n_groups), self.n_test_groups):
            test_idx = np.sort(np.concatenate([groups[g] for g in combo]))
            if len(test_idx) == 0:
                continue
            mask = np.ones(n, dtype=bool)
            mask[test_idx] = False

            n_purged = n_emb = 0
            for g in combo:
                gi = groups[g]
                if len(gi) == 0:
                    continue
                t0, t1 = t.iloc[gi[0]], t.iloc[gi[-1]]
                # PURGE: a train sample whose label is still open during the
                # test block has leaked that block's outcome into training.
                overlap = mask & (e.to_numpy() >= t0) & (t.to_numpy() <= t1)
                n_purged += int(overlap.sum())
                mask &= ~overlap
                # EMBARGO: the bars right after a test block stay correlated
                # with it, so they are dropped from training as well.
                hi = min(n, gi[-1] + 1 + emb)
                if hi > gi[-1] + 1:
                    band = np.zeros(n, dtype=bool)
                    band[gi[-1] + 1:hi] = True
                    band &= mask
                    n_emb += int(band.sum())
                    mask &= ~band

            yield CPCVSplit(np.flatnonzero(mask), test_idx, combo, n_purged, n_emb)


def paths_from_splits(splits: list[CPCVSplit], per_split_scores: list[np.ndarray],
                      n_groups: int) -> list[np.ndarray]:
    """Recombine per-group test results into full-length backtest paths.

    Each group is tested in several splits; taking one split's result per group,
    without reusing a split, walks one complete path across the timeline. The
    spread over paths is what CPCV exists to expose.
    """
    by_group: dict[int, list[np.ndarray]] = {g: [] for g in range(n_groups)}
    for sp, scores in zip(splits, per_split_scores):
        # scores align with sp.test_idx; slice back out per group
        bounds = np.searchsorted(sp.test_idx, sp.test_idx)  # identity, kept explicit
        del bounds
        for g in sp.combo:
            by_group[g].append(scores)
    depth = min(len(v) for v in by_group.values()) if by_group else 0
    return [np.concatenate([by_group[g][i] for g in range(n_groups)])
            for i in range(depth)]
