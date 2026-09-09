"""Collect public realized funding history for cost diagnostics, not features.

Official sources:
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
https://www.gate.com/docs/developers/apiv4/en/futures/
https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history

Reads each settlement history record only. Binance fundingRate and Gate r are
historical rates; OKX must use realizedRate, never its predicted fundingRate as
a substitute. Missing rates and missing settlement mark prices remain NaN.
Original timestamps are preserved, including Gate's observed +1-second clocks.
These endpoints do not prove historical first-seen publication times or a
complete settlement schedule. They must never be joined as causal features.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Dict

import numpy as np
import pandas as pd
import requests

from yoyo.data import altseason_sources
from yoyo.data.altseason_sources import PublicClient, _safe, atomic_json, digest, utc_now


SCHEMA = "altseason-realized-funding-v1"
FIELDS = ["funding_time", "realized_rate", "mark_price", "venue", "symbol"]
LIMITS = {"binance": 1000, "gate": 1000, "okx": 400}


class _BudgetedSession(requests.Session):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        # Public-only: never attach ambient .netrc credentials.
        self.trust_env = False

    def get(self, *args, **kwargs):
        with self.owner.funding_lock:
            delay = max(0, self.owner.next_funding_request - time.monotonic())
            if delay:
                time.sleep(delay)
            self.owner.next_funding_request = time.monotonic() + self.owner.minimum_interval
        return super().get(*args, **kwargs)


class FundingClient(PublicClient):
    """Independent <=1.25 req/s funding budget, including retry attempts.

Binance shares 500 requests per five minutes across fundingRate/fundingInfo.
This client stays below that at 0.8s/request and does not alter candle budgets.
Concurrent external users of the same IP budget are not measured here.
"""

    def __init__(self, venue, root, timeout=30.0, minimum_interval=0.8):
        super().__init__(venue, root, timeout)
        if minimum_interval < 0.8:
            raise ValueError("Funding minimum interval must be >=0.8 seconds")
        self.minimum_interval = minimum_interval
        self.funding_lock = threading.Lock()
        self.next_funding_request = 0.0

    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = _BudgetedSession(self)
        return self._local.session


def _optional_float(value, field):
    if value is None or value == "":
        return float("nan")
    if isinstance(value, bool):
        raise ValueError("Boolean " + field)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite " + field)
    return number


def source_rows(venue: str, payload: Any):
    if venue == "okx":
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise ValueError("Invalid OKX funding response")
        rows = payload["data"]
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("Funding history must be a list")
    return rows


def record_time(venue: str, row: Dict[str, Any]) -> int:
    value = row["t"] if venue == "gate" else row["fundingTime"]
    if isinstance(value, bool) or not str(value).isdigit():
        raise ValueError("Funding time must be an integer timestamp")
    stamp = int(value) * (1000 if venue == "gate" else 1)
    if stamp < 0:
        raise ValueError("Negative funding timestamp")
    return stamp


def normalize(venue: str, symbol: str, payload: Any, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Keep [start,end) original records; missing actual rates are not zero."""
    output = []
    for row in source_rows(venue, payload):
        stamp = record_time(venue, row)
        if venue == "binance" and row.get("symbol") != symbol:
            raise ValueError("Funding symbol mismatch")
        if venue == "okx" and row.get("instId") != symbol:
            raise ValueError("Funding instrument mismatch")
        key = "realizedRate" if venue == "okx" else "r" if venue == "gate" else "fundingRate"
        rate = _optional_float(row.get(key), key)
        mark = _optional_float(row.get("markPrice"), "markPrice") if venue == "binance" else np.nan
        if not math.isnan(mark) and mark <= 0:
            raise ValueError("Funding mark price must be positive")
        if start_ms <= stamp < end_ms:
            output.append([stamp, rate, mark, venue, symbol])
    frame = pd.DataFrame(output, columns=FIELDS)
    if len(frame):
        frame["funding_time"] = frame["funding_time"].astype("int64")
        duplicate = frame[frame.duplicated("funding_time", keep=False)]
        if len(duplicate) and (duplicate.groupby("funding_time").nunique(dropna=False) > 1).any().any():
            raise ValueError("Conflicting duplicate funding records")
        frame = frame.drop_duplicates("funding_time").sort_values("funding_time").reset_index(drop=True)
    return frame


def request_params(venue: str, symbol: str, start_ms: int, end_ms: int, cursor: int):
    if venue == "binance":
        return "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": cursor, "endTime": end_ms - 1, "limit": LIMITS[venue]}
    if venue == "gate":
        # Gate has inclusive from/to in seconds and descending history order.
        return "/futures/usdt/funding_rate", {"contract": symbol, "from": start_ms // 1000, "to": cursor // 1000, "limit": LIMITS[venue]}
    return "/api/v5/public/funding-rate-history", {"instId": symbol, "before": start_ms - 1, "after": cursor, "limit": LIMITS[venue]}


def collect(client: FundingClient, symbol: str, start_ms: int, end_ms: int) -> Dict[str, Any]:
    """Traverse observed history without imposing a constant funding interval."""
    venue = client.venue
    _safe(symbol)
    if start_ms < 0 or end_ms <= start_ms or start_ms % 1000 or end_ms % 1000:
        raise ValueError("Require nonempty second-aligned UTC interval")
    destination = client.root / "normalized" / venue / (symbol + "_funding.csv.gz")
    meta_path = destination.with_suffix(".manifest.json")
    identity = {"venue": venue, "symbol": symbol, "start_ms": start_ms, "end_ms": end_ms}
    source_sha = digest(Path(__file__).read_bytes())
    transport_sha = digest(Path(altseason_sources.__file__).read_bytes())
    if destination.exists() and meta_path.exists():
        old = json.loads(meta_path.read_text())
        if old.get("request_spec") == identity and old.get("schema_version") == SCHEMA and old.get("source_sha256") == source_sha and old.get("transport_sha256") == transport_sha and old.get("sha256") == digest(destination.read_bytes()):
            return old
    cursor = start_ms if venue == "binance" else end_ms - 1 if venue == "gate" else end_ms
    frames, receipts = [], []
    stop_reason = None
    for _ in range(10000):
        path, params = request_params(venue, symbol, start_ms, end_ms, cursor)
        payload, receipt = client.get(path, params)
        rows = source_rows(venue, payload)
        frame = normalize(venue, symbol, payload, start_ms, end_ms)
        frames.append(frame)
        stamps = [record_time(venue, row) for row in rows]
        receipts.append({"request": receipt["request"], "returned_rows": len(rows), "retained_rows": len(frame), "body_sha256": receipt["body_sha256"], "relative_body_path": receipt["relative_body_path"], "fetched_at": receipt["fetched_at"]})
        if not stamps:
            stop_reason = "empty_page"
            break
        if venue == "binance":
            new_cursor = max(stamps) + 1
            if min(stamps) < cursor or max(stamps) >= end_ms:
                raise ValueError("Binance funding endpoint ignored requested range")
            if new_cursor >= end_ms:
                stop_reason = "reached_exclusive_end"
                break
            if new_cursor <= cursor:
                raise ValueError("Binance funding cursor did not progress")
        else:
            # OKX after is exclusive; Gate to is inclusive at second resolution.
            oldest = min(stamps)
            if oldest < start_ms or (max(stamps) > cursor if venue == "gate" else max(stamps) >= cursor):
                raise ValueError("Funding endpoint ignored requested range")
            if oldest <= start_ms:
                stop_reason = "reached_inclusive_start"
                break
            new_cursor = oldest - 1000 if venue == "gate" else oldest
            if new_cursor < start_ms:
                stop_reason = "reached_inclusive_start"
                break
            if new_cursor >= cursor:
                raise ValueError("Descending funding cursor did not progress")
        cursor = new_cursor
    if stop_reason is None:
        raise ValueError("Funding pagination exceeded safety bound")
    nonempty = [part for part in frames if len(part)]
    frame = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=FIELDS)
    if frame.funding_time.duplicated().any():
        raise ValueError("Funding pages overlapped")
    frame = frame.sort_values("funding_time").reset_index(drop=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    frame.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0})
    tmp.replace(destination)
    result = {"schema_version": SCHEMA, "request_spec": identity, "source_sha256": source_sha, "transport_sha256": transport_sha,
              "path": str(destination), "sha256": digest(destination.read_bytes()), "generated_at": utc_now(),
              "row_count": len(frame), "missing_actual_rate_rows": int(frame.realized_rate.isna().sum()), "missing_mark_price_rows": int(frame.mark_price.isna().sum()),
              "first_funding_ms": int(frame.funding_time.iloc[0]) if len(frame) else None, "last_funding_ms": int(frame.funding_time.iloc[-1]) if len(frame) else None,
              "stop_reason": stop_reason, "api_range_traversed": True, "full_settlement_schedule": "unknown", "actual_historical_first_seen": "unknown",
              "record_clock_policy": "Native exchange timestamp preserved; Gate history may be timestamped seconds after nominal boundary.",
              "cost_policy": "Observed actual rates only. Missing rates/marks are unknown, never zero or predicted-rate substitution.", "pages": receipts}
    atomic_json(meta_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", required=True, choices=list(LIMITS))
    parser.add_argument("--symbols", required=True, help="Exact comma-separated ids selected by root candidate study")
    parser.add_argument("--output", required=True, type=Path, help="Dedicated study data/funding directory")
    parser.add_argument("--start", default="2026-07-10T00:00:00Z")
    parser.add_argument("--end", default="2026-09-09T00:00:00Z")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be 1..4")
    start, end = int(pd.Timestamp(args.start).timestamp() * 1000), int(pd.Timestamp(args.end).timestamp() * 1000)
    if end > int(pd.Timestamp.now(tz="UTC").timestamp() * 1000):
        parser.error("Cannot query future realized funding")
    symbols = sorted(set(args.symbols.split(",")))
    client = FundingClient(args.venue, args.output)
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(collect, client, symbol, start, end): symbol for symbol in symbols}
        for future in as_completed(jobs):
            try:
                result = future.result()
                results.append(result)
                print(json.dumps({"symbol": jobs[future], "rows": result["row_count"], "missing_rates": result["missing_actual_rate_rows"]}), flush=True)
            except Exception as exc:
                error = {"venue": args.venue, "symbol": jobs[future], "error": str(exc)}
                errors.append(error)
                print(json.dumps(error), flush=True)
    atomic_json(args.output / (args.venue + "_funding_manifest.json"), {"schema_version": SCHEMA, "generated_at": utc_now(), "completed": results, "errors": errors})


if __name__ == "__main__":
    main()
