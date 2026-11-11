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
    replay=candidates[candidates.follow7].copy()
    replay['local_flag']=replay.isolated.map({True:'是',False:'否或窗口不足'})
    replay['month_flag']=np.where(replay.month_unique,'是',np.where(replay.month_complete,'否','月份未走完'))
    replay['side']=replay.side.map({'lower':'下轨','upper':'上轨'})
    replay['tip']=replay.low.where(replay.side=='下轨',replay.high)
    mapping={'symbol':'币安合约','month':'区间所属月（纽约）','bar_open_Beijing':'12小时开盘（北京）',
             'bar_close_Beijing':'12小时收盘（北京）','side':'触及方向','level':'1.618轨价',
             'tip':'针尖价格','close':'收盘价','wick_pct':'触轨影线占整根百分比',
             'local_flag':'前后各两根均未碰同轨','month_flag':'整月只有一根碰同轨','max_before_retouch':'7天内再碰轨前最大反向幅度百分比',
             'mfe7':'7天最大反向幅度百分比','peak_price':'7天最有利价格',
             'end7':'第7天收盘反向涨跌百分比','first_20pct_hour_Beijing':'首次达到20%的小时（北京）'}
    for name,frame in [('单针形态_全部',replay),('单针形态_7天反转至少20pct',replay[replay.mfe7>=20]),
                       ('邻近单针_再碰轨前已反转20pct',replay[replay.isolated & (replay.max_before_retouch>=20)]),
                       ('整月单针_全部',replay[replay.month_unique]),('整月单针_7天反转至少20pct',replay[replay.month_unique & (replay.mfe7>=20)])]:
        frame[list(mapping)].rename(columns=mapping).to_csv(OUT/(name+'.csv'),index=False,encoding='utf-8-sig')
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
    strict=followed[followed.month_unique & (followed.mfe7>=20)]
    unbroken=followed[followed.isolated & (followed.max_before_retouch>=20)]
    out['local_success_before_retouch']=len(unbroken)
    out['local_success_before_retouch_symbols']=unbroken.symbol.nunique()
    out['local_success_before_retouch_by_side']=unbroken.side.value_counts().to_dict()
    out['strict_success_symbols']=strict.symbol.nunique()
    out['strict_success_by_side']=strict.side.value_counts().to_dict()
    valid_counts=valid.groupby('symbol').size()
    checklist=pd.read_csv(BASE/'universe_eligible.csv')[['symbol','basis','status']].copy()
    checklist['valid_months']=checklist.symbol.map(valid_counts).fillna(0).astype(int)
    checklist['passed_20pct']=checklist.symbol.map(followed[followed.mfe7>=20].groupby('symbol').size()).fillna(0).astype(int)
    checklist['strict_20pct']=checklist.symbol.map(strict.groupby('symbol').size()).fillna(0).astype(int)
    checklist.to_csv(OUT/'contract_coverage.csv',index=False)
    out['no_valid_months']=checklist.loc[checklist.valid_months.eq(0),'symbol'].tolist()
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
