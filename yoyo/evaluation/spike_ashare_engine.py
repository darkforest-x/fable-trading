"""Calendar-aware, long-only A-share adaptation of frozen SPIKE V1/V8.

Signal math comes from spike_burst_replay and the hash-checked spike_v7_fast
oracle. Features use current/prior observed OHLCV only: V1 340-bar warmup;
V8 BB200, preceding 500 widths, preceding 12-bar squeeze run, and current
rope distance <=3 ATR. A regular ordinal clock is used ONLY inside those
bar-count state machines; every order uses the actual exchange calendar.
Execution retains the fixed 5-bar/.2ATR/2ATR, 2R/4ATR and .2% cost contract,
with A-share T+1 and raw-price limit constraints. This is an independent
per-security event study, not a shared-capital or dividend-delivery ledger.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import math

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features, replay as v1_replay
from yoyo.evaluation.spike_v7_fast import v6_signals, v7_diagnostics

TRADE_COLUMNS = ['code','version','timeframe','signal_date','entry_date','exit_date',
                 'entry_price','initial_stop','initial_risk','exit_price','gross_return',
                 'net_return','gross_r','net_r','mfe_r','censored','exit_reason','score',
                 'vol_bucket','stale_days']


def sessions(calendar):
    return sorted(calendar.loc[calendar.is_trading_day.astype(str).eq('1'), 'calendar_date'].astype(str))


def prepare_cycles(daily, calendar, timeframe, end):
    """Aggregate completed real trading weeks without inserting price rows."""
    if timeframe not in ('1D', '1W'):
        raise ValueError('only daily/weekly studies are registered')
    data = daily.copy()
    data['date'] = data.date.astype(str)
    if data.date.duplicated().any() or not data.date.is_monotonic_increasing:
        raise ValueError('daily source is not unique and increasing')
    data = data.loc[data.date.le(end) & data.tradestatus.astype(str).eq('1')
                    & pd.to_numeric(data.volume).gt(0)].copy()
    if not len(data):
        return pd.DataFrame()
    if timeframe == '1D':
        return data[['date','open','high','low','close','volume','factor']].rename(columns={'date':'decision_date','factor':'price_factor'}).reset_index(drop=True)
    data['week'] = pd.to_datetime(data.date).dt.to_period('W-FRI').astype(str)
    cal = pd.DataFrame({'date': sessions(calendar)})
    cal['week'] = pd.to_datetime(cal.date).dt.to_period('W-FRI').astype(str)
    # Provider calendar includes all dates through end. If end is midweek,
    # that terminal week is excluded rather than falsely labelled complete.
    final_week = str(pd.Timestamp(end).to_period('W-FRI'))
    calendar_last = cal.groupby('week').date.max().to_dict()
    bars = data.groupby('week', sort=True).agg(open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last'), volume=('volume','sum'), last_observed=('date','last'),price_factor=('factor','last'))
    bars['decision_date'] = [calendar_last[w] for w in bars.index]
    if pd.Timestamp(end).weekday() < 4:
        bars = bars.loc[bars.index != final_week]
    # A suspended Friday can still close the week's information on Friday;
    # the observed Thursday close remains explicitly the last price.
    return bars.reset_index(drop=True)


def build_signals(daily, calendar, timeframe, start, end):
    """Run frozen signals on observed-bar ordinals, then restore real dates.

    Volatility bucket uses ATR/close against the preceding 252 observed
    cycle values' quartiles (minimum 60), never a full-period rank.
    """
    cycles = prepare_cycles(daily, calendar, timeframe, end)
    if not len(cycles):
        return cycles
    dates = cycles.decision_date.to_numpy()
    source = cycles[['open','high','low','close','volume']].copy()
    source.index = pd.date_range('2000-01-01', periods=len(source), freq='D', tz='UTC')
    bars = features(source)
    original = v1_replay(bars, tick=.01, price_ticks=pd.Series(.01*cycles.price_factor.to_numpy(),index=bars.index))
    raw = v6_signals(bars, 1440)
    bb = v7_diagnostics(bars, data_gap=pd.Series(False, index=bars.index))
    bars['ready_v1'] = bars.ready.astype(bool)
    bars['ready_v8'] = bb.v7_ready.astype(bool)
    bars['long_v1'] = original.burst.astype(bool)
    bars['long_v8'] = (raw.long_signal & bb.v7_ready & bb.prior_squeeze_run3
                       & ((bars.close-bars.ropeHigh)/bars.atr).le(3.0)).fillna(False)
    bars['short_v8'] = raw.short_signal.astype(bool)
    bars['stop_base'] = np.minimum(bars.low.rolling(5, min_periods=5).min()-.2*bars.atr,
                                   bars.close-2*bars.atr)
    bars['score'] = bars.rv
    volatility = bars.atr/bars.close
    prior = volatility.shift().rolling(252, min_periods=60)
    bars['vol_bucket'] = sum(volatility.gt(prior.quantile(q)).astype(int) for q in (.25,.5,.75))
    bars['decision_date'] = dates
    bars['price_factor'] = cycles.price_factor.to_numpy()
    bars['timeframe'] = timeframe
    bars['in_window'] = bars.decision_date.ge(start) & bars.decision_date.le(end)
    return bars.reset_index(drop=True)


def price_limits(row):
    """Raw CNY tick limits with the 2026-07-06 main-board ST rule change."""
    fraction = .05 if str(row['isST']) in ('1','1.0') and str(row['date']) < '2026-07-06' else .10
    prev = Decimal(str(row['raw_preclose']))
    def rounded(multiplier):
        return float((prev * Decimal(str(multiplier))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    return rounded(1-fraction), rounded(1+fraction)


def _le(a, b):
    return a <= b + max(abs(a), abs(b), 1.0)*1e-12


def _floor_tick(value):
    ticks = value/.01
    if abs(ticks-round(ticks)) <= max(abs(ticks),1)*1e-12:
        ticks = round(ticks)
    return math.floor(ticks)*.01


def replay(daily, calendar, cycles, version, start, end, admission_override=None):
    """Serial per-stock next-session fills, daily stops, cycle-close trailing.

    Every feature is known at its decision close. The next calendar session
    is mandatory for entry: suspension expires that entry instruction. Exit
    instructions persist through suspension/limit-down. Weekly T+1 refers
    to actual daily sessions, not to the following weekly bar.
    """
    if version not in ('v1','v8'):
        raise ValueError('unregistered version')
    if not daily.date.map(lambda value: isinstance(value,str) and len(value)==10).all():
        raise ValueError('daily dates require explicit YYYY-MM-DD strings')
    if cycles.empty:
        return dict(trades=pd.DataFrame(columns=TRADE_COLUMNS), signals=0, skips={})
    code = str(daily.iloc[0].code)
    timeframe = str(cycles.iloc[0].timeframe)
    active = cycles.in_window.astype(bool) & cycles[f'ready_{version}'].astype(bool)
    admitted = cycles[f'long_{version}'].astype(bool) if admission_override is None else pd.Series(admission_override, index=cycles.index).astype(bool)
    admitted &= active
    by_close = {r['decision_date']: r for r in cycles.to_dict('records')}
    decisions = dict(zip(cycles.decision_date, admitted))
    rows = {str(r['date']): r for r in daily.to_dict('records')}
    dates = [d for d in sessions(calendar) if start <= d <= end]
    position = None
    pending_entry = None
    trades, skips = [], {}
    last_row = None

    def skip(reason):
        skips[reason] = skips.get(reason,0)+1

    def close(row, price, reason, censored=False):
        nonlocal position
        gross = price/position['entry_price']-1
        risk_frac = position['initial_risk']/position['entry_price']
        trades.append({k:position[k] for k in ('code','version','timeframe','signal_date',
                       'entry_date','entry_price','initial_stop','initial_risk','score','vol_bucket')} |
                      dict(exit_date=end if censored else str(row['date']), exit_price=price,
                           gross_return=gross, net_return=gross-.002, gross_r=gross/risk_frac,
                           net_r=(gross-.002)/risk_frac, mfe_r=position['mfe_r'], censored=censored,
                           exit_reason=reason, stale_days=(pd.Timestamp(end)-pd.Timestamp(row['date'])).days if censored else 0))
        position = None

    for ordinal, date in enumerate(dates):
        row = rows.get(date)
        tradable = (row is not None and str(row['tradestatus']) in ('1','1.0')
                    and float(row['volume']) > 0)
        if tradable:
            last_row = row
            lower, upper = price_limits(row)
            open_blocked = float(row['raw_open']) <= lower+.005
            if position and position['pending_exit'] and ordinal > position['entry_session']:
                if open_blocked:
                    skip('exit_limit_down')
                else:
                    reason=position['pending_exit']
                    if reason=='opposite_v6_next_open' and _le(float(row['open']),position['protection']):
                        reason=('trailing_stop' if position['protection']>position['initial_stop'] else 'initial_stop')+'_gap'
                    close(row, float(row['open']), reason)
        if pending_entry is not None:
            signal = pending_entry
            pending_entry = None
            if position:
                skip('already_in_position')
            elif not tradable:
                skip('entry_suspended_or_missing')
            elif str(row['isST']) not in ('0','0.0'):
                skip('entry_st')
            elif float(row['raw_open']) >= upper-.005:
                skip('entry_limit_up')
            else:
                factor = float(row['factor'])
                entry = float(row['open'])
                stop = _floor_tick(float(signal['stop_base'])/factor)*factor
                if not all(math.isfinite(v) for v in (stop,entry)) or stop <= 0 or _le(entry,stop):
                    skip('invalid_gap_risk')
                else:
                    position = dict(code=code,version=version,timeframe=timeframe,
                        signal_date=signal['decision_date'],entry_date=date,entry_price=entry,
                        initial_stop=stop,initial_risk=entry-stop,protection=stop,mfe_r=0.0,
                        armed=False,pending_exit='',entry_session=ordinal,
                        score=signal['score'],vol_bucket=signal['vol_bucket'])
        if position and tradable:
            protection = position['protection']
            touched = _le(float(row['low']), protection)
            if touched and not position['pending_exit']:
                reason = 'trailing_stop' if protection > position['initial_stop'] else 'initial_stop'
                if ordinal == position['entry_session']:
                    position['pending_exit'] = 'entry_day_stop_T1'
                    skip('entry_day_stop_T1')
                elif open_blocked or protection/float(row['factor']) <= lower+.005:
                    position['pending_exit'] = reason+'_limit_down'
                    skip('stop_limit_down')
                else:
                    fill = min(float(row['open']), protection)
                    close(row, fill, reason+('_gap' if fill < protection else ''))
            if position and not touched and not position['pending_exit']:
                position['mfe_r'] = max(position['mfe_r'],(float(row['high'])-position['entry_price'])/position['initial_risk'])
        cycle = by_close.get(date)
        if cycle is not None:
            if position and not position['pending_exit']:
                current_r = (float(cycle['close'])-position['entry_price'])/position['initial_risk']
                position['armed'] = position['armed'] or current_r >= 2
                if position['armed']:
                    factor = float(cycle.get('price_factor',last_row['factor']))
                    candidate = _floor_tick((float(cycle['close'])-4*float(cycle['atr']))/factor)*factor
                    position['protection'] = max(position['protection'],candidate)
                if version == 'v8' and bool(cycle['short_v8']):
                    position['pending_exit'] = 'opposite_v6_next_open'
            if decisions.get(date, False):
                if position:
                    skip('signal_while_in_position')
                else:
                    pending_entry = cycle
    if pending_entry:
        skip('no_next_session_in_window')
    if position:
        if last_row is None:
            raise ValueError('open position has no sourced terminal price')
        close(last_row,float(last_row['close']),'period_end_mark',True)
    return dict(trades=pd.DataFrame(trades,columns=TRADE_COLUMNS),
                signals=int(admitted.sum()),skips=skips)
