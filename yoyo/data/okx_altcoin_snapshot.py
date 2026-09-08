"""Freeze public OKX derivatives research snapshots without fetching candles.

Source: https://www.okx.com/docs-v5/en/#trading-statistics-rest-api
Contract OI and taker endpoints expose only the latest 1,440 entries, not an
unlimited archive. Funding history is limited to three months. Missing history
is reported, never replaced with a present snapshot or another instrument.

Only current endpoint fields are normalized; there are no computed features.
OI/taker event_time is treated conservatively as the period opening timestamp.
available_at_nominal = event_time + period; actual historical publication latency
is UNKNOWN. Consumers must impose their own additional lag (the research plan
requires one complete extra period). A nominal time is not proof of availability.
Funding realizedRate and fundingRate remain separate; a missing realizedRate
never inherits a predicted rate. Instrument metadata is a current snapshot,
not historical contract-specification evidence.

Raw successful pages are immutable, request-keyed JSON. A fixed run manifest
prevents mixing date ranges or universes. Reruns replay those pages and fetch
only missing pages; normalized CSV/coverage files are replaceable derivatives.
No credentials, canonical K-line caches, monitoring or execution are involved.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Optional
from uuid import uuid4

import requests


BASE_URL = "https://www.okx.com"
SCHEMA = "okx-altcoin-snapshot-v1"
PERIOD_MS = {"1H": 3_600_000, "4H": 14_400_000}
ENDPOINTS = {
    "oi": "/api/v5/rubik/stat/contracts/open-interest-history",
    "taker": "/api/v5/rubik/stat/taker-volume-contract",
    "funding": "/api/v5/public/funding-rate-history",
    "instruments": "/api/v5/public/instruments",
}
COMMON_COLUMNS = ["inst_id", "event_time", "available_at_nominal"]
COLUMNS = {
    "oi": COMMON_COLUMNS + ["oi_contracts", "oi_base", "oi_usd"],
    "taker": COMMON_COLUMNS + ["sell_base", "buy_base"],
    "funding": COMMON_COLUMNS + ["funding_time", "realized_rate", "predicted_rate", "rate_status", "formula_type", "method"],
    "instruments": ["inst_id", "list_time", "ct_val", "ct_mult", "ct_val_ccy", "state"],
}
SOURCE_COLUMNS = ["source_fetched_at", "source_page"]


class SnapshotError(ValueError):
    """An explicit acquisition, schema or frozen-snapshot contract failure."""


def utc_ms(value: str) -> int:
    """Require an explicit UTC offset; naive local times are not accepted."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError("Invalid ISO UTC timestamp: " + value) from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise SnapshotError("Timestamp must explicitly be UTC: " + value)
    return int(parsed.timestamp() * 1000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _atomic_bytes(path: Path, content: bytes, *, frozen: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if frozen:
            # A hard link provides an atomic create-if-absent without clobbering.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _timestamp(value: Any, name: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise SnapshotError(name + " must be integer Unix milliseconds")
    number = int(value)
    if number <= 0:
        raise SnapshotError(name + " must be positive")
    return number


def _number(value: Any, name: str, *, signed: bool = False, optional: bool = False) -> Optional[str]:
    if optional and (value is None or value == ""):
        return None
    if isinstance(value, bool) or value is None or value == "":
        raise SnapshotError(name + " is missing or invalid")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise SnapshotError(name + " is not numeric") from exc
    if not number.is_finite() or (not signed and number < 0):
        raise SnapshotError(name + " must be finite" + ("" if signed else " and nonnegative"))
    return format(number.normalize(), "f")


def _validate_symbols(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise SnapshotError("symbols-file must contain a nonempty list or {symbols: [...]}")
    if any(not isinstance(x, str) or not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-SWAP", x) for x in values):
        raise SnapshotError("Each symbol must be an explicit OKX perpetual instId")
    if len(set(values)) != len(values):
        raise SnapshotError("Duplicate symbols are not allowed")
    return sorted(values)


def load_symbols(path: Path) -> list[str]:
    source = json.loads(path.read_text())
    return _validate_symbols(source.get("symbols") if isinstance(source, dict) else source)


class PublicClient:
    """One synchronous client limits all network attempts to <= 2 requests/s."""

    def __init__(self, out_dir: Path, *, session=None, max_attempts: int = 3,
                 timeout: float = 20.0, clock=time.monotonic, sleep=time.sleep):
        if not 1 <= max_attempts <= 5:
            raise SnapshotError("max_attempts must be between 1 and 5")
        self.out_dir = Path(out_dir)
        self.session = session or requests.Session()
        if session is None:
            # Do not silently load .netrc credentials for this public-only run.
            self.session.trust_env = False
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.clock = clock
        self.sleep = sleep
        self.last_request = None

    def _pace(self) -> None:
        if self.last_request is not None:
            remaining = 0.5 - (self.clock() - self.last_request)
            if remaining > 0:
                self.sleep(remaining)
        self.last_request = self.clock()

    def page(self, kind: str, params: dict) -> dict:
        endpoint = ENDPOINTS[kind]
        identity = {"base_url": BASE_URL, "endpoint": endpoint, "params": params}
        key = hashlib.sha256(_json_bytes(identity)).hexdigest()
        relative = Path("raw") / kind / params["instId"] / (key + ".json")
        path = self.out_dir / relative
        if path.exists():
            try:
                saved = json.loads(path.read_text())
            except (ValueError, OSError) as exc:
                raise SnapshotError("Frozen raw page is unreadable: " + str(relative)) from exc
            if any(saved.get(k) != v for k, v in identity.items()) or saved.get("schema") != SCHEMA:
                raise SnapshotError("Frozen raw page identity mismatch: " + str(relative))
            self._validate_response(saved.get("response"))
            if not saved.get("fetched_at"):
                raise SnapshotError("Frozen raw page has no fetched_at")
            return dict(saved, source_page=str(relative))
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            receipt = dict(identity, schema=SCHEMA, requested_at=utc_now(), attempt=attempt)
            try:
                response = self.session.get(BASE_URL + endpoint, params=params, timeout=self.timeout)
                receipt["fetched_at"] = utc_now()
                receipt["http_status"] = response.status_code
                try:
                    payload = response.json()
                except ValueError as exc:
                    receipt["body_preview"] = response.text[:2000]
                    raise SnapshotError("Non-JSON HTTP response") from exc
                receipt["response"] = payload
                if response.status_code != 200:
                    raise SnapshotError("HTTP " + str(response.status_code))
                self._validate_response(payload)
                _atomic_bytes(path, _json_bytes(receipt), frozen=True)
                return dict(receipt, source_page=str(relative))
            except (requests.RequestException, SnapshotError) as exc:
                last_error = str(exc)
                receipt["error"] = last_error
                receipt["failed_at"] = utc_now()
                error_path = self.out_dir / "errors" / (key + "-" + uuid4().hex + ".json")
                _atomic_bytes(error_path, _json_bytes(receipt), frozen=True)
                if attempt < self.max_attempts:
                    self.sleep(min(2 ** (attempt - 1), 8))
        raise SnapshotError(f"{kind} {params['instId']} failed after {self.max_attempts} attempts: {last_error}")

    @staticmethod
    def _validate_response(payload: Any) -> None:
        if not isinstance(payload, dict) or payload.get("code") != "0":
            raise SnapshotError("OKX business code error: " + json.dumps(payload, ensure_ascii=False)[:1000])
        if not isinstance(payload.get("data"), list):
            raise SnapshotError("OKX response data must be a list")


def _record(kind: str, raw: Any, inst_id: str, period_ms: int) -> dict:
    if kind in ("oi", "taker"):
        expected = 4 if kind == "oi" else 3
        if not isinstance(raw, list) or len(raw) != expected:
            raise SnapshotError(f"{kind} row must contain exactly {expected} fields")
        timestamp = _timestamp(raw[0], "ts")
        row = dict(inst_id=inst_id, event_time=timestamp, available_at_nominal=timestamp + period_ms)
        names = ["oi_contracts", "oi_base", "oi_usd"] if kind == "oi" else ["sell_base", "buy_base"]
        row.update({name: _number(value, name) for name, value in zip(names, raw[1:])})
        return row
    if not isinstance(raw, dict) or raw.get("instId") != inst_id:
        raise SnapshotError(kind + " row has missing or mismatched instId")
    if kind == "funding":
        timestamp = _timestamp(raw.get("fundingTime"), "fundingTime")
        realized = _number(raw.get("realizedRate"), "realizedRate", signed=True, optional=True)
        predicted = _number(raw.get("fundingRate"), "fundingRate", signed=True, optional=True)
        if realized is None and predicted is None:
            raise SnapshotError("Funding row has neither actual nor predicted rate")
        return dict(inst_id=inst_id, event_time=timestamp, funding_time=timestamp,
                    available_at_nominal=timestamp, realized_rate=realized, predicted_rate=predicted,
                    rate_status="actual_available" if realized is not None else "predicted_only",
                    formula_type=raw.get("formulaType", ""), method=raw.get("method", ""))
    if kind == "instruments":
        if not raw.get("ctValCcy") or not raw.get("state"):
            raise SnapshotError("Instrument requires ctValCcy and state")
        return dict(inst_id=inst_id, list_time=_timestamp(raw.get("listTime"), "listTime"),
                    ct_val=_number(raw.get("ctVal"), "ctVal"),
                    ct_mult=_number(raw.get("ctMult"), "ctMult"),
                    ct_val_ccy=raw.get("ctValCcy", ""), state=raw.get("state", ""))
    raise SnapshotError("Unsupported kind " + kind)


def normalize_pages(kind: str, pages: list[dict], inst_id: str, period: str,
                    start_ms: int, end_ms: int) -> tuple[list[dict], dict]:
    """Normalize without future candles, backfills or inference of missing data.

    Numeric duplicates are compared before range filtering. Any conflicting
    duplicate rejects the entire stream; no last-write-wins policy is used.
    """
    seen = {}
    duplicate_count = 0
    excluded_before = 0
    excluded_incomplete = 0
    period_ms = PERIOD_MS[period]
    for page in pages:
        for raw in page["response"]["data"]:
            row = _record(kind, raw, inst_id, period_ms)
            key = row.get("event_time", inst_id)
            if key in seen:
                prior = {k: v for k, v in seen[key].items() if k not in SOURCE_COLUMNS}
                if prior != row:
                    raise SnapshotError(f"Conflicting duplicate {kind} {inst_id} at {key}")
                duplicate_count += 1
                continue
            row.update(source_fetched_at=page["fetched_at"], source_page=page["source_page"])
            seen[key] = row
    rows = []
    for key in sorted(seen):
        row = seen[key]
        if kind != "instruments":
            if row["event_time"] < start_ms:
                excluded_before += 1
                continue
            if row["event_time"] >= end_ms or row["available_at_nominal"] > end_ms:
                excluded_incomplete += 1
                continue
        rows.append(row)
    details = dict(row_count=len(rows), duplicate_count=duplicate_count,
                   excluded_before_start=excluded_before, excluded_at_or_after_end_or_incomplete=excluded_incomplete)
    if kind in ("oi", "taker"):
        first_expected = math.ceil(start_ms / period_ms) * period_ms
        expected = max(0, (end_ms - period_ms - first_expected) // period_ms + 1)
        off_grid = [r["event_time"] for r in rows if r["event_time"] % period_ms]
        if off_grid:
            raise SnapshotError(f"Off-grid {period} timestamps in {kind}: {off_grid[:3]}")
        details.update(expected_periods=expected, missing_periods=max(0, expected - len(rows)),
                       full_period_coverage=len(rows) == expected)
    if kind != "instruments":
        details.update(first_event_time=rows[0]["event_time"] if rows else None,
                       last_event_time=rows[-1]["event_time"] if rows else None)
    return rows, details


def collect_stream(client: PublicClient, kind: str, inst_id: str, period: str,
                   start_ms: int, end_ms: int, *, max_pages: int = 100) -> tuple[list[dict], dict]:
    pages = []
    cursor = end_ms
    stop_reason = "max_pages"
    error = None
    for _ in range(max_pages):
        params = {"instId": inst_id}
        if kind in ("oi", "taker"):
            params.update(period=period, limit="100", end=str(cursor))
            if kind == "taker":
                params["unit"] = "0"
        elif kind == "funding":
            params.update(limit="400", after=str(cursor))
        else:
            params["instType"] = "SWAP"
        try:
            page = client.page(kind, params)
            pages.append(page)
            data = page["response"]["data"]
            if not data:
                stop_reason = "empty_page"
                break
            # Validate before choosing a cursor; unordered pages are supported.
            records = [_record(kind, row, inst_id, PERIOD_MS[period]) for row in data]
            if kind == "instruments":
                stop_reason = "current_metadata_snapshot"
                break
            oldest = min(row["event_time"] for row in records)
            if oldest <= start_ms:
                stop_reason = "reached_start"
                break
            if oldest >= cursor:
                raise SnapshotError("Pagination made no backward progress")
            cursor = oldest
        except SnapshotError as exc:
            error = str(exc)
            stop_reason = "error"
            break
    try:
        rows, coverage = normalize_pages(kind, pages, inst_id, period, start_ms, end_ms)
    except SnapshotError as exc:
        rows = []
        error = str(exc)
        stop_reason = "invalid_data"
        coverage = {"row_count": 0, "normalization_rejected": True}
    coverage.update(inst_id=inst_id, kind=kind, period=period,
                    requested_start=start_ms, requested_end=end_ms, pages=len(pages),
                    stop_reason=stop_reason, acquisition_complete=stop_reason in ("empty_page", "reached_start", "current_metadata_snapshot"),
                    error=error, timestamp_unit="Unix milliseconds",
                    actual_historical_publication_latency="unknown",
                    availability_policy="OI/taker bucket start + period is nominal only; consumer must add one full period research lag",
                    raw_page_paths=[p["source_page"] for p in pages])
    if kind == "funding":
        coverage["full_settlement_coverage"] = "unknown: settlement frequency can change; no fabricated expected schedule"
    if kind == "instruments":
        coverage["historical_specifications"] = False
    return rows, coverage


def _write_csv(path: Path, kind: str, rows: list[dict]) -> None:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS[kind] + SOURCE_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    _atomic_bytes(path, buffer.getvalue().encode())


def run(symbols: list[str], out_dir: Path, start_ms: int, end_ms: int, period: str,
        kinds: list[str], *, client: Optional[PublicClient] = None) -> dict:
    if period not in PERIOD_MS or not 0 < start_ms < end_ms:
        raise SnapshotError("Require 1H/4H and 0 < start < end")
    symbols = _validate_symbols(symbols)
    if end_ms > int(time.time() * 1000):
        raise SnapshotError("end must not be in the future")
    if not kinds or len(set(kinds)) != len(kinds) or any(k not in ENDPOINTS for k in kinds):
        raise SnapshotError("Unknown, empty or duplicate kinds")
    out_dir = Path(out_dir)
    if client is not None and client.out_dir.resolve() != out_dir.resolve():
        raise SnapshotError("Client output directory differs from the frozen run")
    identity = dict(schema=SCHEMA, base_url=BASE_URL, symbols=sorted(symbols), start_ms=start_ms,
                    end_ms=end_ms, period=period, kinds=sorted(kinds))
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()).get("identity") != identity:
            raise SnapshotError("Frozen manifest differs; use a new research output directory")
    else:
        _atomic_bytes(manifest_path, _json_bytes(dict(identity=identity, created_at=utc_now(),
            raw_pages_immutable=True, actual_historical_publication_latency="unknown")), frozen=True)
    client = client or PublicClient(out_dir)
    coverage = []
    for inst_id in sorted(symbols):
        for kind in kinds:
            rows, result = collect_stream(client, kind, inst_id, period, start_ms, end_ms)
            stem = f"{inst_id}_{period}_{kind}"
            _write_csv(out_dir / "normalized" / (stem + ".csv"), kind, rows)
            _atomic_bytes(out_dir / "coverage" / (stem + ".json"), _json_bytes(result))
            coverage.append(result)
            print(json.dumps({k: result.get(k) for k in ["inst_id", "kind", "row_count", "stop_reason", "missing_periods", "error"]}), flush=True)
    summary = dict(schema=SCHEMA, generated_at=utc_now(), streams=coverage,
                   errors=sum(bool(x["error"]) or not x["acquisition_complete"] for x in coverage))
    _atomic_bytes(out_dir / "coverage.json", _json_bytes(summary))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--start", required=True, help="Explicit UTC ISO timestamp, inclusive")
    parser.add_argument("--end", required=True, help="Explicit UTC ISO timestamp, exclusive")
    parser.add_argument("--period", choices=sorted(PERIOD_MS), default="4H")
    parser.add_argument("--kinds", default="oi,taker,funding,instruments")
    args = parser.parse_args(argv)
    try:
        result = run(load_symbols(args.symbols_file), args.out_dir, utc_ms(args.start), utc_ms(args.end),
                     args.period, [k.strip() for k in args.kinds.split(",")])
    except (SnapshotError, OSError, ValueError) as exc:
        parser.exit(2, "snapshot error: " + str(exc) + "\n")
    return 2 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
