"""Incremental OKX updater: extend every okx_{SYM}_15m_{N}.csv in
data/kline_fetched to the latest confirmed candle.

This is the forward-validation data feed (route D): run it daily (manually or
via a scheduler the owner approves) and the loader picks the fresh bars up
automatically -- filenames keep the {rows} suffix in sync.

Usage: python3 -m src.data.update_okx
Reuses fetch_okx's WAF-safe request + global throttle; a full daily update of
56 symbols is ~1-2 pages each, well under a minute.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from src.data.fetch_okx import API, BAR, FETCH_DIR, PAGE_LIMIT, _request

FILE_RE = re.compile(r"^okx_(?P<symbol>.+?)_15m_(?P<rows>\d+)\.csv$")


def update_file(path: Path) -> tuple[str, int]:
    symbol = FILE_RE.match(path.name).group("symbol")
    inst_id = symbol.replace("_", "-")
    with path.open() as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    last_ts = max(int(r[0]) for r in rows)

    new_rows: list[list] = []
    after: int | None = None  # page backward from now until we reach last_ts
    while True:
        url = f"{API}?instId={inst_id}&bar={BAR}&limit={PAGE_LIMIT}"
        if after is not None:
            url += f"&after={after}"
        payload = _request(url)
        if payload.get("code") != "0":
            print(f"  {symbol}: API error: {payload.get('msg')} -- skipped", flush=True)
            return symbol, 0
        page = payload.get("data") or []
        if not page:
            break
        for r in page:  # [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
            ts = int(r[0])
            if ts <= last_ts or (len(r) > 8 and r[8] == "0"):
                continue
            ot = datetime.fromtimestamp(ts / 1e3, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
            new_rows.append([ts, r[1], r[2], r[3], r[4], r[5], ot])
        oldest = int(page[-1][0])
        if oldest <= last_ts:
            break
        after = oldest

    if not new_rows:
        return symbol, 0
    merged = {int(r[0]): r for r in rows}
    merged.update({int(r[0]): r for r in new_rows})
    final = [merged[k] for k in sorted(merged)]
    out = FETCH_DIR / f"okx_{symbol}_{BAR}_{len(final)}.csv"
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(final)
    if out != path:
        path.unlink()
    return symbol, len(new_rows)


def main() -> int:
    files = sorted(p for p in FETCH_DIR.glob("okx_*_15m_*.csv") if FILE_RE.match(p.name))
    total = 0
    for n, path in enumerate(files, 1):
        symbol, added = update_file(path)
        total += added
        if added:
            print(f"[{n}/{len(files)}] {symbol}: +{added} bars", flush=True)
    print(f"done: {total} new bars across {len(files)} symbols", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
