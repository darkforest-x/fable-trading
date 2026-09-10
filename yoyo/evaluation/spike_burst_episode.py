"""Single-variable causal density-episode consumption ablation of SPIKE V2.

Source: frozen spike_burst_progressive.replay. This module changes only whether
its progressive route may reuse an already-consumed density episode. All V2
thresholds, ordinary V1 routes, risk and reference-protection helpers remain.
No training, market fetching, scoring, notification or execution is performed.

Density uses ready, pastWidth and pastCrosses, each current summary based on
previous12 source observations. Maximal contiguous true runs receive causal IDs.
A candidate can use only the latest ID observed in its previous12 bars; current
bar density cannot qualify itself. Any accepted launch consumes the eligible ID,
including invalid-risk launch markers. Exiting never rearms an old ID. New
false-to-true density starts a new ID. At missing-bar boundaries attribution
resets and requires12 fresh observations before accepting a density summary.
"""
from collections import deque
import math

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_progressive as v2
from yoyo.evaluation import spike_burst_replay as v1
from yoyo.evaluation.spike_burst_replay import (
    NAN, MIN_QUIET, NEAR_ATR, DENSE_WIDTH, DENSE_CROSSES, OPPORTUNITY,
    RESULT_COLUMNS, _validate_index, pine_gt, pine_ge, pine_le, pine_lt,
    price_burst, launch_window, burst, risk_reference, path_reference,
)
PROGRESSIVE_COLUMNS = v2.PROGRESSIVE_COLUMNS
DENSE_LOOKBACK = v2.DENSE_LOOKBACK
EPISODE_COLUMNS = ("episode_density", "episode_id", "prior_episode_id",
                   "prior_episode_start", "prior_episode_last_dense_bar",
                   "density_segment", "density_gap_reset")


def density_episodes(frame):
    """Causal ID attribution from current ready/pastWidth/pastCrosses only.

    Input pastWidth and pastCrosses summarize the previous12 observations, so
    the first12 summaries in each continuous segment are ineligible. The
    previous12 ID deque is read BEFORE adding current density. Expected cadence
    uses frame.attrs['minutes'] (default60 for this registered 1H study).
    """
    _validate_index(frame)
    if not {"ready", "pastWidth", "pastCrosses"}.issubset(frame):
        raise ValueError("Missing ready/pastWidth/pastCrosses density inputs")
    if frame.ready.isna().any():
        raise ValueError("ready must be explicit on every bar")
    minutes = frame.attrs.get("minutes", 60)
    if not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("A positive finite bar cadence in minutes is required")
    cadence = pd.Timedelta(minutes=minutes)
    history = deque(maxlen=DENSE_LOOKBACK)
    segment_start = segment = next_id = 0
    active_id = active_start = None
    was_dense = False
    rows = []
    for i, row in enumerate(frame.itertuples()):
        gap = bool(i and frame.index[i] - frame.index[i-1] != cadence)
        if gap:
            history.clear()
            segment += 1
            segment_start = i
            active_id = active_start = None
            was_dense = False
        eligible = [item for item in history if item[0] is not None]
        prior_id, prior_start, prior_last = eligible[-1] if eligible else (None, None, None)
        dense = bool(i-segment_start >= DENSE_LOOKBACK and row.ready
                     and row.pastWidth <= DENSE_WIDTH and row.pastCrosses >= DENSE_CROSSES)
        if dense and not was_dense:
            next_id += 1
            active_id, active_start = next_id, i
        rows.append(dict(episode_density=dense,
            episode_id=float(active_id) if dense else np.nan,
            prior_episode_id=float(prior_id) if prior_id is not None else np.nan,
            prior_episode_start=float(prior_start) if prior_start is not None else np.nan,
            prior_episode_last_dense_bar=float(prior_last) if prior_last is not None else np.nan,
            density_segment=segment, density_gap_reset=gap))
        history.append((active_id, active_start, i) if dense else (None, None, None))
        was_dense = dense
    return pd.DataFrame(rows, index=frame.index, columns=EPISODE_COLUMNS)


def replay(frame, tick, enhanced=True, episode_gate=True):
    """True V2 state replay with one consumed-density-episode gate.

    Disabled returns frozen V2 byte-for-byte values, dtypes and metadata. Enabled
    adds provenance and gates only the progressive route, retaining hard-route
    priority and unchanged V1 risk/protection helpers. Rejected candidates do
    not create holding state. Original caller segmentation contract remains for
    V2 price/risk history; density attribution independently resets at gaps.
    """
    if not episode_gate:
        return v2.replay(frame, tick, enhanced=enhanced)
    _validate_index(frame)
    if tick is None or not math.isfinite(tick) or tick <= 0:
        raise ValueError("A positive finite exchange price tick is required; no inferred tick")
    required = {"open", "high", "low", "close", "md", "sb", "middle", "atr", "pastWidth",
                "pastCrosses", "ropeHigh", "ropeLow", "recentLow", "recentHigh", "rv", "expansion", "ready"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("Missing Pine feature fields: " + ", ".join(sorted(missing)))
    if frame.ready.isna().any():
        raise ValueError("ready must be explicit True or False on every bar")
    if enhanced and not set(PROGRESSIVE_COLUMNS).issubset(frame.columns):
        raise ValueError("Enhanced replay requires progressive_fields; no hidden recomputation")
    if enhanced and frame.prog_up.isna().any():
        raise ValueError("prog_up must be explicit True or False on every bar")
    episode_data = density_episodes(frame)
    consumed_episodes = set()
    provenance = []
    quiet_count, pending_side, trend_side, launch_quiet = 0, 0, 0, 0
    frozen_band = quiet_high = quiet_low = NAN
    launch_high = launch_low = launch_band = NAN
    released_at = entry_bar = None
    entry = initial_stop = risk = protection = current_r = NAN
    peak_r, trail_armed = 0.0, False
    previous_md = previous_mi = previous_atr = NAN
    state = "等待蓄势"
    records = []
    for i, row in enumerate(frame.itertuples(index=False)):
        episode = episode_data.iloc[i]
        episode_id = episode.prior_episode_id
        episode_available = bool(pd.notna(episode_id) and int(episode_id) not in consumed_episodes)
        progressive_candidate = gate_blocked = consumed_this_bar = False
        o, h, l, c = row.open, row.high, row.low, row.close
        active_protection = protection if trend_side else NAN
        active_initial = initial_stop if trend_side else NAN
        active_entry = entry if trend_side else NAN
        burst_event = exit_event = False
        exit_price, age = NAN, NAN
        route = ""
        ended = False
        if bool(row.ready):
            if trend_side and i > entry_bar:
                path = path_reference(trend_side, entry, risk, protection, peak_r, trail_armed,
                                      o, h, l, c, row.atr, tick=tick)
                protection, peak_r, current_r, trail_armed = path.protection, path.peak_r, path.current_r, path.armed
                if not path.alive:
                    exit_event, ended, exit_price = True, True, path.exit_price
                    trend_side, pending_side = 0, 0
                    state = "保护触及 · 本段结束"
                else:
                    state = "趋势跟踪" if trail_armed else "爆发后 · 初始保护"

            band = frozen_band if quiet_count >= MIN_QUIET and not math.isnan(frozen_band) else NEAR_ATR * previous_atr
            can_lead = (quiet_count >= MIN_QUIET and pending_side == 0 and trend_side == 0 and not ended
                        and pine_le(row.pastWidth, DENSE_WIDTH) and pine_ge(row.pastCrosses, DENSE_CROSSES))
            leading_side = 1 if can_lead and price_burst(
                1, o, h, l, c, row.md, row.sb, row.middle, previous_mi, quiet_high, quiet_low,
                row.ropeHigh, row.ropeLow, row.rv, row.expansion) else 0
            near = pending_side == 0 and pine_le(max(abs(row.md), abs(row.sb)), band)
            if leading_side:
                pending_side, released_at = leading_side, i
                launch_high, launch_low, launch_band, launch_quiet = quiet_high, quiet_low, band, quiet_count
                quiet_count, frozen_band, quiet_high, quiet_low = 0, NAN, NAN, NAN
            elif near:
                pending_side = 0
                quiet_count += 1
                quiet_high = h if quiet_count == 1 else max(quiet_high, h)
                quiet_low = l if quiet_count == 1 else min(quiet_low, l)
                if quiet_count == MIN_QUIET:
                    frozen_band = band
                if trend_side == 0 and not ended:
                    state = "蓄势就绪 · 等待爆发" if quiet_count >= MIN_QUIET else "近零整理"
            else:
                if quiet_count >= MIN_QUIET:
                    dense = pine_le(row.pastWidth, DENSE_WIDTH) and pine_ge(row.pastCrosses, DENSE_CROSSES)
                    side = 1 if pine_gt(row.md, band) else -1 if pine_lt(row.md, -band) else 0
                    if dense and side == 1 and trend_side == 0 and not ended:
                        pending_side, released_at = side, i
                        launch_high, launch_low, launch_band, launch_quiet = quiet_high, quiet_low, band, quiet_count
                    elif trend_side == 0 and not ended:
                        state = "方向或主线未通过" if dense else "释放 · 密集未通过"
                quiet_count, frozen_band, quiet_high, quiet_low = 0, NAN, NAN, NAN

            if pending_side and trend_side == 0 and not ended:
                age = i - released_at
                valid = bool(leading_side) or launch_window(age, OPPORTUNITY, pending_side,
                                                          row.md, launch_band, c, launch_high, launch_low)
                if not valid:
                    pending_side = 0
                    state = "窗口结束 · 等待新蓄势"
                else:
                    state = "待爆发 · %d/%d" % (age + 1, OPPORTUNITY)
                    fires = bool(leading_side) or burst(pending_side, o, h, l, c, row.md, previous_md,
                                                       row.sb, launch_high, launch_low, row.ropeHigh,
                                                       row.ropeLow, row.rv, row.expansion)
                    if fires:
                        side = pending_side
                        burst_event, route = True, "price_first" if leading_side else "release_confirm"
                        ref = risk_reference(side, c, row.recentLow, row.atr, tick=tick)
                        entry, initial_stop, risk, protection, entry_bar = c, ref.stop, ref.risk, ref.stop, i
                        peak_r, current_r, trail_armed = 0.0, 0.0 if ref.valid else NAN, False
                        trend_side, pending_side = side if ref.valid else 0, 0
                        state = "强劲爆发 · 初始保护" if ref.valid else "爆发 · 风险参考不可用"

            # V1 has already evaluated both hard-burst routes on this close.
            # Even a V1 arrow with invalid reference risk keeps same-bar priority.
            progressive_candidate = bool(enhanced and not burst_event and trend_side == 0
                                         and not ended and row.prog_up)
            gate_blocked = progressive_candidate and not episode_available
            if progressive_candidate and episode_available:
                burst_event, route = True, "progressive"
                ref = risk_reference(1, c, row.recentLow, row.atr, tick=tick)
                entry, initial_stop, risk, protection, entry_bar = c, ref.stop, ref.risk, ref.stop, i
                peak_r, current_r, trail_armed = 0.0, 0.0 if ref.valid else NAN, False
                trend_side, pending_side = 1 if ref.valid else 0, 0
                # This path is not a near-zero release; never fabricate its age
                # or quiet duration. Freeze its own PRIOR12 price context only.
                launch_high, launch_low = row.prog_prior_high, row.prog_prior_low
                launch_band, launch_quiet, released_at, age = NAN, 0, None, NAN
                quiet_count, frozen_band, quiet_high, quiet_low = 0, NAN, NAN, NAN
                state = "渐进启动 · 初始保护" if ref.valid else "渐进启动 · 风险参考不可用"

        if burst_event and pd.notna(episode_id):
            consumed_episodes.add(int(episode_id))
            consumed_this_bar = True
        provenance.append(dict(episode_available_before=episode_available,
            episode_consumed=bool(pd.notna(episode_id) and int(episode_id) in consumed_episodes),
            episode_consumed_this_bar=consumed_this_bar,
            progressive_candidate=progressive_candidate, episode_gate_blocked=gate_blocked))
        records.append(dict(burst=burst_event, burst_up=burst_event, burst_down=False, route=route,
                            exit=exit_event, exit_price=exit_price, trend_side=trend_side,
                            pending_side=pending_side, quiet_count=quiet_count, quiet_bars=launch_quiet,
                            frozen_band=frozen_band, quiet_high=quiet_high, quiet_low=quiet_low,
                            launch_high=launch_high, launch_low=launch_low, launch_band=launch_band,
                            release_bar=released_at, wait_bars=age, entry_bar=entry_bar,
                            entry_ref=entry, initial_stop=initial_stop, risk=risk,
                            risk_valid=math.isfinite(risk) and pine_gt(risk, 0),
                            protection=protection, active_protection=active_protection,
                            active_initial_stop=active_initial, active_entry_ref=active_entry,
                            peak_r=peak_r, current_r=current_r, trail_armed=trail_armed, state=state))
        previous_md, previous_mi, previous_atr = row.md, row.middle, row.atr
    result = pd.DataFrame(records, index=frame.index, columns=RESULT_COLUMNS)
    if enhanced:
        # Missing integer positions must not acquire a new dtype only after a
        # future event exists; the disabled route retains V1's exact schema.
        for name in ("release_bar", "entry_bar"):
            result[name] = pd.to_numeric(result[name]).astype(float)
    result = result.join(episode_data).join(pd.DataFrame(provenance, index=frame.index))
    result.attrs.update(frame.attrs)
    result.attrs.update(source_sha256=v2.SOURCE_SHA256 if enhanced else v1.SOURCE_SHA256, direction="long", tick=float(tick),
                        price_semantics="signal-close references, not execution fills")
    result.attrs.update(episode_gate=True, episode_protocol="v2-density-episode-token-v1")
    return result
