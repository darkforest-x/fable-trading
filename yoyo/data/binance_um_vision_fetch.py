"""Resumable fetch of public Binance USD-M archives from data.binance.vision.

Used by the Monday1618 non-price study (owner Notion task 区间1.618测试,
2026-10-23). Downloads, without API keys:
  daily   futures/um/daily/metrics/{S}/{S}-metrics-{YYYY-MM-DD}.zip
          (5m open interest, top-trader and global long/short, taker ratio)
  monthly futures/um/monthly/fundingRate/{S}/{S}-fundingRate-{YYYY-MM}.zip
  monthly futures/um/monthly/klines/{S}/15m/{S}-15m-{YYYY-MM}.zip
          (OHLCV plus taker-buy volume)
Files land unchanged under --out/{kind}/{S}/; existing non-empty files are
skipped, a missing archive (404) is recorded, not invented. Writes a receipt
with counts per symbol and kind. No parsing or feature logic lives here.
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

BASE = 'https://data.binance.vision/data/futures/um'


def jobs(symbols, start, end):
    days = [start+timedelta(days=i) for i in range((end-start).days+1)]
    months = sorted({(d.year, d.month) for d in days})
    for s in symbols:
        for d in days:
            yield 'metrics', s, f'{BASE}/daily/metrics/{s}/{s}-metrics-{d:%Y-%m-%d}.zip'
        for y, m in months:
            yield 'funding', s, f'{BASE}/monthly/fundingRate/{s}/{s}-fundingRate-{y}-{m:02d}.zip'
            yield 'klines15m', s, f'{BASE}/monthly/klines/{s}/15m/{s}-15m-{y}-{m:02d}.zip'


def fetch(out, kind, symbol, url):
    path = out/kind/symbol/url.rsplit('/', 1)[1]
    if path.exists() and path.stat().st_size:
        return kind, symbol, 'cached'
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
            path.with_suffix('.part').write_bytes(data)
            path.with_suffix('.part').rename(path)
            return kind, symbol, 'fetched'
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return kind, symbol, 'missing'
        except Exception:
            pass
        time.sleep(1+attempt*2)
    return kind, symbol, 'failed'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', required=True)
    p.add_argument('--start', required=True); p.add_argument('--end', required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--workers', type=int, default=12)
    a = p.parse_args()
    todo = list(jobs(a.symbols, date.fromisoformat(a.start), date.fromisoformat(a.end)))
    counts = {}
    with ThreadPoolExecutor(a.workers) as pool:
        for n, (kind, symbol, status) in enumerate(pool.map(lambda j: fetch(a.out, *j), todo), 1):
            key = f'{symbol}/{kind}'
            counts.setdefault(key, {}).setdefault(status, 0)
            counts[key][status] += 1
            if n % 2000 == 0:
                print(n, len(todo), flush=True)
    receipt = dict(symbols=a.symbols, start=a.start, end=a.end, jobs=len(todo), counts=counts,
                   base=BASE, no_content_hash=True)
    (a.out/'fetch_receipt.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({k: v for k, v in counts.items() if set(v) - {'cached', 'fetched'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
