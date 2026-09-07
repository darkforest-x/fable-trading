"""Two bounded research hypotheses authorized by owner on 2026-09-07.

BE switch changes only confirmed-bar stop adjustment. Initial stop is unchanged.
Slope gate uses SMA(hl2,60)[t] minus SMA(hl2,60)[t-1], requiring only hl2[t-60:t].
It gates flat entries only; raw-signal cooldown counting, opposite exits and
same-side protective stop tightening preserve the R1 contract.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .eth4h_trend_candidate import CandidatePolicy, CandidateReplay


@dataclass(frozen=True)
class ExplorationPolicy(CandidatePolicy):
    name: str = 'R1_BE_on'
    risk_pct: float = .5
    ratchet_only: bool = True
    isolate_stops: bool = True
    fresh_cooldown: bool = True
    close_only: bool = True
    breakeven: bool = True
    slope_gate: bool = False


BASE = ExplorationPolicy()
BE_OFF = ExplorationPolicy(name='BE_off', breakeven=False)


class ExplorationReplay(CandidateReplay):
    def __init__(self, frame, policy):
        super().__init__(frame, policy)
        self.slope = frame.slow_ma.diff().to_numpy(float)

    def managed_stop(self, i, position, current):
        return super().managed_stop(i, position, current) if self.policy.breakeven else current

    def entry_signal_allowed(self, i, direction, position):
        if not self.policy.slope_gate or position is not None:
            return True
        return bool(np.isfinite(self.slope[i]) and direction * self.slope[i] > 0)
