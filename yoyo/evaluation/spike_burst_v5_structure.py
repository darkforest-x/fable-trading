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


MIN_QUIET = 12
NEAR_ATR = 0.10
RELEASE_BARS = 6
REQUIRED = (
    "open", "high", "low", "close", "md", "sb", "atr", "ropeHigh",
    "legacy_confirmed", "legacy_parent_high", "ready", "data_gap", "confirmed",
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
    structure_id: int = 0
    quiet_count: int = 0
    quiet_high: float | None = None
    quiet_low: float | None = None
    frozen_band: float | None = None
    release_i: int | None = None
    body_support_i: int | None = None
    legacy_i: int | None = None
    legacy_parent_high: float | None = None
    pending: bool = False
    consumed: bool = False
    prior_atr: float | None = None
    prior_md: float | None = None

    def clear_episode(self) -> None:
        self.quiet_count = 0
        self.quiet_high = self.quiet_low = self.frozen_band = None
        self.release_i = self.body_support_i = self.legacy_i = None
        self.legacy_parent_high = None
        self.pending = self.consumed = False


def detect(frame: pd.DataFrame) -> pd.DataFrame:
    """Replay V5 from supplied closed-bar inputs without mutating ``frame``.

    A mature quiet episode freezes ``0.10 * prior ATR`` at its twelfth quiet
    close. Its full range ends before the release bar. A full candle body over
    ``ropeHigh`` and an in-episode V4 confirmation may arrive in either order;
    neither is a same-candle requirement. Gaps, unknown values and a new quiet
    run reset all provenance rather than synthesising a continuation. The frame
    may end with one unconfirmed tip; an unconfirmed or unknown interior row is
    rejected because this oracle does not model TradingView intrabar revisions.
    """
    missing = [name for name in REQUIRED if name not in frame]
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
                and all(_finite(row[name]) for name in ("md", "sb", "atr", "ropeHigh"))
                and state.prior_atr is not None
                and state.prior_atr > 0
                and float(row.atr) > 0
            )
            if not valid:
                state.clear_episode()
                why = "gap" if _true(row.data_gap) else "unknown"
                state.prior_atr = float(row.atr) if _finite(row.atr) and float(row.atr) > 0 else None
                state.prior_md = float(row.md) if _finite(row.md) else None
            else:
                o, h, l, c = (float(row[name]) for name in ("open", "high", "low", "close"))
                md, sb, rope = float(row.md), float(row.sb), float(row.ropeHigh)
                developing_band = NEAR_ATR * state.prior_atr
                active_band = state.frozen_band if (state.quiet_count >= MIN_QUIET or state.pending) else developing_band
                near = max(abs(md), abs(sb)) <= active_band
                full_body = min(o, c) > rope
                if c <= rope:
                    state.body_support_i = None
                elif full_body:
                    state.body_support_i = i

                if near:
                    if state.quiet_count == 0:
                        state.structure_id += 1
                        state.quiet_high, state.quiet_low = h, l
                        state.frozen_band = None
                        state.release_i = state.body_support_i = state.legacy_i = None
                        state.legacy_parent_high = None
                        state.pending = state.consumed = False
                        state.body_support_i = i if full_body else None
                    else:
                        state.quiet_high = max(float(state.quiet_high), h)
                        state.quiet_low = min(float(state.quiet_low), l)
                    state.quiet_count += 1
                    if state.quiet_count == MIN_QUIET:
                        state.frozen_band = developing_band
                    if state.quiet_count >= MIN_QUIET and _true(row.legacy_confirmed) and _finite(row.legacy_parent_high) and state.legacy_i is None:
                        state.legacy_i, state.legacy_parent_high = i, float(row.legacy_parent_high)
                    why = "building" if state.quiet_count < MIN_QUIET else "quiet"
                else:
                    if state.quiet_count > 0:
                        if state.quiet_count >= MIN_QUIET and md > 0:
                            state.pending, state.release_i = True, i
                            why = "release"
                        else:
                            state.clear_episode()
                            why = "down_release" if md < 0 else "short_quiet"
                        state.quiet_count = 0
                    release_age = None if state.release_i is None else i - state.release_i
                    if state.pending and _true(row.legacy_confirmed) and _finite(row.legacy_parent_high) and state.legacy_i is None:
                        state.legacy_i, state.legacy_parent_high = i, float(row.legacy_parent_high)
                    if state.pending:
                        if release_age is None or release_age >= RELEASE_BARS:
                            state.clear_episode(); why = "expired"
                        elif c < float(state.quiet_low):
                            state.clear_episode(); why = "below_quiet_low"
                        elif md < 0:
                            state.clear_episode(); why = "negative_md"
                        elif (
                            not state.consumed and state.legacy_i is not None
                            and state.body_support_i is not None and state.legacy_parent_high is not None
                            and state.prior_md is not None and md > float(state.frozen_band)
                            and md > sb and md > state.prior_md and c > rope
                            and c > float(state.quiet_high) and c > state.legacy_parent_high
                        ):
                            event = True
                            state.consumed, state.pending = True, False
                            why = "confirmed"
                        else:
                            why = "await_legacy" if state.legacy_i is None else "await_body" if state.body_support_i is None else "await_release"
                state.prior_atr, state.prior_md = float(row.atr), md

        release_age = None if state.release_i is None else i - state.release_i
        rows.append({
            "structure_id": state.structure_id,
            "quiet_count": state.quiet_count,
            "release_age": release_age,
            "body_support_i": state.body_support_i,
            "legacy_i": state.legacy_i,
            "legacy_parent_high": state.legacy_parent_high,
            "pending": state.pending,
            "confirmed": event,
            "why_pending": why,
        })
    result = pd.DataFrame(rows, index=frame.index)
    for name in ("structure_id", "quiet_count", "release_age", "body_support_i", "legacy_i", "legacy_parent_high"):
        result[name] = pd.to_numeric(result[name], errors="coerce")
    result["pending"] = result["pending"].astype(bool)
    result["confirmed"] = result["confirmed"].astype(bool)
    return result
