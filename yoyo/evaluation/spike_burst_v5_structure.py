"""Literal closed-bar oracle for the SPIKE Burst V5 structural gate.

The gate consumes only supplied OHLC, ``md``, ``sb``, ATR, six-MA rope and
legacy V4 confirmation provenance at each observed bar.  It deliberately does
not calculate features, inspect future bars, score prices, or model risk/PnL.
``detect`` is a structural-gate state oracle after supplied inputs, not a
backtester or evidence of full-script Pine feature/legacy-engine parity.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


REQUIRED = (
    "open", "high", "low", "close", "md", "sb", "atr", "ropeHigh",
    "legacy_confirmed", "legacy_parent_high", "legacy_parent_low", "ready", "data_gap", "confirmed",
)


def _finite(value: object) -> bool:
    return value is not None and not pd.isna(value) and math.isfinite(float(value))


def _true(value: object) -> bool:
    """Accept only an explicit Boolean true; NaN is never truthy provenance."""
    return isinstance(value, (bool, np.bool_)) and bool(value)


def _false(value: object) -> bool:
    """Accept only an explicit Boolean false; unknown gap state fails closed."""
    return isinstance(value, (bool, np.bool_)) and not bool(value)


def _valid_ohlc(row: pd.Series) -> bool:
    values = [row[name] for name in ("open", "high", "low", "close")]
    if not all(_finite(value) for value in values):
        return False
    o, h, l, c = map(float, values)
    return l > 0 and h >= max(o, c, l) and l <= min(o, c, h)


@dataclass
class _State:
    body_support_i: int | None = None
    legacy_i: int | None = None
    legacy_parent_high: float | None = None
    legacy_parent_low: float | None = None
    pending: bool = False
    prior_md: float | None = None

    def clear(self) -> None:
        self.body_support_i = self.legacy_i = None
        self.legacy_parent_high = None
        self.legacy_parent_low = None
        self.pending = False


def detect(frame: pd.DataFrame, side: int = 1) -> pd.DataFrame:
    """Replay V5 from supplied closed-bar inputs without mutating ``frame``.

    A full candle body over ``ropeHigh`` and a supplied V4 confirmation may
    arrive in either order; neither is a same-candle requirement. The body
    persists only during a continuous close-above-rope run. A V4 confirmation
    freezes its supplied parent range, supersedes an older pending provenance,
    and is consumed after one final event. A close strictly below the frozen
    parent low cancels it; an in-range retest may wait for later support. Gaps
    and unknown values clear all provenance rather than synthesising a
    continuation. The frame may end with one unconfirmed tip; an unconfirmed
    or unknown interior row is rejected because this oracle does not model
    TradingView intrabar revisions. ``side=-1`` is the exact directional
    mirror: body below ropeLow, upper wick permitted, parent-high failure,
    close below parent low and falling MD below SB. No prices are transformed,
    and the existing long path remains the default.
    """
    if isinstance(side, bool) or side not in (1, -1):
        raise ValueError("side must be +1 or -1")
    rope_column = "ropeHigh" if side == 1 else "ropeLow"
    required = [rope_column if name == "ropeHigh" else name for name in REQUIRED]
    missing = [name for name in required if name not in frame]
    if missing:
        raise ValueError("Missing V5 structural columns: " + ", ".join(missing))
    confirmed = [_true(value) for value in frame["confirmed"]]
    if any(not value for value in confirmed[:-1]):
        raise ValueError("Unconfirmed rows are permitted only at the final tip")

    state = _State()
    rows: list[dict[str, object]] = []
    for i, (_, row) in enumerate(frame.iterrows()):
        event = False
        why = "unconfirmed"
        if _true(row.confirmed):
            valid = (
                _false(row.data_gap)
                and _true(row.ready)
                and _valid_ohlc(row)
                and all(_finite(row[name]) for name in ("md", "sb", "atr", rope_column))
                and float(row.atr) > 0
            )
            if not valid:
                state.clear()
                why = "gap" if _true(row.data_gap) else "unknown"
                state.prior_md = float(row.md) if _finite(row.md) else None
            else:
                o, _, _, c = (float(row[name]) for name in ("open", "high", "low", "close"))
                md, sb, rope = float(row.md), float(row.sb), float(row[rope_column])
                full_body = min(o, c) > rope if side == 1 else max(o, c) < rope
                if side * (c - rope) <= 0:
                    state.body_support_i = None
                elif full_body:
                    state.body_support_i = i

                parent_range_valid = (
                    _true(row.legacy_confirmed)
                    and _finite(row.legacy_parent_high)
                    and _finite(row.legacy_parent_low)
                    and float(row.legacy_parent_low) <= float(row.legacy_parent_high)
                )
                if parent_range_valid:
                    state.legacy_i = i
                    state.legacy_parent_high = float(row.legacy_parent_high)
                    state.legacy_parent_low = float(row.legacy_parent_low)
                    state.pending = True
                invalidated = state.pending and (
                    c < float(state.legacy_parent_low) if side == 1
                    else c > float(state.legacy_parent_high)
                )
                if invalidated:
                    state.legacy_i = None
                    state.legacy_parent_high = None
                    state.legacy_parent_low = None
                    state.pending = False
                    why = "parent_broken"
                elif (
                    state.pending and state.body_support_i is not None
                    and state.legacy_parent_high is not None and state.legacy_parent_low is not None
                    and state.prior_md is not None
                    and side * (c - rope) > 0
                    and (c > state.legacy_parent_high if side == 1 else c < state.legacy_parent_low)
                    and side * (md - sb) > 0 and side * (md - state.prior_md) > 0
                ):
                    event = True
                    state.pending = False
                    why = "confirmed"
                else:
                    why = (
                        "await_legacy" if not state.pending else
                        "await_body" if state.body_support_i is None else
                        "await_md_turn" if state.prior_md is None else
                        "await_momentum"
                    )
                state.prior_md = md

        rows.append({
            "body_support_i": state.body_support_i,
            "legacy_i": state.legacy_i,
            "legacy_parent_high": state.legacy_parent_high,
            "legacy_parent_low": state.legacy_parent_low,
            "pending": state.pending,
            "confirmed": event,
            "why_pending": why,
        })
    result = pd.DataFrame(rows, index=frame.index)
    for name in ("body_support_i", "legacy_i", "legacy_parent_high", "legacy_parent_low"):
        result[name] = pd.to_numeric(result[name], errors="coerce")
    result["pending"] = result["pending"].astype(bool)
    result["confirmed"] = result["confirmed"].astype(bool)
    return result
