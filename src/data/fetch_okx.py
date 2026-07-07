"""Resumable OKX 15m history fetcher (public API, no key needed).

Fills the data gap identified in p2b: the old cache has only one full-year
series (ETH_USDT_SWAP); everything else is <6 months. This script pulls
`DAYS` days of 15m candles for a curated list of liquid symbols into
data/kline_fetched/, in files the loader merges with the old cache.

Run ON A MACHINE WITH OKX ACCESS (the Cowork sandbox cannot reach okx.com):

    python3 -m src.data.fetch_okx            # all default symbols
    python3 -m src.data.fetch_okx --symbols BTC_USDT ETH_USDT
    python3 -m src.data.fetch_okx --days 400

Resumable: progress is kept in {SYMBOL}_15m.part.csv (ignored by the loader);
finished symbols are skipped on rerun. Safe to Ctrl-C and restart.

Rate limit: history-candles allows 20 req / 2 s; we sleep 0.12 s per request.
Expected runtime for ~55 symbols x 400 days: roughly 45-60 minutes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FETCH_DIR = Path(__file__).resolve().parents[2] / "data" / "kline_fetched"
API = "https://www.okx.com/api/v5/market/history-candles"
BAR = "15m"
PAGE_LIMIT = 100
SLEEP_S = 0.12
MAX_RETRIES = 5

# Curated liquid symbols expected to have >=1 year of OKX history (spot,
# matching the old cache's symbol keys), plus the one legacy swap series.
# Stablecoins/gold are excluded (loader blocks them anyway).
DEFAULT_SYMBOLS = [
    "ETH_USDT_SWAP",
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "DOGE_USDT", "ADA_USDT", "TRX_USDT", "LTC_USDT", "BCH_USDT",
    "LINK_USDT", "AVAX_USDT", "DOT_USDT", "UNI_USDT", "AAVE_USDT",
    "ATOM_USDT", "NEAR_USDT", "APT_USDT", "SUI_USDT", "FIL_USDT",
    "ICP_USDT", "XLM_USDT", "HBAR_USDT", "OP_USDT", "INJ_USDT",
    "TIA_USDT", "ORDI_USDT", "PEPE_USDT", "SHIB_USDT", "WLD_USDT",
    "ENA_USDT", "ETHFI_USDT", "JTO_USDT", "ONDO_USDT", "CRV_USDT",
    "APE_USDT", "CHZ_USDT", "CFX_USDT", "ZRO_USDT", "ID_USDT",
    "GALA_USDT", "EIGEN_USDT", "VIRTUAL_USDT", "PENGU_USDT",
    "TRUMP_USDT", "PI_USDT", "HYPE_USDT", "OKB_USDT", "ZEC_USDT",
    "TON_USDT", "ARB_USDT", "POL_USDT", "ETC_USDT", "SAND_USDT",
    "GRT_USDT", "ZK_USDT",
]


def _request(url: str) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            print(f"    retry {attempt + 1}/{MAX_RETRIES} in {wait}s ({exc})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def _finished_file(symbol: str) -> Path | None:
    hits = sorted(FETCH_DIR.glob(f"okx_{symbol}_{BAR}_*.csv"))
    return hits[-1] if hits else None


def fetch_symbol(symbol: str, start_ms: int) -> None:
    inst_id = symbol.replace("_", "-")
    part = FETCH_DIR / f"{symbol}_{BAR}.part.csv"
    rows: list[list] = []
    oldest_ms: int | None = None
    if part.exists():  # resume: reload progress, continue from oldest ts
        with part.open() as fh:
            rows = [r for r in csv.reader(fh)][1:]
        if rows:
            oldest_ms = min(int(r[0]) for r in rows)
            print(f"  resuming at {datetime.fromtimestamp(oldest_ms / 1e3, tz=timezone.utc):%Y-%m-%d}", flush=True)

    header = ["ts", "open", "high", "low", "close", "volume", "open_time"]
    if not part.exists():
        part.write_text(",".join(header) + "\n")

    while oldest_ms is None or oldest_ms > start_ms:
        url = f"{API}?instId={inst_id}&bar={BAR}&limit={PAGE_LIMIT}"
        if oldest_ms is not None:
            url += f"&after={oldest_ms}"
        payload = _request(url)
        if payload.get("code") != "0":
            print(f"  API error for {inst_id}: {payload.get('msg')} -- skipping", flush=True)
            break
        page = payload.get("data") or []
        if not page:
            break  # listed later than start date: no more history
        new_rows = []
        for r in page:  # [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
            ts = int(r[0])
            if len(r) > 8 and r[8] == "0":
                continue  # unconfirmed candle
            ot = datetime.fromtimestamp(ts / 1e3, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
            new_rows.append([ts, r[1], r[2], r[3], r[4], r[5], ot])
        with part.open("a", newline="") as fh:
            csv.writer(fh).writerows(new_rows)
        rows.extend(new_rows)
        oldest_ms = int(page[-1][0])
        time.sleep(SLEEP_S)

    if not rows:
        part.unlink(missing_ok=True)
        print("  no data", flush=True)
        return
    # dedupe + sort, write final file named to match the loader's pattern
    uniq = {int(r[0]): r for r in rows}
    final_rows = [uniq[k] for k in sorted(uniq)]
    out = FETCH_DIR / f"okx_{symbol}_{BAR}_{len(final_rows)}.csv"
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(final_rows)
    part.unlink(missing_ok=True)
    first = datetime.fromtimestamp(sorted(uniq)[0] / 1e3, tz=timezone.utc)
    print(f"  done: {len(final_rows)} bars from {first:%Y-%m-%d} -> {out.name}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=400)
    args = parser.parse_args()
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
    for n, symbol in enumerate(args.symbols, 1):
        done = _finished_file(symbol)
        if done is not None:
            print(f"[{n}/{len(args.symbols)}] {symbol}: already fetched ({done.name})", flush=True)
            continue
        print(f"[{n}/{len(args.symbols)}] {symbol}", flush=True)
        try:
            fetch_symbol(symbol, start_ms)
        except RuntimeError as exc:
            print(f"  FAILED: {exc} (rerun to resume)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
