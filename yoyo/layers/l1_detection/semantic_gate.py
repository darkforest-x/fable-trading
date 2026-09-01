"""Causal semantic gate for completed-history MA-launch YOLO proposals.

The gate consumes ``open/high/low/close/atr`` plus ``sma/ema 20/60/120``.
Core metrics use only ``core_start_i..core_end_i``; ATR is read at
``core_end_i + 2`` because every supported completed-history proposal already
requires at least two confirmation bars.  Directional confirmation metrics are
read only when their bar is at or before ``observed_end_i``.  No value after
``observed_end_i`` is inspected.

YOLO remains responsible for proposing a location and direction.  This module
only checks whether that proposal still satisfies the numeric morphology
contract from which the positive training examples were generated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .data import ALL_MA_COLS


class SemanticGateError(ValueError):
    """Raised when a proposal cannot be evaluated without contract drift."""


@dataclass(frozen=True)
class CausalCoreSemantics:
    """Direction-normalized features available at one observed endpoint."""

    ma_envelope_atr: float
    ma_spread_end_atr: float
    candle_envelope_atr: float
    max_body_atr: float
    core_progress_atr: float
    post1_progress_atr: float
    post2_progress_atr: float
    post3_progress_atr: float | None
    post5_progress_atr: float | None
    aligned_ma_slope_atr: float
    ma_slope_std_atr: float
    minimum_close_to_ma_atr: float
    max_close_to_ma_envelope_atr: float
    max_body_to_ma_envelope_atr: float

    def to_dict(self) -> dict[str, float | None]:
        """Return stable JSON-ready feature names used by the training contract."""

        return asdict(self)


@dataclass(frozen=True)
class SemanticGateResult:
    """One proposal's deterministic gate decision and predicate audit."""

    passed: bool
    checks: dict[str, bool]
    failed_checks: tuple[str, ...]


REQUIRED_GATE_KEYS = (
    "max_ma_envelope_atr",
    "max_ma_spread_end_atr",
    "max_core_body_atr",
    "min_core_progress_atr",
    "max_core_progress_atr",
    "min_post1_progress_atr",
    "min_post2_progress_atr",
    "min_post3_progress_atr",
    "min_post5_progress_atr",
    "min_aligned_ma_slope_atr",
    "max_minimum_close_to_ma_atr",
    "max_close_to_ma_envelope_atr",
    "max_body_to_ma_envelope_atr",
)


def _direction_sign(direction: str) -> float:
    normalized = str(direction).upper()
    if normalized == "LONG":
        return 1.0
    if normalized == "SHORT":
        return -1.0
    raise SemanticGateError(f"unsupported direction: {direction}")


def compute_causal_core_semantics(
    frame: pd.DataFrame,
    *,
    core_start_i: int,
    core_end_i: int,
    observed_end_i: int,
    direction: str,
) -> CausalCoreSemantics:
    """Compute the frozen morphology features without reading unseen bars.

    Required columns are ``open/high/low/close/atr`` and the six values in
    :data:`ALL_MA_COLS`.  Core features use the inclusive core interval.
    ``post1`` and ``post2`` are always required.  ``post3``/``post5`` are
    returned only when those bars are already visible at ``observed_end_i``.
    """

    start = int(core_start_i)
    end = int(core_end_i)
    observed = int(observed_end_i)
    core_bars = end - start + 1
    if core_bars not in {4, 5}:
        raise SemanticGateError(f"core must contain four or five bars, got {core_bars}")
    if start < 0 or end + 2 > observed:
        raise SemanticGateError("proposal must expose at least two confirmation bars")
    if observed >= len(frame):
        raise SemanticGateError("observed endpoint is outside the frame")

    required = ("open", "high", "low", "close", "atr", *ALL_MA_COLS)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise SemanticGateError(f"semantic frame is missing columns: {missing}")

    sign = _direction_sign(direction)
    atr = float(frame.iloc[end + 2]["atr"])
    if not np.isfinite(atr) or atr <= 0.0:
        raise SemanticGateError("ATR at core+2 is non-finite or non-positive")

    core = frame.iloc[start : end + 1]
    ma_core = core.loc[:, list(ALL_MA_COLS)].to_numpy(dtype=float)
    open_core = core["open"].to_numpy(dtype=float)
    high_core = core["high"].to_numpy(dtype=float)
    low_core = core["low"].to_numpy(dtype=float)
    close_core = core["close"].to_numpy(dtype=float)
    values = np.concatenate(
        (ma_core.ravel(), open_core, high_core, low_core, close_core, np.asarray([atr]))
    )
    if not np.isfinite(values).all():
        raise SemanticGateError("semantic core contains non-finite values")

    ma_low = ma_core.min(axis=1)
    ma_high = ma_core.max(axis=1)
    body_low = np.minimum(open_core, close_core)
    body_high = np.maximum(open_core, close_core)
    close_to_envelope = np.maximum(
        np.maximum(ma_low - close_core, close_core - ma_high), 0.0
    )
    body_to_envelope = np.maximum(
        np.maximum(ma_low - body_high, body_low - ma_high), 0.0
    )
    slopes = (ma_core[-1] - ma_core[0]) / atr
    close_end = float(frame.iloc[end]["close"])

    def progress(offset: int) -> float | None:
        index = end + int(offset)
        if index > observed:
            return None
        value = float(frame.iloc[index]["close"])
        if not np.isfinite(value):
            raise SemanticGateError(f"close at post{offset} is non-finite")
        return sign * (value - close_end) / atr

    post1 = progress(1)
    post2 = progress(2)
    if post1 is None or post2 is None:  # guarded above; retained as fail-closed proof
        raise SemanticGateError("post1/post2 must be visible")

    return CausalCoreSemantics(
        ma_envelope_atr=float((ma_core.max() - ma_core.min()) / atr),
        ma_spread_end_atr=float((ma_core[-1].max() - ma_core[-1].min()) / atr),
        candle_envelope_atr=float((high_core.max() - low_core.min()) / atr),
        max_body_atr=float(np.abs(close_core - open_core).max() / atr),
        core_progress_atr=float(sign * (close_core[-1] - close_core[0]) / atr),
        post1_progress_atr=float(post1),
        post2_progress_atr=float(post2),
        post3_progress_atr=progress(3),
        post5_progress_atr=progress(5),
        aligned_ma_slope_atr=float(sign * slopes.mean()),
        ma_slope_std_atr=float(slopes.std()),
        minimum_close_to_ma_atr=float(
            np.abs(close_core[:, None] - ma_core).min() / atr
        ),
        max_close_to_ma_envelope_atr=float(close_to_envelope.max() / atr),
        max_body_to_ma_envelope_atr=float(body_to_envelope.max() / atr),
    )


def evaluate_causal_semantic_gate(
    features: CausalCoreSemantics | Mapping[str, Any],
    gates: Mapping[str, Any],
) -> SemanticGateResult:
    """Apply the available prefix of the frozen completed-history gate.

    The threshold map is external so the training preregistration remains the
    authority.  ``post3`` and ``post5`` are checked only when their feature is
    present; this makes a post2 proposal causal rather than silently reading
    later candles from the source file.
    """

    missing = [key for key in REQUIRED_GATE_KEYS if key not in gates]
    if missing:
        raise SemanticGateError(f"morphology gate is missing thresholds: {missing}")
    values = features.to_dict() if isinstance(features, CausalCoreSemantics) else dict(features)

    checks: dict[str, bool] = {
        "ma_envelope": float(values["ma_envelope_atr"])
        <= float(gates["max_ma_envelope_atr"]),
        "ma_spread_end": float(values["ma_spread_end_atr"])
        <= float(gates["max_ma_spread_end_atr"]),
        "max_body": float(values["max_body_atr"])
        <= float(gates["max_core_body_atr"]),
        "core_progress": float(gates["min_core_progress_atr"])
        <= float(values["core_progress_atr"])
        <= float(gates["max_core_progress_atr"]),
        "post1": float(values["post1_progress_atr"])
        >= float(gates["min_post1_progress_atr"]),
        "post2": float(values["post2_progress_atr"])
        >= float(gates["min_post2_progress_atr"]),
        "ma_slope": float(values["aligned_ma_slope_atr"])
        >= float(gates["min_aligned_ma_slope_atr"]),
        "minimum_close_to_ma": float(values["minimum_close_to_ma_atr"])
        <= float(gates["max_minimum_close_to_ma_atr"]),
        "close_to_ma_envelope": float(values["max_close_to_ma_envelope_atr"])
        <= float(gates["max_close_to_ma_envelope_atr"]),
        "body_to_ma_envelope": float(values["max_body_to_ma_envelope_atr"])
        <= float(gates["max_body_to_ma_envelope_atr"]),
    }
    if values.get("post3_progress_atr") is not None:
        checks["post3"] = float(values["post3_progress_atr"]) >= float(
            gates["min_post3_progress_atr"]
        )
    if values.get("post5_progress_atr") is not None:
        checks["post5"] = float(values["post5_progress_atr"]) >= float(
            gates["min_post5_progress_atr"]
        )
    failures = tuple(name for name, passed in checks.items() if not passed)
    return SemanticGateResult(passed=not failures, checks=checks, failed_checks=failures)
