"""Funding-rate history for OKX perpetual swaps (P1-7, unblocks H12).

Pages /api/v5/public/funding-rate-history backward into
data/funding/{SYMBOL}.csv. The requested horizon is 400 days, but OKX public
history can cap earlier by symbol/API availability; downstream backtests must
report funding coverage instead of silently treating missing history as zero.

Usage: python3 -m src.data.fetch_funding
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from src.data.fetch_okx import DEFAULT_SYMBOLS, _request

API = "https://www.okx.com/api/v5/public/funding-rate-history"
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "funding"
DAYS = 400
PAGE_LIMIT = 400


def fetch_symbol(symbol: str) -> int:
    inst_id = symbol.replace("_USDT_SWAP", "-USDT-SWAP").replace("_", "-")
    start_ms = int((datetime.now(timezone.utc).timestamp() - DAYS * 86400) * 1000)
    rows: dict[int, list] = {}
    before: int | None = None
    while True:
        url = f"{API}?instId={inst_id}&limit={PAGE_LIMIT}"
        if before is not None:
            url += f"&after={before}"
        payload = _request(url)
        if payload.get("code") != "0":
            print(f"  {symbol}: API error: {payload.get('msg')} -- skipped", flush=True)
            return 0
        page = payload.get("data") or []
        if not page:
            break
        for r in page:
            ts = int(r["fundingTime"])
            rows[ts] = [ts, r["fundingRate"], r.get("realizedRate", "")]
        oldest = min(int(r["fundingTime"]) for r in page)
        if oldest <= start_ms:
            break
        before = oldest
    if not rows:
        return 0
    out = OUT_DIR / f"{symbol}.csv"
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["funding_time_ms", "funding_rate", "realized_rate"])
        writer.writerows(rows[k] for k in sorted(rows))
    return len(rows)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    swaps = sorted({s if s.endswith("_SWAP") else s.replace("_USDT", "_USDT_SWAP")
                    for s in DEFAULT_SYMBOLS})
    total = 0
    for n, symbol in enumerate(swaps, 1):
        if (OUT_DIR / f"{symbol}.csv").exists():
            continue
        got = fetch_symbol(symbol)
        total += got
        print(f"[{n}/{len(swaps)}] {symbol}: {got} funding rows", flush=True)
    print(f"done: {total} rows", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
