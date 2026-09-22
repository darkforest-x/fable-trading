"""Freeze a current OKX UTC+8-day gainers board and closed native OHLCV.

Owner requested today's top20 USDT perpetual gainers for A-model inspection.
Ranking reads public tickers last/sodUtc8 only; this current selection is
retrospective for earlier same-day detections, not a tradable historical rank.
Features may read confirmed OHLCV at or before the frozen cutoff, with 1224
warmup bars before the day starts. No production cache or private API is used.
API: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "experiments/active/exp-ma-morphology-top20-20260922-v1/inputs"
PERIODS = {15: "15m", 30: "30m", 60: "1H"}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PublicClient:
    def __init__(self):
        self.local = threading.local()
        self.lock = threading.Lock()
        self.next_request = 0.0

    def get(self, path, params):
        if not path.startswith(("/api/v5/public/", "/api/v5/market/")):
            raise ValueError("public reads only")
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
        for attempt in range(3):
            with self.lock:
                delay = max(0.0, self.next_request - time.monotonic())
                self.next_request = max(self.next_request, time.monotonic()) + .25
            if delay:
                time.sleep(delay)
            try:
                response = self.local.session.get("https://www.okx.com" + path, params=params, timeout=(6, 25))
                response.raise_for_status()
                body = response.json()
                if body.get("code") != "0" or not isinstance(body.get("data"), list):
                    raise ValueError("bad OKX response: " + str(body)[:250])
                return body
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)


def rank_board(tickers, instruments, cutoff_ms):
    """Current live USDT SWAP board, using last / UTC+8-day open minus one."""
    live = {r["instId"] for r in instruments if r.get("state") == "live" and r.get("settleCcy") == "USDT" and r.get("instType") == "SWAP"}
    ranked, excluded = [], []
    for item in tickers:
        symbol = item["instId"]
        if symbol not in live or not symbol.endswith("-USDT-SWAP"):
            continue
        try:
            last, opening, rolling = (float(item[k]) for k in ("last", "sodUtc8", "open24h"))
            stamp = int(item["ts"])
            if not all(math.isfinite(x) and x > 0 for x in (last, opening, rolling)):
                raise ValueError("missing/invalid opening price")
            if not -10000 <= cutoff_ms - stamp <= 300000:
                raise ValueError("stale/future ticker")
            ranked.append({"symbol": symbol, "last": last, "sodUtc8": opening,
                           "change_today_pct": (last / opening - 1) * 100,
                           "change_24h_pct": (last / rolling - 1) * 100,
                           "ticker_ts": stamp})
        except (KeyError, ValueError, TypeError) as exc:
            excluded.append({"symbol": symbol, "reason": str(exc)})
    ranked.sort(key=lambda r: (-r["change_today_pct"], r["symbol"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    selected = [r for r in ranked if r["change_today_pct"] > 0][:20]
    if len(selected) != 20:
        raise ValueError("fewer than 20 valid positive gainers")
    return selected, ranked, excluded


def fetch_stream(client, out, symbol, minutes, start_ms, cutoff_ms):
    """Page only through the frozen close cutoff; never fabricate missing bars."""
    period = minutes * 60000
    end = cutoff_ms // period * period
    earliest = start_ms - 1224 * period
    cursor, values, pages = end, {}, []
    for page in range(12):
        params = {"instId": symbol, "bar": PERIODS[minutes], "after": str(cursor),
                  "before": str(earliest - 1), "limit": "300"}
        payload = client.get("/api/v5/market/history-candles", params)
        raw = out / "raw" / f"{symbol}_{minutes}m_{page:02d}.json"
        save(raw, {"params": params, "payload": payload})
        pages.append({"path": str(raw.relative_to(ROOT)), "sha256": sha(raw)})
        rows = payload["data"]
        if not rows:
            break
        oldest = min(int(r[0]) for r in rows)
        if oldest >= cursor:
            raise ValueError("nonprogressing candle pagination")
        for item in rows:
            stamp = int(item[0])
            if stamp < earliest or stamp + period > end:
                continue
            if len(item) < 9 or str(item[8]) != "1" or stamp % period:
                raise ValueError("unconfirmed/off-grid candle")
            numbers = [float(v) for v in item[1:6]]
            o, h, l, c, v = numbers
            if not all(math.isfinite(x) for x in numbers) or min(o,h,l,c) <= 0 or v < 0 or h < max(o,l,c) or l > min(o,h,c):
                raise ValueError("invalid OHLCV")
            value = [stamp, *numbers]
            if stamp in values and values[stamp] != value:
                raise ValueError("conflicting duplicate candle")
            values[stamp] = value
        cursor = oldest
        if cursor <= earliest or len(rows) < 300:
            break
    frame = pd.DataFrame([values[k] for k in sorted(values)], columns=["ts","open","high","low","close","volume"])
    if frame.empty:
        raise ValueError("empty history")
    frame.insert(1, "open_time", pd.to_datetime(frame.ts, unit="ms", utc=True))
    dest = out / "ohlcv" / f"{symbol}_{minutes}m.csv"
    dest.parent.mkdir(exist_ok=True)
    frame.to_csv(dest, index=False)
    return {"symbol": symbol, "minutes": minutes, "status": "ok", "rows": len(frame),
            "path": str(dest.relative_to(ROOT)), "sha256": sha(dest), "pages": pages,
            "missing_intervals": int((frame.ts.diff().dropna() != period).sum()),
            "first_ms": int(frame.ts.iloc[0]), "last_close_ms": int(frame.ts.iloc[-1]) + period,
            "warmup_before_day": int((frame.ts < start_ms).sum()),
            "requested_first_ms": earliest, "cutoff_ms": cutoff_ms}


def run(out):
    if out.exists():
        raise FileExistsError("refusing to overwrite frozen inputs: " + str(out))
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ValueError("main required")
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    subprocess.run(["git", "cat-file", "-e", "HEAD:" + relative], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", relative], cwd=ROOT, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    out.mkdir(parents=True)
    client = PublicClient()
    instruments = client.get("/api/v5/public/instruments", {"instType": "SWAP"})
    tickers = client.get("/api/v5/market/tickers", {"instType": "SWAP"})
    clock = client.get("/api/v5/public/time", {})
    cutoff = int(clock["data"][0]["ts"])
    if abs(cutoff - int(time.time()*1000)) > 120000:
        raise ValueError("exchange/local time mismatch")
    current = pd.Timestamp(cutoff, unit="ms", tz="UTC")
    day = current.tz_convert("Asia/Shanghai").normalize().tz_convert("UTC")
    selected, all_ranked, excluded = rank_board(tickers["data"], instruments["data"], cutoff)
    for name, content in (("instruments", instruments), ("tickers", tickers), ("clock", clock)):
        save(out / "raw" / f"{name}.json", content)
    ranking = {"snapshot_utc": current.isoformat(), "snapshot_beijing": current.tz_convert("Asia/Shanghai").isoformat(),
               "day_start_utc": day.isoformat(), "cutoff_ms": cutoff, "ranked": selected,
               "universe_count": len(all_ranked), "ranking_basis": "last/sodUtc8-1; current retrospective board",
               "excluded": excluded, "source_commit": commit, "source_sha256": sha(__file__)}
    save(out / "ranking.json", ranking)
    save(out / "all_ranked.json", all_ranked)
    coverage = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_stream, client, out, r["symbol"], minutes, int(day.timestamp()*1000), cutoff): (r["symbol"], minutes)
                   for r in selected for minutes in PERIODS}
        for future in as_completed(futures):
            symbol, minutes = futures[future]
            try:
                coverage.append(future.result())
            except Exception as exc:
                coverage.append({"symbol": symbol, "minutes": minutes, "status": "error", "error": repr(exc)})
            coverage.sort(key=lambda r: (r["symbol"], r["minutes"]))
            save(out / "coverage.json", coverage)
            print(json.dumps({"done":len(coverage),"total":60,"errors":sum(r["status"]=="error" for r in coverage)}), flush=True)
    save(out / "receipt.json", {"status":"completed", "source_commit":commit, "ranking_sha256":sha(out/"ranking.json"),
                               "coverage_sha256":sha(out/"coverage.json"), "streams":len(coverage),
                               "errors":sum(r["status"]=="error" for r in coverage), "production_eligible":False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    run(args.out.resolve())
