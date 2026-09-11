"""Synthetic closed-bar reference ownership checks for the Pine r2 revision.

This is a deliberately small lifecycle oracle. It validates the required
ownership order, not Pine compilation, feature parity, fills, or PnL.
"""
from dataclasses import dataclass
from typing import List, Optional

import pytest


@dataclass
class Reference:
    side: int
    entry: float
    peak: float = 0.0
    ended_by: Optional[str] = None


class ClosedBarLifecycle:
    """Minimal model of post-``f_path`` reference ownership on closed bars."""

    def __init__(self, side: int) -> None:
        self.active: Optional[Reference] = Reference(side, 100.0)
        self.finished: List[Reference] = []

    def step(
        self,
        *,
        confirmed: bool,
        signal_side: int = 0,
        risk_valid: bool = True,
        stopped: bool = False,
        peak: Optional[float] = None,
    ) -> None:
        if not confirmed:
            return
        stopped_side = self.active.side if stopped and self.active else 0
        if self.active and stopped:
            self.active.ended_by = "protection"
            self.finished.append(self.active)
            self.active = None
        # A stop owns its fill/reason. Only a separately opposite confirmed
        # signal may begin the next reference at this same close.
        if signal_side and signal_side != stopped_side:
            if self.active and signal_side != self.active.side:
                self.active.ended_by = "reverse"
                self.finished.append(self.active)
                self.active = None
            if self.active is None and risk_valid:
                self.active = Reference(signal_side, 101.0)
        if self.active and peak is not None:
            self.active.peak = max(self.active.peak, peak)


@pytest.mark.parametrize("initial, opposite", [(1, -1), (-1, 1)])
def test_confirmed_opposite_closes_old_and_creates_independent_new_group(initial, opposite):
    lifecycle = ClosedBarLifecycle(initial)
    lifecycle.step(confirmed=True, peak=5.0)
    lifecycle.step(confirmed=True, signal_side=opposite, risk_valid=True)
    assert [(item.side, item.peak, item.ended_by) for item in lifecycle.finished] == [(initial, 5.0, "reverse")]
    assert lifecycle.active == Reference(opposite, 101.0)
    lifecycle.step(confirmed=True, peak=3.0)
    assert lifecycle.finished[0].peak == 5.0
    assert lifecycle.active.peak == 3.0


def test_same_direction_does_not_reset_active_reference():
    lifecycle = ClosedBarLifecycle(1)
    original = lifecycle.active
    lifecycle.step(confirmed=True, signal_side=1, risk_valid=True, peak=2.0)
    assert lifecycle.active is original
    assert lifecycle.active.entry == 100.0 and lifecycle.active.peak == 2.0
    assert not lifecycle.finished


def test_unconfirmed_signal_cannot_exit_or_start_reference():
    lifecycle = ClosedBarLifecycle(-1)
    lifecycle.step(confirmed=False, signal_side=1, risk_valid=True, stopped=True)
    assert lifecycle.active == Reference(-1, 100.0)
    assert not lifecycle.finished


def test_protection_reason_precedes_same_close_opposite_reference():
    lifecycle = ClosedBarLifecycle(-1)
    lifecycle.step(confirmed=True, signal_side=1, risk_valid=True, stopped=True)
    assert lifecycle.finished == [Reference(-1, 100.0, ended_by="protection")]
    assert lifecycle.active == Reference(1, 101.0)


def test_invalid_new_risk_still_closes_confirmed_opposite_old_reference():
    lifecycle = ClosedBarLifecycle(1)
    lifecycle.step(confirmed=True, signal_side=-1, risk_valid=False)
    assert lifecycle.finished == [Reference(1, 100.0, ended_by="reverse")]
    assert lifecycle.active is None
