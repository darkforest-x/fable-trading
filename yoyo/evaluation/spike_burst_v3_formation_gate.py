"""Research D: prior six-MA width formation gates an original V3 parent edge.

This single intervention uses original SMA/EMA20/60/120 absolute price widths:
W=max(six MAs)-min(six MAs). At bar i, formed means median(W[i-6:i]) <=
median(W[i-12:i-6]). All twelve PRIOR widths must be finite and hourly-contiguous
through the current bar. Equal/flat widths pass. Neither current width nor ATR
normalization enters this gate; a current invalid width affects the subsequent
twelve historical windows, not the current decision's historical-only gate.

Other fields are unchanged V3: current/past OHLCV, ready, prior12 highs,
SMA/EMA20, recent prior12 density, three-bar advance/volume with prior20 volume
baseline, and MD/SB/ZLEMA. Accepted parents alone own the unchanged twelve-bar
cooldown and age0..3 confirmation window. A rejected raw edge is consumed, never
queued or used to extend cooldown. Children do not reapply formation. No risk
occupancy, future labels, forward outcomes or final trade exits are used here.
This is a fixed research hypothesis, not optimized or validated profitability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_early_warning import HOUR, early_fields

MA_COLUMNS = ("s20", "e20", "s60", "e60", "s120", "e120")


def _formation_fields(frame):
    """Return prior-only width diagnostics after ``early_fields`` validates time.

    No incomplete six-MA vector is reduced with skipna. Missing/infinite widths
    invalidate every subsequent twelve-bar window containing them. A time gap
    starts a new segment, which needs twelve complete prior widths to be valid.
    """
    missing = set(MA_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("Formation requires original six MA columns: " + ", ".join(sorted(missing)))
    mas = frame.loc[:, MA_COLUMNS].to_numpy(dtype=float)
    finite = np.isfinite(mas).all(axis=1)
    widths = np.full(len(frame), np.nan)
    widths[finite] = mas[finite].max(axis=1) - mas[finite].min(axis=1)
    width = pd.Series(widths, index=frame.index).where(np.isfinite(widths))
    segments = np.r_[0, np.cumsum(np.diff(frame.index.asi8) != HOUR.value)]
    pieces = []
    for _, part in width.groupby(segments, sort=False):
        recent = part.shift(1).rolling(6, min_periods=6).median()
        older = part.shift(7).rolling(6, min_periods=6).median()
        valid = np.isfinite(recent) & np.isfinite(older)
        pieces.append(pd.DataFrame(dict(formation_width=part,
            formation_old_median=older, formation_recent_median=recent,
            formation_valid=valid, formed=valid & recent.le(older))))
    return pd.concat(pieces).reindex(frame.index)


def detect(frame):
    """Rebuild original V3 parents/children with only prior formation added.

    ``candidate_edge`` is the original complete V3 condition's rising edge,
    independent of formation. ``cooldown_blocked`` identifies that clock alone;
    ``formation_blocked`` means the edge passed cooldown but failed formation.
    Every raw edge is consumed in either case. No reference holding is created.
    Returned parent identity/frozen boundary/child age use actual current bars,
    and expired or rejected parents cannot be revived by later confirmation.
    """
    fields = early_fields(frame).join(_formation_fields(frame))
    last_accepted = parent = None
    parent_high = np.nan
    previous_condition = child_sent = False
    rows = []
    for i, row in enumerate(fields.itertuples()):
        if i and frame.index[i] - frame.index[i - 1] != HOUR:
            last_accepted = parent = None
            parent_high = np.nan
            previous_condition = child_sent = False
        condition = bool(row.early_condition)
        edge = bool(condition and not previous_condition)
        cooldown = last_accepted is None or i - last_accepted >= 12
        early = bool(edge and cooldown and row.formed)
        if early:
            last_accepted = parent = i
            parent_high, child_sent = float(row.prog_prior_high), False
        if parent is not None and i - parent > 3:
            parent, parent_high, child_sent = None, np.nan, False
        age = i - parent if parent is not None else None
        child = bool(parent is not None and not child_sent and frame.ready.iloc[i]
            and frame.close.iloc[i] > parent_high and row.tag_recent_density
            and row.tag_advance and row.tag_volume and row.tag_md_ge_signal and row.tag_middle_rising)
        if child:
            child_sent = True
        rows.append(dict(early=early, confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=edge, cooldown_blocked=bool(edge and not cooldown),
            formation_blocked=bool(edge and cooldown and not row.formed)))
        previous_condition = condition
    return fields.join(pd.DataFrame(rows, index=frame.index))
