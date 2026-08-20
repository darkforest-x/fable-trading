"""The standard this project accepts on, written down once.

CLAUDE.md states it and the weak-model list opens with the way it gets missed:
AUC is a reference quantity, not a success criterion -- v1 scored AUC 0.59 and
lost money. What counts is

    top-decile net return, after a 0.2% round trip, is positive
    permutation p < 0.01
    the pool beats its matched random control

Three conditions, all required. Each of them exists because leaving it out
produced a wrong verdict at least once:

  net after cost      a gross edge smaller than the fee is not an edge
  permutation         a top decile of five events is noise-sized
  matched control     pool-internal metrics cannot see beta; the 100x6m pool
                      reported +16.9bp of which +7.2bp was simply being short

The numbers here are owner decisions carried from CLAUDE.md, not defaults
chosen by this module. Every one is an explicit argument, so an experiment that
wants a different cost has to say so in its own call and in its own report --
CLAUDE.md reserves cost assumptions, barrier parameters and thresholds to the
owner, and a silent default is how those get changed without anyone deciding to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from yoyo.evaluation.permutation import (
    DEFAULT_ALPHA,
    PermutationResult,
    permutation_test,
    top_decile_mean,
)

#: CLAUDE.md's stage-3 acceptance cost: 0.2% round trip. An owner decision.
ACCEPTANCE_ROUND_TRIP_COST = 0.002


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict, with the number that produced it."""

    name: str
    passed: bool
    value: float
    threshold: float
    detail: str

    def __str__(self) -> str:  # pragma: no cover - presentation
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.name}: {self.value:.6g} vs {self.threshold:.6g} -- {self.detail}"


@dataclass(frozen=True)
class EconomicVerdict:
    gates: Sequence[GateResult]
    n_events: int

    @property
    def accepted(self) -> bool:
        """All three, or none. A partial pass is a rejection with a nice number."""
        return bool(self.gates) and all(gate.passed for gate in self.gates)

    @property
    def failed_gates(self) -> Sequence[str]:
        return [gate.name for gate in self.gates if not gate.passed]

    def summary(self) -> str:
        head = "ACCEPTED" if self.accepted else f"REJECTED ({', '.join(self.failed_gates)})"
        return "\n".join([f"{head} over {self.n_events} events", *(str(g) for g in self.gates)])


def evaluate_economic_gates(
    scores: Sequence[float],
    gross_returns: Sequence[float],
    control_gross_returns: Sequence[float],
    *,
    round_trip_cost: float = ACCEPTANCE_ROUND_TRIP_COST,
    alpha: float = DEFAULT_ALPHA,
    decile_fraction: float = 0.10,
    n_permutations: int = 10_000,
    seed: int = 20260819,
    permutation: Optional[PermutationResult] = None,
) -> EconomicVerdict:
    """Run all three gates and return every number, passing or not.

    `control_gross_returns` is required rather than optional. Making it optional
    would let a caller omit the one comparison that catches beta, and the
    omission would look like an ordinary short call signature.
    """
    scores_array = np.asarray(scores, dtype=float)
    gross = np.asarray(gross_returns, dtype=float)
    control = np.asarray(control_gross_returns, dtype=float)
    if scores_array.shape != gross.shape:
        raise ValueError("scores and gross_returns must be the same length")
    if scores_array.size == 0:
        raise ValueError("no events to judge")
    if control.size == 0:
        raise ValueError(
            "no matched controls. A directional result without its control cannot be "
            "distinguished from the period's beta, so this is a refusal, not a default."
        )

    decile_gross = top_decile_mean(scores_array, gross, fraction=decile_fraction)
    decile_net = decile_gross - round_trip_cost
    control_net = float(np.mean(control)) - round_trip_cost

    result = permutation or permutation_test(
        scores_array,
        gross,
        statistic=lambda s, o: top_decile_mean(s, o, fraction=decile_fraction),
        n_permutations=n_permutations,
        alternative="greater",
        seed=seed,
    )

    gates = (
        GateResult(
            name="net_return_after_cost",
            passed=bool(decile_net > 0.0),
            value=decile_net,
            threshold=0.0,
            detail=(
                f"top {decile_fraction:.0%} gross {decile_gross * 1e4:.1f}bp less "
                f"{round_trip_cost * 1e4:.0f}bp round trip"
            ),
        ),
        GateResult(
            name="permutation",
            passed=bool(result.p_value < alpha),
            value=result.p_value,
            threshold=alpha,
            detail=f"{result.n_permutations} shuffles of the outcomes against fixed scores",
        ),
        GateResult(
            name="beats_matched_control",
            passed=bool(decile_net > control_net),
            value=(decile_net - control_net) * 1e4,
            threshold=0.0,
            detail=(
                f"edge over matched random entry in bp; control net "
                f"{control_net * 1e4:.1f}bp over {control.size} draws"
            ),
        ),
    )
    return EconomicVerdict(gates=gates, n_events=int(scores_array.size))
