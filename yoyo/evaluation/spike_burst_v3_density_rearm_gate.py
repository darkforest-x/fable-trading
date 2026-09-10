"""Research G: one V3 parent quota, restored by a new MA-density episode.

The only intervention is a causal quota on the original V3 full-condition
rising edge. A continuous-hour segment starts with a bootstrap quota. An
accepted parent consumes it. After that parent, observe a VALID dense=False,
then VALID dense=True whose twelve prior width/flip observation rows are all
later than the owner: i-12 > owner_i. That restores one quota. Dense is the
unchanged early_fields.prog_dense: ready, pastWidth<=3 and pastCrosses>=2.
It already summarizes twelve observations; do not require twelve dense=True
summaries. Restoration may occur during a continuing True run when its support
first becomes disjoint; it need not be a False-to-True edge on that exact bar.

Density validity uses current ready and finite pastWidth/pastCrosses, plus
finite original width/flips on [i-12,i) within the same hourly segment. The
current width/flip is excluded. "Disjoint" refers to those observation rows,
not to every dependency of the feature calculations: MAs have earlier memory,
and each flip itself compares against the preceding MA observation. Unknown
density is never interpreted as leaving density. The accepted bar's density
does not count as a post-owner False; the new owner starts with False unseen.

Original V3 fields consume only current/past OHLCV, prior12 highs, fast MAs,
recent density, three-bar advance/volume, MD/SB and ZLEMA. Raw edges are consumed
even when rejected; only accepted parents update the original12-bar cooldown.
The frozen owner box is the ACCEPTED bar's original prior12 high/low, not the
rearm window. Its low/high never unlock or otherwise gate signals. A new dense
episode above or below that box can restore quota. The optional rearm box is
provenance only. Children retain the original frozen parent high and age0..3
quality rules; the quota never filters a child or relocates its confirmation.

No risk/holding lifecycle, future prices, labels, trade outcomes, scoring,
training, notification or production state is read. All state is reconstructed
over the supplied history, without a research-period reset or end liquidation.
This single research hypothesis does not establish successful noise reduction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_early_warning import HOUR, early_fields

LOOKBACK = 12
CONFIG = dict(schema="v3-density-rearm-quota-v1", lookback=LOOKBACK,
    dense="unchanged prog_dense: ready & pastWidth<=3 & pastCrosses>=2",
    validity="finite current summaries and twelve prior original width/flips observations within one hourly segment",
    bootstrap=True, false_observation="strictly after accepted owner",
    rearm="valid False seen, then valid True and i-12>owner_i; True need not begin on this bar",
    restoration_before_raw_edge=True, raw_edge="unchanged complete V3 condition; no queued edge",
    cooldown="12 bars, accepted parents only", confirmation_max_age=3,
    owner_box="accepted parent original prior12 high/low; immutable provenance only",
    rearm_box="prior12 at quota restoration; provenance only, never replaces parent high",
    risk_occupancy=False, future_inputs=False, production_eligible=False)


def _density_fields(frame, fields):
    """Audit the original current summary and its twelve prior observed rows.

    Called after early_fields validates UTC hourly, unique, increasing clocks.
    The first twelve rows after a gap have no complete prior support. A NaN or
    infinite width/flip invalidates each following window containing that row;
    no skipna reduction converts it into known dense=False.
    """
    required = {"width", "flips", "pastWidth", "pastCrosses", "ready"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("Density rearm requires original feature columns: " + ", ".join(sorted(missing)))
    observations_finite = np.isfinite(frame[["width", "flips"]].to_numpy(float)).all(axis=1)
    summaries_finite = np.isfinite(frame[["pastWidth", "pastCrosses"]].to_numpy(float)).all(axis=1)
    segments = np.r_[0, np.cumsum(np.diff(frame.index.asi8) != HOUR.value)]
    support = pd.Series(observations_finite.astype(float), index=frame.index)
    complete = np.zeros(len(frame), dtype=bool)
    finite = np.zeros(len(frame), dtype=bool)
    starts = np.full(len(frame), np.nan)
    ends = np.full(len(frame), np.nan)
    for segment in np.unique(segments):
        positions = np.flatnonzero(segments == segment)
        part = support.iloc[positions]
        totals = part.shift().rolling(LOOKBACK, min_periods=LOOKBACK).sum().to_numpy()
        complete[positions] = np.isfinite(totals)
        finite[positions] = totals == LOOKBACK
        available = positions[np.arange(len(positions)) >= LOOKBACK]
        starts[available], ends[available] = available - LOOKBACK, available - 1
    valid = complete & finite & summaries_finite & frame.ready.eq(True).to_numpy()
    dense = valid & fields.prog_dense.eq(True).to_numpy()
    return pd.DataFrame(dict(density_valid=valid, density_is_dense=dense,
        density_known_false=valid & ~dense, density_support_complete=complete,
        density_support_finite=finite, density_summaries_finite=summaries_finite,
        density_window_start_i=starts, density_window_end_i=ends), index=frame.index)


def detect(frame):
    """Rebuild G parent/child events and prefix-stable quota diagnostics.

    Per closed bar: reset at a gap; update the preceding owner's density/rearm
    state; evaluate the original raw edge, accepted cooldown and quota; on
    acceptance freeze the new owner and clear its False-seen state; evaluate
    the unchanged child window. ``quota_armed_before`` precedes density updates,
    ``quota_armed_for_edge`` follows them, and ``quota_armed`` follows acceptance.
    ``support_disjoint`` and ``structure_owner_before_i`` describe the rearm
    check before a possible same-bar acceptance changes the owner.

    ``accept_origin`` is populated only on accepted events. bootstrap_quota
    means an unused bootstrap quota remains. structure_bootstrap identifies
    the currently recorded owner's origin even after its child has expired.
    Rearm provenance persists through its resulting parent and is overwritten
    only by a later restoration or gap. The owner box never ratchets. A known
    price break of either owner boundary does not restore quota. On a raw edge,
    rejection reasons give cooldown priority, then explain why quota is locked.
    """
    fields = early_fields(frame)
    fields = fields.join(_density_fields(frame, fields))
    last_accepted = parent = owner = None
    parent_high = owner_high = owner_low = np.nan
    previous_condition = child_sent = False
    armed, quota_origin = True, "bootstrap"
    false_seen = False
    first_false = rearm_i = rearm_start = rearm_end = rearm_owner = rearm_false = None
    rearm_high = rearm_low = np.nan
    rearm_wait = np.nan
    owner_origin = ""
    structure_id = 0
    records = []
    for i, (bar, field) in enumerate(zip(frame.itertuples(), fields.itertuples())):
        gap = bool(i and frame.index[i] - frame.index[i-1] != HOUR)
        if gap:
            last_accepted = parent = owner = None
            parent_high = owner_high = owner_low = np.nan
            previous_condition = child_sent = False
            armed, quota_origin, owner_origin = True, "bootstrap", ""
            false_seen = False
            first_false = rearm_i = rearm_start = rearm_end = rearm_owner = rearm_false = None
            rearm_high = rearm_low = np.nan
            rearm_wait = np.nan

        owner_before = owner
        armed_before = armed
        support_disjoint = bool(owner is not None and i-LOOKBACK > owner)
        rearmed = False
        if owner is not None and not armed:
            if field.density_known_false:
                if not false_seen:
                    first_false = i
                false_seen = True
            if false_seen and field.density_valid and field.density_is_dense and support_disjoint:
                armed, quota_origin, rearmed = True, "density_rearm", True
                rearm_i, rearm_start, rearm_end = i, i-LOOKBACK, i-1
                rearm_owner, rearm_false, rearm_wait = owner, first_false, float(i-owner)
                rearm_high, rearm_low = float(field.prog_prior_high), float(field.prog_prior_low)

        armed_for_edge = armed
        condition = bool(field.early_condition)
        edge = bool(condition and not previous_condition)
        cooldown = last_accepted is None or i-last_accepted >= LOOKBACK
        early = bool(edge and cooldown and armed_for_edge)
        blocked = bool(edge and cooldown and not armed_for_edge)
        reason = ""
        if edge and not early:
            if not cooldown:
                reason = "cooldown"
            elif not field.density_valid:
                reason = "density_unknown"
            elif not false_seen:
                reason = "await_density_false"
            elif not field.density_is_dense:
                reason = "await_density_reentry"
            else:
                reason = "await_disjoint_support"
        accept_origin = quota_origin if early else ""
        if early:
            last_accepted = parent = owner = i
            parent_high = owner_high = float(field.prog_prior_high)
            owner_low = float(field.prog_prior_low)
            owner_origin = quota_origin
            child_sent, armed, false_seen = False, False, False
            first_false = None
            structure_id += 1

        if parent is not None and i-parent > 3:
            parent, parent_high, child_sent = None, np.nan, False
        age = i-parent if parent is not None else None
        child = bool(parent is not None and not child_sent and bar.ready
            and bar.close > parent_high and field.tag_recent_density
            and field.tag_advance and field.tag_volume and field.tag_md_ge_signal
            and field.tag_middle_rising)
        if child:
            child_sent = True
        records.append(dict(early=early, confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=edge, cooldown_blocked=bool(edge and not cooldown),
            structure_blocked=blocked, reject_reason=reason, gap_reset=gap,
            quota_armed_before=bool(armed_before), quota_armed_for_edge=bool(armed_for_edge),
            quota_armed=bool(armed), bootstrap_quota=bool(armed and quota_origin == "bootstrap"),
            accept_origin=accept_origin, bootstrap_accepted=bool(early and accept_origin == "bootstrap"),
            structure_id=float(structure_id) if owner is not None else np.nan,
            structure_owner_i=float(owner) if owner is not None else np.nan,
            structure_owner_before_i=float(owner_before) if owner_before is not None else np.nan,
            structure_bootstrap=bool(owner is not None and owner_origin == "bootstrap"),
            structure_high=owner_high, structure_low=owner_low,
            density_false_seen=bool(false_seen),
            first_false_i=float(first_false) if first_false is not None else np.nan,
            support_disjoint=support_disjoint, rearmed=rearmed,
            rearm_i=float(rearm_i) if rearm_i is not None else np.nan,
            rearm_owner_i=float(rearm_owner) if rearm_owner is not None else np.nan,
            rearm_false_i=float(rearm_false) if rearm_false is not None else np.nan,
            rearm_window_start_i=float(rearm_start) if rearm_start is not None else np.nan,
            rearm_window_end_i=float(rearm_end) if rearm_end is not None else np.nan,
            rearm_window_high=rearm_high, rearm_window_low=rearm_low,
            rearm_wait_bars=rearm_wait,
            structure_age_bars=float(i-owner) if owner is not None else np.nan,
            last_accepted_i=float(last_accepted) if last_accepted is not None else np.nan))
        previous_condition = condition
    result = fields.join(pd.DataFrame(records, index=frame.index))
    result.attrs.update(frame.attrs)
    result.attrs["density_rearm_protocol"] = CONFIG["schema"]
    return result
