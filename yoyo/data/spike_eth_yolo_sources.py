"""Freeze bounded OKX ETH-USDT-SWAP candles for the V9 YOLO study.

Only the public OKX ``instruments`` and ``history-candles`` GET endpoints are
used.  The source window is fixed by the study config: each native timeframe
contains 720 (or configured) bars before ``start_utc`` plus every completed
bar in ``[start_utc, end_utc)``.  This is research input acquisition, never a
monitor cache, a source fallback, or a trading action.

OKX history rows are ``[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]``.  The
canonical ``volume`` deliberately preserves native ``vol`` (contracts for
this swap); ``volCcy`` and quote-currency ``volCcyQuote`` are retained as
diagnostic columns.  Pages and receipts are immutable output evidence, so a
partially failed fetch can be inspected and an identical config can resume.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Mapping

import pandas as pd
import requests


OKX_BASE = "https://www.okx.com"
INSTRUMENT_PATH = "/api/v5/public/instruments"
HISTORY_PATH = "/api/v5/market/history-candles"
INSTRUMENT = "ETH-USDT-SWAP"
SCHEMA_VERSION = "spike-eth-yolo-okx-v1"
PAGE_LIMIT = 300
DEFAULT_INTERVAL_SECONDS = 0.55
DEFAULT_RETRIES = 3
CANONICAL_COLUMNS = (
    "open_time", "ts", "open", "high", "low", "close", "volume", "volCcy", "quote_volume",
)
_FRAME_SPECS = {
    "1m": (1, "1m"), "3m": (3, "3m"), "5m": (5, "5m"), "15m": (15, "15m"),
    "30m": (30, "30m"), "1H": (60, "1H"), "4H": (240, "4H"), "1Dutc": (1440, "1Dutc"),
}


class SourceError(RuntimeError):
    """Raised when a frozen public source violates its stated contract."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("UTC timestamp must include an explicit timezone")
    return stamp.tz_convert("UTC")


def _iso(ms: int) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, _json_bytes(value) + b"\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise SourceError(f"invalid {field}")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SourceError(f"invalid {field}") from exc
    if isinstance(value, float) and result != value:
        raise SourceError(f"invalid {field}")
    return result


def _as_number(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise SourceError(f"invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SourceError(f"invalid {field}") from exc
    if not math.isfinite(result):
        raise SourceError(f"non-finite {field}")
    return result


def normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the fixed study contract before any request."""
    if not isinstance(config, Mapping):
        raise ValueError("config must be a JSON object")
    experiment_id = config.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("config.experiment_id must be a nonempty string")
    start, end = _utc(config.get("start_utc")), _utc(config.get("end_utc"))
    if end <= start:
        raise ValueError("end_utc must be after start_utc")
    warmup = _as_int(config.get("warmup_bars", 720), field="warmup_bars")
    if warmup < 1:
        raise ValueError("warmup_bars must be positive")
    instrument = config.get("instrument", INSTRUMENT)
    if instrument != INSTRUMENT:
        raise ValueError(f"instrument is fixed to {INSTRUMENT}")
    raw_timeframes = config.get("timeframes")
    if not isinstance(raw_timeframes, Mapping) or not raw_timeframes:
        raise ValueError("config.timeframes must be a nonempty object")
    timeframes: dict[str, dict[str, Any]] = {}
    for label, source in raw_timeframes.items():
        if label not in _FRAME_SPECS:
            raise ValueError(f"unsupported timeframe: {label}")
        expected_minutes, expected_bar = _FRAME_SPECS[label]
        if isinstance(source, Mapping):
            minutes = _as_int(source.get("minutes"), field=f"timeframes.{label}.minutes")
            sourcebar = source.get("sourcebar", expected_bar)
        else:
            minutes, sourcebar = _as_int(source, field=f"timeframes.{label}"), expected_bar
        if minutes != expected_minutes or sourcebar != expected_bar:
            raise ValueError(f"timeframe {label} must use {expected_minutes} minutes and native bar {expected_bar}")
        period_ms = minutes * 60_000
        requested_start_ms, requested_end_ms = int(start.value // 1_000_000), int(end.value // 1_000_000)
        # A study entry can fall between native closes (the supplied 16:00Z
        # entry does for 1Dutc).  Never include a candle that began before
        # entry: start at the first native open at or after entry, and stop at
        # the last fully closed native boundary before the cutoff.
        start_ms = ((requested_start_ms + period_ms - 1) // period_ms) * period_ms
        end_ms = (requested_end_ms // period_ms) * period_ms
        if end_ms <= start_ms:
            raise ValueError(f"study window has no complete native candles for {label}")
        source_start = start_ms - warmup * period_ms
        if source_start < 0:
            raise ValueError(f"warmup before epoch for {label}")
        timeframes[label] = {
            "minutes": minutes, "sourcebar": sourcebar, "period_ms": period_ms,
            "source_start_ms": source_start, "start_ms": start_ms, "end_ms": end_ms,
            "requested_start_ms": requested_start_ms, "requested_end_ms": requested_end_ms,
            "expected": warmup + (end_ms - start_ms) // period_ms,
        }
    return {
        "schema_version": SCHEMA_VERSION, "experiment_id": experiment_id.strip(), "instrument": INSTRUMENT,
        "start_utc": _iso(int(start.value // 1_000_000)), "end_utc": _iso(int(end.value // 1_000_000)),
        "warmup_bars": warmup, "timeframes": {key: timeframes[key] for key in sorted(timeframes)},
    }


def assert_builder_committed(root: Path | None = None) -> dict[str, str]:
    """Prove this builder is committed and unchanged; callers opt into this gate.

    ``run_config`` intentionally does not invoke this check, which keeps pure
    parsing and synthetic HTTP tests independent of a repository checkout. The
    CLI invokes it before making a live request.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    relative = Path(__file__).resolve().relative_to(root).as_posix()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        committed = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=root)
    except subprocess.CalledProcessError as exc:
        raise SourceError("builder must be committed before public acquisition") from exc
    working = (root / relative).read_bytes()
    if committed != working:
        raise SourceError("builder differs from committed HEAD; commit before public acquisition")
    return {"builder_commit": commit, "builder_path": relative, "builder_sha256": _sha256(working)}


class FrozenOkxClient:
    """A <=2 requests/s public GET client that retains payloads and receipts."""

    def __init__(self, output_dir: Path, *, session: requests.Session | None = None,
                 request_interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
                 max_retries: int = DEFAULT_RETRIES, timeout_seconds: float = 30.0):
        if request_interval_seconds < 0.5:
            raise ValueError("request interval must be at least 0.5 seconds (<=2 req/s)")
        if max_retries < 1:
            raise ValueError("max_retries must be positive")
        self.output_dir = Path(output_dir)
        self.session = session or requests.Session()
        self.request_interval_seconds = request_interval_seconds
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._next_request = 0.0

    def _wait_turn(self) -> None:
        with self._lock:
            delay = max(0.0, self._next_request - time.monotonic())
            if delay:
                time.sleep(delay)
            self._next_request = time.monotonic() + self.request_interval_seconds

    def get(self, path: str, params: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a verified cached or newly frozen OKX public JSON envelope."""
        if path not in (INSTRUMENT_PATH, HISTORY_PATH):
            raise SourceError(f"public endpoint not permitted: {path}")
        request = {"method": "GET", "url": OKX_BASE + path, "params": {key: str(value) for key, value in sorted(params.items())}}
        request_sha = _sha256(_json_bytes(request))
        raw_dir = self.output_dir / "raw" / request_sha[:2]
        body_path = raw_dir / f"{request_sha}.json.gz"
        receipt_path = raw_dir / f"{request_sha}.receipt.json"
        if body_path.exists() or receipt_path.exists():
            if not body_path.exists() or not receipt_path.exists():
                raise SourceError("incomplete cached raw page or receipt")
            receipt = _read_json(receipt_path)
            payload_bytes = gzip.decompress(body_path.read_bytes())
            if receipt.get("request") != request or receipt.get("body_sha256") != _sha256(payload_bytes):
                raise SourceError("cached raw page or receipt hash mismatch")
            try:
                payload = json.loads(payload_bytes)
            except json.JSONDecodeError as exc:
                raise SourceError("cached raw page is not JSON") from exc
            return self._validate_envelope(payload), receipt
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn()
            response = None
            try:
                response = self.session.get(request["url"], params=request["params"], timeout=self.timeout_seconds)
                body = response.content
                receipt = {
                    "request": request, "request_sha256": request_sha, "attempt": attempt,
                    "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "http_status": int(response.status_code),
                    "resolved_url": getattr(response, "url", request["url"]), "body_sha256": _sha256(body),
                    "body_bytes": len(body), "relative_body_path": str(body_path.relative_to(self.output_dir)),
                    "headers": {key: value for key, value in getattr(response, "headers", {}).items()
                                if key.lower() in ("date", "retry-after") or "rate" in key.lower()},
                }
                if response.status_code != 200:
                    raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
                payload = json.loads(body)
                payload = self._validate_envelope(payload)
                _atomic_bytes(body_path, gzip.compress(body, mtime=0))
                _atomic_json(receipt_path, receipt)
                return payload, receipt
            except (requests.RequestException, json.JSONDecodeError, SourceError) as exc:
                last_error = exc
                failure = {
                    "request": request, "request_sha256": request_sha, "attempt": attempt,
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(), "error": str(exc),
                    "http_status": getattr(response, "status_code", None),
                }
                _atomic_json(raw_dir / f"{request_sha}.attempt-{attempt}-{time.time_ns()}.json", failure)
                status = getattr(response, "status_code", None)
                if attempt == self.max_retries or (status is not None and 400 <= status < 500 and status != 429):
                    break
        raise SourceError(f"OKX public GET failed after {self.max_retries} attempts: {path}: {last_error}")

    @staticmethod
    def _validate_envelope(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("code") != "0" or not isinstance(payload.get("data"), list):
            raise SourceError(f"OKX public response envelope rejected: {str(payload)[:300]}")
        return payload


def _instrument_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the single ETH linear swap metadata record and preserve tick data."""
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
        raise SourceError("ETH instrument metadata must contain exactly one record")
    row = dict(data[0])
    # Unlike spot, OKX SWAP responses legitimately leave baseCcy/quoteCcy
    # empty.  Derive ETH only from the explicit derivative-family fields,
    # which makes an accidental differently-underlying linear USDT swap fail.
    required = {
        "instId": INSTRUMENT, "instType": "SWAP", "instFamily": "ETH-USDT",
        "uly": "ETH-USDT", "settleCcy": "USDT", "ctType": "linear", "ctValCcy": "ETH",
    }
    for field, expected in required.items():
        if row.get(field) != expected:
            raise SourceError(f"instrument metadata mismatch for {field}")
    optional_spot_fields = {"baseCcy": "ETH", "quoteCcy": "USDT"}
    for field, expected in optional_spot_fields.items():
        received = row.get(field)
        if received not in (None, "", expected):
            raise SourceError(f"instrument metadata mismatch for nonempty {field}")
    for field in ("tickSz", "lotSz", "ctVal"):
        value = _as_number(row.get(field), field=f"instrument.{field}")
        if value <= 0:
            raise SourceError(f"instrument.{field} must be positive")
    metadata = {key: row.get(key) for key in (
        "instId", "instType", "instFamily", "uly", "state", "baseCcy", "quoteCcy", "settleCcy",
        "ctType", "ctVal", "ctValCcy", "ctMult", "tickSz", "lotSz", "minSz", "listTime",
    )}
    metadata["derived_base_asset"] = "ETH"
    metadata["derived_base_asset_basis"] = "uly=ETH-USDT; instFamily=ETH-USDT; ctValCcy=ETH; ctType=linear; settleCcy=USDT"
    return metadata


def parse_confirmed_rows(raw_rows: Any, *, period_ms: int, source_start_ms: int, end_ms: int) -> tuple[dict[int, dict[str, Any]], dict[str, int]]:
    """Strictly parse native rows, retaining only confirmed rows in the fixed span."""
    if not isinstance(raw_rows, list):
        raise SourceError("history data is not an array")
    collected: dict[int, dict[str, Any]] = {}
    counts = {"unconfirmed": 0, "outside_window": 0, "exact_duplicates": 0, "revised": 0}
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) < 9:
            raise SourceError("history row shape is invalid")
        ts = _as_int(raw[0], field="history timestamp")
        if ts < 0 or ts % period_ms:
            raise SourceError("history timestamp is not on the native UTC grid")
        values = [_as_number(raw[index], field=name) for index, name in zip(range(1, 8), ("open", "high", "low", "close", "vol", "volCcy", "volCcyQuote"))]
        open_, high, low, close, volume, vol_ccy, quote = values
        if min(open_, high, low, close) <= 0 or min(volume, vol_ccy, quote) < 0:
            raise SourceError("history OHLCV contains an invalid sign")
        if high < max(open_, low, close) or low > min(open_, high, close):
            raise SourceError("history OHLC geometry is invalid")
        confirm = str(raw[8])
        if confirm not in ("0", "1"):
            raise SourceError("history confirmation flag is invalid")
        if confirm != "1":
            counts["unconfirmed"] += 1
            continue
        if ts < source_start_ms or ts >= end_ms:
            counts["outside_window"] += 1
            continue
        parsed = {"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                  "volume": volume, "volCcy": vol_ccy, "quote_volume": quote}
        previous = collected.get(ts)
        if previous is None:
            collected[ts] = parsed
        elif previous != parsed:
            raise SourceError("conflicting duplicate confirmed history row")
        else:
            counts["exact_duplicates"] += 1
    return collected, counts


def _validate_grid(rows: dict[int, dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    expected_stamps = list(range(spec["source_start_ms"], spec["end_ms"], spec["period_ms"]))
    gaps = [_iso(stamp) for stamp in expected_stamps if stamp not in rows]
    if gaps:
        raise SourceError(f"confirmed native candle grid has {len(gaps)} gaps; first={gaps[0]}")
    if len(rows) != len(expected_stamps):
        raise SourceError("history rows escaped the requested grid")
    return [rows[stamp] for stamp in expected_stamps], gaps


def _combine_raw_sha(receipts: list[Mapping[str, Any]]) -> str:
    basis = [{"request_sha256": item.get("request_sha256"), "body_sha256": item.get("body_sha256")} for item in receipts]
    return _sha256(_json_bytes(basis))


def _prepare_output(output_dir: Path, normalized: Mapping[str, Any]) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "request_config.json"
    config_sha = _sha256(_json_bytes(normalized))
    frozen = {"config": normalized, "config_sha256": config_sha}
    if config_path.exists():
        if _read_json(config_path) != frozen:
            raise SourceError("output directory belongs to a different acquisition config")
    else:
        _atomic_json(config_path, frozen)
    return config_sha


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> str:
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "quote_volume"])
    frame.insert(0, "open_time", pd.to_datetime(frame["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    frame = frame.loc[:, CANONICAL_COLUMNS]
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    temporary.replace(path)
    return _sha256(path.read_bytes())


def _existing_complete_summary(output_dir: Path, config_sha: str) -> dict[str, Any] | None:
    path = output_dir / "summary.json"
    if not path.exists():
        return None
    summary = _read_json(path)
    if summary.get("config_sha256") != config_sha:
        raise SourceError("summary config hash does not match frozen config")
    for label, item in summary.get("timeframes", {}).items():
        csv_path = output_dir / f"{label}.csv"
        if not csv_path.exists() or _sha256(csv_path.read_bytes()) != item.get("csv_sha256"):
            raise SourceError("completed summary or CSV hash mismatch")
    return summary


def run_config(config: Mapping[str, Any], output_dir: Path, *, session: requests.Session | None = None,
               progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Acquire one predeclared source set, or return its verified identical resume.

    The caller must run ``assert_builder_committed`` before authorizing a real
    fetch.  This function creates only ``output_dir`` and never reads or writes
    ``data/kline_fetched`` or monitor state.
    """
    normalized = normalize_config(config)
    output_dir = Path(output_dir)
    config_sha = _prepare_output(output_dir, normalized)
    completed = _existing_complete_summary(output_dir, config_sha)
    if completed is not None:
        return completed
    client = FrozenOkxClient(output_dir, session=session)
    instrument_payload, instrument_receipt = client.get(INSTRUMENT_PATH, {"instType": "SWAP", "instId": INSTRUMENT})
    metadata = _instrument_metadata(instrument_payload)
    metadata_path = output_dir / "instrument_metadata.json"
    if metadata_path.exists() and _read_json(metadata_path).get("metadata") != metadata:
        raise SourceError("instrument metadata changed within a resumed acquisition")
    _atomic_json(metadata_path, {"metadata": metadata, "metadata_sha256": _sha256(_json_bytes(metadata)),
                                 "receipt": instrument_receipt})
    frames: dict[str, Any] = {}
    for label, spec in normalized["timeframes"].items():
        all_rows: dict[int, dict[str, Any]] = {}
        receipts: list[dict[str, Any]] = []
        counts = {"unconfirmed": 0, "outside_window": 0, "exact_duplicates": 0, "revised": 0}
        cursor = spec["end_ms"]
        page_count = 0
        while len(all_rows) < spec["expected"]:
            payload, receipt = client.get(HISTORY_PATH, {
                "instId": INSTRUMENT, "bar": spec["sourcebar"], "after": cursor,
                "before": spec["source_start_ms"] - 1, "limit": PAGE_LIMIT,
            })
            page_count += 1
            raw = payload["data"]
            parsed, page_counts = parse_confirmed_rows(raw, period_ms=spec["period_ms"], source_start_ms=spec["source_start_ms"], end_ms=spec["end_ms"])
            for key, value in page_counts.items():
                counts[key] += value
            if not raw:
                break
            stamps = [_as_int(row[0], field="history timestamp") for row in raw if isinstance(row, list) and row]
            if not stamps:
                raise SourceError("nonempty history page had no timestamp")
            oldest = min(stamps)
            if oldest >= cursor:
                raise SourceError("OKX history pagination cursor did not advance")
            for stamp, row in parsed.items():
                previous = all_rows.get(stamp)
                if previous is None:
                    all_rows[stamp] = row
                elif previous != row:
                    raise SourceError("conflicting duplicate confirmed history row across pages")
                else:
                    counts["exact_duplicates"] += 1
            receipts.append(receipt)
            cursor = oldest
            if progress is not None and page_count % 20 == 0:
                progress(f"{label}: pages={page_count} confirmed={len(all_rows)}/{spec['expected']}")
            if oldest <= spec["source_start_ms"]:
                break
        rows, gaps = _validate_grid(all_rows, spec)
        csv_sha = _write_csv(output_dir / f"{label}.csv", rows)
        frames[label] = {
            "expected": spec["expected"], "start": _iso(spec["source_start_ms"]), "end": _iso(spec["end_ms"]),
            "study_start": _iso(spec["start_ms"]), "gaps": gaps, "revised": counts["revised"],
            "confirmed": len(rows), "excluded": {key: value for key, value in counts.items() if key != "revised"},
            "raw_sha256": _combine_raw_sha(receipts), "csv_sha256": csv_sha,
            "pages": [{"request_sha256": receipt.get("request_sha256"), "body_sha256": receipt.get("body_sha256"),
                       "relative_body_path": receipt.get("relative_body_path")} for receipt in receipts],
        }
        if progress is not None:
            progress(f"{label}: complete pages={page_count} confirmed={len(rows)}/{spec['expected']}")
    summary = {
        "schema_version": SCHEMA_VERSION, "config_sha256": config_sha, "experiment_id": normalized["experiment_id"],
        "instrument": metadata, "tick_size": metadata["tickSz"], "instrument_metadata_sha256": _sha256(_json_bytes(metadata)),
        "instrument_receipt_sha256": _sha256(_json_bytes(instrument_receipt)), "timeframes": frames,
    }
    _atomic_json(output_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="pre-registered JSON config")
    parser.add_argument("--output", required=True, type=Path, help="new or identical resumable research output directory")
    args = parser.parse_args(argv)
    config = _read_json(args.config)
    builder = assert_builder_committed()
    summary = run_config(config, args.output, progress=print)
    summary["builder"] = builder
    _atomic_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
