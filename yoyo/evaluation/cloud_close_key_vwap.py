"""Rank native confirmed 12H reclaims by the executed-volume weighted price of the closed key."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_funding_rank as rank
from yoyo.evaluation import cloud_close_taker_coverage as coverage

EXP = rank.EXP.parent/'exp-cloud-close-key-vwap-20261109-v1'
ROOT = rank.admin.archive.native.wallet.ROOT
KEY = coverage.full.KEY
HOUR = coverage.full.HOUR
KEY_SOURCE = EXP/'signal_vwap.csv.gz'


def raw_archive(path):
    cols = ['time','open','high','low','close','base','quote']
    bars = pd.read_csv(path,header=None,usecols=[0,1,2,3,4,5,7],names=cols,low_memory=False)
    if str(bars.time.iloc[0]).lower() in ('open_time','open time'):
        bars = bars.iloc[1:]
    bars = bars.astype({k: ('int64' if k=='time' else 'float64') for k in cols})
    if not bars.time.is_monotonic_increasing or not bars.time.is_unique:
        raise ValueError(f'Duplicate or unordered original volume archive {path}')
    return bars.set_index('time')


def measure(close, minutes, raw, clock, canonical):
    """Use only exact native bars in [close-12h,close): OHLC, base volume and quote turnover."""
    times = np.arange(close-KEY,close,minutes*60_000)
    idx = raw.index.get_indexer(times)
    if (idx<0).any():
        return None
    a = raw.to_numpy()[idx].reshape(12,60//minutes,6)
    h = np.arange(close-KEY,close,HOUR)
    j = clock.searchsorted(h)
    if (j>=len(clock)).any() or not np.array_equal(clock[j],h):
        return None
    prices = np.column_stack((a[:,0,0],a[:,:,1].max(axis=1),a[:,:,2].min(axis=1),a[:,-1,3]))
    if not np.allclose(prices,canonical[j],rtol=1e-9,atol=1.e-15):
        return None
    if not np.isfinite(a).all() or (a[:,:,4:]<0).any() or np.any((a[:,:,4]==0)!=(a[:,:,5]==0)):
        return None
    low, high = prices[:,2].min(), prices[:,1].max()
    base, quote = a[:,:,4:].sum(axis=(0,1))
    if high <= low or base<=0:
        return None
    vwap = quote / base
    # The exchange rounds quote turnover. Only accept a whole-key VWAP within the traded range.
    if not low*(1-1.e-8) <= vwap <= high*(1+1.e-8):
        return None
    return float((prices[-1,3]-vwap)/(high-low)), float(vwap)


def symbol_keys(job):
    symbol, keys = job
    ticker = symbol+'USDT'
    archive = rank.admin.archive
    clock,canonical = archive.native.quotes(symbol)
    result = []
    for month, group in keys.groupby('month'):
        pending = set(group.entry_ms)
        preferred = {Path(s) for s in group.source.dropna()}
        monthly = [root/ticker/f'{ticker}-{m}m-{month}.zip' for m,root in coverage.full.ARCHIVES]
        monthly += [archive.CACHE/'monthly'/ticker/f'{ticker}-15m-{month}.zip',
                    archive.PREVIOUS/ticker/f'{ticker}-15m-{month}.zip',
                    coverage.CACHE/ticker/f'{ticker}-15m-{month}.zip']
        for path in dict.fromkeys([ROOT/s for s in sorted(preferred)]+monthly):
            if not pending:
                break
            if not path.is_file():
                continue
            minutes = int(path.name.removeprefix(ticker+'-').split('m-',1)[0])
            raw = raw_archive(path)
            for close in sorted(pending):
                numbers = measure(close,minutes,raw,clock,canonical)
                if numbers is not None:
                    result.append((symbol,close,*numbers,str(path.relative_to(ROOT))))
                    pending.remove(close)
        result.extend((symbol,close,np.nan,np.nan,None) for close in sorted(pending))
    return result


def source():
    all_keys = pd.read_csv(rank.scores.EXP/'candidate_order.csv.gz',usecols=['event_id','symbol','entry_ms'],dtype={'symbol':str})
    old = pd.read_csv(coverage.full.EXP/'event_flow_screen.csv.gz',usecols=['symbol','entry_ms','source','is_control'],dtype={'symbol':str})
    old = old[~old.is_control].drop(columns='is_control')
    recovered = pd.read_csv(coverage.EXP/'recovered_native_keys.csv.gz',usecols=['symbol','entry_ms','new_source'],dtype={'symbol':str})
    old = old.merge(recovered,on=['symbol','entry_ms'],how='left',validate='many_to_one')
    old['source'] = old.new_source.fillna(old.source)
    keys = all_keys.merge(old[['symbol','entry_ms','source']],on=['symbol','entry_ms'],how='left',validate='one_to_one')
    keys['month'] = pd.to_datetime(keys.entry_ms-KEY,unit='ms',utc=True).dt.strftime('%Y-%m')
    archive = rank.admin.archive
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((rank.admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    output = []
    with archive.market_quotes(extra), ThreadPoolExecutor(max_workers=4) as pool:
        for i, records in enumerate(pool.map(symbol_keys,keys.groupby('symbol')),1):
            output.extend(records)
            if i%100==0:
                print('Read traded base and quote volumes for',i,'contracts',flush=True)
    raw = pd.DataFrame(output,columns=['symbol','entry_ms','distance','vwap','source'])
    events = all_keys.merge(raw,on=['symbol','entry_ms'],validate='one_to_one')
    if len(events)!=23262 or not events.event_id.is_unique:
        raise ValueError('Native roster differs from frozen market-lifetime corrected account')
    events['side'] = events.event_id.str.rsplit('|',n=1).str[-1].astype(int)
    events['score'] = events.distance * events.side
    events.to_csv(KEY_SOURCE,index=False)
    print('VWAP coverage',events.score.count(),len(events),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',action='store_true')
    args = parser.parse_args()
    if args.source:
        if KEY_SOURCE.exists():
            raise FileExistsError('Frozen volume inputs already exist')
        source()
        return
    if (EXP/'summary.json').exists():
        raise FileExistsError('VWAP priority account already evaluated')
    panel = pd.read_csv(KEY_SOURCE,usecols=['event_id','score']).set_index('event_id').score
    rank.compare(panel,EXP,'signed_vwap_range_distance')


if __name__=='__main__':
    main()
