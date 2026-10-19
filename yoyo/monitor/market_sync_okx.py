"""OKX public swap adapter for the live market observer (owner 2026-10-16).

Official contract: https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history
Rows are [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]; confirm=1 only.
The observer's internal ascending row schema is preserved, while exchange
storage and forward observations are isolated from historical Binance data.
"""
import math
import re
import threading
import time

import requests

from .market_sync import Binance, MarketSyncError, Observer as BaseObserver

OKX = 'https://www.okx.com'
PATHS = {'/api/v5/public/time', '/api/v5/public/instruments', '/api/v5/market/history-candles'}
BARS = {'15m':(900000,'15m'), '30m':(1800000,'30m'), '1h':(3600000,'1H'), '1d':(86400000,'1Dutc')}


class PublicOKX(Binance):
    source = {'id':'okx-usdt-swap','label':'OKX USDT 永续'}

    def __init__(self, rate=4.0, *, runtime=None):
        from .okx import OKX as SharedOKX
        super().__init__(rate)
        self.shared = SharedOKX(rate=rate, runtime=runtime)

    def get(self, path, params=None):
        from .okx import MarketError
        if path not in PATHS:
            raise MarketSyncError('okx_path_not_allowed')
        try:
            return self.shared.get(path, params)
        except MarketError as error:
            raise MarketSyncError(str(error)) from error
        finally:
            self.requests = self.shared.requests

    def server_ms(self):
        before = int(time.time()*1000)
        stamp = int(self.get('/api/v5/public/time')[0]['ts'])
        offset = stamp-(before+int(time.time()*1000))//2
        if abs(offset)>120000:
            raise MarketSyncError('local_clock_out_of_sync')
        self.shared.offset_ms = offset
        return stamp

    def coins(self):
        rows = self.get('/api/v5/public/instruments',{'instType':'SWAP'})
        result = sorted(r['instId'].replace('-USDT-SWAP','USDT') for r in rows
                        if re.fullmatch(r'[A-Z0-9]{1,24}-USDT-SWAP',r.get('instId',''))
                        and r.get('instType')=='SWAP' and r.get('settleCcy')=='USDT'
                        and r.get('ctType')=='linear' and r.get('state')=='live'
                        and r.get('instCategory')=='1' and r.get('ruleType','normal')=='normal')
        if not result:
            raise MarketSyncError('okx_crypto_catalog_unavailable')
        return result

    def klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        if not re.fullmatch(r'[A-Z0-9]{1,24}USDT',symbol) or interval not in BARS:
            raise MarketSyncError('okx_contract_or_interval_invalid')
        step, bar = BARS[interval]
        end = min(end_ms, start_ms+step*limit-1)
        cursor, result = end+1, {}
        while cursor > start_ms:
            page_size = min(300, max(1, (cursor-start_ms+step-1)//step))
            rows = self.get('/api/v5/market/history-candles',{'instId':symbol[:-4]+'-USDT-SWAP','bar':bar,'after':str(cursor),'limit':str(page_size)})
            if not rows:
                break
            oldest = cursor
            for row in rows:
                if len(row) < 9:
                    raise MarketSyncError('okx_candle_schema_changed')
                ts = int(row[0]);oldest = min(oldest,ts)
                if row[8] != '1' or not start_ms <= ts <= end or ts+step-1 > end_ms:
                    continue
                values = list(map(float,row[1:8]))
                o,h,l,c,vol,base,quote=values
                if not all(math.isfinite(v) for v in values) or min(o,h,l,c)<=0 or min(vol,base,quote)<0 or not l<=min(o,c)<=max(o,c)<=h:
                    raise MarketSyncError('okx_candle_values_invalid')
                result[ts] = [ts,o,h,l,c,base,ts+step-1,quote]
            if oldest >= cursor:
                raise MarketSyncError('okx_pagination_stalled')
            cursor = oldest
            if len(rows)<page_size:
                break
        return [result[k] for k in sorted(result)][:limit]


class Observer(BaseObserver):
    def __init__(self,runtime,client=None):
        now=int(time.time()*1000)
        super().__init__(runtime,client=client or PublicOKX(runtime=runtime), database='market-sync-okx-v1.sqlite3',
                         backfill_start=(now//3600000-7*24)*3600000)
