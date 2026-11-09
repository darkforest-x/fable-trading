"""Recover Binance's original-year USDT spot cohort by appearance in real historic trades."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from yoyo.evaluation import cloud_close_spot_reclaim_repair as checked

source=checked.source
ROOT=checked.ROOT
EXP=ROOT/'experiments/active/exp-cloud-close-spot-launch-universe-20261109-v1'
OLD=ROOT/'data/research/cloud_close_spot_reclaim_20261109'
CACHE=OLD.parent/'cloud_close_spot_launch_universe'
LAST_MONTH='2026-10'
H=3600000


def index(symbol):
    original=source._monthly_index_path(OLD,symbol)
    record=source.spot_month_index(OLD if original.is_file() else CACHE,symbol)
    months=sorted(m for m in record['months'] if m<=LAST_MONTH)
    return dict(symbol=symbol,first_month=months[0] if months else None,last_month=months[-1] if months else None,
                months=months,reused_index=original.is_file())


def archive(job):
    symbol,month=job
    filename=f'{symbol}-1h-{month}.zip'
    existing=OLD/'raw'/symbol/'1h'/filename
    if existing.is_file():
        path=existing
    else:
        result=source._download_archive(CACHE,symbol,month)
        if result['status'] not in ('cached','ok','downloaded'):
            raise ValueError(f'Listed spot archive acquisition failed: {symbol} {month} {result["status"]}')
        path=Path(result['path'])
    bars,audit,_=checked._parse_archive(path,symbol,month)
    if not bars:
        raise ValueError(f'No verified native rows in listed month {symbol} {month}')
    frame=pd.DataFrame.from_dict(bars,orient='index').sort_index()
    frame.index.name='time'
    for col in ['open','high','low','close','volume']:
        frame[col]=frame[col].astype(float)
    return (symbol,dict(symbol=symbol,month=month,source=source._archive_url(symbol,month),
            file=str(path.relative_to(ROOT)),reused_original=existing.is_file(),valid_rows=len(frame),
            rejected_rows=audit['rejected_rows'],zero_trade_rows=int(frame.zero_trade.sum())),frame)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed original-year market universe')
    pairs=sorted(p for p in source.spot_pair_index(OLD)['pairs'] if p.endswith('USDT') and p!='USDT')
    with ThreadPoolExecutor(max_workers=4) as workers:
        catalog=list(workers.map(index,pairs))
    pd.DataFrame(catalog).drop(columns='months').to_csv(EXP/'pair_inventory.csv.gz',index=False)
    selected=[r for r in catalog if any(m.startswith('2017-') for m in r['months'])]
    print('USDT pairs indexed',len(catalog),'launch-year month directories',[r['symbol'] for r in selected],flush=True)
    jobs=[(row['symbol'],m) for row in selected for m in row['months']]
    frames={row['symbol']:[] for row in selected}
    receipts=[]
    with ThreadPoolExecutor(max_workers=4) as workers:
        for n,(symbol,info,frame) in enumerate(workers.map(archive,jobs),1):
            receipts.append(info)
            frames[symbol].append(frame)
            if n%100==0 or n==len(jobs):
                print('validated monthly H1 sources',n,'/',len(jobs),flush=True)
    pd.DataFrame(receipts).to_csv(EXP/'monthly_sources.csv.gz',index=False)
    symbols={}
    dest=CACHE/'normalized'
    dest.mkdir(parents=True,exist_ok=True)
    for symbol,parts in frames.items():
        rows=pd.concat(parts).sort_index()
        if not rows.index.is_unique:
            raise ValueError('Native monthly hours overlap')
        real=rows[rows.trades.gt(0)]
        if not len(real) or int(real.index[0])>=pd.Timestamp('2018-01-01',tz='UTC').value//10**6:
            continue
        file=dest/f'{symbol}.csv.gz'
        rows.to_csv(file,index_label='time')
        symbols[symbol]=dict(file=str(file.relative_to(ROOT)),first_trade_ms=int(real.index[0]),
               last_trade_open_ms=int(real.index[-1]),verified_hours=len(rows),zero_trade_hours=int(rows.zero_trade.sum()),
               missing_between_first_and_last=int((rows.index[-1]-rows.index[0])//H+1-len(rows)))
    (EXP/'summary.json').write_text(json.dumps(dict(historical_spot_pair_prefixes=len(pairs),indexed=len(catalog),
        original_indexes=sum(r['reused_index'] for r in catalog),months=len(receipts),
        reused_original_zips=sum(r['reused_original'] for r in receipts),last_month=LAST_MONTH,
        symbols=symbols),indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
