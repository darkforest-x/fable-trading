"""Auditable public OHLCV collection for the multivenue altcoin study.

Sources are the official Binance USD-M, Gate USDT futures and OKX V5 public
REST APIs. No credentials or execution endpoints are used. Selection is by
contract metadata, never by current returns. Catalogs retain inactive entries;
an as-of catalog still cannot recover every historically removed instrument.

All bars are UTC one-hour trade-price bars, open timestamp in milliseconds.
``volume`` preserves the venue-native unit (Binance base, Gate/OKX contracts);
``quote_volume`` is actual USDT turnover, never price times native volume.
Gate ``base_volume`` uses the catalog's current contract multiplier and is
diagnostic only because historical multiplier changes are not reconstructed.
Only confirmed closed bars are emitted. Missing hours remain missing.

Docs: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
https://www.gate.com/docs/developers/apiv4/en/futures/
https://www.okx.com/docs-v5/en/
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests


HOUR = 3_600_000
SCHEMA_VERSION = "altseason-public-1h-v1"
BASES = {"binance": "https://fapi.binance.com", "gate": "https://api.gateio.ws/api/v4", "okx": "https://www.okx.com"}
INTERVALS = {"binance": 0.40, "gate": 0.20, "okx": 0.55}
PAGE_BARS = {"binance": 1500, "gate": 2000, "okx": 300}
COLS = ["ts", "open", "high", "low", "close", "volume", "quote_volume", "base_volume", "contract_volume", "confirmed"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _safe(value: str) -> str:
    # Public Binance metadata includes Unicode tickers (e.g. 币安人生USDT).
    # Keep exact identifiers while disallowing filesystem path separators.
    if not value or value in (".", "..") or any(not (c.isalnum() or c in "_.-") for c in value):
        raise ValueError("Unsafe market identifier: " + value)
    return value


class PublicClient:
    """Shared venue rate budget plus immutable per-request response receipts."""

    def __init__(self, venue: str, root: Path, timeout: float = 30.0):
        if venue not in BASES:
            raise ValueError(venue)
        self.venue, self.root, self.timeout = venue, Path(root), timeout
        self._lock = threading.Lock()
        self._next = 0.0
        self._local = threading.local()

    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def get(self, path: str, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        request = {"method": "GET", "url": BASES[self.venue] + path, "params": params}
        key = digest(json.dumps(request, sort_keys=True).encode())
        directory = self.root / "raw" / self.venue / key[:2]
        receipt_path = directory / (key + ".json")
        body_path = directory / (key + ".json.gz")
        if receipt_path.exists() and body_path.exists():
            receipt = json.loads(receipt_path.read_text())
            body = gzip.decompress(body_path.read_bytes())
            if receipt["body_sha256"] != digest(body) or receipt["request"] != request:
                raise ValueError("Cached public response receipt mismatch")
            return json.loads(body), receipt
        last_error = None
        for attempt in range(4):
            with self._lock:
                delay = max(0, self._next - time.monotonic())
                if delay:
                    time.sleep(delay)
                self._next = time.monotonic() + INTERVALS[self.venue]
            try:
                response = self._session().get(request["url"], params=params, timeout=self.timeout)
                body = response.content
                receipt = {"request": request, "resolved_url": response.url, "fetched_at": utc_now(), "http_status": response.status_code,
                           "body_sha256": digest(body), "body_bytes": len(body), "relative_body_path": str(body_path.relative_to(self.root)),
                           "headers": {k: v for k, v in response.headers.items() if k.lower() in ("date", "retry-after") or "weight" in k.lower() or "ratelimit" in k.lower()}}
                if response.status_code != 200:
                    error_path = directory / (key + ".attempt%d.%d.json" % (attempt, time.time_ns()))
                    atomic_json(error_path, dict(receipt, body_preview=body[:1000].decode("utf-8", "replace")))
                    response.raise_for_status()
                payload = response.json()
                if self.venue == "okx" and str(payload.get("code")) != "0":
                    raise ValueError("OKX public error: " + str(payload)[:400])
                if self.venue != "okx" and isinstance(payload, dict) and ("code" in payload or "label" in payload):
                    raise ValueError("Public API error: " + str(payload)[:400])
                directory.mkdir(parents=True, exist_ok=True)
                tmp = body_path.with_name(body_path.name + ".tmp")
                tmp.write_bytes(gzip.compress(body, mtime=0))
                tmp.replace(body_path)
                atomic_json(receipt_path, receipt)
                return payload, receipt
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                # Rate-limit or transport retry, never change venue or fabricate rows.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status < 500 and status not in (418, 429):
                    break
                if attempt < 3:
                    time.sleep(min(30.0, 2.0 ** (attempt + 1)))
        raise RuntimeError("%s GET %s failed: %s" % (self.venue, path, last_error))


def normalize_catalog(venue: str, raw: Any, asof: str) -> List[Dict[str, Any]]:
    """Use only exchange catalog fields; status is retained, never filtered."""
    rows = raw["symbols"] if venue == "binance" else raw["data"] if venue == "okx" else raw
    output = []
    for row in rows:
        if venue == "binance":
            symbol, base = row["symbol"], row["baseAsset"]
            eligible = row.get("underlyingType") == "COIN" and row.get("contractType") == "PERPETUAL" and row.get("quoteAsset") == "USDT" and row.get("marginAsset") == "USDT"
            start, end = int(row.get("onboardDate") or 0), int(row.get("deliveryDate") or 0)
            state, category, multiplier = row.get("status"), row.get("underlyingType"), 1.0
            reason = "crypto_usdt_perpetual" if eligible else "not_coin_usdt_perpetual"
            premarket = row.get("underlyingType") == "PREMARKET"
        elif venue == "gate":
            symbol = row["name"]
            base = symbol.removesuffix("_USDT")
            # Missing classification is unknown, not implicitly crypto.
            eligible = row.get("contract_type") == "" and row.get("type") == "direct" and symbol.endswith("_USDT") and not row.get("is_pre_market", False)
            start = int(row.get("launch_time") or row.get("create_time") or 0) * 1000
            end = int(row.get("delisted_time") or 0) * 1000
            state, category, multiplier = row.get("status"), row.get("contract_type"), float(row.get("quanto_multiplier") or 0)
            reason = "unclassified_crypto_direct_usdt" if eligible else "tradfi_premarket_or_unknown"
            premarket = bool(row.get("is_pre_market", False))
        else:
            symbol, base = row["instId"], row["instId"].split("-")[0]
            eligible = row.get("instType") == "SWAP" and row.get("settleCcy") == "USDT" and row.get("ctType") == "linear" and row.get("instCategory") == "1" and row.get("ruleType") == "normal"
            start, end = int(row.get("listTime") or 0), int(row.get("expTime") or 0)
            state, category = row.get("state"), row.get("instCategory")
            multiplier = float(row.get("ctVal") or 0) * float(row.get("ctMult") or 1)
            reason = "crypto_linear_usdt_swap" if eligible else "noncrypto_inverse_or_premarket"
            premarket = row.get("ruleType") == "pre_market"
        output.append({"venue": venue, "symbol": _safe(symbol), "base_symbol": base, "eligible": eligible,
                       "selection_reason": reason, "status": state, "category": category, "premarket": premarket,
                       "listing_ms": start, "delisting_ms": end, "contract_multiplier": multiplier, "asof": asof,
                       "native_volume_unit": "base_asset" if venue == "binance" else "contracts", "raw": row})
    return sorted(output, key=lambda x: x["symbol"])


def fetch_catalog(client: PublicClient) -> Dict[str, Any]:
    destination = client.root / "catalog" / (client.venue + ".json")
    if destination.exists():
        existing = json.loads(destination.read_text())
        if existing.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Frozen catalog schema mismatch: require an explicit new experiment directory")
        return existing
    venue = client.venue
    receipts = []
    if venue == "binance":
        raw, receipt = client.get("/fapi/v1/exchangeInfo", {})
        receipts.append(receipt)
    elif venue == "okx":
        raw, receipt = client.get("/api/v5/public/instruments", {"instType": "SWAP"})
        receipts.append(receipt)
    else:
        raw, offset = [], 0
        while True:
            page, receipt = client.get("/futures/usdt/contracts", {"limit": 1000, "offset": offset})
            receipts.append(receipt)
            if not isinstance(page, list):
                raise ValueError("Gate contract catalog is not a list")
            raw.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
            if offset > 10000:
                raise ValueError("Gate catalog pagination made excessive progress")
    markets = normalize_catalog(venue, raw, receipts[0]["fetched_at"])
    if len({row["symbol"] for row in markets}) != len(markets):
        raise ValueError("Duplicate catalog market ids")
    result = {"schema_version": SCHEMA_VERSION, "venue": venue, "asof": receipts[0]["fetched_at"], "markets": markets, "receipts": receipts,
              "survivorship_warning": "As-of catalog, not an exhaustive historical listing/delisting ledger. Missing removed contracts cannot be inferred."}
    atomic_json(destination, result)
    return result


def page_requests(venue: str, symbol: str, start_ms: int, end_ms: int):
    """Disjoint UTC windows; API endpoints use inclusive end or exclusive after."""
    if start_ms % HOUR or end_ms % HOUR or end_ms <= start_ms:
        raise ValueError("Require aligned nonempty [start,end) UTC hours")
    cursor = start_ms
    while cursor < end_ms:
        stop = min(end_ms, cursor + PAGE_BARS[venue] * HOUR)
        if venue == "binance":
            path, params = "/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "startTime": cursor, "endTime": stop - 1, "limit": 1500}
        elif venue == "gate":
            path, params = "/futures/usdt/candlesticks", {"contract": symbol, "interval": "1h", "from": cursor // 1000, "to": stop // 1000 - 1}
        else:
            path, params = "/api/v5/market/history-candles", {"instId": symbol, "bar": "1H", "after": stop, "before": cursor - 1, "limit": 300}
        yield cursor, stop, path, params
        cursor = stop


def normalize_bars(venue: str, payload: Any, market: Dict[str, Any], start_ms: int, end_ms: int) -> pd.DataFrame:
    """Read only each completed bar's fields; never forward-fill missing data."""
    rows = payload["data"] if venue == "okx" else payload
    result = []
    for row in rows:
        if venue == "binance":
            ts, o, h, l, c, native, closed, quote = row[:8]
            if int(closed) != int(ts) + HOUR - 1:
                raise ValueError("Unexpected Binance bar close clock")
            base, contracts, confirmed = native, np.nan, 1
        elif venue == "gate":
            ts, o, h, l, c, native, quote = int(row["t"]) * 1000, row["o"], row["h"], row["l"], row["c"], row["v"], row["sum"]
            base, contracts, confirmed = float(native) * market["contract_multiplier"], native, 1
        else:
            ts, o, h, l, c, native, base, quote, confirmed = row[:9]
            contracts = native
        ts = int(ts)
        if ts < start_ms or ts + HOUR > end_ms or str(confirmed) != "1":
            continue
        result.append([ts, float(o), float(h), float(l), float(c), float(native), float(quote), float(base), float(contracts), 1])
    frame = pd.DataFrame(result, columns=COLS)
    if not frame.empty:
        frame["ts"] = frame["ts"].astype("int64")
        if (frame["ts"] % HOUR).any():
            raise ValueError("Non-hour aligned source candles")
        essential = frame[["open", "high", "low", "close", "volume", "quote_volume"]]
        if not np.isfinite(essential.to_numpy()).all() or (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame[["volume", "quote_volume"]] < 0).any().any():
            raise ValueError("Invalid price or volume data")
        if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) | (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
            raise ValueError("Broken OHLC ordering")
        duplicates = frame[frame.duplicated("ts", keep=False)]
        if not duplicates.empty and (duplicates.groupby("ts").nunique(dropna=False) > 1).any().any():
            raise ValueError("Conflicting duplicate source bars")
        frame = frame.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return frame


def fetch_symbol(client: PublicClient, market: Dict[str, Any], start_ms: int, end_ms: int) -> Dict[str, Any]:
    symbol, venue = market["symbol"], market["venue"]
    destination = client.root / "normalized" / venue / (symbol + "_1h.csv.gz")
    meta_path = destination.with_suffix(".manifest.json")
    request_spec = {"venue": venue, "symbol": symbol, "start_ms": start_ms, "end_ms": end_ms}
    source_sha = digest(Path(__file__).read_bytes())
    if meta_path.exists() and destination.exists():
        previous = json.loads(meta_path.read_text())
        if previous["request_spec"] == request_spec and previous["sha256"] == digest(destination.read_bytes()) and previous.get("source_sha256") == source_sha and previous.get("schema_version") == SCHEMA_VERSION:
            return previous
    frames, receipts = [], []
    for lo, hi, path, params in page_requests(venue, symbol, start_ms, end_ms):
        payload, receipt = client.get(path, params)
        frame = normalize_bars(venue, payload, market, lo, hi)
        frames.append(frame)
        receipts.append({"from_ms": lo, "to_ms": hi, "rows": len(frame), "body_sha256": receipt["body_sha256"], "relative_body_path": receipt["relative_body_path"]})
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLS)
    if data.ts.duplicated().any():
        raise ValueError("Overlapping page normalization")
    data = data.sort_values("ts").reset_index(drop=True)
    data["open_time"] = pd.to_datetime(data["ts"], unit="ms", utc=True)
    data["venue"], data["symbol"] = venue, symbol
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    data.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0})
    tmp.replace(destination)
    gaps = int(np.maximum(np.diff(data.ts.to_numpy(dtype=np.int64)) // HOUR - 1, 0).sum()) if len(data) else 0
    result = {"schema_version": SCHEMA_VERSION, "source_sha256": source_sha, "request_spec": request_spec, "rows": len(data), "first_open_ms": int(data.ts.iloc[0]) if len(data) else None,
              "last_open_ms": int(data.ts.iloc[-1]) if len(data) else None, "internal_missing_hours": gaps,
              "missing_window_hours": (end_ms - start_ms) // HOUR - len(data), "path": str(destination), "sha256": digest(destination.read_bytes()),
              "generated_at": utc_now(), "pages": receipts, "catalog_listing_ms": market["listing_ms"],
              "catalog_delisting_ms": market["delisting_ms"], "catalog_status": market["status"],
              "native_volume_unit": market["native_volume_unit"], "quote_volume_unit": "USDT", "complete_requested_window": len(data) == (end_ms - start_ms) // HOUR}
    atomic_json(meta_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", choices=list(BASES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-05-01T00:00:00Z")
    parser.add_argument("--end", default="2026-09-09T00:00:00Z")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", help="Comma-separated exact ids; otherwise all eligible metadata entries")
    parser.add_argument("--catalog-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be 1..8")
    start, end = int(pd.Timestamp(args.start).timestamp() * 1000), int(pd.Timestamp(args.end).timestamp() * 1000)
    last_complete_day = int(pd.Timestamp.now(tz="UTC").normalize().timestamp() * 1000)
    if end > last_complete_day:
        parser.error("end must not exceed the current completed UTC-day boundary")
    client = PublicClient(args.venue, args.output)
    catalog = fetch_catalog(client)
    selected = [x for x in catalog["markets"] if x["eligible"]]
    if args.symbols:
        wanted = set(args.symbols.split(","))
        selected = [x for x in selected if x["symbol"] in wanted]
        if {x["symbol"] for x in selected} != wanted:
            parser.error("one or more symbols are missing or ineligible")
    print(json.dumps({"venue": args.venue, "catalog": len(catalog["markets"]), "eligible": len(selected), "asof": catalog["asof"]}), flush=True)
    if args.catalog_only:
        return
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_symbol, client, x, start, end): x for x in selected}
        for done in as_completed(futures):
            market = futures[done]
            try:
                result = done.result()
                results.append(result)
            except Exception as exc:
                errors.append({"venue": args.venue, "symbol": market["symbol"], "error": str(exc), "at": utc_now()})
            if (len(results) + len(errors)) % 20 == 0:
                print(json.dumps({"done": len(results), "failed": len(errors), "total": len(selected)}), flush=True)
    manifest = {"venue": args.venue, "generated_at": utc_now(), "start_ms": start, "end_ms": end,
                "catalog_asof": catalog["asof"], "eligible": len(selected), "completed": sorted(results, key=lambda x: x["request_spec"]["symbol"]), "errors": errors}
    atomic_json(args.output / (args.venue + "_collection_manifest.json"), manifest)
    print(json.dumps({"venue": args.venue, "completed": len(results), "errors": len(errors), "total_rows": sum(x["rows"] for x in results)}), flush=True)


if __name__ == "__main__":
    main()
