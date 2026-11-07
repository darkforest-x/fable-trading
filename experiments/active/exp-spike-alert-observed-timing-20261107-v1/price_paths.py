"""Extend the Nov 5 descriptive forward snapshot for real lower alerts 32 and 33.

A uses the logged 1.618 on strictly later one-minute touch candles. B uses the
next five-minute open after the alarm minute. Like the first five alerts, each
horizon reads the close of the five-minute candle containing start + horizon.
"""
import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

from src.data.fetch_okx import API, _request
from yoyo.evaluation.spike_alert_observed_timing import MINUTE, RUNTIME, rows


STEP = 5 * MINUTE
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CACHE = ROOT / "data/okx_swap_5m_alert_forward_20261107"


def cohorts():
    alerts = rows("spike-alerts.sqlite3",
                  "SELECT id,side,last_ms,symbols FROM alerts WHERE test=0 AND id IN (32, 33) ORDER BY id", ())
    if len(alerts) != 2 or any(a["side"] != "lower" for a in alerts):
        raise ValueError("Expected the two original downside alerts")
    result = []
    for a in alerts:
        t = a["last_ms"]
        later = rows("monday-fib.sqlite3",
                     "SELECT symbol,candle_ms,level FROM touches WHERE source='live' AND side=? "
                     "AND candle_ms>? AND candle_ms<=? ORDER BY candle_ms,symbol",
                     (a["side"], t, t + 30 * MINUTE))
        result += [(a["id"], "later", r["symbol"], r["candle_ms"], r["level"]) for r in later]
        start = ((t + MINUTE + STEP - 1) // STEP) * STEP
        result += [(a["id"], "trigger", sym, start, None) for sym in json.loads(a["symbols"])]
        result.append((a["id"], "benchmark", "BTC-USDT-SWAP", start, None))
    return result


def price_rows(symbol, end):
    dest = CACHE / f"{symbol}-{end}.json"
    if dest.is_file():
        return json.loads(dest.read_text())
    shared = RUNTIME / "shared-candles/cache.sqlite3"
    if shared.is_file():
        with closing(sqlite3.connect(shared.as_uri() + "?mode=ro", uri=True)) as db:
            source = db.execute("SELECT raw FROM candles WHERE key=? AND t>=? AND t<?",
                                (f"https://www.okx.com|{symbol}|5m", end - 100 * STEP, end))
            candles = [json.loads(raw) for raw, in source]
        cached = {r[0]: [r[1], r[4]] for r in candles if r[-1] == "1"}
        if len(cached) == 100:
            dest.write_text(json.dumps(cached))
            return cached
    query = urlencode(dict(instId=symbol, bar="5m", after=end, limit=100))
    payload = _request(API + "?" + query)
    if payload.get("code") != "0":
        raise ValueError(f"OKX error for {symbol}: {payload.get('code')}")
    prices = {r[0]: [r[1], r[4]] for r in payload["data"] if r[-1] == "1"}
    if prices:
        dest.write_text(json.dumps(prices))
    return prices


def main():
    events = cohorts()
    symbols = sorted({r[2] for r in events})
    end = (max(r[3] for r in events) // STEP + 74) * STEP
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(6) as pool:
        bars = dict(zip(symbols, pool.map(lambda sym: price_rows(sym, end), symbols)))
    output = []
    for alert, cohort, symbol, t, level in events:
        prices = bars[symbol]
        start = t // STEP * STEP
        entry = level if level is not None else float(prices[str(start)][0]) if str(start) in prices else None
        result = dict(alert=alert, cohort=cohort, symbol=symbol, start_ms=t, reference=entry)
        for minutes in (30, 360):
            candle = (t + minutes * MINUTE) // STEP * STEP
            close = float(prices[str(candle)][1]) if str(candle) in prices else None
            result[f"close_at_{minutes}m"] = close
            result[f"bp_{minutes}m"] = None if close is None or entry is None else (close / entry - 1) * 1e4
        output.append(result)
    with (HERE / "price_paths.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)


if __name__ == "__main__":
    main()
