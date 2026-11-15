"""First external wick on a frozen NY monthly rail, with 1h/4h/12h priority.

Only H1 time, O/H/L/C from anchor confirmation to the first outside hour
and the OHLC of the containing, completed decision candle determine eligibility.
Any later-duration candle also recorded in the audit has no role in an earlier
signal. Fees, paths and aperture of the market census come from the parent.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from yoyo.data.binance_monthweek_disputed_minutes import minute_lookup
from yoyo.data.binance_monthweek_lifecycle import CACHE_ROOT
from yoyo.evaluation import month_firstweek_12h_wick_census as base
from yoyo.evaluation.month_firstweek_12h_wick_exits import run_exits

EXP = base.EXP.parent / 'exp-binance-monthweek-wick-priority-20261116-v1'
HOUR = base.HOUR_MS
DAY = 24 * HOUR


def frozen_tape(symbol, contract, end_ms, cutoff, read_raw):
    """Apply the audited parent lifecycle exclusions to its immutable hourly tape."""
    raw = base.tape(symbol, cutoff, read_raw)
    if pd.notna(end_ms) and 0 < end_ms <= cutoff:
        raw = raw[raw[:, 0] < end_ms]
    if len(raw):
        receipt = json.loads((CACHE_ROOT / symbol / 'clean_receipt.json').read_text())
        ts = raw[:, 0].astype(np.int64)
        drop = np.isin(ts // DAY * DAY, receipt['removed_zero_trade_days_utc_ms'])
        drop |= np.isin(ts, receipt['removed_zero_trade_hours_ms'])
        raw = raw[~drop]
    if len(raw) != contract.hours or (len(raw) and
        (raw[0, 0] != contract.first_ms or raw[-1, 0] + HOUR != contract.end_ms)):
        raise ValueError(f'Parent hourly coverage changed for {symbol}')
    return raw


def candle(raw, ts, breach_time, hours, expiry, side, rail, tolerance):
    start = breach_time // (hours * HOUR) * hours * HOUR
    close_ms = start + hours * HOUR
    if close_ms > expiry:
        return 'past_expiry', None
    j, k = np.searchsorted(ts, [start, close_ms])
    if k-j != hours:
        return 'incomplete', None
    o, h, l, c = raw[j, 1], raw[j:k, 2].max(), raw[j:k, 3].min(), raw[k-1, 4]
    tip = l if side == 1 else h
    if not (side*(o-rail) > tolerance and side*(c-rail) > tolerance and side*(tip-rail) < -tolerance):
        return 'failed', None
    return 'wick', dict(tf=hours, bar_ms=int(start), close_ms=int(close_ms), bar_open=o, bar_high=h,
                        bar_low=l, bar_close=c, tip=tip, entry_i=int(k))


def classify(raw, records, calendar, durations, buffer):
    ts = raw[:, 0].astype(np.int64)
    gap_ends = np.flatnonzero(np.diff(ts) != HOUR) + 1
    audit, entries = [], []
    for old in records.itertuples(index=False):
        side, rail = int(old.side), old.rail
        w = calendar[old.month]
        # The parent ledger includes all first inclusive reaches. Search again
        # inside its observable active window so an equality cannot consume a rail.
        a, z = np.searchsorted(ts, [w['ready_ms'], w['expires_ms']])
        gap = gap_ends.searchsorted(a, side='right')
        if gap < len(gap_ends):
            z = min(z, gap_ends[gap])
        eps = rail * 1e-12
        extremes = raw[a:z, 3 if side == 1 else 2]
        exterior = np.flatnonzero(side*(extremes-rail) < -eps)
        row = dict(event_id=old.event_id, symbol=old.symbol, month=old.month, side=side,
                   first_reach_ms=old.touch_ms, rail=rail, week_high=old.week_high,
                   week_low=old.week_low, status='touch_only')
        audit.append(row)
        if not len(exterior):
            continue
        offset = int(exterior[0])
        when = int(ts[a+offset])
        prior = extremes[:offset]
        row.update(first_breach_ms=when, status='none_qualified',
                   prior_extreme=(prior.min() if side == 1 else prior.max()) if len(prior) else None)
        chosen = None
        for hours in durations:
            state, bar = candle(raw, ts, when, hours, w['expires_ms'], side, rail, eps)
            row[f'state_{hours}h'] = state
            if chosen is None and bar is not None:
                chosen = bar
        if chosen is None:
            continue
        k = chosen.pop('entry_i')
        stop = chosen['tip'] * (1 - side*buffer)
        row.update(chosen, stop=stop)
        if k >= len(ts) or ts[k] != chosen['close_ms']:
            row['status'] = 'confirmed_no_next_open'
            continue
        px = raw[k, 1]
        risk = side * (px-stop) / px
        if risk <= 0:
            row['status'] = 'confirmed_open_beyond_stop'
            continue
        row.update(status='entered', entry=px, risk_frac=risk)
        entries.append({**row, 'entry_i': k, 'entry_ms': int(ts[k])})
    return audit, entries


def matched_controls(raw, entries, windows):
    ts = raw[:, 0].astype(np.int64)
    clocks = {tf:np.flatnonzero(ts % (tf*HOUR) == 0) for tf in {e['tf'] for e in entries}}
    matched = []
    for e in entries:
        w = windows[e['month']]
        times = clocks[e['tf']]
        choices = times[(ts[times]>=w['ready_ms']) & (ts[times]<w['expires_ms']) & (times!=e['entry_i'])]
        if not len(choices):
            continue
        seed = hashlib.blake2b(('20261115/'+e['event_id']).encode(), digest_size=8).digest()
        i = int(np.random.default_rng(int.from_bytes(seed, 'big')).choice(choices))
        px = raw[i, 1]
        matched.append({**e, 'entry_i':i, 'entry_ms':int(ts[i]), 'entry':px,
                        'stop':px*(1-e['side']*e['risk_frac'])})
    return matched


def evaluate(raw, events, old, cohort):
    if not events:
        return pd.DataFrame()
    previous = old.drop_duplicates('event_id').set_index('event_id')
    keep, fresh = {}, []
    for e in events:
        key = e['event_id']
        if key in previous.index:
            prev = previous.loc[key]
            if (prev.entry_ms, prev.entry, prev.stop) == (e['entry_ms'], e['entry'], e['stop']):
                keep[key] = e['tf']
                continue
        fresh.append(e)
    reused = old[old.event_id.isin(keep)].copy()
    reused['tf'] = reused.event_id.map(keep)
    reused['reused'] = True
    if not fresh:
        return reused
    by_id = {e['event_id']:e for e in fresh}
    rows = run_exits(raw, fresh, minute_lookup=minute_lookup)
    for r in rows:
        e = by_id[r['event_id']]
        r.update(cohort=cohort, reused=False, **{k:e[k] for k in
                 ('tf','side','month','entry_ms','entry','stop')})
    return pd.concat([reused, pd.DataFrame(rows)], ignore_index=True)


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    assert cfg['parent'] == base.EXP.name
    cutoff = int(pd.Timestamp(cfg['cutoff_exclusive']).timestamp()*1000)
    parent_cfg = json.loads((base.EXP/'config.json').read_text())
    assert pd.Timestamp(parent_cfg['data_cutoff_exclusive']) == pd.Timestamp(cfg['cutoff_exclusive'])
    windows = {w['month']:w for w in base.calendar(parent_cfg)}
    contracts = pd.read_csv(base.EXP/'contracts.csv').set_index('symbol')
    ends = pd.read_csv(base.REFRESH/'roster.csv').set_index('symbol').end_ms
    touches = pd.read_csv(base.EXP/'first_touches.csv.gz', float_precision='round_trip')
    old = pd.read_csv(base.EXP/'outcomes.csv.gz', float_precision='round_trip')
    old['symbol'] = old.event_id.str.split('/').str[0]
    old_groups = old.groupby(['symbol','cohort'])
    empty = old.iloc[0:0].drop(columns='symbol')
    sys.path.insert(0, str(base.PARENT))
    from census12 import read_raw

    def one_symbol(item):
        symbol, records = item
        raw = frozen_tape(symbol, contracts.loc[symbol], ends.loc[symbol], cutoff, read_raw)
        audit, entries = classify(raw, records, windows, cfg['timeframes_hours'], cfg['stop_buffer'])
        frames = []
        for cohort, events in [('signal',entries),('random',matched_controls(raw, entries, windows))]:
            key = (symbol,cohort)
            saved = old_groups.get_group(key).drop(columns='symbol') if key in old_groups.indices else empty
            frame = evaluate(raw, events, saved, cohort)
            if len(frame):
                frames.append(frame)
        return audit, frames

    audits, fills = [], []
    with ThreadPoolExecutor(max_workers=7) as pool:
        for n, (audit, frames) in enumerate(pool.map(one_symbol, touches.groupby('symbol')), 1):
            audits.extend(audit)
            fills.extend(frames)
            if n % 150 == 0:
                print(f'{n} symbols with touches, {len(audits)} month/rail records', flush=True)
    a, f = pd.DataFrame(audits), pd.concat(fills, ignore_index=True)
    assert len(a) == len(touches) and a.event_id.is_unique
    assert set(f.target) == set(base.TARGETS) and not f.duplicated(['cohort','event_id','target']).any()
    assert f.groupby(['cohort','event_id']).size().eq(len(base.TARGETS)).all()
    a.to_csv(EXP/'first_breaches.csv.gz', index=False)
    f['tf'] = f.tf.astype(int)
    f.to_csv(EXP/'outcomes.csv.gz', index=False)
    groups = ['all', f.tf.astype(str)+'h', f.side.map({1:'long',-1:'short'}), f.month.str[:4]]
    pd.concat([base.summarize(f.assign(group=g)) for g in groups]).to_csv(EXP/'metrics.csv', index=False)


if __name__ == '__main__':
    main()
