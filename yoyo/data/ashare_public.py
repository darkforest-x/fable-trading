"""Cached public A-share inputs for the owner-requested 2026-10-28 study.

Sina supplies the CURRENT listing universe; this does not recover delisted
stocks or historical ST membership. Tencent supplies unadjusted daily OHLC,
corporate-action annotations and hourly OHLC. No trading outcomes are computed.
Each response is retained with URL and retrieval time. Failed sources are never
silently replaced. Public prices only; no broker credentials or account access.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'data/ashare/public-20261028'


def get(url, path):
    if path.exists():
        return json.loads(path.read_text())['data']
    errors = []
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.load(response)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(url=url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                           data=data, prior_errors=errors)
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False))
            temp.replace(path)
            return data
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
            time.sleep(.4 * (attempt+1))
    raise RuntimeError(f'{url}: {errors}')


def daily(symbol):
    url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param='
           f'{symbol},day,2026-03-01,2026-10-28,320,')
    data = get(url, CACHE / 'daily' / f'{symbol}.json')
    rows = data.get('data', {}).get(symbol, {}).get('day', [])
    if not rows:
        raise ValueError(f'No unadjusted daily rows: {symbol}')
    return rows


def hourly(symbol):
    url = f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},m60,,1023'
    data = get(url, CACHE / 'hourly' / f'{symbol}.json')
    rows = data.get('data', {}).get(symbol, {}).get('m60', [])
    if not rows:
        raise ValueError(f'No hourly rows: {symbol}')
    return rows


def universe():
    base = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
    count = int(get(base+'Market_Center.getHQNodeStockCount?node=hs_a', CACHE/'count.json'))
    def page(i):
        return get(base+'Market_Center.getHQNodeData?'
                   f'page={i}&num=80&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page',
                   CACHE/'universe'/f'page-{i:03d}.json')
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(page, range(1, math.ceil(count/80)+1)))
    rows = [row for p in pages for row in p]
    if len({r['symbol'] for r in rows}) != len(rows) or len(rows) != count:
        raise ValueError(f'Listing pagination drift: {len(rows)} vs {count}')
    main = [r for r in rows if r['symbol'].startswith(('sh600','sh601','sh603','sh605',
                                                       'sz000','sz001','sz002','sz003'))]
    dest = CACHE/'mainboard.json'
    dest.write_text(json.dumps(dict(asof='2026-10-28', all_listed=count,
        membership='current mainboard; historical survivor/ST caveat', rows=main), ensure_ascii=False, indent=2))
    return main


def download():
    rows = universe()
    failures, completed = [], 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(daily, row['symbol']): row['symbol'] for row in rows}
        for future in as_completed(jobs):
            completed += 1
            try:
                future.result()
            except Exception as exc:
                failures.append(dict(symbol=jobs[future], error=str(exc)))
            if completed % 200 == 0 or completed == len(jobs):
                print(json.dumps(dict(done=completed, total=len(jobs), failures=len(failures))), flush=True)
    (CACHE/'download-status.json').write_text(json.dumps(dict(
        total=len(rows), failures=failures, finished_at=datetime.now(timezone.utc).isoformat()), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download:
        download()
