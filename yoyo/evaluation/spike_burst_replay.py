"""Causal replay of frozen SPIKE Burst V1, not an executable trading strategy.

Source: ``pine/spike_burst_v1.pine`` SHA256 ``SOURCE_SHA256``. The source's
engineering defaults and exact state-update order are frozen here. Features use
only current/prior OHLCV: SMA/EMA 20/60/120; SMA-seeded SMMA 34 high/low and 14
true range; double EMA 34 of HLC3; SMA 9 of MD; previous 12 width/flip observations;
median of the previous 20 nonmissing volume observations; current 5-bar extremes.
The state consumes fully closed ordinary time bars, without resampling or filling
missing bars. A caller must segment discontinuous histories before calling.

An arrow uses signal close as a REFERENCE, not a fill. Protection computed at a
close becomes active on the next bar. Execution costs, next-open fills, position
sizing, and funding belong to a separate evaluation layer. A supplied positive
exchange price tick is mandatory: the replay never estimates or substitutes it.

Pine's type-system documentation says float comparison operands round to nine
fractional digits. Native series probes on 2026-09-10 contradicted that claim:
the runtime series x=close/(close+tick)*0.49e-9 satisfies x>0 and close+x>close.
We therefore keep native-observed full-precision series comparisons. Literal
and simple-qualified comparisons can differ; see the experiment's native probe
receipts, rather than assuming price-scale invariance from documentation alone.
The native simple/input zero boundary is epsilon=1e-10: tick=1e-10 is not
positive, but tick=1.01e-10 is. This applies specifically to f_risk's simple
syminfo.mintick>0 guard; physical execution is deliberately a separate module.
"""

from itertools import combinations
import math
from typing import NamedTuple

import numpy as np
import pandas as pd


SOURCE_SHA256 = "18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2"
MA_LEN, SIG_LEN, MIN_QUIET, DENSE_LEN, OPPORTUNITY = 34, 9, 12, 12, 6
NEAR_ATR, DENSE_WIDTH, DENSE_CROSSES = 0.10, 3.0, 2
MIN_VOLUME, MIN_EXPANSION, MIN_BODY, MIN_END = 4.0, 3.0, 0.55, 0.75
STOP_BUFFER, RISK_FLOOR, ARM_R, TRAIL_ATR = 0.2, 2.0, 2.0, 4.0
NAN = float("nan")
RESULT_COLUMNS = (
    "burst", "burst_up", "burst_down", "route", "exit", "exit_price", "trend_side",
    "pending_side", "quiet_count", "quiet_bars", "frozen_band", "quiet_high", "quiet_low",
    "launch_high", "launch_low", "launch_band", "release_bar", "wait_bars", "entry_bar",
    "entry_ref", "initial_stop", "risk", "risk_valid", "protection", "active_protection",
    "active_initial_stop", "active_entry_ref", "peak_r", "current_r", "trail_armed", "state",
)


def pine_gt(a, b):
    """Native-observed SERIES comparison; do not substitute blanket round9."""
    return a > b


def pine_ge(a, b):
    return a >= b


def pine_lt(a, b):
    return a < b


def pine_le(a, b):
    return a <= b


def _validate_index(frame):
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("A DatetimeIndex of closed ordinary time bars is required")
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Bar index must be increasing, unique, and nonmissing")


def _sma(series, length):
    """Pine ta.sma ignores na and requires length nonmissing observations."""
    values = series.dropna().rolling(length, min_periods=length).mean()
    return values.reindex(series.index).ffill()


def _smma(series, length):
    """Match f_smma: SMA seed, then (previous*(length-1)+current)/length."""
    seed = _sma(series, length).to_numpy(dtype=float)
    values = series.to_numpy(dtype=float)
    result = np.full(len(series), np.nan)
    previous = NAN
    for i, value in enumerate(values):
        previous = seed[i] if math.isnan(previous) else (previous * (length - 1) + value) / length
        result[i] = previous
    return pd.Series(result, index=series.index)


def _ema(series, length):
    # With finite OHLC this exactly uses Pine's first observation, not an SMA seed.
    return series.ewm(alpha=2.0 / (length + 1), adjust=False, ignore_na=True).mean()


def features(ohlcv):
    """Compute frozen Pine fields from current/prior bars; return the same index.

    OHLC must be finite and valid. Volume may be missing (no fabricated zero or
    volume); Pine's prior median skips missing observations. Warmup is retained,
    including MD=0 before the high/low SMMA seed and ready=False through bar339.
    Column names match Pine. Original input columns are retained in the copy.
    """
    _validate_index(ohlcv)
    required = ["open", "high", "low", "close", "volume"]
    missing = set(required) - set(ohlcv.columns)
    if missing:
        raise ValueError("Missing OHLCV columns: " + ", ".join(sorted(missing)))
    frame = ohlcv.copy()
    frame[required] = frame[required].astype(float)
    prices = frame[["open", "high", "low", "close"]]
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError("OHLC prices must be positive and finite; segment missing bars upstream")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1))
            | (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC geometry")
    if np.isinf(frame.volume).any() or (frame.volume.dropna() < 0).any():
        raise ValueError("Volume must be nonnegative or missing")
    frame["bar_index"] = np.arange(len(frame), dtype=int)
    frame["smHigh"] = _smma(frame.high, MA_LEN)
    frame["smLow"] = _smma(frame.low, MA_LEN)
    ema1 = _ema((frame.high + frame.low + frame.close) / 3, MA_LEN)
    frame["middle"] = 2 * ema1 - _ema(ema1, MA_LEN)
    frame["md"] = np.where(pine_gt(frame.middle, frame.smHigh), frame.middle - frame.smHigh,
                           np.where(pine_lt(frame.middle, frame.smLow), frame.middle - frame.smLow, 0.0))
    frame["sb"] = _sma(frame.md, SIG_LEN)
    previous_close = frame.close.shift()
    # ta.tr(true) uses high-low when the preceding close is unavailable.
    frame["tr"] = pd.concat((frame.high - frame.low,
                              (frame.high - previous_close).abs(),
                              (frame.low - previous_close).abs()), axis=1).max(axis=1)
    frame["atr"] = _smma(frame.tr, 14)
    ma_columns = []
    for length in (20, 60, 120):
        s, e = "s%d" % length, "e%d" % length
        frame[s], frame[e] = _sma(frame.close, length), _ema(frame.close, length)
        ma_columns.extend((s, e))
    # Pine math.max/min propagate any na, unlike pandas' default skipna=True.
    frame["ropeHigh"] = frame[ma_columns].max(axis=1, skipna=False)
    frame["ropeLow"] = frame[ma_columns].min(axis=1, skipna=False)
    frame["width"] = ((frame.ropeHigh - frame.ropeLow) / frame.atr).where(pine_gt(frame.atr, 0))
    flips = pd.Series(0.0, index=frame.index)
    for a, b in combinations(ma_columns, 2):
        d = frame[a] - frame[b]
        flips += ((pine_gt(d, 0) & pine_le(d.shift(), 0)) | (pine_lt(d, 0) & pine_ge(d.shift(), 0))).astype(float)
    frame["flips"] = flips
    frame["pastWidth"] = _sma(frame.width.shift(), DENSE_LEN)
    frame["pastCrosses"] = flips.shift().rolling(DENSE_LEN, min_periods=DENSE_LEN).sum()
    prior_volume = frame.volume.shift()
    frame["pastVolume"] = (prior_volume.dropna().rolling(20, min_periods=20).median()
                           .reindex(frame.index).ffill())
    frame["rv"] = (frame.volume / frame.pastVolume).where(pine_gt(frame.pastVolume, 0))
    frame["expansion"] = (frame.tr / frame.atr.shift()).where(pine_gt(frame.atr.shift(), 0))
    frame["recentLow"] = frame.low.rolling(5, min_periods=1).min()
    frame["recentHigh"] = frame.high.rolling(5, min_periods=1).max()
    frame["ready"] = ((frame.bar_index >= max(340, 10 * MA_LEN))
                      & pine_gt(frame.atr.shift(), 0) & frame.sb.notna() & frame.pastWidth.notna())
    return frame


def price_burst(side, o, h, l, c, md, sb, mi, prior_mi, zone_high, zone_low,
                rope_high, rope_low, rv, expansion, vol_gate=MIN_VOLUME,
                tr_gate=MIN_EXPANSION, body_gate=MIN_BODY, end_gate=MIN_END):
    """Current force gates on a previously qualified/frozen box, before release."""
    span = h - l
    known = pine_gt(span, 0) and all(math.isfinite(x) for x in
                            (rv, expansion, prior_mi, rope_high, rope_low, zone_high, zone_low))
    if not known:
        return False
    directional = (pine_gt(c, o) and pine_gt(c, zone_high) and pine_gt(c, rope_high) and pine_ge(md, 0) and pine_ge(md, sb) and pine_gt(mi, prior_mi)
                   if side == 1 else pine_lt(c, o) and pine_lt(c, zone_low) and pine_lt(c, rope_low) and pine_le(md, 0) and pine_le(md, sb) and pine_lt(mi, prior_mi)
                   if side == -1 else False)
    end = (c - l) / span if side == 1 else (h - c) / span
    return bool(directional and pine_ge(rv, vol_gate) and pine_ge(expansion, tr_gate)
                and pine_ge(abs(c - o) / span, body_gate) and pine_ge(end, end_gate))


def burst(side, o, h, l, c, md, previous_md, sb, zone_high, zone_low,
          rope_high, rope_low, rv, expansion, vol_gate=MIN_VOLUME,
          tr_gate=MIN_EXPANSION, body_gate=MIN_BODY, end_gate=MIN_END):
    """Current force gates on a frozen release episode; prior MD is required."""
    span = h - l
    known = pine_gt(span, 0) and all(math.isfinite(x) for x in
                            (rv, expansion, previous_md, rope_high, rope_low, zone_high, zone_low))
    if not known:
        return False
    directional = (pine_gt(c, o) and pine_gt(c, zone_high) and pine_gt(c, rope_high) and pine_gt(md, 0) and pine_gt(md, sb) and pine_gt(md, previous_md)
                   if side == 1 else pine_lt(c, o) and pine_lt(c, zone_low) and pine_lt(c, rope_low) and pine_lt(md, 0) and pine_lt(md, sb) and pine_lt(md, previous_md)
                   if side == -1 else False)
    end = (c - l) / span if side == 1 else (h - c) / span
    return bool(directional and pine_ge(rv, vol_gate) and pine_ge(expansion, tr_gate)
                and pine_ge(abs(c - o) / span, body_gate) and pine_ge(end, end_gate))


def launch_window(age, count, side, md, band, c, zone_high, zone_low):
    """Release age zero is included; six-bar default stops at age six."""
    directional = pine_gt(md, band) and pine_ge(c, zone_low) if side == 1 else pine_lt(md, -band) and pine_le(c, zone_high) if side == -1 else False
    return bool(0 <= age < count and directional)


class RiskReference(NamedTuple):
    stop: float
    risk: float
    valid: bool


def risk_reference(side, entry, extreme, atr, floor_atr=RISK_FLOOR, buffer_atr=STOP_BUFFER, tick=None):
    """Frozen signal-close risk with outward price-tick rounding, exactly Pine."""
    # entry/atr are SERIES. tick originates at simple syminfo.mintick; native
    # input/simple comparisons use the observed1e-10 zero tolerance. Applying
    # either this tolerance or blanket round9 to series changes actual arrows.
    known = (side in (-1, 1) and pine_gt(entry, 0) and pine_gt(atr, 0) and tick is not None and tick > 1e-10
             and all(math.isfinite(v) for v in (entry, extreme, atr, tick)))
    if not known:
        return RiskReference(NAN, NAN, False)
    candidate = min(extreme - buffer_atr * atr, entry - floor_atr * atr) if side == 1 else max(extreme + buffer_atr * atr, entry + floor_atr * atr)
    stop = math.floor(candidate / tick) * tick if side == 1 else math.ceil(candidate / tick) * tick
    risk = side * (entry - stop)
    return RiskReference(stop, risk, True) if pine_gt(stop, 0) and pine_gt(risk, 0) else RiskReference(NAN, NAN, False)


class PathReference(NamedTuple):
    alive: bool
    protection: float
    peak_r: float
    current_r: float
    exit_price: float
    armed: bool


def path_reference(side, entry, risk, prior_protection, prior_peak, was_armed,
                   o, h, l, c, atr, activation_r=ARM_R, distance_atr=TRAIL_ATR, tick=None):
    """Apply previous protection first, then ratchet for NEXT bar if alive."""
    if tick is None or not math.isfinite(tick) or tick <= 0:
        raise ValueError("A positive finite exchange price tick is required")
    stopped = pine_le(l, prior_protection) if side == 1 else pine_ge(h, prior_protection)
    exit_price = (min(o, prior_protection) if side == 1 else max(o, prior_protection)) if stopped else NAN
    current_r = side * ((exit_price if stopped else c) - entry) / risk
    peak_r = prior_peak if stopped else max(prior_peak, side * ((h if side == 1 else l) - entry) / risk)
    armed = was_armed or (not stopped and pine_ge(current_r, activation_r))
    protection = prior_protection
    if not stopped and armed and pine_gt(atr, 0):
        raw = c - side * distance_atr * atr
        candidate = math.floor(raw / tick) * tick if side == 1 else math.ceil(raw / tick) * tick
        protection = max(prior_protection, candidate) if side == 1 else min(prior_protection, candidate)
    return PathReference(not stopped, protection, peak_r, current_r, exit_price, armed)


def replay(frame, tick):
    """Replay frozen default LONG-only Pine; input is ``features(ohlcv)`` output.

    ``burst``/``exit`` and ``route`` are single-bar events. ``initial_stop``,
    ``risk``, ``entry_ref``, ``current_r`` persist after an exit as Pine does.
    ``quiet_bars`` is the quiet count of the most recently consumed launch;
    ``quiet_count`` is the evolving current episode. ``protection`` is the value
    known AFTER this close; ``active_protection`` is what was operative DURING
    this bar (na on the signal bar), also preserved on the stop bar. No intrabar
    exits/reentries or signal-bar peak excursion are invented.
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
    result.attrs.update(frame.attrs)
    result.attrs.update(source_sha256=SOURCE_SHA256, direction="long", tick=float(tick),
                        price_semantics="signal-close references, not execution fills")
    return result
