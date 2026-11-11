"""12h-only descriptive search. Native hour ranges resolve all calendar boundaries.
Complete quarter-hour groups supplement any gaps in the hourly archive.
The returned touch timestamp denotes the beginning of its source candle.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from scan import DATA, ROOT, aggregate, scan

BASE = DATA.parent
OUT = BASE.parent / 'results12'
HOUR = 3_600_000


def join(parts):
    if not parts:
        return np.empty((0, 5))
    raw = np.concatenate(parts[::-1])
    _, ix = np.unique(raw[:, 0], return_index=True)
    return raw[ix]


def source_paths(symbol):
    return [BASE / d / (symbol+'.npy') for d in ('hourly12input', 'hourly12tail', 'series', 'archive_fill')]


@lru_cache(maxsize=1)
def existing_quotes():
    stores = [
        ROOT/'experiments/active/exp-cloud-close-binance-transfer-20261109-v1/work',
        ROOT/'data/research/cloud_close_binance_feb2022_replay',
        ROOT/'data/research/cloud_close_archive_cohort/symbols',
        ROOT/'data/research/cloud_close_november_supplemental/symbols',
    ]
    return {p.parent.name:p for store in stores for p in store.glob('*/quotes.npz')}


def read_raw(symbol):
    hist, tail, *quarter = source_paths(symbol)
    pieces = [np.load(p) for p in (hist, tail) if p.exists()]
    if not hist.exists() and symbol in existing_quotes():
        with np.load(existing_quotes()[symbol]) as quotes:
            pieces.insert(0,np.column_stack((quotes['clock'],quotes['prices'])))
    quarters = join([np.load(p) for p in quarter if p.exists()])
    if len(quarters):
        aligned = quarters[:,0].astype('int64')//HOUR
        ids, start, counts = np.unique(aligned, return_index=True, return_counts=True)
        good = counts == 4
        ix = start[good, None] + np.arange(4)
        prices = aggregate(quarters[ix,1:5].reshape(-1,4),4)
        pieces.insert(0, np.column_stack((ids[good]*HOUR, prices)))
    return join(pieces)


def process(symbol):
    store = OUT/'symbols'/symbol
    store.mkdir(parents=True, exist_ok=True)
    sources = {p.parent.name:[p.stat().st_size,p.stat().st_mtime_ns] for p in source_paths(symbol) if p.is_file()}
    if 'hourly12input' not in sources and symbol in existing_quotes():
        p=existing_quotes()[symbol]
        sources['registered_quotes']=[p.stat().st_size,p.stat().st_mtime_ns]
    receipt = store/'done.json' 
    if receipt.is_file() and json.loads(receipt.read_text())['sources'] == sources:
        return
    raw = read_raw(symbol)
    events, coverage = scan(symbol,raw,slot_ms=HOUR,periods=(720,))
    for name, rows in [('events',events), ('coverage',coverage)]:
        dest = store/(name+'.csv.gz')
        tmp = dest.with_suffix('.tmp')
        pd.DataFrame(rows).to_csv(tmp,index=False,compression='gzip')
        tmp.replace(dest)
    meta = dict(sources=sources, rows=len(raw), first_ms=int(raw[0,0]) if len(raw) else None,
                last_ms=int(raw[-1,0]) if len(raw) else None)
    tmp=receipt.with_suffix('.tmp')
    tmp.write_text(json.dumps(meta))
    tmp.replace(receipt)


def read_group(name):
    parts=[]
    for p in (OUT/'symbols').glob('*/'+name+'.csv.gz'):
        try:
            parts.append(pd.read_csv(p))
        except pd.errors.EmptyDataError:
            continue
    return pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()


def summarize():
    events=read_group('events')
    if events.empty:
        return
    candidates=events[events.long_wick & (events.isolated | events.month_unique)].sort_values(['open_ms','symbol']).copy()
    for src, dest in [('open_ms','bar_open_Beijing'),('close_ms','bar_close_Beijing'),('touch_ms','touch_hour_Beijing'),('peak_ms','peak_hour_Beijing'),('first_20_ms','first_20pct_hour_Beijing')]:
        candidates[dest]=pd.to_datetime(candidates[src],unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    cols=['symbol','month','bar_open_Beijing','bar_close_Beijing','side','level','open','high','low','close','wick_pct','week_low','week_high','isolated','month_unique','month_complete','follow7','mfe3','mfe7','peak_price','peak_hour_Beijing','end7','first_20pct_hour_Beijing','touch_hour_Beijing','max_before_retouch','open_ms']
    candidates[cols].to_csv(OUT/'isolated_12h.csv',index=False)
    candidates[candidates.mfe7>=20][cols].to_csv(OUT/'reversals_20pct.csv',index=False)
    candidates[candidates.month_unique & (candidates.mfe7>=20)][cols].to_csv(OUT/'single_touch_month_20pct.csv',index=False)
    coverage=read_group('coverage')
    coverage.to_csv(OUT/'coverage_months.csv',index=False)
    valid=coverage[coverage.status=='valid']
    followed=candidates[candidates.follow7]
    out=dict(roster=len(pd.read_csv(BASE/'universe_eligible.csv')), read=len(list((OUT/'symbols').glob('*/done.json'))),
             valid_symbols=valid.symbol.nunique(),valid_months=len(valid),months=coverage.groupby('status').size().to_dict(),
             first_month=valid.month.min(),last_month=valid.month.max(),events=len(candidates),follow7=len(followed),
             success_symbols=followed[followed.mfe7>=20].symbol.nunique(),
             thresholds={k:int((followed.mfe7>=k).sum()) for k in (10,20,30)},
             local_follow7=int(followed.isolated.sum()),local_success=int((followed.isolated & (followed.mfe7>=20)).sum()),
             unique_follow7=int(followed.month_unique.sum()),
             month_unique_success=int((followed.month_unique & (followed.mfe7>=20)).sum()),
             success_by_side=followed[followed.mfe7>=20].side.value_counts().to_dict())
    (OUT/'summary.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--symbols',nargs='*')
    args=parser.parse_args()
    roster=set(pd.read_csv(BASE/'universe_eligible.csv').symbol)
    available={p.stem for d in ('hourly12input','hourly12tail','series','archive_fill') for p in (BASE/d).glob('*.npy')}
    targets=sorted(set(args.symbols or (available | set(existing_quotes())))&roster)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(process,targets))
    summarize()
