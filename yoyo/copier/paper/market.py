"""Public last prices for paper fills. No API keys; OKX first, Gate as fallback.

OKX:  GET /api/v5/market/ticker?instId=BTC-USDT-SWAP  -> data[0].last
Gate: GET /api/v4/futures/usdt/tickers?contract=BTC_USDT -> [0].last
1m candles (closed bars only) for intrabar highs/lows between polls:
OKX:  GET /api/v5/market/candles?instId=..&bar=1m  -> [[ts,o,h,l,c,..,confirm]] newest first
(Gate's candlestick endpoint answers MISSING_REQUIRED_HEADER without an API key as of
2026-10-07, so Gate-only contracts stay on polled last prices.)
https://www.okx.com/docs-v5/en/#public-data-rest-api
https://www.gate.io/docs/developers/apiv4/#list-futures-tickers
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

OKX_TICKER = "https://www.okx.com/api/v5/market/ticker"
GATE_TICKER = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
MINUTE_MS = 60_000


def _positive(value) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


class PublicMarket:
    def __init__(self, fetch: Optional[Callable[[str, dict], object]] = None, ttl: float = 2.0) -> None:
        self._fetch = fetch or self._http_get
        self._ttl = ttl
        self._cache: dict[str, tuple[float, float, str]] = {}

    @staticmethod
    def _http_get(url: str, params: dict) -> object:
        with httpx.Client(timeout=8) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    def _okx(self, inst_id: str) -> Optional[float]:
        body = self._fetch(OKX_TICKER, {"instId": inst_id})
        rows = body.get("data") if isinstance(body, dict) else None
        return _positive(rows[0].get("last")) if rows else None

    def _gate(self, inst_id: str) -> Optional[float]:
        contract = inst_id.replace("-USDT-SWAP", "_USDT")
        body = self._fetch(GATE_TICKER, {"contract": contract})
        return _positive(body[0].get("last")) if isinstance(body, list) and body else None

    def last(self, inst_id: str) -> Optional[tuple[float, str]]:
        """(price, venue) or None when neither venue lists the contract."""
        cached = self._cache.get(inst_id)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1], cached[2]
        for venue, source in (("okx", self._okx), ("gate", self._gate)):
            try:
                price = source(inst_id)
            except Exception as exc:  # network or schema errors: try the next venue
                logger.debug("paper market %s %s failed: %s", venue, inst_id, exc)
                continue
            if price:
                self._cache[inst_id] = (time.monotonic(), price, venue)
                return price, venue
        return None

    def candles_1m(self, inst_id: str, venue: str, limit: int = 5,
                   now_ms: Optional[float] = None) -> list[tuple[int, float, float, float, float]]:
        """Closed 1m bars, oldest first: (start_ms, open, high, low, close)."""
        now_ms = now_ms if now_ms is not None else time.time() * 1000
        bars: list[tuple[int, float, float, float, float]] = []
        if venue != "okx":
            return bars
        try:
            body = self._fetch(OKX_CANDLES, {"instId": inst_id, "bar": "1m", "limit": str(limit)})
            for row in reversed((body or {}).get("data") or []):
                bars.append((int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        except Exception as exc:
            logger.debug("paper candles %s failed: %s", inst_id, exc)
            return []
        return [b for b in bars if b[0] + MINUTE_MS <= now_ms]
