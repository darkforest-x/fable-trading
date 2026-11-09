"""Recover independently corroborated retired WAVES prices from native historical OKX transactions."""
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_okx_retired_prices as prices

EXP = prices.EXP.parent/'exp-cloud-close-okx-waves-trades-20261109-v1'
CACHE = prices.CACHE.parent/'cloud_close_okx_waves_trades'
INST = 'WAVES-USDT-SWAP'
HOUR = prices.HOUR
FIELDS = prices.FIELDS


def month(args):
    date,base = args
    file = f'{INST}-trades-{date}.zip'
    url = f'{base}/{date.replace("-","")}/{file}'
    dest = CACHE/'raw'/file
    absent = dest.with_suffix('.404')
    if not dest.exists() and not absent.exists():
        res = requests.get(url,timeout=(8,35))
        if res.status_code==404:
            absent.write_text(url+'\n')
        else:
            res.raise_for_status()
            tmp = dest.with_suffix('.part')
            tmp.write_bytes(res.content)
            tmp.replace(dest)
    if not dest.is_file():
        return None,dict(month=date,status='official_404',url=url)
    x = pd.read_csv(dest,float_precision='round_trip')
    headers = ['instrument_name','trade_id','price','size','created_time']
    names = {col:next((n for n in headers if n in col.lower()),col) for col in x.columns}
    x = x.rename(columns=names)[headers].drop_duplicates()
    if (not len(x) or set(x.instrument_name)!={INST} or x.trade_id.duplicated().any() or
        not np.isfinite(x[['price','size']]).all().all() or not x.price.gt(0).all() or not x['size'].gt(0).all()):
        raise ValueError(f'Malformed trade archive {file}')
    x = x.sort_values(['created_time','trade_id'])
    if not x.trade_id.is_monotonic_increasing:
        raise ValueError(f'Unordered native trade identities across timestamps in {file}')
    h = x.groupby(x.created_time//HOUR*HOUR)
    frame = h.agg(open=('price','first'),high=('price','max'),low=('price','min'),close=('price','last'),
             first_id=('trade_id','first'),last_id=('trade_id','last'),prints=('trade_id','count'))
    frame.index.name = 'time'
    return frame,dict(month=date,status='native_trades',trade_rows=len(x),hours=len(frame),url=url)


def candle_reference():
    records = []
    for file in sorted((prices.audit.CACHE/'raw'/INST).glob('*.zip')):
        raw = pd.read_csv(file,usecols=['instrument_name','open_time',*FIELDS,'confirm'],float_precision='round_trip')
        # Previously unconfirmed rows can only corroborate already closed historical
        # native trades; they can never alone create a qualifying H1.
        raw['confirm'] = 1
        frame,_ = prices.minute_hours(raw,INST)
        records.append(frame)
    return pd.concat(records).sort_index()


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed retired-trade source')
    cfg = json.loads((EXP/'config.json').read_text())
    (CACHE/'raw').mkdir(parents=True,exist_ok=True)
    dates = pd.period_range('2020-01','2024-02',freq='M').astype(str)
    with ThreadPoolExecutor(max_workers=3) as threads:
        items = list(threads.map(month,[(d,cfg['native_url']) for d in dates]))
    manifest = [m for _,m in items]
    bars = pd.concat([b for b,_ in items if b is not None]).sort_index()
    if not bars.index.is_unique or not bars.first_id.is_monotonic_increasing or not bars.last_id.is_monotonic_increasing:
        raise ValueError('Overlapping trade histories across exchange months')
    exact = (bars.last_id-bars.first_id+1).eq(bars.prints) & bars.first_id.sub(bars.last_id.shift()).eq(1) & bars.first_id.shift(-1).sub(bars.last_id).eq(1)
    ref = candle_reference().reindex(bars.index)
    have = ref.notna().all(axis=1)
    match = pd.Series(np.isclose(bars[FIELDS],ref,rtol=1.e-9,atol=1.e-13).all(axis=1),index=bars.index)
    strict = (match | exact) & ~(have & ~match)
    end = pd.Timestamp(cfg['end_exclusive']).value//1_000_000
    frozen = pd.read_csv(prices.CACHE/'series'/INST/'bars1h.csv.gz',index_col='time',float_precision='round_trip')
    original = frozen[frozen.index<end]
    additional = bars[(bars.index<end)&~bars.index.isin(original.index)&strict]
    combined = pd.concat([original,additional[FIELDS]]).sort_index()
    combined.index.name = 'time'
    combined.to_csv(EXP/'bars1h.csv.gz')
    tags = pd.DataFrame(dict(bracketed_no_missing_ids=exact,native_minute_source_available=have,
              native_h1_agrees=match,strictly_supported=strict,retained_original=bars.index.isin(original.index),
              contributed_new=bars.index.isin(additional.index)),index=bars.index)
    tags.to_csv(EXP/'hour_source_checks.csv.gz')
    pd.DataFrame(manifest).to_csv(EXP/'months.csv',index=False)
    def streak(index):
        s = pd.Series(index,dtype='int64')
        return int(s.groupby(s.diff().ne(HOUR).cumsum()).size().max())
    prices.safe_json(EXP/'summary.json',dict(native_trade_months=sum(b is not None for b,_ in items),
        official_months_404=sum(b is None for b,_ in items),published_trades=int(sum(m.get('trade_rows',0) for m in manifest)),
        original_hours=len(original),new_hours=len(additional),total_hours=len(combined),native_trade_hours=len(bars),
        price_disagreements=int((have&~match).sum()),short_of_proof_hours=int((~strict).sum()),
        original_longest_contiguous_h1=streak(original.index),new_longest_contiguous_h1=streak(combined.index),
        first_utc=pd.to_datetime(combined.index.min(),unit='ms',utc=True).isoformat(),
        through_utc=pd.to_datetime(combined.index.max()+HOUR,unit='ms',utc=True).isoformat()))
    print((EXP/'summary.json').read_text(),flush=True)


if __name__=='__main__':
    main()
