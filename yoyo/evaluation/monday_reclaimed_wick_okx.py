"""Prepare OKX touch-close events and continuous post-close minute paths.

Signals use only the recorded first weekly-side score and the completed touch
5m candle's OHLC. Prices after that close are solely outcome measurements.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import urlencode

from yoyo.data.candle_hub import validate

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-monday-reclaimed-wick-okx-20261115-v1"
CACHE = ROOT / "data/research/monday-reclaimed-wick-okx-20261115"
RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"
PUBLIC = "https://www.okx.com"
MIN = 60_000


def fetch(route, **params):
    url = f"{PUBLIC}/api/v5/{route}?{urlencode(params)}"
    data = subprocess.check_output(["curl", "-fsS", "--retry", "3", "--retry-all-errors", "--max-time", "20",
                                    "-A", "Mozilla/5.0", url])
    payload = json.loads(data)
    if payload.get("code") != "0":
        raise ValueError(f"{route}: {payload.get('msg')}")
    return payload["data"]


def read_db(name):
    return sqlite3.connect((RUNTIME / name).as_uri() + "?mode=ro", uri=True)


def candles(symbol, bar, start, end, cutoff):
    """Use local confirmed candles first; retrieve only missing minute ranges."""
    step = MIN if bar == "1m" else 5 * MIN
    key = f"{PUBLIC}|{symbol}|{bar}"
    with read_db("shared-candles/cache.sqlite3") as db:
        rows = [json.loads(raw) for (raw,) in db.execute(
            "SELECT raw FROM candles WHERE key=? AND t>=? AND t<?", (key, start, end))]
    found = validate(rows, bar, cutoff) if rows else {}
    found = {t: r for t, r in found.items() if r[8] == "1"}
    missing = [t for t in range(start, end, step) if t not in found]
    while missing:
        last = missing[-1]
        floor = max(start, last - 299 * step)
        page = fetch("market/history-candles", instId=symbol, bar=bar, after=last+step,
                     before=floor-1, limit=300)
        parsed = validate(page, bar, cutoff)
        found.update({t: r for t, r in parsed.items() if floor <= t <= last and r[8] == "1"})
        unresolved = [t for t in missing if t >= floor and t not in found]
        if unresolved:
            raise ValueError(f"Missing {bar} candles for {symbol} at {unresolved[:3]}")
        missing = [t for t in missing if t < floor]
    return [found[t] for t in range(start, end, step)]


def write_csv(path, rows):
    with (gzip.open(path, "wt") if path.suffix == ".gz" else path.open("w")) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prepare():
    cfg = json.loads((EXP / "config.json").read_text())
    cutoff = int(datetime.fromisoformat(cfg["cutoff_utc"]).timestamp() * 1000)
    CACHE.mkdir(parents=True, exist_ok=True)
    roster = EXP / "universe.json"
    with read_db("monday-reclaim-paper.sqlite3") as paper:
        paper.row_factory = sqlite3.Row
        signals = [dict(row) for row in paper.execute(
            """SELECT id,week,symbol,side,score,level,candle_ms,scored_ms FROM signals
               WHERE status='scored' AND score_basis=? AND score>? AND scored_ms<=?
               ORDER BY candle_ms,symbol""", (cfg["score_basis"], cfg["strict_score_above"], cutoff))]
    if not roster.exists():
        ids = {r["symbol"] for r in signals}
        instruments = fetch("public/instruments", instType="SWAP")
        subset = {r["instId"]: {k: r[k] for k in ("instCategory", "ruleType", "tickSz")}
                  for r in instruments if r["instId"] in ids}
        roster.write_text(json.dumps(subset, indent=2, sort_keys=True)+"\n")
    instruments = json.loads(roster.read_text())

    def prepare_event(row):
        symbol, side, level = row["symbol"], row["side"], row["level"]
        catalog = instruments[symbol]
        row["asset_class"] = catalog["instCategory"]
        if (catalog["instCategory"] != "1" or catalog["ruleType"] != "normal"
                or symbol in ("BTC-USDT-SWAP", "ETH-USDT-SWAP")):
            return dict(row, status="outside_universe")
        start = row["candle_ms"] // (5*MIN) * (5*MIN)
        entry_ms = start + 5*MIN
        b = candles(symbol, "5m", start, entry_ms, cutoff)[0]
        extreme, close = float(b[3] if side == 1 else b[2]), float(b[4])
        row.update(entry_ms=entry_ms, entry=close, stop=extreme, tick=float(catalog["tickSz"]))
        if side*(level-extreme) <= 0 or side*(close-level) <= 0:
            return dict(row, status="no_reclaimed_wick")
        window_end = min(cutoff, entry_ms + max(cfg["horizons_hours"])*60*MIN)
        file = CACHE / f"{symbol}-{entry_ms}-1m.csv.gz"
        if not file.exists():
            raw = candles(symbol, "1m", start, window_end, cutoff)
            bars = [dict(open_time=int(v[0]), open=v[1], high=v[2], low=v[3], close=v[4], volume=v[5])
                    for v in raw]
            if abs(float(bars[4]["close"])-close) > row["tick"]*0.001:
                raise ValueError(f"{symbol} minute/5m entry mismatch")
            tmp = file.with_suffix(".tmp.gz")
            write_csv(tmp, bars)
            tmp.replace(file)
        return dict(row, status="selected", observation_end_ms=window_end, path=str(file.relative_to(ROOT)))

    with ThreadPoolExecutor(max_workers=3) as pool:
        out = list(pool.map(prepare_event, signals))
    fields = dict.fromkeys(key for r in out for key in r)
    write_csv(EXP/"selection.csv", [{**fields, **r} for r in out])
    print({state: sum(r["status"] == state for r in out) for state in sorted({r["status"] for r in out})})


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    prepare()
