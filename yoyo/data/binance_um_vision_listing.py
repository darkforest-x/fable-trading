"""List Binance USD-M symbols and archive files from the public S3 index.

data.binance.vision is backed by the S3 bucket listing at
s3-ap-northeast-1.amazonaws.com/data.binance.vision (delimiter "/",
pagination by marker). Delisted symbols stay in the index, so a universe
built from it avoids survivorship bias. No parsing of candle content here.
"""
from __future__ import annotations
import re
import urllib.request

INDEX = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'


def _list(prefix):
    marker, out = '', []
    while True:
        url = f'{INDEX}?delimiter=/&prefix={prefix}' + (f'&marker={marker}' if marker else '')
        with urllib.request.urlopen(url, timeout=60) as r:
            xml = r.read().decode()
        out += re.findall(r'<Prefix>([^<]+)</Prefix>', xml)[1:] + re.findall(r'<Key>([^<]+)</Key>', xml)
        nxt = re.search(r'<NextMarker>([^<]+)</NextMarker>', xml)
        if '<IsTruncated>true</IsTruncated>' not in xml or not nxt:
            return out
        marker = nxt.group(1)


def symbols(kind='klines'):
    """All USD-M symbol directories under monthly/{kind}/."""
    base = f'data/futures/um/monthly/{kind}/'
    return sorted(p[len(base):].strip('/') for p in _list(base) if p.startswith(base))


def files(symbol, interval='15m'):
    """Monthly kline zip keys for one symbol and interval."""
    keys = _list(f'data/futures/um/monthly/klines/{symbol}/{interval}/')
    return sorted(k for k in keys if k.endswith('.zip'))
