"""Bounded, on-demand chart windows from official Binance UM 1m daily archives.

Reuse the registered touch-day CSV cache (exp-monday-limit-partial-bar-score-
20261027-v1) read-only. New days go to the monitor's display cache, never into
the frozen research inputs. No execution or scoring is performed here.
Source/CSV contract: https://github.com/binance/binance-public-data#futures
"""
import csv
import io
import math
from pathlib import Path
import re
import tempfile
import time
from urllib.request import urlopen
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
REGISTERED = ROOT / 'data/crypto/market/kline_binance_um1m_touchdays_20261027'
URL = 'https://data.binance.vision/data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip'
DAY = 86400000


def parse(body, day_start):
    rows = {}
    for r in csv.reader(io.StringIO(body)):
        if not r or r[0] == 'open_time':
            continue
        if len(r) < 6:
            raise ValueError('invalid_binance_minute_row')
        t = int(r[0])
        values = list(map(float, r[1:6]))
        o, h, low, c, volume = values
        if (t % 60000 or not day_start <= t < day_start + DAY
                or not all(map(math.isfinite, values)) or min(o,h,low,c) <= 0
                or volume < 0 or h < max(o,c,low) or low > min(o,c,h) or t in rows):
            raise ValueError('invalid_binance_minute_archive')
        rows[t] = dict(time=t, open=o, high=h, low=low, close=c, volume=volume)
    if not rows:
        raise ValueError('empty_binance_minute_archive')
    return rows


def read(symbol, start, end, *, registered=REGISTERED, cache=None):
    """At most 14 UTC days and 20 seconds per attempt; retries reuse saved days."""
    if not re.fullmatch(r'[A-Z0-9]{1,32}USDT', symbol) or not 0 < end-start <= 13*DAY:
        raise ValueError('unsupported_minute_window')
    if cache is None:
        from yoyo.data.candle_hub import runtime_path
        cache = runtime_path() / 'strategy-minute-cache'
    deadline, result = time.monotonic()+20, {}
    for t in range(start//DAY*DAY, end, DAY):
        day = datetime.fromtimestamp(t/1000, timezone.utc).strftime('%Y-%m-%d')
        old, dest = Path(registered)/symbol/f'{day}.csv', Path(cache)/symbol/f'{day}.csv'
        existing = old if old.is_file() else dest
        if existing.is_file():
            rows = parse(existing.read_text(), t)
        else:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('minute_window_retry')
            with urlopen(URL.format(s=symbol,d=day),timeout=min(5,remaining)) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('oversized_minute_archive')
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    files=z.infolist()
                    if len(files)!=1 or files[0].file_size>4_000_000:
                        raise ValueError('invalid_minute_archive')
                    body=z.read(files[0]).decode()
            except zipfile.BadZipFile as e:
                raise ValueError('invalid_minute_archive') from e
            rows = parse(body,t)
            dest.parent.mkdir(parents=True,exist_ok=True)
            # A cancelled/failed read never leaves a successful-looking CSV.
            with tempfile.NamedTemporaryFile('w',dir=dest.parent,delete=False) as f:
                f.write(body); temporary=Path(f.name)
            temporary.replace(dest)
        result.update({t:r for t,r in rows.items() if start<=t<end})
    return [result[t] for t in sorted(result)]
