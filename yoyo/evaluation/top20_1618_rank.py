"""Freeze current OKX crypto USDT-swap top20 by actual 24h quote turnover.

Owner requests other high-volume coins for the existing short-excursion chart
census, not a historical top20 strategy backtest. Public ticker snapshots only
set the UTC-hour cutoff; turnover is the sum of 24 completed native 1H candles'
volCcyQuote (USDT), not contracts/base coins times the last price. Category1
is crypto per OKX docs; stocks/commodities/forex/bonds are excluded. The current
universe is retrospective when applied to historical samples and has selection
and survivorship bias. The BTC member remains in the rank but is not rescanned.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import threading
import time
import requests
import pandas as pd

from yoyo.evaluation.btc_1618_early_marks import ROOT

EXP = ROOT/'experiments/active/exp-top20-1618-short-marks-20261031-v1'
DOCS = 'https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history'
LOCK = threading.Lock()
NEXT = 0.0


def request(params):
    global NEXT
    for attempt in range(4):
        with LOCK:
            wait = max(0, NEXT-time.monotonic())
            NEXT = max(time.monotonic(), NEXT)+.18
        if wait:
            time.sleep(wait)
        try:
            response = requests.get('https://www.okx.com/api/v5/market/history-candles',
                                    params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data['code'] != '0':
                raise ValueError(data)
            return data
        except (requests.RequestException, ValueError):
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def run():
    tickers = json.loads((EXP/'tickers.json').read_text())['data']
    instruments = json.loads((EXP/'instruments.json').read_text())['data']
    universe = [r for r in instruments if r['state']=='live' and r['instCategory']=='1'
                and r['instId'].endswith('-USDT-SWAP')]
    end = int(next(r['ts'] for r in tickers if r['instId']=='BTC-USDT-SWAP'))//3_600_000*3_600_000
    start = end-86_400_000
    folder = EXP/'ranking_candles';folder.mkdir(exist_ok=True)

    def one(spec):
        path = folder/(spec['instId']+'.json')
        if path.exists():
            data = json.loads(path.read_text())
        else:
            data = request(dict(instId=spec['instId'], bar='1H', limit='30', after=str(end)))
            path.write_text(json.dumps(data))
        rows = [r for r in data['data'] if start <= int(r[0]) < end and r[8]=='1']
        times = sorted(int(r[0]) for r in rows)
        first = max(start, (int(spec['listTime'])//3_600_000)*3_600_000)
        expected = list(range(first, end, 3_600_000))
        assert times == expected, (spec['instId'], len(times), len(expected))
        return dict(instId=spec['instId'], symbol=spec['instId'].split('-')[0],
                    turnover_usdt=sum(float(r[7]) for r in rows), candles=len(rows),
                    tick=float(spec['tickSz']), list_time=spec['listTime'])

    rows=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,row in enumerate(pool.map(one,universe),1):
            rows.append(row)
            if i%50==0:print(f'Ranked {i}/{len(universe)}',flush=True)
    frame=pd.DataFrame(rows).sort_values('turnover_usdt',ascending=False).reset_index(drop=True)
    frame.insert(0,'rank',range(1,len(frame)+1));frame.to_csv(EXP/'ranking_all.csv',index=False)
    chosen=frame.head(20).to_dict('records')
    payload=dict(start=str(pd.to_datetime(start,unit='ms',utc=True)),
                 end=str(pd.to_datetime(end,unit='ms',utc=True)),
                 method='Sum actual volCcyQuote in24completed1H candles;category1 live OKX USDT swaps',
                 universe=len(frame),docs=DOCS,top20=chosen,
                 scan=[r for r in chosen if r['symbol']!='BTC'],
                 historical_rank=False,profitability_claim=False)
    (EXP/'ranking.json').write_text(json.dumps(payload,indent=2)+'\n')
    print(frame.head(20).to_string(index=False),flush=True)


if __name__=='__main__':run()
