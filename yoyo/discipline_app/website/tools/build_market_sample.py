#!/usr/bin/env python3
"""Build the website's deterministic offline chart sample from frozen OKX bars.

The source is the registered local 5m BTC-USDT-SWAP export of OKX's public
monthly candle archives. This script only reads that frozen input and writes
the small static JSON asset consumed by ``dist/chart-demo.js``.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "data/crypto/market/kline_preholdout_okx_5m/okx_BTC_USDT_SWAP_5m_341567.csv"
RECEIPT = ROOT / "data/crypto/market/kline_preholdout_okx_5m/archive_BTC_USDT_SWAP.json"
OUTPUT = ROOT / "yoyo/discipline_app/website/dist/assets/market-btc-usdt-swap.json"

FIVE_MINUTES_MS = 5 * 60_000
SAMPLE_START_MS = int(datetime(2026, 2, 20, tzinfo=timezone.utc).timestamp() * 1000)
SAMPLE_END_MS = int(datetime(2026, 2, 26, tzinfo=timezone.utc).timestamp() * 1000)
FOCUS_END_MS = int(datetime(2026, 2, 24, 6, tzinfo=timezone.utc).timestamp() * 1000)
MONDAY_NY_DATE = "2026-02-23"
EXAMPLE_ENTRY_OPEN_MS = int(datetime(2026, 2, 24, 1, tzinfo=timezone.utc).timestamp() * 1000)
EXAMPLE_EXIT_OPEN_MS = int(datetime(2026, 2, 24, 5, tzinfo=timezone.utc).timestamp() * 1000)
EXTENSION = 0.618


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def number(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric {field}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite {field}")
    return parsed


def read_frozen_sample() -> tuple[list[dict[str, float | int]], dict[str, object]]:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    contract = receipt.get("contract", {})
    if contract.get("bar") != "5m" or contract.get("symbol") != "BTC_USDT_SWAP":
        raise ValueError("frozen OKX receipt has an unexpected symbol or bar size")
    if receipt.get("status") != "complete" or receipt.get("holdout_ohlcv_rows_materialized") != 0:
        raise ValueError("frozen OKX receipt is incomplete or not the expected export")

    monthly = next((row for row in receipt.get("monthly_audits", []) if row.get("month") == "2026-02"), None)
    if not monthly or monthly.get("confirm_values") != ["1"]:
        raise ValueError("the selected OKX month lacks a confirmed archive audit")

    selected: list[dict[str, float | int]] = []
    source_rows = 0
    previous: int | None = None
    with SOURCE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"ts", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"unexpected source columns: {reader.fieldnames}")
        for row in reader:
            source_rows += 1
            stamp = int(row["ts"])
            if previous is not None and stamp <= previous:
                raise ValueError("source timestamps are not strictly increasing")
            previous = stamp
            if stamp % FIVE_MINUTES_MS:
                raise ValueError(f"source timestamp is not UTC 5m aligned: {stamp}")
            if not SAMPLE_START_MS <= stamp < SAMPLE_END_MS:
                continue
            bar = {
                "t": stamp,
                "o": number(row["open"], "open"),
                "h": number(row["high"], "high"),
                "l": number(row["low"], "low"),
                "c": number(row["close"], "close"),
                "v": number(row["volume"], "volume"),
            }
            if min(bar["o"], bar["h"], bar["l"], bar["c"]) <= 0 or bar["v"] < 0:
                raise ValueError(f"invalid OHLCV at {stamp}")
            if bar["h"] < max(bar["o"], bar["c"]) or bar["l"] > min(bar["o"], bar["c"]):
                raise ValueError(f"invalid OHLC bounds at {stamp}")
            selected.append(bar)

    if source_rows != receipt.get("rows"):
        raise ValueError(f"source row count {source_rows} differs from receipt {receipt.get('rows')}")
    expected = (SAMPLE_END_MS - SAMPLE_START_MS) // FIVE_MINUTES_MS
    if len(selected) != expected:
        raise ValueError(f"sample should contain {expected} bars, found {len(selected)}")
    if any(b["t"] - a["t"] != FIVE_MINUTES_MS for a, b in zip(selected, selected[1:])):
        raise ValueError("sample has a missing or duplicate 5m timestamp")
    return selected, monthly


def aggregate(rows: list[dict[str, float | int]], minutes: int) -> list[dict[str, float | int]]:
    step_ms = minutes * 60_000
    count = minutes // 5
    result: list[dict[str, float | int]] = []
    for offset in range(0, len(rows), count):
        chunk = rows[offset : offset + count]
        if len(chunk) != count:
            raise ValueError(f"incomplete {minutes}m source group")
        bucket = int(chunk[0]["t"]) // step_ms * step_ms
        if int(chunk[0]["t"]) != bucket or any(
            int(bar["t"]) != bucket + i * FIVE_MINUTES_MS for i, bar in enumerate(chunk)
        ):
            raise ValueError(f"non-contiguous or misaligned {minutes}m source group at {bucket}")
        result.append(
            {
                "t": bucket,
                "o": chunk[0]["o"],
                "h": max(bar["h"] for bar in chunk),
                "l": min(bar["l"] for bar in chunk),
                "c": chunk[-1]["c"],
                "v": sum(bar["v"] for bar in chunk),
            }
        )
    return result


def wire(rows: list[dict[str, float | int]]) -> list[list[float | int]]:
    return [[bar["t"], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]] for bar in rows]


def main() -> None:
    raw, monthly = read_frozen_sample()
    m15 = aggregate(raw, 15)
    h1 = aggregate(raw, 60)

    new_york = ZoneInfo("America/New_York")
    local_monday = datetime.fromisoformat(MONDAY_NY_DATE).replace(tzinfo=new_york)
    local_tuesday = local_monday.replace(day=local_monday.day + 1)
    monday_start = epoch_ms(local_monday)
    monday_end = epoch_ms(local_tuesday)
    monday_bars = [bar for bar in raw if monday_start <= int(bar["t"]) < monday_end]
    if len(monday_bars) != 24 * 60 // 5:
        raise ValueError(f"NY Monday should contain 288 complete 5m candles, got {len(monday_bars)}")
    if not any(int(bar["t"]) >= monday_end for bar in raw):
        raise ValueError("sample has no post-close candle; Monday levels must remain hidden")
    high = max(float(bar["h"]) for bar in monday_bars)
    low = min(float(bar["l"]) for bar in monday_bars)
    span = high - low
    entry_source = next((bar for bar in raw if int(bar["t"]) == EXAMPLE_ENTRY_OPEN_MS), None)
    exit_source = next((bar for bar in raw if int(bar["t"]) == EXAMPLE_EXIT_OPEN_MS), None)
    if entry_source is None or exit_source is None:
        raise ValueError("educational entry/exit 5m bars are missing from the sample")

    metadata = {
        "exchange": "OKX",
        "instrument": "BTC-USDT-SWAP",
        "timeZone": "UTC",
        "displayTimeZone": "America/New_York",
        "sourceType": "frozen-public-market-history",
        "sourceFile": str(SOURCE.relative_to(ROOT)),
        "sourceReceipt": str(RECEIPT.relative_to(ROOT)),
        "sourceArchiveUrl": monthly["url"],
        "sourceMethod": "OKX public 1m monthly candles aggregated into confirmed UTC 5m bars; website 15m and 1H bars are aggregated from the same frozen 5m rows.",
        "sourceRows": int(receipt_rows := json.loads(RECEIPT.read_text(encoding="utf-8"))["rows"]),
        "sampleStart": datetime.fromtimestamp(SAMPLE_START_MS / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "sampleEndExclusive": datetime.fromtimestamp(SAMPLE_END_MS / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "focusEndMs": FOCUS_END_MS,
        "monday": {
            "newYorkDate": MONDAY_NY_DATE,
            "startMs": monday_start,
            "endMs": monday_end,
            "high": high,
            "low": low,
            "upper1618": high + EXTENSION * span,
            "lower1618": low - EXTENSION * span,
            "extensionFraction": EXTENSION,
        },
        "exampleTrade": {
            "label": "教学示意，非实际执行",
            "entrySourceOpenMs": EXAMPLE_ENTRY_OPEN_MS,
            "entryMs": EXAMPLE_ENTRY_OPEN_MS + FIVE_MINUTES_MS,
            "exitSourceOpenMs": EXAMPLE_EXIT_OPEN_MS,
            "exitMs": EXAMPLE_EXIT_OPEN_MS + FIVE_MINUTES_MS,
            "markerPriceField": "source 5m close",
        },
        "volumeField": "OKX archive vol sum; displayed only as relative volume bars.",
        "offlineAsset": True,
    }
    if int(metadata["sourceRows"]) != receipt_rows:
        raise ValueError("receipt row count changed during export")

    payload = {
        "metadata": metadata,
        "bars": {"5m": wire(raw), "15m": wire(m15), "1H": wire(h1)},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: 5m={len(raw)}, 15m={len(m15)}, 1H={len(h1)}, "
        f"NY Monday H={high:g} L={low:g} upper={high + EXTENSION * span:.4f} lower={low - EXTENSION * span:.4f}; "
        f"entry={metadata['exampleTrade']['entryMs']} close={entry_source['c']}; "
        f"exit={metadata['exampleTrade']['exitMs']} close={exit_source['c']}"
    )


if __name__ == "__main__":
    main()
