"""Build the frozen OKX five-minute quote window for drawdown bounds.

This is a research input collector. It uses public OKX history-candles for
native 5m and 1H candles, with the official OKX monthly 1m archive for LRC
(its history-candles instrument id is no longer available). The command is
deliberately opt-in: pass --fetch to write the normalized inputs.

Only complete symbol-hour groups are emitted. Each accepted hour has twelve
confirmed UTC-aligned 5m bars, valid OHLC, and an H1 OHLC match. Output time is
UTC epoch milliseconds at the 5m bar open.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.fetch_okx import aggregate_archive_bytes, archive_url


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    REPO_ROOT
    / "experiments"
    / "active"
    / "exp-cloud-close-drawdown-bounds-20261109-v1"
    / "config.json"
)
OUTPUT_DIR = REPO_ROOT / "data" / "research" / "cloud_close_drawdown_bounds_20261109"
API_URL = "https://www.okx.com/api/v5/market/history-candles"
API_LIMIT = 100
MAX_RETRIES = 4
MIN_REQUEST_INTERVAL_S = 0.13
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; fable-trading-research/1.0)",
    "Accept": "application/json, */*",
}
QUOTE_COLUMNS = ["symbol", "time", "open", "high", "low", "close"]
AUDIT_COLUMNS = [
    "window_id",
    "symbol",
    "hour_start_utc",
    "expected_5m_bars",
    "observed_5m_bars",
    "missing_5m_open_ms",
    "source_5m",
    "source_url_5m",
    "h1_reference",
    "h1_match",
    "local_h1_match",
    "status",
    "reason",
    "checked_at_utc",
]
LOCAL_MARKET_DIR = REPO_ROOT / "data" / "crypto" / "market" / "kline_fetched"
LRC_ARCHIVE_MONTH = pd.Timestamp("2025-11-01T00:00:00Z")
LRC_ARCHIVE_SYMBOL = "LRC_USDT_SWAP"
LRC_LOCAL_15M = LOCAL_MARKET_DIR / "okx_LRC_USDT_SWAP_15m_39172.csv"
_last_request_at = 0.0


class QuoteBuildError(RuntimeError):
    """Raised when a frozen input source cannot satisfy its data contract."""


def _utc_ms(value: str) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise QuoteBuildError(f"timestamp must include UTC offset: {value}")
    return int(stamp.tz_convert("UTC").value // 1_000_000)


def _utc_iso(value_ms: int) -> str:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).isoformat()


def _pace_request() -> None:
    global _last_request_at
    now = time.monotonic()
    wait = MIN_REQUEST_INTERVAL_S - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get_json(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _pace_request()
        try:
            request = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise QuoteBuildError("OKX response must be a JSON object")
            if payload.get("code") != "0":
                raise QuoteBuildError(
                    f"OKX error {payload.get('code')}: {payload.get('msg')}"
                )
            return payload
        except QuoteBuildError:
            raise
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt + 1 < MAX_RETRIES:
                time.sleep(min(2**attempt, 8))
    raise QuoteBuildError(f"OKX request failed after retries: {last_error}")


def _get_bytes(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _pace_request()
        try:
            request = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < MAX_RETRIES:
                time.sleep(min(2**attempt, 8))
    raise QuoteBuildError(f"OKX archive request failed after retries: {last_error}")


def _api_candles(
    symbol: str,
    bar: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[int, dict[str, Any]], str]:
    """Fetch one bounded history page; OKX timestamps are UTC bar-open ms."""

    instrument_id = f"{symbol}-USDT-SWAP"
    query = urllib.parse.urlencode(
        {
            "instId": instrument_id,
            "bar": bar,
            "after": str(end_ms),
            "limit": str(API_LIMIT),
        }
    )
    url = f"{API_URL}?{query}"
    payload = _get_json(url)
    output: dict[int, dict[str, Any]] = {}
    for raw in payload.get("data", []):
        if len(raw) < 5:
            continue
        timestamp = int(raw[0])
        if not start_ms <= timestamp < end_ms:
            continue
        values = tuple(float(value) for value in raw[1:5])
        confirm = str(raw[8]) if len(raw) > 8 else "unknown"
        row = {"time": timestamp, "ohlc": values, "confirm": confirm}
        prior = output.get(timestamp)
        if prior is not None and prior != row:
            raise QuoteBuildError(
                f"conflicting duplicate {bar} candles for {instrument_id} at {timestamp}"
            )
        output[timestamp] = row
    return output, url


def _archive_lrc_bars() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], str]:
    """Read official November 2025 LRC 1m archive and aggregate to 5m/15m."""

    url = archive_url(LRC_ARCHIVE_SYMBOL, LRC_ARCHIVE_MONTH)
    payload = _get_bytes(url)
    five_minute, audit5 = aggregate_archive_bytes(
        payload,
        symbol=LRC_ARCHIVE_SYMBOL,
        month=LRC_ARCHIVE_MONTH,
        bar="5m",
    )
    fifteen_minute, audit15 = aggregate_archive_bytes(
        payload,
        symbol=LRC_ARCHIVE_SYMBOL,
        month=LRC_ARCHIVE_MONTH,
        bar="15m",
    )
    for audit in (audit5, audit15):
        if audit.get("confirm_values") != ["1"]:
            raise QuoteBuildError("LRC archive contains unconfirmed 1m candles")

    five = _frame_to_candles(five_minute, timestamp_column="ts")
    fifteen = _frame_to_candles(fifteen_minute, timestamp_column="ts")
    return five, fifteen, url


def _frame_to_candles(frame: pd.DataFrame, *, timestamp_column: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in frame.itertuples(index=False):
        timestamp = int(getattr(row, timestamp_column))
        values = (float(row.open), float(row.high), float(row.low), float(row.close))
        output[timestamp] = {"time": timestamp, "ohlc": values, "confirm": "1"}
    return output


def _load_local_bars(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    required = {"ts", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise QuoteBuildError(f"local reference lacks columns: {sorted(required - set(frame.columns))}")
    output: dict[int, dict[str, Any]] = {}
    for row in frame.itertuples(index=False):
        timestamp = int(row.ts)
        values = (float(row.open), float(row.high), float(row.low), float(row.close))
        prior = output.get(timestamp)
        candle = {"time": timestamp, "ohlc": values, "confirm": "1"}
        if prior is not None and prior != candle:
            raise QuoteBuildError(f"conflicting local reference candle at {timestamp}: {path}")
        output[timestamp] = candle
    return output


def _valid_ohlc(values: tuple[float, float, float, float]) -> bool:
    open_, high, low, close = values
    return (
        all(math.isfinite(value) and value > 0.0 for value in values)
        and high >= max(open_, close)
        and low <= min(open_, close)
        and high >= low
    )


def _same_ohlc(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return len(left) == len(right) == 4 and all(
        abs(a - b) <= max(1e-12, abs(b) * 1e-10)
        for a, b in zip(left, right)
    )


def _aggregate_hour(
    bars: list[dict[str, Any]],
) -> tuple[float, float, float, float] | None:
    if len(bars) != 12:
        return None
    values = [row["ohlc"] for row in bars]
    return (
        values[0][0],
        max(value[1] for value in values),
        min(value[2] for value in values),
        values[-1][3],
    )


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config.get("windows"), list) or not config["windows"]:
        raise QuoteBuildError("config must define non-empty windows")
    return config


def _group_audit(
    *,
    window_id: str,
    symbol: str,
    hour_start_ms: int,
    observed: list[dict[str, Any]],
    source_5m: str,
    source_url_5m: str,
    h1_reference: str,
    h1_candle: dict[str, Any] | None,
    local_h1_candle: dict[str, Any] | None,
    archive_15m: dict[int, dict[str, Any]] | None = None,
    local_15m: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    expected_times = list(range(hour_start_ms, hour_start_ms + 3_600_000, 300_000))
    by_time = {int(row["time"]): row for row in observed}
    group = [by_time[timestamp] for timestamp in expected_times if timestamp in by_time]
    missing = [timestamp for timestamp in expected_times if timestamp not in by_time]
    reasons: list[str] = []
    if missing:
        reasons.append("incomplete_5m_hour")
    if len(group) != 12 or any(row.get("confirm") != "1" for row in group):
        reasons.append("not_12_confirmed_5m_bars")
    if any(not _valid_ohlc(row["ohlc"]) for row in group):
        reasons.append("invalid_5m_ohlc")

    aggregated = _aggregate_hour(group)
    h1_match: bool | None = None
    if aggregated is None or h1_candle is None:
        reasons.append("missing_h1_reference")
    elif h1_candle.get("confirm") != "1" or not _valid_ohlc(h1_candle["ohlc"]):
        reasons.append("invalid_h1_reference")
    else:
        h1_match = _same_ohlc(aggregated, h1_candle["ohlc"])
        if not h1_match:
            reasons.append("h1_ohlc_mismatch")

    local_h1_match: bool | str = "not_available"
    if local_h1_candle is not None:
        if aggregated is None:
            local_h1_match = False
        else:
            local_h1_match = _same_ohlc(aggregated, local_h1_candle["ohlc"])
            if not local_h1_match:
                reasons.append("local_h1_ohlc_mismatch")

    if archive_15m is not None and local_15m is not None:
        quarter_starts = list(range(hour_start_ms, hour_start_ms + 3_600_000, 900_000))
        for timestamp in quarter_starts:
            archive_row = archive_15m.get(timestamp)
            local_row = local_15m.get(timestamp)
            if archive_row is None or local_row is None:
                reasons.append("incomplete_lrc_15m_crosscheck")
                break
            if not _same_ohlc(archive_row["ohlc"], local_row["ohlc"]):
                reasons.append("lrc_archive_local_15m_mismatch")
                break

    accepted = not reasons
    checked_at = datetime.now(timezone.utc).isoformat()
    audit = {
        "window_id": window_id,
        "symbol": symbol,
        "hour_start_utc": _utc_iso(hour_start_ms),
        "expected_5m_bars": 12,
        "observed_5m_bars": len(group),
        "missing_5m_open_ms": ";".join(map(str, missing)),
        "source_5m": source_5m,
        "source_url_5m": source_url_5m,
        "h1_reference": h1_reference,
        "h1_match": "true" if h1_match is True else "false" if h1_match is False else "missing",
        "local_h1_match": str(local_h1_match).lower(),
        "status": "included" if accepted else "missing",
        "reason": "ok" if accepted else ";".join(dict.fromkeys(reasons)),
        "checked_at_utc": checked_at,
    }
    quote_rows = []
    if accepted:
        quote_rows = [
            {
                "symbol": symbol,
                "time": int(row["time"]),
                "open": row["ohlc"][0],
                "high": row["ohlc"][1],
                "low": row["ohlc"][2],
                "close": row["ohlc"][3],
            }
            for row in sorted(group, key=lambda item: int(item["time"]))
        ]
    return audit, quote_rows


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def build(config_path: Path = CONFIG_PATH, output_dir: Path = OUTPUT_DIR) -> tuple[int, int]:
    config = _load_config(config_path)
    quote_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    lrc_five: dict[int, dict[str, Any]] = {}
    lrc_fifteen: dict[int, dict[str, Any]] = {}
    lrc_archive_url = ""
    lrc_local_15m = _load_local_bars(LRC_LOCAL_15M)

    # The old LRC history-candles instrument id returns 51001. The official
    # 2025-11 monthly 1m archive is stable and independently cross-checked
    # against the locally retained 15m LRC swap series.
    try:
        lrc_five, lrc_fifteen, lrc_archive_url = _archive_lrc_bars()
    except Exception as exc:
        lrc_archive_error = f"{type(exc).__name__}: {exc}"
    else:
        lrc_archive_error = ""

    for window in config["windows"]:
        window_id = str(window["id"])
        start_ms = _utc_ms(str(window["start_utc"]))
        end_ms = _utc_ms(str(window["end_utc_exclusive"]))
        if start_ms >= end_ms or start_ms % 3_600_000 or end_ms % 3_600_000:
            raise QuoteBuildError(f"window {window_id} must be ascending and hour-aligned")
        symbols = [str(symbol).upper() for symbol in window["symbols"]]
        hour_starts = list(range(start_ms, end_ms, 3_600_000))

        for symbol in symbols:
            five_source = "okx_history_candles_5m"
            five_url = ""
            h1_source = "okx_history_candles_1H"
            five_map: dict[int, dict[str, Any]] = {}
            h1_map: dict[int, dict[str, Any]] = {}
            local_h1_map: dict[int, dict[str, Any]] = {}
            local_15m_map: dict[int, dict[str, Any]] = {}
            error: str | None = None

            if symbol == "LRC":
                five_source = "okx_official_monthly_1m_archive_aggregated_5m"
                five_url = lrc_archive_url
                h1_source = "local_okx_15m_aggregate"
                if lrc_archive_error:
                    error = f"lrc_archive_unavailable:{lrc_archive_error}"
                else:
                    five_map = lrc_five
                    local_15m_map = lrc_local_15m
                    if not local_15m_map:
                        error = "lrc_local_15m_reference_missing"
                    else:
                        # A native LRC 1H history-candles id is retired. Form
                        # the reference from the retained independent 15m bars.
                        for hour in hour_starts:
                            parts = [local_15m_map.get(ts) for ts in range(hour, hour + 3_600_000, 900_000)]
                            if any(part is None for part in parts):
                                continue
                            values = [part["ohlc"] for part in parts if part is not None]
                            h1_map[hour] = {
                                "time": hour,
                                "ohlc": (
                                    values[0][0],
                                    max(value[1] for value in values),
                                    min(value[2] for value in values),
                                    values[-1][3],
                                ),
                                "confirm": "1",
                            }
            else:
                try:
                    five_map, five_url = _api_candles(symbol, "5m", start_ms, end_ms)
                except Exception as exc:
                    error = f"5m_api:{type(exc).__name__}:{exc}"
                try:
                    h1_map, _ = _api_candles(symbol, "1H", start_ms, end_ms)
                except Exception as exc:
                    error = ";".join(filter(None, [error, f"1h_api:{type(exc).__name__}:{exc}"]))
                local_h1_path = next(
                    iter(sorted(LOCAL_MARKET_DIR.glob(f"okx_{symbol}_USDT_SWAP_1H_*.csv"))),
                    None,
                )
                if local_h1_path is not None:
                    local_h1_map = _load_local_bars(local_h1_path)

            for hour in hour_starts:
                observed = [
                    row
                    for timestamp, row in five_map.items()
                    if hour <= timestamp < hour + 3_600_000
                ]
                reference = h1_map.get(hour)
                if error:
                    reference = None
                audit, accepted_rows = _group_audit(
                    window_id=window_id,
                    symbol=symbol,
                    hour_start_ms=hour,
                    observed=observed,
                    source_5m=five_source,
                    source_url_5m=five_url,
                    h1_reference=h1_source,
                    h1_candle=reference,
                    local_h1_candle=local_h1_map.get(hour),
                    archive_15m=lrc_fifteen if symbol == "LRC" else None,
                    local_15m=local_15m_map if symbol == "LRC" else None,
                )
                if error:
                    audit["status"] = "missing"
                    audit["reason"] = ";".join(filter(None, [audit["reason"], error]))
                audit_rows.append(audit)
                quote_rows.extend(accepted_rows if audit["status"] == "included" else [])

    quote_rows.sort(key=lambda row: (str(row["symbol"]), int(row["time"])))
    audit_rows.sort(key=lambda row: (str(row["window_id"]), int(_utc_ms(row["hour_start_utc"])), str(row["symbol"])))
    _write_csv_atomic(output_dir / "quotes.csv", quote_rows, QUOTE_COLUMNS)
    _write_csv_atomic(output_dir / "acquisition_audit.csv", audit_rows, AUDIT_COLUMNS)
    included_groups = sum(row["status"] == "included" for row in audit_rows)
    return included_groups, len(quote_rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="fetch public OKX history and write the normalized research inputs",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    if not args.fetch:
        parser.error("explicit --fetch is required to write data/research inputs")
    groups, rows = build(args.config, args.output_dir)
    print(f"included symbol-hours={groups}; quote rows={rows}; output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
