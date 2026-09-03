"""Causal MA-bundle position check applied only after an L1 model proposal.

The caller supplies a model proposal endpoint ``t`` and a LONG/SHORT
direction.  This module reads only ``close`` and the six trailing
SMA/EMA 20/60/120 values at ``t``.  A LONG passes when the current close is
strictly above the entire six-MA bundle; SHORT is the exact mirror below it.
There is deliberately no condition on ``t-1`` and no scan for a first cross.

This is a proposal gate, not a candidate generator: callers must not scan this
rule first and then pretend that a later model detection existed earlier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .data import ALL_MA_COLS


class ModelFirstStandingError(ValueError):
    """Raised when a model proposal cannot be checked causally."""


@dataclass(frozen=True)
class ModelFirstStandingDecision:
    """Auditable one-bar position decision for a model proposal."""

    direction: str
    proposal_end_i: int
    passed: bool
    current_close: float
    current_bundle_edge: float
    current_beyond_bundle: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON/CSV representation."""

        return asdict(self)


def _finite_row(frame: pd.DataFrame, index: int) -> tuple[float, np.ndarray]:
    required = ("close", *ALL_MA_COLS)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ModelFirstStandingError(f"missing columns: {missing}")
    close = float(frame.iloc[index]["close"])
    mas = frame.iloc[index].loc[list(ALL_MA_COLS)].to_numpy(dtype=float)
    values = np.concatenate(([close], mas))
    if not bool(np.isfinite(values).all()):
        raise ModelFirstStandingError(f"non-finite close/MA values at index {index}")
    return close, mas


def evaluate_model_first_standing(
    frame: pd.DataFrame,
    *,
    proposal_end_i: int,
    direction: str,
) -> ModelFirstStandingDecision:
    """Check whether a model proposal closes beyond the full MA bundle.

    Required columns are ``close`` plus :data:`ALL_MA_COLS`.  Only row
    ``proposal_end_i`` is read.  Equality does not count as standing beyond
    the bundle, which keeps the rule deterministic without an epsilon or a
    tunable distance threshold.
    """

    end = int(proposal_end_i)
    if end < 0 or end >= len(frame):
        raise ModelFirstStandingError("proposal endpoint is outside the frame")
    side = str(direction).upper()
    if side not in {"LONG", "SHORT"}:
        raise ModelFirstStandingError(f"unsupported direction: {direction}")

    current_close, current_mas = _finite_row(frame, end)
    if side == "LONG":
        current_edge = float(current_mas.max())
        current_beyond = current_close > current_edge
    else:
        current_edge = float(current_mas.min())
        current_beyond = current_close < current_edge

    return ModelFirstStandingDecision(
        direction=side,
        proposal_end_i=end,
        passed=bool(current_beyond),
        current_close=current_close,
        current_bundle_edge=current_edge,
        current_beyond_bundle=bool(current_beyond),
    )


def standing_decisions_equal(
    left: ModelFirstStandingDecision | Mapping[str, Any],
    right: ModelFirstStandingDecision | Mapping[str, Any],
    *,
    atol: float = 1e-12,
) -> bool:
    """Compare two position decisions with strict booleans and float tolerance."""

    a = left.to_dict() if isinstance(left, ModelFirstStandingDecision) else dict(left)
    b = right.to_dict() if isinstance(right, ModelFirstStandingDecision) else dict(right)
    scalar_keys = (
        "direction",
        "proposal_end_i",
        "passed",
        "current_beyond_bundle",
    )
    if any(a[key] != b[key] for key in scalar_keys):
        return False
    return all(
        bool(np.isclose(float(a[key]), float(b[key]), rtol=0.0, atol=float(atol)))
        for key in ("current_close", "current_bundle_edge")
    )
