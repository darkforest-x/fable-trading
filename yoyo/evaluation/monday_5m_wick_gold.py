"""HistData quality adapter for the owner-confirmed Monday 5m study.

Use the same candidate rules and Nautilus engine as monday_5m_wick_engulf.
Conflicting duplicate minutes have no defensible ordering: exclude every 5m
bar containing one, and censor positions before the excluded interval. Never
choose the first/last conflicting quote. Scheduled source gaps remain visible;
Bid-only OHLC does not establish executable spot spreads or commissions.
Splitting also resets ATR warmup and Monday discovery conservatively. A Monday
containing a conflict cannot anchor any signal, even if 240 other bars exist.
"""
from __future__ import annotations

import json
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_5m_wick_engulf as m
from yoyo.evaluation.range_framework import dump, file_stamp, runtime_versions

CODE = [*m.CODE, 'yoyo/evaluation/monday_5m_wick_gold.py',
        'tests/evaluation/test_monday_5m_wick_gold.py']


def clean_minutes(minutes):
    """Exclude ambiguous 5m intervals, without inferring quote order from prices."""
    minutes = minutes.sort_index()
    minutes.index = minutes.index.as_unit('ns')
    dup = minutes[minutes.index.duplicated(keep=False)]
    conflicts = dup.groupby(level=0).nunique().gt(1).any(axis=1)
    bad_minutes = conflicts.index[conflicts]
    bad_bars = bad_minutes.floor('5min').unique().sort_values()
    clean = minutes[~minutes.index.floor('5min').isin(bad_bars)]
    clean = clean[~clean.index.duplicated()]
    group = clean.resample('5min', closed='left', label='left')
    f = group.agg(dict(open='first', high='max', low='min', close='last')).dropna()
    f['count'] = group.close.count().reindex(f.index)
    f.index = f.index.as_unit('ns')
    # An interval at t separates bars before t from bars after t+5m.
    f['quality_segment'] = np.searchsorted(bad_bars.asi8, f.index.asi8, side='right')
    return f, dict(duplicate_rows=len(dup), conflicting_minutes=len(bad_minutes),
        excluded_five_minute_bars=len(bad_bars),
        conflicting_minute_times=[str(t) for t in bad_minutes],
        excluded_bar_times=[str(t) for t in bad_bars])


def load(paths):
    parts = []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith('.csv'))
            with archive.open(name) as handle:
                raw = pd.read_csv(handle, sep=';', header=None,
                                  names=['time', *m.OHLC, 'volume'])
        raw.index = m.histdata_index(raw.time)
        parts.append(raw[m.OHLC].astype(float))
    return clean_minutes(pd.concat(parts))


def invalid_monday_weeks(quality):
    ix = pd.to_datetime(quality['excluded_bar_times'], utc=True).tz_convert(m.TZ)
    return set(ix[ix.dayofweek == 0].strftime('%Y-%m-%d'))


def main():
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE], cwd=m.ROOT).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((m.EXP/'config.json').read_text())
    paths = next(paths for symbol, paths in m.jobs(cfg)[0] if symbol == 'XAUUSD')
    folder = m.EXP/'symbols/XAUUSD'; folder.mkdir(parents=True, exist_ok=True)
    identity = dict(config=cfg, inputs=[file_stamp(p) for p in paths],
                    code=[file_stamp(m.ROOT/p) for p in CODE], runtime=runtime_versions())
    if (folder/'done.json').exists():
        assert json.loads((folder/'identity.json').read_text()) == identity
        print((folder/'done.json').read_text()); return
    f, quality = load(paths)
    if not np.isfinite(f[m.OHLC]).all().all() or (f[m.OHLC] <= 0).any().any():
        raise ValueError('Invalid spot OHLC')
    if (f.high < f[m.OHLC].max(axis=1)).any() or (f.low > f[m.OHLC].min(axis=1)).any():
        raise ValueError('Invalid spot high/low')
    blocked = invalid_monday_weeks(quality)
    quality.update(invalid_monday_weeks=sorted(blocked),
        policy='Exclude whole conflicting5m intervals; split and censor; reject affected Monday anchors',
        limitations='Ordinary source closures/gaps retained; no verified spread/commission; Bid OHLC gross only')
    dump(folder/'data_quality.json', quality)
    all_rows = []; all_entries = []; receipts = []
    for segment, chunk in f.groupby('quality_segment', sort=True):
        entries, detail = m.candidates(chunk, 'XAUUSD', cfg)
        if len(entries):
            entries = entries[~entries.week.isin(blocked)].copy()
        if not len(entries): continue
        for col in ['trade_id', 'pair_id']:
            entries[col] = f'q{segment}-'+entries[col]
        dest = folder/f'segment-{segment}'; dest.mkdir(exist_ok=True)
        entries.to_csv(dest/'candidates.csv.gz', index=False)
        rows, audit = m.execute(chunk, entries, 'XAUUSD', dest, cfg)
        rows['quality_segment'] = int(segment)
        all_rows.append(rows); all_entries.append(entries)
        receipt = dict(quality_segment=int(segment), first=str(chunk.index[0]), last=str(chunk.index[-1]),
            signals=int(entries.population.eq('signal').sum()), positions=audit['positions'], **detail)
        receipts.append(receipt); print(json.dumps(receipt), flush=True)
    if not all_rows: raise ValueError('No valid spot signals')
    rows = pd.concat(all_rows, ignore_index=True)
    entries = pd.concat(all_entries, ignore_index=True)
    assert not rows.trade_id.duplicated().any()
    rows.to_csv(folder/'trades.csv.gz', index=False)
    entries.to_csv(folder/'candidates.csv.gz', index=False)
    dump(folder/'audit.json', dict(segments=receipts, positions=len(rows),
        oracle_checked=len(rows), runtime=runtime_versions(),
        censored_signals=int((rows.population.eq('signal') & ~rows.closed).sum())))
    done = dict(symbol='XAUUSD', bars=len(f), first=str(f.index[0]), last=str(f.index[-1]),
        signals=int(entries.population.eq('signal').sum()), positions=len(rows),
        rejected_geometry=sum(r['rejected_geometry'] for r in receipts),
        unmatched=sum(r['unmatched'] for r in receipts), quality_segments=f.quality_segment.nunique())
    done['quality_segments'] = int(done['quality_segments'])
    dump(folder/'identity.json', identity); dump(folder/'done.json', done)
    print(json.dumps(done), flush=True)


if __name__ == '__main__': main()
