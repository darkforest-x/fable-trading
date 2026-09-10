"""Frozen engineering hypothesis for gradual SPIKE launches; LONG-only replay.

This adds one causal path to V1, with V1 same-bar priority and unchanged shared
signal-close risk / next-bar protection. It does not establish profitability.
No market observations, fitting, grid search, execution fills or deployment occur
in this module. Pine supports a short-side mirror, which is NOT backtested here.

New fields use OHLCV and existing V1 fields only: prior12 ready six-MA-density
observations, prior12 high/low, close/ATR three bars ago, current three volumes
and true ranges, median of twenty nonmissing volumes ending at i-3, current
ropeHigh/ropeLow, current/prior middle, and current MD/SB. No future bars are read.
The three current volumes must all exist; an absent numerator is never zero-filled.
Thresholds are a single fixed engineering proposal, not optimized parameters.
"""
import math
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_replay as v1
from yoyo.evaluation.spike_burst_replay import (
    NAN, MIN_QUIET, NEAR_ATR, DENSE_WIDTH, DENSE_CROSSES, OPPORTUNITY,
    RESULT_COLUMNS, _validate_index, pine_gt, pine_ge, pine_le, pine_lt,
    price_burst, launch_window, burst, risk_reference, path_reference,
)

SOURCE_SHA256 = "87764a2e030fb0f7b58f95d2799485a92dbec791d424ad054afb5f417a57a70b"
DENSE_LOOKBACK, PROGRESS_BARS, VOLUME_BASELINE = 12, 3, 20
MIN_PROGRESS, MIN_PROGRESS_VOLUME = 1.5, 1.5
MIN_EFFICIENCY, MIN_PROGRESS_END = 0.55, 0.65
PROGRESSIVE_COLUMNS = (
    "prog_dense", "prog_dense_hits", "prog_recent_dense", "prog_prior_high", "prog_prior_low",
    "prog_advance", "prog_volume_base", "prog_volume_ratio", "prog_efficiency",
    "prog_close_position", "prog_up",
)


def progressive_fields(frame):
    """New columns only; input is V1 features, never modified or overwritten.

    At i, dense hits count D[i-12:i]; the breakout excludes current high/low.
    Advance is (close[i]-close[i-3])/ATR[i-3]; volume baseline is the median
    of20 prior nonmissing volumes ending i-3. Numerator and TR use i-2..i.
    """
    _validate_index(frame)
    needed = {"ready", "pastWidth", "pastCrosses", "high", "low", "open", "close",
              "volume", "tr", "atr", "ropeHigh", "middle", "md", "sb"}
    missing = needed - set(frame.columns)
    if missing:
        raise ValueError("Missing V1 progressive inputs: " + ", ".join(sorted(missing)))
    if frame.ready.isna().any():
        raise ValueError("ready must be explicit True or False on every bar")
    out = pd.DataFrame(index=frame.index)
    out["prog_dense"] = frame.ready.eq(True) & (frame.pastWidth <= DENSE_WIDTH) & (frame.pastCrosses >= DENSE_CROSSES)
    out["prog_dense_hits"] = out.prog_dense.astype(float).shift(1).rolling(DENSE_LOOKBACK, min_periods=DENSE_LOOKBACK).sum()
    out["prog_recent_dense"] = out.prog_dense_hits.gt(0)
    out["prog_prior_high"] = frame.high.shift(1).rolling(DENSE_LOOKBACK, min_periods=DENSE_LOOKBACK).max()
    out["prog_prior_low"] = frame.low.shift(1).rolling(DENSE_LOOKBACK, min_periods=DENSE_LOOKBACK).min()
    previous_atr = frame.atr.shift(PROGRESS_BARS)
    move = frame.close - frame.close.shift(PROGRESS_BARS)
    out["prog_advance"] = (move / previous_atr).where(previous_atr.gt(0))
    historical_volume = frame.volume.shift(PROGRESS_BARS)
    out["prog_volume_base"] = (historical_volume.dropna().rolling(VOLUME_BASELINE, min_periods=VOLUME_BASELINE)
        .median().reindex(frame.index).ffill())
    volume_sum = frame.volume.rolling(PROGRESS_BARS, min_periods=PROGRESS_BARS).sum()
    out["prog_volume_ratio"] = (volume_sum / (PROGRESS_BARS * out.prog_volume_base)).where(out.prog_volume_base.gt(0))
    tr_sum = frame.tr.rolling(PROGRESS_BARS, min_periods=PROGRESS_BARS).sum()
    out["prog_efficiency"] = (move / tr_sum).where(tr_sum.gt(0))
    span = frame.high - frame.low
    out["prog_close_position"] = ((frame.close - frame.low) / span).where(span.gt(0))
    out["prog_up"] = (frame.ready.eq(True) & out.prog_recent_dense
        & frame.close.gt(out.prog_prior_high) & frame.close.gt(frame.ropeHigh) & frame.close.gt(frame.open)
        & frame.middle.gt(frame.middle.shift(1)) & frame.md.ge(frame.sb)
        & out.prog_advance.ge(MIN_PROGRESS) & out.prog_volume_ratio.ge(MIN_PROGRESS_VOLUME)
        & out.prog_efficiency.ge(MIN_EFFICIENCY) & out.prog_close_position.ge(MIN_PROGRESS_END))
    return out.loc[:, list(PROGRESSIVE_COLUMNS)]


def features(ohlcv):
    """Preserve every V1 feature and append the causal progressive fields."""
    frame = v1.features(ohlcv)
    if set(PROGRESSIVE_COLUMNS) & set(frame.columns):
        raise ValueError("Input already contains progressive fields; refusing silent overwrite")
    extra = progressive_fields(frame)
    result = frame.join(extra)
    result.attrs.update(frame.attrs)
    return result


def replay(frame, tick, enhanced=True):
    """V1 state plus a lower-priority progressive launch; ordinary closed bars.

    enhanced=False retains V1 columns, dtypes, values and attrs exactly. Enhanced
    mode requires fields from features() or progressive_fields(). A progressive
    launch consumes the same holding state, so it can suppress later V1 arrows.
    That gained/lost/earlier tradeoff must be measured, never assumed beneficial.
    """
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
            if enhanced and not burst_event and trend_side == 0 and not ended and bool(row.prog_up):
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
    result.attrs.update(frame.attrs)
    result.attrs.update(source_sha256=SOURCE_SHA256 if enhanced else v1.SOURCE_SHA256, direction="long", tick=float(tick),
                        price_semantics="signal-close references, not execution fills")
    return result
