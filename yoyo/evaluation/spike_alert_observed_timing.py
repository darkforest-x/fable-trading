"""Count observed first touches before and after real market-wide alarms, without simulating fills.

Inputs are the monitor's append-only real alarm creation times and weekly live touches.
Warnings use only distinct same-side symbols seen so far in the production five-minute
candle window. Every trial threshold has an independent same-side 30-minute cooldown.
Outcomes use the actual server creation clock, never a rounded candle or a push receipt.
"""
import json
import sqlite3
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median


MINUTE = 60_000
ZONE = timezone(timedelta(hours=8))
START = datetime(2026, 10, 28, tzinfo=ZONE)
END = datetime(2026, 11, 7, 21, 50, tzinfo=ZONE)
RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"


def rows(book, sql, args):
    with closing(sqlite3.connect((RUNTIME / book).as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def replay_warnings(touches, threshold):
    recent = {side: deque() for side in ("upper", "lower")}
    last = {side: -10**15 for side in recent}
    warnings = []
    for row in touches:
        side, candle = row["side"], row["candle_ms"]
        window = recent[side]
        window.append((candle, row["symbol"]))
        while window[0][0] <= candle - 5 * MINUTE:
            window.popleft()
        if candle - last[side] >= 30 * MINUTE and len({sym for _, sym in window}) >= threshold:
            warnings.append(row)
            last[side] = candle
    return warnings


def summarize():
    start, end = (int(t.timestamp() * 1000) for t in (START, END))
    touches = rows("monday-fib.sqlite3",
                   "SELECT side,symbol,candle_ms,observed_ms FROM touches WHERE source='live' "
                   "AND observed_ms>=? AND observed_ms<? ORDER BY observed_ms,rowid",
                   (start - 60 * MINUTE, end))
    alerts = rows("spike-alerts.sqlite3",
                  "SELECT id,side,n,symbols,last_ms,created_ms FROM alerts WHERE test=0 "
                  "AND created_ms>=? AND created_ms<? ORDER BY created_ms", (start, end))
    replay = [(r["side"], r["candle_ms"]) for r in replay_warnings(touches, 25)
              if start <= r["observed_ms"] < end]
    if replay != [(a["side"], a["last_ms"]) for a in alerts]:
        raise ValueError("25-symbol replay differs from the real alarm book")
    counts = []
    for alert in alerts:
        clock = alert["created_ms"]
        if clock + 30 * MINUTE > end:
            continue
        initial = set(json.loads(alert["symbols"]))
        if len(initial) != alert["n"]:
            raise ValueError("Trigger set size differs from the stored count")
        delays = {}
        for r in touches:
            delay = r["observed_ms"] - clock
            if r["side"] == alert["side"] and r["symbol"] not in initial and 0 < delay <= 30 * MINUTE:
                delays.setdefault(r["symbol"], delay)
        counts.append(dict(id=alert["id"], side=alert["side"], trigger=alert["n"],
                           created_bjt=datetime.fromtimestamp(clock / 1000, ZONE).isoformat(),
                           new_by_minutes={m: sum(d <= m * MINUTE for d in delays.values())
                                           for m in (1, 5, 15, 30)},
                           first_new_seconds=min(delays.values()) / 1000 if delays else None))
    trials = {}
    for threshold in (15, 20):
        eligible = [r for r in replay_warnings(touches, threshold)
                    if start <= r["observed_ms"] < end - 5 * MINUTE]
        hits = {}
        for r in eligible:
            for a in alerts:
                lead = a["created_ms"] - r["observed_ms"]
                if r["side"] == a["side"] and 0 < lead <= 5 * MINUTE:
                    hits[a["id"]] = round(lead / 1000, 3)
                    break
        trials[threshold] = dict(warnings=len(eligible), alerts_with_prior_warning=len(hits),
                                 warnings_without_alert_in_5m=len(eligible) - len(hits),
                                 lead_seconds_by_alert_id=hits,
                                 median_seconds_among_hits=median(hits.values()) if hits else None)
    return dict(start_bjt=START.isoformat(), end_bjt_exclusive=END.isoformat(), real_alarms=len(alerts),
                replay25_matches_all_real_side_and_candle_times=True, observations=counts,
                retrospective_early_warnings=trials, paper_or_live_orders_simulated=False)


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=2))
