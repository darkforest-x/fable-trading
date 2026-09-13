"""Causal SPIKE V1 short-only replay for the current Pine ``空头`` setting.

The frozen monitor replay remains long-only so its historical event sequence is
unchanged.  This adapter transcribes the same current Pine source with its
``方向 = 空头`` input: it admits only negative releases/price-first bursts,
uses the five-bar high for its initial risk reference, and keeps short
protection on the upside.  It consumes closed OHLCV-derived fields only.
"""
from __future__ import annotations

import math

import pandas as pd

from yoyo.evaluation.spike_burst_replay import (
    DENSE_CROSSES,
    DENSE_WIDTH,
    MIN_QUIET,
    NEAR_ATR,
    OPPORTUNITY,
    RESULT_COLUMNS,
    _validate_index,
    launch_window,
    path_reference,
    pine_gt,
    pine_ge,
    pine_le,
    pine_lt,
    price_burst,
    burst,
    risk_reference,
)

NAN = float("nan")


def replay_short(frame: pd.DataFrame, tick: float) -> pd.DataFrame:
    """Replay the current Pine source with ``方向`` fixed to ``空头``.

    Required fields are the frozen V1 feature columns calculated from the
    current and prior closed bars.  The independent short state deliberately
    cannot be ended or consumed by a long release, preserving the existing
    long-only replay's signal and outcome comparability.
    """
    _validate_index(frame)
    if tick is None or not math.isfinite(tick) or tick <= 0:
        raise ValueError("A positive finite exchange price tick is required; no inferred tick")
    required = {"open", "high", "low", "close", "md", "sb", "middle", "atr", "pastWidth",
                "pastCrosses", "ropeHigh", "ropeLow", "recentHigh", "rv", "expansion", "ready"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("Missing Pine feature fields: " + ", ".join(sorted(missing)))
    if frame.ready.isna().any():
        raise ValueError("ready must be explicit True or False on every bar")

    quiet_count = pending_side = trend_side = launch_quiet = 0
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
        exit_price = age = NAN
        route = ""
        ended = False
        if bool(row.ready):
            if trend_side and i > entry_bar:
                path = path_reference(-1, entry, risk, protection, peak_r, trail_armed,
                                      o, h, l, c, row.atr, tick=tick)
                protection, peak_r, current_r, trail_armed = (
                    path.protection, path.peak_r, path.current_r, path.armed)
                if not path.alive:
                    exit_event, ended, exit_price = True, True, path.exit_price
                    trend_side, pending_side = 0, 0
                    state = "保护触及 · 本段结束"
                else:
                    state = "趋势跟踪" if trail_armed else "爆发后 · 初始保护"

            band = frozen_band if quiet_count >= MIN_QUIET and not math.isnan(frozen_band) else NEAR_ATR * previous_atr
            can_lead = (quiet_count >= MIN_QUIET and pending_side == 0 and trend_side == 0 and not ended
                        and pine_le(row.pastWidth, DENSE_WIDTH) and pine_ge(row.pastCrosses, DENSE_CROSSES))
            # Pine's ``方向 = 空头`` disables the long branch before the shared
            # lifecycle runs.  Do the same instead of mirroring a long event.
            leading_side = -1 if can_lead and price_burst(
                -1, o, h, l, c, row.md, row.sb, row.middle, previous_mi, quiet_high, quiet_low,
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
                    side = -1 if pine_lt(row.md, -band) else 0
                    if dense and side == -1 and trend_side == 0 and not ended:
                        pending_side, released_at = side, i
                        launch_high, launch_low, launch_band, launch_quiet = quiet_high, quiet_low, band, quiet_count
                    elif trend_side == 0 and not ended:
                        state = "方向或主线未通过" if dense else "释放 · 密集未通过"
                quiet_count, frozen_band, quiet_high, quiet_low = 0, NAN, NAN, NAN

            if pending_side and trend_side == 0 and not ended:
                age = i - released_at
                valid = bool(leading_side) or launch_window(age, OPPORTUNITY, -1,
                                                            row.md, launch_band, c, launch_high, launch_low)
                if not valid:
                    pending_side = 0
                    state = "窗口结束 · 等待新蓄势"
                else:
                    state = "待爆发 · %d/%d" % (age + 1, OPPORTUNITY)
                    fires = bool(leading_side) or burst(-1, o, h, l, c, row.md, previous_md,
                                                        row.sb, launch_high, launch_low, row.ropeHigh,
                                                        row.ropeLow, row.rv, row.expansion)
                    if fires:
                        burst_event, route = True, "price_first" if leading_side else "release_confirm"
                        ref = risk_reference(-1, c, row.recentHigh, row.atr, tick=tick)
                        entry, initial_stop, risk, protection, entry_bar = c, ref.stop, ref.risk, ref.stop, i
                        peak_r, current_r, trail_armed = 0.0, 0.0 if ref.valid else NAN, False
                        trend_side, pending_side = -1 if ref.valid else 0, 0
                        state = "强劲爆发 · 初始保护" if ref.valid else "爆发 · 风险参考不可用"

        records.append(dict(burst=burst_event, burst_up=False, burst_down=burst_event, route=route,
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
    result.attrs.update(frame.attrs)
    result.attrs.update(direction="short", pine_direction_setting="空头", tick=float(tick),
                        price_semantics="signal-close references, not execution fills")
    return result
