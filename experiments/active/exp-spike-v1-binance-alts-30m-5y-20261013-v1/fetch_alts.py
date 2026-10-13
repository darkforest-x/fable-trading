"""Universe ranking and five years of Binance USD-M 30m archives for the SPIKE V1 alt study.

1. List every symbol prefix under data.binance.vision futures/um/monthly/klines (S3 listing, paginated;
   delisted symbols stay in the archive), keep USDT-quoted perpetual names minus config exclude.
2. Download each candidate's rank_month 30m archive (checksum verified, yoyo.data.binance_um_archives);
   404 = not listed then. Rank by median daily quote volume; keep the top universe_size.
3. Fetch 30m archive_start..archive_end_inclusive for those symbols with binance_um_archives.fetch_symbol.

Receipts (listing, ranking, per-symbol audits) land in config data_dir (not in git).

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1-binance-alts-30m-5y-20261013-v1/fetch_alts.py
"""
from __future__ import annotations

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from yoyo.data import binance_um_archives as bua

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
OUT = Path(CFG["data_dir"])
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/"


def list_symbols() -> list[str]:
    names, marker = [], ""
    while True:
        url = LISTING + (f"&marker={marker}" if marker else "")
        body = urllib.request.urlopen(urllib.request.Request(url, headers=bua.REQUEST_HEADERS), timeout=60).read().decode()
        names += re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", body)
        nxt = re.search(r"<NextMarker>([^<]+)</NextMarker>", body)
        if "<IsTruncated>true</IsTruncated>" not in body or not nxt:
            break
        marker = nxt.group(1)
    return sorted(set(names))


def rank_one(symbol: str) -> dict:
    frame, audit = bua._download_month(symbol=symbol, month=CFG["rank_month"], download_dir=OUT / "rank_downloads",
                                       interval=CFG["interval"])
    if frame is None:
        return {"symbol": symbol, "status": audit["status"]}
    day = frame.open_time.dt.floor("D")
    daily = (frame.close * frame.volume).groupby(day).sum()  # the archive reader keeps OHLCV only; close x volume as quote volume
    return {"symbol": symbol, "status": "complete", "days": int(len(daily)), "median_daily_quote_volume": float(daily.median())}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    listed = list_symbols()
    exclude = set(CFG["exclude"])
    candidates = [s for s in listed if s.endswith("USDT") and "_" not in s and s not in exclude]
    print("listed", len(listed), "candidates", len(candidates), flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        ranked = list(pool.map(rank_one, candidates))
    table = pd.DataFrame(ranked)
    have = table.loc[table.status.eq("complete")].sort_values("median_daily_quote_volume", ascending=False)
    universe = have.symbol.head(CFG["universe_size"]).tolist()
    (OUT / "ranking.json").write_text(json.dumps({"rank_month": CFG["rank_month"], "listed": len(listed),
                                                   "candidates": len(candidates), "with_rank_month_data": int(len(have)),
                                                   "universe": universe, "table": have.to_dict("records")}, indent=1))
    print("universe", universe, flush=True)
    rows = [{"symbol": s, "pair": s, "status": "ARCHIVE", "onboard_time": "2019-01-01T00:00:00+00:00"} for s in universe]

    def fetch(row):
        return bua.fetch_symbol(row, output_dir=OUT, archive_start=CFG["archive_start"],
                                archive_end_inclusive=CFG["archive_end_inclusive"],
                                archive_max_exclusive=CFG["archive_max_exclusive"], interval=CFG["interval"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        for got in pool.map(fetch, rows):
            print(got["symbol"], got["status"], got.get("rows"), got.get("first_time"), got.get("last_time"),
                  "missing", len(got["months_missing"]), flush=True)


if __name__ == "__main__":
    main()
