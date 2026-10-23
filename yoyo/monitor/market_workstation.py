"""Read-only, demand-driven public data for the Stitch market workstation.

Source: https://www.okx.com/docs-v5/en/#public-data and #order-book-trading-market-data.
Independent field caches keep slow public endpoints off the UI and execution
paths. Trade delta covers the returned sample, never an invented 15m CVD. Swap
sizes are contracts: conversion requires the instrument's actual ctVal/ctMult.
Liquidations describe reported past events, not predicted liquidation levels.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import math
import re
import threading
import time

from fastapi import HTTPException, Query
from yoyo.research_workspace.paper_desk_market import PublicHTTP

SYMBOL = r'^[A-Z0-9]{1,24}-USDT-SWAP$'
FIELDS = {
    'funding': ('public/funding-rate', 30),
    'interest': ('public/open-interest', 15),
    'mark': ('public/mark-price', 10),
    'index': ('market/index-tickers', 10),
    'instrument': ('public/instruments', 3600),
    'trades': ('market/trades', 10),
    'liquidations': ('public/liquidation-orders', 60),
}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def notional(row, instrument, price_key='px'):
    """Convert a linear swap's contracts to quote currency; no guessed defaults."""
    size, price, value = number(row.get('sz')), number(row.get(price_key)), number(instrument.get('ctVal'))
    multiplier = number(instrument.get('ctMult')) if instrument.get('ctMult') else 1
    if any(x is None or x <= 0 for x in [size, price, value, multiplier]):
        return None
    currency = instrument.get('ctValCcy')
    if currency == 'USDT':
        return size * value * multiplier
    if currency == instrument.get('instId', '').split('-')[0]:
        return size * value * multiplier * price
    return None


def trade_flow(rows, instrument, now_ms):
    """Use only returned, deduplicated, non-future trades from the last 15m."""
    seen, items = set(), []
    for row in rows:
        key = row.get('tradeId')
        ts = number(row.get('ts'))
        if not key or key in seen or row.get('instId') != instrument.get('instId'):
            continue
        if ts is None or not now_ms - 900000 <= ts <= now_ms + 1000 or row.get('side') not in ('buy', 'sell'):
            continue
        amount = notional(row, instrument)
        if amount is None:
            continue
        seen.add(key)
        items.append(dict(id=key, ts=int(ts), price=number(row.get('px')), side=row['side'], notional_usdt=amount))
    items.sort(key=lambda row: row['ts'])
    if not items:
        return {'available': False, 'items': [], 'reason': '没有可换算的近期成交样本'}
    buy = sum(r['notional_usdt'] for r in items if r['side'] == 'buy')
    sell = sum(r['notional_usdt'] for r in items if r['side'] == 'sell')
    cumulative, curve = 0, []
    for row in items:
        cumulative += row['notional_usdt'] * (1 if row['side'] == 'buy' else -1)
        curve.append({'ts': row['ts'], 'value': cumulative})
    return dict(available=True, count=len(items), start_ms=items[0]['ts'], end_ms=items[-1]['ts'],
                buy_usdt=buy, sell_usdt=sell, delta_usdt=buy-sell, buy_ratio=buy/(buy+sell),
                curve=curve, items=sorted(items, key=lambda r: r['notional_usdt'], reverse=True)[:8],
                scope='OKX 最近最多 500 笔成交样本；非完整 15 分钟累计量')


def liquidation_events(rows, instrument, now_ms):
    items, seen = [], set()
    for row in rows:
        if row.get('instId') != instrument.get('instId'):
            continue
        for detail in row.get('details', []):
            ts = number(detail.get('ts'))
            amount = notional(detail, instrument, 'bkPx')
            key = (detail.get('ts'), detail.get('posSide'), detail.get('bkPx'), detail.get('sz'))
            if key in seen or ts is None or not now_ms - 10800000 <= ts <= now_ms + 1000 or amount is None:
                continue
            if detail.get('posSide') not in ('long', 'short'):
                continue
            seen.add(key)
            items.append(dict(ts=int(ts), price=number(detail.get('bkPx')), side=detail['posSide'], notional_usdt=amount))
    items.sort(key=lambda r: r['ts'], reverse=True)
    return dict(items=items[:12], count=len(items),
                long_usdt=sum(r['notional_usdt'] for r in items if r['side'] == 'long'),
                short_usdt=sum(r['notional_usdt'] for r in items if r['side'] == 'short'),
                scope='OKX 接口返回的近 3 小时已发生强平样本；价格为破产价，非未来清算预测')


class WorkstationData:
    def __init__(self, http=None, clock=time.time):
        self.http = http or PublicHTTP()
        self.clock = clock
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='market-display')
        self.slots = threading.BoundedSemaphore(8)
        self.closed = False

    def _fetch(self, symbol, field, entry):
        path, ttl = FIELDS[field]
        params = {'instId': symbol}
        if field in ('interest', 'mark', 'instrument'):
            params['instType'] = 'SWAP'
        if field == 'index':
            params = {'instId': symbol.removesuffix('-SWAP')}
        if field == 'trades':
            params['limit'] = '500'
        if field == 'liquidations':
            params = dict(instType='SWAP', uly=symbol.removesuffix('-SWAP'), state='filled', limit='100')
        try:
            rows = self.http.get(path, **params)
            expected = symbol.removesuffix('-SWAP') if field == 'index' else symbol
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('invalid_public_payload')
            if field not in ('liquidations',) and (not rows or any(row.get('instId') != expected for row in rows)):
                raise ValueError('public_symbol_mismatch')
            now = self.clock()
            timestamps = [number(row.get('ts')) for row in rows if row.get('ts')]
            source_ms = max((ts for ts in timestamps if ts is not None), default=now * 1000)
            if field not in ('liquidations', 'instrument', 'trades') and (source_ms > now*1000 + 5000 or now*1000-source_ms > 120000):
                raise ValueError('public_data_stale')
            with self.lock:
                entry.update(data=rows, received_ms=int(now*1000), refresh_ms=int((now+ttl)*1000),
                             expires_ms=int((now+ttl*3)*1000), error=None)
        except Exception:
            with self.lock:
                # Do not expose transport URLs or credentials; stale data remains labelled.
                entry['error'] = 'OKX 公开数据暂不可用'
        finally:
            with self.lock:
                entry.update(pending=False, retry_ms=int((self.clock()+10)*1000))
            self.slots.release()

    def snapshot(self, symbol):
        if not re.fullmatch(SYMBOL, symbol):
            raise ValueError('invalid_symbol')
        now = int(self.clock()*1000)
        fields = {}
        with self.lock:
            for field in FIELDS:
                key = (symbol, field)
                entry = self.cache.setdefault(key, {'data': [], 'pending': False, 'expires_ms': 0})
                self.cache.move_to_end(key)
                if not self.closed and entry.get('refresh_ms', 0) <= now and not entry['pending'] and entry.get('retry_ms', 0) <= now and self.slots.acquire(False):
                    entry['pending'] = True
                    self.pool.submit(self._fetch, symbol, field, entry)
                fields[field] = deepcopy(entry)
                fields[field]['stale'] = entry['expires_ms'] <= now
            while len(self.cache) > 56:
                removable = next((key for key, entry in self.cache.items() if not entry['pending']), None)
                if removable is None:
                    break
                del self.cache[removable]
        instrument = fields['instrument']['data']
        result = dict(symbol=symbol, source='OKX public REST', fetched_ms=now, fields=fields)
        if instrument and not fields['instrument']['stale'] and not fields['instrument'].get('error'):
            if not fields['trades']['stale'] and not fields['trades'].get('error'):
                result['flow'] = trade_flow(fields['trades']['data'], instrument[0], now)
            if not fields['liquidations']['stale'] and not fields['liquidations'].get('error'):
                result['liquidations'] = liquidation_events(fields['liquidations']['data'], instrument[0], now)
        # Raw trade arrays are unnecessary once the bounded UI projection exists.
        for name in ['instrument', 'trades', 'liquidations']:
            fields[name].pop('data', None)
        return result

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.http.close()


def install(app):
    service = WorkstationData()
    app.state.market_workstation = service

    @app.get('/api/market-workstation')
    def snapshot(symbol: str = Query('BTC-USDT-SWAP', pattern=SYMBOL)):
        # Retired tabs must not restart demand-driven market-only collection.
        raise HTTPException(410, '市场页监控已停用')
