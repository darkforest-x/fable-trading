"""Recover the last completed H1's native quote turnover for every actual 12H applicant."""
import gzip
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_allmarket_inputs as inputs
from yoyo.evaluation import cloud_close_adverse_btc_rank as candidates
from yoyo.evaluation import cloud_close_okx_roster_audit as archives

repair = candidates.repair
wallet = repair.wallet
EXP = candidates.EXP.parent/'exp-cloud-close-liquid-priority-inputs-20261109-v1'
FIELDS = ['open','high','low','close']


def instrument(sym,parents,quotes,db):
    inst = sym+'-USDT-SWAP'
    ts,ohlc = quotes(sym)
    wanted = set((parents.entry_ms-wallet.HOUR).astype(int))
    location = {t:int(ts.searchsorted(t)) for t in wanted}
    records = {}
    def take(t,row,value,source):
        if t not in wanted or t in records:
            return
        idx = location[t]
        if idx==len(ts) or ts[idx]!=t or not np.isfinite(value) or value<0:
            return
        if np.allclose(ohlc[idx],row,rtol=1e-9,atol=1e-15):
            records[t] = (value,source)
    def raw(row,source):
        if len(row)>=9 and str(row[8])=='1' and row[7] not in ('',None):
            take(int(row[0]),[float(v) for v in row[1:5]],float(row[7]),source)
    times = sorted(wanted)
    for i in range(0,len(times),250):
        part = times[i:i+250]
        marks = ','.join('?' for _ in part)
        query = f'SELECT raw FROM candles WHERE key=? AND t IN ({marks})'
        for (row,) in db.execute(query,[f'https://www.okx.com|{inst}|1H',*part]):
            raw(json.loads(row),'shared_OKX_1H')
    for folder,pattern in [(wallet.TOP/'inputs'/sym,'1H-*.json.gz'),(wallet.SOURCE/'inputs'/sym/'raw/1H','*.json.gz')]:
        for file in sorted(folder.glob(pattern)) if len(records)<len(wanted) else []:
            with gzip.open(file,'rt') as fh:
                for row in json.load(fh):
                    if int(row[0]) in wanted:
                        raw(row,'archived_OKX_1H')
    missing = sorted(wanted-records.keys())
    months = {pd.Timestamp(t,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y-%m') for t in missing}
    for month in sorted(months):
        filename = f'{inst}-candlesticks-{month}.zip'
        file = archives.CACHE/'raw'/inst/filename
        if not file.exists():
            continue
        data = pd.read_csv(file,usecols=['instrument_name','open_time','confirm','vol_quote',*FIELDS])
        if not data.instrument_name.eq(inst).all():
            raise ValueError(f'Mismatched archived market {filename}')
        data['hour'] = data.open_time//wallet.HOUR*wallet.HOUR
        valid = data['confirm'].eq(1)&data.hour.isin(missing)
        for t,g in data[valid].groupby('hour'):
            g=g.sort_values('open_time')
            if np.array_equal(g.open_time,t+np.arange(60)*60000) and g.vol_quote.notna().all():
                take(t,[g.open.iat[0],g.high.max(),g.low.min(),g.close.iat[-1]],float(g.vol_quote.sum()),'archived_complete_60m')
    out = parents[['event_id','symbol','entry_ms','side','prior_atr']].copy()
    prev = out.entry_ms-wallet.HOUR
    out['prior_hour_ms'] = prev
    out['prior_turnover_usdt'] = [records.get(t,(np.nan,'unavailable'))[0] for t in prev]
    out['source'] = [records.get(t,(np.nan,'unavailable'))[1] for t in prev]
    return out


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed previous-hour turnover grid')
    f = pd.read_csv(candidates.EXP/'opportunities.csv.gz',dtype={'symbol':str})
    quotes,_ = repair.checked_quotes()
    rows = []
    with closing(sqlite3.connect(inputs.CANDLE_CACHE.as_uri()+'?mode=ro',uri=True,timeout=25)) as db:
        for n,(sym,g) in enumerate(f.groupby('symbol')):
            rows.append(instrument(sym,g,quotes,db))
            if n%50==0:
                print('source symbols',n,flush=True)
    out = pd.concat(rows,ignore_index=True)
    if set(out.event_id)!=set(f.event_id) or len(out)!=len(f):
        raise ValueError('Candidate coverage changed')
    out.to_csv(EXP/'opportunities.csv.gz',index=False)
    repair.archive.prices.safe_json(EXP/'summary.json',dict(eligible=len(out),covered=int(out.prior_turnover_usdt.notna().sum()),
                source=out.source.value_counts().to_dict(),symbols=out.symbol.nunique(),
                incomplete_symbols=int(out.groupby('symbol').prior_turnover_usdt.count().eq(0).sum())))


if __name__=='__main__':
    main()
