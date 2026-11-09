"""Collect native realized funding clocks and marks for the actual hourly-capped Binance positions."""
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from yoyo.data.binance_um_vision_fetch import BASE, fetch
from yoyo.evaluation import cloud_close_funding_ledger as ledger
from yoyo.evaluation import cloud_close_funding_pressure as pressure
from yoyo.evaluation import cloud_close_hourly_trim as hourly

EXP = hourly.EXP.parent/'exp-cloud-close-hourly-trim-funding-20261109-v1'


def prime(key):
    symbol,month = key
    ticker = symbol+'USDT'
    base = ledger.BASE_DATA/'funding_archives'
    filename = f'{ticker}-fundingRate-{month}.zip'
    rate = ledger._rate_archive_path(symbol,month)
    if rate is None:
        other = pressure.MONTHLY_RAW/ticker/filename
        if other.is_file():
            # The shared ledger reader expects one specific local rate-cache path.
            # Retain the official compressed bytes and reuse them across studies.
            target = base/'funding'/ticker/filename
            target.parent.mkdir(parents=True,exist_ok=True)
            try:
                os.link(other,target)
            except FileExistsError:
                pass
            except OSError:
                tmp = target.with_suffix('.trim-copy')
                shutil.copyfile(other,tmp)
                tmp.replace(target)
        else:
            fetch(base,'funding',ticker,f'{BASE}/monthly/fundingRate/{ticker}/{filename}')
    mark = ledger._mark_archive_path(symbol,month)
    if not mark.is_file():
        fetch(base,'mark_1h',ticker,f'{BASE}/monthly/markPriceKlines/{ticker}/1h/{mark.name}')


def main():
    receipt = EXP/'source_coverage.json'
    if receipt.exists():
        raise FileExistsError('Completed funding source receipt already exists')
    archive = hourly.tested.admin.archive
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((hourly.tested.admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    with archive.market_quotes(extra):
        data,_ = hourly.tested.binance_inputs(extra)
    columns = ['event_id','parent_id','symbol','side','entry_ms','entry_price','entry_order','exit_bar_ms',
               'exit_phase','exit_price','exit_reason','exit_fee_rate','tp_r','slot']
    legs = data['selected'][columns].copy()
    del data
    reference = pd.read_csv(hourly.EXP/'funded_Binance.csv.gz',usecols=['event_id'])
    if len(reference)!=1536 or set(legs.event_id)!=set(reference.event_id) or not legs.groupby('event_id').size().eq(2).all():
        raise ValueError('Funding source must cover the original scored hourly-cap parent and both legs')
    collect(legs,EXP)


def collect(legs,directory):
    receipt = directory/'source_coverage.json'
    if receipt.exists():
        raise FileExistsError('Completed funding source receipt already exists')
    legs.to_csv(directory/'child_windows.csv.gz',index=False)
    windows = ledger._month_windows(legs)
    old = set((x['symbol'].removesuffix('USDT'),x['month']) for x in json.loads(
        (hourly.EXP.parent/'exp-cloud-close-current-funding-20261109-v1/resolved/symbol_month_audit.json').read_text()))
    print(f'Funding windows {len(windows)}, previous account also held {len(old.intersection(windows))}',flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n,_ in enumerate(pool.map(prime,sorted(windows)),1):
            if n%250==0:
                print(f'Monthly sources {n}/{len(windows)}',flush=True)
    client = ledger.FundingClient('binance',ledger.CACHE/'funding_api')
    frames,audits = [],[]
    for (symbol,month),win in sorted(windows.items()):
        frame,audit = ledger._window_history(symbol,month,win,client,allow_api=False,bulk_archives=True,cache_only=True)
        if not audit['range_complete']:
            _,api = ledger._api_rows(client,symbol,month,win['start_ms'],win['end_ms'])
            if not api.get('api_range_traversed'):
                raise ValueError(f'Unresolved required settlement window: {symbol} {month}')
            frame,audit = ledger._window_history(symbol,month,win,client,allow_api=False,bulk_archives=True,cache_only=True)
        if audit['missing_mark_rows']:
            ledger.archive_funding.archive_month(symbol+'USDT',month,local_only=False)
            ledger.archive_funding.archive_month.cache_clear()
            frame,audit = ledger._window_history(symbol,month,win,client,allow_api=False,bulk_archives=True,cache_only=True)
        if not audit['range_complete'] or audit['missing_mark_rows'] or audit['missing_rate_rows']:
            raise ValueError(f'Incomplete rates or settlement marks for {symbol} {month}')
        frames.append(frame.assign(symbol=symbol))
        audits.append({k:audit[k] for k in ('symbol','month','query_start_ms','query_end_exclusive_ms','source',
                                            'monthly_rate_archive','monthly_mark_1h_archive','range_complete',
                                            'missing_mark_rows','missing_rate_rows','funding_settlement_rows_in_query_window')})
    tape = pd.concat([f for f in frames if not f.empty],ignore_index=True)
    if tape.duplicated(['symbol','funding_time_ms']).any() or not np.isfinite(tape[['funding_rate','mark_price']]).all().all() or not tape.mark_price.gt(0).all():
        raise ValueError('Market tape contains duplicate events or invalid rates/marks')
    tape.to_csv(directory/'funding_tape.csv.gz',index=False)
    pd.DataFrame(audits).to_csv(directory/'source_months.csv.gz',index=False)
    result = dict(complete=True,positions=legs.event_id.nunique(),legs=len(legs),symbols=legs.symbol.nunique(),
                  symbol_months=len(windows),overlap_original_reference_months=len(old.intersection(windows)),
                  market_settlements=len(tape),mark_sources=tape.mark_source.value_counts().to_dict(),
                  prices_are_official_mark_h1_open_proxies_or_exact_api=True)
    receipt.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
