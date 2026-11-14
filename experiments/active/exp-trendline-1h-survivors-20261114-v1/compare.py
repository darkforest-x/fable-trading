"""Measure price excursions of a fixed snapshot of the trendline monitor."""

import argparse
import csv
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import NamedTuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE = Path.home() / "Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3"
HOUR = 3_600_000
BEIJING = ZoneInfo("Asia/Shanghai")


class Bar(NamedTuple):
    open: float
    high: float
    low: float
    close: float
    volume: float


def pct(value, reference):
    return 100 * (value / reference - 1)


def break_features(bars, line):
    """Use signal OHLCV, its known 4H line, and the preceding 25 hourly OHLCV bars."""
    bar = bars[-1]
    prior = bars[-25:-1]
    atr = mean(
        max(current.high, previous.close) - min(current.low, previous.close)
        for previous, current in zip(bars[-26:-2], prior)
    )
    usual_volume = median(b.volume for b in prior)
    return {
        "body_pct": pct(bar.close, bar.open),
        "body_prior_atr": (bar.close - bar.open) / atr if atr else None,
        "prior_atr_pct": 100 * atr / prior[-1].close,
        "close_in_bar_pct": 100 * (bar.close - bar.low) / (bar.high - bar.low) if bar.high > bar.low else None,
        "volume_to_prior_median": bar.volume / usual_volume if usual_volume else None,
        "close_over_line_pct": pct(bar.close, line),
        "above_prior_12h_high": bar.close > max(b.high for b in prior[-12:]),
        "prior_24h_change_pct": pct(prior[-1].close, bars[-26].close),
    }


def outcomes(bars, entry):
    """All bars start at or after breakout confirmation and close by the snapshot cutoff."""
    first_day = bars[:24]
    peak = max(b.high for b in bars)
    day_peak = max(b.high for b in first_day)
    return {
        "observed_hours": len(bars),
        "peak_first_24h_observed_pct": pct(day_peak, entry),
        "day_peak_hour": next(i for i, b in enumerate(first_day, 1) if b.high == day_peak),
        "close_24h_pct": pct(bars[23].close, entry) if len(bars) >= 24 else None,
        "peak_to_cutoff_pct": pct(peak, entry),
        "peak_to_cutoff_hour": next(i for i, b in enumerate(bars, 1) if b.high == peak),
        "first_30pct_hour": next((i for i, b in enumerate(bars, 1) if b.high >= entry * 1.30), None),
        "close_at_cutoff_pct": pct(bars[-1].close, entry),
    }


def measure(db, event, cutoff):
    signal = event["candle_ms"]
    assert event["confirmed_ms"] == signal + HOUR
    start = signal - 25 * HOUR
    key = f"https://www.okx.com|{event['symbol']}|1H"
    rows = db.execute("SELECT t, raw FROM candles WHERE key=? AND t>=? AND t<? ORDER BY t", (key, start, cutoff))
    candles = {}
    for timestamp, raw in rows:
        row = json.loads(raw)
        if str(row[8]) == "1":
            assert int(row[0]) == timestamp
            candles[timestamp] = Bar(*map(float, row[1:6]))
    bars = [candles[t] for t in range(start, cutoff, HOUR)]
    history, future = bars[:26], bars[26:]
    assert math.isclose(history[-1].close, event["close"], rel_tol=1e-12)
    assert min(b.low for b in history[-11:]) == event["prebreak_low"]
    assert all(b.low >= event["prebreak_low"] for b in future)
    return {
        "symbol": event["symbol"],
        "confirmed_beijing": datetime.fromtimestamp(event["confirmed_ms"] / 1000, BEIJING).isoformat(),
        "event_id": event["event_id"],
        "signal_close": event["close"],
        **break_features(history, event["level"]),
        **outcomes(future, event["close"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    snapshot = json.loads((HERE / "cohort.json").read_text())
    db = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = [measure(db, event, snapshot["asof_ms"]) for event in snapshot["events"]]
    finally:
        db.close()
    with (HERE / "events.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
