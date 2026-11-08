"""Fill native Binance taker-data gaps only at historical V1.7 signal or control keys."""
import gc
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_market_taker_pressure as full

pilot = full.pilot
EXP = full.EXP.parent/'exp-cloud-close-taker-coverage-20261109-v1'
CACHE = pilot.ROOT/'data/research/cloud_close_missing_native_taker'
PUBLIC = 'https://data.binance.vision/data/futures/um'


def fetch(ticker,minutes,period):
    frequency = 'monthly' if len(period)==7 else 'daily'
    filename = f'{ticker}-{minutes}m-{period}.zip'
    path = CACHE/ticker/filename
    if not path.is_file():
        url = f"{PUBLIC}/{frequency}/klines/{quote(ticker, safe='')}/{minutes}m/{quote(filename, safe='')}"
        for attempt in range(3):
            try:
                with urlopen(Request(url,headers={'User-Agent':'fable-historical-kline-research/1'}),timeout=20) as response:
                    data = response.read()
                if not data.startswith(b'PK'):
                    raise ValueError(f'Expected ZIP archive from {url}')
                path.parent.mkdir(parents=True,exist_ok=True)
                tmp = path.with_suffix('.part')
                tmp.write_bytes(data)
                tmp.replace(path)
                break
            except HTTPError as exc:
                if exc.code in (403,404):
                    return None
                if attempt==2:
                    raise
                time.sleep(attempt+1)
            except (TimeoutError,URLError):
                if attempt==2:
                    raise
                time.sleep(attempt+1)
    return path


def collect(job):
    symbol,month,closes = job
    ticker = symbol+'USDT'
    clock,prices = full.venue.quotes(symbol)
    remaining = set(closes)
    output = []
    sources = [(15,month)]
    days = sorted({pd.Timestamp(t-full.KEY,unit='ms',tz='UTC').strftime('%Y-%m-%d') for t in closes})
    sources.extend((mins,day) for day in days for mins in (15,5))
    reasons = dict.fromkeys(closes,'official_archive_missing')
    for minutes,period in sources:
        batch = [t for t in sorted(remaining) if len(period)==7 or pd.Timestamp(t-full.KEY,unit='ms',tz='UTC').strftime('%Y-%m-%d')==period]
        if not batch:
            continue
        path = fetch(ticker,minutes,period)
        if path is None:
            continue
        raw = full.native._read_month(path,month,minutes*60_000)
        if np.any(np.diff(raw[:,full.native.TS])<=0):
            raise ValueError(f'Duplicate native times in {path}')
        for t in batch:
            h = np.arange(t-full.KEY,t,full.HOUR)
            ix = clock.searchsorted(h)
            canonical = prices[ix] if (ix<len(clock)).all() and np.array_equal(clock[ix],h) else None
            status,imbalance = full.measurement(t,raw,minutes,canonical)
            if status=='valid':
                output.append((symbol,t,status,imbalance,str(path.relative_to(pilot.ROOT))))
                remaining.remove(t)
            else:
                reasons[t] = status
    output.extend((symbol,t,reasons[t],np.nan,None) for t in sorted(remaining))
    return output


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Coverage extension already evaluated')
    source = pd.read_csv(full.EXP/'event_flow_screen.csv.gz',dtype={'symbol':str},float_precision='round_trip',keep_default_na=False)
    source['flow_imbalance'] = pd.to_numeric(source.flow_imbalance,errors='coerce')
    missing = source.loc[source.raw_status.ne('valid'),['symbol','entry_ms']].drop_duplicates()
    missing['month'] = pd.to_datetime(missing.entry_ms-full.KEY,unit='ms',utc=True).dt.strftime('%Y-%m')
    jobs = [(s,m,g.entry_ms.tolist()) for (s,m),g in missing.groupby(['symbol','month'])]
    saved = EXP/'recovered_native_keys.csv.gz'
    if not saved.exists():
        rows = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i,group in enumerate(pool.map(collect,jobs),1):
                rows.extend(group)
                if i%50==0:
                    print('Merged new native flow for',i,'of',len(jobs),'contract-months',flush=True)
        pd.DataFrame(rows,columns=['symbol','entry_ms','new_status','new_flow','new_source']).to_csv(saved,index=False)
    new = pd.read_csv(saved,dtype={'symbol':str},float_precision='round_trip')
    frames = source.merge(new,on=['symbol','entry_ms'],how='left',validate='many_to_one')
    wanted = frames.raw_status.ne('valid')
    if frames.loc[wanted,'new_status'].isna().any() or frames.loc[~wanted,'new_status'].notna().any():
        raise ValueError('New observations do not cover precisely the prior missing windows')
    filled = frames.new_status.eq('valid')
    frames.loc[wanted,'raw_status'] = frames.loc[wanted,'new_status']
    frames.loc[filled,'flow_imbalance'] = frames.loc[filled,'new_flow']
    frames.loc[filled,'source'] = frames.loc[filled,'new_source']
    frames['flow_opposes_side'] = (frames.side*frames.flow_imbalance).lt(0)
    frames['flow_status'] = np.where(frames.raw_status.eq('valid'),
                        np.where(frames.flow_opposes_side,'valid_opposing','valid_not_opposing'),frames.raw_status)
    summary = full.pilot_check(frames)
    paths = full.venue.assembled(set(full.venue.patch.affected_tickers()))
    if set(frames.event_id)!=set(paths.event_id):
        raise ValueError('Native inputs differ from complete corrected roster')
    cohorts = pilot._event_sets(frames)
    for key, subset in [('opposing',frames.flow_opposes_side),('available',frames.raw_status.eq('valid'))]:
        cohorts[key+'_ex_pilot'] = set(frames.loc[~frames.is_control & subset & ~frames.symbol.isin(pilot.PANEL),'event_id'])
    cohorts['opposing_stressed'] = cohorts['opposing_actual']
    baseline = pd.read_csv(full.repair.EXP/'thirds'/'account_metrics.csv')
    base = baseline[baseline.venue.eq('Binance') & baseline.scenario.eq('actual')]
    rows = base.assign(cohort='unfiltered').to_dict('records')
    real = frames[~frames.is_control]
    for key,ids in cohorts.items():
        if key=='available_control':
            continue
        if key=='available_actual' and ids==set(real.event_id):
            rows.extend(base.assign(cohort=key).to_dict('records'))
            continue
        metrics,tr,eq = pilot._run_account(paths[paths.event_id.isin(ids)],.0005 if key=='opposing_stressed' else 0.)
        if key=='opposing_actual':
            tr.to_csv(EXP/'funded.csv.gz')
            eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
            summary.update(dev_pass=full.reclaim.meets(metrics['dev']),full_pass=full.reclaim.meets(metrics['all']))
        rows.extend(dict(cohort=key,period=p,**m) for p,m in metrics.items())
        del tr,eq
        gc.collect()
    summary.update(previous_missing_events=int(wanted.sum()),recovered_events=int(filled.sum()),actual_coverage=real.raw_status.value_counts().to_dict(),
                   counts={k:len(v) for k,v in cohorts.items()})
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')][['cohort','final_usdt','trades','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__ == '__main__':
    main()
