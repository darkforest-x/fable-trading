"""Build identical, contiguous 7-day OKX paths for the three swing horizons.

Signal eligibility uses the original closed 5m bars and the executable next
open. Control matching uses the 12 returns ending before each candidate open.
Future candles are only checked for measurement coverage, never for direction.
"""
from collections import Counter
import json
import os

import numpy as np
import pandas as pd

from prepare import BAR, CACHE, CFG, HERE, PARENT, ROOT

LENGTH = 2016
END = pd.Timestamp(CFG['end_exclusive_utc']).value // 1_000_000
ARCHIVE = ROOT / 'data/research/month-first-week-okx-archive5m-v2/bars'
OLD = HERE.parent / 'exp-spike-okx-midnight-rank-20260915-v1/inputs'


def load_months(symbol, months, receipt):
    pieces = []
    for month in sorted(months):
        current = ARCHIVE / symbol / (str(month) + '.npz')
        old = OLD / (symbol + '-USDT-SWAP') / current.name
        path = current if current.is_file() else old
        if not path.is_file():
            receipt['missing_month_files'] += 1
            continue
        with np.load(path, allow_pickle=False) as z:
            bars = z['bar5'][:, :5]
        receipt['local_month_files' if path == current else 'historical_month_files'] += 1
        pieces.append(bars)
    if not pieces:
        return np.empty((0,5))
    bars = np.concatenate(pieces)
    if len(bars) and (np.any(np.diff(bars[:,0]) <= 0) or np.any(bars[:,0] % BAR)):
        raise ValueError(f'invalid or duplicated time grid for {symbol}')
    return bars


def main():
    candidates = pd.read_csv(HERE / 'entry_candidates.csv.gz')
    upper = candidates[candidates.rule.eq('upper_reclaim')]
    roots = candidates[candidates.rule.eq('touch_close')].set_index('event_id').entry_idx - 1
    meta = pd.read_csv(PARENT / 'event_paths.csv.gz', usecols=['event_id','month','ready_utc_derived'])
    rows = upper.merge(meta, on='event_id', validate='one_to_one')
    source = np.load(CACHE / 'windows.npy', mmap_mode='r')
    count = Counter(upper_candidates=len(rows))
    eligible = ~rows.entry_after_near & (rows.entry_open * 0.9995 > rows.target_price)
    count['already_reached_or_5bp_gap'] = int((~eligible).sum())
    windows, roster = [], []
    for symbol, group in rows[eligible].groupby('symbol', sort=True):
        months = {p + n for s in group.month for p in [pd.Period(s,'M')] for n in (0,1)}
        bars = load_months(symbol, months, count)
        ts = bars[:,0].astype('int64')
        if not len(ts):
            count['no_archive'] += len(group)
            continue
        future = np.zeros(len(ts), dtype=bool)
        past = future.copy()
        if len(ts) >= LENGTH:
            future[:len(ts)-LENGTH+1] = ts[LENGTH-1:]-ts[:len(ts)-LENGTH+1] == (LENGTH-1)*BAR
        future &= ts + LENGTH*BAR <= END
        if len(ts)>13:
            past[13:] = ts[13:]-ts[:-13] == 13*BAR
        vol = pd.Series(np.log(bars[:,4])).diff().rolling(12).std(ddof=0).shift(1).to_numpy()
        for r in group.itertuples(index=False):
            i = int(ts.searchsorted(r.entry_ms))
            if i >= len(ts) or ts[i] != r.entry_ms or not future[i]:
                count['missing_contiguous_7d_or_end'] += 1
                continue
            root = roots.at[r.event_id]
            original = source[r.grid_idx, root:r.entry_idx]
            before = bars[max(0,i-len(original)):i]
            if original.shape != before.shape or not np.array_equal(original[:,0], before[:,0]) or not np.allclose(original[:,1:], before[:,1:], rtol=1e-10, atol=1e-12) or not np.isclose(r.entry_open, bars[i,1], rtol=1e-10, atol=1e-12):
                count['source_price_or_clock_disagreement'] += 1
                continue
            ready = pd.Timestamp(r.ready_utc_derived).value // 1_000_000
            ix = np.arange(ts.searchsorted(ready), ts.searchsorted(r.month_end_ms))
            ix = ix[(np.abs(ts[ix]-ts[i]) >= LENGTH*BAR) & future[ix] & past[ix] & (ts[ix] % 3600000 == ts[i] % 3600000)]
            match = None
            if len(ix) and past[i] and vol[i] > 0:
                ix = ix[(vol[ix] >= vol[i]*.5) & (vol[ix] <= vol[i]*2)]
                if len(ix):
                    match = ix[np.argmin(np.abs(np.log(vol[ix]/vol[i])))]
            for kind,j in [('signal', i), ('placebo', match)]:
                if j is None:
                    continue
                key = r.event_id if kind=='signal' else r.event_id + '|control'
                roster.append(dict(window_idx=len(windows),window_key=key,event_id=r.event_id,
                    parent_event_id=r.event_id,candidate_id=r.candidate_id if kind=='signal' else r.candidate_id+'|control',
                    symbol=symbol,side='upper',target_price=r.target_price * (bars[j,1]/bars[i,1]),
                    entry_ms=int(ts[j]),entry_open=bars[j,1],kind=kind,matched=match is not None,entry_after_near=False))
                windows.append(bars[j:j+LENGTH].copy())
    out = pd.DataFrame(roster)
    assert not out.empty and out.window_key.is_unique
    tmp = CACHE / 'swing_windows.tmp.npy'
    np.save(tmp,np.stack(windows))
    os.replace(tmp, CACHE/'swing_windows.npy')
    out.to_csv(HERE/'swing_roster.csv.gz', index=False)
    out.to_csv(CACHE/'swing_roster.csv.gz', index=False)
    count.update(out.kind.value_counts().astype(int).to_dict())
    assert sum(count[k] for k in ('already_reached_or_5bp_gap','no_archive','missing_contiguous_7d_or_end','source_price_or_clock_disagreement','signal')) == count['upper_candidates']
    (HERE/'swing_coverage.json').write_text(json.dumps(dict(count),indent=2)+'\n')
    print(dict(count), flush=True)


if __name__ == '__main__':
    main()
