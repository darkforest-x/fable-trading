"""Compare one 15m versus one H1 reclaim on frozen monthly upper touches.

The first-touch event and its NY first-week range come from the existing H1
event ledger. A recorded event is admitted only when a 15m high literally
touches that frozen level inside its recorded touch hour. Both arms fill and
exit on the same local OKX 15m bars. The H1 arm uses its original cached H1
close/cancellation stream; the 15m arm uses the local 15m stream.

Signal discovery only constructs entry clocks. Stop/target/timeout outcomes,
including adverse market-fill stress, use VectorBT's native OHLC stop kernel
and Portfolio.from_orders. Each event/arm is an independent one-unit column.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio.nb import get_stop_price_nb
from vectorbt.signals.enums import StopType
from vectorbt.signals.nb import ohlc_stop_choice_nb

from yoyo.evaluation.month_first_week_touch_review import bounds

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-alt-month-first-week-quarter-reclaim-20261110-v1'
SOURCE = ROOT / 'experiments/active/exp-alt-month-first-week-entries-20261110-v1'
BAR_DIR = ROOT / 'data/crypto/market/kline_deep'
CONFIG = json.loads((OUT / 'config.json').read_text())
FREQ = pd.Timedelta(minutes=15)
HOUR = pd.Timedelta(hours=1)
FREQ_NS = FREQ.value
HOUR_NS = HOUR.value
SPLIT = pd.Timestamp('2025-01-01T00:00:00Z')


@njit(cache=True)
def _native_barriers(o, h, l, c, starts, ends, entries, stop_distances, target_distances, stop_pct):
    """Search 15m OHLC with the same native stop kernel as the frozen adapter."""
    n = len(starts)
    hit_index = np.full(n, -1, dtype=np.int64)
    hit_kind = np.full(n, -1, dtype=np.int64)
    hit_price = np.full(n, np.nan)
    price_out = np.empty(o.shape)
    type_out = np.empty(o.shape, dtype=np.int64)
    scratch = np.empty(1, dtype=np.int64)
    for k in range(n):
        eps = 4 * np.finfo(np.float64).eps
        hit = ohlc_stop_choice_nb(
            starts[k], ends[k] + 1, 0, o, h, l, c,
            price_out, type_out,
            max(0., stop_distances[k] - eps), False,
            max(0., target_distances[k] - eps), True,
            True, 0, True, scratch, True)
        if len(hit):
            i = hit[0]
            kind = type_out[i, 0]
            px = price_out[i, 0]
            if kind == StopType.StopLoss:
                px = get_stop_price_nb(-1., entries[k], stop_pct,
                                       o[i, 0], l[i, 0], h[i, 0], True)
                if np.isnan(px):
                    px = entries[k] * (1. + stop_pct)
            hit_index[k] = i
            hit_kind[k] = kind
            hit_price[k] = px
    return hit_index, hit_kind, hit_price


def _read_events():
    f = pd.read_csv(SOURCE / 'events.csv.gz', parse_dates=['touch'])
    f['touch'] = pd.to_datetime(f.touch, utc=True)
    f = f[(f.side == 'upper') & f.month.between('2022-02', '2026-10')].copy()
    if f.event_id.duplicated().any():
        raise ValueError('Source event ledger has duplicate event ids')
    return f.sort_values(['touch', 'symbol']).reset_index(drop=True)


def _source_map():
    f = pd.read_csv(SOURCE / 'sources.csv.gz', parse_dates=['cutoff'])
    f['cutoff'] = pd.to_datetime(f.cutoff, utc=True)
    return f.set_index('symbol')


def _read_15m(symbol):
    matches = sorted(BAR_DIR.glob(f'okx_{symbol}_USDT_SWAP_15m_*.csv'))
    if len(matches) != 1:
        return None, None, 'no_15m_file' if not matches else 'ambiguous_15m_files'
    path = matches[0]
    f = pd.read_csv(path, usecols=['ts', 'open', 'high', 'low', 'close'])
    f.index = pd.DatetimeIndex(pd.to_datetime(f.pop('ts'), unit='ms', utc=True), name='time')
    if not f.index.is_monotonic_increasing or not f.index.is_unique:
        return None, path, 'invalid_15m_clock'
    x = f[['open', 'high', 'low', 'close']].to_numpy(float)
    if (not np.isfinite(x).all() or (x <= 0).any() or
            (f.high < f[['open', 'close', 'low']].max(axis=1)).any() or
            (f.low > f[['open', 'close']].min(axis=1)).any() or
            (f.index.minute % 15 != 0).any() or (f.index.second != 0).any()):
        return None, path, 'invalid_15m_ohlc'
    return f, path, ''


def _read_h1(source_row):
    path = ROOT / str(source_row.file)
    if not path.is_file():
        return None, path
    f = pd.read_csv(path, usecols=['time', 'open', 'high', 'low', 'close'])
    f.index = pd.DatetimeIndex(pd.to_datetime(f.pop('time'), unit='ms', utc=True), name='time')
    f = f.loc[f.index + HOUR <= source_row.cutoff]
    if not f.index.is_monotonic_increasing or not f.index.is_unique:
        raise ValueError(f'Invalid cached H1 clock for {source_row.symbol}')
    x = f[['open', 'high', 'low', 'close']].to_numpy(float)
    if (not np.isfinite(x).all() or (x <= 0).any() or
            (f.high < f[['open', 'close', 'low']].max(axis=1)).any() or
            (f.low > f[['open', 'close']].min(axis=1)).any()):
        raise ValueError(f'Invalid cached H1 OHLC for {source_row.symbol}')
    return f, path


def _touch_15m(bars, event):
    """Return first literal 15m bar in the recorded H1 touch hour and audits."""
    idx = bars.index
    t = event.touch
    i0 = idx.searchsorted(t, side='left')
    i1 = idx.searchsorted(t + HOUR, side='left')
    touch_hour_index = idx[i0:i1]
    touch_hour_complete = (i1 - i0 == 4 and i0 < len(idx) and
                           touch_hour_index[0] == t and
                           np.all(np.diff(touch_hour_index.asi8) == FREQ_NS))
    touch_ix = np.flatnonzero(bars.high.iloc[i0:i1].to_numpy(float) >= float(event.level))
    literal_i = i0 + int(touch_ix[0]) if len(touch_ix) else -1
    ready = bounds(event.month)[1]
    p0 = idx.searchsorted(ready, side='left')
    p1 = idx.searchsorted(t, side='left')
    prior = bars.high.iloc[p0:p1].to_numpy(float)
    prior_hit = bool((prior >= float(event.level)).any())
    expected = max(0, int((t - ready) / FREQ))
    prior_index = idx[p0:p1]
    prior_complete = (p1 - p0 == expected and
                      (expected == 0 or (prior_index[0] == ready and
                       np.all(np.diff(prior_index.asi8) == FREQ_NS))))
    return literal_i, prior_hit, prior_complete, touch_hour_complete


def _find_signal(arm, event, touch_15m_i, bars15, h1):
    """Find the first strict inward close; do not use a close before the touch."""
    idx15, idx1 = bars15.index, h1.index
    edge, level = float(event.high), float(event.level)
    touch = event.touch
    expiry = bounds(event.month)[2]
    deadline = min(touch + pd.Timedelta(hours=24), expiry)
    if arm == '15m':
        start = touch_15m_i
        if start < 0:
            return {'status': 'no_literal_15m_touch'}
        last = idx15.searchsorted(deadline - FREQ, side='right') - 1
        last = min(last, len(idx15) - 1)
        if last < start:
            return {'status': 'wait_data_gap'}
        prev = None
        for j in range(start, last + 1):
            t = idx15[j]
            if prev is not None and t - prev != FREQ:
                return {'status': 'wait_data_gap'}
            if j > start and float(bars15.low.iloc[j]) <= edge:
                return {'status': 'cancel_near_edge'}
            if float(bars15.close.iloc[j]) < level:
                close_time = t + FREQ
                if close_time >= expiry:
                    return {'status': 'month_expiry'}
                return {'status': 'confirmed', 'confirm_bar': t,
                        'confirm_time': close_time, 'entry_time': close_time}
            prev = t
        if len(idx15) == 0 or (idx15[last] + FREQ) < deadline:
            return {'status': 'wait_data_gap'}
        return {'status': 'month_expiry' if expiry <= touch + pd.Timedelta(hours=24)
                else 'deadline_no_reclaim'}

    i0 = idx1.get_indexer([touch])[0]
    if i0 < 0:
        return {'status': 'h1_touch_bar_missing'}
    last_time = deadline - HOUR
    last = idx1.searchsorted(last_time, side='right') - 1
    last = min(last, len(idx1) - 1)
    prev = None
    for j in range(i0, last + 1):
        t = idx1[j]
        if prev is not None and t - prev != HOUR:
            return {'status': 'wait_data_gap'}
        if j > i0 and float(h1.low.iloc[j]) <= edge:
            return {'status': 'cancel_near_edge'}
        if float(h1.close.iloc[j]) < level:
            close_time = t + HOUR
            if close_time >= expiry:
                return {'status': 'month_expiry'}
            return {'status': 'confirmed', 'confirm_bar': t,
                    'confirm_time': close_time, 'entry_time': close_time}
        prev = t
    if len(idx1) == 0 or (idx1[last] + HOUR) < deadline:
        return {'status': 'wait_data_gap'}
    return {'status': 'month_expiry' if expiry <= touch + pd.Timedelta(hours=24)
            else 'deadline_no_reclaim'}


def _candidate(event, arm, found, bars):
    if found.get('status') != 'confirmed':
        return None, found.get('status', 'not_confirmed')
    entry_time = found['entry_time']
    i = bars.index.get_indexer([entry_time])[0]
    if i < 0:
        return None, 'no_15m_entry_open'
    entry = float(bars.open.iloc[i])
    edge = float(event.high)
    if entry <= edge:
        return None, 'entry_at_or_past_near_edge'
    return dict(event_id=event.event_id, symbol=event.symbol, month=event.month,
                cohort=event.cohort, touch=event.touch, arm=arm,
                confirm_bar=found['confirm_bar'], confirm_time=found['confirm_time'],
                entry_time=entry_time, entry_i=int(i), entry_open=entry,
                level=float(event.level), edge=edge,
                target_price=max(edge, entry * 0.96)), 'confirmed_entry'


def _native_trades(bars, candidates, *, arm, stress=False):
    """Resolve all candidates through VectorBT native stops and Portfolio."""
    if not candidates:
        return []
    f = pd.DataFrame(candidates).reset_index(drop=True)
    raw_open, high, low, close = (bars[k].to_numpy(float) for k in ('open', 'high', 'low', 'close'))
    starts = f.entry_i.to_numpy(np.int64)
    slip = float(CONFIG['costs']['market_slippage_bp']) / 10000 if stress else 0.
    entry = raw_open[starts] * (1. - slip)
    edge = f.edge.to_numpy(float)
    target = np.maximum(edge, entry * 0.96)
    valid = target < entry
    rejected = f.loc[~valid].copy()
    f = f.loc[valid].reset_index(drop=True)
    if f.empty:
        return [dict(**row, scenario='market_slippage_stress', status='stress_entry_past_target')
                for row in rejected.to_dict('records')]
    starts = f.entry_i.to_numpy(np.int64)
    entry = raw_open[starts] * (1. - slip)
    target = np.maximum(f.edge.to_numpy(float), entry * 0.96)
    gaps = np.r_[np.flatnonzero(np.diff(bars.index.asi8) != FREQ_NS), len(bars) - 1]
    last_observed = gaps[np.searchsorted(gaps, starts)]
    timeout = starts + 4 * 168
    can_timeout = last_observed >= timeout
    ends = np.where(can_timeout, 2 * timeout, 2 * last_observed + 1)
    op2 = np.repeat(raw_open, 2).reshape(-1, 1)
    h2 = np.column_stack((raw_open, high)).reshape(-1, 1)
    l2 = np.column_stack((raw_open, low)).reshape(-1, 1)
    c2 = np.column_stack((raw_open, close)).reshape(-1, 1)
    # VectorBT anchors stop distances to the candle open. Translate the
    # slipped entry's absolute barriers back to that exact open reference.
    stop_distance = (entry * 1.05 / raw_open[starts]) - 1.
    target_distance = (raw_open[starts] - target) / raw_open[starts]
    hit, kind, exit_price = _native_barriers(op2, h2, l2, c2, starts * 2,
        ends, entry, stop_distance, target_distance, 0.05)
    exit_price = np.where((hit >= 0) & (kind == StopType.TakeProfit), target, exit_price)
    hit_bar = np.where(hit >= 0, hit // 2, np.minimum(timeout, last_observed))
    live = (hit < 0) & ~can_timeout
    reason = np.where(hit >= 0, np.where(kind == StopType.TakeProfit, 'target', 'stop'),
                      np.where(live, 'censored', 'timeout'))
    if stress:
        market_exit = reason != 'target'
        exit_price = np.where(hit >= 0, exit_price,
                              np.where(live, close[hit_bar], raw_open[hit_bar]))
        exit_price = np.where(market_exit & ~live, exit_price * (1. + slip), exit_price)
    else:
        exit_price = np.where(hit >= 0, exit_price,
                              np.where(live, close[hit_bar], raw_open[hit_bar]))
    # The timeout is a market open; censored columns have no exit order.
    exit_fee_rate = np.where(reason == 'target', float(CONFIG['costs']['maker_fee']),
                             float(CONFIG['costs']['taker_fee']))
    fees = np.vstack((np.full(len(f), float(CONFIG['costs']['taker_fee'])), exit_fee_rate))
    qty = np.vstack((-np.ones(len(f)), np.where(live, np.nan, np.ones(len(f)))))
    prices = np.vstack((entry, exit_price))
    portfolio = vbt.Portfolio.from_orders(prices, size=qty, price=prices,
        size_type='amount', direction='both', fees=fees,
        init_cash=np.maximum(entry, np.nan_to_num(exit_price, nan=0.)) * 4,
        cash_sharing=False, allow_partial=False, raise_reject=True,
        max_orders=2 * len(f))
    records = portfolio.trades.records.sort_values('col')
    if len(records) != len(f) or not np.array_equal(records.status.to_numpy() == 0, live):
        raise AssertionError('VectorBT trade/status rows did not reconcile')
    fees_paid = records.entry_fees.to_numpy() + records.exit_fees.to_numpy()
    gross = (entry - exit_price) / entry * 100
    net = records.pnl.to_numpy() / entry * 100
    fee_pct = fees_paid / entry * 100
    if not np.allclose(gross[~live], net[~live] + fee_pct[~live], atol=1e-8):
        raise AssertionError('VectorBT net returns do not reconcile to gross less fees')
    target_exit = (reason == 'target') & ~live
    if not np.allclose(exit_price[target_exit], target[target_exit], atol=1e-10, rtol=1e-12):
        raise AssertionError('Target fills must use the frozen limit price')
    stop_exit = (reason == 'stop') & ~live
    if (exit_price[stop_exit] < entry[stop_exit] * 1.05 - 1e-10).any():
        raise AssertionError('Short stop fills cannot be better than the stop level')
    out = []
    for k, row in enumerate(f.to_dict('records')):
        a, z = int(starts[k]), int(hit_bar[k])
        intrabar_exit = not live[k] and hit[k] >= 0 and hit[k] % 2 == 1
        if live[k]:
            # Open/censored trades remain exposed through the last observed close.
            low_seen, high_seen = low[a:z + 1], high[a:z + 1]
            mfe = max(0., 100 * (1. - float(np.min(low_seen)) / entry[k]))
            mae = max(0., 100 * (float(np.max(high_seen)) / entry[k] - 1.))
            mfe_upper, mae_upper = mfe, mae
        else:
            # Before the exit candle, all observed wicks occurred while open.
            # Add its known open and fill; only the opposite terminal wick is
            # uncertain. Cap that wick at the resting TP or actual stop.
            prior_low = low[a:z]
            prior_high = high[a:z]
            known_exit_prices = [entry[k], exit_price[k]]
            if z > a:
                known_exit_prices.append(raw_open[z])
            fav_known = max(100 * (1. - p / entry[k]) for p in known_exit_prices)
            adv_known = max(100 * (p / entry[k] - 1.) for p in known_exit_prices)
            mfe = max(0., fav_known,
                      100 * (1. - float(np.min(prior_low)) / entry[k]) if len(prior_low) else 0.)
            mae = max(0., adv_known,
                      100 * (float(np.max(prior_high)) / entry[k] - 1.) if len(prior_high) else 0.)
            mfe_upper, mae_upper = mfe, mae
            if intrabar_exit:
                terminal_fav = 100 * (1. - max(low[z], target[k]) / entry[k])
                terminal_adv = 100 * (min(high[z], entry[k] * 1.05) / entry[k] - 1.)
                mfe_upper = max(mfe, terminal_fav)
                mae_upper = max(mae, terminal_adv)
        row.update(scenario='market_slippage_stress' if stress else 'base',
                   entry_fill=float(entry[k]), stop_price=float(entry[k] * 1.05),
                   target_price=float(target[k]), target_distance_pct=float((entry[k] - target[k]) / entry[k] * 100),
                   exit_reason=str(reason[k]), exit_bar=str(bars.index[z]),
                   exit_time_resolution='within_15m' if (hit[k] >= 0 and hit[k] % 2 == 1) else 'bar_open',
                   exit_price=float(exit_price[k]) if not live[k] else np.nan,
                   mark_price=float(exit_price[k]) if live[k] else np.nan,
                   gross_return_pct=float(gross[k]) if not live[k] else np.nan,
                   fees_pct=float(fee_pct[k]), net_return_pct=float(net[k]) if not live[k] else np.nan,
                   mfe_pct=float(mfe), mfe_pct_upper_bound=float(mfe_upper),
                   mae_pct=float(mae), mae_pct_upper_bound=float(mae_upper),
                   ambiguous_sl_tp=bool(intrabar_exit and low[z] <= target[k] and
                                        high[z] >= entry[k] * 1.05),
                   status='open_censored' if live[k] else 'closed')
        out.append(row)
    for row in rejected.to_dict('records'):
        out.append(dict(**row, scenario='market_slippage_stress', status='stress_entry_past_target'))
    return out


def _process_symbol(events, source_row, path):
    bars, _, error = _read_15m(events.iloc[0].symbol)
    if error:
        return ([dict(event_id=e.event_id, symbol=e.symbol, month=e.month, cohort=e.cohort,
                      touch=e.touch, coverage_status=error) for e in events.itertuples()], [])
    h1, h1_path = _read_h1(source_row)
    if h1 is None:
        return ([dict(event_id=e.event_id, symbol=e.symbol, month=e.month, cohort=e.cohort,
                      touch=e.touch, coverage_status='no_h1_source') for e in events.itertuples()], [])
    quarter = []
    ledgers = []
    for e in events.itertuples(index=False):
        q_i, prior_hit, prior_complete, touch_hour_complete = _touch_15m(bars, e)
        h1_i = h1.index.get_indexer([e.touch])[0]
        h1_touch = h1_i >= 0 and float(h1.high.iloc[h1_i]) >= float(e.level)
        touch_hour_literal = q_i >= 0
        primary_touch_valid = (h1_touch and touch_hour_literal and touch_hour_complete and
                               prior_complete and not prior_hit)
        row = dict(event_id=e.event_id, symbol=e.symbol, month=e.month, cohort=e.cohort,
                   touch=e.touch, level=float(e.level), first_week_high=float(e.high),
                   h1_event_bar_present=bool(h1_i >= 0), h1_event_bar_touch=bool(h1_touch),
                   file_15m=str(path.relative_to(ROOT)), first_15m=str(bars.index.min()),
                   last_15m=str(bars.index.max()), h1_file=str(h1_path.relative_to(ROOT)),
                   literal_touch_15m=bool(touch_hour_literal),
                   touch_hour_complete_15m=bool(touch_hour_complete),
                   touch_time_15m=str(bars.index[q_i]) if q_i >= 0 else '',
                   earlier_15m_touch_before_recorded_hour=bool(prior_hit),
                   prior_15m_history_complete=bool(prior_complete),
                   source_touch_mismatch=bool(not h1_touch or not touch_hour_literal or
                                              not touch_hour_complete or prior_hit or not prior_complete),
                   coverage_status=('eligible' if primary_touch_valid else
                       ('h1_touch_mismatch' if not h1_touch else
                        ('no_literal_15m_touch' if not touch_hour_literal else
                         ('incomplete_15m_touch_hour' if not touch_hour_complete else
                          ('earlier_15m_touch_mismatch' if prior_hit else
                           'prior_15m_history_incomplete'))))))
        arm_rows = {}
        if primary_touch_valid:
            for arm in ('15m', '1h'):
                found = _find_signal(arm, e, q_i, bars, h1)
                candidate, status = _candidate(e, arm, found, bars)
                row[f'{arm}_status'] = status
                row[f'{arm}_confirm_time'] = str(found.get('confirm_time', ''))
                row[f'{arm}_entry_time'] = str(found.get('entry_time', ''))
                if candidate:
                    arm_rows[arm] = candidate
        else:
            for arm in ('15m', '1h'):
                row[f'{arm}_status'] = row['coverage_status']
                row[f'{arm}_confirm_time'] = ''
                row[f'{arm}_entry_time'] = ''
        quarter.extend(arm_rows.values())
        ledgers.append(row)
    trades = []
    for arm in ('15m', '1h'):
        candidates = [x for x in quarter if x['arm'] == arm]
        trades.extend(_native_trades(bars, candidates, arm=arm, stress=False))
        trades.extend(_native_trades(bars, candidates, arm=arm, stress=True))
    return ledgers, trades


def _summarize(ledger, trades):
    l = pd.DataFrame(ledger)
    t = pd.DataFrame(trades)
    if not t.empty:
        t['touch'] = pd.to_datetime(t.touch, utc=True)
        t['period'] = np.where(t.touch < SPLIT, 'pre_2025', 'post_2025')
        t['period_touch'] = t.touch.astype(str)
        t.to_csv(OUT / 'trades.csv.gz', index=False)
    l.to_csv(OUT / 'event_eligibility.csv.gz', index=False)
    rows = []
    if not t.empty:
        base = t[(t.scenario == 'base') & (t.status == 'closed')]
        for period in ('all', 'pre_2025', 'post_2025'):
            for arm in ('15m', '1h'):
                arm_all = t[(t.scenario == 'base') & t.arm.eq(arm)]
                if period != 'all':
                    arm_all = arm_all[arm_all.period.eq(period)]
                x = base[base.arm.eq(arm)]
                if period != 'all':
                    x = x[x.period.eq(period)]
                wins = x.net_return_pct > 0
                positive = x.loc[x.net_return_pct > 0, 'net_return_pct'].sum()
                negative = -x.loc[x.net_return_pct < 0, 'net_return_pct'].sum()
                rows.append(dict(period=period, arm=arm, n_triggered=len(arm_all),
                    n_closed=len(x), n_censored=int(arm_all.status.eq('open_censored').sum()),
                    mean_net_pct=x.net_return_pct.mean(), median_net_pct=x.net_return_pct.median(),
                    win_rate_pct=100 * wins.mean() if len(x) else np.nan,
                    profit_factor=positive / negative if negative else np.nan,
                    mean_fee_pct=x.fees_pct.mean(), mean_mfe_pct=x.mfe_pct.mean(),
                    mean_mfe_upper_bound_pct=x.mfe_pct_upper_bound.mean(),
                    median_mfe_pct=x.mfe_pct.median(), mfe_2pct=int((x.mfe_pct >= 2).sum()),
                    mfe_4pct=int((x.mfe_pct >= 4).sum())))
        stress = t[(t.scenario == 'market_slippage_stress') & (t.status == 'closed')]
        for period in ('all', 'pre_2025', 'post_2025'):
            for arm in ('15m', '1h'):
                arm_all = t[(t.scenario == 'market_slippage_stress') & t.arm.eq(arm)]
                if period != 'all':
                    arm_all = arm_all[arm_all.period.eq(period)]
                x = stress[stress.arm.eq(arm)]
                if period != 'all':
                    x = x[x.period.eq(period)]
                rows.append(dict(period=period, arm=arm + '_5bp_market_slippage',
                    n_triggered=len(arm_all), n_closed=len(x),
                    n_censored=int(arm_all.status.eq('open_censored').sum()),
                    mean_net_pct=x.net_return_pct.mean(), median_net_pct=x.net_return_pct.median(),
                    win_rate_pct=100 * (x.net_return_pct > 0).mean() if len(x) else np.nan,
                    profit_factor=np.nan, mean_fee_pct=x.fees_pct.mean(),
                    mean_mfe_pct=x.mfe_pct.mean(), mean_mfe_upper_bound_pct=x.mfe_pct_upper_bound.mean(),
                    median_mfe_pct=x.mfe_pct.median(),
                    mfe_2pct=int((x.mfe_pct >= 2).sum()), mfe_4pct=int((x.mfe_pct >= 4).sum())))
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / 'metrics.csv', index=False)
    status_counts = l.coverage_status.value_counts(dropna=False).to_dict()
    for arm in ('15m', '1h'):
        status_counts[f'{arm}_confirmed_entries'] = int(l.get(f'{arm}_status', pd.Series(dtype=str)).eq('confirmed_entry').sum())
    base = t[(t.scenario == 'base') & (t.status == 'closed')] if not t.empty else pd.DataFrame()
    both = l[(l.get('15m_status', '') == 'confirmed_entry') & (l.get('1h_status', '') == 'confirmed_entry')]
    paired = pd.DataFrame()
    if not base.empty and not both.empty:
        p = base[base.event_id.isin(both.event_id)].pivot(index='event_id', columns='arm',
                                                          values=['entry_time', 'confirm_time', 'net_return_pct'])
        if ('net_return_pct', '15m') in p and ('net_return_pct', '1h') in p:
            p['net_return_difference_15m_minus_1h'] = p[('net_return_pct', '15m')] - p[('net_return_pct', '1h')]
            paired = p.reset_index()
            paired.columns = ['_'.join(str(x) for x in c if str(x)) if isinstance(c, tuple) else str(c)
                              for c in paired.columns]
            paired.to_csv(OUT / 'paired_clocks.csv', index=False)
    file_present = l.get('file_15m', pd.Series('', index=l.index)).fillna('').ne('')
    h1_touch_values = l.get('h1_event_bar_touch', pd.Series(False, index=l.index))
    literal_values = l.get('literal_touch_15m', pd.Series(False, index=l.index))
    prior_values = l.get('earlier_15m_touch_before_recorded_hour', pd.Series(False, index=l.index))
    prior_complete_values = l.get('prior_15m_history_complete', pd.Series(False, index=l.index))
    summary = {
        'experiment_id': CONFIG['experiment_id'],
        'vectorbt_version': vbt.__version__,
        'source_upper_events': int(len(l)),
        'coverage_status_counts': status_counts,
        'arm_status_counts': {arm: l[f'{arm}_status'].value_counts(dropna=False).to_dict()
                              for arm in ('15m', '1h') if f'{arm}_status' in l.columns},
        'recorded_h1_source_touch_mismatches': int(h1_touch_values.eq(False).sum()),
        '15m_literal_touch_mismatches': int(literal_values.eq(False).sum()),
        'incomplete_15m_touch_hours': int((file_present & l.get('touch_hour_complete_15m', pd.Series(False, index=l.index)).eq(False)).sum()),
        'earlier_15m_touch_before_recorded_h1_hour': int(prior_values.eq(True).sum()),
        'prior_15m_history_incomplete': int((file_present & prior_complete_values.eq(False)).sum()),
        'shared_confirmed_events': int(len(both)),
        'paired_closed_events': int(len(paired)),
        'paired_mean_entry_lag_15m_minus_1h_minutes': (
            float((pd.to_datetime(both['15m_entry_time'], utc=True) -
                   pd.to_datetime(both['1h_entry_time'], utc=True)).dt.total_seconds().mean() / 60)
            if len(both) else None),
        'period_split': 'event touch before vs at/after 2025-01-01 UTC',
        'metrics': metrics.to_dict('records'),
        'scope_limitations': [
            'Only symbols/events covered by the pre-existing local OKX 15m archive enter the head-to-head.',
            'Recorded event timestamps/levels and H1 first-week range are frozen from the original H1 ledger.',
            'H1 reclaim/cancellation use the original cached H1 bars; every entry and exit fill uses the same 15m source.',
            'OHLC cannot reveal within-15m order; simultaneous stop/target uses VectorBT stop-first behavior.',
            'Funding, shared capital, market capacity, and liquidation are not modeled; returns are independent trade percentages.'
        ]
    }
    def json_safe(value):
        if isinstance(value, dict):
            return {str(k): json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(v) for v in value]
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating, float)):
            return float(value) if np.isfinite(value) else None
        if pd.isna(value):
            return None
        return value
    summary = json_safe(summary)
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: summary[k] for k in ('source_upper_events', 'coverage_status_counts',
          'recorded_h1_source_touch_mismatches', '15m_literal_touch_mismatches',
          'earlier_15m_touch_before_recorded_h1_hour', 'shared_confirmed_events', 'paired_closed_events')},
          indent=2))
    print(metrics.to_string(index=False))


def main():
    events = _read_events()
    sources = _source_map()
    ledger, trades = [], []
    for symbol, group in events.groupby('symbol', sort=False):
        if symbol not in sources.index:
            ledger.extend(dict(event_id=e.event_id, symbol=e.symbol, month=e.month,
                               cohort=e.cohort, touch=e.touch, coverage_status='no_h1_manifest')
                          for e in group.itertuples())
            continue
        source_row = sources.loc[symbol]
        matches = sorted(BAR_DIR.glob(f'okx_{symbol}_USDT_SWAP_15m_*.csv'))
        path = matches[0] if len(matches) == 1 else None
        if path is None:
            ledger.extend(dict(event_id=e.event_id, symbol=e.symbol, month=e.month,
                               cohort=e.cohort, touch=e.touch, coverage_status=
                               ('no_15m_file' if not matches else 'ambiguous_15m_files'))
                          for e in group.itertuples())
            continue
        part_ledger, part_trades = _process_symbol(group, source_row, path)
        ledger.extend(part_ledger)
        trades.extend(part_trades)
    _summarize(ledger, trades)


if __name__ == '__main__':
    main()
