"""Reconcile a frozen owner watchlist with the monitor's chart and delivery journal.

Inputs are read-only JSON exports of /api/chart and SQLite receipts. Chart fields
are the live monitor's precomputed OHLC, md, sb, ATR, focus state and diagnostic
entry state; do not reseed moving averages from the 240-bar display tail. Only
bars closed by the supplied screenshot cutoff enter the diagnosis. Past setup
runs use preceding rows, while later prices are descriptive outcomes, never
features or proof of a profitable strategy. No inference, tuning or delivery.

Source contract: yoyo/monitor/signals.py and Pine V2.6 focusRelease/showMarks.
Owner authorized inspection of today's leaderboard and all historical dates.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
PERIODS = ("15m", "30m", "1H", "4H")


def bjt(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%m-%d %H:%M")


def verify_focus(rows):
    """Replay focus from an observed cleared state, independent of near-zero counts."""
    start = next((i for i, c in enumerate(rows) if not c["focus"] and c["near_zero_bars"] == 0), None)
    if start is None:
        return {"checked": 0, "mismatches": [], "reason": "no_cleared_state_in_tail"}
    run, qualified, band = 0, False, None
    failures = []
    for i in range(start + 1, len(rows)):
        c, previous = rows[i], rows[i - 1]
        release = None
        magnitude = max(abs(c["md"]), abs(c["sb"]))
        if qualified:
            if magnitude <= band:
                run += 1
            else:
                release = "long" if c["md"] > band else "short" if c["md"] < -band else None
                run, qualified, band = 0, False, None
        elif magnitude <= 0.1 * previous["atr"]:
            run += 1
            if run >= 12:
                qualified, band = True, 0.1 * previous["atr"]
        else:
            run = 0
        if (run, qualified, release) != (c["near_zero_bars"], c["focus"], c["tv_start_side"]):
            failures.append({"close_ms": c["bar_close_ms"], "expected": [run, qualified, release],
                             "observed": [c["near_zero_bars"], c["focus"], c["tv_start_side"]]})
    return {"checked": len(rows) - start - 1, "mismatches": failures}


def summarize(root):
    snapshot = json.loads((root / "chart_snapshot.json").read_text())
    journal = json.loads((root / "notification_audit.json").read_text())
    cutoff = int(datetime.fromisoformat(snapshot["cutoff_bjt"]).timestamp() * 1000)
    day = datetime.fromtimestamp(cutoff / 1000, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    day_ms = int(day.timestamp() * 1000)
    rows, validation, trends = [], {}, []
    symbols = [x["symbol"].split("-")[0] for x in journal["summary_by_symbol"]]
    for symbol in symbols:
        symbol_rows = []
        for tf in PERIODS:
            key = symbol + "_" + tf
            chart = snapshot["charts"][key]
            if "error" in chart:
                raise ValueError(key + ": " + chart["error"])
            candles = [c for c in chart["candles"] if c["bar_close_ms"] <= cutoff]
            step = {"15m": 900000, "30m": 1800000, "1H": 3600000, "4H": 14400000}[tf]
            assert all(b["t"] - a["t"] == step for a, b in zip(candles, candles[1:]))
            assert all(c["bar_close_ms"] == c["t"] + step and c["ready"] for c in candles)
            validation[key] = verify_focus(candles)
            assert not validation[key]["mismatches"], key
            today = [c for c in candles if c["bar_close_ms"] >= day_ms]
            events = [e for e in chart["events"] if day_ms <= e["bar_close_ms"] <= cutoff]
            longs = [e for e in events if e["kind"] == "tv_start" and e["side"] == "long"]
            db_longs = [e for e in journal["events"] if e["symbol"] == symbol + "-USDT-SWAP"
                        and e["timeframe"] == tf and e["kind"] == "tv_start" and e["side"] == "long"
                        and day_ms <= e["close_ms"] <= cutoff]
            assert {e["bar_close_ms"] for e in longs} == {e["close_ms"] for e in db_longs}, key
            departures = []
            for i, c in enumerate(candles):
                if c["bar_close_ms"] < day_ms or c["zero_breakout_side"] != "long" or i == 0:
                    continue
                departures.append({"time": bjt(c["bar_close_ms"]), "price": c["c"],
                    "zero_bars_before": candles[i - 1]["zero_bars"],
                    "joint_near_bars_before": candles[i - 1]["near_zero_bars"],
                    "dense": c["dense"], "internal_entry": c["entry_side"] == "long",
                    "visible_start": c["tv_start_side"] == "long"})
            prior_signals = [i for i, c in enumerate(candles) if c["tv_start_side"] == "long"]
            continuous = None
            if prior_signals:
                i = prior_signals[-1]
                if all(c["md"] > 0 for c in candles[i:]):
                    continuous = {"time": bjt(candles[i]["bar_close_ms"]), "price": candles[i]["c"],
                                  "positive_bars": len(candles) - i}
            symbol_rows.append({"timeframe": tf, "today_visible_long_count": len(longs),
                "today_visible_longs": db_longs, "today_departures": departures,
                "prior_start_with_continuously_positive_md": continuous,
                "max_joint_near_bars_today": max(c["near_zero_bars"] for c in today),
                "latest_md_atr": candles[-1]["md"] / candles[-1]["atr"],
                "last_closed": bjt(candles[-1]["bar_close_ms"]), "bars_checked": len(candles)})
            if tf == "15m" and symbol in ("IOST", "GRASS", "GPS", "MINA"):
                baseline = today[0]["c"]
                for c in today:
                    trends.append({"symbol": symbol, "time": bjt(c["bar_close_ms"]),
                        "close_ms": c["bar_close_ms"], "close": c["c"],
                        "price_index": 100 * c["c"] / baseline,
                        "md_atr": c["md"] / c["atr"], "sb_atr": c["sb"] / c["atr"],
                        "joint_near_bars": c["near_zero_bars"], "raw_arrow": c["tv_start_side"] == "long",
                        "internal_entry": c["entry_side"] == "long"})
        count = sum(r["today_visible_long_count"] for r in symbol_rows)
        today_events = [e for r in symbol_rows for e in r["today_visible_longs"]]
        sent = sum(e["bark_status"] == "sent" for e in today_events)
        before_activation = count > 0 and all(
            e.get("stage_timeframe_cutover_ms") is not None
            and e["close_ms"] <= e["stage_timeframe_cutover_ms"] for e in today_events)
        rows.append({"symbol": symbol, "periods": symbol_rows, "today_visible_long_count": count,
                     "today_raw_bark_sent": sent,
                     "category": "今天有启动且Bark接受" if sent else "今天有启动但早于周期启用" if before_activation
                     else "今天有启动，未发送原因待核实" if count else "今天无当前可见启动"})
    counts = {name: sum(r["category"] == name for r in rows) for name in dict.fromkeys(r["category"] for r in rows)}
    result = {"cutoff_bjt": snapshot["cutoff_bjt"], "cohort": symbols, "rows": rows,
              "category_counts": counts, "trend_series": trends, "validation": validation,
              "frozen_at": snapshot["frozen_at"],
              "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                                for p in ("chart_snapshot.json", "notification_audit.json")}}
    (root / "evidence_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"symbols": len(rows), "windows": len(validation), "counts": counts,
                      "focus_bars_checked": sum(x["checked"] for x in validation.values()),
                      "mismatches": sum(len(x["mismatches"]) for x in validation.values())}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    summarize(parser.parse_args().snapshot_dir)
