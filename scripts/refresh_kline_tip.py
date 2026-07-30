#!/usr/bin/env python3
"""Incrementally refresh tip bars for existing data/kline_fetched/*_15m_*.csv.

The full history fetcher (`python3 -m src.data.fetch_okx`) **skips** finished
symbols, so live paper scans can silently run on multi-day-stale tips. This
script only pulls recent confirmed candles and merges them into the finished
files (atomic rename, new row-count suffix).

Usage:
  PYTHONPATH=. python3 scripts/refresh_kline_tip.py              # all SWAP 15m
  PYTHONPATH=. python3 scripts/refresh_kline_tip.py --limit 50
  PYTHONPATH=. python3 scripts/refresh_kline_tip.py --symbols ETH_USDT_SWAP BTC_USDT_SWAP
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.bars import normalize_bar  # noqa: E402

FETCH_DIR = PROJECT / "data" / "kline_fetched"
# Recent candles endpoint (newest first); enough for multi-day catch-up.
API = "https://www.okx.com/api/v5/market/candles"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
PAGE = 100
MIN_INTERVAL_S = 0.12
_lock = threading.Lock()
_last = 0.0


def _throttle() -> None:
    global _last
    with _lock:
        now = time.monotonic()
        wait = MIN_INTERVAL_S - (now - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def _get(url: str) -> dict:
    _throttle()
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _finished_file(symbol: str, bar: str) -> Path | None:
    hits = sorted(FETCH_DIR.glob(f"okx_{symbol}_{bar}_*.csv"))
    return hits[-1] if hits else None


def _read_csv(path: Path) -> tuple[list[str], dict[int, list]]:
    with path.open(newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return ["ts", "open", "high", "low", "close", "volume", "open_time"], {}
    header = rows[0]
    by_ts: dict[int, list] = {}
    for r in rows[1:]:
        if not r:
            continue
        by_ts[int(r[0])] = r
    return header, by_ts


def _pull_recent(symbol: str, bar: str, after_ts_ms: int, max_pages: int = 40) -> list[list]:
    """Return rows newer than after_ts_ms (confirmed only), oldest→newest."""
    inst = symbol.replace("_", "-")
    collected: list[list] = []
    before: int | None = None  # paginate toward older among the *recent* window
    for _ in range(max_pages):
        url = f"{API}?instId={inst}&bar={bar}&limit={PAGE}"
        if before is not None:
            url += f"&after={before}"
        payload = _get(url)
        if payload.get("code") != "0":
            raise RuntimeError(payload.get("msg") or str(payload))
        page = payload.get("data") or []
        if not page:
            break
        page_rows: list[list] = []
        for r in page:
            ts = int(r[0])
            if ts <= after_ts_ms:
                continue
            if len(r) > 8 and str(r[8]) == "0":
                continue  # unconfirmed
            ot = datetime.fromtimestamp(ts / 1e3, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S+00:00"
            )
            page_rows.append([str(ts), r[1], r[2], r[3], r[4], r[5], ot])
        collected.extend(page_rows)
        oldest_on_page = int(page[-1][0])
        if oldest_on_page <= after_ts_ms:
            break
        before = oldest_on_page
        if len(page) < PAGE:
            break
    # dedupe
    uniq = {int(r[0]): r for r in collected}
    return [uniq[k] for k in sorted(uniq)]


def refresh_one(symbol: str, bar: str) -> tuple[str, int, str]:
    path = _finished_file(symbol, bar)
    if path is None:
        return symbol, 0, "no finished file"
    header, by_ts = _read_csv(path)
    if not by_ts:
        return symbol, 0, "empty file"
    last_ts = max(by_ts)
    new_rows = _pull_recent(symbol, bar, last_ts)
    if not new_rows:
        last_ot = by_ts[last_ts][6] if len(by_ts[last_ts]) > 6 else str(last_ts)
        return symbol, 0, f"up-to-date tip={last_ot}"
    for r in new_rows:
        by_ts[int(r[0])] = r
    final = [by_ts[k] for k in sorted(by_ts)]
    out = FETCH_DIR / f"okx_{symbol}_{bar}_{len(final)}.csv"
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(final)
    tmp.replace(out)
    if path.resolve() != out.resolve() and path.exists():
        path.unlink(missing_ok=True)
    tip = final[-1][6] if len(final[-1]) > 6 else final[-1][0]
    return symbol, len(new_rows), f"+{len(new_rows)} → tip={tip} ({out.name})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bar", default="15m")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0, help="only first N symbols (debug)")
    ap.add_argument("--swap-only", action="store_true", default=True)
    args = ap.parse_args()
    bar = normalize_bar(args.bar)
    FETCH_DIR.mkdir(parents=True, exist_ok=True)

    if args.symbols:
        symbols = list(args.symbols)
    else:
        symbols = sorted(
            {
                p.name.split("_")[1] + "_" + "_".join(p.name.split("_")[2:]).rsplit(f"_{bar}_", 1)[0]
                # filenames: okx_{SYMBOL}_{bar}_{n}.csv — SYMBOL may contain underscores
                for p in FETCH_DIR.glob(f"okx_*_{bar}_*.csv")
            }
        )
        # robust parse: strip okx_ prefix and _{bar}_{n}.csv suffix
        symbols = []
        for p in FETCH_DIR.glob(f"okx_*_{bar}_*.csv"):
            name = p.name
            if not name.startswith("okx_") or f"_{bar}_" not in name:
                continue
            core = name[len("okx_") :]
            sym = core.rsplit(f"_{bar}_", 1)[0]
            if args.swap_only and not sym.endswith("_USDT_SWAP"):
                continue
            symbols.append(sym)
        symbols = sorted(set(symbols))
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"refresh tip: {len(symbols)} symbols bar={bar} workers={args.workers}", flush=True)
    ok = fail = added = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(refresh_one, s, bar): s for s in symbols}
        for i, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                s, n, msg = fut.result()
                added += n
                ok += 1
                print(f"[{i}/{len(symbols)}] {s}: {msg}", flush=True)
            except Exception as exc:  # noqa: BLE001
                fail += 1
                print(f"[{i}/{len(symbols)}] {sym}: FAIL {exc}", flush=True)
    print(f"done: ok={ok} fail={fail} new_bars={added}", flush=True)
    return 1 if fail and ok == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
