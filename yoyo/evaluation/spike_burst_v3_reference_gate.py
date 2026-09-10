"""One causal intervention on V3: a live reference suppresses new parents.

Fields use current/past OHLC, ATR, last-five lows and unchanged V3 fields.
The occupancy reference is the existing Pine SIGNAL-CLOSE reference, not an
actual position: 2ATR/structure initial risk, 2R activation and 4ATR ratchet.
No execution outcomes or future exit indexes enter detection. Independent
economic evaluation still uses actual next-open fills. Children remain quality
upgrades at ages0..3, including after reference exit, and never restart risk.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_early_warning import early_fields, HOUR
from yoyo.evaluation.spike_burst_replay import risk_reference, path_reference


def detect(frame, tick):
    """Return V3-compatible alerts and causal reference/blocked diagnostics.

    First apply protection fixed at the previous close. A stop bar cannot
    accept another parent. Every raw-condition edge is consumed even when
    blocked; only accepted parents update cooldown. Missing-hour boundaries
    censor/reset occupancy and alert state. No period-end liquidation exists
    here: appending future bars cannot alter prior detection.
    """
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("Positive finite exchange tick required")
    fields = early_fields(frame)
    lows = frame.low.rolling(5, min_periods=1).min().to_numpy(float)
    parent = last_accepted = owner = None
    parent_high = entry = stop = risk = protection = np.nan
    previous = child_sent = active = armed = False
    peak = 0.
    rows = []
    for i, (bar, f) in enumerate(zip(frame.itertuples(), fields.itertuples())):
        gap = bool(i and frame.index[i] - frame.index[i-1] != HOUR)
        censored = gap and active
        if gap:
            parent = last_accepted = owner = None
            parent_high = entry = stop = risk = protection = np.nan
            previous = child_sent = active = armed = False
            peak = 0.
        active_before = active
        ended = False
        current_r = exit_price = np.nan
        operative = protection if active else np.nan
        if active and bool(bar.ready):
            path = path_reference(1, entry, risk, protection, peak, armed,
                bar.open, bar.high, bar.low, bar.close, bar.atr, tick=tick)
            active, protection, peak, current_r, exit_price, armed = path
            ended = not active
        condition = bool(f.early_condition)
        edge = condition and not previous
        cooldown = last_accepted is None or i-last_accepted >= 12
        blocked = bool(edge and cooldown and (active or ended))
        early = bool(edge and cooldown and not active and not ended)
        started = False
        if early:
            last_accepted = parent = i
            parent_high, child_sent = float(f.prog_prior_high), False
            ref = risk_reference(1, bar.close, lows[i], bar.atr, tick=tick)
            if ref.valid:
                entry, stop, risk, protection = bar.close, ref.stop, ref.risk, ref.stop
                active, armed, peak, current_r, owner, started = True, False, 0., 0., i, True
        if parent is not None and i-parent > 3:
            parent, parent_high, child_sent = None, np.nan, False
        age = i-parent if parent is not None else None
        child = bool(parent is not None and not child_sent and bar.ready
            and bar.close > parent_high and f.tag_recent_density and f.tag_advance
            and f.tag_volume and f.tag_md_ge_signal and f.tag_middle_rising)
        if child:
            child_sent = True
        rows.append(dict(early=early, confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=bool(edge), cooldown_blocked=bool(edge and not cooldown),
            holding_blocked=blocked, reference_active=bool(active), reference_before=bool(active_before),
            reference_started=started, reference_exit=ended, reference_gap_censored=censored,
            reference_owner_i=float(owner) if owner is not None else np.nan,
            reference_entry=entry, reference_initial_stop=stop, reference_risk=risk,
            active_protection=operative, reference_protection=protection,
            reference_peak_r=peak, reference_current_r=current_r, reference_exit_price=exit_price,
            reference_armed=bool(armed), reference_active_at_confirmation=bool(child and active)))
        previous = condition
    return fields.join(pd.DataFrame(rows, index=frame.index))
