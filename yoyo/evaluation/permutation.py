"""Permutation tests: is the ranking better than a shuffle of itself?

What it answers, and only this: given these events and these outcomes, does the
model's ORDER carry information. It holds the pool fixed, which is exactly why
it cannot see the failure that matched controls catch -- a pool standing on
short beta permutes to the same beta and reports p=0.5 while the money is real
and has nothing to do with the model. The two tests are not alternatives; a
directional result needs both (CLAUDE.md, and
docs/learnings/pool-internal-metrics-cannot-see-beta.md).

The project standard is p < 0.01, not 0.05. With a few hundred events and a
top-decile of five, 0.05 is reached by chance often enough that it stopped
being evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np

#: CLAUDE.md's acceptance standard for a ranking claim.
DEFAULT_ALPHA = 0.01
DEFAULT_N_PERMUTATIONS = 10_000


@dataclass(frozen=True)
class PermutationResult:
    statistic: float
    p_value: float
    n_permutations: int
    n_samples: int
    alternative: str
    null_mean: float
    null_std: float

    def passes(self, alpha: float = DEFAULT_ALPHA) -> bool:
        return self.p_value < alpha


def top_decile_mean(scores: np.ndarray, outcomes: np.ndarray, *, fraction: float = 0.10) -> float:
    """Mean outcome of the highest-scoring `fraction` of events."""
    k = max(1, int(np.ceil(len(scores) * fraction)))
    order = np.argsort(scores)[::-1][:k]
    return float(np.mean(outcomes[order]))


def spearman_statistic(scores: np.ndarray, outcomes: np.ndarray) -> float:
    from scipy import stats

    return float(stats.spearmanr(scores, outcomes).statistic)


def permutation_test(
    scores: Sequence[float],
    outcomes: Sequence[float],
    *,
    statistic: Callable[[np.ndarray, np.ndarray], float] = top_decile_mean,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    alternative: str = "greater",
    seed: int = 20260819,
) -> PermutationResult:
    """Shuffle the outcomes against fixed scores and count the ties and betters.

    The p-value uses the (r + 1) / (n + 1) form, which is the unbiased estimate
    and never returns exactly zero. A reported p=0.0000 from 10,000 shuffles
    invites being read as certainty, when what it means is "smaller than 1e-4".
    """
    scores_array = np.asarray(scores, dtype=float)
    outcomes_array = np.asarray(outcomes, dtype=float)
    if scores_array.shape != outcomes_array.shape:
        raise ValueError("scores and outcomes must be the same length")
    if scores_array.size < 2:
        raise ValueError("a permutation test needs at least two samples")
    if alternative not in ("greater", "less", "two-sided"):
        raise ValueError(f"unknown alternative {alternative!r}")

    observed = statistic(scores_array, outcomes_array)
    rng = np.random.default_rng(seed)
    null = np.empty(n_permutations, dtype=float)
    shuffled = outcomes_array.copy()
    for i in range(n_permutations):
        rng.shuffle(shuffled)
        null[i] = statistic(scores_array, shuffled)

    if alternative == "greater":
        extreme = int(np.sum(null >= observed))
    elif alternative == "less":
        extreme = int(np.sum(null <= observed))
    else:
        centre = float(np.mean(null))
        extreme = int(np.sum(np.abs(null - centre) >= abs(observed - centre)))

    return PermutationResult(
        statistic=observed,
        p_value=(extreme + 1) / (n_permutations + 1),
        n_permutations=n_permutations,
        n_samples=int(scores_array.size),
        alternative=alternative,
        null_mean=float(np.mean(null)),
        null_std=float(np.std(null)),
    )
