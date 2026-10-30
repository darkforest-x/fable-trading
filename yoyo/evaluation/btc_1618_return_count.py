"""Descriptive return-to-range counts for the owner's 859 repeated touches.

Owner 2026-10-30: OKX BTC, completed NY Monday H/L, Tuesday-Sunday 15m
touch episodes; adjacent bars spanning the same outer 1.618 are one episode.
One wholly non-touching 15m bar rearms the counter. All 859 original episode
starts and the original noon Shanghai cutoff are frozen, including repeats.
This counts subsequent price reachability, not orders, returns or a strategy.

Range return means upper -> Monday H, lower -> Monday L. A later bar proves
ordering. Within the first touch bar, a close inside the edge or an open
beyond the extension proves ordering; otherwise refine with existing 1m
bars and retain any residual ordering ambiguity. No OHLC path is invented.
Future labels are evaluated separately to NY next Monday, 7 days, and the
fixed data cutoff. Failure to return before a truncated horizon is censored.
Repeated episodes may share a single return; no independent-trade inference.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-btc-1618-return-count-20261030-v1'
START = pd.Timestamp('2024-10-30T00:00:00+08:00')
END = pd.Timestamp('2026-10-30T12:00:00+08:00')
STEP = pd.Timedelta(minutes=15)
ARCHIVE = ROOT / 'experiments/active/exp-btc-monday-fib1618-okx-20261022-v1/candles.csv.gz'
CACHE = Path.home() / 'Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3'


def ordered_same_bar(bar, side, level, edge):
    """OHLC proof of edge reach after first extension reach; no path model."""
    if side == 'upper':
        return bar.low <= edge and (bar.close <= edge or bar.open >= level)
    return bar.high >= edge and (bar.close >= edge or bar.open <= level)


def load():
    raw = pd.read_csv(ARCHIVE)
    raw.index = pd.to_datetime(raw.ts, unit='ms', utc=True)
    assert raw.index.is_unique and (np.diff(raw.ts) == 60_000).all()
    grouped = raw.resample('15min')
    assert grouped.close.count().eq(15).all()
    old = grouped.agg(dict(open='first', high='max', low='min', close='last'))
    snapshot = EXP / 'inputs/recent_15m.csv.gz'
    if snapshot.exists():
        recent = pd.read_csv(snapshot)
    else:
        with sqlite3.connect(CACHE.as_uri() + '?mode=ro', uri=True) as db:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT raw FROM candles WHERE key=? AND t<? ORDER BY t',
                ('https://www.okx.com|BTC-USDT-SWAP|15m', int(END.timestamp()*1000)))]
        recent = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close',
                                            'contracts', 'base', 'quote', 'confirm'])
        assert recent['confirm'].astype(str).eq('1').all()
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        recent.to_csv(snapshot, index=False)
    recent.index = pd.to_datetime(recent.ts.astype('int64'), unit='ms', utc=True)
    recent = recent[['open', 'high', 'low', 'close']].astype(float)
    overlap = old.index.intersection(recent.index)
    assert len(overlap) > 0
    assert np.allclose(old.loc[overlap], recent.loc[overlap], atol=1e-8, rtol=0)
    bars = pd.concat([old, recent[recent.index > old.index[-1]]])
    bars = bars[bars.index < END]
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    assert ((bars.index[1:]-bars.index[:-1]) == STEP).all()
    assert bars.index[-1]+STEP == END
    return bars, raw, len(overlap)


def same_bar_bounds(bars, minute, i, side, level, edge):
    bar = bars.iloc[i]
    possible = bool(bar.low <= edge if side == 'upper' else bar.high >= edge)
    if not possible or ordered_same_bar(bar, side, level, edge):
        return possible, possible
    fine = minute[(minute.index >= bars.index[i]) & (minute.index < bars.index[i]+STEP)]
    if len(fine) != 15:
        return False, True
    touch = (fine.low <= level) & (fine.high >= level)
    assert touch.any()
    first = int(np.flatnonzero(touch)[0])
    reach = (fine.low <= edge if side == 'upper' else fine.high >= edge).to_numpy()
    after = bool(reach[first+1:].any())
    definite = after or ordered_same_bar(fine.iloc[first], side, level, edge)
    return bool(definite), bool(after or reach[first])


def run():
    EXP.mkdir(parents=True, exist_ok=True)
    bars, minute, overlap = load()
    local = bars.index.tz_convert('America/New_York').tz_localize(None)
    weeks = local.normalize()-pd.to_timedelta(local.dayofweek, unit='D')
    events = []
    for week, positions in pd.Series(np.arange(len(bars)), index=weeks).groupby(level=0):
        pos = positions.to_numpy()
        mon = pos[local[pos].dayofweek == 0]
        rest = pos[local[pos].dayofweek > 0]
        if not len(rest) or bars.index[rest[-1]] < START:
            continue
        assert len(mon) == 96
        high, low = bars.high.iloc[mon].max(), bars.low.iloc[mon].min()
        next_week = (week+pd.Timedelta(days=7)).tz_localize('America/New_York')
        for side, level, edge in [('upper', high+.618*(high-low), high),
                                  ('lower', low-.618*(high-low), low)]:
            hit = ((bars.low.iloc[rest] <= level) & (bars.high.iloc[rest] >= level)).to_numpy()
            ix = np.flatnonzero(hit)
            starts = ix[np.r_[True, np.diff(ix) > 1]] if len(ix) else []
            ends = ix[np.r_[np.diff(ix) > 1, True]] if len(ix) else []
            reach = np.flatnonzero((bars.low <= edge if side == 'upper' else bars.high >= edge).to_numpy())
            for a, b in zip(starts, ends):
                i, last = int(rest[a]), int(rest[b])
                stamp = bars.index[i]
                if stamp < START:
                    continue
                certain, possible = same_bar_bounds(bars, minute, i, side, level, edge)
                following = reach[reach > i]
                later = bars.index[following[0]] if len(following) else None
                definite_time = stamp if certain else later
                possible_time = stamp if possible else later
                row = dict(week=str(week.date()), side=side, level=level, edge=edge,
                           start=str(stamp.tz_convert('Asia/Shanghai')),
                           episode_last_bar=str(bars.index[last].tz_convert('Asia/Shanghai')),
                           episode_bars=last-i+1, return_bar=str(definite_time) if definite_time is not None else None,
                           same_bar_definite=certain, same_bar_possible=possible)
                for name, horizon in [('same_week', next_week), ('seven_days', stamp+pd.Timedelta(days=7)), ('through_cutoff', END)]:
                    end = min(horizon, END)
                    yes = definite_time is not None and definite_time < end
                    maybe = possible_time is not None and possible_time < end
                    row[name] = ('returned' if yes else 'ambiguous' if maybe else
                                 'pending' if horizon > END or name == 'through_cutoff' else 'not_returned')
                events.append(row)
    frame = pd.DataFrame(events).sort_values(['start', 'side']).reset_index(drop=True)
    assert frame.groupby('side').size().to_dict() == {'lower': 451, 'upper': 408}
    current = frame[frame.week.eq('2026-10-26')]
    assert current.side.value_counts().to_dict() == {'upper': 3, 'lower': 2}
    frame.to_csv(EXP/'events.csv', index=False)
    summary = dict(start=str(START), end=str(END), events=len(frame), overlap_bars_equal=overlap,
                   source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   training_eligible=False, production_eligible=False, tables={})
    for name in ['same_week', 'seven_days', 'through_cutoff']:
        summary['tables'][name] = {side: dict(n=len(g), **g[name].value_counts().to_dict())
                                   for side, g in frame.groupby('side')}
    (EXP/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    run()
