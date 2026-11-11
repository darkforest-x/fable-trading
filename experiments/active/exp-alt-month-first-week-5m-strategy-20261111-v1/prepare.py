"""Construct causal entry candidates from the validated OKX first-touch ledger.

Signal features are the frozen outer/near rails and selected 5m OHLC up to the
signal close. Placebo volatility uses exactly 12 already-closed returns.
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CFG = json.loads((HERE / 'config.json').read_text())
PARENT = HERE.parent / CFG['source']
CACHE = ROOT / 'data/research/month-first-week-5m-strategy-20261111-v1'
sys.path.insert(0, str(PARENT))
from fetch_windows import _cache_path

BAR = 300_000
PRICE_COLS = ('ts_ms','open','high','low','close')


def read_window(event):
    native = event.primary_5m_source == 'okx_native_5m'
    archive = ROOT / 'data/research/month-first-week-okx-archive5m-v2/windows'
    file = _cache_path(event.event_id) if native else archive / event.symbol / f'{event.month}-{event.side}.json.gz'
    with gzip.open(file,'rt') as f:
        payload = json.load(f)
    if payload['event_id'] != event.event_id:
        raise ValueError('wrong event in candle cache')
    if native:
        prices = [[float(b[k]) for k in PRICE_COLS] for b in payload['candles']]
    else:
        prices = [b[:5] for b in payload['candles']]
    data = np.array(prices, dtype='float64')
    hour = pd.Timestamp(event.touch).value // 1_000_000
    grid = np.full((240,5), np.nan)
    grid[:,0] = hour + np.arange(-120,120)*BAR
    idx = ((data[:,0]-grid[0,0])/BAR).astype(int)
    if not np.array_equal(grid[idx,0], data[:,0]) or np.unique(idx).size != len(idx):
        raise ValueError('bad five-minute grid')
    grid[idx,1:] = data[:,1:]
    return grid


def entries(event, a, root):
    """All decisions use the signal bar inclusive, with execution on the following open."""
    upper = event.side == 'upper'
    level = event.level
    window = a[root:root+25]
    close, highs, lows = window[:,4], window[:,2], window[:,3]
    inside = close < level if upper else close > level
    outside = close > level if upper else close < level
    touch = highs >= level if upper else lows <= level
    if not touch[0]:
        raise ValueError(f'no first-touch candle for {event.event_id}')
    yield 'touch_close', 0
    idx = np.flatnonzero(inside[:6])
    if idx.size:
        first = idx[0]
        yield 'first_reclaim', first
        yield 'upper_reclaim' if upper else 'lower_reclaim', first
        yield 'same_bar' if first == 0 else 'delayed', first
        yield 'repeat_reclaim', first
        last, emitted = first, 1
        for i in range(first+1,len(window)):
            if i-last >= 3 and not inside[i-1] and inside[i]:
                yield 'repeat_reclaim', i
                emitted += 1
                last = i
                if emitted == 3:
                    break
        if first == 0:
            retest = np.flatnonzero(touch[1:13]) + 1
            if retest.size and inside[retest[0]]:
                yield 'first_retest_inside', retest[0]
    paired = np.flatnonzero(outside[:5] & outside[1:6])
    if paired.size:
        yield 'continue_outside', paired[0]+1


def match_clock(grid, vol, entry, root, ready, event_id):
    """Find a prior clock in the same event whose pre-entry 12 returns resemble the signal's."""
    indices = np.arange(13, root-36+1)
    if not len(indices) or not np.isfinite(vol[entry]) or vol[entry] <= 0:
        return -1
    indices = indices[(grid[indices,0]>=ready) & (vol[indices]>0) & np.isfinite(vol[indices])]
    ratios = vol[indices]/vol[entry]
    valid = (ratios>=0.5) & (ratios<=2)
    indices = indices[valid]
    if not len(indices):
        return -1
    difference = np.abs(np.log(ratios[valid]))
    best = indices[difference == difference.min()]
    tie = int.from_bytes(hashlib.blake2s(f'{event_id}|{entry}'.encode(),digest_size=4).digest(),'big')
    return int(best[tie % len(best)])


def build_inputs():
    source = pd.read_csv(PARENT/'event_paths.csv.gz',low_memory=False)
    valid = source[source.category.isin(['A','B','C'])].copy()
    grids, all_candidates = [], []
    for j,event in enumerate(valid.itertuples(index=False)):
        grid = read_window(event)
        grids.append(grid)
        t = int(pd.Timestamp(event.primary_first_touch_5m_utc).value//1_000_000)
        root = int((t-grid[0,0])//BAR)
        if not 120 <= root < 132:
            raise ValueError('unexpected touch root')
        ready = pd.Timestamp(event.ready_utc_derived).value//1_000_000
        ny_start = pd.Timestamp(event.month+'-01',tz='America/New_York')
        month_end = (ny_start+pd.offsets.MonthBegin(1)).value//1_000_000
        near = event.high if event.side=='upper' else event.low
        dlog = pd.Series(np.log(grid[:,4])).diff()
        # Entry occurs at index j; use returns through j-1.
        vol = dlog.rolling(12,min_periods=12).std(ddof=0).shift(1).to_numpy()
        controls = {}
        for rule, rel in entries(event,grid,root):
            entry = int(root+rel+1)
            if not np.isfinite(grid[root:entry+1,1:]).all():
                continue
            if entry not in controls:
                controls[entry] = match_clock(grid,vol,entry,root,ready,event.event_id)
            before = grid[root:entry]
            upper = event.side=='upper'
            edge_hit = bool(np.any(before[:,3] <= near) or grid[entry,1] <= near) if upper else bool(np.any(before[:,2] >= near) or grid[entry,1] >= near)
            side = event.side if rule!='continue_outside' else ('lower' if upper else 'upper')
            all_candidates.append(dict(event_id=event.event_id,symbol=event.symbol,entry_idx=entry,
                candidate_id=f'{event.event_id}/{rule}/{entry}',rule=rule,side=side,rail_side=event.side,
                target_price=near,entry_open=grid[entry,1],entry_ms=int(grid[entry,0]),month_end_ms=month_end,
                entry_after_near=edge_hit,control_idx=controls[entry],grid_idx=j))
    CACHE.mkdir(parents=True,exist_ok=True)
    np.save(CACHE/'windows.npy',np.stack(grids))
    valid[['event_id','symbol','month','side']].to_csv(HERE/'source_events.csv.gz',index=False)
    c=pd.DataFrame(all_candidates)
    if not c.candidate_id.is_unique:
        raise ValueError('duplicate signal candidate ids')
    c.to_csv(HERE/'entry_candidates.csv.gz',index=False)
    print('Prepared', len(valid), 'events;',c.rule.value_counts().to_dict(),flush=True)


if __name__=='__main__':
    build_inputs()
