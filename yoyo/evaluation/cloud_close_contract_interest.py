"""Test own-contract open interest change over the completed cloud signal candle."""
import argparse
import csv
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from yoyo.data.binance_um_vision_fetch import BASE
from yoyo.evaluation import cloud_close_fvg_cap_filter as gap
from yoyo.evaluation import cloud_close_open_interest as pilot
from yoyo.evaluation import cloud_close_spot_reclaim as wallet

EXP = wallet.EXP.parent / 'exp-cloud-close-contract-interest-20261109-v1'
CACHE = wallet.admin.archive.native.wallet.ROOT / 'data/research/cloud_close_contract_interest'
LOCAL = pilot.SOURCE / 'inputs/metrics'
SESSION = threading.local()


def snapshot(path, ticker, day):
    """Read only 11:55 and 23:55 UTC contract quantities from this native day."""
    observed = {}
    with ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith('.csv')]
        if len(names) != 1:
            raise ValueError(f'Unexpected members in {path}')
        with archive.open(names[0]) as stream:
            for row in csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig')):
                clock = row['create_time']
                if clock not in (day + ' 11:55:00', day + ' 23:55:00'):
                    continue
                if row['symbol'] != ticker:
                    raise ValueError(f'Mismatched native contract in {path}: {row["symbol"]}')
                value = float(row['sum_open_interest'] or 'nan')
                if np.isfinite(value) and value > 0:
                    if clock in observed and observed[clock] != value:
                        raise ValueError(f'Conflicting snapshots in {path} at {clock}')
                    observed[clock] = value
    return observed


def source_day(key):
    ticker, day = key
    name = f'{ticker}-metrics-{day}.zip'
    url = f'{BASE}/daily/metrics/{ticker}/{name}'
    local = LOCAL / ticker / name
    cache = CACHE / 'raw' / ticker / name
    absent = cache.with_suffix('.404')
    if local.is_file():
        path, status = local, 'original_local_zip'
    elif cache.is_file():
        path, status = cache, 'official_daily_zip'
    elif absent.is_file():
        path, status = None, 'official_404'
    else:
        path, status = cache, 'official_daily_zip'
        cache.parent.mkdir(exist_ok=True, parents=True)
        if not hasattr(SESSION, 'client'):
            SESSION.client = requests.Session()
        for attempt in range(4):
            try:
                reply = SESSION.client.get(url, timeout=(10,30))
                if reply.status_code == 404:
                    absent.write_text(url+'\n')
                    path, status = None, 'official_404'
                    break
                reply.raise_for_status()
                tmp = cache.with_suffix('.part')
                tmp.write_bytes(reply.content)
                with ZipFile(tmp) as f:
                    if f.testzip() is not None:
                        raise OSError('Invalid official ZIP')
                tmp.replace(cache)
                break
            except (requests.RequestException, OSError):
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
    found = snapshot(path, ticker, day) if path else {}
    return {'ticker': ticker, 'day': day, 'status': status, 'url': url,
            'oi_1155': found.get(day+' 11:55:00'), 'oi_2355': found.get(day+' 23:55:00')}


def collect():
    if (EXP / 'source_coverage.json').exists():
        return
    source = pd.read_csv(wallet.rank.scores.EXP/'candidate_order.csv.gz',
                         usecols=['event_id','symbol','entry_ms'], dtype={'symbol':str})
    source['ticker'] = source.symbol + 'USDT'
    if not (source.entry_ms % pilot.KEY_MS == 0).all() or not source.event_id.is_unique:
        raise ValueError('Expected unique events at the original UTC 12H close')
    stamps = {}
    for end in source.entry_ms.unique():
        for t in [int(end)-pilot.LAG_MS, int(end)-pilot.LAG_MS-pilot.KEY_MS]:
            stamps[t] = datetime.fromtimestamp(t/1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    source['latest'] = (source.entry_ms-pilot.LAG_MS).map(stamps)
    source['prior'] = (source.entry_ms-pilot.LAG_MS-pilot.KEY_MS).map(stamps)
    needs = sorted({(r.ticker,s[:10]) for r in source.itertuples() for s in (r.prior,r.latest)})
    rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for item in pool.map(source_day, needs):
            rows.append(item)
            if len(rows) % 1000 == 0:
                print(f'Own-contract OI days {len(rows)}/{len(needs)}',flush=True)
    days = pd.DataFrame(rows)
    days.to_csv(EXP/'source_days.csv.gz',index=False)
    values = {}
    for r in days.itertuples():
        values[r.ticker, r.day+' 11:55:00'] = r.oi_1155
        values[r.ticker, r.day+' 23:55:00'] = r.oi_2355
    for clock in ('prior','latest'):
        source['oi_'+clock] = [values[k] for k in zip(source.ticker,source[clock])]
    source['covered'] = source.oi_prior.notna() & source.oi_latest.notna()
    source['oi_shrinks'] = source.oi_latest.lt(source.oi_prior).astype('boolean').where(source.covered)
    source.to_csv(EXP/'decisions.csv.gz',index=False)
    summary = dict(requested_days=len(needs), status=days.status.value_counts().to_dict(),
                   eligible=len(source), covered=int(source.covered.sum()),
                   shrinking=int(source.oi_shrinks.sum()), symbols=int(source.ticker.nunique()),
                   covered_symbols=int(source.loc[source.covered,'ticker'].nunique()))
    (EXP/'source_coverage.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(summary,flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-only',action='store_true')
    args = parser.parse_args()
    collect()
    if not args.source_only:
        panel = pd.read_csv(EXP/'decisions.csv.gz',usecols=['event_id','covered','oi_shrinks'],
                            dtype={'covered':'boolean','oi_shrinks':'boolean'}).set_index('event_id')
        refs = (wallet.vwap.EXP,wallet.timing.EXP,gap.fvg.EXP,gap.EXP)
        wallet.compare_filter(panel,'oi_shrinks',EXP,refs)


if __name__ == '__main__':
    main()
