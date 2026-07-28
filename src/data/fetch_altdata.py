"""Accumulate OKX alternative data, because its history cannot be bought back.

The judgment layer has run out of road on price-derived features: 28 production
features plus the 19 causal alphas leave the top-decile lift indistinguishable
from a model trained on shuffled targets (permutation p=0.32). The obvious next
move is a different kind of information -- funding, open interest, taker flow,
positioning -- and that move is blocked by a fact worth stating plainly:

    funding rate            ~3 months of public history
    open interest           ~30 days
    taker buy/sell volume   ~30 days
    long/short account ratio ~30 days
    judgment training pool   2025-11-04 .. 2026-05-03

None of it reaches back far enough to backtest against the pool. The data is not
missing from our disk; it does not exist to be fetched. So it cannot be evaluated
today at any effort, and the only thing that changes that is elapsed time with a
collector running.

This is that collector. It appends rather than overwrites, so each run extends
coverage and a gap costs only the window that was missed. Run it daily; in about
three months the earliest rows reach far enough back to test a hypothesis that
today can only be asserted.

Every series is stored at its native resolution with the raw exchange timestamp,
no forward-filling and no resampling -- an alignment choice made now would be
baked into every future experiment, and the right one depends on the question.

Read-only against the exchange, public endpoints, no key required.

Usage:
  python3 -m src.data.fetch_altdata                       # pool symbols
  python3 -m src.data.fetch_altdata --symbols BTC_USDT_SWAP ETH_USDT_SWAP
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT / "data" / "altdata"
POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
BASE = "https://www.okx.com"
# the rubik endpoints 403 without a browser UA even though the market ones do not
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
           "Accept": "application/json"}
THROTTLE_S = 0.25
MAX_RETRIES = 3

# name -> (path, params template, timestamp column, value columns)
FEEDS = {
    "funding": ("/api/v5/public/funding-rate-history",
                "instId={inst}&limit=100", 0, ["fundingRate", "realizedRate"]),
    "open_interest": ("/api/v5/rubik/stat/contracts/open-interest-volume",
                      "ccy={ccy}&period=1H", 0, ["oi", "vol"]),
    "taker_volume": ("/api/v5/rubik/stat/taker-volume",
                     "ccy={ccy}&instType=CONTRACTS&period=1H", 0,
                     ["sellVol", "buyVol"]),
    "long_short_ratio": ("/api/v5/rubik/stat/contracts/long-short-account-ratio",
                         "ccy={ccy}&period=1H", 0, ["ratio"]),
}


def _get(url: str) -> dict:
    for attempt in range(MAX_RETRIES):
        time.sleep(THROTTLE_S)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            if attempt == MAX_RETRIES - 1:
                return {"err": str(exc)[:120]}
            time.sleep(2 ** attempt)
    return {"err": "unreachable"}


def _inst_id(symbol: str) -> str:
    """ATH_USDT_SWAP -> ATH-USDT-SWAP."""
    return symbol.replace("_", "-")


def _ccy(symbol: str) -> str:
    return symbol.split("_")[0]


def _merge(path: Path, rows: list[list[str]], header: list[str]) -> int:
    """Append only timestamps not already present. Returns rows added."""
    existing: set[str] = set()
    if path.exists():
        with path.open(newline="") as fh:
            r = csv.reader(fh)
            next(r, None)
            existing = {row[0] for row in r if row}
    fresh = [row for row in rows if row[0] not in existing]
    if not fresh:
        return 0
    write_header = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.writer(fh)
        if write_header:
            w.writerow(header)
        w.writerows(sorted(fresh, key=lambda r: int(r[0])))
    return len(fresh)


def fetch_feed(symbol: str, feed: str) -> tuple[int, str]:
    endpoint, tmpl, ts_i, cols = FEEDS[feed]
    q = tmpl.format(inst=_inst_id(symbol), ccy=_ccy(symbol))
    payload = _get(f"{BASE}{endpoint}?{q}")
    if "err" in payload:
        return 0, payload["err"]
    data = payload.get("data") or []
    if not data:
        return 0, payload.get("msg") or "empty"

    rows: list[list[str]] = []
    if isinstance(data[0], dict):                       # funding returns objects
        for r in data:
            ts = r.get("fundingTime") or r.get("ts")
            if ts:
                rows.append([str(ts)] + [str(r.get(c, "")) for c in cols])
    else:                                               # rubik returns arrays
        for r in data:
            rows.append([str(r[ts_i])] + [str(v) for v in r[1:1 + len(cols)]])
    out = OUT_DIR / feed / f"{symbol}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    return _merge(out, rows, ["ts_ms"] + cols), ""


def pool_symbols() -> list[str]:
    if not POOL.exists():
        return ["BTC_USDT_SWAP", "ETH_USDT_SWAP"]
    import pandas as pd
    return sorted(pd.read_csv(POOL, usecols=["symbol"])["symbol"].unique())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--feeds", nargs="*", default=sorted(FEEDS))
    args = ap.parse_args()

    syms = args.symbols or pool_symbols()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC  "
          f"{len(syms)} 币 x {len(args.feeds)} 数据源", flush=True)

    totals = {f: 0 for f in args.feeds}
    errs: dict[str, int] = {}
    for n, sym in enumerate(syms, 1):
        got = []
        for feed in args.feeds:
            added, err = fetch_feed(sym, feed)
            totals[feed] += added
            if err:
                errs[err] = errs.get(err, 0) + 1
            got.append(f"{feed[:4]}+{added}")
        if n % 10 == 0 or n == len(syms):
            print(f"  [{n}/{len(syms)}] {sym:<20} {' '.join(got)}", flush=True)

    print("\n新增行数:")
    for feed, n in totals.items():
        cov = len(list((OUT_DIR / feed).glob("*.csv"))) if (OUT_DIR / feed).exists() else 0
        print(f"  {feed:<18} +{n:<7} 覆盖 {cov} 币")
    if errs:
        print("\n错误(按出现次数):")
        for e, n in sorted(errs.items(), key=lambda kv: -kv[1])[:5]:
            print(f"  x{n}  {e}")
    print("\n注:另类数据的历史深度由交易所决定(资金费率 ~3 月,其余 ~30 天),"
          "\n    判断层训练池始于 2025-11 —— 现在只能开始积累,无法回补。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
