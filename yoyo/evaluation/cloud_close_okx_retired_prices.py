"""Build venue-native H1 histories for the retired OKX names missing from the cloud study."""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_allmarket_inputs as old
from yoyo.evaluation import cloud_close_okx_roster_audit as audit

EXP = audit.EXP.parent / 'exp-cloud-close-okx-retired-prices-20261109-v1'
CACHE = audit.ROOT/'data/research/cloud_close_okx_retired_prices'
HOUR = 3_600_000
MINUTE = 60_000
FIELDS = ['open','high','low','close']
START = pd.Timestamp('2020-01-01T00:00Z').value // 1_000_000
END = audit.END.value // 1_000_000


def safe_json(path, obj):
    tmp = path.with_suffix('.part')
    tmp.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
    tmp.replace(path)


def minute_hours(raw, inst):
    if not len(raw):
        return pd.DataFrame(columns=FIELDS), {'raw_rows':0}
    if set(raw.instrument_name)!={inst}:
        raise ValueError(f'Incorrect native instrument in {inst}')
    if not raw['confirm'].isin([0,1]).all():
        raise ValueError(f'Unknown candle confirmation flag in {inst}')
    x = raw.loc[raw['confirm'].eq(1), ['open_time',*FIELDS]].drop_duplicates()
    if x.open_time.duplicated().any() or (x.open_time % MINUTE).any():
        raise ValueError(f'Conflicting or off-grid minute records in {inst}')
    if not (np.isfinite(x[FIELDS]).all().all() and x.low.gt(0).all() and
            x.low.le(x[['open','close']].min(axis=1)).all() and x.high.ge(x[['open','close']].max(axis=1)).all()):
        raise ValueError(f'Invalid OHLC in {inst}')
    x = x.sort_values('open_time')
    g = x.groupby(x.open_time // HOUR * HOUR).agg(open=('open','first'), high=('high','max'),
             low=('low','min'), close=('close','last'), n=('open_time','size'))
    return g.loc[g.n.eq(60),FIELDS], dict(raw_rows=len(raw), unconfirmed_rows=int(raw['confirm'].eq(0).sum()),
                                          incomplete_hours=int(g.n.ne(60).sum()))


def archive_month(client, inst, month):
    root = audit.CACHE/'raw'/inst
    name = f'{inst}-candlesticks-{month}.zip'
    path = root/name
    absent = path.with_suffix('.404')
    url = f'{audit.ARCHIVE}/{month.replace("-","")}/{name}'
    if not path.is_file() and not absent.is_file():
        root.mkdir(parents=True,exist_ok=True)
        for attempt in range(4):
            try:
                response = client.get(url,timeout=(8,35))
                if response.status_code==404:
                    absent.write_text(url+'\n')
                else:
                    response.raise_for_status()
                    tmp = path.with_suffix('.downloading')
                    tmp.write_bytes(response.content)
                    tmp.replace(path)
                break
            except requests.RequestException:
                if attempt==3:
                    raise
                time.sleep(2**attempt)
    if not path.exists():
        return None, dict(month=month, status='official_404', archive=url)
    raw = pd.read_csv(path,usecols=['instrument_name','open_time',*FIELDS,'confirm'],float_precision='round_trip')
    frame, info = minute_hours(raw,inst)
    return frame, dict(month=month,status='native_archive',archive=url,complete_h1=len(frame),**info)


def build_symbol(inst):
    folder = CACHE/'series'/inst
    folder.mkdir(parents=True,exist_ok=True)
    receipt = folder/'source.json'
    if receipt.is_file():
        return json.loads(receipt.read_text())
    parts = []
    months = []
    with requests.Session() as client:
        for month in pd.period_range('2020-01','2026-11',freq='M').astype(str):
            frame, meta = archive_month(client,inst,month)
            months.append(meta)
            if frame is not None and len(frame):
                parts.append(frame)
    historical, local_info = old.read_local_sources(inst.removesuffix('-USDT-SWAP'),inst,END)
    direct = historical[FIELDS].astype(float)
    data = pd.concat(parts).sort_index() if parts else pd.DataFrame(columns=FIELDS, index=pd.Index([],dtype='int64'))
    if not data.index.is_unique:
        raise ValueError(f'UTC periods overlap in monthly archives for {inst}')
    common = data.index.intersection(direct.index)
    if len(common):
        same = np.isclose(data.loc[common],direct.loc[common],rtol=1.e-8,atol=1.e-13).all(axis=1)
        # Keep confirmed exchange minutes where local legacy data disagree, and report which hours differ.
        differences = common[~same]
    else:
        differences = common
    local = direct.loc[~direct.index.isin(data.index)]
    if len(local):
        data = pd.concat([data,local]).sort_index()
    data = data[(data.index>=START)&(data.index<END)]
    data.index.name = 'time'
    tmp = folder/'bars1h.tmp.gz'
    data.to_csv(tmp)
    tmp.replace(folder/'bars1h.csv.gz')
    safe_json(folder/'months.json',months)
    meta = dict(instrument=inst,hours=len(data),first_ms=int(data.index.min()) if len(data) else None,
          last_ms=int(data.index.max()) if len(data) else None,from_complete_native_minutes=sum(len(f) for f in parts),
          supplemental_local_h1=len(local),overlap_hours=len(common),overlap_disagreements=len(differences),
          different_first_utc_hours=list(map(int,differences[:12])),preexisting_local_source=local_info,
          native_months=sum(m['status']=='native_archive' for m in months),
          unconfirmed_minute_rows=sum(m.get('unconfirmed_rows',0) for m in months))
    safe_json(receipt,meta)
    print(f'{inst}: {len(data)} H1 ({meta["native_months"]} native months)', flush=True)
    return meta


def main():
    arg = argparse.ArgumentParser()
    arg.add_argument('--symbol')
    opts = arg.parse_args()
    directory = pd.read_csv(audit.EXP/'historical_directory_checks.csv.gz')
    names = sorted(directory.instrument,key=lambda x:(x!='WAVES-USDT-SWAP',x))
    if opts.symbol:
        if opts.symbol not in names: raise ValueError('Unregistered contract')
        build_symbol(opts.symbol)
        return
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed retired-market source collection')
    with ThreadPoolExecutor(max_workers=5) as pool:
        result = list(pool.map(build_symbol,names))
    table = pd.DataFrame(result).drop(columns=['preexisting_local_source','different_first_utc_hours'])
    table.to_csv(EXP/'coverage.csv.gz',index=False)
    safe_json(EXP/'summary.json', dict(candidates=len(table),with_prices=int(table.hours.gt(0).sum()),
              h1_hours=int(table.hours.sum()),native_months=int(table.native_months.sum()),
              scope='Previously omitted candidate instruments. Historical identity, cutoff and contract category require separate checks before admission. No account PnL.'))


if __name__=='__main__':
    main()
