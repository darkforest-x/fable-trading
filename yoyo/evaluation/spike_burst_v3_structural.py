"""Causal near-zero setup-box intervention for the frozen V3 parent/child scan.

Source: SPIKE V1 near-zero counting (MD and SB together, at most0.1 previous
ATR, at least12 consecutive bars, band frozen on bar12) and V3 confirmation.
Only current/prior ready, MD, SB, ATR, OHLC and SMA20/EMA20 form a setup. High
and low include the entire near-zero run through its LAST near bar; only a
subsequent close outside that frozen high may issue its single early alert.
A later ordinary twelve-bar high during the same trend cannot rearm a box.
A fresh near-zero run supersedes the old box and must accumulate twelve bars
again. The released box is eligible only on release ages0..5, using the original
six-bar opportunity constant; age6 expires it until a new full setup. Existing
current/prior V3 quality tags alone may confirm a parent on age0..3; actual confirmation never moves back to its parent.

This is one offline structural hypothesis, no labels, tuning, order execution,
notification, data fetching or outcome scoring. Original V3 is not modified.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_early_warning as v3
from yoyo.evaluation.spike_burst_replay import MIN_QUIET, NEAR_ATR, OPPORTUNITY

HOUR = v3.HOUR
CONFIG = dict(schema="v3-near-zero-box-v1", min_quiet=MIN_QUIET, near_atr=NEAR_ATR,
              band="freeze on twelfth near bar", boundary="all near bars; frozen after release",
              consume="one early per setup", rearm="new consecutive12 near-zero bars",
              supersede="new near run invalidates old released box", early_cooldown_bars=12,
              release_window_bars=OPPORTUNITY, confirmation_max_age=3, confirmation="unchanged V3 quality; frozen full-box parent high")


def detect(frame):
    """V3-compatible causal fields plus complete setup and frozen-box provenance.

    early_condition is this intervention's actual condition; v3_early_condition
    retains the original rolling12-high condition for diagnostics only. This
    replaces structural eligibility and rebuilds events, not a filter of old
    V3 events. A gap clears setup, parent, edge and cooldown; ready=False clears
    only structure (existing parent/cooldown follow unchanged V3 semantics). A
    nonfinite structural input also invalidates the box instead of fabricating
    an oscillator release. ATR from
    before a gap cannot initialize a new near band. setup_eligible is measured
    BEFORE this bar consumes its setup; setup_consumed is measured AFTER it.
    """
    fields = v3.early_fields(frame)
    fields = fields.rename(columns={"early_condition": "v3_early_condition"})
    next_id = 0
    quiet_count = 0
    quiet_high = quiet_low = frozen_band = np.nan
    setup_id = setup_start = setup_last_near = release_i = None
    setup_bars = 0
    box_high = box_low = np.nan
    released = consumed = expired = False
    last_accepted = parent = None
    parent_high = np.nan
    previous_condition = parent_confirmed = False
    previous_atr = np.nan
    records = []
    for i, row in enumerate(frame.itertuples()):
        tag = fields.iloc[i]
        gap = bool(i and frame.index[i] - frame.index[i-1] != HOUR)
        finite_structure = all(math.isfinite(x) for x in (row.open, row.high, row.low, row.close, row.atr, row.md, row.sb))
        reset = gap or not bool(row.ready) or not finite_structure
        if reset:
            quiet_count, quiet_high, quiet_low, frozen_band = 0, np.nan, np.nan, np.nan
            setup_id = setup_start = setup_last_near = release_i = None
            setup_bars, box_high, box_low = 0, np.nan, np.nan
            released = consumed = expired = False
            previous_condition = False
            previous_atr = np.nan
        if gap:
            last_accepted = parent = None
            parent_high = np.nan
            parent_confirmed = False

        band = frozen_band if quiet_count >= MIN_QUIET else NEAR_ATR * previous_atr
        known_band = math.isfinite(band) and band > 0
        near = bool(row.ready and finite_structure and known_band
                    and max(abs(row.md), abs(row.sb)) <= band)
        if near:
            if quiet_count == 0:
                # A new developing near run supersedes any released old box.
                setup_id = release_i = None
                setup_start, setup_last_near = i, None
                setup_bars, box_high, box_low = 0, np.nan, np.nan
                released = consumed = expired = False
                quiet_high, quiet_low = row.high, row.low
            else:
                quiet_high, quiet_low = max(quiet_high, row.high), min(quiet_low, row.low)
            quiet_count += 1
            setup_last_near = i
            if quiet_count == MIN_QUIET:
                frozen_band = band
                next_id += 1
                setup_id = next_id
            if quiet_count >= MIN_QUIET:
                setup_bars = quiet_count
                box_high, box_low = quiet_high, quiet_low
        elif quiet_count:
            if quiet_count >= MIN_QUIET:
                released, release_i = True, i
                # box_high/low already end on the preceding, last near bar.
            else:
                setup_id = setup_start = setup_last_near = release_i = None
                setup_bars, box_high, box_low = 0, np.nan, np.nan
                released = consumed = expired = False
            quiet_count, quiet_high, quiet_low, frozen_band = 0, np.nan, np.nan, np.nan

        release_age = i-release_i if release_i is not None else None
        if released and release_age is not None and release_age >= OPPORTUNITY:
            expired = True
        eligible = bool(row.ready and finite_structure and released and setup_id is not None
                        and not consumed and not expired)
        condition = bool(eligible and row.close > box_high and row.close > tag.fast_high)
        edge = bool(condition and not previous_condition)
        early = bool(edge and (last_accepted is None or i-last_accepted >= 12))
        if early:
            consumed = True
            last_accepted, parent, parent_high, parent_confirmed = i, i, float(box_high), False
        if parent is not None and i-parent > 3:
            parent, parent_high, parent_confirmed = None, np.nan, False
        age = i-parent if parent is not None else None
        child = bool(parent is not None and not parent_confirmed and row.ready
                     and row.close > parent_high and tag.tag_recent_density and tag.tag_advance
                     and tag.tag_volume and tag.tag_md_ge_signal and tag.tag_middle_rising)
        if child:
            parent_confirmed = True
        records.append(dict(early_condition=condition, early=early, confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=edge, cooldown_blocked=bool(edge and not early),
            near_zero=near, near_band=band, quiet_count=quiet_count,
            setup_id=float(setup_id) if setup_id is not None else np.nan,
            setup_start_i=float(setup_start) if setup_start is not None else np.nan,
            setup_last_near_i=float(setup_last_near) if setup_last_near is not None else np.nan,
            release_i=float(release_i) if release_i is not None else np.nan,
            setup_bars=setup_bars, boxHigh=box_high, boxLow=box_low,
            setup_released=released, setup_expired=expired,
            release_age=float(release_age) if release_age is not None else np.nan,
            setup_eligible=eligible, eligible=eligible,
            setup_consumed=consumed, gap_reset=gap))
        previous_condition = condition
        previous_atr = float(row.atr) if row.ready else np.nan
    result = fields.join(pd.DataFrame(records, index=frame.index))
    result.attrs.update(frame.attrs)
    result.attrs["structural_protocol"] = CONFIG["schema"]
    return result
