"""Fetch Binance USD-M 15m klines for the 20-alt universe used by the H altcoin study.

Universe: the 20 alts of exp-spike-v129-alts-confirm-20261011-v1 (top 20 Binance USD-M alts by median
daily quote volume 2022-11-01..2022-12-31, stables/BTC/ETH excluded). The ranking window closes
2022-12-31, so build.py only counts trades from 2023-01-01 (earlier bars are indicator warmup).

Source: data.binance.vision monthly archives 2022-01..2026-09 plus daily archives 2026-10-01..2026-10-10
(the same end as the ETH/BTC/SOL files of the H studies). Every ZIP is checksum-verified and parsed by
yoyo.data.binance_um_archives (column, epoch, boundary and OHLC validation). Months before a listing
return 404 and are skipped. Output: data/vidya_retest/binance_um_{SYMBOL}_15m.csv with columns
open_time,open,high,low,close,volume,time, matching the existing H inputs. data/ is not in git.

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-vidya-h-altcoins-20261012-v1/fetch.py
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pandas as pd

from yoyo.data.binance_um_archives import (_download_month, _request_bytes, archive_months, parse_checksum,
                                           parse_month_zip, sha256_bytes)

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[2] / "data/vidya_retest"
DOWNLOADS = OUT / "downloads"
UNIVERSE = ["DOGEUSDT", "BNBUSDT", "XRPUSDT", "LTCUSDT", "SOLUSDT", "MASKUSDT", "CHZUSDT", "LINKUSDT", "APEUSDT", "ETCUSDT",
            "DYDXUSDT", "ADAUSDT", "APTUSDT", "NEARUSDT", "DOTUSDT", "AXSUSDT", "AVAXUSDT", "CRVUSDT", "GMTUSDT", "1000SHIBUSDT"]
SOURCE_CONFIG = HERE.parent / "exp-spike-v129-alts-confirm-20261011-v1/config.json"
MONTHS = archive_months("2022-01-01", "2026-09-30")
DAYS = [f"2026-10-{d:02d}" for d in range(1, 11)]
DAILY_BASE = "https://data.binance.vision/data/futures/um/daily/klines"


def download_day(symbol: str, day: str):
    """Daily archive with the same checksum and parsing rules as the monthly ones."""
    filename = f"{symbol}-15m-{day}.zip"
    url = f"{DAILY_BASE}/{symbol}/15m/{filename}"
    path = DOWNLOADS / symbol / filename
    checksum = _request_bytes(url + ".CHECKSUM")
    if checksum is None:
        return None
    expected = parse_checksum(checksum, expected_filename=filename)
    if path.exists() and sha256_bytes(path.read_bytes()) == expected:
        payload = path.read_bytes()
    else:
        payload = _request_bytes(url)
        if payload is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    frame, _ = parse_month_zip(payload, symbol=symbol, month=day, expected_sha256=expected, interval="15m")
    return frame


def fetch(symbol: str) -> dict:
    frames, missing = [], []
    for month in MONTHS:
        frame, _ = _download_month(symbol=symbol, month=month, download_dir=DOWNLOADS, interval="15m")
        (frames.append(frame) if frame is not None else missing.append(month))
    for day in DAYS:
        frame = download_day(symbol, day)
        (frames.append(frame) if frame is not None else missing.append(day))
    df = (pd.concat(frames, ignore_index=True).sort_values("ts").drop_duplicates("ts").reset_index(drop=True))
    out = pd.DataFrame({"open_time": df["ts"], "open": df["open"], "high": df["high"], "low": df["low"],
                        "close": df["close"], "volume": df["volume"], "time": df["open_time"]})
    path = OUT / f"binance_um_{symbol}_15m.csv"
    tmp = path.with_suffix(".csv.part")
    out.to_csv(tmp, index=False)
    tmp.replace(path)
    gaps = int((out["open_time"].diff().dropna() != 900_000).sum())
    return {"symbol": symbol, "rows": len(out), "first": str(out["time"].iloc[0]), "last": str(out["time"].iloc[-1]),
            "missing": missing, "gaps": gaps}


def main():
    if SOURCE_CONFIG.exists():
        expected = json.loads(SOURCE_CONFIG.read_text())["expected_universe"]
        if expected != UNIVERSE:
            raise SystemExit("universe differs from exp-spike-v129-alts-confirm-20261011-v1")
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=10) as pool:
        for r in pool.map(fetch, UNIVERSE):
            pre = [m for m in r["missing"] if m < r["first"][:7]]
            print(r["symbol"], r["rows"], r["first"][:10], r["last"][:16], "gaps", r["gaps"],
                  "missing before listing", len(pre), "other missing", [m for m in r["missing"] if m not in pre])


if __name__ == "__main__":
    main()
