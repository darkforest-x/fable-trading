"""Public OKX SWAP universe and confirmed in-memory candles.

API contract: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
Only public GETs are used, at <=8 requests/sec across scanner threads. The
monitor deliberately does not write to the VPS-owned OHLCV cache/forward log.
15m/30m/1H/4H bars use exchange boundaries; the confirming daily bar is 1Dutc.
"""
from __future__ import annotations

import math
import threading
import time
import requests

from yoyo.monitor import TIMEFRAMES


class MarketError(RuntimeError):
    pass


def parse_rows(rows, timeframe, server_ms):
    if not isinstance(rows, list):
        raise MarketError("invalid_candle_payload")
    result = {}
    period = TIMEFRAMES[timeframe]
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 9:
            raise MarketError("invalid_candle_shape")
        if str(raw[8]) == "0":
            continue
        if str(raw[8]) != "1":
            raise MarketError("invalid_confirmation_flag")
        try:
            stamp = int(raw[0])
            values = [float(x) for x in raw[1:6]]
        except (ValueError, TypeError):
            raise MarketError("invalid_candle_number") from None
        o, h, l, c, v = values
        if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0:
            raise MarketError("invalid_candle_value")
        if h < max(o, l, c) or l > min(o, h, c) or stamp % period:
            raise MarketError("invalid_candle_bounds_or_alignment")
        if stamp + period > server_ms:
            continue
        row = dict(t=stamp, o=o, h=h, l=l, c=c, v=v)
        if stamp in result and result[stamp] != row:
            raise MarketError("conflicting_confirmed_candles")
        result[stamp] = row
    return [result[k] for k in sorted(result)]


def merge_rows(previous, new, timeframe):
    values = {r["t"]: r for r in previous}
    for r in new:
        if r["t"] in values and values[r["t"]] != r:
            raise MarketError("exchange_revised_confirmed_candle")
        values[r["t"]] = r
    ordered = [values[k] for k in sorted(values)]
    # A missing bar resets the available causal warmup; never fabricate a bar.
    gap_count = 0
    start = 0
    for i in range(1, len(ordered)):
        if ordered[i]["t"] - ordered[i-1]["t"] != TIMEFRAMES[timeframe]:
            gap_count += 1
            start = i
    return ordered[start:], gap_count


class OKX:
    def __init__(self, rate=8.0):
        self._lock = threading.Lock()
        self._next = 0.0
        self.rate = rate
        self.local = threading.local()
        self.offset_ms = 0
        self.requests = 0

    def clock(self):
        return int(time.time() * 1000) + self.offset_ms

    def get(self, path, params=None):
        if not path.startswith(("/api/v5/public/", "/api/v5/market/")):
            raise MarketError("non_public_endpoint_forbidden")
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers["User-Agent"] = "Fable-ImpulseMonitor/1.0"
        for attempt in range(3):
            with self._lock:
                wait = max(0, self._next - time.monotonic())
                self._next = max(self._next, time.monotonic()) + 1 / self.rate
                self.requests += 1
            if wait:
                time.sleep(wait)
            try:
                response = self.local.session.get("https://www.okx.com" + path, params=params, timeout=(5, 12))
                payload = response.json()
                if response.status_code == 429 or payload.get("code") == "50011":
                    time.sleep(2 * (attempt + 1))
                    continue
                if response.status_code != 200 or str(payload.get("code")) != "0":
                    raise MarketError("okx_response_" + str(response.status_code) + "_" + str(payload.get("code", "unknown")))
                if not isinstance(payload.get("data"), list):
                    raise MarketError("invalid_data_schema")
                return payload["data"]
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise MarketError("okx_network_or_json_error") from None
                time.sleep(attempt + 1)
        raise MarketError("okx_rate_limit_exhausted")

    def synchronize(self):
        before = int(time.time() * 1000)
        rows = self.get("/api/v5/public/time")
        after = int(time.time() * 1000)
        candidate = int(rows[0]["ts"]) - (before + after) // 2
        if abs(candidate) > 120000:
            raise MarketError("local_clock_out_of_sync")
        self.offset_ms = candidate
        return self.offset_ms

    def instruments(self):
        rows = self.get("/api/v5/public/instruments", {"instType": "SWAP"})
        live = [r for r in rows if r.get("state") == "live" and r.get("instType") == "SWAP"]
        if not live:
            raise MarketError("empty_live_swap_universe")
        return sorted(live, key=lambda r: (r["instId"] not in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"), r["instId"]))

    def candles(self, symbol, timeframe, previous=None, limit=720):
        previous = previous or []
        expected = self.clock() // TIMEFRAMES[timeframe] * TIMEFRAMES[timeframe] - TIMEFRAMES[timeframe]
        if previous and previous[-1]["t"] >= expected:
            return previous, 0
        if previous:
            rows = self.get("/api/v5/market/candles", {"instId": symbol, "bar": timeframe, "limit": "300"})
            new = parse_rows(rows, timeframe, self.clock())
            if new and new[0]["t"] <= previous[-1]["t"] + TIMEFRAMES[timeframe]:
                combined, gaps = merge_rows(previous, new, timeframe)
                # Keep the original in-process recurrence origin. Trimming a
                # rolling seed would subtly rewrite IMACD/focus state.
                return combined, gaps
        collected = []
        after = None
        for _ in range(5):
            params = {"instId": symbol, "bar": timeframe, "limit": "300"}
            if after is not None:
                params["after"] = str(after)
            rows = self.get("/api/v5/market/candles", params)
            if not rows:
                break
            batch = parse_rows(rows, timeframe, self.clock())
            collected += batch
            oldest = min(int(r[0]) for r in rows)
            if oldest == after or len(collected) >= limit or len(rows) < 300:
                break
            after = oldest
        combined, gaps = merge_rows([], collected, timeframe)
        return combined[-limit:], gaps
