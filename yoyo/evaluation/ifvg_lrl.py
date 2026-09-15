"""Causal native-timeframe iFVG + LRL research, not an authenticated author clone.

Source: https://discord.com/channels/1106285657879478303/1524851577817661490/1546273453005607002
FVG uses current and previous two closed OHLC bars. Strict pivots use two bars
on either side and become visible only at the second right bar's close. LRL
uses the latest three unswept pivots within 60 minutes, endpoint interpolation,
and middle residual <= 10% of the last 30 closed bars' high-low range. Stops
use the latest confirmed opposite pivot, one tick outside. These mechanical
choices fill omissions in the post and are frozen before price evaluation.
Only a native timeframe is implemented: no invented intrabars or HTF claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

import numpy as np
import pandas as pd

from yoyo.data.release_eth_prefix import validate_ohlcv


@dataclass(frozen=True)
class Config:
    minutes: int = 3
    tick: float = .01
    context_minutes: int = 60
    pivot_wing: int = 2
    range_bars: int = 30
    line_tolerance: float = .10
    roundtrip_cost: float = .002
    session_only: bool = True


def _arrays(frame, cfg):
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError('timezone-aware datetime index required')
    validate_ohlcv(frame.rename_axis('open_time').reset_index(), cfg.minutes)
    if cfg.tick <= 0 or cfg.minutes <= 0 or cfg.pivot_wing < 1:
        raise ValueError('invalid configuration')
    return [frame[k].to_numpy(float) for k in ('open', 'high', 'low', 'close')]


def _lrl(pivots, side, i, close, tol, maxbars):
    """Use exactly the last three eligible points, never search winning triples."""
    eligible = [p for p in pivots if i-p[0] <= maxbars]
    if len(eligible) < 3:
        return False, np.nan, np.nan, []
    pts = eligible[-3:]
    (a, pa, _), (b, pb, _), (d, pd_, _) = pts
    residual = abs(pb-(pa+(pd_-pa)*(b-a)/(d-a)))
    ordered = pd_ <= pa if side == 1 else pd_ >= pa
    ahead = all(side*(p[1]-close) > 0 for p in pts)
    ok = ordered and ahead and residual <= tol
    target = min(p[1] for p in pts) if side == 1 else max(p[1] for p in pts)
    return bool(ok), float(target), float(residual), pts


def generate(frame, cfg=Config()):
    """Emit immutable inversion events and per-close control context, using no future."""
    o, h, l, c = _arrays(frame, cfg)
    n = len(frame); w = cfg.pivot_wing
    maxbars = cfg.context_minutes // cfg.minutes
    clock = frame.index + pd.Timedelta(minutes=cfg.minutes)
    ny = clock.tz_convert('America/New_York')
    minute = ny.hour*60+ny.minute
    session = ((minute >= 570) & (minute < 600) & (ny.weekday < 5)) if cfg.session_only else np.ones(n, bool)
    span = (frame.high.rolling(cfg.range_bars, min_periods=cfg.range_bars).max()-frame.low.rolling(cfg.range_bars, min_periods=cfg.range_bars).min()).to_numpy()
    highs, lows, zones, events = [], [], {}, []
    last_high = last_low = None
    context = np.full((n, 5), np.nan)
    for i in range(n):
        # Sweeps are permanent; only confirmed pivots can enter the pool.
        highs = [p for p in highs if h[i] <= p[1] and i-p[0] <= maxbars]
        lows = [p for p in lows if l[i] >= p[1] and i-p[0] <= maxbars]
        if i >= 2*w:
            j = i-w
            if h[j] > max(np.max(h[j-w:j]), np.max(h[j+1:i+1])):
                last_high = (j, h[j], i); highs.append(last_high)
            if l[j] < min(np.min(l[j-w:j]), np.min(l[j+1:i+1])):
                last_low = (j, l[j], i); lows.append(last_low)
        stop_long = np.nan if last_low is None else last_low[1]-cfg.tick
        stop_short = np.nan if last_high is None else last_high[1]+cfg.tick
        context[i] = [span[i]/c[i], stop_long, stop_short, float(session[i]), span[i]*cfg.line_tolerance]
        inversions = []
        for bias, zone in list(zones.items()):
            born, bottom, top = zone
            if i-born > maxbars:
                del zones[bias]
            elif (bias == 1 and c[i] < bottom) or (bias == -1 and c[i] > top):
                inversions.append((-bias, born, bottom, top)); del zones[bias]
        for side, born, bottom, top in inversions:
            pool = highs if side == 1 else lows
            ok, target, residual, pts = _lrl(pool, side, i, c[i], span[i]*cfg.line_tolerance, maxbars)
            stop = stop_long if side == 1 else stop_short
            swing = last_low if side == 1 else last_high
            risk_ok = bool(np.isfinite(stop) and stop > 0 and side*(c[i]-stop) > cfg.tick)
            reason = 'ambiguous_direction' if len(inversions)>1 else 'outside_session' if not session[i] else 'invalid_stop' if not risk_ok else 'candidate'
            row = dict(event_id=f'{clock[i].isoformat()}|{side}|{born}', signal_i=i,
                signal_time=clock[i], side=side, zone_born_i=born, zone_low=bottom,
                zone_high=top, signal_close=c[i], stop=stop, has_lrl=ok,
                lrl_target=target, lrl_residual=residual, lrl_tolerance=span[i]*cfg.line_tolerance,
                swing_i=None if swing is None else swing[0],
                swing_confirm_i=None if swing is None else swing[2], reason=reason,
                range_fraction=span[i]/c[i])
            for k in range(3):
                p = pts[k] if len(pts)==3 else (None, np.nan, None)
                row.update({f'pivot{k+1}_i':p[0], f'pivot{k+1}_price':p[1], f'pivot{k+1}_confirm_i':p[2]})
            events.append(row)
        # A newly formed zone cannot invert on its own formation bar.
        if i >= 2:
            if l[i]-h[i-2] >= cfg.tick:
                zones[1] = (i, h[i-2], l[i])
            if l[i-2]-h[i] >= cfg.tick:
                zones[-1] = (i, h[i], l[i-2])
    columns = ['event_id','signal_i','signal_time','side','zone_born_i','zone_low','zone_high','signal_close','stop','has_lrl','lrl_target','lrl_residual','lrl_tolerance','swing_i','swing_confirm_i','reason','range_fraction']
    event_frame = pd.DataFrame(events) if events else pd.DataFrame(columns=columns)
    ctx = pd.DataFrame(context, index=frame.index, columns=['range_fraction','stop_long','stop_short','session','lrl_tolerance'])
    return event_frame, ctx


def resolve(frame, signal_i, side, stop, cfg=Config(), *, end_i=None):
    """Next-open entry, fixed structural stop/1R TP; conservative dual touch.

    OHLC at/after entry is used ONLY for outcomes. No terminal close is invented:
    unresolved trades are censored, with gross/net outcomes left missing.
    """
    end = len(frame) if end_i is None else min(len(frame), end_i)
    entry_i = int(signal_i)+1
    if entry_i >= end:
        return None
    entry = float(frame.open.iloc[entry_i]); risk = side*(entry-stop)
    if not np.isfinite(stop) or stop <= 0 or risk <= cfg.tick:
        return None
    target = entry+side*risk
    row = dict(signal_i=int(signal_i), entry_i=entry_i, entry_time=frame.index[entry_i],
               side=side, entry_price=entry, initial_stop=stop, initial_risk=risk,
               risk_fraction=risk/entry, target=target, censored=True, exit_i=end-1,
               exit_time=frame.index[end-1]+pd.Timedelta(minutes=cfg.minutes),
               exit_price=np.nan, exit_reason='censored', gross_return=np.nan,
               net_return=np.nan, gross_r=np.nan, net_r=np.nan, cost_r=cfg.roundtrip_cost*entry/risk)
    opens, highs, lows = [frame[k].to_numpy(copy=False) for k in ('open','high','low')]
    idx = None
    # Bounded slices avoid copying years of future OHLC for a short-lived trade.
    for base in range(entry_i, end, 256):
        h, l = highs[base:min(base+256,end)], lows[base:min(base+256,end)]
        st = l <= stop if side==1 else h >= stop
        tp = h >= target if side==1 else l <= target
        hits = np.flatnonzero(st | tp)
        if len(hits):
            j = int(hits[0]); idx = base+j
            break
    if idx is None:
        return row
    if st[j]:
        price = min(opens[idx], stop) if side==1 else max(opens[idx], stop)
        reason = 'sl_ambiguous' if tp[j] else 'sl'
    else:
        price, reason = target, 'tp'
    gross = side*(price-entry)/entry
    row.update(censored=False, exit_i=idx, exit_time=frame.index[idx]+pd.Timedelta(minutes=cfg.minutes),
               exit_price=price, exit_reason=reason, gross_return=gross,
               net_return=gross-cfg.roundtrip_cost, gross_r=gross/(risk/entry),
               net_r=(gross-cfg.roundtrip_cost)/(risk/entry))
    return row


def replay(frame, events, cfg=Config(), *, use_lrl=True, serial=True, start=None, end=None):
    """Replay fixed entries with a pure admission switch and immutable skips."""
    start_t = frame.index[0] if start is None else pd.Timestamp(start)
    end_t = frame.index[-1]+pd.Timedelta(minutes=cfg.minutes) if end is None else pd.Timestamp(end)
    end_i = int(frame.index.searchsorted(end_t))
    trades, rejects, available = [], [], -1
    for e in events.itertuples(index=False):
        if e.reason != 'candidate' or not start_t <= e.signal_time < end_t:
            continue
        reason = None
        if use_lrl and not e.has_lrl:
            reason = 'no_lrl'
        elif serial and e.signal_i < available:
            reason = 'position_busy'
        elif e.signal_i+1 >= end_i:
            reason = 'no_next_open'
        elif use_lrl and e.side*(e.lrl_target-float(frame.open.iloc[e.signal_i+1])) <= 0:
            reason = 'target_passed_at_open'
        if reason:
            rejects.append(dict(event_id=e.event_id, reason=reason)); continue
        trade = resolve(frame, e.signal_i, e.side, e.stop, cfg, end_i=end_i)
        if trade is None:
            rejects.append(dict(event_id=e.event_id, reason='invalid_next_open_risk')); continue
        trade.update(event_id=e.event_id, has_lrl=e.has_lrl, lrl_target=e.lrl_target,
                     lrl_residual=e.lrl_residual, range_fraction=e.range_fraction)
        trades.append(trade)
        if serial:
            available = trade['exit_i']
    return pd.DataFrame(trades), pd.DataFrame(rejects, columns=['event_id','reason'])


def matched_controls(frame, trades, context, cfg=Config(), *, start, end, seed=91537):
    """One predeclared draw per target: same date/30m NY block/causal range bin.

    The target's relative risk and 1R obstacle are transported to the random
    entry. Empty/unresolved matches remain missing, without outcome retries.
    Controls are paired event comparisons, not a tradable serial portfolio.
    """
    if trades.empty:
        return pd.DataFrame()
    clocks = frame.index+pd.Timedelta(minutes=cfg.minutes)
    ny = clocks.tz_convert('America/New_York')
    bins = np.searchsorted([.001,.002,.004,.008,.016], context.range_fraction.to_numpy())
    keys = list(zip(ny.strftime('%Y-%m-%d'), (ny.hour*60+ny.minute)//30, bins))
    groups = {}
    start_t, end_t = pd.Timestamp(start), pd.Timestamp(end)
    valid = (clocks >= start_t) & (clocks < end_t) & np.isfinite(context.range_fraction.to_numpy())
    for i, key in enumerate(keys):
        if valid[i] and i+1<len(frame):
            groups.setdefault(key, []).append(i)
    rows = []; end_i=int(frame.index.searchsorted(pd.Timestamp(end)))
    for t in trades.itertuples(index=False):
        choices = [i for i in groups.get(keys[t.signal_i], []) if i != t.signal_i]
        row = dict(event_id=t.event_id, signal_i=t.signal_i, target_net_r=t.net_r,
                   target_censored=t.censored, matched=False, reason='empty_stratum', control_net_r=np.nan,
                   control_signal_i=np.nan, block=keys[t.signal_i][0],
                   half_hour=int(keys[t.signal_i][1]), range_bin=int(keys[t.signal_i][2]))
        if choices:
            key = int(hashlib.sha256(f'{seed}|{t.event_id}'.encode()).hexdigest(),16)
            j=choices[key % len(choices)]; entry=float(frame.open.iloc[j+1])
            result=resolve(frame,j,t.side,entry*(1-t.side*t.risk_fraction),cfg,end_i=end_i)
            row.update(control_signal_i=j, reason='invalid_or_censored')
            if result is not None:
                row.update({f'control_{key}': value for key,value in result.items()})
            if result is not None and not result['censored'] and not t.censored:
                row.update(matched=True, reason='matched', control_net_r=result['net_r'])
        rows.append(row)
    return pd.DataFrame(rows)


def metrics(trades):
    """Closed-trade fixed-risk metrics; never silently turn censoring into loss."""
    if trades.empty:
        return dict(n=0,censored=0,wins=0,win_rate=None,gross_r=0.,net_r=0.,mean_net_r=None,pf=None,max_dd_r=0.,longest_loss=0,cost_r=0.)
    closed=trades.loc[~trades.censored]; r=closed.net_r.to_numpy(float)
    profit=r[r>0].sum(); loss=-r[r<0].sum()
    curve=np.r_[0,np.cumsum(r)]; dd=np.maximum.accumulate(curve)-curve
    streak=longest=0
    for x in r:
        streak=streak+1 if x<0 else 0; longest=max(longest,streak)
    return dict(n=len(closed),censored=int(trades.censored.sum()),wins=int((r>0).sum()),
        win_rate=None if not len(r) else float((r>0).mean()),gross_r=float(closed.gross_r.sum()),
        net_r=float(r.sum()),mean_net_r=None if not len(r) else float(r.mean()),
        pf=None if loss==0 else float(profit/loss),max_dd_r=float(dd.max()),longest_loss=longest,
        cost_r=float(closed.cost_r.sum()))
