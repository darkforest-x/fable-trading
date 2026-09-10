"""Closed-bar oracle for the SPIKE Burst V6 volume-price structure gate.

V6 preserves V5's supplied legacy provenance, body support, frozen parent
range, and MD completion. Its one additional causal requirement is a latched
V1 directional-candle shape paired with the existing supplied rolling-three
advance and volume envelope. It never restores V1's same-bar RV or TR gates,
looks beyond the observed prefix, calculates PnL, or mutates its input.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


REQUIRED = (
    "open", "high", "low", "close", "md", "sb", "atr", "ropeHigh",
    "legacy_confirmed", "legacy_parent_high", "legacy_parent_low", "ready",
    "data_gap", "confirmed", "advance3", "volume_ratio3",
)
MIN_ADVANCE = 1.5
MIN_VOLUME = 1.5
MIN_BODY = 0.55
MIN_END = 0.75


def _finite(value: object) -> bool:
    return value is not None and not pd.isna(value) and math.isfinite(float(value))


def _true(value: object) -> bool:
    return isinstance(value, (bool, np.bool_)) and bool(value)


def _false(value: object) -> bool:
    return isinstance(value, (bool, np.bool_)) and not bool(value)


def _ohlc(row: pd.Series) -> tuple[float, float, float, float] | None:
    values = [row[name] for name in ("open", "high", "low", "close")]
    if not all(_finite(value) for value in values):
        return None
    o, h, l, c = map(float, values)
    return (o, h, l, c) if l > 0 and h >= max(o, c, l) and l <= min(o, c, h) else None


def _shape(row: pd.Series, side: int) -> bool:
    values = _ohlc(row)
    if values is None:
        return False
    o, h, l, c = values
    span = h - l
    if span <= 0:
        return False
    direction = c > o if side == 1 else c < o
    end = (c - l) / span if side == 1 else (h - c) / span
    return direction and abs(c - o) / span >= MIN_BODY and end >= MIN_END


def _envelope(row: pd.Series, side: int) -> bool:
    if not all(_finite(row[name]) for name in ("advance3", "volume_ratio3")):
        return False
    return side * float(row.advance3) >= MIN_ADVANCE and float(row.volume_ratio3) >= MIN_VOLUME


@dataclass
class _State:
    body_support_i: int | None = None
    legacy_i: int | None = None
    parent_high: float | None = None
    parent_low: float | None = None
    pending: bool = False
    evidence: bool = False
    evidence_i: int | None = None
    prior_md: float | None = None
    segment_start_i: int = 0

    def clear(self) -> None:
        self.body_support_i = self.legacy_i = self.evidence_i = None
        self.parent_high = self.parent_low = None
        self.pending = self.evidence = False


def detect(frame: pd.DataFrame, side: int = 1) -> pd.DataFrame:
    """Replay V6 from supplied closed inputs.

    At a supplied V4 confirmation, any V1 directional candle among that closed
    bar and its two predecessors seeds evidence only when that confirmation's
    supplied three-bar envelope passes. While provenance remains pending, a
    later candle must itself meet the same envelope to latch evidence. A gap,
    unknown field, new provenance, parent break, or consumed confirmation clears
    evidence. ``side=-1`` is the exact price/momentum reflection.
    """
    if isinstance(side, bool) or side not in (1, -1):
        raise ValueError("side must be +1 or -1")
    rope = "ropeHigh" if side == 1 else "ropeLow"
    required = [rope if name == "ropeHigh" else name for name in REQUIRED]
    missing = [name for name in required if name not in frame]
    if missing:
        raise ValueError("Missing V6 structural columns: " + ", ".join(missing))
    confirmed = [_true(value) for value in frame.confirmed]
    if any(not value for value in confirmed[:-1]):
        raise ValueError("Unconfirmed rows are permitted only at the final tip")

    state = _State()
    rows: list[dict[str, object]] = []
    for i, (_, row) in enumerate(frame.iterrows()):
        event = False
        consumed_evidence = False
        consumed_evidence_i: int | None = None
        why = "unconfirmed"
        if _true(row.confirmed):
            valid = (_false(row.data_gap) and _true(row.ready) and _ohlc(row) is not None
                     and all(_finite(row[name]) for name in ("md", "sb", "atr", rope))
                     and float(row.atr) > 0)
            if not valid:
                state.clear()
                state.segment_start_i = i + 1
                why = "gap" if _true(row.data_gap) else "unknown"
                state.prior_md = float(row.md) if _finite(row.md) else None
            else:
                o, _, _, c = _ohlc(row)  # type: ignore[misc]
                md, sb, rope_value = float(row.md), float(row.sb), float(row[rope])
                full_body = min(o, c) > rope_value if side == 1 else max(o, c) < rope_value
                if side * (c - rope_value) <= 0:
                    state.body_support_i = None
                elif full_body:
                    state.body_support_i = i

                parent_valid = (_true(row.legacy_confirmed)
                                and _finite(row.legacy_parent_high)
                                and _finite(row.legacy_parent_low)
                                and float(row.legacy_parent_low) <= float(row.legacy_parent_high))
                if parent_valid:
                    state.legacy_i = i
                    state.parent_high = float(row.legacy_parent_high)
                    state.parent_low = float(row.legacy_parent_low)
                    state.pending = True
                    # Even malformed supplied ready=True after a gap cannot
                    # make this oracle scan across the cleared segment boundary.
                    window_start = max(state.segment_start_i, i - 2)
                    shape_i = next((j for j in range(i, window_start - 1, -1)
                                    if _shape(frame.iloc[j], side)), None)
                    state.evidence = _envelope(row, side) and shape_i is not None
                    state.evidence_i = shape_i if state.evidence else None
                elif state.pending and _shape(row, side) and _envelope(row, side):
                    state.evidence = True
                    state.evidence_i = i

                invalidated = state.pending and (
                    c < float(state.parent_low) if side == 1 else c > float(state.parent_high)
                )
                if invalidated:
                    state.legacy_i = state.evidence_i = None
                    state.parent_high = state.parent_low = None
                    state.pending = state.evidence = False
                    why = "parent_broken"
                elif (state.pending and state.evidence and state.body_support_i is not None
                      and state.parent_high is not None and state.parent_low is not None
                      and state.prior_md is not None and side * (c - rope_value) > 0
                      and (c > state.parent_high if side == 1 else c < state.parent_low)
                      and side * (md - sb) > 0 and side * (md - state.prior_md) > 0):
                    event = True
                    consumed_evidence = state.evidence
                    consumed_evidence_i = state.evidence_i
                    state.pending = state.evidence = False
                    state.evidence_i = None
                    why = "confirmed"
                else:
                    why = ("await_legacy" if not state.pending else "await_launch_evidence"
                           if not state.evidence else "await_body" if state.body_support_i is None
                           else "await_md_turn" if state.prior_md is None else "await_momentum")
                state.prior_md = md
        rows.append(dict(body_support_i=state.body_support_i, legacy_i=state.legacy_i,
                         legacy_parent_high=state.parent_high, legacy_parent_low=state.parent_low,
                         evidence_i=state.evidence_i, evidence=state.evidence,
                         consumed_evidence_i=consumed_evidence_i,
                         consumed_evidence=consumed_evidence,
                         pending=state.pending, confirmed=event, why_pending=why))
    output = pd.DataFrame(rows, index=frame.index)
    for name in ("body_support_i", "legacy_i", "legacy_parent_high", "legacy_parent_low", "evidence_i", "consumed_evidence_i"):
        output[name] = pd.to_numeric(output[name], errors="coerce")
    for name in ("evidence", "consumed_evidence", "pending", "confirmed"):
        output[name] = output[name].astype(bool)
    return output
