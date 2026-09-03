"""Causal deterministic breakout check applied only after an L1 model proposal.

The caller supplies a proposal endpoint ``t`` and a LONG/SHORT direction.
This module reads only ``close`` and the six trailing SMA/EMA 20/60/120 values
at ``t-1`` and ``t``.  A LONG passes when the current close is strictly above
the entire six-MA bundle while the previous close was not; SHORT is the exact
mirror below the bundle.  Rows after ``t`` are never inspected.

This is a proposal gate, not a candidate generator: callers must not scan this
rule first and then pretend that a later model detection existed earlier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .data import ALL_MA_COLS


class ModelFirstBreakoutError(ValueError):
    """Raised when a proposal cannot be checked causally and deterministically."""


@dataclass(frozen=True)
class ModelFirstBreakoutDecision:
    """Auditable two-bar first-close breakout decision."""

    direction: str
    proposal_end_i: int
    passed: bool
    current_close: float
    current_bundle_edge: float
    previous_close: float
    previous_bundle_edge: float
    current_beyond_bundle: bool
    previous_not_beyond_bundle: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON/CSV representation."""

        return asdict(self)


def _finite_row(frame: pd.DataFrame, index: int) -> tuple[float, np.ndarray]:
    required = ("close", *ALL_MA_COLS)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ModelFirstBreakoutError(f"missing columns: {missing}")
    close = float(frame.iloc[index]["close"])
    mas = frame.iloc[index].loc[list(ALL_MA_COLS)].to_numpy(dtype=float)
    values = np.concatenate(([close], mas))
    if not bool(np.isfinite(values).all()):
        raise ModelFirstBreakoutError(f"non-finite close/MA values at index {index}")
    return close, mas


def evaluate_model_first_breakout(
    frame: pd.DataFrame,
    *,
    proposal_end_i: int,
    direction: str,
) -> ModelFirstBreakoutDecision:
    """Check a model proposal for the first close beyond the full MA bundle.

    Required columns are ``close`` plus :data:`ALL_MA_COLS`.  Only rows
    ``proposal_end_i - 1`` and ``proposal_end_i`` are read.  Equality does not
    count as standing beyond the bundle, making the rule deterministic without
    an epsilon or tunable distance threshold.
    """

    end = int(proposal_end_i)
    if end < 1 or end >= len(frame):
        raise ModelFirstBreakoutError("proposal endpoint must have one prior row")
    side = str(direction).upper()
    if side not in {"LONG", "SHORT"}:
        raise ModelFirstBreakoutError(f"unsupported direction: {direction}")

    previous_close, previous_mas = _finite_row(frame, end - 1)
    current_close, current_mas = _finite_row(frame, end)
    if side == "LONG":
        previous_edge = float(previous_mas.max())
        current_edge = float(current_mas.max())
        current_beyond = current_close > current_edge
        previous_not_beyond = previous_close <= previous_edge
    else:
        previous_edge = float(previous_mas.min())
        current_edge = float(current_mas.min())
        current_beyond = current_close < current_edge
        previous_not_beyond = previous_close >= previous_edge

    return ModelFirstBreakoutDecision(
        direction=side,
        proposal_end_i=end,
        passed=bool(current_beyond and previous_not_beyond),
        current_close=current_close,
        current_bundle_edge=current_edge,
        previous_close=previous_close,
        previous_bundle_edge=previous_edge,
        current_beyond_bundle=bool(current_beyond),
        previous_not_beyond_bundle=bool(previous_not_beyond),
    )


def decisions_equal(
    left: ModelFirstBreakoutDecision | Mapping[str, Any],
    right: ModelFirstBreakoutDecision | Mapping[str, Any],
    *,
    atol: float = 1e-12,
) -> bool:
    """Compare two decisions while retaining strict booleans and float tolerance."""

    a = left.to_dict() if isinstance(left, ModelFirstBreakoutDecision) else dict(left)
    b = right.to_dict() if isinstance(right, ModelFirstBreakoutDecision) else dict(right)
    scalar_keys = (
        "direction",
        "proposal_end_i",
        "passed",
        "current_beyond_bundle",
        "previous_not_beyond_bundle",
    )
    if any(a[key] != b[key] for key in scalar_keys):
        return False
    float_keys = (
        "current_close",
        "current_bundle_edge",
        "previous_close",
        "previous_bundle_edge",
    )
    return all(
        bool(np.isclose(float(a[key]), float(b[key]), rtol=0.0, atol=float(atol)))
        for key in float_keys
    )

