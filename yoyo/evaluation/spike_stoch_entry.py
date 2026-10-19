"""Closed-bar Stoch entry timing under the frozen V13.1 risk/exit contract.

Owner 2026-10-19: compare original oversold Stoch5/3/3 crosses, same-bar WVF
green, and their use after a valid Spike pullback. All features use high/low/
close through the decision bar only. Spike stops stay at the original anchor;
the 24-bar deadline, raw opposite, stop-touch and lost-level cancellations stay.
Standalone means independent entry admission, not an independently tuned exit:
both standalone arms retain the same raw Spike opposite exits and risk engine.
"""
from __future__ import annotations
import math

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v131_entry_stages import parent, attempt
from yoyo.evaluation.spike_v131_excursions import trace

SPIKE_POLICIES = ('rebreak', 'pullback', 'pullback_stoch', 'pullback_wvf')
STOCH_POLICIES = ('stoch', 'stoch_wvf')


def timed_decision(p, anchor, stop, trigger, limit=24):
    """First trigger at/after a valid pullback, with cancellation priority.

    Reads OHLC, gap, raw_side and precomputed causal trigger through returned
    decision_i. Breakout must precede the touch bar; a trigger on that touch
    bar is allowed. A later failure cannot revoke an earlier decision.
    """
    level = p.high[anchor]
    stage = 0
    end = min(len(p.close)-1, anchor+limit)
    for j in range(anchor+1, end+1):
        if p.gap[j]: return 'cancel_gap', j
        if p.raw_side[j] == -1: return 'cancel_raw_opposite', j
        if p.low[j] <= stop: return 'cancel_stop_touch', j
        if stage and p.close[j] <= level: return 'cancel_lost_level', j
        if stage == 0:
            if p.close[j] > level: stage = 1
        else:
            if stage == 1 and p.low[j] <= level: stage = 2
            if stage == 2 and trigger[j]: return 'confirmed', j
    return ('expired' if anchor+limit < len(p.close) else 'pending_boundary'), end


def timed_attempt(p, anchor, policy, trigger):
    """Original absolute anchor stop; next-open execution and actual-entry R."""
    if policy not in ('pullback_stoch', 'pullback_wvf'):
        raise ValueError('unknown timed policy')
    out = dict(policy=policy, anchor_i=anchor, side=1, decision_i=anchor,
               status='no_next_bar', filled=False, net_r=None)
    if anchor+1 >= len(p.frame): return out
    if p.gap[anchor+1]: return out | dict(status='next_bar_is_gap', decision_i=anchor+1)
    initial = parent._initial_position_fast(p.frame.index, p.open, p.high, p.low,
        p.close, p.atr, p.gap, anchor, 1, p.spec)
    if initial is None: return out | dict(status='risk_invalid')
    stop = float(initial['initial_stop'])
    status, d = timed_decision(p, anchor, stop, trigger)
    out.update(status=status, decision_i=d, initial_stop=stop,
               anchor_entry=float(initial['entry_price']), anchor_risk=float(initial['initial_risk']))
    if status != 'confirmed': return out
    if d+1 >= len(p.frame): return out | dict(status='no_next_bar')
    if p.gap[d+1]: return out | dict(status='next_bar_is_gap', decision_i=d+1)
    entry = float(p.open[d+1]); risk = entry-stop
    if not math.isfinite(entry) or entry <= 0 or risk <= 0:
        return out | dict(status='delayed_risk_invalid')
    initial.update(signal_i=d, signal_bar_open=p.frame.index[d], entry_i=d+1,
        entry_time=p.frame.index[d+1], entry_price=entry, initial_risk=risk, initial_risk_frac=risk/entry)
    r = parent.fixed.replay_fixed_entry(p.context, pd.Series(initial), arm='v8', enable_be=False, prepared=p)
    status = 'closed' if not r['censored'] else ('censored_boundary' if r['exit_reason']=='boundary_mark' else 'censored_gap')
    out.update({k:r[k] for k in ('entry_i','exit_i','entry_price','initial_risk','exit_price','exit_reason','net_r','gross_return','net_return')})
    out.update(status=status, filled=True, gross_r=r['gross_return']*entry/risk,
        cost_r=.002*entry/risk, delay_bars=d-anchor,
        chase_r=(entry-out['anchor_entry'])/out['anchor_risk'])
    return out


def excursions(p, r):
    """Outcome-only MAE/MFE up to actual fill, with ambiguous stop-wick bounds.

    Uses OHLC/ATR from entry_i through exit_i, never as admission features.
    Every closed path is independently traced and reconciled with frozen exit.
    """
    if r['status'] != 'closed': return {}
    z = trace(side=1, entry=r['entry_price'], stop0=r['initial_stop'], tick=p.spec.tick,
        o=p.open, h=p.high, l=p.low, c=p.close, atr=p.atr,
        e=int(r['entry_i']), x=int(r['exit_i']),
        opposite=str(r['exit_reason']).startswith('opposite'), trail=p.spec.trail_atr)
    assert z['actual_exit_i'] == r['exit_i']
    assert np.isclose(z['actual_exit_price'], r['exit_price'], atol=1e-10, rtol=1e-10)
    net = (z['actual_exit_price']-r['entry_price']-.002*r['entry_price'])/r['initial_risk']
    assert np.isclose(net, r['net_r'], atol=1e-8, rtol=1e-10)
    ratio = r['initial_risk']/r['entry_price']
    return {k:z[k] for k in ('mae_r','mfe_lower_r','mfe_upper_r','peak_close_r','stop_bar_ambiguous','first_bar_mae_r')} | dict(
        mae_bp=z['mae_r']*ratio*10000, mfe_lower_bp=z['mfe_lower_r']*ratio*10000,
        mae_anchor_r=z['mae_r']*r['initial_risk']/r['anchor_risk'],
        held_bars=r['exit_i']-r['entry_i']+1,
        risk_fraction=ratio)
