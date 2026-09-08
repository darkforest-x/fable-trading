"""Public Binance spot acquisition, bounded before IO, with no candle storage.

API: https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market
Kline array positions: open time, OHLC, base volume, inclusive close time,
quote volume. endTime is inclusive; requests stop one millisecond before the
last complete interval boundary. No current listing/ticker is read in history.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pandas as pd

from yoyo.contracts.rotation import INTERVAL_SECONDS, RotationError, digest, iso, now_utc, utc

BINANCE_BASE = "https://api.binance.com"
NON_CRYPTO_BASES = frozenset({"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDD",
    "USDE", "USD1", "EUR", "EURI", "GBP", "TRY", "BRL", "ARS", "RUB", "BIDR",
    "AEUR", "PAXG", "XAUT", "XAU", "XAG", "INTW", "CRWV", "IREN"})
LEVERAGED_BASES = frozenset(base + suffix for base in
    ("BTC", "ETH", "BNB", "XRP", "ADA", "DOT", "LINK", "LTC", "BCH", "EOS", "TRX", "UNI", "SUSHI", "YFI")
    for suffix in ("UP", "DOWN", "BULL", "BEAR"))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RotationError("Binance public redirect rejected; venue cannot change")


def parse_klines(payload: object, *, interval: str, as_of: datetime) -> pd.DataFrame:
    """Check timestamps before parsing OHLCV, reject duplicate/conflicting bars."""
    if interval not in INTERVAL_SECONDS or not isinstance(payload, list) or not payload:
        raise RotationError("empty or invalid Binance kline response")
    duration = INTERVAL_SECONDS[interval] * 1000
    cutoff = int(utc(as_of).timestamp() * 1000)
    rows = []
    seen = set()
    for raw in payload:
        if not isinstance(raw, list) or len(raw) < 8:
            raise RotationError("Binance kline schema changed")
        try:
            stamp, close_stamp = int(raw[0]), int(raw[6])
        except (TypeError, ValueError, OverflowError) as exc:
            raise RotationError("invalid kline timestamp") from exc
        if stamp < 0 or stamp % duration or close_stamp != stamp + duration - 1:
            raise RotationError("unaligned kline or inconsistent close timestamp")
        if stamp + duration > cutoff:
            raise RotationError("source returned a candle beyond the requested closed cutoff")
        if stamp in seen:
            raise RotationError("duplicate kline timestamp")
        seen.add(stamp)
        try:
            o, h, l, c, v, q = [float(raw[i]) for i in (1, 2, 3, 4, 5, 7)]
        except (TypeError, ValueError, OverflowError) as exc:
            raise RotationError("invalid numeric kline value") from exc
        if not all(math.isfinite(x) for x in (o, h, l, c, v, q)):
            raise RotationError("nonfinite kline value")
        if min(o, h, l, c) <= 0 or min(v, q) < 0 or h < max(o, c, l) or l > min(o, c, h):
            raise RotationError("invalid kline OHLC/volume geometry")
        rows.append({"time": pd.Timestamp(stamp, unit="ms", tz="UTC"), "open": o,
                     "high": h, "low": l, "close": c, "volume": v,
                     "quote_volume": q, "confirmed": True})
    result = pd.DataFrame(rows).set_index("time").sort_index()
    if len(result) > 1 and not result.index.to_series().diff().iloc[1:].eq(pd.Timedelta(milliseconds=duration)).all():
        raise RotationError("source candle gap; missing bars are not filled")
    expected_last = (cutoff // duration) * duration - duration
    if result.index[-1].value // 1_000_000 != expected_last:
        raise RotationError("source is missing the latest closed candle")
    return result


class BinanceProvider:
    """Public, paced, timeout-bounded reads; transport is injectable for tests.

    A single transport failure is exposed. There is no silent venue fallback.
    `guard` is called immediately before every request, including discovery.
    """

    name = "Binance spot public API"

    def __init__(self, *, guard: Callable[[], None], transport: Optional[Callable] = None,
                 timeout: float = 12, request_spacing: float = .35):
        self.guard = guard
        self.transport = transport
        self.timeout = timeout
        self.request_spacing = request_spacing
        self._lock = threading.Lock()
        self._last = 0.0
        self.receipts = []

    def _get(self, endpoint: str, params: dict) -> object:
        self.guard()
        url = BINANCE_BASE + endpoint + ("?" + urlencode(params) if params else "")
        with self._lock:
            pause = self.request_spacing - (time.monotonic() - self._last)
            if pause > 0:
                time.sleep(pause)
            self._last = time.monotonic()
        self.guard()
        try:
            if self.transport is not None:
                payload = self.transport(url)
            else:
                request = Request(url, headers={"Accept": "application/json", "User-Agent": "fable-rotation-research/1"})
                with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                    raw = response.read(8_000_001)
                    if len(raw) > 8_000_000:
                        raise RotationError("Binance response exceeded size budget")
                    payload = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise RotationError("Binance public request failed: " + str(exc)) from exc
        if isinstance(payload, dict) and "code" in payload and payload.get("code") != 0:
            raise RotationError("Binance rejected request: " + str(payload.get("msg", payload["code"])))
        receipt = {"name": self.name, "url": url, "observed_at": iso(now_utc()),
                   "payload_sha256": digest(payload), "raw_persisted": False}
        with self._lock:
            self.receipts.append(receipt)
        return payload

    def candles(self, symbol: str, interval: str, *, as_of: datetime, limit: int) -> pd.DataFrame:
        duration = INTERVAL_SECONDS[interval] * 1000
        cutoff_ms = int(utc(as_of).timestamp() * 1000)
        end = (cutoff_ms // duration) * duration - 1
        payload = self._get("/api/v3/klines", {"symbol": symbol, "interval": interval,
                                             "endTime": end, "limit": limit})
        frame = parse_klines(payload, interval=interval, as_of=as_of)
        frame.attrs["source"] = self.name
        frame.attrs["input_digest"] = digest(payload)
        return frame

    def discover(self, *, maximum: int) -> tuple[list[str], list[dict], str]:
        """Freeze current spot membership and trailing-volume selection first.

        This is only callable from live mode after authorization. Selection
        includes all qualifying spot names before rank truncation, not gainers.
        Unknown assets still require catalog review before risk readiness.
        """
        info = self._get("/api/v3/exchangeInfo", {})
        tickers = self._get("/api/v3/ticker/24hr", {"type": "MINI"})
        if not isinstance(info, dict) or not isinstance(info.get("symbols"), list) or not isinstance(tickers, list):
            raise RotationError("exchange membership/ticker schema changed")
        volumes = {}
        for t in tickers:
            if isinstance(t, dict) and isinstance(t.get("symbol"), str):
                try:
                    v = float(t["quoteVolume"])
                except (KeyError, ValueError, TypeError):
                    continue
                if math.isfinite(v) and v >= 0:
                    volumes[t["symbol"]] = v
        eligible, excluded = [], []
        for m in info["symbols"]:
            if m.get("quoteAsset") != "USDT":
                continue
            symbol, base = m.get("symbol"), m.get("baseAsset")
            reason = None
            if m.get("status") != "TRADING" or m.get("isSpotTradingAllowed") is not True:
                reason = "not_active_spot"
            elif base in NON_CRYPTO_BASES or base in LEVERAGED_BASES:
                reason = "non_crypto_or_leveraged_asset"
            elif symbol not in volumes:
                reason = "missing_quote_volume"
            if reason:
                excluded.append({"symbol": symbol, "reason": reason})
            else:
                eligible.append(symbol)
        ordered = sorted(eligible, key=lambda s: (-volumes[s], s))
        benchmarks = [s for s in ("BTCUSDT", "ETHUSDT") if s in ordered]
        selected = benchmarks + [s for s in ordered if s not in benchmarks][:maximum - len(benchmarks)]
        excluded += [{"symbol": s, "reason": "outside_frozen_volume_budget"} for s in ordered if s not in selected]
        return selected, excluded, iso(now_utc())


class SyntheticProvider:
    """Deterministic engineering fixtures, always labelled synthetic, never prices.

    Synthetic rows are constructed in memory for UI/causality QA. They are not
    a fallback when the real provider fails and never support economic claims.
    """

    name = "Synthetic engineering scenario"

    def __init__(self, *, guard: Callable[[], None]):
        self.guard = guard
        self.receipts = []

    def candles(self, symbol: str, interval: str, *, as_of: datetime, limit: int) -> pd.DataFrame:
        self.guard()
        step = INTERVAL_SECONDS[interval]
        frequency = pd.Timedelta(seconds=step)
        end = pd.Timestamp(as_of).floor(frequency)
        index = pd.date_range(end=end - frequency, periods=limit, freq=frequency)
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:6], 16)
        baseline = 10 + seed % 90
        values = []
        for i in range(limit):
            close = baseline * (1 + .002 * i + .003 * math.sin(i / 3 + seed))
            if interval != "1d":
                close = baseline * (1 + .001 * math.sin(i))
                if i == limit - 3 and seed % 3:
                    close = baseline * 1.025
                elif i > limit - 3 and seed % 3:
                    close = baseline * (1.005 if seed % 3 == 1 else 1.20)
            previous = values[-1]["close"] if values else close * .999
            volume = 100000 * (3 if i == limit - 3 and interval != "1d" and seed % 3 else 1)
            values.append({"open": previous, "high": max(previous, close) * 1.002,
                           "low": min(previous, close) * .998, "close": close,
                           "volume": volume, "quote_volume": volume * close, "confirmed": True})
        result = pd.DataFrame(values, index=index)
        result.attrs["input_digest"] = digest({"symbol": symbol, "interval": interval,
                                               "as_of": iso(as_of), "values": values})
        self.receipts.append({"name": self.name, "url": "synthetic://engineering-only",
                              "observed_at": iso(now_utc()), "raw_persisted": False,
                              "payload_sha256": result.attrs["input_digest"]})
        return result
