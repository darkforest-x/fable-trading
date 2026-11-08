"""Build two complete OKX 1m drawdown-bound windows after explicit --fetch."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.fetch_okx import aggregate_archive_bytes, archive_url
from yoyo.evaluation import cloud_close_drawdown_quotes as base


OUTPUT_DIR = base.REPO_ROOT / "data/research/cloud_close_drawdown_bounds_1m_20261109"
FIVE_MINUTE_QUOTES = base.OUTPUT_DIR / "quotes.csv"
QUOTE_COLUMNS = ["symbol", "time", "open", "high", "low", "close"]
AUDIT_COLUMNS = [
    "window_id",
    "symbol",
    "hour_start_utc",
    "expected_1m_bars",
    "observed_1m_bars",
    "missing_1m_open_ms",
    "source_1m",
    "source_url_1m",
    "source_url_h1",
    "five_minute_match",
    "h1_match",
    "status",
    "reason",
    "checked_at_utc",
]
GROUPS = [
    (
        "nov04_21_22_utc",
        "2025-11-04T21:00:00Z",
        ["RESOLV", "JELLYJELLY", "H", "NMR", "TAO", "SOL", "DOGE"],
    ),
    (
        "nov12_14_15_utc",
        "2025-11-12T14:00:00Z",
        ["NMR", "SOL", "DOGE", "SYRUP", "SHIB", "LRC", "CFX", "CORE"],
    ),
]
ARCHIVE_MONTH = pd.Timestamp("2025-11-01T00:00:00Z")


def _aggregate(rows: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    candles = [row["ohlc"] for row in rows]
    return (
        candles[0][0],
        max(candle[1] for candle in candles),
        min(candle[2] for candle in candles),
        candles[-1][3],
    )


def _minute_archive() -> tuple[dict[int, dict[str, Any]], str]:
    symbol = "LRC_USDT_SWAP"
    url = archive_url(symbol, ARCHIVE_MONTH)
    payload = base._get_bytes(url)
    frame, audit = aggregate_archive_bytes(
        payload, symbol=symbol, month=ARCHIVE_MONTH, bar="1m"
    )
    if audit.get("confirm_values") != ["1"]:
        raise base.QuoteBuildError("LRC official archive contains unconfirmed 1m bars")
    return base._frame_to_candles(frame, timestamp_column="ts"), url


def _five_minute_reference() -> dict[tuple[str, int], tuple[float, float, float, float]]:
    if not FIVE_MINUTE_QUOTES.exists():
        raise base.QuoteBuildError(
            f"verified 5m baseline is missing: {FIVE_MINUTE_QUOTES}"
        )
    frame = pd.read_csv(FIVE_MINUTE_QUOTES)
    required = {"symbol", "time", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise base.QuoteBuildError("5m baseline has an unexpected schema")
    result = {}
    for row in frame.itertuples(index=False):
        key = (str(row.symbol), int(row.time))
        values = (float(row.open), float(row.high), float(row.low), float(row.close))
        if key in result and not base._same_ohlc(result[key], values):
            raise base.QuoteBuildError(f"duplicate conflicting 5m baseline row: {key}")
        result[key] = values
    return result


def _local_lrc_hour(hour_ms: int, local_15m: dict[int, dict[str, Any]]) -> tuple[float, float, float, float] | None:
    rows = [local_15m.get(ts) for ts in range(hour_ms, hour_ms + 3_600_000, 900_000)]
    if any(row is None for row in rows):
        return None
    return _aggregate([row for row in rows if row is not None])


def build() -> tuple[int, int]:
    five_reference = _five_minute_reference()
    local_lrc_15m = base._load_local_bars(base.LRC_LOCAL_15M)
    lrc_minutes, lrc_url = _minute_archive()
    quote_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for window_id, hour_start, symbols in GROUPS:
        start_ms = base._utc_ms(hour_start)
        end_ms = start_ms + 3_600_000
        expected = list(range(start_ms, end_ms, 60_000))
        for symbol in symbols:
            source = "okx_history_candles_1m"
            source_url = ""
            h1_url = ""
            reasons: list[str] = []
            try:
                if symbol == "LRC":
                    source = "okx_official_monthly_1m_archive"
                    source_url = lrc_url
                    minute_map = {
                        ts: row for ts, row in lrc_minutes.items() if start_ms <= ts < end_ms
                    }
                    h1_values = _local_lrc_hour(start_ms, local_lrc_15m)
                    h1_source = "local_okx_15m_aggregate"
                else:
                    minute_map, source_url = base._api_candles(
                        symbol, "1m", start_ms, end_ms
                    )
                    h1_map, h1_url = base._api_candles(
                        symbol, "1H", start_ms, end_ms
                    )
                    h1 = h1_map.get(start_ms)
                    h1_values = h1["ohlc"] if h1 and h1.get("confirm") == "1" else None
                    h1_source = "okx_history_candles_1H"
            except Exception as exc:
                minute_map = {}
                h1_values = None
                h1_source = "unavailable"
                reasons.append(f"source_error:{type(exc).__name__}:{exc}")

            confirmed = {
                ts: row for ts, row in minute_map.items() if row.get("confirm") == "1"
            }
            missing = [ts for ts in expected if ts not in confirmed]
            if missing:
                reasons.append("incomplete_1m_hour")
            if any(not base._valid_ohlc(row["ohlc"]) for row in confirmed.values()):
                reasons.append("invalid_1m_ohlc")

            hourly_rows = [confirmed[ts] for ts in expected if ts in confirmed]
            h1_match: bool | None = None
            five_match = True
            if len(hourly_rows) == 60:
                h1_values_1m = _aggregate(hourly_rows)
                if h1_values is None:
                    h1_match = False
                    reasons.append("missing_h1_reference")
                elif not base._valid_ohlc(h1_values):
                    h1_match = False
                    reasons.append("invalid_h1_reference")
                else:
                    h1_match = base._same_ohlc(h1_values_1m, h1_values)
                    if not h1_match:
                        reasons.append("h1_ohlc_mismatch")

                for five_start in range(start_ms, end_ms, 300_000):
                    five_rows = [
                        confirmed.get(ts)
                        for ts in range(five_start, five_start + 300_000, 60_000)
                    ]
                    if any(row is None for row in five_rows):
                        five_match = False
                        continue
                    five_values = _aggregate([row for row in five_rows if row is not None])
                    reference = five_reference.get((symbol, five_start))
                    if reference is None or not base._same_ohlc(five_values, reference):
                        five_match = False
                if not five_match:
                    reasons.append("five_minute_ohlc_mismatch")
            else:
                five_match = False
                reasons.append("cannot_aggregate_incomplete_hour")

            accepted = not reasons and h1_match is True and five_match
            checked = datetime.now(timezone.utc).isoformat()
            audit_rows.append(
                {
                    "window_id": window_id,
                    "symbol": symbol,
                    "hour_start_utc": base._utc_iso(start_ms),
                    "expected_1m_bars": 60,
                    "observed_1m_bars": len(hourly_rows),
                    "missing_1m_open_ms": ";".join(map(str, missing)),
                    "source_1m": source,
                    "source_url_1m": source_url,
                    "source_url_h1": h1_url or h1_source,
                    "five_minute_match": str(five_match).lower(),
                    "h1_match": "true" if h1_match is True else "false",
                    "status": "included" if accepted else "missing",
                    "reason": "ok" if accepted else ";".join(dict.fromkeys(reasons)),
                    "checked_at_utc": checked,
                }
            )
            if accepted:
                quote_rows.extend(
                    {
                        "symbol": symbol,
                        "time": int(row["time"]),
                        "open": row["ohlc"][0],
                        "high": row["ohlc"][1],
                        "low": row["ohlc"][2],
                        "close": row["ohlc"][3],
                    }
                    for row in hourly_rows
                )

    quote_rows.sort(key=lambda row: (row["symbol"], int(row["time"])))
    audit_rows.sort(key=lambda row: (row["window_id"], row["symbol"]))
    base._write_csv_atomic(OUTPUT_DIR / "quotes.csv", quote_rows, QUOTE_COLUMNS)
    base._write_csv_atomic(OUTPUT_DIR / "acquisition_audit.csv", audit_rows, AUDIT_COLUMNS)
    included = sum(row["status"] == "included" for row in audit_rows)
    return included, len(quote_rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="write the verified 1m research input")
    args = parser.parse_args(argv)
    if not args.fetch:
        parser.error("explicit --fetch is required to write the 1m input")
    groups, rows = build()
    print(f"included symbol-hours={groups}; quote rows={rows}; output={OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
