"""Shared OKX connections and independent refreshers for the local paper desk.

HTTPX Client pools connections (https://www.python-httpx.org/advanced/clients/).
Readers consume immutable snapshots; source timestamps still govern execution.
Slow instrument/tier requests cannot stall ticker or mark-price publication.
Transport failures retire the affected pool: a failed proxy TLS tunnel can
otherwise occupy its connection slot indefinitely (httpcore 1.0.9 http_proxy).
Only public GETs are retried, once; in-flight readers drain before pool close.
"""
import logging
import threading
import time

import httpx

log = logging.getLogger(__name__)


class PublicHTTP:
    def __init__(self):
        self.lock = threading.Lock()
        self.client = None
        self.users = {}

    def _acquire(self):
        with self.lock:
            if self.client is None:
                self.client = httpx.Client(base_url='https://www.okx.com/api/v5/',
                                          timeout=httpx.Timeout(8, connect=4),
                                          limits=httpx.Limits(max_connections=6, max_keepalive_connections=6,
                                                              keepalive_expiry=60))
            client = self.client
            self.users[client] = self.users.get(client, 0) + 1
            return client

    def _release(self, client, failed=False):
        with self.lock:
            # A late failure from an older pool cannot retire its replacement.
            if failed and self.client is client:
                self.client = None
            self.users[client] -= 1
            closing = self.users[client] == 0 and self.client is not client
            if closing:
                del self.users[client]
        if closing:
            client.close()

    def get(self, path, **params):
        for attempt in range(2):
            client = self._acquire()
            failed = False
            try:
                response = client.get(path, params=params)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get('code') != '0' or not isinstance(body.get('data'), list):
                    raise ValueError('OKX 公开数据返回异常')
                return body['data']
            except httpx.TransportError:
                failed = True
                if attempt:
                    raise
            finally:
                self._release(client, failed)

    def close(self):
        with self.lock:
            client, self.client = self.client, None
            closing = client is not None and self.users.get(client, 0) == 0
            if closing:
                self.users.pop(client, None)
        if closing:
            client.close()


class MarketRefresh:
    def __init__(self, desk, quotes, symbols, http):
        self.desk, self.quotes, self.symbols, self.http = desk, quotes, symbols, http
        self.stop = threading.Event()
        self.tier_wake = threading.Event()
        self.threads = []

    def _run(self, action, interval, wake=None):
        while not self.stop.is_set():
            if wake:
                wake.clear()
            try:
                action()
            except Exception:
                log.warning('Paper market refresh failed: %s', action.__name__, exc_info=True)
            if wake:
                self.stop.wait(.1)
                wake.wait(interval)
            else:
                self.stop.wait(interval)

    def _catalog(self):
        self.symbols.refresh()
        if not self.stop.is_set() and self.symbols.instruments:
            # Catalog and risk consume the same public instrument response.
            self.quotes.risk.instruments = self.symbols.instruments
            self.quotes.risk.instrument_at = self.symbols.cached_at
            self.tier_wake.set()

    def _tiers(self):
        risk = self.quotes.risk
        with risk.lock:
            wanted = [s for s, at in risk.requested.items() if time.monotonic()-at < 300]
        with self.desk.connect() as db:
            active = [r[0] for r in db.execute("SELECT DISTINCT symbol FROM positions WHERE CAST(remaining AS NUMERIC)>0 "
                                              "UNION SELECT symbol FROM limit_orders WHERE status='pending'")]
        for symbol in dict.fromkeys(active + wanted + ['BTC-USDT-SWAP', 'ETH-USDT-SWAP']):
            if self.stop.is_set():
                break
            try:
                risk.refresh_tiers([symbol], self.stop.is_set)
            except Exception:
                log.warning('Paper risk tier unavailable: %s', symbol, exc_info=True)

    def start(self):
        if self.threads:
            return
        self.stop.clear()
        self.quotes.background = self.symbols.background = self.quotes.risk.background = True
        self.quotes.risk.wake = self.tier_wake.set
        for name, action, interval, wake in [
                ('quotes', self.quotes.refresh, .5, None),
                ('marks', self.quotes.risk.refresh_marks, .5, None),
                ('catalog', self._catalog, 5, None),
                ('tiers', self._tiers, 5, self.tier_wake)]:
            thread = threading.Thread(target=self._run, args=(action, interval, wake),
                                      name='paper-market-' + name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        self.tier_wake.set()
        for thread in self.threads:
            thread.join(timeout=12)
        self.threads.clear()
        self.http.close()
