"""Official OKX archive inputs for the owner's midnight early-leader study.

Ranking features use only the first N complete 1m bars of a UTC+8 day.
Forward prices are labels, never universe or ranking eligibility. Complete
3m/5m aggregates retain full precision for later frozen V1/V8 replay. Named
archive months are checked before downloading; no May2026/holdout is read.
Raw ZIPs are processed in memory and bound by SHA rather than retained twice.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import time
import zipfile

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-okx-midnight-rank-20260915-v1'
MINUTE = 60_000
DAY = 1440 * MINUTE
OFFSET = 480 * MINUTE


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    tmp.replace(path)


def month_bounds(month):
    left = pd.Timestamp(month + '-01', tz='Asia/Shanghai').tz_convert('UTC')
    right = left.tz_convert('Asia/Shanghai') + pd.offsets.MonthBegin(1)
    if right.tz_convert('UTC') > pd.Timestamp('2026-05-01T00:00Z'):
        raise ValueError('archive month would expose restricted prices')
    return int(left.value // 1_000_000), int(right.value // 1_000_000)


def parse_archive(payload, symbol, month):
    """Validate exact instrument, UTC+8 month, confirmation and price geometry."""
    left, right = month_bounds(month)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [x for x in archive.namelist() if x.endswith('.csv')]
        if len(names) != 1: raise ValueError('archive must have one CSV')
        raw = archive.read(names[0])
    f = pd.read_csv(io.BytesIO(raw))
    required = ['open_time', 'open', 'high', 'low', 'close', 'vol']
    if not set(required + ['instrument_name', 'confirm']).issubset(f):
        raise ValueError('archive schema changed')
    if set(f.instrument_name.astype(str)) != {symbol}: raise ValueError('instrument drift')
    if not pd.to_numeric(f['confirm']).eq(1).all(): raise ValueError('unconfirmed archive bar')
    x = f[required].apply(pd.to_numeric, errors='raise').sort_values('open_time')
    duplicates = x[x.open_time.duplicated(keep=False)]
    if any(len(g.drop_duplicates()) != 1 for _, g in duplicates.groupby('open_time')):
        raise ValueError('conflicting duplicate minute')
    before = len(x); x = x.drop_duplicates('open_time').reset_index(drop=True)
    a = x.to_numpy(float)
    if not len(x) or not np.isfinite(a).all(): raise ValueError('empty/nonfinite archive')
    if ((a[:, 0] < left) | (a[:, 0] >= right) | (a[:, 0] % MINUTE != 0)).any():
        raise ValueError('out-of-month or off-grid archive bar')
    if (a[:, 1:5] <= 0).any() or (a[:, 5] < 0).any(): raise ValueError('invalid prices/volume')
    if (a[:, 2] < np.max(a[:, [1, 3, 4]], axis=1)).any(): raise ValueError('invalid high')
    if (a[:, 3] > np.min(a[:, [1, 2, 4]], axis=1)).any(): raise ValueError('invalid low')
    return a, dict(zip_sha256=hashlib.sha256(payload).hexdigest(),
                  csv_sha256=hashlib.sha256(raw).hexdigest(), minute_rows=len(a),
                  exact_duplicates_dropped=before-len(a),
                  missing_minutes=int((right-left)//MINUTE-len(a)),
                  first_ms=int(a[0, 0]), last_ms=int(a[-1, 0]))


def aggregate(a, minutes):
    """Emit only exactly complete UTC buckets; no gap fill or future borrowing."""
    f = pd.DataFrame(a, columns=['ts','open','high','low','close','volume'])
    f['bucket'] = (f.ts.astype('int64') // (MINUTE*minutes)) * (MINUTE*minutes)
    g = f.groupby('bucket', sort=True)
    out = g.agg(open=('open','first'), high=('high','max'), low=('low','min'),
                close=('close','last'), volume=('volume','sum'), count=('ts','size'),
                first=('ts','min'), last=('ts','max'))
    keep = (out['count'].eq(minutes) & out['first'].eq(out.index)
            & (out['last']-out['first']).eq((minutes-1)*MINUTE))
    return out.loc[keep, ['open','high','low','close','volume']].reset_index().to_numpy(float)


def daily_rows(a, ns, horizons):
    """First-N-minute features; target entry is minute N+1, strictly after rank.

    Reads rank return, high-low range and volume only from [00:00,00:N).
    Forward open-to-open returns/high-low excursions are retrospective labels.
    Missing future bars do not delete a rankable symbol-day.
    """
    days = ((a[:, 0].astype('int64') + OFFSET) // DAY) * DAY - OFFSET
    rows = []
    for day in np.unique(days):
        v = a[days == day]; offset = ((v[:, 0]-day)//MINUTE).astype(int)
        by = {int(i): row for i, row in zip(offset, v)}
        for n in ns:
            if not all(i in by for i in range(n)): continue
            early = np.array([by[i] for i in range(n)])
            start = float(early[0, 1]); end = float(early[-1, 4])
            row = dict(day_ms=int(day), n=n, rank_available_ms=int(day+n*MINUTE),
                       rank_return=end/start-1, rank_range=(early[:, 2].max()-early[:, 3].min())/start,
                       rank_volume=float(early[:, 5].sum()), rank_start_price=start,
                       rank_end_price=end, entry_ms=int(day+(n+1)*MINUTE))
            ent = by.get(n+1)
            row['entry_price'] = float(ent[1]) if ent is not None else np.nan
            for h in horizons:
                last = 1440 if h == 'day' else n+1+int(h)
                complete = ent is not None and all(i in by for i in range(n+1, last))
                ep = by[1439][4] if last == 1440 and 1439 in by else by.get(last, [0,np.nan])[1]
                valid = complete and np.isfinite(ep)
                held = np.array([by[i] for i in range(n+1, last)]) if valid else None
                row['gross_'+str(h)] = ep/ent[1]-1 if valid else np.nan
                row['mfe_'+str(h)] = held[:, 2].max()/ent[1]-1 if valid else np.nan
                row['mae_'+str(h)] = held[:, 3].min()/ent[1]-1 if valid else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def catalog():
    """Freeze current and previously observed symbols, not a historical census."""
    cfg = json.loads((EXP/'config.json').read_text())
    source = ROOT/cfg['catalog_source']; raw = json.loads(source.read_text())
    names = {x['symbol']: dict(symbol=x['symbol'], tick=x.get('tick'),
              listing_ms=x.get('listing_ms',0), delisting_ms=x.get('delisting_ms',0),
              origin='frozen_current_catalog') for x in raw if x.get('venue')=='okx' and x.get('eligible')}
    for p in (ROOT/'data').glob('kline*/*okx_*_USDT_SWAP_*.csv'):
        m = re.match(r'okx_(.+_USDT_SWAP)_(?:\d+[mh]|\d+H)_', p.name)
        if m:
            symbol = m[1].replace('_','-')
            if symbol not in names:
                names[symbol] = dict(symbol=symbol,tick=None,listing_ms=0,delisting_ms=0,
                                     origin='historical_local_filename',source_path=str(p.relative_to(ROOT)))
    # Listing metadata is retained for audit; archive probes, not current liveness,
    # determine historical availability. Do not drop delisted names.
    save(EXP/'universe.json', dict(catalog_source=str(source.relative_to(ROOT)),
         catalog_sha256=sha(source), symbols=sorted(names.values(),key=lambda r:r['symbol']),
         point_in_time_census=False, holdout_consumed=False))
    print('catalog',len(names),flush=True)


def fetch_one(symbol, month, cfg):
    month_bounds(month)
    folder = EXP/'inputs'/symbol; folder.mkdir(parents=True,exist_ok=True)
    receipt = folder/(month+'.json'); npz=folder/(month+'.npz'); daily=folder/(month+'.daily.csv.gz')
    identity=dict(config_sha256=sha(EXP/'config.json'),builder_sha256=sha(__file__))
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if any(old.get(k)!=v for k,v in identity.items()): raise ValueError('input contract drift')
        if old['status']=='missing': return old
        if old['status']=='complete' and all(sha(folder/k)==v for k,v in old['files'].items()): return old
        if old['status']=='complete': raise ValueError('input cache drift')
    url=('https://static.okx.com/cdn/okex/traderecords/candlesticks/monthly/'
         +month.replace('-','')+'/'+symbol+'-candlesticks-'+month+'.zip?v=999')
    result=dict(symbol=symbol,month=month,url=url,holdout_consumed=False,**identity)
    for attempt in range(3):
        try:
            r=requests.get(url,timeout=(10,35),headers={'User-Agent':'Mozilla/5.0'})
            if r.status_code==404:
                result.update(status='missing',http_status=404);save(receipt,result);return result
            r.raise_for_status(); a,audit=parse_archive(r.content,symbol,month)
            with npz.with_suffix('.tmp').open('wb') as f:
                np.savez_compressed(f,bar3=aggregate(a,3),bar5=aggregate(a,5))
            npz.with_suffix('.tmp').replace(npz)
            daily_rows(a,cfg['rank_minutes'],cfg['forward_minutes']).to_csv(daily,index=False,
                         compression={'method':'gzip','mtime':0})
            result.update(status='complete',**audit,files={p.name:sha(p) for p in [npz,daily]})
            save(receipt,result);return result
        except Exception as e:
            error=type(e).__name__+': '+str(e)
            if attempt<2: time.sleep(attempt+1)
    result.update(status='error',error=error);save(receipt,result);return result


def fetch(phase, workers, limit):
    cfg=json.loads((EXP/'config.json').read_text()); universe=json.loads((EXP/'universe.json').read_text())
    if phase=='evaluate':
        choice=EXP/'results/selection.json'
        subprocess.run(['git','diff','--exit-code','HEAD','--',str(choice.relative_to(ROOT))],cwd=ROOT,check=True)
        subprocess.run(['git','cat-file','-e','HEAD:'+str(choice.relative_to(ROOT))],cwd=ROOT,check=True)
    first,last=cfg['archive_months'][phase]
    months=pd.date_range(first+'-01',last+'-01',freq='MS').strftime('%Y-%m').tolist()
    symbols=universe['symbols'][:limit] if limit else universe['symbols']
    jobs=[(r['symbol'],m) for r in symbols for m in months]
    # Probe every named coin/month. Current listing dates can describe relistings,
    # and therefore are not permission to erase an earlier archived instrument.
    save(EXP/('fetch_'+phase+'_plan.json'),dict(jobs=len(jobs),symbols=len(symbols),months=months,
         config_sha256=sha(EXP/'config.json'),universe_sha256=sha(EXP/'universe.json')))
    total=0; counts={}; started=time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(fetch_one,s,m,cfg):(s,m) for s,m in jobs}
        for f in as_completed(futures):
            r=f.result(); counts[r['status']]=counts.get(r['status'],0)+1;total+=1
            if total%50==0 or total==len(jobs):
                print(phase,total,'/',len(jobs),counts,'seconds',round(time.monotonic()-started),flush=True)
                save(EXP/('fetch_'+phase+'_progress.json'),dict(done=total,total=len(jobs),counts=counts))
    save(EXP/('fetch_'+phase+'_complete.json'),dict(done=total,total=len(jobs),counts=counts,
         seconds=time.monotonic()-started,holdout_consumed=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['catalog','develop','evaluate'])
    p.add_argument('--workers',type=int,default=6);p.add_argument('--limit',type=int)
    args=p.parse_args()
    for rel in ['yoyo/data/okx_midnight_rank.py',str((EXP/'config.json').relative_to(ROOT))]:
        subprocess.run(['git','cat-file','-e','HEAD:'+rel],cwd=ROOT,check=True)
        subprocess.run(['git','diff','--exit-code','HEAD','--',rel],cwd=ROOT,check=True)
    catalog() if args.phase=='catalog' else fetch(args.phase,args.workers,args.limit)
