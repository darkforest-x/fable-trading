"""Second 1H pass for variant 2 (1H close across a live 4H line with a bullish 1H cloud).

Every V1.2 line in lines.csv is scanned over its whole life (born close to end
close), not only lines that later broke on 4H, so the needed window per line
is [born_close - 300h, end_close + 480h]. Fetches only ranges missing from the
first pass and merges them into the same per-symbol CSV.
"""
from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
import fetch_1h_windows as f1  # noqa: E402
from src.data import fetch_okx  # noqa: E402

H1 = 3600_000


def request(url: str) -> dict:
    """fetch_okx._request plus retries on dropped connections (RemoteDisconnected, IncompleteRead)."""
    import json
    import time
    import urllib.request
    for attempt in range(8):
        fetch_okx._throttle()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=fetch_okx.REQUEST_HEADERS), timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as exc:  # network layer only; API errors come back as JSON
            time.sleep(min(60, 2 ** attempt))
            last = exc
    raise RuntimeError(f"giving up: {last}")


def merge(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + H1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def run(symbol: str, need: list[list[int]]) -> str:
    path = f1.OUT / f"okx_{symbol}_1H.csv"
    have = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
    got = set(have.ts.astype("int64")) if len(have) else set()
    gaps = []
    for a, b in need:
        a -= a % H1
        missing = [t for t in range(a, b + 1, H1) if t not in got]
        if missing:
            gaps.extend(merge([[t, t] for t in missing]))
    gaps = merge(gaps)
    if not gaps:
        return f"{symbol}: covered"
    rows = {int(r.ts): [int(r.ts), r.open, r.high, r.low, r.close] for r in have.itertuples()}
    inst = symbol.replace("_", "-")
    for start, end in gaps:
        after = end + H1
        while after > start:
            payload = request(f"{fetch_okx.API}?instId={inst}&bar=1H&limit=100&after={after}")
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
    tmp.replace(path)
    return f"{symbol}: +{len(gaps)} gaps, {len(rows)} bars"


def main() -> int:
    ln = pd.read_csv(HERE / "lines.csv")
    tr = pd.read_csv(HERE / "trades.csv")
    win = pd.concat([
        tr[["symbol", "win_start_ms", "win_end_ms"]],
        pd.DataFrame({"symbol": ln.symbol, "win_start_ms": ln.born_close - 300 * H1,
                      "win_end_ms": ln.end_close + 480 * H1}),
    ])
    jobs = {s: merge(list(zip(g.win_start_ms, g.win_end_ms))) for s, g in win.groupby("symbol")}
    print(f"{len(jobs)} symbols", flush=True)
    with ThreadPoolExecutor(max_workers=24) as pool:
        futs = {pool.submit(run, s, b): s for s, b in jobs.items()}
        for fut in as_completed(futs):
            try:
                print("  " + fut.result(), flush=True)
            except Exception as exc:
                print(f"  {futs[fut]}: FAILED {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
