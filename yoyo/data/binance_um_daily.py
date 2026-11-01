"""Fetch checksum-verified Binance USD-M daily kline archives (owner 2026-11-01, October 2026 backtest).

Owner: "补跑10月". The October 2026 monthly archives are not published yet on ``data.binance.vision``, so this reads the
daily archives with the same validation as binance_um_archives.parse_month_zip: SHA-256 against the official CHECKSUM,
one CSV member with the expected name, the 12 kline columns, a constant bar span, positive OHLC with the high / low
around the body, unique ascending bars inside the day. A missing day (HTTP 404: not listed yet or not published) is
recorded, not filled. Output per symbol: ts, open, high, low, close, volume (base volume, as the existing research
series), in its own research directory; never the VPS-owned live K-line cache.
"""
from __future__ import annotations

import argparse
import io
import json
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.binance_um_archives import (KLINE_COLUMNS, BinanceArchiveError, _normalise_epoch,
                                           interval_delta, parse_checksum, sha256_bytes)

DAILY_BASE = "https://data.binance.vision/data/futures/um/daily/klines"
_local = threading.local()


def _get(url, retries=5):
    """Keep-alive GET per thread (each new TLS connection costs about 3 s here); 404 -> None."""
    import requests
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = requests.Session()
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException as err:
            if attempt+1 == retries:
                raise BinanceArchiveError(f"request failed: {url}: {err}") from err
            time.sleep(2**attempt)


def daily_urls(symbol, day, interval):
    url = f"{DAILY_BASE}/{symbol}/{interval}/{symbol}-{interval}-{day}.zip"
    return url, url+".CHECKSUM"


def parse_day_zip(payload, *, symbol, day, expected_sha256, interval):
    if sha256_bytes(payload) != expected_sha256:
        raise BinanceArchiveError(f"checksum mismatch for {symbol} {day}")
    name = f"{symbol}-{interval}-{day}.csv"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [m for m in archive.namelist() if m.endswith(".csv")]
        if members != [name]:
            raise BinanceArchiveError(f"archive member drift for {symbol} {day}: {members}")
        frame = pd.read_csv(io.BytesIO(archive.read(name)), header=None, dtype=str)
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] != len(KLINE_COLUMNS):
        raise BinanceArchiveError(f"kline column drift for {symbol} {day}")
    frame.columns = list(KLINE_COLUMNS)
    open_ms, _ = _normalise_epoch(frame["open_time"], label="open_time")
    close_ms, _ = _normalise_epoch(frame["close_time"], label="close_time")
    if not bool(((close_ms-open_ms) == int(interval_delta(interval).total_seconds()*1000)-1).all()):
        raise BinanceArchiveError(f"non-{interval} close boundary for {symbol} {day}")
    out = pd.DataFrame({"ts": open_ms})
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = pd.to_numeric(frame[c], errors="raise").astype(float)
    o = out[["open", "high", "low", "close"]]
    if (not np.isfinite(out[["open", "high", "low", "close", "volume"]].to_numpy()).all() or (o <= 0).any().any()
            or (out.volume < 0).any() or (out.high < o[["open", "close"]].max(axis=1)).any()
            or (out.low > o[["open", "close"]].min(axis=1)).any()):
        raise BinanceArchiveError(f"invalid OHLCV for {symbol} {day}")
    start = pd.Timestamp(day, tz="UTC").value//1_000_000
    if out.ts.duplicated().any() or not out.ts.is_monotonic_increasing or out.ts.min() < start or out.ts.max() >= start+86_400_000:
        raise BinanceArchiveError(f"bars outside or unordered in {symbol} {day}")
    return out


def fetch_symbol(symbol, days, interval, out_dir):
    frames, missing, errors = [], [], []
    for day in days:
        url, checksum_url = daily_urls(symbol, day, interval)
        checksum = _get(checksum_url)
        if checksum is None:
            missing.append(day); continue
        payload = _get(url)
        if payload is None:
            missing.append(day); continue
        try:
            frames.append(parse_day_zip(payload, symbol=symbol, day=day,
                                        expected_sha256=parse_checksum(checksum, expected_filename=f"{symbol}-{interval}-{day}.zip"),
                                        interval=interval))
        except BinanceArchiveError as err:
            errors.append(f"{day}: {err}")
    rec = dict(symbol=symbol, days=len(frames), missing=missing, errors=errors, rows=0)
    if frames:
        f = pd.concat(frames, ignore_index=True).sort_values("ts")
        path = Path(out_dir)/"series"/f"{symbol}.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        f.to_csv(path, index=False)
        rec.update(rows=int(len(f)), path=str(path), first_ts=int(f.ts.iloc[0]), last_ts=int(f.ts.iloc[-1]))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=Path, required=True, help="JSON list of symbols")
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--interval", default="5m"); ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--threads", type=int, default=12)
    a = ap.parse_args()
    symbols = json.loads(a.symbols.read_text())
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(a.start, a.end, freq="D")]
    a.out.mkdir(parents=True, exist_ok=True)
    recs = []
    with ThreadPoolExecutor(a.threads) as ex:
        futs = {ex.submit(fetch_symbol, s, days, a.interval, a.out): s for s in symbols}
        for n, fu in enumerate(as_completed(futs), 1):
            recs.append(fu.result())
            if n % 50 == 0:
                print(json.dumps(dict(done=n, total=len(symbols))), flush=True)
    (a.out/"manifest.json").write_text(json.dumps(dict(start=a.start, end=a.end, interval=a.interval, source=DAILY_BASE,
                                                       streams=sorted(recs, key=lambda r: r["symbol"])), indent=1))
    print(json.dumps(dict(symbols=len(recs), with_rows=sum(r["rows"] > 0 for r in recs),
                          errors=sum(len(r["errors"]) for r in recs))))


if __name__ == "__main__":
    main()
