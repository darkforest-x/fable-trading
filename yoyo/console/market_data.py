"""Read-only OKX candles for the local console's live chart.

This module is deliberately isolated from model replay and every trading layer.
It shares the local public read cache and returns display data to the browser.
It never writes canonical candles, forward logs, model artifacts or execution state.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Mapping
from yoyo.monitor.okx import OKX, MarketError

from yoyo.console.replay import ReplayError, normalize_symbol

DEFAULT_REST_BASE = "https://www.okx.com"
DEFAULT_WS_URL = "wss://ws.okx.com:8443/ws/v5/business"
ALLOWED_BARS = frozenset({"1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H"})
MAX_CANDLES = 300


class MarketDataError(RuntimeError):
    """Public market data is unavailable or violates the expected contract."""


@lru_cache(maxsize=4)
def _shared_client(base, timeout):
    return OKX(rate=3, include_open=True, base_url=base, timeout=(min(5, timeout), timeout))


def _finite_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"OKX candle {label} is not numeric") from exc
    if not math.isfinite(number):
        raise MarketDataError(f"OKX candle {label} is not finite")
    return number


def parse_okx_candles(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate OKX's newest-first `[ts,o,h,l,c,vol,...,confirm]` rows."""
    if str(payload.get("code")) != "0":
        raise MarketDataError(str(payload.get("msg") or "OKX candles request failed"))
    raw_rows = payload.get("data")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MarketDataError("OKX candles response is empty")

    by_time: dict[int, dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) < 9:
            raise MarketDataError("OKX candle row has an unexpected shape")
        try:
            time_seconds = int(raw[0]) // 1000
        except (TypeError, ValueError) as exc:
            raise MarketDataError("OKX candle timestamp is invalid") from exc
        open_price = _finite_number(raw[1], "open")
        high = _finite_number(raw[2], "high")
        low = _finite_number(raw[3], "low")
        close = _finite_number(raw[4], "close")
        volume = _finite_number(raw[5], "volume")
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise MarketDataError("OKX candle OHLC bounds are invalid")
        by_time[time_seconds] = {
            "time": time_seconds,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "confirmed": str(raw[8]) == "1",
        }
    return [by_time[key] for key in sorted(by_time)]


def fetch_recent_candles(
    symbol: str,
    *,
    bar: str = "15m",
    limit: int = MAX_CANDLES,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Read shared public candles without passing them to a model.

    API contract: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks
    WebSocket contract: https://www.okx.com/docs-v5/en/#websocket-api-public-channel-candlesticks-channel
    """
    try:
        normalized = normalize_symbol(symbol)
    except ReplayError as exc:
        raise MarketDataError(str(exc)) from exc
    if bar not in ALLOWED_BARS:
        raise MarketDataError(f"unsupported candle bar: {bar!r}")
    bounded_limit = max(2, min(int(limit), MAX_CANDLES))
    instrument = normalized.replace("_", "-")
    rest_base = os.environ.get("FABLE_SIGNAL_CONSOLE_OKX_REST_BASE", DEFAULT_REST_BASE).rstrip("/")
    try:
        rows = _shared_client(rest_base, timeout_seconds).get('/api/v5/market/candles', {
            'instId': instrument, 'bar': bar, 'limit': str(bounded_limit)})
        payload = {'code': '0', 'data': rows}
    except (OSError, ValueError, MarketError) as exc:
        raise MarketDataError(f"cannot load OKX candles: {exc}") from exc
    if not isinstance(payload, dict):
        raise MarketDataError("OKX candles response is not an object")
    candles = parse_okx_candles(payload)
    return {
        "ok": True,
        "source": "OKX public market data",
        "symbol": normalized,
        "instrument": instrument,
        "bar": bar,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "candles": candles,
        "stream": {
            "url": os.environ.get("FABLE_SIGNAL_CONSOLE_OKX_WS_URL", DEFAULT_WS_URL),
            "channel": f"candle{bar}",
            "instrument": instrument,
            "authentication": False,
        },
        "safety": {
            "display_only": True,
            "persisted": True,
            "storage": "shared_public_read_cache",
            "model_scored": False,
            "execution_eligible": False,
        },
    }
