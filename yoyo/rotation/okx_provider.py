"""Anonymous OKX spot acquisition with closed UTC bars and bounded pagination.

Source: https://app.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history
The history endpoint returns [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]; ts is
the opening instant and after is exclusive (older records). We deliberately
request at most 100 rows per page and five pages, even if the venue permits more.
1Dutc avoids OKX's default UTC+8 daily candle boundary. Spot vol is base units,
volCcyQuote is quote units, and spot ticker volCcy24h is quote turnover.

Only three public GET routes exist here. The caller's canonical authorization
guard is checked immediately before each IO, after pacing. No credentials,
SDK, persistent candle cache, venue fallback, or execution modules are used.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pandas as pd

from yoyo.contracts.rotation import INTERVAL_SECONDS, RotationError, digest, iso, now_utc, utc
from yoyo.rotation.providers import LEVERAGED_BASES, NON_CRYPTO_BASES

OKX_BASE = "https://www.okx.com"
OKX_BARS = {"1d": "1Dutc", "4h": "4H", "15m": "15m"}
_ENDPOINTS = frozenset({"/api/v5/market/history-candles", "/api/v5/public/instruments",
                        "/api/v5/market/tickers"})
_MAX_RESPONSE_BYTES = 8_000_000
_PAGE_LIMIT = 100
_MAX_PAGES = 5


class _RejectRedirects(HTTPRedirectHandler):
    """A fixed venue may fail, but cannot silently redirect to another source."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RotationError("OKX public endpoint redirected; source change rejected")


def _bounded_integer(value: object, *, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RotationError(f"{name} must be an integer between {low} and {high}")
    return value


def _instrument(symbol: str) -> str:
    """Convert an explicit quote-qualified symbol without venue guessing."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{1,24}USDT", symbol):
        raise RotationError("OKX provider requires an uppercase USDT spot symbol")
    return symbol[:-4] + "-USDT"


def _timestamp(value: object) -> int:
    # int(1.5) truncates and bool is an int; both would hide source corruption.
    if isinstance(value, bool) or not (isinstance(value, int) or
                                      isinstance(value, str) and re.fullmatch(r"[0-9]+", value)):
        raise RotationError("invalid OKX candle timestamp")
    stamp = int(value)
    if stamp < 0:
        raise RotationError("invalid OKX candle timestamp")
    return stamp


def _page_timestamps(payload: object, *, duration: int, cutoff: int,
                     after: int, seen: set[int]) -> list[int]:
    """Validate the entire page's temporal bounds before any OHLCV conversion."""
    if not isinstance(payload, list):
        raise RotationError("OKX candle data must be an array")
    stamps = []
    for raw in payload:
        if not isinstance(raw, list) or len(raw) != 9:
            raise RotationError("OKX candle schema changed; expected nine fields")
        stamp = _timestamp(raw[0])
        if stamp % duration:
            raise RotationError("unaligned OKX candle opening time")
        if stamp + duration > cutoff:
            raise RotationError("OKX source returned a candle beyond the closed cutoff")
        if stamp in seen:
            raise RotationError("duplicate OKX candle timestamp")
        if stamp >= after:
            raise RotationError("OKX source violated the exclusive pagination bound")
        seen.add(stamp)
        stamps.append(stamp)
    return stamps


def _frame(rows: list[list], *, interval: str, cutoff: int) -> pd.DataFrame:
    """Parse prices only after every fetched page has passed timestamp checks."""
    if not rows:
        raise RotationError("empty OKX candle response")
    duration = INTERVAL_SECONDS[interval] * 1000
    stamps = sorted(int(raw[0]) for raw in rows)
    if any(right - left != duration for left, right in zip(stamps, stamps[1:])):
        raise RotationError("OKX source candle gap; missing bars are not filled")
    latest = cutoff // duration * duration - duration
    if stamps[-1] != latest:
        raise RotationError("OKX source is missing the latest closed candle")
    values = []
    for raw in rows:
        # A past-looking candle with confirm=0 is still provisional and unusable.
        if raw[8] != "1":
            raise RotationError("OKX source returned an unconfirmed candle")
        try:
            if any(isinstance(raw[index], bool) for index in range(1, 8)):
                raise ValueError("boolean price/volume")
            o, h, l, c, v, v_ccy, q = (float(raw[index]) for index in range(1, 8))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RotationError("invalid OKX numeric candle value") from exc
        if not all(math.isfinite(value) for value in (o, h, l, c, v, v_ccy, q)):
            raise RotationError("nonfinite OKX candle value")
        if (min(o, h, l, c) <= 0 or min(v, v_ccy, q) < 0
                or h < max(o, c, l) or l > min(o, c, h)):
            raise RotationError("invalid OKX candle OHLC/volume geometry")
        values.append({"time": pd.Timestamp(int(raw[0]), unit="ms", tz="UTC"),
                       "open": o, "high": h, "low": l, "close": c, "volume": v,
                       "quote_volume": q, "confirmed": True})
    frame = pd.DataFrame(values).set_index("time").sort_index()
    return frame


class OKXProvider:
    """Paced anonymous spot reads; injected transports accept the full URL.

    The supplied guard owns mode/holdout permission and is never swallowed as
    a provider failure. Receipts record each response, including API rejection;
    transport failures have no invented response hash. No API key is loaded.
    """

    name = "OKX spot public API"

    def __init__(self, *, guard: Callable[[], None], transport: Optional[Callable] = None,
                 timeout: float = 12, request_spacing: float = .35):
        if not callable(guard):
            raise RotationError("OKX provider requires an authorization guard")
        if transport is not None and not callable(transport):
            raise RotationError("OKX transport must be callable")
        for key, value in (("timeout", timeout), ("request_spacing", request_spacing)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise RotationError(key + " must be finite")
        if timeout <= 0 or timeout > 120 or request_spacing < 0 or request_spacing > 60:
            raise RotationError("OKX timeout or request spacing is out of bounds")
        self.guard = guard
        self.transport = transport
        self.timeout = timeout
        self.request_spacing = request_spacing
        self.receipts: list[dict] = []
        self._lock = threading.Lock()
        self._last = 0.0

    def _get(self, endpoint: str, params: dict) -> list:
        if endpoint not in _ENDPOINTS:
            raise RotationError("OKX endpoint is outside the public spot allowlist")
        url = OKX_BASE + endpoint + "?" + urlencode(params)
        with self._lock:
            pause = self.request_spacing - (time.monotonic() - self._last)
            if pause > 0:
                time.sleep(pause)
            # Permission may expire while waiting; check after the wait, not just
            # before entering the queue. Do not catch or downgrade this error.
            self.guard()
            requested_at = iso(now_utc())
            self._last = time.monotonic()
            try:
                if self.transport is not None:
                    payload = self.transport(url)
                    # Test transports have decoded JSON; the real HTTP path
                    # below hashes the original response bytes instead.
                    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")
                    hash_basis = "canonical_decoded_json"
                else:
                    request = Request(url, method="GET", headers={
                        "Accept": "application/json", "User-Agent": "fable-rotation-research/1"})
                    with build_opener(_RejectRedirects()).open(request, timeout=self.timeout) as response:
                        raw = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise RotationError("OKX public response exceeded the byte limit")
                    payload = json.loads(raw)
                    hash_basis = "raw_response_bytes"
            except (OSError, ValueError, TypeError) as exc:
                raise RotationError("OKX public request failed: " + str(exc)) from exc
            received_at = iso(now_utc())
            response_hash = hashlib.sha256(raw).hexdigest()
            self.receipts.append({"name": self.name, "url": url, "endpoint": endpoint,
                                  "params": dict(params), "method": "GET",
                                  "requested_at": requested_at, "received_at": received_at,
                                  "observed_at": received_at, "payload_sha256": response_hash,
                                  "response_sha256": response_hash, "hash_basis": hash_basis,
                                  "raw_persisted": False})
        if not isinstance(payload, dict) or not isinstance(payload.get("code"), str):
            raise RotationError("OKX public response envelope changed")
        if payload["code"] != "0":
            raise RotationError(f"OKX rejected public request ({payload['code']}): {payload.get('msg', '')}")
        if not isinstance(payload.get("data"), list):
            raise RotationError("OKX public response data must be an array")
        return payload["data"]

    def candles(self, symbol: str, interval: str, *, as_of: datetime, limit: int) -> pd.DataFrame:
        """Read no more than five older pages, each bounded before market IO.

        Short contiguous history is returned honestly for newly listed assets;
        downstream minimum-history gates decide whether it is usable. Gaps,
        duplicates, provisional rows and an absent latest closed bar fail.
        """
        instrument = _instrument(symbol)
        if interval not in OKX_BARS:
            raise RotationError("unsupported OKX research candle interval")
        _bounded_integer(limit, name="limit", low=1, high=_PAGE_LIMIT * _MAX_PAGES)
        cutoff = int(utc(as_of).timestamp() * 1000)
        duration = INTERVAL_SECONDS[interval] * 1000
        after = cutoff // duration * duration
        if after < duration:
            raise RotationError("OKX cutoff has no complete historical candle")
        rows: list[list] = []
        seen: set[int] = set()
        for _ in range(_MAX_PAGES):
            requested = min(_PAGE_LIMIT, limit - len(rows))
            page = self._get("/api/v5/market/history-candles", {
                "instId": instrument, "bar": OKX_BARS[interval],
                "after": str(after), "limit": str(requested)})
            if len(page) > requested:
                raise RotationError("OKX source exceeded the requested candle limit")
            stamps = _page_timestamps(page, duration=duration, cutoff=cutoff, after=after, seen=seen)
            if not page:
                break
            rows.extend(page)
            after = min(stamps)
            if len(rows) == limit or len(page) < requested:
                break
        frame = _frame(rows, interval=interval, cutoff=cutoff)
        frame.attrs["source"] = self.name
        # Order-independent data identity, independent of response chunk size.
        frame.attrs["input_digest"] = digest(sorted(rows, key=lambda row: int(row[0])))
        return frame

    def discover(self, *, maximum: int) -> tuple[list[str], list[dict], str]:
        """Freeze live USDT spot membership ranked by quote turnover, not gains.

        The caller permits current discovery only in authorized live mode.
        SPOT volCcy24h is quote currency per OKX's ticker specification; vol24h
        is base units and is never substituted when quote volume is absent.
        Shared explicit exclusions avoid treating genuine names like JUP as
        leveraged tokens merely because their spelling ends in UP.
        """
        _bounded_integer(maximum, name="maximum", low=2, high=200)
        members = self._get("/api/v5/public/instruments", {"instType": "SPOT"})
        tickers = self._get("/api/v5/market/tickers", {"instType": "SPOT"})
        if not members or not tickers:
            raise RotationError("empty OKX spot membership or ticker response")
        volumes: dict[str, float] = {}
        ticker_ids: set[str] = set()
        for ticker in tickers:
            if not isinstance(ticker, dict) or not isinstance(ticker.get("instId"), str):
                raise RotationError("OKX spot ticker schema changed")
            instrument = ticker["instId"]
            if instrument in ticker_ids:
                raise RotationError("duplicate OKX spot ticker instrument")
            ticker_ids.add(instrument)
            if ticker.get("instType") != "SPOT" or not instrument.endswith("-USDT"):
                continue
            try:
                raw = ticker["volCcy24h"]
                if isinstance(raw, bool):
                    continue
                volume = float(raw)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(volume) and volume > 0:
                volumes[instrument] = volume
        eligible: list[str] = []
        excluded: list[dict] = []
        member_ids: set[str] = set()
        for member in members:
            if not isinstance(member, dict) or not isinstance(member.get("instId"), str):
                raise RotationError("OKX spot membership schema changed")
            instrument = member["instId"]
            if instrument in member_ids:
                raise RotationError("duplicate OKX spot membership instrument")
            member_ids.add(instrument)
            if member.get("quoteCcy") != "USDT":
                continue
            base = member.get("baseCcy")
            if not isinstance(base, str) or not re.fullmatch(r"[A-Z0-9]{1,24}", base) or instrument != base + "-USDT":
                raise RotationError("OKX spot instrument/base/quote mapping is inconsistent")
            symbol = base + "USDT"
            if member.get("instType") != "SPOT" or member.get("state") != "live":
                reason = "not_active_spot"
            elif base in NON_CRYPTO_BASES or base in LEVERAGED_BASES:
                reason = "non_crypto_or_leveraged_asset"
            elif instrument not in volumes:
                reason = "missing_or_invalid_quote_volume"
            else:
                eligible.append(symbol)
                continue
            excluded.append({"symbol": symbol, "reason": reason})
        if not eligible:
            raise RotationError("OKX discovery has no eligible active USDT spot instruments with valid quote turnover")
        ordered = sorted(eligible, key=lambda symbol: (-volumes[_instrument(symbol)], symbol))
        benchmarks = [symbol for symbol in ("BTCUSDT", "ETHUSDT") if symbol in ordered]
        selected = benchmarks + [symbol for symbol in ordered if symbol not in benchmarks][:maximum - len(benchmarks)]
        excluded.extend({"symbol": symbol, "reason": "outside_frozen_volume_budget"}
                        for symbol in ordered if symbol not in selected)
        return selected, excluded, iso(now_utc())
