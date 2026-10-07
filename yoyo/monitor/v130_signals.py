"""Notification-only V13.1 ordinary retest replay on completed 15m/30m/1H/4H bars.

The candidate set and frozen five-bar/ATR stop come from the V12.8 adapter of the
same timeframe (15m keeps its completed-H1 SMA60 gate). Confirmation uses the
already-tested research state machine; position timing follows
spike_burst_v13_1.pine: next-open reference, entry-bar stop, raw opposite
next-open exit, 2R close arming and a next-bar close trail of 2ATR on 1H and
4ATR elsewhere (owner 2026-10-07, exp-spike-exit-giveback-20261007-v1). The
owner moved the signal center from V12.8 to V13.1 on the same day. Only supplied
closed OHLC/ATR/raw-side prefixes are read. No broker orders or developing candles.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

from yoyo.evaluation.spike_v128_retest_state import confirmation
from yoyo.monitor import TIMEFRAMES
from yoyo.monitor.signals import AnalysisResult
from yoyo.monitor.v130_policy import trail_atr

SIGNAL_PROTOCOL = 'spike-burst-v130-retest-monitor-v1'
SIGNAL_KIND = 'spike_burst_v130_retest'
STRATEGY_VERSION = 'spike-v13.1-retest-monitor-20261007-v1'
STEP = TIMEFRAMES['15m']
WAIT = 24
COST = .002
BASIS = 'v130_next_open_serial_reference_net_20bp_not_account'
PINE = Path(__file__).resolve().parents[1] / 'evaluation/pine/spike_burst_v13_1.pine'
SOURCE_SHA256 = hashlib.sha256(PINE.read_bytes()).hexdigest()


def _unknown(reason):
    return dict(status='unknown', basis=BASIS, reason=reason, current_r=None,
                exit_r=None, entry_price=None, entry_time_ms=None, round_trip_cost=COST)


def replay(built, raw_side, anchors, *, tick, timeframe='15m'):
    """Advance reference occupancy in bar order; decisions never require a next bar.

    Input columns: open/high/low/close/atr, data_gap attr, raw_side at each
    closed bar, and original anchor initial_stop. A signal at the final supplied
    close is emitted immediately with an unknown next-open reference. Future
    performance changes cannot retroactively create or remove that signal.
    """
    if not math.isfinite(float(tick)) or tick <= 0:
        raise ValueError('invalid tick')
    step, trail = TIMEFRAMES.get(timeframe), trail_atr(timeframe)
    size = len(built)
    if not size:
        return [], dict(waiting=0, confirmations=0, occupied_skips=0, canceled=0)
    times = built.index.asi8 // 1_000_000
    if np.any(times % step) or np.any(np.diff(times) <= 0):
        raise ValueError(f'V13 requires ordered {timeframe} opens')
    o, h, low, close, atr = [built[k].to_numpy(float) for k in ['open', 'high', 'low', 'close', 'atr']]
    gap = np.asarray(built.attrs['data_gap'], dtype=bool)
    by_close = {int(t) + step: i for i, t in enumerate(times)}
    confirmations = {}
    waiting = canceled = 0
    for anchor in sorted(anchors, key=lambda e: e['bar_close_ms']):
        i = by_close[anchor['bar_close_ms']]
        side = 1 if anchor['side'] == 'long' else -1
        stop = float(anchor['initial_stop'])
        if i + 1 < size and (gap[i + 1] or o[i + 1] <= 0 or side * (o[i + 1] - stop) <= 0):
            canceled += 1
            continue
        trace = confirmation(o, h, low, close, gap, raw_side, i, side, stop, WAIT)
        if trace['status'] == 'confirmed':
            confirmations.setdefault(trace['confirmation_i'], []).append((anchor, trace, side, stop))
        elif trace['status'] == 'pending_boundary':
            waiting += 1
        else:
            canceled += 1
    events = []
    scheduled = position = None
    skipped = 0
    for j in range(size):
        if gap[j]:
            if position:
                position['event']['performance'] = _unknown('data_gap_censored')
            if scheduled:
                scheduled['performance'] = _unknown('next_bar_is_gap')
            position = scheduled = None
            continue
        exit_price, exit_reason = None, None
        if position and position['reverse_pending']:
            exit_price, exit_reason = o[j], 'raw_opposite_next_open'
        if scheduled:
            risk = (1 if scheduled['side'] == 'long' else -1) * (o[j] - scheduled['initial_stop'])
            if position is None and o[j] > 0 and risk > 0:
                position = dict(event=scheduled, side=1 if scheduled['side'] == 'long' else -1,
                                entry=float(o[j]), entry_i=j, risk=float(risk), protection=scheduled['initial_stop'],
                                peak=0., armed=False, reverse_pending=False)
            else:
                scheduled['performance'] = _unknown('delayed_risk_invalid')
            scheduled = None
        if position:
            p = position
            side, protection = p['side'], p['protection']
            if exit_price is None:
                if low[j] <= protection if side == 1 else h[j] >= protection:
                    exit_price = min(o[j], protection) if side == 1 else max(o[j], protection)
                    exit_reason = 'trailing_stop' if p['armed'] else 'initial_stop'
                else:
                    p['peak'] = max(p['peak'], side * ((h[j] if side == 1 else low[j]) - p['entry']) / p['risk'])
                    close_r = side * (close[j] - p['entry']) / p['risk']
                    p['armed'] = p['armed'] or close_r >= 2.
                    if p['armed'] and np.isfinite(atr[j]) and atr[j] > 0:
                        raw = close[j] - side * trail * atr[j]
                        next_stop = (math.floor(raw / tick) if side == 1 else math.ceil(raw / tick)) * tick
                        p['protection'] = max(protection, next_stop) if side == 1 else min(protection, next_stop)
                    p['reverse_pending'] = raw_side[j] == -side
            mark = close[j] if exit_price is None else exit_price
            net_r = float((side * (mark / p['entry'] - 1.) - COST) * p['entry'] / p['risk'])
            p['event']['performance'] = dict(
                status='active' if exit_price is None else 'profit' if net_r > 1e-9 else 'loss' if net_r < -1e-9 else 'breakeven',
                basis=BASIS, current_r=net_r, net_r=net_r, exit_r=None if exit_price is None else net_r,
                peak_r=float(p['peak']), entry_price=p['entry'], entry_time_ms=int(times[p['entry_i']]),
                initial_stop=p['event']['initial_stop'], initial_risk=p['risk'], stop_price=float(p['protection']),
                stop_triggered=exit_reason in ('initial_stop', 'trailing_stop'), trailing_active=bool(p['armed']),
                bars_held=j-p['entry_i'], exit_reason=exit_reason, exit_price=None if exit_price is None else float(exit_price),
                exit_time_ms=None if exit_price is None else int(times[j]) + step,
                updated_at_ms=int(times[j]) + step, round_trip_cost=COST, trail_atr=trail,
                mark='last_closed_bar_close' if exit_price is None else None)
            if exit_price is not None:
                position = None
        for anchor, trace, side, stop in confirmations.get(j, []):
            if position is not None or scheduled is not None:
                skipped += 1
                continue
            risk = side * (close[j] - stop)
            event = dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, strategy_version=STRATEGY_VERSION,
                         source='live', confirmation='retest', v130_admitted=True, side=anchor['side'], direction=anchor['side'],
                         timeframe=timeframe, timeframe_min=step // 60_000, bar_open_ms=int(times[j]),
                         bar_close_ms=int(times[j])+step, signal_close_time=int(times[j])+step,
                         confirmed=True, is_closed=True, ready=True, trail_atr=trail,
                         price=float(close[j]), risk=float(risk), initial_stop=stop, reference_price=float(close[j]),
                         reference_initial_risk=float(risk), reference_initial_stop=stop,
                         anchor_close_ms=anchor['bar_close_ms'], breakout_close_ms=int(times[trace['breakout_i']])+step,
                         retest_close_ms=int(times[trace['retest_i']])+step, wait_bars=j-trace['anchor_i'],
                         level=trace['level'], first_leg_extreme=trace['first_leg_extreme'],
                         candidate_source='ordinary_both', source_sha256=SOURCE_SHA256,
                         parent_source_sha256=anchor.get('source_sha256'), base_asset=anchor.get('base_asset'),
                         h1_sma60=anchor.get('h1_sma60'), entry_reference='next_open_reference_not_fill',
                         executable_entry_time=None, is_trade=False, performance=_unknown('awaiting_next_closed_bar'))
            events.append(event)
            scheduled = event
    return events, dict(waiting=waiting, confirmations=len(events), occupied_skips=skipped, canceled=canceled,
                        last_confirmation_ms=events[-1]['bar_close_ms'] if events else None,
                        reference_active=position is not None, timeframe=timeframe, protocol=SIGNAL_PROTOCOL,
                        candidate_source='ordinary_both', max_wait_bars=WAIT)


def from_parent(parent_result, context, *, tick, timeframe='15m'):
    """Reuse the already-computed V12.8 prefix of the same monitored timeframe."""
    if timeframe not in TIMEFRAMES:
        raise ValueError('V13.1 monitors only 15m/30m/1H/4H')
    if not context:
        return AnalysisResult(dict(events=[], chart=[], state=dict(phase='loading', ready=False, timeframe=timeframe, protocol=SIGNAL_PROTOCOL)))
    events, state = replay(context['built'], context['evidence'].side.to_numpy(int), parent_result['events'],
                           tick=tick, timeframe=timeframe)
    confirmations = {e['bar_open_ms']: e for e in events}
    chart = [dict(row, v130_burst=row['t'] in confirmations,
                  v130_side=confirmations[row['t']]['side'] if row['t'] in confirmations else None)
             for row in parent_result['chart']]
    state.update(phase=parent_result['state']['phase'], ready=parent_result['state']['ready'])
    return AnalysisResult(dict(events=events, chart=chart, state=state))
