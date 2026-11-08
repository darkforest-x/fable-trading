"""Add all formerly omitted contracts from the independent Binance historical-altcoin archive."""
import gc
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data import binance_um_vision_listing as listing
from yoyo.data.binance_um_vision_fetch import BASE, fetch
from yoyo.evaluation import cloud_close_binance_gap_repair as native
from yoyo.evaluation import cloud_close_efficient_trend as trend
from yoyo.evaluation import cloud_close_volatility_expansion as market
from yoyo.evaluation import signal_center_score as keys

venue = native.venue
EXP = native.EXP.parent/'exp-cloud-close-archive-cohort-20261109-v1'
CACHE = native.wallet.ROOT/'data/research/cloud_close_archive_cohort'
PREVIOUS = EXP.parent/'exp-monday-1618-alt-hours-20261023-v1/inputs/klines15m'
STEP = 900000


def months_for(symbol):
    listed = listing.files(symbol,interval='15m')
    months = {Path(k).stem[-7:]:k for k in listed if '2019-12'<=Path(k).stem[-7:]<='2026-09'}
    existing = {p.stem[-7:]:p for p in (PREVIOUS/symbol).glob('*.zip')}
    if not months or set(existing)-months.keys():
        raise ValueError(f'{symbol}: registered and exchange archive inventories disagree')
    return months,existing


def read_prices(path):
    bars = pd.read_csv(path,header=None,usecols=range(6),names=['time','open','high','low','close','volume'])
    if str(bars.time.iloc[0]).lower() in ('open_time','open time'):
        bars = bars.iloc[1:].copy()
    bars['time'] = pd.to_numeric(bars.time,errors='raise').astype('int64')
    bars = bars.set_index('time').astype(float)
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing or (bars.index%STEP!=0).any():
        raise ValueError(f'Invalid native 15m clocks in {path}')
    if not np.isfinite(bars.values).all() or (bars[venue.FIELDS]<=0).any().any():
        raise ValueError(f'Invalid native 15m prices in {path}')
    return bars


def input_frame(symbol,work):
    months,existing = months_for(symbol)
    frames,source = [],[]
    for month,key in sorted(months.items()):
        path = existing.get(month)
        if path is None:
            url = 'https://data.binance.vision/'+key
            if fetch(CACHE,'monthly',symbol,url)[2] not in ('fetched','cached'):
                raise IOError(f'Listed Binance archive unavailable: {key}')
            path = CACHE/'monthly'/symbol/Path(key).name
        rows = read_prices(path)
        frames.append(rows)
        source.append(dict(month=month,archive=key,local=str(path.relative_to(native.wallet.ROOT)),rows=len(rows)))
    bars = pd.concat(frames).sort_index()
    if not bars.index.is_unique:
        raise ValueError('Overlapping monthly exchange archives')
    bars = bars[bars.index<venue.END]
    missing = pd.Index(np.arange(bars.index[0],bars.index[-1]+STEP,STEP)).difference(bars.index)
    repairs = []
    monthset = set(months)
    for stamp in pd.to_datetime(missing,unit='ms',utc=True).normalize().unique():
        if stamp.strftime('%Y-%m') not in monthset:
            continue
        day = stamp.strftime('%Y-%m-%d'); filename=f'{symbol}-15m-{day}.zip'
        downloaded = fetch(CACHE,'daily',symbol,f'{BASE}/daily/klines/{symbol}/15m/{filename}')[2]
        if downloaded=='failed':
            raise IOError(f'Failed to check published daily file for {symbol} {day}')
        new = 0
        if downloaded in ('fetched','cached'):
            daily = read_prices(CACHE/'daily'/symbol/filename)
            overlap = daily.index.intersection(bars.index)
            if not np.allclose(daily.loc[overlap],bars.loc[overlap],rtol=1e-10,atol=1e-12):
                raise ValueError(f'{symbol} {day}: monthly and daily prices conflict')
            extra = daily.loc[daily.index.intersection(missing)]
            new = len(extra)
            if new:
                bars=pd.concat([bars,extra]).sort_index()
        repairs.append(dict(day=day,state=downloaded,added=new))
    bars.index = pd.to_datetime(bars.index,unit='ms',utc=True).as_unit('ns')
    gap_hours = float((bars.index.to_series().diff().dt.total_seconds().div(3600).sub(.25).clip(lower=0)).sum())
    audit=dict(symbol=symbol,months=source,repairs=repairs,registered_months=len(existing),
               missing_elapsed_hours_between_first_and_last=gap_hours,first=str(bars.index[0]),last=str(bars.index[-1]))
    (work/'source.json').write_text(json.dumps(audit,indent=2)+'\n')
    return bars


def aggregate(bars,hours):
    group=bars.resample(f'{hours}h',origin='epoch',label='left',closed='left')
    frame=group.agg({'open':'first','high':'max','low':'min','close':'last'})[group.close.count().eq(hours*4)]
    frame.index=pd.Index(frame.index.as_unit('ms').asi8,name='time')
    return frame


def price(symbol):
    work=CACHE/'symbols'/symbol
    work.mkdir(parents=True,exist_ok=True)
    receipt=work/'complete.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    bars=input_frame(symbol,work)
    events=keys.build_inputs(bars,symbol,None,key_timeframes=['12H'],source_minutes=15)[4].reset_index(drop=True)
    if events.empty:
        events=pd.DataFrame(columns=['tf','close_ns','signal_id','week','side','stop'])
    hours=bars[(bars.index.as_unit('ms').asi8>=venue.START)]
    stats=venue.price_frames(symbol,work,aggregate(hours,1),aggregate(hours,12),events)
    receipt.write_text(json.dumps(stats)+'\n')
    print(symbol,stats['source_signals'],flush=True)
    return stats


@lru_cache(maxsize=40)
def quotes(symbol):
    path=CACHE/'symbols'/(symbol+'USDT')/'quotes.npz'
    if path.exists():
        with np.load(path) as data:
            return data['clock'],data['prices']
    raise FileNotFoundError(f'Missing extra contract H1 path: {symbol}')


def pool(extra):
    repaired=set(native.patch.affected_tickers())
    baseline=pd.read_csv(venue.EXP/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    parts=[baseline[~(baseline.symbol+'USDT').isin(repaired)].drop(columns='eligible')]
    ranks=Counter()
    for ticker in sorted({d.name for d in venue.WORK.iterdir() if d.name.endswith('USDT')}|set(extra)):
        work=(CACHE/'symbols' if ticker in extra else native.WORK if ticker in repaired else venue.WORK)/ticker
        with np.load(work/'quotes.npz') as f:
            ranks.update(map(int,f['rank_times']))
        if ticker in repaired or ticker in extra:
            parts.append(pd.read_csv(work/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip'))
    all_paths=pd.concat([part for part in parts if not part.empty],ignore_index=True)
    for field in ('is_control','eligible_at_own_time'):
        if not all_paths[field].isin([True,False]).all():
            raise ValueError(f'Unexpected indicator in native paths: {field}')
        all_paths[field] = all_paths[field].astype(bool)
    if all_paths.duplicated(['event_id','tp_r']).any() or not all_paths.groupby('event_id').size().eq(2).all():
        raise ValueError('Native runner/limit pairs incomplete in enlarged eligible universe')
    valid=all_paths.eligible_at_own_time & all_paths.entry_ms.map(ranks).fillna(0).ge(10)
    paths=all_paths[valid]
    return paths[paths.parent_id.isin(paths.loc[~paths.is_control,'parent_id'])].copy()


def verify_native_overlap():
    ticker = 'ADAUSDT'
    sample = sorted((PREVIOUS/ticker).glob(f'{ticker}-15m-2020-*.zip'))
    if len(sample)!=12:
        raise ValueError('Registered overlap reference is incomplete')
    bars = pd.concat([read_prices(p) for p in sample]).sort_index()
    bars.index = pd.to_datetime(bars.index,unit='ms',utc=True).as_unit('ns')
    result = keys.build_inputs(bars,ticker,None,key_timeframes=['12H'],source_minutes=15)[4]
    original = pd.read_csv(venue.ARCHIVE/'results/symbols'/ticker/'signals.csv.gz')
    original = original[original.tf.eq('12H') & original.close_ns.between(bars.index[0].value,bars.index[-1].value)]
    result = result.set_index('signal_id').sort_index()
    original = original.set_index('signal_id').sort_index()
    if len(result)<10 or not result.index.equals(original.index) or not result.side.equals(original.side):
        raise ValueError('Native 15m and 5m versions yielded different 2020 ADA weekly keys')
    if not np.allclose(result.stop,original.stop,rtol=1e-10,atol=1e-12):
        raise ValueError('Native 15m and 5m versions yielded different 2020 ADA protective stops')
    hours = aggregate(bars,1)
    clock,prices = native.quotes('ADA')
    intersect = hours.index.intersection(clock)
    if len(intersect)<7000 or not np.allclose(hours.loc[intersect,venue.FIELDS],prices[np.searchsorted(clock,intersect)],rtol=1e-10,atol=1e-12):
        raise ValueError('Native 15m and 5m archives disagree on 2020 shared hourly prices')
    return dict(ticker=ticker,year=2020,matched_keys=len(result),matched_hourly_ohlc=len(intersect))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed expanded-universe study')
    old={p.name for p in venue.WORK.iterdir() if p.name.endswith('USDT')}
    archive={p.name for p in PREVIOUS.iterdir() if p.name.endswith('USDT')}
    extra=archive-old
    if (len(old),len(archive),len(extra))!=(632,503,29):
        raise ValueError('Registered cohort changed')
    overlap = verify_native_overlap()
    print('Native overlap confirmed:',overlap,flush=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        coverage=list(executor.map(price,sorted(extra)))
    pd.DataFrame(coverage).to_csv(EXP/'archive_coverage.csv',index=False)
    paths=pool(extra)
    native_quotes=native.quotes
    # Different venue callbacks and all live settings stay outside this offline process.
    def extended(symbol):
        if symbol+'USDT' in extra:
            return quotes(symbol)
        return native_quotes(symbol)
    native.quotes=extended
    records=[];summary={'archived_additions':sorted(extra),'overlap_validation':overlap}
    try:
        for policy in ['normal_partial','trend_efficiency']:
            selected=paths if policy=='normal_partial' else trend.inputs('Binance',paths)[1]
            real=selected[~selected.is_control]
            controls=selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
            metrics,trades,equity=market.fund('Binance',real,0.)
            trades.to_csv(EXP/f'funded_{policy}.csv.gz')
            equity.to_csv(EXP/f'hourly_{policy}.csv.gz',index_label='hour_end_utc')
            records.extend(dict(policy=policy,scenario='actual',period=p,**m) for p,m in metrics.items())
            summary[policy]=dict(full=metrics['all'],dev=metrics['dev'],dev_pass=trend.second.meets(metrics['dev']),
                                 full_pass=trend.second.meets(metrics['all']),expanded_real=len(real)//2,
                                 new_real=int((real[real.tp_r.eq(0)].symbol+'USDT').isin(extra).sum()),
                                 new_funded=int((trades.symbol+'USDT').isin(extra).sum()))
            if summary[policy]['dev_pass']:
                for name,sample,slip in [('paired',real[real.parent_id.isin(controls.parent_id)],0.),('control',controls,0.),('stress',real,.0005)]:
                    checked=market.fund('Binance',sample,slip)[0]
                    records.extend(dict(policy=policy,scenario=name,period=p,**m) for p,m in checked.items())
            del selected,real,controls,trades,equity
            gc.collect()
    finally:
        native.quotes=native_quotes
    pd.DataFrame(records).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
