"""Research C: only a live, confirmed V3 parent earns suppression rights.

One causal change from the reference-gate experiment: an early alert starts a
provisional SIGNAL-CLOSE reference, but cannot suppress later parents until its
existing V3 child confirms at age0..3 while that reference remains alive. An
unconfirmed reference expires at age4. Confirmation never changes its entry,
initial risk or protection, and cannot revive a stopped reference. This is a
research occupancy reference, not an actual position or a performance claim.

Features use only current/past OHLCV, ready, ATR, SMA/EMA20, MD/SB/ZLEMA and the
unchanged V3 fields: prior12 range/density, recent12 density, three-bar price and
volume progress with prior20 volume baseline. Risk uses current ATR and the
last-five lows; the original 2ATR/structure stop, 2R activation and 4ATR ratchet
are reused unchanged. Future bars, outcome labels and final trade exit indexes
are forbidden here. Economic evaluation must independently use actual fills.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_early_warning import HOUR, early_fields
from yoyo.evaluation.spike_burst_replay import path_reference, risk_reference


def detect(frame, tick):
    """Rebuild parents, children and confirmation-earned suppression causally.

    Every bar first applies the protection known at the previous close, then
    expires an unconfirmed age4 parent, considers a new early edge, and finally
    considers its child. A confirmed-reference stop bar cannot accept another
    parent. Every condition edge is consumed even when blocked; only accepted
    parents update the unchanged twelve-bar cooldown. Child markers survive a
    provisional stop, but cannot restart risk or acquire suppression rights.

    ``reference_active`` includes provisional references; ``suppression_active``
    includes only live references that earned confirmation. Expiry/censoring is
    separate from a real reference stop: it has no synthetic exit fill. Gaps
    reset fields, parent/cooldown state and both kinds of reference. There is no
    period-end liquidation, so appending future bars leaves past rows unchanged.
    """
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("Positive finite exchange tick required")
    fields = early_fields(frame)
    lows = frame.low.rolling(5, min_periods=1).min().to_numpy(float)
    parent = last_accepted = owner = None
    parent_high = entry = stop = risk = protection = np.nan
    previous = child_sent = active = armed = qualified = False
    peak = 0.
    rows = []

    for i, (bar, f) in enumerate(zip(frame.itertuples(), fields.itertuples())):
        gap = bool(i and frame.index[i] - frame.index[i - 1] != HOUR)
        censored = bool(gap and active)
        suppression_censored = bool(gap and active and qualified)
        if gap:
            parent = last_accepted = owner = None
            parent_high = entry = stop = risk = protection = np.nan
            previous = child_sent = active = armed = qualified = False
            peak = 0.

        active_before = active
        suppression_before = bool(active and qualified)
        ended = False
        current_r = exit_price = np.nan
        operative = protection if active else np.nan
        if active and bool(bar.ready):
            path = path_reference(1, entry, risk, protection, peak, armed,
                bar.open, bar.high, bar.low, bar.close, bar.atr, tick=tick)
            active, protection, peak, current_r, exit_price, armed = path
            ended = not active
        suppression_ended = bool(suppression_before and ended)

        window_expired = bool(parent is not None and i - parent > 3)
        expired_unconfirmed = False
        if window_expired:
            # A live, unconfirmed reference loses its provisional lifetime.
            # This is cancellation at the close, never a stop or simulated fill.
            if owner == parent and not qualified:
                expired_unconfirmed = bool(active)
                active = False
            parent, parent_high, child_sent = None, np.nan, False

        condition = bool(f.early_condition)
        edge = bool(condition and not previous)
        cooldown = last_accepted is None or i - last_accepted >= 12
        blocking = bool((active and qualified) or suppression_ended)
        blocked = bool(edge and cooldown and blocking)
        early = bool(edge and cooldown and not blocking)
        started = False
        if early:
            last_accepted = parent = i
            parent_high, child_sent = float(f.prog_prior_high), False
            owner = i
            entry = stop = risk = protection = np.nan
            active = armed = qualified = False
            peak = 0.
            ref = risk_reference(1, bar.close, lows[i], bar.atr, tick=tick)
            if ref.valid:
                entry, stop, risk, protection = bar.close, ref.stop, ref.risk, ref.stop
                active, current_r, started = True, 0., True

        age = i - parent if parent is not None else None
        child = bool(parent is not None and not child_sent and bar.ready
            and bar.close > parent_high and f.tag_recent_density and f.tag_advance
            and f.tag_volume and f.tag_md_ge_signal and f.tag_middle_rising)
        acquired = False
        if child:
            child_sent = True
            if active and owner == parent:
                qualified, acquired = True, True

        suppressing = bool(active and qualified)
        rows.append(dict(early=early, confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=edge, cooldown_blocked=bool(edge and not cooldown),
            holding_blocked=blocked, suppression_blocked=blocked,
            suppression_active=suppressing, suppression_before=suppression_before,
            suppression_acquired=acquired, suppression_exit=suppression_ended,
            suppression_gap_censored=suppression_censored,
            reference_active=bool(active), reference_before=bool(active_before),
            reference_started=started, reference_exit=ended, reference_gap_censored=censored,
            confirmation_window_expired=window_expired,
            reference_expired_unconfirmed=expired_unconfirmed,
            reference_pending=bool(active and not qualified), reference_qualified=bool(qualified),
            reference_owner_i=float(owner) if owner is not None else np.nan,
            reference_entry=entry, reference_initial_stop=stop, reference_risk=risk,
            active_protection=operative, reference_protection=protection,
            reference_peak_r=peak, reference_current_r=current_r, reference_exit_price=exit_price,
            reference_armed=bool(armed),
            reference_active_at_confirmation=bool(child and active and owner == parent)))
        previous = condition
    return fields.join(pd.DataFrame(rows, index=frame.index))
