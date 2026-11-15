"""First touch of each frozen monthly rail, followed by its confirmed 12h close.

Signal columns use only H1 timestamps and OHLC through the close of the UTC
12h bar. The hourly tape must continuously cover the first New York week and
the interval between its completion and first touch. The following open is an
execution price. Subsequent highs/lows enter only through the exit engine.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from yoyo.monitor.month_first_week_1618 import HOUR_MS, month_window

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-binance-monthweek-12h-first-wick-20261115-v1'
REFRESH = ROOT / 'data/research/monthweek-12h-first-touch-20261115'
PARENT = ROOT / 'experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1'
H12 = 12 * HOUR_MS
TARGETS = ['1R','2R','3R','5R','6R','7R','10R','20R','near','mid','far']


def calendar(config):
    stop = pd.Timestamp(config['data_cutoff_exclusive'])
    months = pd.period_range(config['calendar_start'], stop.strftime('%Y-%m'), freq='M')
    return [month_window(int(pd.Timestamp(f'{m}-15', tz='UTC').timestamp() * 1000)) for m in months]


def tape(symbol, cutoff, read_raw):
    old = read_raw(symbol)
    f = REFRESH / 'stores' / (symbol + '.npy')
    if f.exists():
        new = np.load(f, allow_pickle=False)
        old = np.concatenate((new, old))
        _, indices = np.unique(old[:, 0], return_index=True)
        old = old[indices]
    raw = old[old[:, 0] + HOUR_MS <= cutoff]
    if len(raw):
        t, o, h, l, c = raw.T
        if (not np.isfinite(raw).all() or np.any(l <= 0) or np.any(t % HOUR_MS)
                or np.any(np.diff(t) <= 0) or np.any(h < np.maximum(o, c))
                or np.any(l > np.minimum(o, c))):
            raise ValueError(f'Invalid hourly candles for {symbol}')
    return raw


def scan(symbol, raw, months, buffer):
    """Return the first touch ledger and the observable anchor-month ledger."""
    touches, coverage = [], []
    if not len(raw):
        return touches, coverage
    ts = raw[:, 0].astype(np.int64)
    gaps = np.flatnonzero(np.diff(ts) != HOUR_MS) + 1
    for window in months:
        month = window['month']
        start, ready, expiry = (window[k] for k in ('start_ms', 'ready_ms', 'expires_ms'))
        if expiry <= ts[0] or ready > ts[-1] + HOUR_MS:
            continue
        a, b, z = np.searchsorted(ts, [start, ready, expiry])
        coverage_row = dict(symbol=symbol, month=month, status='eligible')
        coverage.append(coverage_row)
        if b - a != window['expected_1h_bars']:
            coverage_row['status'] = 'incomplete_anchor'
            continue
        hi, lo = raw[a:b, 2].max(), raw[a:b, 3].min()
        if hi <= lo:
            coverage_row['status'] = 'flat_anchor'
            continue
        if b == len(ts) or ts[b] != ready:
            coverage_row['status'] = 'no_contiguous_post_week_tape'
            continue
        next_gap = gaps[gaps > b]
        end = min(z, next_gap[0]) if len(next_gap) else z
        coverage_row.update(week_high=hi, week_low=lo, last_observed_ms=int(ts[end - 1]),
                            active_window_complete=bool(ts[end - 1] + HOUR_MS == expiry))
        for side, rail in [(1, lo - .618 * (hi-lo)), (-1, hi + .618 * (hi-lo))]:
            if rail <= 0:
                continue
            extreme = raw[b:end, 3 if side == 1 else 2]
            eps = 1e-12 * rail
            hits = np.flatnonzero(side * (extreme - rail) <= eps)
            if not len(hits):
                continue
            i = b + hits[0]
            bar = int(ts[i] // H12 * H12)
            close_ms = bar + H12
            j, k = np.searchsorted(ts, [bar, close_ms])
            row = dict(event_id=f"{symbol}/{month}/{'lower' if side == 1 else 'upper'}",
                       symbol=symbol, month=month, side=side, touch_ms=int(ts[i]),
                       bar_ms=bar, close_ms=close_ms, rail=rail, week_high=hi, week_low=lo,
                       status='no_reclaim')
            touches.append(row)
            if close_ms > expiry:
                row['status'] = 'expires_before_close'
                continue
            if k-j != 12:
                row['status'] = 'incomplete_12h'
                continue
            o, h, l, c = raw[j, 1], raw[j:k, 2].max(), raw[j:k, 3].min(), raw[k-1, 4]
            row.update(bar_open=o, bar_high=h, bar_low=l, bar_close=c,
                       body_inside=bool(side*(o-rail) > eps and side*(c-rail) > eps))
            if side*(c-rail) <= eps:
                continue
            row['stop'] = l*(1-buffer) if side == 1 else h*(1+buffer)
            if k == len(ts) or ts[k] != close_ms:
                row['status'] = 'confirmed_no_next_open'
                continue
            entry = raw[k, 1]
            if side*(entry-row['stop']) <= 0:
                row['status'] = 'confirmed_open_beyond_stop'
                continue
            row.update(status='entered', entry_i=int(k), entry=entry,
                       risk_frac=side*(entry-row['stop'])/entry)
    return touches, coverage


def controls(raw, entries, by_month):
    """Match each observation's symbol, range month, side and initial risk percentage."""
    ts = raw[:, 0].astype(np.int64)
    clocks = np.flatnonzero(ts % H12 == 0)
    out = []
    for e in entries:
        w = by_month[e['month']]
        choices = clocks[(ts[clocks] >= w['ready_ms']) & (ts[clocks] < w['expires_ms'])
                         & (clocks != e['entry_i'])]
        if not len(choices):
            continue
        seed = hashlib.blake2b(('20261115/'+e['event_id']).encode(), digest_size=8).digest()
        i = int(np.random.default_rng(int.from_bytes(seed, 'big')).choice(choices))
        px = raw[i, 1]
        out.append({**e, 'entry_i': i, 'entry': px, 'stop': px*(1-e['side']*e['risk_frac'])})
    return out


def summarize(frame):
    rows = []
    for (cohort, target, group), g in frame.groupby(['cohort','target','group'], sort=False):
        tp = g.outcome.eq('tp')
        sl = g.outcome.eq('sl')
        cens = g.outcome.eq('censored')
        behind = g.outcome.eq('behind_entry')
        if not (tp|sl|cens|behind).all():
            raise ValueError('Unknown outcome')
        at_risk = int((~behind).sum())
        closed = int((tp|sl).sum())
        rows.append(dict(cohort=cohort, target=target, group=group, entries=len(g),
                         at_risk=at_risk, tp=int(tp.sum()), sl=int(sl.sum()),
                         censored=int(cens.sum()), behind=int(behind.sum()),
                         impossible=int((~g.target_possible & ~behind).sum()),
                         ambiguous=int(g.ambiguous.sum()),
                         hit_pct=100*tp.sum()/at_risk if at_risk else np.nan,
                         settled_hit_pct=100*tp.sum()/closed if closed else np.nan,
                         settled_net_win_pct=100*(g.net_r>0).sum()/closed if closed else np.nan,
                         mean_settled_net_r=g.net_r.mean()))
    return pd.DataFrame(rows)


def main():
    from yoyo.evaluation.month_firstweek_12h_wick_exits import run_exits
    sys.path.insert(0, str(PARENT))
    from census12 import read_raw

    config = json.loads((EXP / 'config.json').read_text())
    cutoff = int(pd.Timestamp(config['data_cutoff_exclusive']).timestamp()*1000)
    months = calendar(config)
    by_month = {w['month']: w for w in months}
    roster = pd.read_csv(REFRESH / 'roster.csv')
    assert roster.symbol.is_unique
    symbol_rows, all_touches, coverage, outcomes = [], [], [], []
    for n, symbol in enumerate(roster.symbol, 1):
        raw = tape(symbol, cutoff, read_raw)
        sig, cov = scan(symbol, raw, months, config['stop_buffer'])
        entries = [e for e in sig if e['status'] == 'entered']
        symbol_rows.append(dict(symbol=symbol, hours=len(raw),
                                first_ms=int(raw[0,0]) if len(raw) else None,
                                end_ms=int(raw[-1,0])+HOUR_MS if len(raw) else None,
                                eligible_months=sum(x['status']=='eligible' for x in cov), trades=len(entries)))
        for cohort, events in [('signal', entries), ('random', controls(raw, entries, by_month))]:
            if events:
                fills = run_exits(raw, events)
                keys = {e['event_id']:e for e in events}
                for f in fills:
                    e = keys[f['event_id']]
                    f.update(cohort=cohort, side=e['side'], month=e['month'],
                             entry_ms=int(raw[e['entry_i'],0]), entry=e['entry'], stop=e['stop'])
                outcomes.extend(fills)
        all_touches.extend(sig)
        coverage.extend(cov)
        if n % 100 == 0:
            print(f'{n}/{len(roster)} contracts; {len(all_touches)} first touches', flush=True)
    pd.DataFrame(symbol_rows).to_csv(EXP/'contracts.csv', index=False)
    pd.DataFrame(coverage).to_csv(EXP/'anchor_coverage.csv.gz', index=False)
    pd.DataFrame(all_touches).drop(columns='entry_i').to_csv(EXP/'first_touches.csv.gz', index=False)
    f = pd.DataFrame(outcomes)
    assert not f.duplicated(['cohort','event_id','target']).any()
    assert set(f.target) == set(TARGETS)
    assert f.groupby(['cohort','event_id']).size().eq(len(TARGETS)).all()
    f.to_csv(EXP/'outcomes.csv.gz', index=False)
    summary = pd.concat([summarize(f.assign(group=g)) for g in
                         ['all', f.side.map({1:'long', -1:'short'}), f.month.str[:4]]], ignore_index=True)
    summary.to_csv(EXP/'metrics.csv', index=False)


if __name__ == '__main__':
    main()
