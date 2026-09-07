"""Bounded-risk ETH 4h candidate, with a preregistered one-change-at-a-time chain.

The parent replay's causal signal inputs are unchanged: OHLC through t,
SMA10/40/60 on hl2, EMA100 on close, ATR14, percentile200, lag10 and HMA10.
Sizing uses only current equity, signal close and min(4*ATR,3%*close).
Existing 1.5%/0.1% BE thresholds and nominal 20bp costs remain frozen.
No training, optimization, holdout, live execution, or profitability guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pine_allin_eth4h import Policy, Replay


@dataclass(frozen=True)
class CandidatePolicy(Policy):
    name: str = "S0_immediate_1x"
    fee: float = .001
    immediate_stop: bool = True
    unit_leverage: bool = True
    risk_pct: float | None = None
    ratchet_only: bool = False
    isolate_stops: bool = False
    fresh_cooldown: bool = False
    close_only: bool = False


CHAIN = (
    CandidatePolicy(),
    CandidatePolicy("S1_risk_half_pct", risk_pct=.5),
    CandidatePolicy("S2_stop_ratchet", risk_pct=.5, ratchet_only=True),
    CandidatePolicy("S3_isolated_stops", risk_pct=.5, ratchet_only=True, isolate_stops=True),
    CandidatePolicy("S4_fresh_cooldown", risk_pct=.5, ratchet_only=True, isolate_stops=True, fresh_cooldown=True),
    CandidatePolicy("S5_close_only", risk_pct=.5, ratchet_only=True, isolate_stops=True, fresh_cooldown=True, close_only=True),
)


class CandidateReplay(Replay):
    """Override only the explicitly frozen behavioral dimension at each stage."""

    def target_leverage(self, i: int, original: float) -> float:
        if self.policy.risk_pct is None:
            return original
        stop_fraction = min(self.atr[i] * 4 / self.c[i], .03)
        return min(1., self.policy.risk_pct / 100 / stop_fraction)

    def cooling_state(self, skip: int, original: bool) -> bool:
        return skip > 0 if self.policy.fresh_cooldown else original

    def reset_stop(self, current: float, proposed: float, position, direction: int) -> float:
        if position is None:
            return proposed
        if position["direction"] != direction:
            return current if self.policy.isolate_stops else proposed
        if self.policy.ratchet_only:
            return max(current, proposed) if direction > 0 else min(current, proposed)
        return proposed

    def entry_stop_state(self, current: float, initial: float) -> float:
        return initial if self.policy.isolate_stops else current

    def pending_quantity(self, quantity: float, position, direction: int) -> float:
        if self.policy.close_only and position is not None and position["direction"] != direction:
            return 0.
        return quantity
