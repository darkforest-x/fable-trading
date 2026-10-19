"""Request-scoped parallel GETs for manual previews; never execution writes.

Profiling found repeated balances and serial TLS connections dominated sizing.
Use one full balance receipt and overlap independent reads, including ticker.
Recheck the quote's existing 5-second age limit at use; refresh old quotes.
No response survives a preview and the worker still re-reads before entry.
HTTPX pooling/thread sharing: https://www.python-httpx.org/advanced/clients/
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from copy import copy, deepcopy
from io import BytesIO
from threading import Lock
import time

import httpx

from .client import Client, TransportError
from .engine import one


class ReadPacer:
    """Independent REST endpoint budgets, shared by previews in this service.

    OKX limits each endpoint separately (private: UID; public: IP). Keep
    headroom below account/config 5/2s and algo-list 20/2s for the account's
    worker; this reader is not the account's only API consumer.
    Other reads use a conservative 5/2s, below their documented ceilings.
    https://www.okx.com/docs-v5/en/#overview-rate-limits
    """
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.lock, self.buckets = Lock(), {}
        self.clock, self.sleep = clock, sleep

    def wait(self, path):
        interval = (0.81 if path == "/api/v5/account/config" else
                    0.21 if path == "/api/v5/trade/orders-algo-pending" else 0.41)
        with self.lock:
            bucket = self.buckets.setdefault(path, [Lock(), None])
        # A sleeping algo/config caller must not hold up a different endpoint.
        with bucket[0]:
            if bucket[1] is not None:
                self.sleep(max(0, interval - (self.clock() - bucket[1])))
            bucket[1] = self.clock()


class PooledOpener:
    """GET-only urllib adapter; auth/response validation stay in Client."""
    def __init__(self, *, transport=None):
        self.session = httpx.Client(follow_redirects=False, timeout=10,
                                    limits=httpx.Limits(max_connections=4, max_keepalive_connections=4,
                                                       keepalive_expiry=120),
                                    transport=transport)

    def open(self, request, timeout):
        if request.get_method() != "GET":
            raise PermissionError("read-only client")
        try:
            outgoing = self.session.build_request("GET", request.full_url,
                                                  headers=dict(request.header_items()), timeout=timeout)
            # Connections may be reused, authentication never is. Match urllib's
            # cookie-free behavior even if the venue sets an incidental cookie.
            outgoing.headers.pop("cookie", None)
            response = self.session.send(outgoing, stream=True)
            try:
                if response.is_redirect:
                    raise TransportError("redirect refused")
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 8 * 1024 * 1024:
                        raise TransportError("response too large")
                return BytesIO(data)
            finally:
                response.close()
        except httpx.HTTPError:
            raise TransportError("GET response unavailable; preview again") from None

    def close(self):
        self.session.close()


class PreviewClient(Client):
    def __init__(self, original, opener, pacer):
        super().__init__(original.config, original.credentials, writes=False, opener=opener)
        self.pacer = pacer

    def pace(self, path):
        self.pacer.wait(path)


class PreviewReads:
    """Deduplicate only this preview; quotes must still pass the final age gate."""
    def __init__(self, client, clock):
        self.client, self.futures = client, {}
        self.clock, self.quote_future, self.quote_symbol = clock, None, None
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="manual-preview")

    def submit(self, path, **query):
        # Both consumers select USDT details; unfiltered totalEq also includes
        # other trading-account assets. Never alias other currency filters.
        if path == "/api/v5/account/balance" and query == {"ccy": "USDT"}:
            query = {}
        key = path, tuple(sorted(query.items()))
        if key not in self.futures:
            self.futures[key] = self.pool.submit(self.client.get, path, **query)
        return self.futures[key]

    def get(self, path, **query):
        if path == "/api/v5/market/ticker":
            if self.quote_future is not None and query == {"instId": self.quote_symbol}:
                requested, rows = self.quote_future.result()
                if self.clock() - int(one(rows)["ts"]) > 5000:
                    requested, rows = self.read_quote(**query)
            else:
                requested, rows = self.read_quote(**query)
            self.quote_request_ms = requested
            return deepcopy(rows)
        return deepcopy(self.submit(path, **query).result())

    def read_quote(self, **query):
        requested = self.clock()
        return requested, self.client.get("/api/v5/market/ticker", **query)

    def post(self, *args, **kwargs):
        raise PermissionError("read-only client")

    def prefetch(self, symbol, *, shared):
        # A valid quote can arrive while inventory is being checked. This also
        # overlaps the existing bounded exchange-clock-lead wait with useful I/O.
        self.quote_symbol = symbol
        self.quote_future = self.pool.submit(self.read_quote, instId=symbol)
        for path in ("config", "balance", "positions"):
            self.submit("/api/v5/account/" + path)
        self.submit("/api/v5/account/instruments", instType="SWAP", instId=symbol)
        self.submit("/api/v5/trade/orders-pending", **({"limit": "100"} if shared else {}))
        for kind in ("conditional", "oco", "trigger", "move_order_stop"):
            self.submit("/api/v5/trade/orders-algo-pending", ordType=kind, limit="100")
        instrument = one(self.get("/api/v5/account/instruments", instType="SWAP", instId=symbol))
        self.submit("/api/v5/public/position-tiers", instType="SWAP", tdMode="cross",
                    instFamily=instrument["instFamily"])

    def close(self):
        # Do not orphan reads or close their connections while still in flight.
        self.pool.shutdown(wait=True, cancel_futures=True)


@contextmanager
def preview_engine(engine, pacer, opener=None):
    """A local engine copy prevents caches from reaching confirmation/worker."""
    with ExitStack() as stack:
        client = engine.client
        if isinstance(client, Client):
            if opener is None:
                opener = PooledOpener()
                stack.callback(opener.close)
            client = PreviewClient(client, opener, pacer)
        reads = PreviewReads(client, engine.clock)
        stack.callback(reads.close)
        local = copy(engine)
        local.client = reads
        yield local
