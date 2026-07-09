"""Resumable OKX history fetcher (public API, no key needed).

Fills the data gap identified in p2b: the old cache has only one full-year
series (ETH_USDT_SWAP); everything else is <6 months. This script pulls
`DAYS` days of candles for a curated list of liquid symbols into
data/kline_fetched/, in files the loader merges with the old cache.

Run ON A MACHINE WITH OKX ACCESS (the Cowork sandbox cannot reach okx.com):

    python3 -m src.data.fetch_okx            # all default symbols
    python3 -m src.data.fetch_okx --symbols BTC_USDT ETH_USDT
    python3 -m src.data.fetch_okx --days 400

Resumable: progress is kept in {SYMBOL}_{bar}.part.csv (ignored by the loader);
finished symbols are skipped on rerun. Safe to Ctrl-C and restart.

Rate limit: history-candles allows 20 req / 2 s; a global throttle spaces
requests >=0.12 s apart across all workers (<=8.3 req/s). Symbols are fetched
in parallel (--workers, default 8) so per-request network latency overlaps;
expected runtime for ~55 symbols x 400 days is under 2 hours even at ~1.5 s
per request.
"""
from __future__ import annotations

import argparse
import csv
import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.bars import BAR_CHOICES, normalize_bar

FETCH_DIR = Path(__file__).resolve().parents[2] / "data" / "kline_fetched"
API = "https://www.okx.com/api/v5/market/history-candles"
DEFAULT_BAR = "15m"
PAGE_LIMIT = 100
MAX_RETRIES = 5
DEFAULT_WORKERS = 8
# Global request spacing shared by all workers: <=8.3 req/s, safely under
# OKX's 20 req / 2 s limit for history-candles.
MIN_REQUEST_INTERVAL_S = 0.12

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


REQUEST_HEADERS = {
    # OKX's WAF rejects the default Python-urllib user agent with 403.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}


_rate_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    while True:
        with _rate_lock:
            now = time.monotonic()
            wait = _last_request_at + MIN_REQUEST_INTERVAL_S - now
            if wait <= 0:
                _last_request_at = now
                return
        time.sleep(wait)


def _request(url: str) -> dict:
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            print(f"    retry {attempt + 1}/{MAX_RETRIES} in {wait}s ({exc})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def _finished_file(symbol: str, bar: str) -> Path | None:
    hits = sorted(FETCH_DIR.glob(f"okx_{symbol}_{bar}_*.csv"))
    return hits[-1] if hits else None


def fetch_symbol(symbol: str, start_ms: int, bar: str = DEFAULT_BAR) -> None:
    bar = normalize_bar(bar)
    inst_id = symbol.replace("_", "-")
    part = FETCH_DIR / f"{symbol}_{bar}.part.csv"
    rows: list[list] = []
    oldest_ms: int | None = None
    if part.exists():  # resume: reload progress, continue from oldest ts
        with part.open() as fh:
            rows = [r for r in csv.reader(fh)][1:]
        if rows:
            oldest_ms = min(int(r[0]) for r in rows)
            print(f"  {symbol}: resuming at {datetime.fromtimestamp(oldest_ms / 1e3, tz=timezone.utc):%Y-%m-%d}", flush=True)

    header = ["ts", "open", "high", "low", "close", "volume", "open_time"]
    if not part.exists():
        part.write_text(",".join(header) + "\n")

    while oldest_ms is None or oldest_ms > start_ms:
        url = f"{API}?instId={inst_id}&bar={bar}&limit={PAGE_LIMIT}"
        if oldest_ms is not None:
            url += f"&after={oldest_ms}"
        payload = _request(url)
        if payload.get("code") != "0":
            print(f"  {symbol}: API error: {payload.get('msg')} -- skipping", flush=True)
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

    if not rows:
        part.unlink(missing_ok=True)
        print(f"  {symbol}: no data", flush=True)
        return
    # dedupe + sort, write final file named to match the loader's pattern
    uniq = {int(r[0]): r for r in rows}
    final_rows = [uniq[k] for k in sorted(uniq)]
    out = FETCH_DIR / f"okx_{symbol}_{bar}_{len(final_rows)}.csv"
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(final_rows)
    part.unlink(missing_ok=True)
    first = datetime.fromtimestamp(sorted(uniq)[0] / 1e3, tz=timezone.utc)
    print(f"  {symbol}: done, {len(final_rows)} bars from {first:%Y-%m-%d} -> {out.name}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--bar", default=DEFAULT_BAR, choices=BAR_CHOICES,
                        help="candle timeframe (filenames and API both follow it)")
    args = parser.parse_args()
    bar = normalize_bar(args.bar)
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
    pending: list[str] = []
    for symbol in args.symbols:
        done = _finished_file(symbol, bar)
        if done is not None:
            print(f"{symbol}: already fetched ({done.name})", flush=True)
        else:
            pending.append(symbol)
    print(f"fetching {len(pending)} symbols with {args.workers} workers", flush=True)
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_symbol, s, start_ms, bar): s for s in pending}
        for n, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                future.result()
            except RuntimeError as exc:
                failed += 1
                print(f"  {symbol}: FAILED: {exc} (rerun to resume)", flush=True)
            print(f"[{n}/{len(pending)} finished]", flush=True)
    if failed:
        print(f"{failed} symbols failed -- rerun to resume them", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
