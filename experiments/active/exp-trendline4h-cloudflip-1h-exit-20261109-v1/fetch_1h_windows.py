"""Fetch OKX 1H candles for the merged per-symbol trade windows in trades.csv.

Reuses src.data.fetch_okx request throttling (<= 8.3 req/s shared). Confirmed
candles only. One CSV per symbol (ts = open time ms, UTC). Also stores each
instrument's tickSz for the Nautilus price grid. Resumable: finished symbols
are skipped.
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.data import fetch_okx  # noqa: E402

OUT = ROOT / "data/crypto/market/okx_swap_1h_windows_20261109"
H1 = 3600_000


def blocks(g: pd.DataFrame) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted(zip(g.win_start_ms, g.win_end_ms)):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def fetch(symbol: str, spans: list[list[int]]) -> str:
    path = OUT / f"okx_{symbol}_1H.csv"
    if path.exists():
        return f"{symbol}: cached"
    inst = symbol.replace("_", "-")
    rows: dict[int, list] = {}
    for start, end in spans:
        after = end + H1
        while after > start:
            url = f"{fetch_okx.API}?instId={inst}&bar=1H&limit=100&after={after}"
            payload = fetch_okx._request(url)
            if payload.get("code") != "0":
                raise RuntimeError(f"{symbol}: {payload.get('msg')}")
            page = payload.get("data") or []
            if not page:
                break
            for r in page:
                if len(r) > 8 and r[8] == "0":
                    continue
                rows[int(r[0])] = [int(r[0]), r[1], r[2], r[3], r[4]]
            after = int(page[-1][0])
    tmp = path.with_suffix(".part")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "open", "high", "low", "close"])
        w.writerows(rows[k] for k in sorted(rows))
    tmp.rename(path)
    return f"{symbol}: {len(rows)} bars in {len(spans)} blocks"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://www.okx.com/api/v5/public/instruments?instType=SWAP",
                                 headers=fetch_okx.REQUEST_HEADERS)
    inst = json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]
    ticks = {x["instId"].replace("-", "_"): x["tickSz"] for x in inst if x["settleCcy"] == "USDT"}
    (OUT / "ticks.json").write_text(json.dumps(ticks, indent=0))
    tr = pd.read_csv(HERE / "trades.csv")
    jobs = {s: blocks(g) for s, g in tr.groupby("symbol")}
    print(f"{len(jobs)} symbols", flush=True)
    with ThreadPoolExecutor(max_workers=40) as pool:
        futs = {pool.submit(fetch, s, b): s for s, b in jobs.items()}
        for fut in as_completed(futs):
            try:
                print("  " + fut.result(), flush=True)
            except Exception as exc:
                print(f"  {futs[fut]}: FAILED {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
