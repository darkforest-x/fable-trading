"""Fetch OKX USDT perpetual 4H candles for the 4H trendline breakout census.

Reuses src.data.fetch_okx (throttle, resume, confirmed-candle filter) and only
widens its bar whitelist to OKX's native "4H". OKX 4H bars align to UTC
00/04/08/12/16/20, the same grid TradingView shows for OKX:*USDT.P.
Universe: every live USDT-settled SWAP listed on 2026-11-09 (survivorship not
removed: delisted contracts are absent).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data import fetch_okx  # noqa: E402

OUT = ROOT / "data/crypto/market/okx_swap_4h_20261109"
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def main() -> int:
    fetch_okx.FETCH_DIR = OUT
    fetch_okx.normalize_bar = lambda bar: bar  # module-level name used by fetch_symbol
    OUT.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://www.okx.com/api/v5/public/instruments?instType=SWAP",
                                 headers=fetch_okx.REQUEST_HEADERS)
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]
    symbols = sorted(x["instId"].replace("-", "_") for x in data
                     if x["settleCcy"] == "USDT" and x["state"] == "live")
    (OUT / "universe.json").write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
                                                    "symbols": symbols}, indent=1))
    todo = [s for s in symbols if not list(OUT.glob(f"okx_{s}_4H_*.csv"))]
    print(f"{len(symbols)} symbols, {len(todo)} to fetch", flush=True)
    start_ms = int(START.timestamp() * 1000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_okx.fetch_symbol, s, start_ms, "4H"): s for s in todo}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:  # keep going; report at the end
                print(f"  {futures[fut]}: FAILED {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
